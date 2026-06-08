"""
简化启动 - 测试核心功能
"""

import sys
import time
import threading
from pathlib import Path
from PyQt5.QtWidgets import QApplication

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_info import APP_NAME
from overlay import GameOverlay, OverlayConfig

def test_overlay():
    """测试浮窗"""
    app = QApplication([])
    overlay = GameOverlay(OverlayConfig())
    overlay.show()
    
    # 添加测试翻译
    overlay.add_translation("Hello team, push A site", "队友，冲A点")
    overlay.add_translation("He's one shot", "他大残了")
    overlay.add_translation("Need backup", "需要支援")
    
    print("✅ 浮窗已启动，按 Ctrl+C 停止")
    app.exec_()

if __name__ == "__main__":
    print(f"{APP_NAME} - 简化测试")
    print("正在启动浮窗...")
    
    # 在独立线程中启动 Qt
    qt_thread = threading.Thread(target=test_overlay, daemon=True)
    qt_thread.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n程序停止")
