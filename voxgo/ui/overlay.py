"""
游戏浮窗叠加层
使用 PyQt5 创建透明置顶窗口，显示翻译结果
"""

import ctypes
import asyncio
import platform
import socket
import sys
import threading
import time
import webbrowser
from collections import deque
from dataclasses import dataclass
from typing import Callable, List, Optional

from PyQt5.QtCore import (
    Qt, QEvent, QPoint, QRect, QTimer, pyqtSignal, QObject
)
from PyQt5.QtGui import (
    QColor, QPainter, QPen, QBrush, QIcon, QPixmap, QTextDocument, QPolygon, QCursor,
)
from PyQt5.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QApplication, QPushButton, QFrame, QDialog, QFormLayout,
    QSlider, QCheckBox, QLineEdit, QColorDialog, QToolButton, QComboBox,
    QSpinBox, QDoubleSpinBox, QSizePolicy, QScrollArea, QProgressBar, QPlainTextEdit,
    QStackedWidget, QTabWidget
)

from voxgo.app_info import APP_NAME, APP_VERSION, APP_WEBSITE, GITHUB_URL
from voxgo.audio.capture import (
    AUDIO_LATENCY_PRESETS,
    LATENCY_MODE_ACCURATE,
    LATENCY_MODE_BALANCED,
    LATENCY_MODE_CUSTOM,
    LATENCY_MODE_FAST,
    AudioConfig,
    AudioLevelMonitor,
    apply_audio_latency_preset,
    normalize_latency_mode,
)
from voxgo.ui.qr_widget import QrCodeWidget
from voxgo.translation import GameTranslator, TranslationConfig, TRANSLATION_PROVIDERS, normalize_translation_provider
from voxgo.update.checker import UpdateCheckResult, UpdateInfo, UpdateSettings, normalize_update_channel
from voxgo.i18n import (
    UI_LANGUAGE_ZH,
    UI_LANGUAGE_OPTIONS,
    is_english_ui,
    language_label,
    normalize_ui_language,
    ui_text,
)


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
    chunk_duration_ms: int = 220
    speech_threshold_blocks: int = 2
    silence_limit_blocks: int = 4
    max_buffer_blocks: int = 120
    max_speech_seconds: float = 6.0
    pre_roll_ms: int = 450
    speech_idle_timeout_ms: int = 650
    min_segment_seconds: float = 0.35
    min_segment_peak_margin_db: float = 1.5


@dataclass
class WhisperDeviceConfig:
    device: str = "cpu"
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


class TranslationTestSignals(QObject):
    finished = pyqtSignal(bool, str)


class TranslationTestRunner:
    def __init__(self, config: TranslationConfig, callback: Callable[[bool, str], None]):
        self.signals = TranslationTestSignals()
        self.signals.finished.connect(callback)
        self._config = _copy_translation_config(config)

    def start(self):
        threading.Thread(target=self._run, name="translation-test", daemon=True).start()

    def _run(self):
        started_at = time.time()
        translator = GameTranslator(self._config)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            translated = loop.run_until_complete(
                translator.translate("Hello, can you hear me?", "en")
            )
            elapsed_ms = int(round((time.time() - started_at) * 1000))
            translated = (translated or "").strip()
            if not translated:
                self.signals.finished.emit(False, "翻译接口返回空结果")
            elif translated.startswith("[翻译") or translated.startswith("[未翻译]"):
                self.signals.finished.emit(False, translated)
            else:
                self.signals.finished.emit(True, f"测试成功：{translated}\n耗时：{elapsed_ms} ms")
        except Exception as e:
            self.signals.finished.emit(False, f"测试失败：{str(e)[:220]}")
        finally:
            try:
                loop.run_until_complete(translator.close())
            except Exception:
                pass
            loop.close()


class AudioTestSignals(QObject):
    level = pyqtSignal(dict)


class AudioTestPanel(QWidget):
    def __init__(
        self,
        get_audio_config: Callable[[], AudioConfig],
        parent=None,
        ui_language: str = UI_LANGUAGE_ZH,
    ):
        super().__init__(parent)
        self._get_audio_config = get_audio_config
        self._ui_language = normalize_ui_language(ui_language)
        self._monitor: Optional[AudioLevelMonitor] = None
        self._signals = AudioTestSignals()
        self._signals.level.connect(self._handle_level_update)
        self._init_ui()

    def set_ui_language(self, ui_language: str):
        self._ui_language = normalize_ui_language(ui_language)
        self.start_button.setText(_tr(self._ui_language, "测试音频", "Test Audio"))
        self.stop_button.setText(_tr(self._ui_language, "停止", "Stop"))
        if not self._monitor:
            self.device_label.setText(_tr(self._ui_language, "当前设备：未测试", "Current device: not tested"))
            self.status_label.setText(_tr(
                self._ui_language,
                "播放游戏、Discord 或视频声音后，音量条应该跳动。",
                "Play game, Discord, or video audio; the level meter should move.",
            ))

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        button_row = QHBoxLayout()
        self.start_button = QPushButton(_tr(self._ui_language, "测试音频", "Test Audio"))
        self.stop_button = QPushButton(_tr(self._ui_language, "停止", "Stop"))
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_test)
        self.stop_button.clicked.connect(self.stop_test)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        button_row.addStretch()

        self.device_label = QLabel(_tr(self._ui_language, "当前设备：未测试", "Current device: not tested"))
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.status_label = QLabel(_tr(
            self._ui_language,
            "播放游戏、Discord 或视频声音后，音量条应该跳动。",
            "Play game, Discord, or video audio; the level meter should move.",
        ))
        self.status_label.setWordWrap(True)

        layout.addLayout(button_row)
        layout.addWidget(self.device_label)
        layout.addWidget(self.level_bar)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

    def start_test(self):
        self.stop_test()
        self.level_bar.setValue(0)
        self.status_label.setText(_tr(self._ui_language, "正在打开音频设备...", "Opening audio device..."))
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        try:
            config = _copy_audio_config(self._get_audio_config())
            self._monitor = AudioLevelMonitor(config, self._signals.level.emit)
            self._monitor.start()
            self.status_label.setText(_tr(
                self._ui_language,
                "正在监听声音，播放游戏/视频/Discord 后观察音量条。",
                "Listening. Play game, video, or Discord audio and watch the meter.",
            ))
        except Exception as e:
            self._monitor = None
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.status_label.setText(_tr(
                self._ui_language,
                f"音频测试失败：{str(e)[:220]}",
                f"Audio test failed: {str(e)[:220]}",
            ))

    def stop_test(self):
        if self._monitor:
            self._monitor.stop()
            self._monitor = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def _handle_level_update(self, payload: dict):
        if payload.get("error"):
            self.status_label.setText(_tr(
                self._ui_language,
                f"音频读取失败：{payload.get('error')}",
                f"Audio read failed: {payload.get('error')}",
            ))
            self.stop_test()
            return
        rms = float(payload.get("rms_dbfs", -120.0))
        peak = float(payload.get("peak_dbfs", -120.0))
        detected = bool(payload.get("detected"))
        value = int(max(0, min(100, (rms + 70.0) / 70.0 * 100.0)))
        self.level_bar.setValue(value)
        device = payload.get("device") or {}
        if device:
            self.device_label.setText(
                _tr(self._ui_language, "当前设备：", "Current device: ")
                + f"{device.get('type', 'Audio')} [{device.get('index')}] "
                + f"{device.get('name', '')} ({device.get('sample_rate')}Hz/{device.get('channels')}ch)"
            )
        if is_english_ui(self._ui_language):
            state = "Sound detected" if detected else "No clear sound yet"
            self.status_label.setText(f"{state}. Current {rms:.1f} dBFS, peak {peak:.1f} dBFS.")
        else:
            state = "检测到声音" if detected else "暂未检测到明显声音"
            self.status_label.setText(f"{state}。当前 {rms:.1f} dBFS，峰值 {peak:.1f} dBFS。")

    def close(self):
        self.stop_test()
        super().close()


class FeedbackDialog(QDialog):
    def __init__(self, report_text: str, ui_language: str = UI_LANGUAGE_ZH, parent=None):
        super().__init__(parent)
        self._ui_language = normalize_ui_language(ui_language)
        self.setWindowTitle(_tr(self._ui_language, "提交反馈", "Submit Feedback"))
        self.setWindowFlags(self.windowFlags() | Qt.Tool)
        layout = QVBoxLayout()
        label = QLabel(_tr(
            self._ui_language,
            "复制下面的诊断模板，到 GitHub Issue 里补充问题描述。",
            "Copy the diagnostic template below into a GitHub Issue and add your own description.",
        ))
        label.setWordWrap(True)
        self.text = QPlainTextEdit(report_text)
        self.text.setMinimumSize(640, 360)
        button_row = QHBoxLayout()
        copy_button = QPushButton(_tr(self._ui_language, "复制模板", "Copy Template"))
        open_button = QPushButton(_tr(self._ui_language, "打开 Issue", "Open Issue"))
        close_button = QPushButton(_tr(self._ui_language, "关闭", "Close"))
        copy_button.clicked.connect(self._copy)
        open_button.clicked.connect(self._open_issue)
        close_button.clicked.connect(self.close)
        button_row.addWidget(copy_button)
        button_row.addWidget(open_button)
        button_row.addStretch()
        button_row.addWidget(close_button)
        layout.addWidget(label)
        layout.addWidget(self.text)
        layout.addLayout(button_row)
        self.setLayout(layout)

    def _copy(self):
        QApplication.clipboard().setText(self.text.toPlainText())

    def _open_issue(self):
        webbrowser.open("https://github.com/zxbb1190/VoxGo_game_voice_trans/issues/new")


class UpdatePromptDialog(QDialog):
    def __init__(
        self,
        update: UpdateInfo,
        current_version: str,
        on_ignore: Callable[[str], None],
        ui_language: str = UI_LANGUAGE_ZH,
        parent=None,
    ):
        super().__init__(parent)
        self._ui_language = normalize_ui_language(ui_language)
        self.setWindowTitle(_tr(self._ui_language, "发现新版本", "Update Available"))
        self.setWindowFlags(self.windowFlags() | Qt.Tool)
        self._update = update
        self._current_version = current_version or APP_VERSION
        self._on_ignore = on_ignore
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        message = QLabel(self._build_message())
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(message)

        button_row = QHBoxLayout()
        open_button = QPushButton(_tr(self._ui_language, "打开下载页", "Open Downloads"))
        later_button = QPushButton(_tr(self._ui_language, "稍后提醒", "Later"))
        ignore_button = QPushButton(_tr(self._ui_language, "忽略此版本", "Ignore This Version"))
        open_button.clicked.connect(self._open_download_page)
        later_button.clicked.connect(self.close)
        ignore_button.clicked.connect(self._ignore_version)
        button_row.addWidget(open_button)
        button_row.addWidget(later_button)
        button_row.addStretch()
        button_row.addWidget(ignore_button)
        layout.addLayout(button_row)
        self.setLayout(layout)
        self.resize(460, 260)

    def _build_message(self) -> str:
        notes = "\n".join(f"- {note}" for note in self._update.notes)
        if not notes:
            notes = _tr(self._ui_language, "- 查看下载页面了解更新内容", "- Open the download page for details")
        if is_english_ui(self._ui_language):
            return (
                f"New version available: {self._update.display_title()}\n\n"
                f"Current version: v{self._current_version}\n"
                f"What's new:\n{notes}\n\n"
                "Open the download page?"
            )
        return (
            f"发现新版本 {self._update.display_title()}\n\n"
            f"当前版本：v{self._current_version}\n"
            f"新版内容：\n{notes}\n\n"
            "是否打开下载页面？"
        )

    def _open_download_page(self):
        url = self._update.release_url or self._update.download_lite_url or self._update.download_full_url
        if url:
            webbrowser.open(url)
        self.close()

    def _ignore_version(self):
        if self._on_ignore:
            self._on_ignore(self._update.latest)
        self.close()


class FullscreenHelpDialog(QDialog):
    def __init__(self, hotkeys: HotkeyConfig, ui_language: str = UI_LANGUAGE_ZH, parent=None):
        super().__init__(parent)
        self._ui_language = normalize_ui_language(ui_language)
        self.setWindowTitle(_tr(self._ui_language, "全屏兼容说明", "Fullscreen Compatibility"))
        self.setWindowFlags(self.windowFlags() | Qt.Tool)
        hotkeys = hotkeys or HotkeyConfig()

        layout = QVBoxLayout()
        if is_english_ui(self._ui_language):
            message_text = (
                "If the overlay does not appear in exclusive fullscreen games, switch the game to borderless windowed or windowed fullscreen first.\n\n"
                "Place the overlay on the desktop before locking it. Locked mode reduces mouse interference.\n\n"
                f"Hotkeys available in-game: {_hotkey_label_for_ui(hotkeys.toggle_overlay, self._ui_language)} show/hide, "
                f"{_hotkey_label_for_ui(hotkeys.toggle_translation, self._ui_language)} pause/resume, "
                f"{_hotkey_label_for_ui(hotkeys.clear_history, self._ui_language)} clear subtitles.\n\n"
                "If the game runs as administrator, run VoxGo as administrator as well for more reliable hotkeys and always-on-top behavior."
            )
        else:
            message_text = (
                "如果独占全屏游戏里看不到浮窗，请优先把游戏显示模式改成无边框窗口或窗口化全屏。\n\n"
                "建议先在桌面把浮窗拖到合适位置，再点击锁定。锁定后浮窗会减少鼠标干扰。\n\n"
                f"全屏中可用热键控制：{_hotkey_label(hotkeys.toggle_overlay)} 显示/隐藏，"
                f"{_hotkey_label(hotkeys.toggle_translation)} 暂停/恢复，"
                f"{_hotkey_label(hotkeys.clear_history)} 清空字幕。\n\n"
                "如果游戏以管理员身份运行，VoxGo 也需要用管理员身份启动，热键和置顶才更稳定。"
            )
        message = QLabel(message_text)
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(message)

        button_row = QHBoxLayout()
        close_button = QPushButton(_tr(self._ui_language, "知道了", "Got It"))
        close_button.clicked.connect(self.close)
        button_row.addStretch()
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
        self.setLayout(layout)
        self.resize(520, 300)


def _build_help_text(hotkeys: HotkeyConfig, ui_language: str = UI_LANGUAGE_ZH) -> str:
    hotkeys = hotkeys or HotkeyConfig()
    ui_language = normalize_ui_language(ui_language)
    if is_english_ui(ui_language):
        return (
            "Fullscreen compatibility\n"
            "If the overlay does not appear in exclusive fullscreen games, switch the game to borderless windowed or windowed fullscreen first.\n"
            "Place the overlay on the desktop before locking it to reduce mouse interference.\n"
            "If the game runs as administrator, run VoxGo as administrator as well for more reliable hotkeys and always-on-top behavior.\n\n"
            "Current hotkeys\n"
            f"{_hotkey_label_for_ui(hotkeys.toggle_overlay, ui_language)}: Show/hide overlay\n"
            f"{_hotkey_label_for_ui(hotkeys.toggle_translation, ui_language)}: Pause/resume translation\n"
            f"{_hotkey_label_for_ui(hotkeys.clear_history, ui_language)}: Clear subtitles\n"
            f"{_hotkey_label_for_ui(getattr(hotkeys, 'toggle_lock', ''), ui_language)}: Lock/unlock overlay\n"
            f"{_hotkey_label_for_ui(getattr(hotkeys, 'toggle_compact', ''), ui_language)}: Toggle compact mode\n\n"
            "System tray\n"
            "The tray icon can show/hide the overlay, pause/resume, clear subtitles, toggle compact mode, open settings, and quit.\n"
            "If a game covers the overlay or the overlay is hidden, use the tray icon or hotkeys."
        )

    return (
        "全屏兼容\n"
        "如果独占全屏游戏里看不到浮窗，请优先把游戏显示模式改成无边框窗口或窗口化全屏。\n"
        "建议先在桌面把浮窗拖到合适位置，再点击锁定，减少鼠标干扰。\n"
        "如果游戏以管理员身份运行，VoxGo 也需要用管理员身份启动，热键和置顶才更稳定。\n\n"
        "当前快捷键\n"
        f"{_hotkey_label(hotkeys.toggle_overlay)}：显示/隐藏浮窗\n"
        f"{_hotkey_label(hotkeys.toggle_translation)}：暂停/恢复翻译\n"
        f"{_hotkey_label(hotkeys.clear_history)}：清空字幕\n"
        f"{_hotkey_label(getattr(hotkeys, 'toggle_lock', ''))}：锁定/解锁浮窗\n"
        f"{_hotkey_label(getattr(hotkeys, 'toggle_compact', ''))}：切换紧凑模式\n\n"
        "系统托盘\n"
        "任务栏托盘图标可以显示/隐藏浮窗、暂停/恢复、清空字幕、切换紧凑模式、打开设置和退出。\n"
        "如果游戏遮住浮窗或浮窗被隐藏，优先从托盘或快捷键控制。"
    )


