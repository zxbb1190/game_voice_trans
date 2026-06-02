"""
Run startup components in isolated subprocesses to find native crashes.
"""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parent
PYTHON = ROOT / ".venv-win" / "Scripts" / "python.exe"


CASES = {
    "whisper_only": """
from faster_whisper import WhisperModel
print("before", flush=True)
WhisperModel("tiny", device="cpu", compute_type="int8", download_root=".models")
print("after", flush=True)
""",
    "pyqt_then_whisper": """
from PyQt5.QtWidgets import QApplication
from faster_whisper import WhisperModel
print("before", flush=True)
WhisperModel("tiny", device="cpu", compute_type="int8", download_root=".models")
print("after", flush=True)
""",
    "keyboard_then_whisper": """
import keyboard
from faster_whisper import WhisperModel
print("before", flush=True)
WhisperModel("tiny", device="cpu", compute_type="int8", download_root=".models")
print("after", flush=True)
""",
    "pyaudio_then_whisper": """
import pyaudio
from faster_whisper import WhisperModel
print("before", flush=True)
WhisperModel("tiny", device="cpu", compute_type="int8", download_root=".models")
print("after", flush=True)
""",
    "audio_capture_then_whisper": """
from audio_capture import SystemAudioCapture, AudioConfig
from speech_recognition import SpeechRecognizer, WhisperConfig
print("before", flush=True)
capture = SystemAudioCapture(AudioConfig(sample_rate=44100, chunk_duration_ms=300))
recognizer = SpeechRecognizer(WhisperConfig(model_size="tiny", device="cpu", compute_type="int8"))
recognizer.initialize()
capture.stop()
print("after", flush=True)
""",
    "project_imports_then_whisper": """
import main
from speech_recognition import SpeechRecognizer, WhisperConfig
print("before", flush=True)
recognizer = SpeechRecognizer(WhisperConfig(model_size="tiny", device="cpu", compute_type="int8"))
recognizer.initialize()
print("after", flush=True)
""",
}


def run_case(name: str, code: str) -> int:
    print(f"=== {name} ===", flush=True)
    result = subprocess.run(
        [str(PYTHON), "-X", "utf8", "-X", "faulthandler", "-c", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    print(result.stdout, end="")
    print(result.stderr, end="")
    print(f"exit={result.returncode}", flush=True)
    return result.returncode


def main():
    failed = False
    for name, code in CASES.items():
        if run_case(name, code) != 0:
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
