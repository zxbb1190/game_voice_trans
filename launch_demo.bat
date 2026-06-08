@echo off
chcp 65001 >nul
echo ============================================
echo   VoxGo - 演示模式
echo ============================================
echo.
echo 正在启动演示程序...
echo 按 Ctrl+C 停止
echo.

python scripts\quick_start.py

echo.
echo ============================================
echo   演示已结束
echo ============================================
pause
