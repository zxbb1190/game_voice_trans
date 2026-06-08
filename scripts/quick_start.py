"""
快速启动 - VoxGo 演示
"""

import sys
import time
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout
from PyQt5.QtCore import Qt, QTimer, QPoint
from PyQt5.QtGui import QFont, QColor, QPainter, QBrush, QPen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_info import APP_NAME

class GameTranslatorDemo(QWidget):
    def __init__(self):
        super().__init__()
        self.setup_ui()
        self.setup_timer()
        
    def setup_ui(self):
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(100, 100, 500, 200)
        
        # 主布局
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 标题
        title = QLabel(f"{APP_NAME} (演示模式)")
        title.setStyleSheet("""
            QLabel {
                color: #00FF00;
                font-size: 18px;
                font-weight: bold;
                padding: 5px;
                background: rgba(0,0,0,0.5);
                border-radius: 5px;
            }
        """)
        layout.addWidget(title)
        
        # 翻译内容
        self.content = QLabel()
        self.content.setStyleSheet("""
            QLabel {
                color: #00FF00;
                font-size: 16px;
                font-family: Microsoft YaHei;
                background: rgba(0,0,0,0.7);
                padding: 10px;
                border: 2px solid #00FF00;
                border-radius: 10px;
                min-height: 100px;
            }
        """)
        self.content.setWordWrap(True)
        layout.addWidget(self.content)
        
        # 状态栏
        self.status = QLabel("状态: 🟢 运行中 | 热键: Ctrl+Shift+T 隐藏/显示")
        self.status.setStyleSheet("""
            QLabel {
                color: #888888;
                font-size: 12px;
                padding: 3px;
            }
        """)
        layout.addWidget(self.status)
        
        self.setLayout(layout)
        
        # 演示文本
        self.demo_texts = [
            ("Hello team, push A site", "队友，冲A点"),
            ("He's one shot", "他大残了"),
            ("Need backup", "需要支援"),
            ("Rotate to B", "转B点"),
            ("Eco round", "经济局"),
            ("Nice shot!", "好枪！"),
            ("Watch flank", "小心绕后"),
            ("Plant the bomb", "下包")
        ]
        self.current_index = 0
        
    def setup_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_translation)
        self.timer.start(3000)  # 每3秒更新一次
        
        # 初始显示
        self.update_translation()
        
    def update_translation(self):
        if self.current_index >= len(self.demo_texts):
            self.current_index = 0
            
        original, translated = self.demo_texts[self.current_index]
        self.content.setText(f"🎤 {original}\n\n🇨🇳 {translated}")
        self.current_index += 1
        
        # 更新状态
        self.status.setText(f"状态: 🟢 运行中 | 已处理 {self.current_index}/{len(self.demo_texts)} 条翻译")
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 半透明背景
        painter.setBrush(QBrush(QColor(0, 0, 0, 180)))
        painter.setPen(QPen(QColor(0, 255, 0, 100), 2))
        painter.drawRoundedRect(self.rect(), 10, 10)
        
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            
    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self.drag_pos)
            event.accept()

def main():
    print(f"{APP_NAME} - 演示模式")
    print("=" * 50)
    print("功能演示:")
    print("  • 透明置顶浮窗")
    print("  • 游戏术语翻译")
    print("  • 自动更新演示")
    print("  • 可拖拽移动")
    print("=" * 50)
    print("按 Ctrl+C 停止演示")
    print()
    
    app = QApplication(sys.argv)
    window = GameTranslatorDemo()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
