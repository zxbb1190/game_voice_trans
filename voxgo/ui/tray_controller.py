from loguru import logger

from voxgo.app_info import APP_NAME, APP_VERSION
from voxgo.i18n import ui_text
from voxgo.config.schema import ui_language_of


class TrayController:
    def __init__(self, owner):
        self._owner = owner
        self.icon = None
        self.menu = None
        self.actions = {}

    def setup(self, tray_cls, menu_cls, qt_app, app_icon, overlay):
        if not qt_app or self.icon:
            return
        if not tray_cls.isSystemTrayAvailable():
            logger.warning("系统托盘不可用，跳过托盘入口")
            return

        self.icon = tray_cls(app_icon or qt_app.windowIcon(), qt_app)
        self.icon.setToolTip(f"{APP_NAME} v{APP_VERSION}")
        self.menu = menu_cls(qt_app.activeWindow())
        ui_language = ui_language_of(self._owner.config)

        self.actions = {
            "toggle_overlay": self.menu.addAction(ui_text(ui_language, "隐藏浮窗", "Hide Overlay")),
            "toggle_translation": self.menu.addAction(ui_text(ui_language, "暂停翻译", "Pause Translation")),
            "clear_history": self.menu.addAction(ui_text(ui_language, "清空字幕", "Clear Subtitles")),
            "compact_mode": self.menu.addAction(ui_text(ui_language, "启用紧凑浮窗", "Enable Compact Overlay")),
        }
        self.menu.addSeparator()
        self.actions["settings"] = self.menu.addAction(ui_text(ui_language, "设置", "Settings"))
        self.actions["fullscreen_help"] = self.menu.addAction(ui_text(ui_language, "全屏兼容说明", "Fullscreen Compatibility"))
        self.menu.addSeparator()
        self.actions["quit"] = self.menu.addAction(ui_text(ui_language, "退出", "Quit"))

        self.actions["toggle_overlay"].triggered.connect(self._owner._tray_toggle_overlay)
        self.actions["toggle_translation"].triggered.connect(self._owner._toggle_translation)
        self.actions["clear_history"].triggered.connect(self._owner._clear_history)
        self.actions["compact_mode"].triggered.connect(self._owner._tray_toggle_compact_mode)
        self.actions["settings"].triggered.connect(self._owner._tray_open_settings)
        self.actions["fullscreen_help"].triggered.connect(self._owner._tray_show_fullscreen_help)
        self.actions["quit"].triggered.connect(self._owner._request_shutdown)
        self.icon.activated.connect(self._owner._handle_tray_activated)
        self.icon.setContextMenu(self.menu)
        self.sync_state(overlay)
        self.icon.show()

    def sync_state(self, overlay):
        if not self.actions:
            return
        config = self._owner.config
        ui_language = ui_language_of(config)
        if "toggle_overlay" in self.actions and overlay:
            self.actions["toggle_overlay"].setText(ui_text(
                ui_language,
                "隐藏浮窗" if overlay.isVisible() else "显示浮窗",
                "Hide Overlay" if overlay.isVisible() else "Show Overlay",
            ))
        if "toggle_translation" in self.actions:
            paused = bool(getattr(self._owner, "_paused", False))
            self.actions["toggle_translation"].setText(ui_text(
                ui_language,
                "恢复翻译" if paused else "暂停翻译",
                "Resume Translation" if paused else "Pause Translation",
            ))
        if "compact_mode" in self.actions:
            compact = bool(getattr(config.overlay, "compact_mode", False))
            self.actions["compact_mode"].setText(ui_text(
                ui_language,
                "退出紧凑浮窗" if compact else "启用紧凑浮窗",
                "Exit Compact Overlay" if compact else "Enable Compact Overlay",
            ))
        static_labels = {
            "clear_history": ("清空字幕", "Clear Subtitles"),
            "settings": ("设置", "Settings"),
            "fullscreen_help": ("全屏兼容说明", "Fullscreen Compatibility"),
            "quit": ("退出", "Quit"),
        }
        for key, (zh, en) in static_labels.items():
            if key in self.actions:
                self.actions[key].setText(ui_text(ui_language, zh, en))
        if self.icon:
            paused = bool(getattr(self._owner, "_paused", False))
            state = ui_text(
                ui_language,
                "暂停" if paused else "运行中",
                "Paused" if paused else "Running",
            )
            self.icon.setToolTip(f"{APP_NAME} v{APP_VERSION} - {state}")

    def hide(self):
        if self.icon:
            self.icon.hide()
