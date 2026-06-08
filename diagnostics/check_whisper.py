"""
Diagnose faster-whisper model loading in isolation.
"""

from pathlib import Path

from faster_whisper import WhisperModel


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main():
    model_dir = PROJECT_ROOT / ".models"
    print("before whisper load", flush=True)
    model = WhisperModel(
        "tiny",
        device="cpu",
        compute_type="int8",
        download_root=str(model_dir),
    )
    print("after whisper load", flush=True)
    print(model, flush=True)


if __name__ == "__main__":
    main()
