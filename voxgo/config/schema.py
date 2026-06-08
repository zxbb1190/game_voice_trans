from dataclasses import dataclass
from typing import Optional

from voxgo.audio.capture import AudioConfig
from voxgo.i18n import UI_LANGUAGE_ZH, normalize_ui_language
from voxgo.mobile.server import WebSocketConfig
from voxgo.asr.whisper_engine import WhisperConfig
from voxgo.translation import TranslationConfig
from voxgo.update.checker import UpdateSettings


LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "英语": "en",
    "zh": "zh",
    "zh-cn": "zh",
    "zh-tw": "zh",
    "cmn": "zh",
    "yue": "zh",
    "chinese": "zh",
    "中文": "zh",
}
LANGUAGE_NAMES = {"en": "英语", "zh": "中文"}
OPPOSITE_LANGUAGE = {"en": "zh", "zh": "en"}
WHISPER_DEVICE_NAMES = {
    "cpu": "CPU（推荐）",
    "auto": "自动检测",
    "cuda": "NVIDIA GPU / CUDA",
}


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
class RuntimeConfig:
    setup_completed: bool = False
    language: str = UI_LANGUAGE_ZH


@dataclass
class DebugConfig:
    enabled: bool = False
    log_level: str = "INFO"
    save_audio_chunks: bool = False
    save_transcripts: bool = False


@dataclass
class AppConfig:
    audio: AudioConfig = None
    whisper: WhisperConfig = None
    translation: TranslationConfig = None
    overlay: OverlayConfig = None
    websocket: WebSocketConfig = None
    hotkeys: HotkeyConfig = None
    app: RuntimeConfig = None
    debug: DebugConfig = None
    update: UpdateSettings = None


def normalize_language_code(value: str, default: str = "en") -> str:
    value = (value or "").strip().lower()
    normalized = LANGUAGE_ALIASES.get(value, value)
    return normalized if normalized in LANGUAGE_NAMES else default


def normalize_whisper_device(value: str) -> str:
    value = (value or "").strip().lower()
    aliases = {
        "gpu": "cuda",
        "nvidia": "cuda",
        "nvidia gpu": "cuda",
        "自动": "auto",
        "自动检测": "auto",
    }
    value = aliases.get(value, value)
    return value if value in WHISPER_DEVICE_NAMES else "cpu"


def ui_language_of(config) -> str:
    return normalize_ui_language(getattr(getattr(config, "app", None), "language", UI_LANGUAGE_ZH))
