"""
游戏浮窗叠加层
使用 PyQt5 创建透明置顶窗口，显示翻译结果
"""

import socket
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, List, Optional

from PyQt5.QtCore import (
    Qt, QEvent, QPoint, QRect, QTimer, pyqtSignal, QObject
)
from PyQt5.QtGui import (
    QColor, QPainter, QPen, QBrush, QIcon, QPixmap
)
from PyQt5.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QApplication, QPushButton, QFrame, QDialog, QFormLayout,
    QSlider, QCheckBox, QLineEdit, QColorDialog, QToolButton, QComboBox,
    QSpinBox
)

from qr_widget import QrCodeWidget
from translator import TranslationConfig


@dataclass
class OverlayConfig:
    font_size: int = 16
    font_family: str = "Microsoft YaHei"
    text_color: str = "#00FF00"
    bg_color: str = "#000000AA"
    position: str = "bottom"
    max_lines: int = 5
    fade_duration: int = 5
    window_width: int = 500
    window_height: int = 200
    opacity: float = 0.85
    original_text_color: str = "#B7C4D8"
    show_original: bool = True
    draggable: bool = True
    mobile_url: str = ""


@dataclass
class HotkeyConfig:
    toggle_overlay: str = "ctrl+shift+t"
    toggle_translation: str = "ctrl+alt+s"
    clear_history: str = "ctrl+alt+c"


@dataclass
class AudioDeviceConfig:
    input_device_index: Optional[int] = None
    input_device_name: str = ""
    input_device_id: str = ""
    max_speech_seconds: float = 8.0


class TranslationItem:
    """单条翻译记录"""

    def __init__(self, original: str, translated: str, fade_duration: int, timestamp: float = None):
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
    clear_history = pyqtSignal()
    toggle_visibility = pyqtSignal()
    settings_changed = pyqtSignal(object, object, object, object)
    refresh_audio_devices = pyqtSignal()


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
    else:
        painter.drawLine(8, 8, 20, 20)
        painter.drawLine(20, 8, 8, 20)

    painter.end()
    return QIcon(pixmap)


