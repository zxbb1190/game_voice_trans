import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voxgo.asr.pipeline import SpeechPipeline
from voxgo.asr.whisper_engine import SpeechRecognizer, WhisperConfig
from voxgo.runtime.work_items import LatencyTrace


class FakeWhisperModelFactory:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.calls = []

    def __call__(self, *args, **kwargs):
        device = kwargs.get("device")
        compute_type = kwargs.get("compute_type")
        self.calls.append((device, compute_type))
        if (device, compute_type) in self.failures:
            raise RuntimeError(f"unsupported {device}/{compute_type}")
        return object()


class TranscribeRuntimeModel:
    def __init__(self, device, compute_type):
        self.device = device
        self.compute_type = compute_type

    def transcribe(self, *args, **kwargs):
        info = SimpleNamespace(language="en", language_probability=0.95)
        if self.device == "cuda":
            def failing_segments():
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                yield None

            return failing_segments(), info
        segment = SimpleNamespace(
            text="hello",
            avg_logprob=-0.1,
            no_speech_prob=0.05,
            compression_ratio=1.0,
        )
        return iter([segment]), info


class TranscribeRuntimeModelFactory:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        device = kwargs.get("device")
        compute_type = kwargs.get("compute_type")
        self.calls.append((device, compute_type))
        return TranscribeRuntimeModel(device, compute_type)


class WhisperRuntimeDeviceTest(unittest.TestCase):
    def test_cuda_auto_candidates_try_float32_before_cpu(self):
        recognizer = SpeechRecognizer(WhisperConfig(device="cuda", compute_type="auto"))

        self.assertEqual(
            recognizer._model_load_candidates(),
            [("cuda", "float16"), ("cuda", "float32"), ("cpu", "int8")],
        )

    def test_auto_device_candidates_try_cuda_float32_before_cpu_when_cuda_exists(self):
        recognizer = SpeechRecognizer(WhisperConfig(device="auto", compute_type="auto"))
        recognizer._is_cuda_runtime_available = lambda: True

        self.assertEqual(
            recognizer._model_load_candidates(),
            [("cuda", "float16"), ("cuda", "float32"), ("cpu", "int8")],
        )

    def test_runtime_cpu_fallback_does_not_overwrite_configured_cuda(self):
        fake_model = FakeWhisperModelFactory(
            failures={("cuda", "float16"), ("cuda", "float32")}
        )
        with tempfile.TemporaryDirectory() as tmp, patch(
            "voxgo.asr.whisper_engine.WhisperModel",
            fake_model,
        ):
            config = WhisperConfig(
                model_size="tiny",
                device="cuda",
                compute_type="auto",
                model_dir=tmp,
                local_files_only=True,
            )
            notices = []
            recognizer = SpeechRecognizer(config, device_fallback_callback=lambda reason, message: notices.append((reason, message)))

            recognizer.initialize()

        self.assertEqual(
            fake_model.calls,
            [("cuda", "float16"), ("cuda", "float32"), ("cpu", "int8")],
        )
        self.assertEqual(config.device, "cuda")
        self.assertEqual(config.compute_type, "auto")
        self.assertEqual(recognizer.runtime_device, "cpu")
        self.assertEqual(recognizer.runtime_compute_type, "int8")
        self.assertEqual(recognizer.get_model_info()["configured_device"], "cuda")
        self.assertEqual(recognizer.get_model_info()["runtime_device"], "cpu")
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0][0], "cuda_model_load_failed")
        self.assertIn("CPU", notices[0][1])

    def test_runtime_cuda_float32_success_keeps_configured_auto_compute_type(self):
        fake_model = FakeWhisperModelFactory(failures={("cuda", "float16")})
        with tempfile.TemporaryDirectory() as tmp, patch(
            "voxgo.asr.whisper_engine.WhisperModel",
            fake_model,
        ):
            config = WhisperConfig(
                model_size="tiny",
                device="cuda",
                compute_type="auto",
                model_dir=tmp,
                local_files_only=True,
            )
            recognizer = SpeechRecognizer(config)

            recognizer.initialize()

        self.assertEqual(fake_model.calls, [("cuda", "float16"), ("cuda", "float32")])
        self.assertEqual(config.device, "cuda")
        self.assertEqual(config.compute_type, "auto")
        self.assertEqual(recognizer.runtime_device, "cuda")
        self.assertEqual(recognizer.runtime_compute_type, "float32")

    def test_cuda_transcription_runtime_error_falls_back_to_cpu_for_session(self):
        fake_model = TranscribeRuntimeModelFactory()
        with tempfile.TemporaryDirectory() as tmp, patch(
            "voxgo.asr.whisper_engine.WhisperModel",
            fake_model,
        ):
            config = WhisperConfig(
                model_size="tiny",
                device="cuda",
                compute_type="float32",
                model_dir=tmp,
                local_files_only=True,
            )
            notices = []
            recognizer = SpeechRecognizer(config, device_fallback_callback=lambda reason, message: notices.append((reason, message)))

            result = recognizer.transcribe_audio_bytes_with_language(b"\x01\x00" * 1600, sample_rate=16000)

        self.assertEqual(fake_model.calls, [("cuda", "float32"), ("cpu", "int8")])
        self.assertEqual(result.text, "hello")
        self.assertEqual(config.device, "cuda")
        self.assertEqual(config.compute_type, "float32")
        self.assertEqual(recognizer.runtime_device, "cpu")
        self.assertEqual(recognizer.runtime_compute_type, "int8")
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0][0], "cuda_runtime_disabled")
        self.assertIn("CPU", notices[0][1])

    def test_latency_trace_prefers_recognizer_runtime_metadata(self):
        class FakeRecognizer:
            config = WhisperConfig(device="cuda", compute_type="auto")
            runtime_device = "cpu"
            runtime_compute_type = "int8"

            def _effective_model_size(self):
                return "tiny"

            def _resolved_cpu_threads(self):
                return 4

        trace = LatencyTrace("translation-1", 1.0, 1.0)

        SpeechPipeline._refresh_trace_recognizer_metadata(trace, FakeRecognizer())

        self.assertEqual(trace.whisper_model_size, "tiny")
        self.assertEqual(trace.whisper_device, "cpu")
        self.assertEqual(trace.whisper_compute_type, "int8")
        self.assertEqual(trace.whisper_cpu_threads, 4)


if __name__ == "__main__":
    unittest.main()
