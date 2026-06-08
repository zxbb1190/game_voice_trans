import threading
import time

from loguru import logger

from voxgo.app_info import USER_AGENT
from voxgo.update.checker import (
    UpdateCheckResult,
    UpdateSettings,
    check_for_update,
    normalize_update_channel,
    should_check_for_update,
)


class UpdateRuntime:
    def __init__(self, app_version: str, get_config, migrate_defaults, save_settings, notify_user):
        self._app_version = app_version
        self._get_config = get_config
        self._migrate_defaults = migrate_defaults
        self._save_settings = save_settings
        self._notify_user = notify_user
        self._thread = None

    def snapshot_settings(self) -> UpdateSettings:
        config = self._get_config()
        self._migrate_defaults(config)
        return UpdateSettings(
            enabled=bool(getattr(config.update, "enabled", True)),
            channel=normalize_update_channel(getattr(config.update, "channel", "stable")),
            last_check_at=float(getattr(config.update, "last_check_at", 0) or 0),
            ignored_version=str(getattr(config.update, "ignored_version", "") or "")
            .strip()
            .lstrip("v"),
            manifest_url=str(getattr(config.update, "manifest_url", "") or "").strip(),
        )

    def request(self, manual: bool, overlay, is_stopping) -> bool:
        if is_stopping():
            return False
        settings = self.snapshot_settings()
        if not manual and not should_check_for_update(settings):
            logger.info("跳过自动更新检查：尚未到达检查间隔或自动检查已关闭")
            return False
        if self._thread and self._thread.is_alive():
            logger.info("更新检查正在进行，跳过重复请求")
            if manual and overlay:
                overlay.set_update_checking(True)
            return False
        if manual and overlay:
            overlay.set_update_checking(True)
        self._thread = threading.Thread(
            target=self._run,
            args=(settings, bool(manual), overlay, is_stopping),
            name="update-check",
            daemon=True,
        )
        self._thread.start()
        return True

    def _run(self, settings: UpdateSettings, manual: bool, overlay, is_stopping):
        try:
            result = check_for_update(
                self._app_version,
                settings,
                manual=manual,
                user_agent=USER_AGENT,
            )
        except Exception as exc:
            result = UpdateCheckResult("error", message=str(exc))

        config = self._get_config()
        config.update.last_check_at = float(getattr(result, "checked_at", time.time()) or time.time())
        self._save_settings()
        logger.info("更新检查完成: status={}, message={}", result.status, result.message)
        if overlay and not is_stopping():
            overlay.handle_update_check_result(result, manual)
        if manual and result.status == "error":
            self._notify_user("更新检查失败", result.message, "错误")

    def ignore_version(self, version: str):
        config = self._get_config()
        config.update.ignored_version = str(version or "").strip().lstrip("v")
        self._save_settings()
        logger.info("已忽略更新版本: v{}", config.update.ignored_version)
