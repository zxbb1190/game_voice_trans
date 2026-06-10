"""Shared UI configuration models and helper functions."""

import platform
from dataclasses import dataclass
from typing import Optional

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QPushButton

from voxgo.app_info import APP_VERSION
from voxgo.audio.capture import (
    AUDIO_LATENCY_PRESETS,
    LATENCY_MODE_ACCURATE,
    LATENCY_MODE_BALANCED,
    LATENCY_MODE_CUSTOM,
    LATENCY_MODE_FAST,
    AudioConfig,
    apply_audio_latency_preset,
    normalize_latency_mode,
)
from voxgo.i18n import (
    UI_LANGUAGE_ZH,
    UI_LANGUAGE_OPTIONS,
    is_english_ui,
    language_label,
    normalize_ui_language,
    ui_text,
)
from voxgo.translation import TRANSLATION_PROVIDERS, TranslationConfig, normalize_translation_provider


SPEECH_LANGUAGE_CODES = ("en", "zh")
LANGUAGE_OPTIONS = (("en", "英语"), ("zh", "中文"))
LANGUAGE_LABELS = dict(LANGUAGE_OPTIONS)
OPPOSITE_LANGUAGE = {"en": "zh", "zh": "en"}
WHISPER_DEVICE_OPTIONS = (
    ("cpu", "CPU（推荐）"),
    ("auto", "自动检测"),
    ("cuda", "NVIDIA GPU / CUDA"),
)
WHISPER_DEVICE_LABELS = dict(WHISPER_DEVICE_OPTIONS)
WHISPER_DOWNLOAD_SOURCE_OPTIONS = (
    ("modelscope", "ModelScope 国内源（推荐）"),
    ("huggingface", "官方 Hugging Face"),
    ("custom_hf_endpoint", "自定义 Hugging Face Endpoint"),
)
TRANSLATION_TEST_COOLDOWN_SECONDS = 5
AUDIO_LATENCY_MODE_OPTIONS = (
    (LATENCY_MODE_FAST, "极速"),
    (LATENCY_MODE_BALANCED, "均衡（推荐）"),
    (LATENCY_MODE_ACCURATE, "准确"),
    (LATENCY_MODE_CUSTOM, "自定义"),
)


def _hotkey_label(value: str) -> str:
    return str(value or "").strip() or "未设置"


def _ui_language(config) -> str:
    return normalize_ui_language(getattr(config or RuntimeConfig(), "language", UI_LANGUAGE_ZH))


def _tr(language: str, zh: str, en: str) -> str:
    return ui_text(language, zh, en)


def _speech_language_options(ui_language: str):
    return tuple((code, language_label(code, ui_language)) for code in SPEECH_LANGUAGE_CODES)


def _whisper_device_options(ui_language: str):
    if is_english_ui(ui_language):
        return (
            ("cpu", "CPU (Recommended)"),
            ("auto", "Auto detect"),
            ("cuda", "NVIDIA GPU / CUDA"),
        )
    return WHISPER_DEVICE_OPTIONS


def _model_download_source_options(ui_language: str):
    if is_english_ui(ui_language):
        return (
            ("modelscope", "ModelScope mirror (Recommended in China)"),
            ("huggingface", "Official Hugging Face"),
            ("custom_hf_endpoint", "Custom Hugging Face Endpoint"),
        )
    return WHISPER_DOWNLOAD_SOURCE_OPTIONS


def _audio_latency_mode_options(ui_language: str):
    if is_english_ui(ui_language):
        return (
            (LATENCY_MODE_FAST, "Fast"),
            (LATENCY_MODE_BALANCED, "Balanced (Recommended)"),
            (LATENCY_MODE_ACCURATE, "Accurate"),
            (LATENCY_MODE_CUSTOM, "Custom"),
        )
    return AUDIO_LATENCY_MODE_OPTIONS


def _update_channel_options(ui_language: str):
    if is_english_ui(ui_language):
        return (
            ("stable", "Stable"),
            ("beta", "Beta"),
        )
    return UPDATE_CHANNEL_OPTIONS


def _hotkey_label_for_ui(value: str, ui_language: str) -> str:
    return str(value or "").strip() or _tr(ui_language, "未设置", "Not set")


def _copy_runtime_config(config):
    return RuntimeConfig(
        setup_completed=bool(getattr(config, "setup_completed", False)),
        language=normalize_ui_language(getattr(config, "language", UI_LANGUAGE_ZH)),
    )


