import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audio_capture import LATENCY_MODE_FAST
from config.loader import (
    default_app_config,
    load_config,
    save_user_settings,
    serialize_user_settings,
    sync_language_flow,
    sync_whisper_vad_limit,
)
from speech_recognition import MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT


class ConfigLoaderTest(unittest.TestCase):
    def test_load_config_migrates_and_applies_user_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            config_path = runtime_dir / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "audio": {"latency_mode": LATENCY_MODE_FAST},
                        "translation": {
                            "source_lang": "zh",
                            "target_lang": "zh",
                            "timeout_seconds": 2,
                            "max_concurrent_requests": 9,
                        },
                        "whisper": {
                            "model_download_endpoint": "https://hf-mirror.com",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (runtime_dir / "user_settings.json").write_text(
                json.dumps(
                    {
                        "translation": {"target_lang": "en"},
                        "whisper": {"device": "gpu"},
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(str(config_path), runtime_dir)

            self.assertEqual(config.audio.latency_mode, LATENCY_MODE_FAST)
            self.assertEqual(config.translation.source_lang, "zh")
            self.assertEqual(config.translation.target_lang, "en")
            self.assertEqual(config.whisper.language, "zh")
            self.assertEqual(config.translation.timeout_seconds, 12)
            self.assertEqual(config.translation.max_concurrent_requests, 4)
            self.assertEqual(config.whisper.model_download_source, MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT)

    def test_save_user_settings_normalizes_serialized_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            config = default_app_config()
            config.app.setup_completed = True
            config.whisper.device = "gpu"
            config.translation.provider = "openai"
            config.update.ignored_version = "v0.3.0"

            save_user_settings(config, runtime_dir)

            data = json.loads((runtime_dir / "user_settings.json").read_text(encoding="utf-8"))
            self.assertTrue(data["app"]["setup_completed"])
            self.assertEqual(data["whisper"]["device"], "cuda")
            self.assertEqual(data["translation"]["provider"], "openai_compatible")
            self.assertEqual(data["update"]["ignored_version"], "0.3.0")

    def test_language_flow_and_vad_limit_sync(self):
        config = default_app_config()
        config.translation.source_lang = "en"
        config.translation.target_lang = "en"
        config.audio.max_speech_seconds = 4.5

        source, target = sync_language_flow(config)
        sync_whisper_vad_limit(config)

        self.assertEqual((source, target), ("en", "zh"))
        self.assertEqual(config.whisper.language, "en")
        self.assertEqual(config.whisper.vad_parameters["max_speech_duration_s"], 4.5)

    def test_serialize_user_settings_preserves_expected_sections(self):
        data = serialize_user_settings(default_app_config())

        self.assertEqual(
            set(data.keys()),
            {"app", "audio", "overlay", "hotkeys", "whisper", "translation", "debug", "update"},
        )


if __name__ == "__main__":
    unittest.main()
