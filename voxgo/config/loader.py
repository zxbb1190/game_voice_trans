import json
from pathlib import Path

from loguru import logger

from voxgo.audio.capture import (
    LATENCY_MODE_ACCURATE,
    LATENCY_MODE_BALANCED,
    LATENCY_MODE_FAST,
    AudioConfig,
    SAFE_MAX_SPEECH_THRESHOLD_DBFS,
    apply_audio_latency_preset,
    infer_latency_mode,
    normalize_latency_mode,
)
from voxgo.mobile.server import WebSocketConfig
from voxgo.asr.whisper_engine import (
    DEFAULT_VAD_PARAMS,
    MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT,
    WhisperConfig,
    normalize_model_download_endpoint,
    normalize_model_download_source,
    sanitize_vad_parameters,
)
from voxgo.translation import TranslationConfig, normalize_translation_provider
from voxgo.update.checker import UpdateSettings, normalize_update_channel
from voxgo.i18n import UI_LANGUAGE_ZH, normalize_ui_language

from voxgo.config.schema import (
    OPPOSITE_LANGUAGE,
    AppConfig,
    DebugConfig,
    HotkeyConfig,
    OverlayConfig,
    RuntimeConfig,
    normalize_language_code,
    normalize_whisper_device,
)


CONFIG_SECTIONS = [
    "audio",
    "whisper",
    "translation",
    "overlay",
    "websocket",
    "hotkeys",
    "app",
    "debug",
    "update",
]
USER_SETTINGS_SECTIONS = ["audio", "overlay", "hotkeys", "translation", "whisper", "app", "debug", "update"]
WHISPER_BEAM_SIZE_BY_LATENCY_MODE = {
    LATENCY_MODE_FAST: 1,
    LATENCY_MODE_BALANCED: 1,
    LATENCY_MODE_ACCURATE: 5,
}


def default_app_config() -> AppConfig:
    return AppConfig(
        audio=AudioConfig(),
        whisper=WhisperConfig(),
        translation=TranslationConfig(),
        overlay=OverlayConfig(),
        websocket=WebSocketConfig(),
        hotkeys=HotkeyConfig(),
        app=RuntimeConfig(),
        debug=DebugConfig(),
        update=UpdateSettings(),
    )