def _start_button_cooldown(button: QPushButton, idle_text: str, seconds: int = TRANSLATION_TEST_COOLDOWN_SECONDS):
    def tick(remaining: int):
        try:
            if remaining <= 0:
                button.setText(idle_text)
                button.setEnabled(True)
                return
            button.setText(f"{idle_text}（{remaining}s）")
            button.setEnabled(False)
            QTimer.singleShot(1000, lambda: tick(remaining - 1))
        except RuntimeError:
            return

    tick(max(0, int(seconds)))


def _normalize_language_code(value: str, default: str = "en") -> str:
    value = (value or "").strip().lower()
    aliases = {
        "english": "en",
        "eng": "en",
        "英语": "en",
        "chinese": "zh",
        "zh-cn": "zh",
        "zh-tw": "zh",
        "cmn": "zh",
        "中文": "zh",
    }
    value = aliases.get(value, value)
    return value if value in LANGUAGE_LABELS else default


def _normalize_whisper_device(value: str) -> str:
    value = (value or "").strip().lower()
    aliases = {
        "gpu": "cuda",
        "nvidia": "cuda",
        "nvidia gpu": "cuda",
        "自动": "auto",
        "自动检测": "auto",
    }
    value = aliases.get(value, value)
    return value if value in WHISPER_DEVICE_LABELS else "cpu"


def _normalize_model_download_endpoint(value: str) -> str:
    endpoint = (value or "").strip()
    if endpoint.lower() in ("official", "huggingface", "huggingface.co", "default", "none"):
        return ""
    if not endpoint:
        return ""
    aliases = {
        "hf-mirror": "https://hf-mirror.com",
        "hf-mirror.com": "https://hf-mirror.com",
        "mirror": "https://hf-mirror.com",
        "china": "https://hf-mirror.com",
        "cn": "https://hf-mirror.com",
    }
    endpoint = aliases.get(endpoint.lower(), endpoint)
    endpoint = endpoint.rstrip("/")
    if endpoint in ("https://huggingface.co", "http://huggingface.co"):
        return ""
    if not endpoint.startswith(("http://", "https://")):
        endpoint = "https://" + endpoint
    return endpoint.rstrip("/")


def _normalize_model_download_source(value: str, endpoint: str = "") -> str:
    source = (value or "").strip().lower().replace("-", "_")
    normalized_endpoint = _normalize_model_download_endpoint(endpoint)
    if not source and normalized_endpoint:
        return "custom_hf_endpoint"
    aliases = {
        "": "modelscope",
        "default": "modelscope",
        "china": "modelscope",
        "cn": "modelscope",
        "domestic": "modelscope",
        "modelscope": "modelscope",
        "model_scope": "modelscope",
        "ms": "modelscope",
        "official": "huggingface",
        "huggingface": "huggingface",
        "hugging_face": "huggingface",
        "hf": "huggingface",
        "custom": "custom_hf_endpoint",
        "custom_hf": "custom_hf_endpoint",
        "custom_huggingface": "custom_hf_endpoint",
        "custom_hugging_face": "custom_hf_endpoint",
        "custom_hf_endpoint": "custom_hf_endpoint",
    }
    normalized = aliases.get(source, source)
    if normalized not in {"modelscope", "huggingface", "custom_hf_endpoint"}:
        normalized = "custom_hf_endpoint" if normalized_endpoint else "modelscope"
    if normalized == "huggingface" and normalized_endpoint:
        return "custom_hf_endpoint"
    if normalized == "custom_hf_endpoint" and not normalized_endpoint:
        return "huggingface"
    return normalized


@dataclass
class OverlayConfig:
    font_size: int = 16
    font_family: str = "Microsoft YaHei"
    text_color: str = "#00FF00"
    bg_color: str = "#20242A"
    bg_opacity: float = 0.82
    position: str = "bottom"
    max_lines: int = 5
    fade_duration: int = 5
    window_width: int = 500
    window_height: int = 200
    window_x: Optional[int] = None
    window_y: Optional[int] = None
    opacity: float = 0.85
    original_text_color: str = "#B7C4D8"
    show_original: bool = True
    draggable: bool = True
    locked: bool = False
    compact_mode: bool = False
    mobile_url: str = ""


