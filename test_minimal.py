"""
最小化测试 - 验证核心功能
"""

import sys
import time
import threading
from PyQt5.QtWidgets import QApplication, QLabel, QWidget
from PyQt5.QtCore import Qt, QTimer

class SimpleOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("游戏翻译测试")
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(100, 100, 400, 150)
        
        self.label = QLabel("🎮 游戏语音翻译器\n\n测试翻译:\n- Hello team → 队友\n- He's one shot → 他大残了", self)
        self.label.setStyleSheet("""
            QLabel {
                color: #00FF00;
                font-size: 16px;
                font-family: Microsoft YaHei;
                background: rgba(0,0,0,0.7);
                padding: 10px;
                border: 2px solid #00FF00;
                border-radius: 10px;
            }
        """)
        self.label.setGeometry(0, 0, 400, 150)
        
        print("✅ 浮窗已创建")

def run_qt():
    app = QApplication([])
    overlay = SimpleOverlay()
    overlay.show()
    print("🎮 浮窗已显示")
    app.exec_()

if __name__ == "__main__":
    print("启动测试...")
    qt_thread = threading.Thread(target=run_qt, daemon=True)
    qt_thread.start()
    
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n测试结束")