"""Settings dialog for overlay, audio, translation, and update preferences."""

from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from voxgo.app_info import APP_NAME, APP_VERSION, APP_WEBSITE, GITHUB_URL
from voxgo.audio.capture import (
    AudioConfig,
    AUDIO_LATENCY_PRESETS,
    LATENCY_MODE_BALANCED,
    LATENCY_MODE_CUSTOM,
    normalize_latency_mode,
)
from voxgo.i18n import UI_LANGUAGE_OPTIONS, UI_LANGUAGE_ZH, is_english_ui, normalize_ui_language
from voxgo.translation import TRANSLATION_PROVIDERS, TranslationConfig, normalize_translation_provider
from voxgo.update.checker import UpdateCheckResult, UpdateInfo, UpdateSettings, normalize_update_channel
from voxgo.ui.config_models import (
    AudioDeviceConfig,
    DebugConfig,
    HotkeyConfig,
    OverlayConfig,
    RuntimeConfig,
    WhisperDeviceConfig,
    _audio_latency_mode_options,
    _build_feedback_report,
    _copy_audio_config,
    _copy_runtime_config,
    _model_download_source_options,
    _normalize_model_download_endpoint,
    _normalize_model_download_source,
    _normalize_whisper_device,
    _start_button_cooldown,
    _tr,
    _update_channel_options,
    _whisper_device_options,
)
from voxgo.ui.dialogs import FeedbackDialog, _build_help_text
from voxgo.ui.widgets import AudioTestPanel, ColorButton, HotkeyCaptureEdit, TranslationTestRunner


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

        self.pure_english_environment_check = QCheckBox(_tr(
            self._ui_language,
            "纯英文环境，使用英文专用模型加速",
            "Pure English environment, use English-only model for speed",
        ))
        self.pure_english_environment_check.setChecked(
            bool(getattr(self.whisper_config, "pure_english_environment", False))
        )
        self.pure_english_environment_check.setToolTip(_tr(
            self._ui_language,
            "默认关闭：使用多语言模型检测输入语言，中文声音会被跳过。\n只有音频几乎全是英文时才建议开启。",
            "Off by default: the multilingual model detects the input language, so Chinese audio can be skipped.\nEnable only when the audio is almost entirely English.",
        ))
        self.pure_english_environment_check.stateChanged.connect(self._preview)
        audio_form.addRow(_tr(self._ui_language, "英文快路径", "English Fast Path"), self.pure_english_environment_check)

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
        self.chunk_duration_spin.setValue(int(getattr(self.audio_config, "chunk_duration_ms", 200) or 200))
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
        self.silence_limit_blocks_spin.setValue(int(getattr(self.audio_config, "silence_limit_blocks", 3) or 3))
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
            int(getattr(self.audio_config, "speech_idle_timeout_ms", 550) or 550)
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
            ("chunk_duration_ms", "chunk_duration_spin", int, 200),
            ("speech_threshold_blocks", "speech_threshold_blocks_spin", int, 2),
            ("silence_limit_blocks", "silence_limit_blocks_spin", int, 3),
            ("max_speech_seconds", "max_speech_seconds_spin", lambda value: int(round(float(value))), 6),
            ("pre_roll_ms", "pre_roll_ms_spin", int, 450),
            ("speech_idle_timeout_ms", "speech_idle_timeout_ms_spin", int, 550),
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
        self.whisper_config.pure_english_environment = bool(self.pure_english_environment_check.isChecked())
        self.whisper_config.enable_english_model = self.whisper_config.pure_english_environment
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