@dataclass
class HotkeyConfig:
    toggle_overlay: str = "ctrl+shift+t"
    toggle_translation: str = "ctrl+alt+s"
    clear_history: str = "ctrl+alt+c"
    toggle_lock: str = ""
    toggle_compact: str = ""


@dataclass
class AudioDeviceConfig:
    latency_mode: str = LATENCY_MODE_BALANCED
    input_device_index: Optional[int] = None
    input_device_name: str = ""
    input_device_id: str = ""
    chunk_duration_ms: int = 200
    speech_threshold_blocks: int = 2
    silence_limit_blocks: int = 3
    max_buffer_blocks: int = 120
    max_speech_seconds: float = 6.0
    pre_roll_ms: int = 450
    speech_idle_timeout_ms: int = 550
    min_segment_seconds: float = 0.35
    min_segment_peak_margin_db: float = 1.5


@dataclass
class WhisperDeviceConfig:
    device: str = "cpu"
    model_size: str = "small"
    fast_model_size: str = ""
    pure_english_environment: bool = False
    enable_english_model: bool = False
    english_model_size: str = "small.en"
    fast_english_model_size: str = ""
    model_download_source: str = "modelscope"
    model_download_endpoint: str = ""


@dataclass
class RuntimeConfig:
    setup_completed: bool = False
    language: str = UI_LANGUAGE_ZH


@dataclass
class DebugConfig:
    enabled: bool = False
    log_level: str = "INFO"
    save_audio_chunks: bool = False
    save_transcripts: bool = False
    save_failed_audio: bool = False
    save_dropped_audio: bool = False
    save_empty_asr_audio: bool = False
    save_low_confidence_audio: bool = False
    diagnostics_audio_dir: str = "diagnostics/audio"


UPDATE_CHANNEL_OPTIONS = (
    ("stable", "稳定版"),
    ("beta", "Beta"),
)


def _copy_translation_config(config: TranslationConfig) -> TranslationConfig:
    return TranslationConfig(
        provider=normalize_translation_provider(getattr(config, "provider", "openai_compatible")),
        api_key=getattr(config, "api_key", ""),
        model=getattr(config, "model", TranslationConfig.model),
        endpoint=getattr(config, "endpoint", TranslationConfig.endpoint),
        max_tokens=getattr(config, "max_tokens", 80),
        temperature=getattr(config, "temperature", 0.0),
        source_lang=getattr(config, "source_lang", "en"),
        target_lang=getattr(config, "target_lang", "zh"),
        context_messages=getattr(config, "context_messages", 0),
        timeout_seconds=getattr(config, "timeout_seconds", TranslationConfig.timeout_seconds),
        max_concurrent_requests=getattr(
            config,
            "max_concurrent_requests",
            TranslationConfig.max_concurrent_requests,
        ),
        skip_language_mismatch=getattr(
            config,
            "skip_language_mismatch",
            TranslationConfig.skip_language_mismatch,
        ),
        language_gate_min_probability=getattr(
            config,
            "language_gate_min_probability",
            TranslationConfig.language_gate_min_probability,
        ),
        language_gate_short_text_min_probability=getattr(
            config,
            "language_gate_short_text_min_probability",
            TranslationConfig.language_gate_short_text_min_probability,
        ),
        language_gate_short_text_chars=getattr(
            config,
            "language_gate_short_text_chars",
            TranslationConfig.language_gate_short_text_chars,
        ),
        enable_local_phrase_cache=getattr(
            config,
            "enable_local_phrase_cache",
            TranslationConfig.enable_local_phrase_cache,
        ),
        local_phrase_cache=getattr(config, "local_phrase_cache", TranslationConfig.local_phrase_cache),
    )


def _copy_audio_config(config) -> AudioConfig:
    audio_config = AudioConfig()
    for key, value in getattr(config, "__dict__", {}).items():
        if hasattr(audio_config, key):
            setattr(audio_config, key, value)
    audio_config.latency_mode = normalize_latency_mode(getattr(audio_config, "latency_mode", LATENCY_MODE_BALANCED))
    apply_audio_latency_preset(audio_config)
    return audio_config


def _device_label(device: Optional[dict]) -> str:
    if not device:
        return "自动选择"
    device_type = "系统声音" if device.get("is_loopback") else "输入设备"
    return (
        f"[{device_type}] [{device.get('index')}] {device.get('name', '')} "
        f"({device.get('sample_rate') or 0}Hz/{device.get('channels') or 0}ch)"
    )