class FirstRunWizard(QDialog):
    """First-run setup flow before the backend starts loading models."""

    setup_completed = pyqtSignal()

    def __init__(
        self,
        audio_config: AudioDeviceConfig,
        translation_config: TranslationConfig,
        audio_devices: Optional[List[dict]] = None,
        whisper_config=None,
        app_config: RuntimeConfig = None,
        debug_config: DebugConfig = None,
        app_version: str = "",
        runtime_dir: str = "",
        get_last_latency_summary: Optional[Callable[[], dict]] = None,
        on_audio_devices_refresh: Optional[Callable[[], List[dict]]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("VoxGo 首次启动向导")
        self.setWindowFlags(self.windowFlags() | Qt.Tool)
        self.audio_config = audio_config or AudioDeviceConfig()
        self.translation_config = translation_config or TranslationConfig()
        self.audio_devices = audio_devices or []
        self.whisper_config = whisper_config or WhisperDeviceConfig()
        self.app_config = app_config or RuntimeConfig()
        self.app_config.language = normalize_ui_language(getattr(self.app_config, "language", UI_LANGUAGE_ZH))
        self._ui_language = self.app_config.language
        self.debug_config = debug_config or DebugConfig()
        self.app_version = app_version or APP_VERSION
        self.runtime_dir = runtime_dir
        self._get_last_latency_summary = get_last_latency_summary
        self._on_audio_devices_refresh = on_audio_devices_refresh
        self._translation_test_runner = None
        self._feedback_dialog = None
        self._completed = False
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout()
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_translation_page())
        self.stack.addWidget(self._build_audio_page())
        self.stack.addWidget(self._build_finish_page())
        self.stack.currentChanged.connect(self._refresh_buttons)

        nav_row = QHBoxLayout()
        self.skip_button = QPushButton("稍后设置并启动")
        self.back_button = QPushButton("上一步")
        self.next_button = QPushButton("下一步")
        self.finish_button = QPushButton("完成并启动")
        self.skip_button.clicked.connect(self._complete_setup)
        self.back_button.clicked.connect(self._go_back)
        self.next_button.clicked.connect(self._go_next)
        self.finish_button.clicked.connect(self._complete_setup)
        nav_row.addWidget(self.skip_button)
        nav_row.addStretch()
        nav_row.addWidget(self.back_button)
        nav_row.addWidget(self.next_button)
        nav_row.addWidget(self.finish_button)

        root.addWidget(self.stack)
        root.addLayout(nav_row)
        self.setLayout(root)
        self.resize(760, 560)
        self._refresh_buttons()

    def _build_translation_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        title = QLabel("先确认翻译接口")
        title.setObjectName("wizardTitle")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        note = QLabel("填写你准备用来翻译游戏语音的服务。测试成功后再进入游戏，少走一圈弯路。")
        note.setWordWrap(True)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.wizard_provider_combo = QComboBox()
        self._fill_wizard_translation_providers()
        self.wizard_provider_combo.currentIndexChanged.connect(self._wizard_provider_changed)
        form.addRow("翻译服务", self.wizard_provider_combo)

        self.wizard_api_key_input = QLineEdit(self.translation_config.api_key)
        self.wizard_api_key_input.setEchoMode(QLineEdit.Password)
        form.addRow("API Key", self.wizard_api_key_input)

        self.wizard_model_input = QLineEdit(self.translation_config.model)
        self.wizard_model_input.setPlaceholderText("tencent/Hunyuan-MT-7B")
        form.addRow("模型名", self.wizard_model_input)

        self.wizard_endpoint_input = QLineEdit(self.translation_config.endpoint)
        self.wizard_endpoint_input.setPlaceholderText("https://api.siliconflow.cn/v1/chat/completions")
        form.addRow("兼容地址", self.wizard_endpoint_input)

        test_row = QHBoxLayout()
        self.wizard_translation_test_button = QPushButton("测试 API Key")
        self.wizard_translation_test_label = QLabel("会发送一句测试文本；每次点击只调用一次接口，结束后按钮会短暂冷却。")
        self.wizard_translation_test_label.setWordWrap(True)
        self.wizard_translation_test_button.clicked.connect(self._test_translation)
        test_row.addWidget(self.wizard_translation_test_button)
        test_row.addWidget(self.wizard_translation_test_label, 1)
        form.addRow("接口测试", test_row)

        layout.addWidget(title)
        layout.addWidget(note)
        layout.addSpacing(10)
        layout.addLayout(form)
        layout.addStretch()
        page.setLayout(layout)
        self._refresh_wizard_translation_provider_ui()
        return page

    def _build_audio_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        title = QLabel("再确认能听到游戏声音")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        note = QLabel("优先选择和你正在用的耳机、扬声器、HDMI 或 USB 声卡同名的 [系统声音] / Loopback 设备。普通麦克风通常录不到游戏声音。")
        note.setWordWrap(True)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        audio_row = QHBoxLayout()
        self.wizard_audio_device_combo = QComboBox()
        self.wizard_refresh_audio_button = QPushButton("刷新")
        self._fill_wizard_audio_devices()
        self.wizard_refresh_audio_button.clicked.connect(self._refresh_wizard_audio_devices)
        audio_row.addWidget(self.wizard_audio_device_combo)
        audio_row.addWidget(self.wizard_refresh_audio_button)
        form.addRow("音频设备", audio_row)

        self.wizard_audio_test_panel = AudioTestPanel(self._current_audio_config, self)
        form.addRow("音频测试", self.wizard_audio_test_panel)

        layout.addWidget(title)
        layout.addWidget(note)
        layout.addSpacing(10)
        layout.addLayout(form)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def _build_finish_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout()
        title = QLabel("准备启动实时翻译")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        note = QLabel("完成后会保存首次设置状态，并开始加载 Whisper。Lite 包首次加载模型可能需要等待。")
        note.setWordWrap(True)
        self.wizard_debug_check = QCheckBox("开启调试模式，记录最近一次识别、翻译和浮窗更新延迟")
        self.wizard_debug_check.setChecked(bool(getattr(self.debug_config, "enabled", False)))
        self.wizard_summary_label = QLabel()
        self.wizard_summary_label.setWordWrap(True)
        feedback_button = QPushButton("生成反馈模板")
        feedback_button.clicked.connect(self._open_feedback_dialog)

        layout.addWidget(title)
        layout.addWidget(note)
        layout.addSpacing(10)
        layout.addWidget(self.wizard_debug_check)
        layout.addWidget(self.wizard_summary_label)
        layout.addWidget(feedback_button, 0, Qt.AlignLeft)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def _fill_wizard_translation_providers(self):
        self.wizard_provider_combo.blockSignals(True)
        self.wizard_provider_combo.clear()
        selected_provider = normalize_translation_provider(
            getattr(self.translation_config, "provider", "openai_compatible")
        )
        selected_row = 0
        for row, (provider, label) in enumerate(TRANSLATION_PROVIDERS.items()):
            self.wizard_provider_combo.addItem(label, provider)
            if provider == selected_provider:
                selected_row = row
        self.wizard_provider_combo.setCurrentIndex(selected_row)
        self.wizard_provider_combo.blockSignals(False)

    def _wizard_provider_changed(self, *args):
        self._refresh_wizard_translation_provider_ui()

    def _refresh_wizard_translation_provider_ui(self):
        provider = normalize_translation_provider(self.wizard_provider_combo.currentData())
        is_google = provider == "google"
        self.wizard_api_key_input.setPlaceholderText(
            "Google Cloud Translation API Key" if is_google else "OpenAI 兼容 API Key"
        )
        self.wizard_model_input.setEnabled(not is_google)
        self.wizard_endpoint_input.setEnabled(not is_google)

    def _fill_wizard_audio_devices(self):
        self.wizard_audio_device_combo.blockSignals(True)
        self.wizard_audio_device_combo.clear()
        self.wizard_audio_device_combo.addItem("自动选择", None)
        selected_row = 0
        selected_index = getattr(self.audio_config, "input_device_index", None)
        selected_name = (getattr(self.audio_config, "input_device_name", "") or "").strip()
        selected_device_id = (getattr(self.audio_config, "input_device_id", "") or "").strip()
        for row, device in enumerate(self.audio_devices, start=1):
            self.wizard_audio_device_combo.addItem(_device_label(device), device)
            index = device.get("index")
            name = device.get("name", "")
            device_id = (device.get("device_id") or "").strip()
            if selected_device_id and selected_device_id == device_id:
                selected_row = row
            elif selected_row == 0 and selected_name and selected_name == name:
                selected_row = row
            elif selected_row == 0 and not selected_name and selected_index is not None and int(selected_index) == int(index):
                selected_row = row
        self.wizard_audio_device_combo.setCurrentIndex(selected_row)
        self.wizard_audio_device_combo.blockSignals(False)

    def _refresh_wizard_audio_devices(self):
        if self._on_audio_devices_refresh:
            self.audio_devices = self._on_audio_devices_refresh() or []
        self._fill_wizard_audio_devices()

    def _current_audio_config(self) -> AudioConfig:
        self._collect_audio_values()
        return _copy_audio_config(self.audio_config)

    def _collect_translation_values(self):
        self.translation_config.provider = normalize_translation_provider(self.wizard_provider_combo.currentData())
        self.translation_config.api_key = self.wizard_api_key_input.text().strip()
        if self.translation_config.provider != "google":
            self.translation_config.model = self.wizard_model_input.text().strip() or self.translation_config.model
            self.translation_config.endpoint = self.wizard_endpoint_input.text().strip() or self.translation_config.endpoint

    def _collect_audio_values(self):
        device = self.wizard_audio_device_combo.currentData()
        if device:
            self.audio_config.input_device_index = int(device.get("index"))
            self.audio_config.input_device_name = device.get("name", "")
            self.audio_config.input_device_id = device.get("device_id", "")
        else:
            self.audio_config.input_device_index = None
            self.audio_config.input_device_name = ""
            self.audio_config.input_device_id = ""

    def _collect_values(self):
        self._collect_translation_values()
        self._collect_audio_values()
        self.debug_config.enabled = self.wizard_debug_check.isChecked()

    def _test_translation(self):
        self._collect_translation_values()
        self.wizard_translation_test_button.setEnabled(False)
        self.wizard_translation_test_button.setText("测试中...")
        self.wizard_translation_test_label.setText("正在测试翻译接口...")
        self._translation_test_runner = TranslationTestRunner(
            self.translation_config,
            self._handle_translation_test_result,
        )
        self._translation_test_runner.start()

    def _handle_translation_test_result(self, ok: bool, message: str):
        prefix = "成功" if ok else "失败"
        self.wizard_translation_test_label.setText(f"{prefix}：{message}")
        _start_button_cooldown(self.wizard_translation_test_button, "测试 API Key")

    def _go_back(self):
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))

    def _go_next(self):
        if self.stack.currentIndex() == 0:
            self._collect_translation_values()
        elif self.stack.currentIndex() == 1:
            self._collect_audio_values()
            self.wizard_audio_test_panel.stop_test()
        self.stack.setCurrentIndex(min(self.stack.count() - 1, self.stack.currentIndex() + 1))
        if self.stack.currentIndex() == self.stack.count() - 1:
            self._refresh_summary()

    def _refresh_buttons(self):
        index = self.stack.currentIndex()
        last_index = self.stack.count() - 1
        self.back_button.setEnabled(index > 0)
        self.next_button.setVisible(index < last_index)
        self.finish_button.setVisible(index == last_index)
        if index == last_index:
            self._refresh_summary()

    def _refresh_summary(self):
        self._collect_values()
        provider = normalize_translation_provider(getattr(self.translation_config, "provider", "openai_compatible"))
        provider_label = TRANSLATION_PROVIDERS.get(provider, provider)
        audio_label = self.wizard_audio_device_combo.currentText() or "自动选择"
        self.wizard_summary_label.setText(
            f"翻译服务：{provider_label}\n"
            f"音频设备：{audio_label}\n"
            f"调试模式：{'开启' if self.debug_config.enabled else '关闭'}"
        )

    def _complete_setup(self):
        self._mark_completed()
        self.accept()

    def _mark_completed(self):
        if self._completed:
            return
        self._collect_values()
        if hasattr(self, "wizard_audio_test_panel"):
            self.wizard_audio_test_panel.stop_test()
        self.app_config.setup_completed = True
        self._completed = True
        self.setup_completed.emit()

    def _open_feedback_dialog(self):
        self._collect_values()
        selected_device = self.wizard_audio_device_combo.currentText() if hasattr(self, "wizard_audio_device_combo") else ""
        latency = self._get_last_latency_summary() if self._get_last_latency_summary else {}
        self._feedback_dialog = FeedbackDialog(
            _build_feedback_report(
                self.translation_config,
                self.whisper_config,
                self.debug_config,
                self.app_version,
                self.runtime_dir,
                latency,
                selected_device,
                self._ui_language,
            ),
            self._ui_language,
            self,
        )
        self._feedback_dialog.show()

    def closeEvent(self, event):
        if hasattr(self, "wizard_audio_test_panel"):
            self.wizard_audio_test_panel.stop_test()
        if not self._completed:
            self._mark_completed()
        super().closeEvent(event)


class TranslationItem:
    """单条翻译记录"""

    def __init__(
        self,
        original: str,
        translated: str,
        fade_duration: int,
        timestamp: float = None,
        item_id: str = "",
    ):
        self.item_id = item_id
        self.original = original
        self.translated = translated
        self.fade_duration = max(1, fade_duration)
        self.timestamp = timestamp or time.time()
        self.fade_start = None

    def start_fade(self):
        self.fade_start = time.time()

    @property
    def opacity(self) -> float:
        if self.fade_start is None:
            return 1.0
        elapsed = time.time() - self.fade_start
        return max(0.0, 1.0 - elapsed / self.fade_duration)


class OverlaySignals(QObject):
    """信号类，用于线程安全更新"""
    new_translation = pyqtSignal(str, str)
    new_translation_with_id = pyqtSignal(str, str, str)
    update_translation = pyqtSignal(str, str)
    clear_history = pyqtSignal()
    toggle_visibility = pyqtSignal()
    toggle_lock = pyqtSignal()
    toggle_compact = pyqtSignal()
    settings_changed = pyqtSignal(object, object, object, object, object, object, object)
    refresh_audio_devices = pyqtSignal()
    update_checking = pyqtSignal(bool)
    update_check_result = pyqtSignal(object, bool)
    pause_state_changed = pyqtSignal(bool)