def load_config(config_path: str = None, runtime_dir: Path = None) -> AppConfig:
    config = default_app_config()
    if config_path and Path(config_path).exists():
        try:
            with open(config_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            apply_section_data(config, data, CONFIG_SECTIONS)
            migrate_legacy_model_download_settings(config, data.get("whisper", {}))
            migrate_runtime_defaults(
                config,
                preserve_existing_audio_tuning="latency_mode" not in data.get("audio", {}),
            )
            logger.info(f"已加载配置: {config_path}")
        except Exception as e:
            logger.error(f"配置加载失败: {e}")

    if runtime_dir is not None:
        load_user_settings(config, Path(runtime_dir))
    migrate_runtime_defaults(config)
    sync_language_flow(config)
    sync_whisper_vad_limit(config)
    return config


def apply_section_data(config: AppConfig, data: dict, sections: list):
    for section in sections:
        if section in data:
            target = getattr(config, section)
            for key, value in data[section].items():
                if hasattr(target, key):
                    setattr(target, key, value)


def load_user_settings(config: AppConfig, runtime_dir: Path):
    settings_path = Path(runtime_dir) / "user_settings.json"
    if not settings_path.exists():
        return
    try:
        with open(settings_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        apply_section_data(config, data, USER_SETTINGS_SECTIONS)
        if "latency_mode" not in data.get("audio", {}):
            config.audio.latency_mode = ""
        migrate_legacy_model_download_settings(config, data.get("whisper", {}))
        migrate_runtime_defaults(
            config,
            preserve_existing_audio_tuning="latency_mode" not in data.get("audio", {}),
        )
        logger.info("已加载用户设置: {}", settings_path)
    except Exception as e:
        logger.warning("用户设置加载失败: {}", e)


def migrate_legacy_model_download_settings(config: AppConfig, whisper_data: dict):
    if not whisper_data:
        return
    endpoint = normalize_model_download_endpoint(whisper_data.get("model_download_endpoint", ""))
    if endpoint and "model_download_source" not in whisper_data:
        config.whisper.model_download_source = MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT


def migrate_runtime_defaults(config: AppConfig, preserve_existing_audio_tuning: bool = True):
    if not getattr(config, "app", None):
        config.app = RuntimeConfig()
    config.app.language = normalize_ui_language(getattr(config.app, "language", UI_LANGUAGE_ZH))
    if not getattr(config, "update", None):
        config.update = UpdateSettings()
    try:
        if float(getattr(config.translation, "timeout_seconds", 0) or 0) < 12:
            config.translation.timeout_seconds = 12
    except Exception:
        config.translation.timeout_seconds = 12
    if not hasattr(config.translation, "max_concurrent_requests"):
        config.translation.max_concurrent_requests = 2
    try:
        config.translation.max_concurrent_requests = max(
            1,
            min(4, int(getattr(config.translation, "max_concurrent_requests", 2) or 2)),
        )
    except Exception:
        config.translation.max_concurrent_requests = 2
    if not hasattr(config.whisper, "cpu_threads"):
        config.whisper.cpu_threads = 2
    if not hasattr(config.whisper, "num_workers"):
        config.whisper.num_workers = 1
    if preserve_existing_audio_tuning:
        config.audio.latency_mode = infer_latency_mode(config.audio)
    else:
        config.audio.latency_mode = normalize_latency_mode(
            getattr(config.audio, "latency_mode", LATENCY_MODE_BALANCED)
        )
    latency_mode = apply_audio_latency_preset(config.audio)
    try:
        config.whisper.beam_size = max(
            1,
            min(5, int(getattr(config.whisper, "beam_size", 5) or 5)),
        )
    except Exception:
        config.whisper.beam_size = 5
    if latency_mode in WHISPER_BEAM_SIZE_BY_LATENCY_MODE:
        config.whisper.beam_size = WHISPER_BEAM_SIZE_BY_LATENCY_MODE[latency_mode]
    try:
        config.audio.chunk_duration_ms = max(
            60,
            min(1000, int(getattr(config.audio, "chunk_duration_ms", 220) or 220)),
        )
    except Exception:
        config.audio.chunk_duration_ms = 220
    try:
        config.audio.speech_threshold_blocks = max(
            1,
            min(20, int(getattr(config.audio, "speech_threshold_blocks", 2) or 2)),
        )
    except Exception:
        config.audio.speech_threshold_blocks = 2
    try:
        config.audio.silence_limit_blocks = max(
            1,
            min(50, int(getattr(config.audio, "silence_limit_blocks", 4) or 4)),
        )
    except Exception:
        config.audio.silence_limit_blocks = 4
    try:
        config.audio.max_buffer_blocks = max(
            10,
            min(1000, int(getattr(config.audio, "max_buffer_blocks", 120) or 120)),
        )
    except Exception:
        config.audio.max_buffer_blocks = 120
    try:
        config.audio.pre_roll_ms = max(
            0,
            min(2000, int(getattr(config.audio, "pre_roll_ms", 450) or 0)),
        )
    except Exception:
        config.audio.pre_roll_ms = 450
    try:
        config.audio.speech_idle_timeout_ms = max(
            100,
            min(3000, int(getattr(config.audio, "speech_idle_timeout_ms", 650) or 650)),
        )
    except Exception:
        config.audio.speech_idle_timeout_ms = 650
    try:
        config.whisper.min_language_probability = max(
            0.0,
            min(1.0, float(getattr(config.whisper, "min_language_probability", 0.35) or 0.0)),
        )
    except Exception:
        config.whisper.min_language_probability = 0.35
    try:
        config.audio.min_segment_seconds = max(
            0.0,
            min(3.0, float(getattr(config.audio, "min_segment_seconds", 0.35) or 0.0)),
        )
    except Exception:
        config.audio.min_segment_seconds = 0.35
    try:
        config.audio.min_segment_peak_margin_db = max(
            0.0,
            min(12.0, float(getattr(config.audio, "min_segment_peak_margin_db", 1.5) or 0.0)),
        )
    except Exception:
        config.audio.min_segment_peak_margin_db = 1.5
    try:
        config.audio.max_speech_threshold = min(
            SAFE_MAX_SPEECH_THRESHOLD_DBFS,
            float(
                getattr(
                    config.audio,
                    "max_speech_threshold",
                    SAFE_MAX_SPEECH_THRESHOLD_DBFS,
                )
                or SAFE_MAX_SPEECH_THRESHOLD_DBFS
            ),
        )
    except Exception:
        config.audio.max_speech_threshold = SAFE_MAX_SPEECH_THRESHOLD_DBFS
    try:
        config.audio.min_speech_threshold = float(
            getattr(config.audio, "min_speech_threshold", -45.0) or -45.0
        )
    except Exception:
        config.audio.min_speech_threshold = -45.0
    if config.audio.min_speech_threshold > config.audio.max_speech_threshold:
        config.audio.min_speech_threshold = config.audio.max_speech_threshold
    config.update.enabled = bool(getattr(config.update, "enabled", True))
    config.update.channel = normalize_update_channel(getattr(config.update, "channel", "stable"))
    try:
        config.update.last_check_at = float(getattr(config.update, "last_check_at", 0) or 0)
    except Exception:
        config.update.last_check_at = 0
    config.update.ignored_version = str(getattr(config.update, "ignored_version", "") or "").strip().lstrip("v")
    config.update.manifest_url = str(getattr(config.update, "manifest_url", "") or "").strip()


def save_user_settings(config: AppConfig, runtime_dir: Path):
    settings_path = Path(runtime_dir) / "user_settings.json"
    data = serialize_user_settings(config)
    try:
        settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("用户设置保存失败: {}", e)


def serialize_user_settings(config: AppConfig) -> dict:
    return {
        "app": {
            "setup_completed": bool(getattr(config.app, "setup_completed", False)),
            "language": normalize_ui_language(getattr(config.app, "language", UI_LANGUAGE_ZH)),
        },
        "audio": {
            "latency_mode": config.audio.latency_mode,
            "input_device_id": config.audio.input_device_id,
            "input_device_index": config.audio.input_device_index,
            "input_device_name": config.audio.input_device_name,
            "chunk_duration_ms": config.audio.chunk_duration_ms,
            "speech_threshold_blocks": config.audio.speech_threshold_blocks,
            "silence_limit_blocks": config.audio.silence_limit_blocks,
            "max_buffer_blocks": config.audio.max_buffer_blocks,
            "max_speech_seconds": config.audio.max_speech_seconds,
            "pre_roll_ms": config.audio.pre_roll_ms,
            "speech_idle_timeout_ms": config.audio.speech_idle_timeout_ms,
            "min_speech_threshold": config.audio.min_speech_threshold,
            "max_speech_threshold": config.audio.max_speech_threshold,
            "min_segment_seconds": config.audio.min_segment_seconds,
            "min_segment_peak_margin_db": config.audio.min_segment_peak_margin_db,
        },
        "overlay": {
            "font_size": config.overlay.font_size,
            "text_color": config.overlay.text_color,
            "original_text_color": config.overlay.original_text_color,
            "bg_color": config.overlay.bg_color,
            "bg_opacity": config.overlay.bg_opacity,
            "window_width": config.overlay.window_width,
            "window_height": config.overlay.window_height,
            "window_x": config.overlay.window_x,
            "window_y": config.overlay.window_y,
            "opacity": config.overlay.opacity,
            "show_original": config.overlay.show_original,
            "draggable": config.overlay.draggable,
            "locked": config.overlay.locked,
            "compact_mode": bool(getattr(config.overlay, "compact_mode", False)),
        },
        "hotkeys": {
            "toggle_overlay": config.hotkeys.toggle_overlay,
            "toggle_translation": config.hotkeys.toggle_translation,
            "clear_history": config.hotkeys.clear_history,
            "toggle_lock": config.hotkeys.toggle_lock,
            "toggle_compact": config.hotkeys.toggle_compact,
        },
        "whisper": {
            "device": normalize_whisper_device(config.whisper.device),
            "cpu_threads": int(getattr(config.whisper, "cpu_threads", 2) or 2),
            "num_workers": int(getattr(config.whisper, "num_workers", 1) or 1),
            "model_download_source": normalize_model_download_source(
                getattr(config.whisper, "model_download_source", "modelscope"),
                getattr(config.whisper, "model_download_endpoint", ""),
            ),
            "model_download_endpoint": normalize_model_download_endpoint(
                getattr(config.whisper, "model_download_endpoint", "")
            ),
        },
        "translation": {
            "provider": normalize_translation_provider(config.translation.provider),
            "api_key": config.translation.api_key,
            "model": config.translation.model,
            "endpoint": config.translation.endpoint,
            "max_tokens": config.translation.max_tokens,
            "temperature": config.translation.temperature,
            "source_lang": config.translation.source_lang,
            "target_lang": config.translation.target_lang,
            "context_messages": config.translation.context_messages,
            "timeout_seconds": config.translation.timeout_seconds,
            "max_concurrent_requests": config.translation.max_concurrent_requests,
        },
        "debug": {
            "enabled": bool(getattr(config.debug, "enabled", False)),
            "log_level": getattr(config.debug, "log_level", "INFO"),
            "save_audio_chunks": bool(getattr(config.debug, "save_audio_chunks", False)),
            "save_transcripts": bool(getattr(config.debug, "save_transcripts", False)),
        },
        "update": {
            "enabled": bool(getattr(config.update, "enabled", True)),
            "channel": normalize_update_channel(getattr(config.update, "channel", "stable")),
            "last_check_at": float(getattr(config.update, "last_check_at", 0) or 0),
            "ignored_version": str(getattr(config.update, "ignored_version", "") or "").strip().lstrip("v"),
            "manifest_url": str(getattr(config.update, "manifest_url", "") or "").strip(),
        },
    }


def sync_language_flow(config: AppConfig):
    source = normalize_language_code(config.translation.source_lang, "en")
    target = normalize_language_code(config.translation.target_lang, OPPOSITE_LANGUAGE[source])
    if target == source:
        target = OPPOSITE_LANGUAGE[source]
    config.translation.source_lang = source
    config.translation.target_lang = target
    config.whisper.language = source
    return source, target


def sync_whisper_vad_limit(config: AppConfig):
    vad_parameters = dict(config.whisper.vad_parameters or DEFAULT_VAD_PARAMS)
    vad_parameters["max_speech_duration_s"] = float(config.audio.max_speech_seconds or 8)
    config.whisper.vad_parameters = sanitize_vad_parameters(vad_parameters)