def _build_feedback_report(
    translation_config: TranslationConfig,
    whisper_config,
    debug_config: DebugConfig,
    app_version: str,
    runtime_dir: str,
    last_latency_summary: Optional[dict],
    selected_audio_device: str,
    ui_language: str = UI_LANGUAGE_ZH,
) -> str:
    provider = normalize_translation_provider(getattr(translation_config, "provider", "openai_compatible"))
    provider_label = TRANSLATION_PROVIDERS.get(provider, provider)
    latency = last_latency_summary or {}
    log_dir = runtime_dir or "."
    if is_english_ui(ui_language):
        return "\n".join([
            "## VoxGo Feedback",
            "",
            "### Basic Info",
            f"- VoxGo version: {app_version or APP_VERSION}",
            "- Package: Lite / Full (keep the one you used)",
            f"- Windows version: {platform.platform()}",
            "- Game:",
            "- Bluetooth headset: yes / no",
            "",
            "### Current Settings",
            f"- Audio device: {selected_audio_device or 'Auto select'}",
            f"- Translation provider: {provider_label}",
            f"- Model: {getattr(translation_config, 'model', '')}",
            f"- Endpoint: {getattr(translation_config, 'endpoint', '')}",
            f"- Recognition device: {getattr(whisper_config, 'device', 'cpu')}",
            f"- Debug mode: {'on' if getattr(debug_config, 'enabled', False) else 'off'}",
            "",
            "### Latest Latency",
            f"- Queue wait: {latency.get('wait_ms', 0)} ms",
            f"- Recognition: {latency.get('recognition_ms', 0)} ms",
            f"- Translation: {latency.get('translation_ms', 0)} ms",
            f"- Overlay update: {latency.get('overlay_ms', 0)} ms",
            f"- Total: {latency.get('total_ms', 0)} ms",
            "",
            "### Log Files",
            f"- app.log: {log_dir}/app.log",
            f"- crash_report.txt: {log_dir}/crash_report.txt",
            "",
            "### Problem Type",
            "- Startup failed / no sound / sound but no subtitles / inaccurate recognition / slow translation / mobile mirror failed / other",
            "",
            "### Reproduction Steps",
            "1. ",
            "2. ",
            "3. ",
            "",
            "### Expected Result",
            "",
            "### Actual Result",
        ])
    return "\n".join([
        "## VoxGo 反馈",
        "",
        "### 基本信息",
        f"- VoxGo 版本：{app_version or APP_VERSION}",
        "- 包类型：Lite / Full（请保留实际使用的一项）",
        f"- Windows 版本：{platform.platform()}",
        "- 游戏名：",
        "- 是否使用蓝牙耳机：是 / 否",
        "",
        "### 当前配置",
        f"- 音频设备：{selected_audio_device or '自动选择'}",
        f"- 翻译服务：{provider_label}",
        f"- 模型名：{getattr(translation_config, 'model', '')}",
        f"- 兼容地址：{getattr(translation_config, 'endpoint', '')}",
        f"- 识别设备：{WHISPER_DEVICE_LABELS.get(_normalize_whisper_device(getattr(whisper_config, 'device', 'cpu')), 'CPU')}",
        f"- 调试模式：{'开启' if getattr(debug_config, 'enabled', False) else '关闭'}",
        "",
        "### 最近一次延迟",
        f"- 队列等待：{latency.get('wait_ms', 0)} ms",
        f"- 识别耗时：{latency.get('recognition_ms', 0)} ms",
        f"- 翻译耗时：{latency.get('translation_ms', 0)} ms",
        f"- 浮窗更新：{latency.get('overlay_ms', 0)} ms",
        f"- 总延迟：{latency.get('total_ms', 0)} ms",
        "",
        "### 日志文件",
        f"- app.log：{log_dir}/app.log",
        f"- crash_report.txt：{log_dir}/crash_report.txt",
        "",
        "### 问题类型",
        "- 启动失败 / 无声音 / 有声音无字幕 / 识别不准 / 翻译慢 / 手机同步失败 / 其他",
        "",
        "### 复现步骤",
        "1. ",
        "2. ",
        "3. ",
        "",
        "### 期望结果",
        "",
        "### 实际结果",
    ])