class ColorButton(QPushButton):
    """Small color picker button."""

    color_changed = pyqtSignal(str)

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedWidth(80)
        self.clicked.connect(self._pick_color)
        self._refresh()

    def color(self) -> str:
        return self._color

    def set_color(self, color: str):
        self._color = color
        self._refresh()

    def _refresh(self):
        self.setText(self._color)
        self.setStyleSheet(f"""
            QPushButton {{
                color: #FFFFFF;
                background: {self._color};
                border: 1px solid #7D8794;
                border-radius: 4px;
                padding: 4px 6px;
            }}
        """)

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self._color), self, "选择颜色")
        if color.isValid():
            self.set_color(color.name().upper())
            self.color_changed.emit(self._color)


class HotkeyCaptureEdit(QLineEdit):
    """Line edit that records the next pressed key combination."""

    hotkey_changed = pyqtSignal(str)

    MODIFIER_NAMES = [
        (Qt.ControlModifier, "ctrl"),
        (Qt.AltModifier, "alt"),
        (Qt.ShiftModifier, "shift"),
        (Qt.MetaModifier, "windows"),
    ]

    KEY_NAMES = {
        Qt.Key_Escape: "esc",
        Qt.Key_Space: "space",
        Qt.Key_Tab: "tab",
        Qt.Key_Backspace: "backspace",
        Qt.Key_Delete: "delete",
        Qt.Key_Return: "enter",
        Qt.Key_Enter: "enter",
    }

    def __init__(self, value: str, parent=None):
        super().__init__(value, parent)
        self.setReadOnly(True)
        self.setPlaceholderText("点击后按快捷键")
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        self.setText("请按快捷键...")
        self.selectAll()
        self.setFocus(Qt.MouseFocusReason)
        event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Escape, Qt.Key_Backspace, Qt.Key_Delete):
            self.clear()
            self.hotkey_changed.emit("")
            return
        if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta):
            return
        hotkey = self._event_to_hotkey(event)
        if hotkey:
            self.setText(hotkey)
            self.hotkey_changed.emit(hotkey)

    def _event_to_hotkey(self, event) -> str:
        parts = []
        modifiers = event.modifiers()
        for modifier, name in self.MODIFIER_NAMES:
            if modifiers & modifier:
                parts.append(name)
        key_name = self.KEY_NAMES.get(event.key())
        if not key_name:
            text = event.text().lower()
            if text and text.isprintable():
                key_name = text
            elif Qt.Key_F1 <= event.key() <= Qt.Key_F35:
                key_name = f"f{event.key() - Qt.Key_F1 + 1}"
            else:
                key_name = event.keyCombination().key().name.lower()
        if not key_name:
            return ""
        parts.append(key_name)
        return "+".join(parts)


def _make_icon(kind: str, color: str) -> QIcon:
    pixmap = QPixmap(28, 28)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 2)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    if kind == "qr":
        for rect in (QRect(5, 5, 6, 6), QRect(17, 5, 6, 6), QRect(5, 17, 6, 6)):
            painter.drawRect(rect)
            painter.fillRect(rect.adjusted(2, 2, -2, -2), QColor(color))
        painter.fillRect(QRect(17, 17, 3, 3), QColor(color))
        painter.fillRect(QRect(21, 21, 3, 3), QColor(color))
        painter.fillRect(QRect(17, 23, 7, 2), QColor(color))
    elif kind == "settings":
        painter.drawEllipse(QPoint(14, 14), 4, 4)
        for angle in range(0, 360, 45):
            painter.save()
            painter.translate(14, 14)
            painter.rotate(angle)
            painter.drawLine(0, -11, 0, -8)
            painter.restore()
        painter.drawEllipse(QPoint(14, 14), 10, 10)
    elif kind in ("lock", "unlock"):
        painter.drawRoundedRect(QRect(7, 12, 16, 11), 2, 2)
        if kind == "lock":
            painter.drawArc(QRect(9, 4, 12, 15), 0, 180 * 16)
        else:
            painter.drawArc(QRect(13, 4, 12, 15), 35 * 16, 185 * 16)
            painter.drawLine(10, 12, 10, 10)
        painter.drawLine(14, 16, 14, 19)
    elif kind == "swap":
        painter.drawLine(7, 10, 20, 10)
        painter.drawLine(17, 7, 20, 10)
        painter.drawLine(17, 13, 20, 10)
        painter.drawLine(21, 18, 8, 18)
        painter.drawLine(11, 15, 8, 18)
        painter.drawLine(11, 21, 8, 18)
    elif kind == "pause":
        painter.setBrush(QBrush(QColor(color)))
        painter.drawRoundedRect(QRect(9, 7, 4, 14), 1, 1)
        painter.drawRoundedRect(QRect(16, 7, 4, 14), 1, 1)
    elif kind == "play":
        painter.setBrush(QBrush(QColor(color)))
        painter.drawPolygon(QPolygon([QPoint(10, 7), QPoint(21, 14), QPoint(10, 21)]))
    elif kind == "trash":
        painter.drawLine(9, 10, 21, 10)
        painter.drawLine(12, 7, 18, 7)
        painter.drawRoundedRect(QRect(10, 11, 10, 13), 2, 2)
        painter.drawLine(13, 14, 13, 21)
        painter.drawLine(17, 14, 17, 21)
    elif kind == "compact":
        painter.drawRect(QRect(7, 8, 16, 12))
        painter.drawLine(7, 14, 12, 14)
        painter.drawLine(10, 11, 12, 14)
        painter.drawLine(10, 17, 12, 14)
        painter.drawLine(23, 14, 18, 14)
        painter.drawLine(20, 11, 18, 14)
        painter.drawLine(20, 17, 18, 14)
    elif kind == "expand":
        painter.drawRect(QRect(7, 8, 16, 12))
        painter.drawLine(12, 14, 7, 14)
        painter.drawLine(9, 11, 7, 14)
        painter.drawLine(9, 17, 7, 14)
        painter.drawLine(18, 14, 23, 14)
        painter.drawLine(21, 11, 23, 14)
        painter.drawLine(21, 17, 23, 14)
    elif kind == "help":
        painter.drawEllipse(QPoint(14, 14), 10, 10)
        painter.drawArc(QRect(10, 7, 8, 8), 20 * 16, 220 * 16)
        painter.drawLine(14, 15, 14, 17)
        painter.drawPoint(14, 21)
    else:
        painter.drawLine(8, 8, 20, 20)
        painter.drawLine(20, 8, 8, 20)

    painter.end()
    return QIcon(pixmap)


