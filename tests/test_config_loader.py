import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voxgo.audio.capture import LATENCY_MODE_BALANCED, LATENCY_MODE_FAST
from voxgo.config.loader import (
    apply_language_runtime_policy,
    default_app_config,
    load_config,
    migrate_runtime_defaults,
    save_user_settings,
    serialize_user_settings,
    sync_language_flow,
    sync_whisper_vad_limit,
)
from voxgo.asr.whisper_engine import MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT


class ConfigLoaderTest(unittest.TestCase):
    def test_default_recognition_device_is_auto_gpu_first(self):
        config = default_app_config()

        self.assertEqual(config.whisper.device, "auto")

    def test_legacy_cpu_user_setting_migrates_to_auto_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            (runtime_dir / "user_settings.json").write_text(
                json.dumps({"whisper": {"device": "cpu"}}),
                encoding="utf-8",
            )

            config = load_config(runtime_dir=runtime_dir)

            self.assertEqual(config.whisper.device, "auto")

    def test_current_cpu_user_setting_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            (runtime_dir / "user_settings.json").write_text(
                json.dumps(
                    {
                        "app": {"recognition_device_policy_version": 2},
                        "whisper": {"device": "cpu"},
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(runtime_dir=runtime_dir)

            self.assertEqual(config.whisper.device, "cpu")

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
            config.whisper.fast_model_size = "base"
            config.whisper.pure_english_environment = True
            config.translation.provider = "openai"
            config.update.ignored_version = "v0.3.0"

            save_user_settings(config, runtime_dir)

            data = json.loads((runtime_dir / "user_settings.json").read_text(encoding="utf-8"))
            self.assertTrue(data["app"]["setup_completed"])
            self.assertEqual(data["whisper"]["device"], "cuda")
            self.assertEqual(data["whisper"]["model_size"], "small")
            self.assertEqual(data["whisper"]["fast_model_size"], "base")
            self.assertTrue(data["whisper"]["pure_english_environment"])
            self.assertTrue(data["whisper"]["enable_english_model"])
            self.assertEqual(data["translation"]["provider"], "openai_compatible")
            self.assertEqual(data["update"]["ignored_version"], "0.3.0")

    def test_fast_model_size_only_activates_for_fast_mode(self):
        config = default_app_config()
        config.whisper.model_size = "small"
        config.whisper.fast_model_size = "base"
        config.whisper.enable_english_model = False
        config.audio.latency_mode = LATENCY_MODE_FAST
        load_like_fast = copy.deepcopy(config)

        migrate_runtime_defaults(load_like_fast, preserve_existing_audio_tuning=False)

        self.assertEqual(load_like_fast.whisper.model_size, "small")
        self.assertEqual(load_like_fast.whisper.active_model_size, "base")

        balanced = copy.deepcopy(config)
        balanced.audio.latency_mode = LATENCY_MODE_BALANCED
        migrate_runtime_defaults(balanced, preserve_existing_audio_tuning=False)

        self.assertEqual(balanced.whisper.model_size, "small")
        self.assertEqual(balanced.whisper.active_model_size, "")

    def test_english_to_chinese_uses_auto_language_without_specialized_model(self):
        config = default_app_config()
        config.audio.latency_mode = LATENCY_MODE_BALANCED
        config.translation.source_lang = "en"
        config.translation.target_lang = "zh"
        config.whisper.enable_english_model = True

        source, target = sync_language_flow(config)
        migrate_runtime_defaults(config, preserve_existing_audio_tuning=False)
        source, target = sync_language_flow(config)
        apply_language_runtime_policy(config)

        self.assertEqual((source, target), ("en", "zh"))
        self.assertEqual(config.whisper.language, "auto")
        self.assertFalse(config.whisper.pure_english_environment)
        self.assertFalse(config.whisper.enable_english_model)
        self.assertEqual(config.whisper.active_model_size, "")
        self.assertEqual(config.audio.speech_idle_timeout_ms, 400)
        self.assertEqual(config.audio.max_speech_seconds, 4.0)

    def test_pure_english_environment_uses_english_specialized_model(self):
        config = default_app_config()
        config.audio.latency_mode = LATENCY_MODE_BALANCED
        config.translation.source_lang = "en"
        config.translation.target_lang = "zh"
        config.whisper.pure_english_environment = True

        source, target = sync_language_flow(config)
        migrate_runtime_defaults(config, preserve_existing_audio_tuning=False)
        source, target = sync_language_flow(config)
        apply_language_runtime_policy(config)

        self.assertEqual((source, target), ("en", "zh"))
        self.assertEqual(config.whisper.language, "en")
        self.assertEqual(config.whisper.active_model_size, "small.en")
        self.assertEqual(config.audio.speech_idle_timeout_ms, 400)

    def test_fast_english_to_chinese_uses_fast_english_model(self):
        config = default_app_config()
        config.audio.latency_mode = LATENCY_MODE_FAST
        config.translation.source_lang = "en"
        config.translation.target_lang = "zh"
        config.whisper.pure_english_environment = True
        config.whisper.fast_english_model_size = "base.en"

        migrate_runtime_defaults(config, preserve_existing_audio_tuning=False)
        sync_language_flow(config)
        apply_language_runtime_policy(config)

        self.assertEqual(config.whisper.active_model_size, "base.en")
        self.assertEqual(config.audio.max_speech_seconds, 2.5)

    def test_chinese_to_english_keeps_multilingual_model(self):
        config = default_app_config()
        config.audio.latency_mode = LATENCY_MODE_FAST
        config.translation.source_lang = "zh"
        config.translation.target_lang = "en"
        config.whisper.fast_model_size = "base"

        migrate_runtime_defaults(config, preserve_existing_audio_tuning=False)
        sync_language_flow(config)
        apply_language_runtime_policy(config)

        self.assertEqual(config.whisper.language, "zh")
        self.assertEqual(config.whisper.active_model_size, "base")

    def test_language_flow_and_vad_limit_sync(self):
        config = default_app_config()
        config.translation.source_lang = "en"
        config.translation.target_lang = "en"
        config.audio.max_speech_seconds = 4.5

        source, target = sync_language_flow(config)
        sync_whisper_vad_limit(config)

        self.assertEqual((source, target), ("en", "zh"))
        self.assertEqual(config.whisper.language, "auto")
        self.assertEqual(config.whisper.vad_parameters["max_speech_duration_s"], 4.5)

    def test_pure_english_environment_forces_english_language(self):
        config = default_app_config()
        config.translation.source_lang = "en"
        config.translation.target_lang = "zh"
        config.whisper.pure_english_environment = True

        source, target = sync_language_flow(config)

        self.assertEqual((source, target), ("en", "zh"))
        self.assertEqual(config.whisper.language, "en")

    def test_serialize_user_settings_preserves_expected_sections(self):
        data = serialize_user_settings(default_app_config())

        self.assertEqual(
            set(data.keys()),
            {"app", "audio", "overlay", "hotkeys", "whisper", "translation", "debug", "update"},
        )


if __name__ == "__main__":
    unittest.main()