class SettingsDialog(QDialog):
    """Graphical settings for overlay and hotkeys."""

    settings_changed = pyqtSignal(object, object, object, object)

    def __init__(
        self,
        overlay_config: OverlayConfig,
        hotkey_config: HotkeyConfig,
        audio_config: AudioDeviceConfig = None,
        translation_config: TranslationConfig = None,
        audio_devices: Optional[List[dict]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("浮窗设置")
        self.setWindowFlags(self.windowFlags() | Qt.Tool)
        self.overlay_config = overlay_config
        self.hotkey_config = hotkey_config
        self.audio_config = audio_config or AudioDeviceConfig()
        self.translation_config = translation_config or TranslationConfig()
        self.audio_devices = audio_devices or []
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(int(self.overlay_config.opacity * 100))
        self.opacity_label = QLabel(f"{self.opacity_slider.value()}%")
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity_slider)
        opacity_row.addWidget(self.opacity_label)
        self.opacity_slider.valueChanged.connect(lambda value: self.opacity_label.setText(f"{value}%"))
        self.opacity_slider.valueChanged.connect(self._preview)
        form.addRow("透明度", opacity_row)

        self.font_slider = QSlider(Qt.Horizontal)
        self.font_slider.setRange(12, 30)
        self.font_slider.setValue(self.overlay_config.font_size)
        self.font_label = QLabel(f"{self.font_slider.value()}px")
        font_row = QHBoxLayout()
        font_row.addWidget(self.font_slider)
        font_row.addWidget(self.font_label)
        self.font_slider.valueChanged.connect(lambda value: self.font_label.setText(f"{value}px"))
        self.font_slider.valueChanged.connect(self._preview)
        form.addRow("字号", font_row)

        self.text_color_btn = ColorButton(self.overlay_config.text_color)
        self.text_color_btn.color_changed.connect(self._preview)
        form.addRow("译文颜色", self.text_color_btn)

        self.original_color_btn = ColorButton(self.overlay_config.original_text_color)
        self.original_color_btn.color_changed.connect(self._preview)
        form.addRow("原文颜色", self.original_color_btn)

        self.show_original_check = QCheckBox("显示英文原文")
        self.show_original_check.setChecked(self.overlay_config.show_original)
        self.show_original_check.stateChanged.connect(self._preview)
        form.addRow("中英对照", self.show_original_check)

        self.api_key_input = QLineEdit(self.translation_config.api_key)
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("OpenAI 兼容 API Key")
        self.api_key_input.setToolTip("硅基流动、DeepSeek、Qwen、GLM 或本地兼容服务的 API Key；本地服务不需要时可留空")
        self.api_key_input.editingFinished.connect(self._preview)
        form.addRow("API Key", self.api_key_input)

        self.model_input = QLineEdit(self.translation_config.model)
        self.model_input.setPlaceholderText("Qwen/Qwen2.5-7B-Instruct")
        self.model_input.setToolTip("填写服务商要求的模型名，例如 Qwen/Qwen2.5-7B-Instruct、deepseek-chat、qwen-plus、glm-4-flash")
        self.model_input.editingFinished.connect(self._preview)
        form.addRow("模型名", self.model_input)

        self.endpoint_input = QLineEdit(self.translation_config.endpoint)
        self.endpoint_input.setPlaceholderText("https://api.siliconflow.cn/v1/chat/completions")
        self.endpoint_input.setToolTip("填写 OpenAI 兼容地址；可填完整 /chat/completions URL，也可填以 /v1 结尾的 base_url")
        self.endpoint_input.editingFinished.connect(self._preview)
        form.addRow("兼容地址", self.endpoint_input)

        self.audio_device_combo = QComboBox()
        self.audio_device_combo.setToolTip("优先选择 [系统声音] 或 Loopback；普通麦克风通常录不到游戏声音")
        self.refresh_audio_button = QPushButton("刷新")
        self._fill_audio_devices()
        audio_row = QHBoxLayout()
        audio_row.addWidget(self.audio_device_combo)
        audio_row.addWidget(self.refresh_audio_button)
        self.audio_device_combo.currentIndexChanged.connect(self._preview)
        self.refresh_audio_button.clicked.connect(self._request_audio_refresh)
        form.addRow("音频设备", audio_row)

        self.max_speech_seconds_spin = QSpinBox()
        self.max_speech_seconds_spin.setRange(3, 30)
        self.max_speech_seconds_spin.setSuffix(" 秒")
        self.max_speech_seconds_spin.setValue(
            int(round(float(getattr(self.audio_config, "max_speech_seconds", 8) or 8)))
        )
        self.max_speech_seconds_spin.setToolTip("连续有声超过这个时长会强制切成一段，推荐 6-10 秒")
        self.max_speech_seconds_spin.valueChanged.connect(self._preview)
        form.addRow("最长捕获", self.max_speech_seconds_spin)

        self.toggle_overlay_input = HotkeyCaptureEdit(self.hotkey_config.toggle_overlay)
        self.toggle_translation_input = HotkeyCaptureEdit(self.hotkey_config.toggle_translation)
        self.clear_history_input = HotkeyCaptureEdit(self.hotkey_config.clear_history)
        self.toggle_overlay_input.hotkey_changed.connect(self._preview)
        self.toggle_translation_input.hotkey_changed.connect(self._preview)
        self.clear_history_input.hotkey_changed.connect(self._preview)
        form.addRow("显示/隐藏", self.toggle_overlay_input)
        form.addRow("暂停/恢复", self.toggle_translation_input)
        form.addRow("清空历史", self.clear_history_input)

        layout.addLayout(form)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)
        self.setLayout(layout)
        self.resize(640, 500)

    def closeEvent(self, event):
        self._preview()
        super().closeEvent(event)

    def _fill_audio_devices(self):
        self.audio_device_combo.blockSignals(True)
        self.audio_device_combo.clear()
        self.audio_device_combo.addItem("自动选择", None)
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

    def set_audio_devices(self, audio_devices: List[dict], audio_config: AudioDeviceConfig):
        self.audio_devices = audio_devices or []
        self.audio_config = audio_config
        self._fill_audio_devices()
        if hasattr(self, "max_speech_seconds_spin"):
            self.max_speech_seconds_spin.blockSignals(True)
            self.max_speech_seconds_spin.setValue(
                int(round(float(getattr(self.audio_config, "max_speech_seconds", 8) or 8)))
            )
            self.max_speech_seconds_spin.blockSignals(False)

    def _request_audio_refresh(self):
        parent = self.parent()
        if parent and hasattr(parent, "request_audio_device_refresh"):
            parent.request_audio_device_refresh()

    def _preview(self, *args):
        self._collect_values()
        self.settings_changed.emit(
            self.overlay_config,
            self.hotkey_config,
            self.audio_config,
            self.translation_config,
        )

    def _collect_values(self):
        self.overlay_config.opacity = self.opacity_slider.value() / 100
        self.overlay_config.font_size = self.font_slider.value()
        self.overlay_config.text_color = self.text_color_btn.color()
        self.overlay_config.original_text_color = self.original_color_btn.color()
        self.overlay_config.show_original = self.show_original_check.isChecked()

        self.hotkey_config.toggle_overlay = self.toggle_overlay_input.text().strip() or self.hotkey_config.toggle_overlay
        self.hotkey_config.toggle_translation = self.toggle_translation_input.text().strip() or self.hotkey_config.toggle_translation
        self.hotkey_config.clear_history = self.clear_history_input.text().strip() or self.hotkey_config.clear_history

        self.translation_config.api_key = self.api_key_input.text().strip()
        self.translation_config.model = self.model_input.text().strip() or self.translation_config.model
        self.translation_config.endpoint = self.endpoint_input.text().strip() or self.translation_config.endpoint

        device = self.audio_device_combo.currentData()
        if device:
            self.audio_config.input_device_index = int(device.get("index"))
            self.audio_config.input_device_name = device.get("name", "")
            self.audio_config.input_device_id = device.get("device_id", "")
        else:
            self.audio_config.input_device_index = None
            self.audio_config.input_device_name = ""
            self.audio_config.input_device_id = ""
        self.audio_config.max_speech_seconds = float(self.max_speech_seconds_spin.value())


