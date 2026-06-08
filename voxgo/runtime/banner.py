import sys

from voxgo.app_info import APP_NAME, APP_VERSION


def hotkey_label(value: str) -> str:
    return str(value or "").strip() or "未设置"


def print_startup_banner(config, mobile_url: str):
    if sys.stdout is None:
        return
    hotkeys = config.hotkeys
    title = f"{APP_NAME} v{APP_VERSION}"
    print(
        """
╔══════════════════════════════════════════════╗
║{title:^46}║
╠══════════════════════════════════════════════╣
║  热键:                                       ║
║    {toggle_overlay:<14} 切换浮窗显示/隐藏       ║
║    {clear_history:<14} 清空翻译历史            ║
║    {toggle_translation:<14} 暂停/恢复翻译       ║
║    {toggle_lock:<14} 锁定/解锁浮窗            ║
║    {toggle_compact:<14} 切换紧凑模式          ║
╠══════════════════════════════════════════════╣
║  手机端: {url}  ║
╠══════════════════════════════════════════════╣
║  按 Ctrl+C 停止                               ║
╚══════════════════════════════════════════════╝
""".format(
            title=title,
            toggle_overlay=hotkey_label(hotkeys.toggle_overlay),
            clear_history=hotkey_label(hotkeys.clear_history),
            toggle_translation=hotkey_label(hotkeys.toggle_translation),
            toggle_lock=hotkey_label(hotkeys.toggle_lock),
            toggle_compact=hotkey_label(hotkeys.toggle_compact),
            url=mobile_url,
        )
    )
