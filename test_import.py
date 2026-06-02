import sys
print("测试导入...")

try:
    import torch
    print(f"✓ torch {torch.__version__}")
except Exception as e:
    print(f"✗ torch: {e}")

try:
    import faster_whisper
    print("✓ faster-whisper")
except Exception as e:
    print(f"✗ faster-whisper: {e}")

try:
    import PyQt5
    print("✓ PyQt5")
except Exception as e:
    print(f"✗ PyQt5: {e}")

try:
    import sounddevice
    print("✓ sounddevice")
except Exception as e:
    print(f"✗ sounddevice: {e}")

try:
    import webrtcvad
    print("✓ webrtcvad")
except Exception as e:
    print(f"✗ webrtcvad: {e}")

try:
    import keyboard
    print("✓ keyboard")
except Exception as e:
    print(f"✗ keyboard: {e}")

try:
    import pyaudio
    print("✓ pyaudio")
except Exception as e:
    print(f"✗ pyaudio: {e}")

try:
    import aiohttp
    print("✓ aiohttp")
except Exception as e:
    print(f"✗ aiohttp: {e}")

try:
    import fastapi
    print(f"✓ fastapi {fastapi.__version__}")
except Exception as e:
    print(f"✗ fastapi: {e}")

try:
    import websockets
    print(f"✓ websockets {websockets.__version__}")
except Exception as e:
    print(f"✗ websockets: {e}")