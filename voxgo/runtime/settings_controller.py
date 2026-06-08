from loguru import logger

from voxgo.audio.capture import AudioConfig, normalize_latency_mode
from voxgo.i18n import UI_LANGUAGE_ZH, language_label, normalize_ui_language, ui_text
from voxgo.asr.whisper_engine import (
    WhisperConfig,
    describe_model_download_source,
    normalize_model_download_endpoint,
    normalize_model_download_source,
)
from voxgo.update.checker import UpdateSettings, normalize_update_channel
from voxgo.config.schema import (
    HotkeyConfig,
    OverlayConfig,
    RuntimeConfig,
    WHISPER_DEVICE_NAMES,
    normalize_whisper_device,
)
from voxgo.translation import (
    TRANSLATION_PROVIDERS,
    TranslationConfig,
    normalize_translation_provider,
)


class OverlaySettingsController:
    def __init__(self, app):
        self._app = app

    def apply(
        self,
        overlay_config: OverlayConfig,
        hotkey_config: HotkeyConfig,
        audio_config: AudioConfig,
        translation_config: TranslationConfig,
        whisper_config: WhisperConfig,
        app_config: RuntimeConfig,
        update_config: UpdateSettings,
    ):
        app = self._app
        previous_device = app._last_audio_device
        previous_translation = app._last_translation_settings
        previous_whisper_device = app._last_whisper_device
        previous_model_download_source = app._last_model_download_source
        previous_update = (
            bool(getattr(app.config.update, "enabled", True)),
            normalize_update_channel(getattr(app.config.update, "channel", "stable")),
        )
        app.config.overlay = overlay_config
        app.config.hotkeys = hotkey_config
        previous_ui_language = normalize_ui_language(
            getattr(app.config.app, "language", UI_LANGUAGE_ZH)
        )
        app.config.app = app_config or app.config.app or RuntimeConfig()
        app.config.app.language = normalize_ui_language(
            getattr(app.config.app, "language", UI_LANGUAGE_ZH)
        )
        current_ui_language = app.config.app.language
        app.config.translation = translation_config
        app.config.translation.provider = normalize_translation_provider(
            app.config.translation.provider
        )
        app.config.update = update_config or app.config.update or UpdateSettings()
        app.config.whisper.device = normalize_whisper_device(
            getattr(whisper_config, "device", app.config.whisper.device)
        )
        app.config.whisper.model_download_source = normalize_model_download_source(
            getattr(
                whisper_config,
                "model_download_source",
                getattr(app.config.whisper, "model_download_source", "modelscope"),
            ),
            getattr(
                whisper_config,
                "model_download_endpoint",
                getattr(app.config.whisper, "model_download_endpoint", ""),
            ),
        )
        app.config.whisper.model_download_endpoint = normalize_model_download_endpoint(
            getattr(
                whisper_config,
                "model_download_endpoint",
                getattr(app.config.whisper, "model_download_endpoint", ""),
            )
        )
        app.config.audio.input_device_id = getattr(audio_config, "input_device_id", "")
        app.config.audio.input_device_index = audio_config.input_device_index
        app.config.audio.input_device_name = audio_config.input_device_name
        app.config.audio.latency_mode = normalize_latency_mode(
            getattr(audio_config, "latency_mode", app.config.audio.latency_mode)
        )
        app.config.audio.chunk_duration_ms = getattr(
            audio_config,
            "chunk_duration_ms",
            app.config.audio.chunk_duration_ms,
        )
        app.config.audio.speech_threshold_blocks = getattr(
            audio_config,
            "speech_threshold_blocks",
            app.config.audio.speech_threshold_blocks,
        )
        app.config.audio.silence_limit_blocks = getattr(
            audio_config,
            "silence_limit_blocks",
            app.config.audio.silence_limit_blocks,
        )
        app.config.audio.max_buffer_blocks = getattr(
            audio_config,
            "max_buffer_blocks",
            app.config.audio.max_buffer_blocks,
        )
        app.config.audio.max_speech_seconds = audio_config.max_speech_seconds
        app.config.audio.pre_roll_ms = getattr(
            audio_config,
            "pre_roll_ms",
            app.config.audio.pre_roll_ms,
        )
        app.config.audio.speech_idle_timeout_ms = getattr(
            audio_config,
            "speech_idle_timeout_ms",
            app.config.audio.speech_idle_timeout_ms,
        )
        app.config.audio.min_segment_seconds = getattr(
            audio_config,
            "min_segment_seconds",
            app.config.audio.min_segment_seconds,
        )
        app.config.audio.min_segment_peak_margin_db = getattr(
            audio_config,
            "min_segment_peak_margin_db",
            app.config.audio.min_segment_peak_margin_db,
        )
        app._migrate_runtime_defaults(app.config, preserve_existing_audio_tuning=False)
        previous_language_flow = app._last_language_flow
        current_language_flow = app._sync_language_flow()
        app._sync_whisper_vad_limit()
        if app._speech_recognizer:
            app._speech_recognizer.config = app.config.whisper
        app._translation.update_config(app.config.translation)
        app._setup_hotkeys()
        app._save_user_settings()
        current_device = (
            app.config.audio.input_device_id,
            app.config.audio.input_device_index,
            app.config.audio.input_device_name,
            app.config.audio.latency_mode,
            app.config.audio.chunk_duration_ms,
            app.config.audio.speech_threshold_blocks,
            app.config.audio.silence_limit_blocks,
            app.config.audio.max_buffer_blocks,
            app.config.audio.max_speech_seconds,
            app.config.audio.pre_roll_ms,
            app.config.audio.speech_idle_timeout_ms,
        )
        current_translation = (
            normalize_translation_provider(app.config.translation.provider),
            app.config.translation.api_key,
            app.config.translation.model,
            app.config.translation.endpoint,
        )
        current_whisper_device = normalize_whisper_device(app.config.whisper.device)
        current_model_download_source = (
            normalize_model_download_source(
                getattr(app.config.whisper, "model_download_source", "modelscope"),
                getattr(app.config.whisper, "model_download_endpoint", ""),
            ),
            normalize_model_download_endpoint(
                getattr(app.config.whisper, "model_download_endpoint", "")
            ),
        )
        current_update = (
            bool(getattr(app.config.update, "enabled", True)),
            normalize_update_channel(getattr(app.config.update, "channel", "stable")),
        )
        if app._running and current_device != previous_device:
            app._restart_audio_capture(reuse_noise_gate=current_device[:3] == previous_device[:3])
        if current_language_flow != previous_language_flow:
            app._translation.clear_context()
            app._notify_user(
                ui_text(current_ui_language, "语言方向已更新", "Language Direction Updated"),
                f"{language_label(current_language_flow[0], current_ui_language)} -> "
                f"{language_label(current_language_flow[1], current_ui_language)}",
                ui_text(current_ui_language, "状态", "Status"),
            )
        if current_translation != previous_translation:
            provider_label = TRANSLATION_PROVIDERS.get(
                normalize_translation_provider(app.config.translation.provider),
                app.config.translation.provider,
            )
            detail = ui_text(
                current_ui_language,
                f"服务商: {provider_label}",
                f"Provider: {provider_label}",
            )
            if normalize_translation_provider(app.config.translation.provider) == "google":
                detail += ui_text(
                    current_ui_language,
                    "\n接口: Google Cloud Translation Basic v2",
                    "\nAPI: Google Cloud Translation Basic v2",
                )
            else:
                detail += ui_text(
                    current_ui_language,
                    f"\n模型: {app.config.translation.model}\n兼容地址: {app.config.translation.endpoint}",
                    f"\nModel: {app.config.translation.model}\nEndpoint: {app.config.translation.endpoint}",
                )
            app._notify_user(
                ui_text(current_ui_language, "翻译接口已更新", "Translation Provider Updated"),
                detail,
                ui_text(current_ui_language, "状态", "Status"),
            )
        if current_whisper_device != previous_whisper_device:
            app._notify_user(
                ui_text(current_ui_language, "识别设备已更新", "Recognition Device Updated"),
                ui_text(
                    current_ui_language,
                    f"当前选择: {WHISPER_DEVICE_NAMES[current_whisper_device]}\n重启程序后生效",
                    f"Current selection: {WHISPER_DEVICE_NAMES[current_whisper_device]}\nRestart VoxGo to apply it.",
                ),
                ui_text(current_ui_language, "状态", "Status"),
            )
        if current_model_download_source != previous_model_download_source:
            app._notify_user(
                ui_text(current_ui_language, "模型下载源已更新", "Model Download Source Updated"),
                ui_text(
                    current_ui_language,
                    f"当前选择: {describe_model_download_source(*current_model_download_source)}\n重启程序后生效",
                    f"Current selection: {describe_model_download_source(*current_model_download_source)}\nRestart VoxGo to apply it.",
                ),
                ui_text(current_ui_language, "状态", "Status"),
            )
        if current_update != previous_update:
            app._notify_user(
                ui_text(current_ui_language, "更新检查已更新", "Update Check Settings Updated"),
                ui_text(
                    current_ui_language,
                    f"自动检查: {'开启' if current_update[0] else '关闭'}\n通道: {current_update[1]}",
                    f"Automatic checks: {'On' if current_update[0] else 'Off'}\nChannel: {current_update[1]}",
                ),
                ui_text(current_ui_language, "状态", "Status"),
            )
        if current_ui_language != previous_ui_language:
            if app._overlay:
                app._overlay.app_config = app.config.app
                app._overlay.refresh_language()
            app._notify_user(
                ui_text(current_ui_language, "界面语言已更新", "Interface Language Updated"),
                ui_text(
                    current_ui_language,
                    "主要界面已切换为简体中文",
                    "The main interface has switched to English.",
                ),
                ui_text(current_ui_language, "状态", "Status"),
            )
        app._last_audio_device = current_device
        app._last_translation_settings = current_translation
        app._last_language_flow = current_language_flow
        app._last_whisper_device = current_whisper_device
        app._last_model_download_source = current_model_download_source
        app._sync_tray_state()
        logger.info(
            "浮窗设置已应用: opacity={:.2f}, bg_opacity={:.2f}, text_color={}, show_original={}, audio_device={} {}, latency_mode={}, beam_size={}, max_speech={}s, min_segment={:.2f}s, min_peak_margin={:.1f}dB, language={}→{}, whisper_device={}, model_download_source={}, provider={}, model={}, endpoint={}, hotkeys={}/{}/{}/{}/{}",
            overlay_config.opacity,
            overlay_config.bg_opacity,
            overlay_config.text_color,
            overlay_config.show_original,
            app.config.audio.input_device_index,
            app.config.audio.input_device_name,
            app.config.audio.latency_mode,
            app.config.whisper.beam_size,
            app.config.audio.max_speech_seconds,
            app.config.audio.min_segment_seconds,
            app.config.audio.min_segment_peak_margin_db,
            current_language_flow[0],
            current_language_flow[1],
            current_whisper_device,
            describe_model_download_source(*current_model_download_source),
            normalize_translation_provider(app.config.translation.provider),
            app.config.translation.model,
            app.config.translation.endpoint,
            hotkey_config.toggle_overlay,
            hotkey_config.clear_history,
            hotkey_config.toggle_translation,
            hotkey_config.toggle_lock,
            hotkey_config.toggle_compact,
        )
