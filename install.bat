@echo off
chcp 65001 >nul
echo ============================================
echo   游戏语音实时翻译器 - 环境安装
echo ============================================
echo.

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] 升级 pip...
python -m pip install --upgrade pip -q

echo [2/4] 安装 PyTorch (CPU 版本，如需 GPU 加速请手动安装 CUDA 版)...
python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu -q

echo [3/4] 安装依赖包...
python -m pip install -r requirements.txt -q

echo [4/4] 安装 VB-Cable 虚拟音频设备 (可选，用于捕获系统音频)...
echo.
echo 请手动下载安装 VB-Cable:
echo https://vb-audio.com/Cable/
echo.
echo ============================================
echo   安装完成！
echo ============================================
echo.
echo 使用前请:
echo   1. 编辑 config.json，填入你的硅基流动 API Key
echo   2. 将系统默认音频输出切换到 VB-Cable
echo   3. 运行 run.bat 启动翻译器
echo.
pause