class SettingsDialog(QDialog):
    """Graphical settings for overlay and hotkeys."""

    settings_changed = pyqtSignal(object, object, object, object, object, object, object)

    def __init__(
        self,
        overlay_config: OverlayConfig,
        hotkey_config: HotkeyConfig,
        audio_config: AudioDeviceConfig = None,
        translation_config: TranslationConfig = None,
        audio_devices: Optional[List[dict]] = None,
        whisper_config=None,
        app_config: RuntimeConfig = None,
        debug_config: DebugConfig = None,
        update_config: UpdateSettings = None,
        app_version: str = "",
        runtime_dir: str = "",
        last_latency_summary: Optional[dict] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.Tool)
        self.overlay_config = overlay_config
        self.hotkey_config = hotkey_config
        self.audio_config = audio_config or AudioDeviceConfig()
        self.translation_config = translation_config or TranslationConfig()
        self.audio_devices = audio_devices or []
        self.whisper_config = whisper_config or WhisperDeviceConfig()
        self.app_config = _copy_runtime_config(app_config or RuntimeConfig())
        self.app_config.language = normalize_ui_language(getattr(self.app_config, "language", UI_LANGUAGE_ZH))
        self._ui_language = self.app_config.language
        self.setWindowTitle(_tr(self._ui_language, "浮窗设置", "Overlay Settings"))
        self.debug_config = debug_config or DebugConfig()
        self.update_config = update_config or UpdateSettings()
        self.app_version = app_version
        self.runtime_dir = runtime_dir
        self.last_latency_summary = last_latency_summary or {}
        self._translation_test_runner = None
        self._feedback_dialog = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        tabs = QTabWidget()
        appearance_form = self._make_settings_tab(tabs, _tr(self._ui_language, "外观", "Appearance"))
        translation_form = self._make_settings_tab(tabs, _tr(self._ui_language, "翻译", "Translation"))
        audio_form = self._make_settings_tab(tabs, _tr(self._ui_language, "语音", "Speech"))
        advanced_form = self._make_settings_tab(tabs, _tr(self._ui_language, "高级", "Advanced"))
        help_form = self._make_settings_tab(tabs, _tr(self._ui_language, "说明", "Guide"))
        hotkey_form = self._make_settings_tab(tabs, _tr(self._ui_language, "快捷键", "Hotkeys"))
        about_form = self._make_settings_tab(tabs, _tr(self._ui_language, "关于", "About"))
        about_form.setRowWrapPolicy(QFormLayout.WrapLongRows)

        self.ui_language_combo = QComboBox()
        self._fill_ui_languages()
        self.ui_language_combo.currentIndexChanged.connect(self._ui_language_changed)
        appearance_form.addRow("Language", self.ui_language_combo)

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(int(self.overlay_config.opacity * 100))
        self.opacity_label = QLabel(f"{self.opacity_slider.value()}%")
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity_slider)
        opacity_row.addWidget(self.opacity_label)
        self.opacity_slider.valueChanged.connect(lambda value: self.opacity_label.setText(f"{value}%"))
        self.opacity_slider.valueChanged.connect(self._preview)
        appearance_form.addRow(_tr(self._ui_language, "整体透明度", "Window Opacity"), opacity_row)

        self.bg_opacity_slider = QSlider(Qt.Horizontal)
        self.bg_opacity_slider.setRange(20, 100)
        self.bg_opacity_slider.setValue(int(float(getattr(self.overlay_config, "bg_opacity", 0.82)) * 100))
        self.bg_opacity_label = QLabel(f"{self.bg_opacity_slider.value()}%")
        bg_opacity_row = QHBoxLayout()
        bg_opacity_row.addWidget(self.bg_opacity_slider)
        bg_opacity_row.addWidget(self.bg_opacity_label)
        self.bg_opacity_slider.valueChanged.connect(lambda value: self.bg_opacity_label.setText(f"{value}%"))
        self.bg_opacity_slider.valueChanged.connect(self._preview)
        appearance_form.addRow(_tr(self._ui_language, "背景透明度", "Background Opacity"), bg_opacity_row)

        self.font_slider = QSlider(Qt.Horizontal)
        self.font_slider.setRange(12, 30)
        self.font_slider.setValue(self.overlay_config.font_size)
        self.font_label = QLabel(f"{self.font_slider.value()}px")
        font_row = QHBoxLayout()
        font_row.addWidget(self.font_slider)
        font_row.addWidget(self.font_label)
        self.font_slider.valueChanged.connect(lambda value: self.font_label.setText(f"{value}px"))
        self.font_slider.valueChanged.connect(self._preview)
        appearance_form.addRow(_tr(self._ui_language, "字号", "Font Size"), font_row)

        self.text_color_btn = ColorButton(self.overlay_config.text_color)
        self.text_color_btn.color_changed.connect(self._preview)
        appearance_form.addRow(_tr(self._ui_language, "译文颜色", "Translation Color"), self.text_color_btn)

        self.original_color_btn = ColorButton(self.overlay_config.original_text_color)
        self.original_color_btn.color_changed.connect(self._preview)
        appearance_form.addRow(_tr(self._ui_language, "原文颜色", "Original Color"), self.original_color_btn)

        self.show_original_check = QCheckBox(_tr(self._ui_language, "显示英文原文", "Show original speech text"))
        self.show_original_check.setChecked(self.overlay_config.show_original)
        self.show_original_check.stateChanged.connect(self._preview)
        appearance_form.addRow(_tr(self._ui_language, "中英对照", "Bilingual Display"), self.show_original_check)

        self.compact_mode_check = QCheckBox(_tr(self._ui_language, "启用紧凑浮窗", "Enable compact overlay"))
        self.compact_mode_check.setChecked(bool(getattr(self.overlay_config, "compact_mode", False)))
        self.compact_mode_check.stateChanged.connect(self._preview)
        appearance_form.addRow(_tr(self._ui_language, "紧凑模式", "Compact Mode"), self.compact_mode_check)

        self.provider_combo = QComboBox()
        self.provider_combo.setToolTip("OpenAI 兼容适合硅基流动、DeepSeek、Qwen、GLM 和本地模型；Google 使用 Cloud Translation Basic v2")
        self._fill_translation_providers()
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        translation_form.addRow(_tr(self._ui_language, "翻译服务", "Translation Provider"), self.provider_combo)

        self.api_key_input = QLineEdit(self.translation_config.api_key)
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("OpenAI 兼容 API Key")
        self.api_key_input.setToolTip("硅基流动、DeepSeek、Qwen、GLM 或本地兼容服务的 API Key；本地服务不需要时可留空")
        self.api_key_input.editingFinished.connect(self._preview)
        translation_form.addRow("API Key", self.api_key_input)

        translation_test_row = QHBoxLayout()
        self.translation_test_button = QPushButton(_tr(self._ui_language, "测试翻译", "Test Translation"))
        self.translation_test_label = QLabel(_tr(
            self._ui_language,
            "会发送一句测试文本；每次点击只调用一次接口，结束后按钮会短暂冷却。",
            "Sends one test sentence; each click makes one request and then briefly cools down.",
        ))
        self.translation_test_label.setWordWrap(True)
        self.translation_test_button.clicked.connect(self._test_translation)
        translation_test_row.addWidget(self.translation_test_button)
        translation_test_row.addWidget(self.translation_test_label, 1)
        translation_form.addRow(_tr(self._ui_language, "接口测试", "API Test"), translation_test_row)

        self.model_input = QLineEdit(self.translation_config.model)
        self.model_input.setPlaceholderText("tencent/Hunyuan-MT-7B")
        self.model_input.setToolTip("填写服务商要求的模型名，例如 tencent/Hunyuan-MT-7B、deepseek-chat、qwen-plus、glm-4-flash")
        self.model_input.editingFinished.connect(self._preview)
        translation_form.addRow(_tr(self._ui_language, "模型名", "Model"), self.model_input)

        self.endpoint_input = QLineEdit(self.translation_config.endpoint)
        self.endpoint_input.setPlaceholderText("https://api.siliconflow.cn/v1/chat/completions")
        self.endpoint_input.setToolTip("填写 OpenAI 兼容地址；可填完整 /chat/completions URL，也可填以 /v1 结尾的 base_url")
        self.endpoint_input.editingFinished.connect(self._preview)
        translation_form.addRow(_tr(self._ui_language, "兼容地址", "Compatible Endpoint"), self.endpoint_input)
        self._refresh_translation_provider_ui()

        self.model_download_source_combo = QComboBox()
        self.model_download_source_combo.setToolTip("lite 包首次运行会下载 Whisper 模型；大陆网络推荐 ModelScope 国内源")
        self.model_download_endpoint_input = QLineEdit(
            _normalize_model_download_endpoint(getattr(self.whisper_config, "model_download_endpoint", ""))
        )
        self.model_download_endpoint_input.setPlaceholderText("https://your-hf-endpoint.example.com")
        self.model_download_endpoint_input.setToolTip("仅自定义 Hugging Face Endpoint 使用；ModelScope 不是 Hugging Face endpoint")
        self.model_download_endpoint_input.editingFinished.connect(self._preview)
        self._fill_model_download_sources()
        self.model_download_source_combo.currentIndexChanged.connect(self._download_source_changed)
        download_source_row = QHBoxLayout()
        download_source_row.addWidget(self.model_download_source_combo)
        download_source_row.addWidget(self.model_download_endpoint_input)
        advanced_form.addRow(_tr(self._ui_language, "模型下载源", "Model Download Source"), download_source_row)

        self.debug_enabled_check = QCheckBox(_tr(self._ui_language, "显示并记录延迟指标", "Show and log latency metrics"))
        self.debug_enabled_check.setChecked(bool(getattr(self.debug_config, "enabled", False)))
        self.debug_enabled_check.stateChanged.connect(self._preview)
        advanced_form.addRow(_tr(self._ui_language, "调试模式", "Debug Mode"), self.debug_enabled_check)

        self.audio_device_combo = QComboBox()
        self.audio_device_combo.setToolTip("优先选择 [系统声音] 或 Loopback；普通麦克风通常录不到游戏声音")
        self.refresh_audio_button = QPushButton(_tr(self._ui_language, "刷新", "Refresh"))
        self._fill_audio_devices()
        audio_row = QHBoxLayout()
        audio_row.addWidget(self.audio_device_combo)
        audio_row.addWidget(self.refresh_audio_button)
        self.audio_device_combo.currentIndexChanged.connect(self._preview)
        self.refresh_audio_button.clicked.connect(self._request_audio_refresh)
        audio_form.addRow(_tr(self._ui_language, "音频设备", "Audio Device"), audio_row)

        self.audio_test_panel = AudioTestPanel(self._current_audio_config, self, self._ui_language)
        audio_form.addRow(_tr(self._ui_language, "测试音频", "Test Audio"), self.audio_test_panel)

        self.latency_mode_combo = QComboBox()
        self.latency_mode_combo.setToolTip("极速适合竞技游戏；均衡适合默认使用；准确适合直播、会议、慢节奏和口音较重的语音")
        self._fill_latency_modes()
        self.latency_mode_combo.currentIndexChanged.connect(self._latency_mode_changed)
        audio_form.addRow(_tr(self._ui_language, "响应模式", "Response Mode"), self.latency_mode_combo)

        self.whisper_device_combo = QComboBox()
        self.whisper_device_combo.setToolTip("普通用户选 CPU；自动/GPU 需要本机有可用 NVIDIA CUDA 运行环境")
        self._fill_whisper_devices()
        self.whisper_device_combo.currentIndexChanged.connect(self._preview)
        audio_form.addRow(_tr(self._ui_language, "识别设备", "Recognition Device"), self.whisper_device_combo)

        self.latency_hint_label = QLabel()
        self.latency_hint_label.setWordWrap(True)
        self.latency_hint_label.setMinimumHeight(42)
        self.latency_hint_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        advanced_form.addRow(_tr(self._ui_language, "识别调校", "Recognition Tuning"), self.latency_hint_label)

        self.max_speech_seconds_spin = QSpinBox()
        self.max_speech_seconds_spin.setRange(3, 30)
        self.max_speech_seconds_spin.setSuffix(" 秒")
        self.max_speech_seconds_spin.setValue(
            int(round(float(getattr(self.audio_config, "max_speech_seconds", 8) or 8)))
        )
        self.max_speech_seconds_spin.setToolTip("连续有声超过这个时长会强制切成一段，推荐 6-10 秒")
        self.max_speech_seconds_spin.valueChanged.connect(self._preview)
        advanced_form.addRow(_tr(self._ui_language, "最长捕获", "Max Capture"), self.max_speech_seconds_spin)

        self.chunk_duration_spin = QSpinBox()
        self.chunk_duration_spin.setRange(60, 1000)
        self.chunk_duration_spin.setSingleStep(10)
        self.chunk_duration_spin.setSuffix(" ms")
        self.chunk_duration_spin.setValue(int(getattr(self.audio_config, "chunk_duration_ms", 220) or 220))
        self.chunk_duration_spin.setToolTip("单个音频块长度；越小响应越快，CPU/切段压力越高")
        self.chunk_duration_spin.valueChanged.connect(self._preview)
        advanced_form.addRow(_tr(self._ui_language, "音频块", "Audio Chunk"), self.chunk_duration_spin)

        self.speech_threshold_blocks_spin = QSpinBox()
        self.speech_threshold_blocks_spin.setRange(1, 20)
        self.speech_threshold_blocks_spin.setValue(
            int(getattr(self.audio_config, "speech_threshold_blocks", 2) or 2)
        )
        self.speech_threshold_blocks_spin.setToolTip("连续多少个有声块后判定开始说话")
        self.speech_threshold_blocks_spin.valueChanged.connect(self._preview)
        advanced_form.addRow(_tr(self._ui_language, "触发块数", "Trigger Blocks"), self.speech_threshold_blocks_spin)

        self.silence_limit_blocks_spin = QSpinBox()
        self.silence_limit_blocks_spin.setRange(1, 50)
        self.silence_limit_blocks_spin.setValue(int(getattr(self.audio_config, "silence_limit_blocks", 4) or 4))
        self.silence_limit_blocks_spin.setToolTip("连续多少个静音块后切出一段")
        self.silence_limit_blocks_spin.valueChanged.connect(self._preview)
        advanced_form.addRow(_tr(self._ui_language, "静音块数", "Silence Blocks"), self.silence_limit_blocks_spin)

        self.pre_roll_ms_spin = QSpinBox()
        self.pre_roll_ms_spin.setRange(0, 2000)
        self.pre_roll_ms_spin.setSingleStep(50)
        self.pre_roll_ms_spin.setSuffix(" ms")
        self.pre_roll_ms_spin.setValue(int(getattr(self.audio_config, "pre_roll_ms", 450) or 0))
        self.pre_roll_ms_spin.setToolTip("开始说话前保留的缓冲，太小可能丢句首")
        self.pre_roll_ms_spin.valueChanged.connect(self._preview)
        advanced_form.addRow(_tr(self._ui_language, "句首缓冲", "Pre-roll"), self.pre_roll_ms_spin)

        self.speech_idle_timeout_ms_spin = QSpinBox()
        self.speech_idle_timeout_ms_spin.setRange(100, 3000)
        self.speech_idle_timeout_ms_spin.setSingleStep(50)
        self.speech_idle_timeout_ms_spin.setSuffix(" ms")
        self.speech_idle_timeout_ms_spin.setValue(
            int(getattr(self.audio_config, "speech_idle_timeout_ms", 650) or 650)
        )
        self.speech_idle_timeout_ms_spin.setToolTip("有语音缓冲但没有新音频块时，等待多久主动切段")
        self.speech_idle_timeout_ms_spin.valueChanged.connect(self._preview)
        advanced_form.addRow(_tr(self._ui_language, "空闲切段", "Idle Cut"), self.speech_idle_timeout_ms_spin)

        self.min_segment_seconds_spin = QDoubleSpinBox()
        self.min_segment_seconds_spin.setRange(0.0, 3.0)
        self.min_segment_seconds_spin.setSingleStep(0.05)
        self.min_segment_seconds_spin.setDecimals(2)
        self.min_segment_seconds_spin.setSuffix(" 秒")
        self.min_segment_seconds_spin.setValue(
            float(getattr(self.audio_config, "min_segment_seconds", 0.35) or 0.0)
        )
        self.min_segment_seconds_spin.setToolTip("低于这个语音活跃时长的片段会在识别前丢弃；设为 0 可关闭")
        self.min_segment_seconds_spin.valueChanged.connect(self._preview)
        advanced_form.addRow(_tr(self._ui_language, "最短语音", "Minimum Speech"), self.min_segment_seconds_spin)

        self.min_segment_peak_margin_spin = QDoubleSpinBox()
        self.min_segment_peak_margin_spin.setRange(0.0, 12.0)
        self.min_segment_peak_margin_spin.setSingleStep(0.5)
        self.min_segment_peak_margin_spin.setDecimals(1)
        self.min_segment_peak_margin_spin.setSuffix(" dB")
        self.min_segment_peak_margin_spin.setValue(
            float(getattr(self.audio_config, "min_segment_peak_margin_db", 1.5) or 0.0)
        )
        self.min_segment_peak_margin_spin.setToolTip("片段峰值至少要高过当前语音门限的 dB；设为 0 可关闭")
        self.min_segment_peak_margin_spin.valueChanged.connect(self._preview)
        advanced_form.addRow(_tr(self._ui_language, "触发余量", "Trigger Margin"), self.min_segment_peak_margin_spin)
        self._refresh_latency_preset_controls()

        self.toggle_overlay_input = HotkeyCaptureEdit(self.hotkey_config.toggle_overlay)
        self.toggle_translation_input = HotkeyCaptureEdit(self.hotkey_config.toggle_translation)
        self.clear_history_input = HotkeyCaptureEdit(self.hotkey_config.clear_history)
        self.toggle_lock_input = HotkeyCaptureEdit(getattr(self.hotkey_config, "toggle_lock", ""))
        self.toggle_compact_input = HotkeyCaptureEdit(getattr(self.hotkey_config, "toggle_compact", ""))
        self.toggle_overlay_input.hotkey_changed.connect(self._hotkeys_changed)
        self.toggle_translation_input.hotkey_changed.connect(self._hotkeys_changed)
        self.clear_history_input.hotkey_changed.connect(self._hotkeys_changed)
        self.toggle_lock_input.hotkey_changed.connect(self._hotkeys_changed)
        self.toggle_compact_input.hotkey_changed.connect(self._hotkeys_changed)
        hotkey_form.addRow(_tr(self._ui_language, "显示/隐藏", "Show / Hide"), self.toggle_overlay_input)
        hotkey_form.addRow(_tr(self._ui_language, "暂停/恢复", "Pause / Resume"), self.toggle_translation_input)
        hotkey_form.addRow(_tr(self._ui_language, "清空历史", "Clear Subtitles"), self.clear_history_input)
        hotkey_form.addRow(_tr(self._ui_language, "锁定/解锁", "Lock / Unlock"), self.toggle_lock_input)
        hotkey_form.addRow(_tr(self._ui_language, "紧凑模式", "Compact Mode"), self.toggle_compact_input)

        self.help_text_label = QLabel()
        self.help_text_label.setWordWrap(True)
        self.help_text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        help_form.addRow(_tr(self._ui_language, "使用说明", "Guide"), self.help_text_label)
        self._refresh_help_text()

        self.update_enabled_check = QCheckBox(_tr(self._ui_language, "每天自动检查", "Check daily"))
        self.update_enabled_check.setChecked(bool(getattr(self.update_config, "enabled", True)))
        self.update_enabled_check.stateChanged.connect(self._preview)
        self.update_channel_combo = QComboBox()
        self._fill_update_channels()
        self.update_channel_combo.currentIndexChanged.connect(self._preview)
        self.update_check_button = QPushButton(_tr(self._ui_language, "检查更新", "Check for Updates"))
        self.update_check_button.clicked.connect(self._request_update_check)
        self.update_status_label = QLabel(_tr(
            self._ui_language,
            f"当前版本：v{self.app_version or APP_VERSION}",
            f"Current version: v{self.app_version or APP_VERSION}",
        ))
        self.update_status_label.setWordWrap(True)
        update_row = QHBoxLayout()
        update_row.addWidget(self.update_enabled_check)
        update_row.addWidget(self.update_channel_combo)
        update_row.addWidget(self.update_check_button)
        update_row.addWidget(self.update_status_label, 1)
        about_name_label = QLabel(_tr(
            self._ui_language,
            f"{APP_NAME} 游戏语音实时翻译浮窗",
            f"{APP_NAME} real-time game voice translation overlay",
        ))
        about_name_label.setWordWrap(True)
        about_name_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        about_name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        about_name_label.setMinimumWidth(0)
        about_name_label.setMinimumHeight(36)
        about_form.addRow(_tr(self._ui_language, "软件", "App"), about_name_label)

        about_version_label = QLabel(f"v{self.app_version or APP_VERSION}")
        about_version_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        about_form.addRow(_tr(self._ui_language, "版本", "Version"), about_version_label)

        about_desc_label = QLabel(
            _tr(
                self._ui_language,
                "面向 PC 游戏、Discord 语音和直播字幕的开源工具，支持系统声音捕获、Whisper 识别、翻译浮窗和手机端同步。",
                "An open-source tool for PC games, Discord voice, and live captions, with system audio capture, Whisper ASR, overlay subtitles, and mobile mirroring.",
            )
        )
        about_desc_label.setWordWrap(True)
        about_desc_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        about_desc_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        about_desc_label.setMinimumWidth(0)
        about_desc_label.setMinimumHeight(64)
        about_form.addRow(_tr(self._ui_language, "说明", "Description"), about_desc_label)

        about_links_label = QLabel(
            f'<a href="{APP_WEBSITE}">Website</a> · <a href="{GITHUB_URL}">GitHub</a> · '
            f'<a href="{GITHUB_URL}/releases/latest">Release</a>'
        )
        about_links_label.setOpenExternalLinks(True)
        about_links_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        about_form.addRow(_tr(self._ui_language, "链接", "Links"), about_links_label)

        about_form.addRow(_tr(self._ui_language, "检查更新", "Updates"), update_row)
        layout.addWidget(tabs, 1)

        action_row = QHBoxLayout()
        feedback_button = QPushButton(_tr(self._ui_language, "提交反馈", "Submit Feedback"))
        close_button = QPushButton(_tr(self._ui_language, "关闭", "Close"))
        feedback_button.clicked.connect(self._open_feedback_dialog)
        close_button.clicked.connect(self.close)
        action_row.addWidget(feedback_button)
        action_row.addStretch()
        action_row.addWidget(close_button)
        layout.addLayout(action_row)
        self.setLayout(layout)
        self.resize(780, 620)

    def _make_settings_tab(self, tabs: QTabWidget, title: str) -> QFormLayout:
        page = QWidget()
        page_layout = QVBoxLayout()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        page_layout.addLayout(form)
        page_layout.addStretch()
        page.setLayout(page_layout)
        tabs.addTab(page, title)
        return form

    def _fill_ui_languages(self):
        self.ui_language_combo.blockSignals(True)
        self.ui_language_combo.clear()
        selected = normalize_ui_language(getattr(self.app_config, "language", UI_LANGUAGE_ZH))
        selected_row = 0
        for row, (code, label) in enumerate(UI_LANGUAGE_OPTIONS):
            self.ui_language_combo.addItem(label, code)
            if code == selected:
                selected_row = row
        self.ui_language_combo.setCurrentIndex(selected_row)
        self.ui_language_combo.blockSignals(False)

    def _ui_language_changed(self, *args):
        self.app_config.language = normalize_ui_language(self.ui_language_combo.currentData())
        self._ui_language = self.app_config.language
        if hasattr(self, "audio_test_panel"):
            self.audio_test_panel.set_ui_language(self._ui_language)
        self._fill_whisper_devices()
        self._fill_model_download_sources()
        self._fill_latency_modes()
        self._fill_update_channels()
        self._refresh_translation_provider_ui()
        self._refresh_latency_preset_controls()
        self._refresh_help_text()
        self._preview()

    def closeEvent(self, event):
        if hasattr(self, "audio_test_panel"):
            self.audio_test_panel.stop_test()
        self._preview()
        super().closeEvent(event)

    def _fill_audio_devices(self):
        self.audio_device_combo.blockSignals(True)
        self.audio_device_combo.clear()
        self.audio_device_combo.addItem(_tr(self._ui_language, "自动选择", "Auto select"), None)
        selected_row = 0
        selected_index = self.audio_config.input_device_index
        selected_name = (self.audio_config.input_device_name or "").strip()
        selected_device_id = (getattr(self.audio_config, "input_device_id", "") or "").strip()
        for row, device in enumerate(self.audio_devices, start=1):
            index = device.get("index")
            name = device.get("name", "")
            device_id = (device.get("device_id") or "").strip()
            sample_rate = device.get("sample_rate") or 0
            channels = device.get("channels") or 0
            if is_english_ui(self._ui_language):
                device_type = "System audio" if device.get("is_loopback") else "Input"
            else:
                device_type = "系统声音" if device.get("is_loopback") else "输入设备"
            label = f"[{device_type}] [{index}] {name} ({sample_rate}Hz/{channels}ch)"
            self.audio_device_combo.addItem(label, device)
            if selected_device_id and selected_device_id == device_id:
                selected_row = row
            elif selected_row == 0 and selected_name and selected_name == name:
                selected_row = row
            elif selected_row == 0 and not selected_name and selected_index is not None and int(selected_index) == int(index):
                selected_row = row
        self.audio_device_combo.setCurrentIndex(selected_row)
        self.audio_device_combo.blockSignals(False)

    def _fill_whisper_devices(self):
        self.whisper_device_combo.blockSignals(True)
        self.whisper_device_combo.clear()
        selected_device = _normalize_whisper_device(getattr(self.whisper_config, "device", "cpu"))
        selected_row = 0
        for row, (device, label) in enumerate(_whisper_device_options(self._ui_language)):
            self.whisper_device_combo.addItem(label, device)
            if device == selected_device:
                selected_row = row
        self.whisper_device_combo.setCurrentIndex(selected_row)
        self.whisper_device_combo.blockSignals(False)

    def _fill_model_download_sources(self):
        self.model_download_source_combo.blockSignals(True)
        self.model_download_source_combo.clear()
        selected_source = _normalize_model_download_source(
            getattr(self.whisper_config, "model_download_source", "modelscope"),
            getattr(self.whisper_config, "model_download_endpoint", ""),
        )
        selected_row = 0
        for row, (source, label) in enumerate(_model_download_source_options(self._ui_language)):
            self.model_download_source_combo.addItem(label, source)
            if source == selected_source:
                selected_row = row
        self.model_download_source_combo.setCurrentIndex(selected_row)
        self.model_download_source_combo.blockSignals(False)
        self._refresh_model_download_source_ui()

    def _fill_latency_modes(self):
        self.latency_mode_combo.blockSignals(True)
        self.latency_mode_combo.clear()
        selected_mode = normalize_latency_mode(getattr(self.audio_config, "latency_mode", LATENCY_MODE_BALANCED))
        selected_row = 0
        for row, (mode, label) in enumerate(_audio_latency_mode_options(self._ui_language)):
            self.latency_mode_combo.addItem(label, mode)
            if mode == selected_mode:
                selected_row = row
        self.latency_mode_combo.setCurrentIndex(selected_row)
        self.latency_mode_combo.blockSignals(False)

    def _latency_mode_changed(self, *args):
        self.audio_config.latency_mode = normalize_latency_mode(self.latency_mode_combo.currentData())
        self._refresh_latency_preset_controls()
        self._preview()

    def _refresh_latency_preset_controls(self):
        mode = normalize_latency_mode(getattr(self.audio_config, "latency_mode", LATENCY_MODE_BALANCED))
        preset = AUDIO_LATENCY_PRESETS.get(mode)
        is_custom = mode == LATENCY_MODE_CUSTOM
        if hasattr(self, "latency_hint_label"):
            if is_custom:
                self.latency_hint_label.setText(_tr(
                    self._ui_language,
                    "当前为自定义模式。\n可以调整下面的识别切段参数。",
                    "Custom mode is enabled.\nYou can adjust the recognition segmentation parameters below.",
                ))
            else:
                self.latency_hint_label.setText(_tr(
                    self._ui_language,
                    "切段参数由响应模式预设控制。\n要修改请到“语音”页改为“自定义”。",
                    "These segmentation parameters are controlled by the response-mode preset.\nSwitch Response Mode to Custom on the Speech tab before editing them.",
                ))
        controls = (
            ("chunk_duration_ms", "chunk_duration_spin", int),
            ("speech_threshold_blocks", "speech_threshold_blocks_spin", int),
            ("silence_limit_blocks", "silence_limit_blocks_spin", int),
            ("max_speech_seconds", "max_speech_seconds_spin", lambda value: int(round(float(value)))),
            ("pre_roll_ms", "pre_roll_ms_spin", int),
            ("speech_idle_timeout_ms", "speech_idle_timeout_ms_spin", int),
            ("min_segment_seconds", "min_segment_seconds_spin", float),
            ("min_segment_peak_margin_db", "min_segment_peak_margin_spin", float),
        )
        for key, attr, coerce in controls:
            widget = getattr(self, attr, None)
            if not widget:
                continue
            widget.setEnabled(is_custom)
            if preset and key in preset:
                widget.blockSignals(True)
                widget.setValue(coerce(preset[key]))
                widget.blockSignals(False)

    def _download_source_changed(self, *args):
        self._refresh_model_download_source_ui()
        self._preview()

    def _refresh_model_download_source_ui(self):
        selected = self.model_download_source_combo.currentData()
        is_custom = selected == "custom_hf_endpoint"
        self.model_download_endpoint_input.setEnabled(is_custom)
        if not is_custom:
            self.model_download_endpoint_input.setText("")

    def _fill_translation_providers(self):
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        selected_provider = normalize_translation_provider(
            getattr(self.translation_config, "provider", "openai_compatible")
        )
        selected_row = 0
        for row, (provider, label) in enumerate(TRANSLATION_PROVIDERS.items()):
            self.provider_combo.addItem(label, provider)
            if provider == selected_provider:
                selected_row = row
        self.provider_combo.setCurrentIndex(selected_row)
        self.provider_combo.blockSignals(False)

    def _fill_update_channels(self):
        self.update_channel_combo.blockSignals(True)
        self.update_channel_combo.clear()
        selected_channel = normalize_update_channel(getattr(self.update_config, "channel", "stable"))
        selected_row = 0
        for row, (channel, label) in enumerate(_update_channel_options(self._ui_language)):
            self.update_channel_combo.addItem(label, channel)
            if channel == selected_channel:
                selected_row = row
        self.update_channel_combo.setCurrentIndex(selected_row)
        self.update_channel_combo.blockSignals(False)

    def _provider_changed(self, *args):
        self._refresh_translation_provider_ui()
        self._preview()

    def _refresh_translation_provider_ui(self):
        if not hasattr(self, "provider_combo"):
            return
        provider = normalize_translation_provider(self.provider_combo.currentData())
        is_google = provider == "google"
        if hasattr(self, "api_key_input"):
            self.api_key_input.setPlaceholderText(
                "Google Cloud Translation API Key"
                if is_google
                else _tr(self._ui_language, "OpenAI 兼容 API Key", "OpenAI-compatible API Key")
            )
            self.api_key_input.setToolTip(
                _tr(
                    self._ui_language,
                    "Google Cloud 项目中启用 Cloud Translation API 后创建的 API Key",
                    "API Key created after enabling Cloud Translation API in a Google Cloud project",
                )
                if is_google
                else _tr(
                    self._ui_language,
                    "硅基流动、DeepSeek、Qwen、GLM 或本地兼容服务的 API Key；本地服务不需要时可留空",
                    "API Key for SiliconFlow, DeepSeek, Qwen, GLM, or a local compatible service; leave blank when your local service does not need it",
                )
            )
        for widget in (getattr(self, "model_input", None), getattr(self, "endpoint_input", None)):
            if widget:
                widget.setEnabled(not is_google)
        if hasattr(self, "model_input"):
            self.model_input.setToolTip(
                _tr(
                    self._ui_language,
                    "Google Cloud Translation Basic v2 不需要模型名",
                    "Google Cloud Translation Basic v2 does not need a model name",
                )
                if is_google
                else _tr(
                    self._ui_language,
                    "填写服务商要求的模型名，例如 tencent/Hunyuan-MT-7B、deepseek-chat、qwen-plus、glm-4-flash",
                    "Enter the model name required by your provider, such as tencent/Hunyuan-MT-7B, deepseek-chat, qwen-plus, or glm-4-flash",
                )
            )
        if hasattr(self, "endpoint_input"):
            self.endpoint_input.setToolTip(
                _tr(
                    self._ui_language,
                    "Google Cloud Translation Basic v2 使用固定官方接口，不需要填写兼容地址",
                    "Google Cloud Translation Basic v2 uses the official endpoint; no compatible endpoint is needed",
                )
                if is_google
                else _tr(
                    self._ui_language,
                    "填写 OpenAI 兼容地址；可填完整 /chat/completions URL，也可填以 /v1 结尾的 base_url",
                    "Enter an OpenAI-compatible endpoint; a full /chat/completions URL or a /v1 base URL both work",
                )
            )

    def set_audio_devices(self, audio_devices: List[dict], audio_config: AudioDeviceConfig):
        self.audio_devices = audio_devices or []
        self.audio_config = audio_config
        self._fill_audio_devices()
        if hasattr(self, "latency_mode_combo"):
            self._fill_latency_modes()
        self._sync_audio_tuning_controls()
        if hasattr(self, "min_segment_seconds_spin"):
            self.min_segment_seconds_spin.blockSignals(True)
            self.min_segment_seconds_spin.setValue(
                float(getattr(self.audio_config, "min_segment_seconds", 0.35) or 0.0)
            )
            self.min_segment_seconds_spin.blockSignals(False)
        if hasattr(self, "min_segment_peak_margin_spin"):
            self.min_segment_peak_margin_spin.blockSignals(True)
            self.min_segment_peak_margin_spin.setValue(
                float(getattr(self.audio_config, "min_segment_peak_margin_db", 1.5) or 0.0)
            )
            self.min_segment_peak_margin_spin.blockSignals(False)
        self._refresh_latency_preset_controls()

    def _sync_audio_tuning_controls(self):
        controls = (
            ("chunk_duration_ms", "chunk_duration_spin", int, 220),
            ("speech_threshold_blocks", "speech_threshold_blocks_spin", int, 2),
            ("silence_limit_blocks", "silence_limit_blocks_spin", int, 4),
            ("max_speech_seconds", "max_speech_seconds_spin", lambda value: int(round(float(value))), 6),
            ("pre_roll_ms", "pre_roll_ms_spin", int, 450),
            ("speech_idle_timeout_ms", "speech_idle_timeout_ms_spin", int, 650),
        )
        for key, attr, coerce, default in controls:
            widget = getattr(self, attr, None)
            if not widget:
                continue
            widget.blockSignals(True)
            try:
                widget.setValue(coerce(getattr(self.audio_config, key, default) or default))
            except Exception:
                widget.setValue(coerce(default))
            widget.blockSignals(False)

    def _request_audio_refresh(self):
        parent = self.parent()
        if parent and hasattr(parent, "request_audio_device_refresh"):
            parent.request_audio_device_refresh()

    def _request_update_check(self):
        self._collect_values()
        self.settings_changed.emit(
            self.overlay_config,
            self.hotkey_config,
            self.audio_config,
            self.translation_config,
            self.whisper_config,
            self.app_config,
            self.update_config,
        )
        parent = self.parent()
        if parent and hasattr(parent, "request_update_check"):
            parent.request_update_check(manual=True)

    def set_update_checking(self, checking: bool):
        if not hasattr(self, "update_check_button"):
            return
        self.update_check_button.setEnabled(not checking)
        self.update_check_button.setText(_tr(
            self._ui_language,
            "检查中..." if checking else "检查更新",
            "Checking..." if checking else "Check for Updates",
        ))
        if checking:
            self.update_status_label.setText(_tr(self._ui_language, "正在检查更新...", "Checking for updates..."))

    def set_update_check_result(self, result: UpdateCheckResult):
        if not hasattr(self, "update_check_button"):
            return
        self.set_update_checking(False)
        self.update_status_label.setText(self._format_update_status(result))

    def show_pending_update(self, update: UpdateInfo):
        if not hasattr(self, "update_status_label") or not update:
            return
        self.update_status_label.setText(_tr(
            self._ui_language,
            f"发现新版本：{update.display_title()}",
            f"New version available: {update.display_title()}",
        ))

    def _format_update_status(self, result: UpdateCheckResult) -> str:
        status = getattr(result, "status", "")
        update = getattr(result, "update", None)
        if status == "available" and update:
            return _tr(
                self._ui_language,
                f"发现新版本：{update.display_title()}",
                f"New version available: {update.display_title()}",
            )
        if status == "current":
            return _tr(self._ui_language, "当前已是最新版本", "You are on the latest version")
        if status == "ignored" and update:
            return _tr(self._ui_language, f"已忽略 v{update.latest}", f"Ignored v{update.latest}")
        if status == "channel_mismatch":
            return result.message or _tr(self._ui_language, "当前通道没有新版本", "No update on the current channel")
        if status == "disabled":
            return _tr(self._ui_language, "已关闭自动检查更新", "Automatic update checks are disabled")
        if status == "error":
            return _tr(
                self._ui_language,
                f"检查失败：{(result.message or '')[:160]}",
                f"Check failed: {(result.message or '')[:160]}",
            )
        return result.message or _tr(
            self._ui_language,
            f"当前版本：v{self.app_version or APP_VERSION}",
            f"Current version: v{self.app_version or APP_VERSION}",
        )

    def _current_audio_config(self) -> AudioConfig:
        self._collect_values()
        return _copy_audio_config(self.audio_config)

    def _test_translation(self):
        self._collect_values()
        self.translation_test_button.setEnabled(False)
        self.translation_test_button.setText(_tr(self._ui_language, "测试中...", "Testing..."))
        self.translation_test_label.setText(_tr(
            self._ui_language,
            "正在测试翻译接口...",
            "Testing the translation endpoint...",
        ))
        self._translation_test_runner = TranslationTestRunner(
            self.translation_config,
            self._handle_translation_test_result,
        )
        self._translation_test_runner.start()

    def _handle_translation_test_result(self, ok: bool, message: str):
        prefix = _tr(self._ui_language, "成功", "Success") if ok else _tr(self._ui_language, "失败", "Failed")
        separator = ": " if is_english_ui(self._ui_language) else "："
        self.translation_test_label.setText(f"{prefix}{separator}{message}")
        _start_button_cooldown(
            self.translation_test_button,
            _tr(self._ui_language, "测试翻译", "Test Translation"),
        )

    def _hotkeys_changed(self, *args):
        self.hotkey_config.toggle_overlay = self.toggle_overlay_input.text().strip()
        self.hotkey_config.toggle_translation = self.toggle_translation_input.text().strip()
        self.hotkey_config.clear_history = self.clear_history_input.text().strip()
        self.hotkey_config.toggle_lock = self.toggle_lock_input.text().strip()
        self.hotkey_config.toggle_compact = self.toggle_compact_input.text().strip()
        self._refresh_help_text()
        self._preview()

    def _refresh_help_text(self):
        if hasattr(self, "help_text_label"):
            self.help_text_label.setText(_build_help_text(self.hotkey_config, self._ui_language))

    def _open_feedback_dialog(self):
        self._collect_values()
        self._feedback_dialog = FeedbackDialog(self._build_feedback_report(), self._ui_language, self)
        self._feedback_dialog.show()

    def _build_feedback_report(self) -> str:
        selected_device = self.audio_device_combo.currentText() if hasattr(self, "audio_device_combo") else ""
        return _build_feedback_report(
            self.translation_config,
            self.whisper_config,
            self.debug_config,
            self.app_version,
            self.runtime_dir,
            self.last_latency_summary,
            selected_device,
            self._ui_language,
        )

    def _preview(self, *args):
        self._collect_values()
        self.settings_changed.emit(
            self.overlay_config,
            self.hotkey_config,
            self.audio_config,
            self.translation_config,
            self.whisper_config,
            self.app_config,
            self.update_config,
        )

    def _collect_values(self):
        self.overlay_config.opacity = self.opacity_slider.value() / 100
        self.overlay_config.bg_opacity = self.bg_opacity_slider.value() / 100
        self.overlay_config.font_size = self.font_slider.value()
        self.overlay_config.text_color = self.text_color_btn.color()
        self.overlay_config.original_text_color = self.original_color_btn.color()
        self.overlay_config.show_original = self.show_original_check.isChecked()
        self.overlay_config.compact_mode = self.compact_mode_check.isChecked()
        self.app_config.language = normalize_ui_language(self.ui_language_combo.currentData())
        self._ui_language = self.app_config.language

        self.hotkey_config.toggle_overlay = self.toggle_overlay_input.text().strip()
        self.hotkey_config.toggle_translation = self.toggle_translation_input.text().strip()
        self.hotkey_config.clear_history = self.clear_history_input.text().strip()
        self.hotkey_config.toggle_lock = self.toggle_lock_input.text().strip()
        self.hotkey_config.toggle_compact = self.toggle_compact_input.text().strip()
        self.debug_config.enabled = self.debug_enabled_check.isChecked()
        self.update_config.enabled = self.update_enabled_check.isChecked()
        self.update_config.channel = normalize_update_channel(self.update_channel_combo.currentData())

        self.translation_config.provider = normalize_translation_provider(self.provider_combo.currentData())
        self.translation_config.api_key = self.api_key_input.text().strip()
        self.translation_config.model = self.model_input.text().strip() or self.translation_config.model
        self.translation_config.endpoint = self.endpoint_input.text().strip() or self.translation_config.endpoint
        self.whisper_config.device = _normalize_whisper_device(self.whisper_device_combo.currentData())
        selected_download_source = self.model_download_source_combo.currentData()
        self.whisper_config.model_download_source = _normalize_model_download_source(
            selected_download_source,
            self.model_download_endpoint_input.text(),
        )
        if self.whisper_config.model_download_source == "custom_hf_endpoint":
            self.whisper_config.model_download_endpoint = _normalize_model_download_endpoint(
                self.model_download_endpoint_input.text()
            )
        else:
            self.whisper_config.model_download_endpoint = ""

        device = self.audio_device_combo.currentData()
        if device:
            self.audio_config.input_device_index = int(device.get("index"))
            self.audio_config.input_device_name = device.get("name", "")
            self.audio_config.input_device_id = device.get("device_id", "")
        else:
            self.audio_config.input_device_index = None
            self.audio_config.input_device_name = ""
            self.audio_config.input_device_id = ""
        self.audio_config.latency_mode = normalize_latency_mode(self.latency_mode_combo.currentData())
        self.audio_config.chunk_duration_ms = int(self.chunk_duration_spin.value())
        self.audio_config.speech_threshold_blocks = int(self.speech_threshold_blocks_spin.value())
        self.audio_config.silence_limit_blocks = int(self.silence_limit_blocks_spin.value())
        self.audio_config.max_buffer_blocks = int(getattr(self.audio_config, "max_buffer_blocks", 120) or 120)
        self.audio_config.max_speech_seconds = float(self.max_speech_seconds_spin.value())
        self.audio_config.pre_roll_ms = int(self.pre_roll_ms_spin.value())
        self.audio_config.speech_idle_timeout_ms = int(self.speech_idle_timeout_ms_spin.value())
        self.audio_config.min_segment_seconds = float(self.min_segment_seconds_spin.value())
        self.audio_config.min_segment_peak_margin_db = float(self.min_segment_peak_margin_spin.value())
        preset = AUDIO_LATENCY_PRESETS.get(self.audio_config.latency_mode)
        if preset:
            for key, value in preset.items():
                if hasattr(self.audio_config, key):
                    setattr(self.audio_config, key, value)