class GameOverlay(QWidget):
    """游戏浮窗叠加层"""

    def __init__(
        self,
        config: OverlayConfig = None,
        hotkeys: HotkeyConfig = None,
        audio_config: AudioDeviceConfig = None,
        translation_config: TranslationConfig = None,
        audio_devices: Optional[List[dict]] = None,
        on_settings_changed: Optional[Callable[[OverlayConfig, HotkeyConfig, AudioDeviceConfig, TranslationConfig], None]] = None,
        on_audio_devices_refresh: Optional[Callable[[], List[dict]]] = None,
        on_shutdown_requested: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.config = config or OverlayConfig()
        self.hotkeys = hotkeys or HotkeyConfig()
        self.audio_config = audio_config or AudioDeviceConfig()
        self.translation_config = translation_config or TranslationConfig()
        self.audio_devices = audio_devices or []
        self._on_settings_changed = on_settings_changed
        self._on_audio_devices_refresh = on_audio_devices_refresh
        self._on_shutdown_requested = on_shutdown_requested
        self._translations: deque = deque(maxlen=self.config.max_lines)
        self._signals = OverlaySignals()
        self._dragging = False
        self._drag_pos = None
        self._resizing = False
        self._resize_start_pos = None
        self._resize_start_size = None
        self._settings_dialog = None
        self._fade_timer = QTimer()
        self._fade_timer.timeout.connect(self._update_fade)
        self._fade_timer.start(100)

        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        """初始化界面"""
        # 窗口属性
        self.setWindowTitle("Game Voice Translator")
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        # 窗口大小和位置
        screen = QApplication.primaryScreen().geometry()
        self.resize(self.config.window_width, self.config.window_height)

        if self.config.position == "bottom":
            x = (screen.width() - self.config.window_width) // 2
            y = screen.height() - self.config.window_height - 50
        elif self.config.position == "top":
            x = (screen.width() - self.config.window_width) // 2
            y = 50
        elif self.config.position == "right":
            x = screen.width() - self.config.window_width - 20
            y = (screen.height() - self.config.window_height) // 2
        else:
            x = (screen.width() - self.config.window_width) // 2
            y = screen.height() - self.config.window_height - 50

        self.move(x, y)

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

        self._title_label = QLabel("实时翻译")
        self._title_label.setObjectName("title")
        toolbar_layout.addWidget(self._title_label)
        toolbar_layout.addStretch()

        self._qr_button = QToolButton()
        self._qr_button.setObjectName("qrButton")
        self._qr_button.setIcon(_make_icon("qr", self.config.text_color))
        self._qr_button.setToolTip("手机二维码")
        self._qr_button.setFixedSize(28, 24)
        self._qr_button.setCursor(Qt.PointingHandCursor)
        self._qr_button.installEventFilter(self)
        toolbar_layout.addWidget(self._qr_button)

        self._settings_button = QToolButton()
        self._settings_button.setObjectName("settingsButton")
        self._settings_button.setIcon(_make_icon("settings", self.config.text_color))
        self._settings_button.setToolTip("浮窗设置")
        self._settings_button.setFixedSize(28, 24)
        self._settings_button.setCursor(Qt.PointingHandCursor)
        self._settings_button.clicked.connect(self._open_settings)
        toolbar_layout.addWidget(self._settings_button)

        self._quit_button = QToolButton()
        self._quit_button.setObjectName("quitButton")
        self._quit_button.setIcon(_make_icon("close", self.config.text_color))
        self._quit_button.setToolTip("退出程序")
        self._quit_button.setFixedSize(28, 24)
        self._quit_button.setCursor(Qt.PointingHandCursor)
        self._quit_button.clicked.connect(self._request_shutdown)
        toolbar_layout.addWidget(self._quit_button)
        self._layout.addWidget(self._toolbar)

        self._qr_popup = QFrame(self)
        self._qr_popup.setObjectName("qrPopup")
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
        self._content_widget = QWidget()
        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(2)
        self._content_widget.setLayout(self._content_layout)
        self._layout.addWidget(self._content_widget)

        # 翻译标签池
        self._labels: List[QLabel] = []
        for i in range(self.config.max_lines):
            label = QLabel()
            label.setWordWrap(True)
            label.setTextFormat(Qt.RichText)
            label.setTextInteractionFlags(Qt.NoTextInteraction)
            label.hide()
            self._content_layout.addWidget(label)
            self._labels.append(label)

        self._content_layout.addStretch()
        self.setMinimumSize(360, 150)
        self.setMaximumSize(980, 520)

        # 设置窗口透明度
        self.setWindowOpacity(self.config.opacity)
        self._apply_styles()

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
            QToolButton#qrButton, QToolButton#settingsButton, QToolButton#quitButton {{
                background: rgba(18, 24, 33, 150);
                border: 1px solid {self.config.text_color};
                border-radius: 4px;
            }}
            QToolButton#qrButton:hover, QToolButton#settingsButton:hover, QToolButton#quitButton:hover {{
                background: rgba(40, 60, 48, 210);
            }}
            QFrame#qrPopup {{
                background: rgba(255, 255, 255, 245);
                border: 1px solid #D7DFEA;
                border-radius: 6px;
            }}
            QLabel#qrUrl {{
                color: #1B2430;
                font-size: 10px;
                background: transparent;
            }}
        """)

    def _setup_qr_code(self):
        url = self.config.mobile_url or self._guess_mobile_url()
        self._qr_widget.set_text(url)
        self._qr_url_label.setText(url)
        self._qr_popup.setToolTip(url)
        self._qr_popup.adjustSize()

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
            if event.type() == QEvent.Enter:
                self._show_qr_popup()
            elif event.type() == QEvent.Leave:
                self._qr_popup.hide()
        return super().eventFilter(watched, event)

    def _show_qr_popup(self):
        x = max(8, self.width() - self._qr_popup.width() - 8)
        y = self._toolbar.height() + 6
        self._qr_popup.move(x, y)
        self._qr_popup.show()
        self._qr_popup.raise_()

    def _open_settings(self):
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
            self,
        )
        self._settings_dialog.settings_changed.connect(self._apply_settings)
        self._settings_dialog.show()

    def _apply_settings(
        self,
        overlay_config: OverlayConfig,
        hotkey_config: HotkeyConfig,
        audio_config: AudioDeviceConfig,
        translation_config: TranslationConfig,
    ):
        self.config = overlay_config
        self.hotkeys = hotkey_config
        self.audio_config = audio_config
        self.translation_config = translation_config
        self.setWindowOpacity(self.config.opacity)
        self._apply_styles()
        self._qr_button.setIcon(_make_icon("qr", self.config.text_color))
        self._settings_button.setIcon(_make_icon("settings", self.config.text_color))
        self._quit_button.setIcon(_make_icon("close", self.config.text_color))
        self._refresh_labels()
        self.update()
        if self._on_settings_changed:
            self._on_settings_changed(
                self.config,
                self.hotkeys,
                self.audio_config,
                self.translation_config,
            )

    def request_audio_device_refresh(self):
        if self._on_audio_devices_refresh:
            self.audio_devices = self._on_audio_devices_refresh() or []
        if self._settings_dialog:
            self._settings_dialog.set_audio_devices(self.audio_devices, self.audio_config)

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
        self._signals.clear_history.connect(self._clear_history)
        self._signals.toggle_visibility.connect(self._toggle_visibility)

    def add_translation(self, original: str, translated: str):
        """线程安全地添加翻译"""
        self._signals.new_translation.emit(original, translated)

    def _add_translation(self, original: str, translated: str):
        """添加翻译到浮窗"""
        item = TranslationItem(original, translated, self.config.fade_duration)
        self._translations.append(item)

        # 更新标签显示
        items = list(self._translations)
        self._render_items(items)

        # 启动淡出计时器
        if len(self._translations) >= self.config.max_lines:
            oldest = self._translations[0]
            if oldest.fade_start is None:
                oldest.start_fade()

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
        self._render_items(list(self._translations))

    def _render_items(self, items: List[TranslationItem]):
        for i, label in enumerate(self._labels):
            if i < len(items):
                item = items[i]
                label.setText(self._format_item(item, item.opacity))
                label.show()
            else:
                label.hide()

    def _format_item(self, item: TranslationItem, opacity: float) -> str:
        translated = self._escape(self._truncate(item.translated, 90))
        original = self._escape(self._truncate(item.original, 120))
        main_color = self._rgba_css(self.config.text_color, opacity)
        original_color = self._rgba_css(self.config.original_text_color, opacity * 0.92)
        if self.config.show_original:
            return (
                f'<div style="font-size:{self.config.font_size}px; color:{main_color};">'
                f'{translated}</div>'
                f'<div style="font-size:{max(10, self.config.font_size - 4)}px; '
                f'color:{original_color}; margin-top:2px;">{original}</div>'
            )
        return f'<div style="font-size:{self.config.font_size}px; color:{main_color};">{translated}</div>'

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit - 1] + "..."

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
        return pos.x() >= self.width() - 24 and pos.y() >= self.height() - 24

    def mousePressEvent(self, event):
        """鼠标按下 - 开始拖拽"""
        if event.button() == Qt.LeftButton and self._resize_hit_test(event.pos()):
            self._resizing = True
            self._resize_start_pos = event.globalPos()
            self._resize_start_size = self.size()
            event.accept()
        elif self.config.draggable and event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        """鼠标移动 - 拖拽窗口"""
        if self._resizing and event.buttons() == Qt.LeftButton:
            delta = event.globalPos() - self._resize_start_pos
            width = max(self.minimumWidth(), min(self.maximumWidth(), self._resize_start_size.width() + delta.x()))
            height = max(self.minimumHeight(), min(self.maximumHeight(), self._resize_start_size.height() + delta.y()))
            self.resize(width, height)
            self.config.window_width = width
            self.config.window_height = height
            event.accept()
        elif self._dragging and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()
        else:
            self.setCursor(Qt.SizeFDiagCursor if self._resize_hit_test(event.pos()) else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        """鼠标释放 - 结束拖拽"""
        self._dragging = False
        self._resizing = False
        self.setCursor(Qt.ArrowCursor)
        event.accept()

    def enable_drag(self):
        """启用拖拽（临时关闭鼠标穿透）"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

    def disable_drag(self):
        """禁用拖拽（恢复鼠标穿透）"""
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

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

    def closeEvent(self, event):
        """关闭事件"""
        self._fade_timer.stop()
        super().closeEvent(event)
