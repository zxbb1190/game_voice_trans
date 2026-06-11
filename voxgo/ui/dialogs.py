"""Dialog windows used by the VoxGo overlay UI."""

import webbrowser
from typing import Callable, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from voxgo.app_info import APP_VERSION
from voxgo.audio.capture import AudioConfig
from voxgo.i18n import UI_LANGUAGE_ZH, is_english_ui, normalize_ui_language
from voxgo.translation import TRANSLATION_PROVIDERS, TranslationConfig, normalize_translation_provider
from voxgo.update.checker import UpdateInfo
from voxgo.ui.config_models import (
    AudioDeviceConfig,
    DebugConfig,
    HotkeyConfig,
    RuntimeConfig,
    WhisperDeviceConfig,
    _build_feedback_report,
    _copy_audio_config,
    _device_label,
    _hotkey_label,
    _hotkey_label_for_ui,
    _start_button_cooldown,
    _tr,
)
from voxgo.ui.widgets import AudioTestPanel, TranslationTestRunner


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
        url = (
            self._update.release_url
            or self._update.download_lite_url
            or self._update.download_full_url
            or getattr(self._update, "download_full_cuda_url", "")
        )
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