class OverlayLockButton(QToolButton):
    """Separate top-level button so the overlay can be mouse-transparent while locked."""

    def __init__(self, owner):
        super().__init__(None)
        self._owner = owner
        flags = Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool
        if hasattr(Qt, "WindowDoesNotAcceptFocus"):
            flags |= Qt.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("floatingLockButton")
        self.setFixedSize(28, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.clicked.connect(owner._toggle_lock)

    def closeEvent(self, event):
        event.accept()


class GameOverlay(QWidget):
    """游戏浮窗叠加层"""

    def __init__(
        self,
        config: OverlayConfig = None,
        hotkeys: HotkeyConfig = None,
        audio_config: AudioDeviceConfig = None,
        translation_config: TranslationConfig = None,
        audio_devices: Optional[List[dict]] = None,
        whisper_config=None,
        app_config: RuntimeConfig = None,
        debug_config: DebugConfig = None,
        update_config: UpdateSettings = None,
        app_version: str = "",
        runtime_dir: str = "",
        get_last_latency_summary: Optional[Callable[[], dict]] = None,
        on_settings_changed: Optional[
            Callable[
                [OverlayConfig, HotkeyConfig, AudioDeviceConfig, TranslationConfig, object, RuntimeConfig, UpdateSettings],
                None,
            ]
        ] = None,
        on_audio_devices_refresh: Optional[Callable[[], List[dict]]] = None,
        on_update_check_requested: Optional[Callable[[bool], None]] = None,
        on_update_version_ignored: Optional[Callable[[str], None]] = None,
        on_shutdown_requested: Optional[Callable[[], None]] = None,
        on_overlay_updated: Optional[Callable[[str], None]] = None,
    ):
        super().__init__()
        self.config = config or OverlayConfig()
        self.hotkeys = hotkeys or HotkeyConfig()
        self.audio_config = audio_config or AudioDeviceConfig()
        self.translation_config = translation_config or TranslationConfig()
        self.audio_devices = audio_devices or []
        self.whisper_config = whisper_config or WhisperDeviceConfig()
        self.app_config = app_config or RuntimeConfig()
        self.app_config.language = normalize_ui_language(getattr(self.app_config, "language", UI_LANGUAGE_ZH))
        self.debug_config = debug_config or DebugConfig()
        self.update_config = update_config or UpdateSettings()
        self.app_version = app_version or APP_VERSION
        self.runtime_dir = runtime_dir
        self._get_last_latency_summary = get_last_latency_summary
        self._on_settings_changed = on_settings_changed
        self._on_audio_devices_refresh = on_audio_devices_refresh
        self._on_update_check_requested = on_update_check_requested
        self._on_update_version_ignored = on_update_version_ignored
        self._on_shutdown_requested = on_shutdown_requested
        self._on_overlay_updated = on_overlay_updated
        self._translations: deque = deque(maxlen=self.config.max_lines)
        self._signals = OverlaySignals()
        self._dragging = False
        self._drag_pos = None
        self._resizing = False
        self._resize_start_pos = None
        self._resize_start_size = None
        self._initializing_geometry = True
        self._syncing_language_controls = False
        self._settings_dialog = None
        self._first_run_wizard = None
        self._fullscreen_help_dialog = None
        self._paused = False
        self._pending_update: Optional[UpdateInfo] = None
        self._update_prompt_dialog = None
        self._update_notice_shown_version = ""
        self._fade_timer = QTimer()
        self._fade_timer.timeout.connect(self._update_fade)
        self._fade_timer.start(100)

        self._init_ui()
        self._connect_signals()

    def _ui_language(self) -> str:
        return normalize_ui_language(getattr(self.app_config or RuntimeConfig(), "language", UI_LANGUAGE_ZH))

    def _init_ui(self):
        """初始化界面"""
        # 窗口属性
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        compact = bool(getattr(self.config, "compact_mode", False))
        self.setMinimumSize(260 if compact else 360, 92 if compact else 150)
        self.setMaximumSize(980, 520)

        self._restore_window_geometry()

        # 主布局
        self._layout = QVBoxLayout()
        self._layout.setContentsMargins(10, 8, 10, 10)
        self._layout.setSpacing(6)
        self.setLayout(self._layout)

        # 顶部工具条
        self._toolbar = QFrame()
        self._toolbar.setObjectName("toolbar")
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(2, 0, 2, 0)
        toolbar_layout.setSpacing(6)
        self._toolbar.setLayout(toolbar_layout)

        ui_language = self._ui_language()
        self._source_lang_combo = self._create_language_combo(_tr(ui_language, "识别语言", "Recognition Language"))
        self._source_lang_combo.setObjectName("languageCombo")
        toolbar_layout.addWidget(self._source_lang_combo)

        self._swap_lang_button = QToolButton()
        self._swap_lang_button.setObjectName("languageSwapButton")
        self._swap_lang_button.setIcon(_make_icon("swap", self.config.original_text_color))
        self._swap_lang_button.setToolTip(_tr(ui_language, "交换识别语言和翻译目标语言", "Swap recognition and target languages"))
        self._swap_lang_button.setFixedSize(28, 24)
        self._swap_lang_button.setCursor(Qt.PointingHandCursor)
        self._swap_lang_button.clicked.connect(self._swap_language_flow)
        toolbar_layout.addWidget(self._swap_lang_button)

        self._target_lang_combo = self._create_language_combo(_tr(ui_language, "翻译目标语言", "Target Language"))
        self._target_lang_combo.setObjectName("languageCombo")
        toolbar_layout.addWidget(self._target_lang_combo)
        self._sync_language_controls()
        self._source_lang_combo.currentIndexChanged.connect(self._language_combo_changed)
        self._target_lang_combo.currentIndexChanged.connect(self._language_combo_changed)

        toolbar_layout.addStretch()

        self._compact_button = QToolButton()
        self._compact_button.setObjectName("compactButton")
        self._compact_button.setFixedSize(28, 24)
        self._compact_button.setCursor(Qt.PointingHandCursor)
        self._compact_button.clicked.connect(self.toggle_compact_mode)
        toolbar_layout.addWidget(self._compact_button)

        self._qr_button = QToolButton()
        self._qr_button.setObjectName("qrButton")
        self._qr_button.setIcon(_make_icon("qr", self.config.text_color))
        self._qr_button.setToolTip(_tr(ui_language, "手机二维码", "Mobile QR Code"))
        self._qr_button.setFixedSize(28, 24)
        self._qr_button.setCursor(Qt.PointingHandCursor)
        self._qr_button.installEventFilter(self)
        toolbar_layout.addWidget(self._qr_button)

        self._settings_button = QToolButton()
        self._settings_button.setObjectName("settingsButton")
        self._settings_button.setIcon(_make_icon("settings", self.config.text_color))
        self._settings_button.setToolTip(_tr(ui_language, "浮窗设置", "Overlay Settings"))
        self._settings_button.setFixedSize(28, 24)
        self._settings_button.setCursor(Qt.PointingHandCursor)
        self._settings_button.clicked.connect(self._open_settings)
        toolbar_layout.addWidget(self._settings_button)
        self._settings_badge = QLabel(self._settings_button)
        self._settings_badge.setObjectName("settingsBadge")
        self._settings_badge.setFixedSize(9, 9)
        self._settings_badge.setStyleSheet(
            "background: #F04438; border: 1px solid #FFFFFF; border-radius: 4px;"
        )
        self._settings_badge.hide()

        self._quit_button = QToolButton()
        self._quit_button.setObjectName("quitButton")
        self._quit_button.setIcon(_make_icon("close", self.config.text_color))
        self._quit_button.setToolTip(_tr(ui_language, "退出程序", "Quit"))
        self._quit_button.setFixedSize(28, 24)
        self._quit_button.setCursor(Qt.PointingHandCursor)
        self._quit_button.clicked.connect(self._request_shutdown)
        toolbar_layout.addWidget(self._quit_button)

        self._lock_slot = QWidget()
        self._lock_slot.setFixedSize(28, 24)
        toolbar_layout.addWidget(self._lock_slot)
        self._layout.addWidget(self._toolbar)

        self._lock_button = OverlayLockButton(self)

        self._qr_popup = QFrame(None)
        self._qr_popup.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self._qr_popup.setAttribute(Qt.WA_ShowWithoutActivating)
        self._qr_popup.setObjectName("qrPopup")
        self._qr_popup.installEventFilter(self)
        qr_layout = QVBoxLayout()
        qr_layout.setContentsMargins(8, 8, 8, 8)
        qr_layout.setSpacing(6)
        self._qr_popup.setLayout(qr_layout)
        self._qr_widget = QrCodeWidget(parent=self._qr_popup)
        self._qr_url_label = QLabel()
        self._qr_url_label.setObjectName("qrUrl")
        self._qr_url_label.setAlignment(Qt.AlignCenter)
        self._qr_url_label.setWordWrap(True)
        qr_layout.addWidget(self._qr_widget)
        qr_layout.addWidget(self._qr_url_label)
        self._qr_popup.hide()
        self._setup_qr_code()

        # 翻译内容容器
        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("translationScrollArea")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._content_widget = QWidget()
        self._content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(4)
        self._content_widget.setLayout(self._content_layout)
        self._scroll_area.setWidget(self._content_widget)
        self._layout.addWidget(self._scroll_area, 1)
        self._content_layout.addStretch(1)

        # 翻译标签池
        self._labels: List[QLabel] = []
        for i in range(self.config.max_lines):
            label = QLabel()
            label.setWordWrap(True)
            label.setTextFormat(Qt.RichText)
            label.setTextInteractionFlags(Qt.NoTextInteraction)
            label.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            label.setContentsMargins(0, 0, 0, 0)
            label.setMinimumWidth(0)
            label.hide()
            self._content_layout.addWidget(label)
            self._labels.append(label)

        # 设置窗口透明度
        self.setWindowOpacity(self.config.opacity)
        self._apply_styles()
        self._refresh_control_state()
        self._refresh_compact_mode()
        self._refresh_lock_state()
        self._initializing_geometry = False
        QTimer.singleShot(0, self._position_lock_button)

    def _restore_window_geometry(self):
        primary = QApplication.primaryScreen().geometry()
        screen = primary
        for item in QApplication.screens():
            screen = screen.united(item.geometry())
        width = max(260, min(980, int(getattr(self.config, "window_width", 500) or 500)))
        height = max(92, min(520, int(getattr(self.config, "window_height", 200) or 200)))
        self.resize(width, height)
        x = getattr(self.config, "window_x", None)
        y = getattr(self.config, "window_y", None)
        if x is None or y is None:
            x, y = self._default_position_for_size(primary, width, height)
        x = max(screen.left(), min(int(x), screen.right() - width + 1))
        y = max(screen.top(), min(int(y), screen.bottom() - height + 1))
        self.move(x, y)
        self._remember_window_geometry()

    def _default_position_for_size(self, screen: QRect, width: int, height: int):
        if self.config.position == "top":
            return (screen.width() - width) // 2, 50
        if self.config.position == "right":
            return screen.width() - width - 20, (screen.height() - height) // 2
        if self.config.position == "left":
            return 20, (screen.height() - height) // 2
        return (screen.width() - width) // 2, screen.height() - height - 50

    def _remember_window_geometry(self):
        if not hasattr(self, "config"):
            return
        self.config.window_width = int(self.width())
        self.config.window_height = int(self.height())
        self.config.window_x = int(self.x())
        self.config.window_y = int(self.y())

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QLabel {{
                font-family: "{self.config.font_family}";
            }}
            QLabel#title {{
                color: {self.config.original_text_color};
                font-size: {max(10, self.config.font_size - 4)}px;
                padding: 0 2px;
            }}
            QComboBox#languageCombo {{
                color: {self.config.original_text_color};
                background: transparent;
                border: 0;
                padding: 1px 9px 1px 1px;
                font-size: {max(10, self.config.font_size - 3)}px;
            }}
            QComboBox#languageCombo:hover {{
                background: rgba(255, 255, 255, 24);
            }}
            QComboBox#languageCombo::drop-down {{
                border: 0;
                width: 10px;
            }}
            QToolButton#languageSwapButton {{
                background: transparent;
                border: 0;
                border-radius: 4px;
            }}
            QToolButton#languageSwapButton:hover {{
                background: rgba(255, 255, 255, 28);
            }}
            QToolButton#compactButton, QToolButton#qrButton, QToolButton#settingsButton, QToolButton#quitButton {{
                background: rgba(18, 24, 33, 150);
                border: 1px solid {self.config.text_color};
                border-radius: 4px;
            }}
            QToolButton#compactButton:hover, QToolButton#qrButton:hover,
            QToolButton#settingsButton:hover, QToolButton#quitButton:hover {{
                background: rgba(40, 60, 48, 210);
            }}
            QFrame#qrPopup {{
                background: rgba(255, 255, 255, 245);
                border: 1px solid #D7DFEA;
                border-radius: 6px;
            }}
            QScrollArea#translationScrollArea {{
                background: transparent;
                border: 0;
            }}
            QScrollArea#translationScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
            QScrollArea#translationScrollArea QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 0;
            }}
            QScrollArea#translationScrollArea QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 75);
                border-radius: 3px;
                min-height: 18px;
            }}
            QScrollArea#translationScrollArea QScrollBar::add-line:vertical,
            QScrollArea#translationScrollArea QScrollBar::sub-line:vertical {{
                height: 0;
                border: 0;
            }}
            QLabel#qrUrl {{
                color: #1B2430;
                font-size: 10px;
                background: transparent;
            }}
        """)
        if hasattr(self, "_source_lang_combo"):
            self._fit_language_combo_width(self._source_lang_combo)
        if hasattr(self, "_target_lang_combo"):
            self._fit_language_combo_width(self._target_lang_combo)
        if hasattr(self, "_lock_button"):
            self._style_lock_button()
        if hasattr(self, "_settings_badge"):
            self._position_settings_badge()

    def _create_language_combo(self, tooltip: str) -> QComboBox:
        combo = QComboBox()
        combo.setToolTip(tooltip)
        combo.setCursor(Qt.PointingHandCursor)
        for code, label in _speech_language_options(self._ui_language()):
            combo.addItem(label, code)
        self._fit_language_combo_width(combo)
        return combo

    def _refresh_language_combo_items(self, combo: QComboBox, current: str):
        combo.blockSignals(True)
        combo.clear()
        for code, label in _speech_language_options(self._ui_language()):
            combo.addItem(label, code)
        self._set_combo_language(combo, current)
        self._fit_language_combo_width(combo)
        combo.blockSignals(False)

    def _fit_language_combo_width(self, combo: QComboBox):
        combo.ensurePolished()
        metrics = combo.fontMetrics()
        text_width = max(
            metrics.horizontalAdvance(combo.itemText(index))
            for index in range(combo.count())
        )
        combo.setFixedWidth(text_width + 18)

    def _sync_language_controls(self):
        source = _normalize_language_code(self.translation_config.source_lang, "en")
        target = _normalize_language_code(self.translation_config.target_lang, OPPOSITE_LANGUAGE[source])
        if target == source:
            target = OPPOSITE_LANGUAGE[source]

        self.translation_config.source_lang = source
        self.translation_config.target_lang = target
        self._syncing_language_controls = True
        try:
            self._refresh_language_combo_items(self._source_lang_combo, source)
            self._refresh_language_combo_items(self._target_lang_combo, target)
            self._set_combo_language(self._source_lang_combo, source)
            self._set_combo_language(self._target_lang_combo, target)
            self._fit_language_combo_width(self._source_lang_combo)
            self._fit_language_combo_width(self._target_lang_combo)
        finally:
            self._syncing_language_controls = False

    def _set_combo_language(self, combo: QComboBox, language: str):
        for index in range(combo.count()):
            if combo.itemData(index) == language:
                combo.setCurrentIndex(index)
                return

    def _language_combo_changed(self, *args):
        if self._syncing_language_controls:
            return
        source = _normalize_language_code(self._source_lang_combo.currentData(), "en")
        target = _normalize_language_code(self._target_lang_combo.currentData(), "zh")
        sender = self.sender()
        if source == target:
            if sender is self._source_lang_combo:
                target = OPPOSITE_LANGUAGE[source]
            else:
                source = OPPOSITE_LANGUAGE[target]
        self._apply_language_flow(source, target)

    def _swap_language_flow(self):
        source = _normalize_language_code(self.translation_config.source_lang, "en")
        target = _normalize_language_code(self.translation_config.target_lang, OPPOSITE_LANGUAGE[source])
        self._apply_language_flow(target, source)

    def _apply_language_flow(self, source: str, target: str):
        self.translation_config.source_lang = _normalize_language_code(source, "en")
        self.translation_config.target_lang = _normalize_language_code(
            target,
            OPPOSITE_LANGUAGE[self.translation_config.source_lang],
        )
        if self.translation_config.source_lang == self.translation_config.target_lang:
            self.translation_config.target_lang = OPPOSITE_LANGUAGE[self.translation_config.source_lang]
        self._sync_language_controls()
        if self._on_settings_changed:
            self._on_settings_changed(
                self.config,
                self.hotkeys,
                self.audio_config,
                self.translation_config,
                self.whisper_config,
                self.app_config,
                self.update_config,
            )

    def _setup_qr_code(self):
        url = self.config.mobile_url or self._guess_mobile_url()
        self._qr_widget.set_text(url)
        self._qr_url_label.setText(url)
        self._qr_popup.setToolTip(url)
        self._qr_popup.adjustSize()

    def _refresh_control_state(self):
        ui_language = self._ui_language()
        if hasattr(self, "_compact_button"):
            compact = bool(getattr(self.config, "compact_mode", False))
            self._compact_button.setIcon(_make_icon("expand" if compact else "compact", self.config.text_color))
            self._compact_button.setToolTip(_tr(
                ui_language,
                "退出紧凑浮窗" if compact else "紧凑浮窗",
                "Exit compact overlay" if compact else "Compact overlay",
            ))
        if hasattr(self, "_qr_button"):
            self._qr_button.setToolTip(_tr(ui_language, "手机二维码", "Mobile QR Code"))
        if hasattr(self, "_settings_button"):
            self._settings_button.setToolTip(_tr(ui_language, "浮窗设置", "Overlay Settings"))
        if hasattr(self, "_swap_lang_button"):
            self._swap_lang_button.setToolTip(_tr(ui_language, "交换识别语言和翻译目标语言", "Swap recognition and target languages"))
        if hasattr(self, "_source_lang_combo"):
            self._source_lang_combo.setToolTip(_tr(ui_language, "识别语言", "Recognition Language"))
        if hasattr(self, "_target_lang_combo"):
            self._target_lang_combo.setToolTip(_tr(ui_language, "翻译目标语言", "Target Language"))
        if hasattr(self, "_quit_button"):
            self._quit_button.setToolTip(_tr(ui_language, "退出程序", "Quit"))

    def set_paused(self, paused: bool):
        self._paused = bool(paused)
        self._refresh_control_state()
        self._signals.pause_state_changed.emit(self._paused)

    def is_paused(self) -> bool:
        return bool(self._paused)

    def clear_history(self):
        self._clear_history()

    def show_settings(self):
        self._open_settings()

    def toggle_compact_mode(self, *args):
        self.set_compact_mode(not bool(getattr(self.config, "compact_mode", False)))

    def set_compact_mode(self, compact: bool):
        compact = bool(compact)
        if bool(getattr(self.config, "compact_mode", False)) == compact:
            self._refresh_compact_mode()
            return
        self.config.compact_mode = compact
        self._refresh_compact_mode()
        self._notify_settings_changed()

    def _refresh_compact_mode(self):
        if not hasattr(self, "_toolbar"):
            return
        compact = bool(getattr(self.config, "compact_mode", False))
        for widget in (
            getattr(self, "_source_lang_combo", None),
            getattr(self, "_swap_lang_button", None),
            getattr(self, "_target_lang_combo", None),
        ):
            if widget:
                widget.setVisible(not compact)
        self._layout.setContentsMargins(7 if compact else 10, 5 if compact else 8, 7 if compact else 10, 7 if compact else 10)
        self._layout.setSpacing(3 if compact else 6)
        self._content_layout.setSpacing(2 if compact else 4)
        self.setMinimumSize(260 if compact else 360, 92 if compact else 150)
        self._remember_window_geometry()
        self._refresh_control_state()
        self._refresh_labels()
        self.update()

    def show_fullscreen_help(self):
        if self._fullscreen_help_dialog and self._fullscreen_help_dialog.isVisible():
            self._fullscreen_help_dialog.raise_()
            self._fullscreen_help_dialog.activateWindow()
            return
        self._fullscreen_help_dialog = FullscreenHelpDialog(self.hotkeys, self._ui_language(), self)
        self._fullscreen_help_dialog.show()
        self._fullscreen_help_dialog.raise_()
        self._fullscreen_help_dialog.activateWindow()

    def _guess_mobile_url(self) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                host = sock.getsockname()[0]
        except Exception:
            host = "127.0.0.1"
        return f"http://{host}:8765/mobile"

    def eventFilter(self, watched, event):
        if watched is self._qr_button:
            if self._is_locked():
                self._qr_popup.hide()
                return True
            if event.type() == QEvent.Enter:
                self._show_qr_popup()
            elif event.type() == QEvent.Leave:
                QTimer.singleShot(140, self._hide_qr_popup_if_unhovered)
        elif watched is self._qr_popup:
            if event.type() == QEvent.Leave:
                QTimer.singleShot(140, self._hide_qr_popup_if_unhovered)
        return super().eventFilter(watched, event)

    def _show_qr_popup(self):
        if self._is_locked():
            self._qr_popup.hide()
            return
        self._qr_popup.adjustSize()
        button_rect = self._qr_button.rect()
        button_top_left = self._qr_button.mapToGlobal(button_rect.topLeft())
        screen = QApplication.screenAt(button_top_left) or QApplication.primaryScreen()
        available = screen.availableGeometry()
        x = button_top_left.x() + self._qr_button.width() - self._qr_popup.width()
        y = button_top_left.y() + self._qr_button.height() + 6
        if y + self._qr_popup.height() > available.bottom():
            y = button_top_left.y() - self._qr_popup.height() - 6
        x = max(available.left() + 8, min(x, available.right() - self._qr_popup.width() - 8))
        y = max(available.top() + 8, min(y, available.bottom() - self._qr_popup.height() - 8))
        self._qr_popup.move(x, y)
        self._qr_popup.show()
        self._qr_popup.raise_()

    def _hide_qr_popup_if_unhovered(self):
        if not hasattr(self, "_qr_popup") or not self._qr_popup.isVisible():
            return
        cursor_pos = QCursor.pos()
        if self._qr_button.rect().contains(self._qr_button.mapFromGlobal(cursor_pos)):
            return
        if self._qr_popup.rect().contains(self._qr_popup.mapFromGlobal(cursor_pos)):
            return
        self._qr_popup.hide()

    def _open_settings(self):
        if self._is_locked():
            return
        if self._settings_dialog and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        self._settings_dialog = SettingsDialog(
            self.config,
            self.hotkeys,
            self.audio_config,
            self.translation_config,
            self.audio_devices,
            self.whisper_config,
            self.app_config,
            self.debug_config,
            self.update_config,
            self.app_version,
            self.runtime_dir,
            self._current_latency_summary(),
            self,
        )
        self._settings_dialog.settings_changed.connect(self._apply_settings)
        if self._pending_update:
            self._settings_dialog.show_pending_update(self._pending_update)
        self._set_update_badge_visible(False)
        self._settings_dialog.show()

    def show_first_run_wizard(self, on_completed: Optional[Callable[[], None]] = None):
        if self._first_run_wizard and self._first_run_wizard.isVisible():
            self._first_run_wizard.raise_()
            self._first_run_wizard.activateWindow()
            return
        self._first_run_wizard = FirstRunWizard(
            self.audio_config,
            self.translation_config,
            self.audio_devices,
            self.whisper_config,
            self.app_config,
            self.debug_config,
            self.app_version,
            self.runtime_dir,
            self._current_latency_summary,
            self._on_audio_devices_refresh,
            self,
        )
        self._first_run_wizard.setup_completed.connect(
            lambda: self._handle_first_run_completed(on_completed)
        )
        self._first_run_wizard.show()
        self._first_run_wizard.raise_()
        self._first_run_wizard.activateWindow()

    def _handle_first_run_completed(self, on_completed: Optional[Callable[[], None]] = None):
        self._sync_language_controls()
        if on_completed:
            on_completed()

    def refresh_language(self):
        self._sync_language_controls()
        self._refresh_control_state()
        self._refresh_lock_state()
        if self._fullscreen_help_dialog and self._fullscreen_help_dialog.isVisible():
            self._fullscreen_help_dialog.close()
            self._fullscreen_help_dialog = None
        if self._settings_dialog and self._settings_dialog.isVisible():
            old_dialog = self._settings_dialog
            tab_index = 0
            tabs = old_dialog.findChild(QTabWidget)
            if tabs:
                tab_index = tabs.currentIndex()
            old_dialog.close()
            self._settings_dialog = None
            QTimer.singleShot(0, lambda index=tab_index: self._reopen_settings_at(index))

    def _reopen_settings_at(self, tab_index: int):
        self._open_settings()
        if self._settings_dialog:
            tabs = self._settings_dialog.findChild(QTabWidget)
            if tabs:
                tabs.setCurrentIndex(max(0, min(int(tab_index or 0), tabs.count() - 1)))

    def _current_latency_summary(self) -> dict:
        if self._get_last_latency_summary:
            try:
                return self._get_last_latency_summary() or {}
            except Exception:
                return {}
        return {}

    def _is_locked(self) -> bool:
        return bool(getattr(self.config, "locked", False))

    def _can_interact(self) -> bool:
        return not self._is_locked() and bool(getattr(self.config, "draggable", True))

    def _toggle_lock(self, checked=None):
        self.config.locked = not self._is_locked()
        self._refresh_lock_state()
        self._notify_settings_changed()

    def _refresh_lock_state(self):
        locked = self._is_locked()
        self.config.locked = locked
        self._dragging = False
        self._resizing = False
        self.setCursor(Qt.ArrowCursor)
        self._qr_popup.hide()
        if locked and self._settings_dialog and self._settings_dialog.isVisible():
            self._settings_dialog.close()

        for widget in (
            self._source_lang_combo,
            self._target_lang_combo,
            self._swap_lang_button,
            self._compact_button,
            self._qr_button,
            self._settings_button,
            self._quit_button,
        ):
            widget.setEnabled(not locked)

        self._lock_button.setChecked(locked)
        self._lock_button.setIcon(_make_icon("unlock" if locked else "lock", self.config.text_color))
        self._lock_button.setToolTip(_tr(
            self._ui_language(),
            "解锁浮窗" if locked else "锁定浮窗",
            "Unlock overlay" if locked else "Lock overlay",
        ))
        self._style_lock_button()
        self._set_overlay_mouse_passthrough(locked)
        self._position_lock_button()
        self._set_update_badge_visible(bool(self._pending_update))
        self.update()

    def _style_lock_button(self):
        border_color = self.config.text_color
        bg = "rgba(35, 58, 42, 230)" if self._is_locked() else "rgba(18, 24, 33, 150)"
        self._lock_button.setStyleSheet(f"""
            QToolButton#floatingLockButton {{
                background: {bg};
                border: 1px solid {border_color};
                border-radius: 4px;
            }}
            QToolButton#floatingLockButton:hover {{
                background: rgba(40, 60, 48, 230);
            }}
        """)

    def _set_overlay_mouse_passthrough(self, enabled: bool):
        self.setAttribute(Qt.WA_TransparentForMouseEvents, enabled)
        if not sys.platform.startswith("win"):
            return
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            gwl_exstyle = -20
            ws_ex_transparent = 0x00000020
            ws_ex_layered = 0x00080000
            ws_ex_noactivate = 0x08000000
            if ctypes.sizeof(ctypes.c_void_p) == 8:
                get_window_long = user32.GetWindowLongPtrW
                set_window_long = user32.SetWindowLongPtrW
                get_window_long.restype = ctypes.c_longlong
                set_window_long.restype = ctypes.c_longlong
            else:
                get_window_long = user32.GetWindowLongW
                set_window_long = user32.SetWindowLongW
                get_window_long.restype = ctypes.c_long
                set_window_long.restype = ctypes.c_long
            get_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
            set_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int, get_window_long.restype]
            style = int(get_window_long(hwnd, gwl_exstyle))
            style |= ws_ex_layered | ws_ex_noactivate
            if enabled:
                style |= ws_ex_transparent
            else:
                style &= ~ws_ex_transparent
            set_window_long(hwnd, gwl_exstyle, style)
        except Exception:
            pass

    def _position_lock_button(self):
        if not hasattr(self, "_lock_button") or not hasattr(self, "_lock_slot"):
            return
        self._lock_button.move(self._lock_slot.mapToGlobal(QPoint(0, 0)))
        if self.isVisible():
            self._lock_button.show()
            self._lock_button.raise_()

    def _position_settings_badge(self):
        if not hasattr(self, "_settings_badge") or not hasattr(self, "_settings_button"):
            return
        self._settings_badge.move(
            max(0, self._settings_button.width() - self._settings_badge.width() - 1),
            1,
        )
        self._settings_badge.raise_()

    def _set_update_badge_visible(self, visible: bool):
        if hasattr(self, "_settings_badge"):
            self._settings_badge.setVisible(bool(visible) and not self._is_locked())
            self._position_settings_badge()

    def request_update_check(self, manual: bool = False):
        if self._on_update_check_requested:
            self.set_update_checking(True)
            self._on_update_check_requested(bool(manual))
            return
        self.set_update_checking(False)

    def set_update_checking(self, checking: bool):
        self._signals.update_checking.emit(bool(checking))

    def handle_update_check_result(self, result: UpdateCheckResult, manual: bool = False):
        self._signals.update_check_result.emit(result, bool(manual))

    def _handle_update_checking(self, checking: bool):
        if self._settings_dialog and self._settings_dialog.isVisible():
            self._settings_dialog.set_update_checking(checking)

    def _handle_update_check_result(self, result: UpdateCheckResult, manual: bool = False):
        self._handle_update_checking(False)
        if self._settings_dialog and self._settings_dialog.isVisible():
            self._settings_dialog.set_update_check_result(result)

        update = getattr(result, "update", None)
        if getattr(result, "status", "") == "available" and update:
            self._pending_update = update
            self._set_update_badge_visible(True)
            self._show_update_prompt(update, manual)
            return

        if manual:
            self._pending_update = None
            self._set_update_badge_visible(False)

    def _show_update_prompt(self, update: UpdateInfo, manual: bool = False):
        if not update:
            return
        version = str(getattr(update, "latest", "") or "").strip()
        if not manual and version == self._update_notice_shown_version:
            return
        self._update_notice_shown_version = version
        if self._update_prompt_dialog and self._update_prompt_dialog.isVisible():
            self._update_prompt_dialog.raise_()
            self._update_prompt_dialog.activateWindow()
            return
        self._update_prompt_dialog = UpdatePromptDialog(
            update,
            self.app_version,
            self._ignore_update_version,
            self._ui_language(),
            self,
        )
        self._update_prompt_dialog.show()
        self._update_prompt_dialog.raise_()
        self._update_prompt_dialog.activateWindow()

    def _ignore_update_version(self, version: str):
        self.update_config.ignored_version = str(version or "").strip().lstrip("v")
        self._pending_update = None
        self._set_update_badge_visible(False)
        if self._settings_dialog and self._settings_dialog.isVisible():
            self._settings_dialog.set_update_check_result(
                UpdateCheckResult("ignored", message=f"已忽略 v{self.update_config.ignored_version}")
            )
        if self._on_update_version_ignored:
            self._on_update_version_ignored(self.update_config.ignored_version)

    def _notify_settings_changed(self):
        if self._on_settings_changed:
            self._on_settings_changed(
                self.config,
                self.hotkeys,
                self.audio_config,
                self.translation_config,
                self.whisper_config,
                self.app_config,
                self.update_config,
            )

    def _apply_settings(
        self,
        overlay_config: OverlayConfig,
        hotkey_config: HotkeyConfig,
        audio_config: AudioDeviceConfig,
        translation_config: TranslationConfig,
        whisper_config,
        app_config: RuntimeConfig,
        update_config,
    ):
        self._remember_window_geometry()
        overlay_config.window_x = self.config.window_x
        overlay_config.window_y = self.config.window_y
        overlay_config.window_width = self.config.window_width
        overlay_config.window_height = self.config.window_height
        self.config = overlay_config
        self.hotkeys = hotkey_config
        self.audio_config = audio_config
        self.translation_config = translation_config
        self.whisper_config = whisper_config
        self.app_config = app_config or self.app_config or RuntimeConfig()
        self.app_config.language = normalize_ui_language(getattr(self.app_config, "language", UI_LANGUAGE_ZH))
        self.update_config = update_config or self.update_config
        self.setWindowOpacity(self.config.opacity)
        self._apply_styles()
        self._qr_button.setIcon(_make_icon("qr", self.config.text_color))
        self._swap_lang_button.setIcon(_make_icon("swap", self.config.original_text_color))
        self._settings_button.setIcon(_make_icon("settings", self.config.text_color))
        self._quit_button.setIcon(_make_icon("close", self.config.text_color))
        self._sync_language_controls()
        self._refresh_control_state()
        self._refresh_compact_mode()
        self._refresh_labels()
        self._refresh_lock_state()
        self.update()
        if self._on_settings_changed:
            self._on_settings_changed(
                self.config,
                self.hotkeys,
                self.audio_config,
                self.translation_config,
                self.whisper_config,
                self.app_config,
                self.update_config,
            )

    def request_audio_device_refresh(self):
        if self._on_audio_devices_refresh:
            self.audio_devices = self._on_audio_devices_refresh() or []
        if self._settings_dialog:
            self._settings_dialog.set_audio_devices(self.audio_devices, self.audio_config)
        if self._first_run_wizard and self._first_run_wizard.isVisible():
            self._first_run_wizard.audio_devices = self.audio_devices
            self._first_run_wizard._fill_wizard_audio_devices()

    def _request_shutdown(self):
        if self._on_shutdown_requested:
            self._on_shutdown_requested()
        else:
            app = QApplication.instance()
            if app:
                app.quit()

    def _connect_signals(self):
        """连接信号"""
        self._signals.new_translation.connect(self._add_translation)
        self._signals.new_translation_with_id.connect(self._add_translation_with_id)
        self._signals.update_translation.connect(self._update_translation)
        self._signals.clear_history.connect(self._clear_history)
        self._signals.toggle_visibility.connect(self._toggle_visibility)
        self._signals.toggle_lock.connect(self._toggle_lock)
        self._signals.toggle_compact.connect(self.toggle_compact_mode)
        self._signals.update_checking.connect(self._handle_update_checking)
        self._signals.update_check_result.connect(self._handle_update_check_result)

    def add_translation(self, original: str, translated: str):
        """线程安全地添加翻译"""
        self._signals.new_translation.emit(original, translated)

    def add_translation_with_id(self, item_id: str, original: str, translated: str):
        """线程安全地添加可更新的翻译记录。"""
        self._signals.new_translation_with_id.emit(item_id, original, translated)

    def update_translation(self, item_id: str, translated: str):
        """线程安全地更新已有翻译记录。"""
        self._signals.update_translation.emit(item_id, translated)

    def _add_translation(self, original: str, translated: str):
        """添加翻译到浮窗"""
        item = TranslationItem(original, translated, self.config.fade_duration)
        self._append_translation_item(item)

    def _add_translation_with_id(self, item_id: str, original: str, translated: str):
        """添加一条之后可用 item_id 更新的翻译。"""
        item = TranslationItem(original, translated, self.config.fade_duration, item_id=item_id)
        self._append_translation_item(item)

    def _append_translation_item(self, item: TranslationItem):
        self._translations.append(item)

        # 更新标签显示
        items = list(self._translations)
        self._render_items(items)

        # 启动淡出计时器
        if len(self._translations) >= self.config.max_lines:
            oldest = self._translations[0]
            if oldest.fade_start is None:
                oldest.start_fade()

    def _update_translation(self, item_id: str, translated: str):
        """更新已有翻译内容，避免慢翻译显示到下一条上。"""
        for item in self._translations:
            if item.item_id == item_id:
                item.translated = translated
                item.fade_start = None
                item.timestamp = time.time()
                self._refresh_labels()
                if self._on_overlay_updated:
                    self._on_overlay_updated(item_id)
                return

    def _update_fade(self):
        """更新淡出效果"""
        needs_update = False
        to_remove = []

        for item in self._translations:
            if item.fade_start is not None and item.opacity <= 0:
                to_remove.append(item)
                needs_update = True

        for item in to_remove:
            self._translations.remove(item)

        if needs_update:
            self._refresh_labels()

    def _refresh_labels(self):
        if not hasattr(self, "_labels") or not hasattr(self, "_content_widget"):
            return
        self._render_items(list(self._translations))

    def _render_items(self, items: List[TranslationItem]):
        text_width = self._label_text_width()
        visible_items = []

        for item in items[-self.config.max_lines:]:
            html = self._format_item(item, item.opacity, text_width)
            height = self._measure_html_height(html, text_width)
            visible_items.append((html, height))

        for i, label in enumerate(self._labels):
            if i < len(visible_items):
                html, height = visible_items[i]
                label.setText(html)
                label.setFixedHeight(max(1, height))
                label.show()
                label.updateGeometry()
            else:
                label.clear()
                label.setFixedHeight(0)
                label.hide()
        self._content_layout.activate()
        self._content_widget.updateGeometry()
        if hasattr(self, "_scroll_area"):
            QTimer.singleShot(0, self._scroll_to_latest)

    def _label_text_width(self) -> int:
        if hasattr(self, "_scroll_area"):
            width = self._scroll_area.viewport().contentsRect().width()
        else:
            width = self._content_widget.contentsRect().width()
        if width <= 0:
            margins = self._layout.contentsMargins()
            width = self.width() - margins.left() - margins.right()
        return max(40, int(width) - 2)

    def _measure_html_height(self, html: str, width: int) -> int:
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(self.font())
        doc.setHtml(html)
        doc.setTextWidth(max(80, width))
        return max(1, int(doc.size().height()) + 3)

    def _format_item(self, item: TranslationItem, opacity: float, width: int = None) -> str:
        translated = self._escape(self._normalize_display_text(item.translated))
        original = self._escape(self._normalize_display_text(item.original))
        main_color = self._rgba_css(self.config.text_color, opacity)
        original_color = self._rgba_css(self.config.original_text_color, opacity * 0.92)
        family = self._escape(self.config.font_family).replace('"', "&quot;")
        if self.config.show_original:
            return (
                f'<div style="font-family:&quot;{family}&quot;; font-size:{self.config.font_size}px; '
                f'line-height:1.25; margin:0; color:{main_color}; white-space:normal; '
                f'word-wrap:break-word;">'
                f'{translated}</div>'
                f'<div style="font-family:&quot;{family}&quot;; font-size:{max(10, self.config.font_size - 4)}px; '
                f'line-height:1.2; margin:2px 0 0 0; color:{original_color}; '
                f'white-space:normal; word-wrap:break-word;">{original}</div>'
            )
        return (
            f'<div style="font-family:&quot;{family}&quot;; font-size:{self.config.font_size}px; '
            f'line-height:1.25; margin:0; color:{main_color}; white-space:normal; '
            f'word-wrap:break-word;">{translated}</div>'
        )

    def _normalize_display_text(self, text: str) -> str:
        return " ".join((text or "").split())

    def _scroll_to_latest(self):
        if not hasattr(self, "_scroll_area"):
            return
        bar = self._scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _escape(self, text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _rgba_css(self, color: str, opacity: float) -> str:
        qcolor = QColor(color)
        alpha = max(0, min(255, int(opacity * 255)))
        return f"rgba({qcolor.red()}, {qcolor.green()}, {qcolor.blue()}, {alpha})"

    def _clear_history(self):
        """清除翻译历史"""
        self._translations.clear()
        for label in self._labels:
            label.hide()

    def _toggle_visibility(self):
        """切换可见性"""
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def paintEvent(self, event):
        """绘制半透明背景"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 背景
        bg_color = QColor(self.config.bg_color)
        if not bg_color.isValid():
            bg_color = QColor("#20242A")
        bg_opacity = max(0.0, min(1.0, float(getattr(self.config, "bg_opacity", 0.82))))
        bg_color.setAlpha(int(bg_opacity * 255))
        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.rect(), 10, 10)

        # 边框
        pen = QPen(QColor(self.config.text_color))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(
            self.rect().adjusted(1, 1, -1, -1), 10, 10
        )

        # 右下角尺寸拖拽手柄
        if not self._can_interact():
            return
        handle_color = QColor(self.config.text_color)
        handle_color.setAlpha(150)
        painter.setPen(QPen(handle_color, 1))
        for offset in (7, 12, 17):
            painter.drawLine(
                self.width() - offset,
                self.height() - 4,
                self.width() - 4,
                self.height() - offset,
            )

    def _resize_hit_test(self, pos) -> bool:
        return self._can_interact() and pos.x() >= self.width() - 24 and pos.y() >= self.height() - 24

    def mousePressEvent(self, event):
        """鼠标按下 - 开始拖拽"""
        if not self._can_interact():
            event.ignore()
            return
        if event.button() == Qt.LeftButton and self._resize_hit_test(event.pos()):
            self._resizing = True
            self._resize_start_pos = event.globalPos()
            self._resize_start_size = self.size()
            event.accept()
        elif event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        """鼠标移动 - 拖拽窗口"""
        if not self._can_interact():
            self.setCursor(Qt.ArrowCursor)
            event.ignore()
            return
        if self._resizing and event.buttons() == Qt.LeftButton:
            delta = event.globalPos() - self._resize_start_pos
            width = max(self.minimumWidth(), min(self.maximumWidth(), self._resize_start_size.width() + delta.x()))
            height = max(self.minimumHeight(), min(self.maximumHeight(), self._resize_start_size.height() + delta.y()))
            self.resize(width, height)
            self.config.window_width = width
            self.config.window_height = height
            self._refresh_labels()
            event.accept()
        elif self._dragging and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()
        else:
            self.setCursor(Qt.SizeFDiagCursor if self._resize_hit_test(event.pos()) else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        """鼠标释放 - 结束拖拽"""
        was_resizing = self._resizing
        self._dragging = False
        self._resizing = False
        self.setCursor(Qt.ArrowCursor)
        self._refresh_labels()
        if was_resizing or event.button() == Qt.LeftButton:
            self._remember_window_geometry()
            self._notify_settings_changed()
        event.accept()

    def enable_drag(self):
        """启用拖拽（临时关闭鼠标穿透）"""
        self.config.draggable = True
        if not self._is_locked():
            self._set_overlay_mouse_passthrough(False)
        self.update()

    def disable_drag(self):
        """禁用拖拽（恢复鼠标穿透）"""
        self.config.draggable = False
        self._set_overlay_mouse_passthrough(True)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._remember_window_geometry()
        self._refresh_labels()
        self._position_lock_button()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._remember_window_geometry()
        self._position_lock_button()
        if hasattr(self, "_qr_popup") and self._qr_popup.isVisible():
            self._show_qr_popup()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_lock_state()
        self._refresh_labels()
        self._position_lock_button()

    def hideEvent(self, event):
        if hasattr(self, "_lock_button"):
            self._lock_button.hide()
        if hasattr(self, "_qr_popup"):
            self._qr_popup.hide()
        super().hideEvent(event)

    def set_position(self, position: str):
        """设置浮窗位置"""
        screen = QApplication.primaryScreen().geometry()
        positions = {
            "top": (screen.width() // 2 - self.width() // 2, 50),
            "bottom": (screen.width() // 2 - self.width() // 2,
                       screen.height() - self.height() - 50),
            "left": (20, screen.height() // 2 - self.height() // 2),
            "right": (screen.width() - self.width() - 20,
                      screen.height() // 2 - self.height() // 2),
        }
        if position in positions:
            self.move(*positions[position])
            self.config.position = position
            self._remember_window_geometry()
            self._notify_settings_changed()

    def closeEvent(self, event):
        """关闭事件"""
        self._fade_timer.stop()
        if hasattr(self, "_lock_button"):
            self._lock_button.close()
        if hasattr(self, "_qr_popup"):
            self._qr_popup.close()
        super().closeEvent(event)
