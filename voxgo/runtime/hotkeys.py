import keyboard
from loguru import logger


class HotkeyManager:
    def __init__(self, notify_error):
        self._notify_error = notify_error
        self._handles = []

    def setup(self, hotkeys, callbacks):
        self.remove_all()
        registrations = (
            ("显示/隐藏", hotkeys.toggle_overlay, callbacks["toggle_overlay"]),
            ("清空历史", hotkeys.clear_history, callbacks["clear_history"]),
            ("暂停/恢复", hotkeys.toggle_translation, callbacks["toggle_translation"]),
            ("锁定/解锁", hotkeys.toggle_lock, callbacks["toggle_lock"]),
            ("紧凑模式", hotkeys.toggle_compact, callbacks["toggle_compact"]),
        )
        active_hotkeys = []
        try:
            for label, value, callback in registrations:
                value = str(value or "").strip()
                if not value:
                    continue
                self._handles.append(keyboard.add_hotkey(value, callback))
                active_hotkeys.append(f"{label}={value}")
            logger.info("热键已注册: {}", ", ".join(active_hotkeys) or "无")
        except Exception as e:
            logger.exception(f"热键注册失败: {e}")
            self._notify_error("热键注册失败", str(e), "错误")

    def remove_all(self):
        for handle in self._handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                pass
        self._handles = []
