import os
import sys
import time
import traceback
from pathlib import Path

from loguru import logger


class DiagnosticsReporter:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def setup_logging(self):
        logger.remove()
        log_target = sys.stderr if sys.stderr is not None else open(os.devnull, "w", encoding="utf-8")
        log_dir = self.runtime_dir()
        logger.add(
            log_target,
            level="INFO",
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        )
        logger.add(
            log_dir / "app.log",
            level="INFO",
            rotation="2 MB",
            retention=3,
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        )

    def runtime_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).parent
        return self.project_root

    def write_crash_report(self, title: str, exc: Exception, detail: str = ""):
        report_path = self.runtime_dir() / "crash_report.txt"
        content = [
            title,
            time.strftime("%Y-%m-%d %H:%M:%S"),
            detail,
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        ]
        try:
            report_path.write_text("\n\n".join(part for part in content if part), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def show_error_dialog(title: str, message: str):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
        except Exception:
            pass

