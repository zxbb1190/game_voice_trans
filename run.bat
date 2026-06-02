@echo off
chcp 65001 >nul
echo ============================================
echo   游戏语音实时翻译器 - 启动
echo ============================================
echo.

:: 检查依赖
if not exist "requirements.txt" (
    echo [错误] 请先运行 install.bat 安装依赖
    pause
    exit /b 1
)

:: 检查配置文件
if not exist "config.json" (
    echo [错误] config.json 不存在
    pause
    exit /b 1
)

:: 检查 API Key 配置
findstr /C:"YOUR_SILICONFLOW_API_KEY" config.json >nul
if %errorlevel% equ 0 (
    echo [警告] 请在 config.json 中填入你的硅基流动 API Key
    echo 获取地址: https://cloud.siliconflow.cn/
    echo.
    echo 是否继续？(Y/N)
    set /p choice=
    if /i not "%choice%"=="Y" exit /b 1
)

:: 启动程序
echo 正在启动游戏语音实时翻译器...
echo 按 Ctrl+C 停止程序
echo.

python main.py

echo.
echo ============================================
echo   程序已停止
echo ============================================
pause