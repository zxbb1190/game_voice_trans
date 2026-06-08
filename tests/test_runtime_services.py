import asyncio
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audio_capture import AudioConfig, SpeechSegment
from speech_recognition import TranscriptionResult, WhisperConfig
from voxgo.asr.pipeline import SpeechPipeline
from voxgo.config.schema import AppConfig
from voxgo.runtime.events import EventBus, TranscriptReady, TranslationReady
from voxgo.runtime.work_items import LatencyTrace, SpeechWorkItem
from voxgo.translation.base import TranslationResult
from voxgo.translation.runtime import TranslationRuntime


class FakeRecognizer:
    def transcribe_audio_bytes_with_language(self, audio_data, sample_rate):
        return TranscriptionResult("hello there", "en", 0.95)


class FakeTranslator:
    def __init__(self):
        self.closed = False
        self.config = None

    async def translate_result(self, text, detected_language=""):
        return TranslationResult(
            translated="你好",
            source_lang=detected_language or "en",
            target_lang="zh",
            provider="fake",
        )

    async def close(self):
        self.closed = True

    def clear_context(self):
        pass


class RuntimeServicesTest(unittest.TestCase):
    def test_speech_pipeline_publishes_transcript_ready(self):
        config = AppConfig(audio=AudioConfig(min_segment_seconds=0.0), whisper=WhisperConfig())
        bus = EventBus()
        seen = []
        stats = {
            "speech_detected": 0,
            "filtered_speech": 0,
            "dropped_speech": 0,
            "errors": 0,
        }
        latency_traces = {}
        next_ids = iter(["translation-1"])
        pipeline = SpeechPipeline(
            lambda: config,
            lambda: FakeRecognizer(),
            bus,
            stats,
            lambda: True,
            lambda: False,
            lambda: next(next_ids),
            latency_traces,
            lambda *args: None,
        )
        bus.subscribe(TranscriptReady, seen.append)

        segment = SpeechSegment(
            audio_data=b"\x01\x00" * 1600,
            sample_rate=16000,
            duration_seconds=0.1,
            voice_duration_seconds=0.1,
            block_count=1,
            voice_blocks=1,
            peak_rms_dbfs=-20.0,
            energy_threshold_dbfs=-40.0,
            noise_floor_dbfs=None,
            reason="test",
        )
        pipeline._process(
            SpeechWorkItem(
                segment=segment,
                trace=LatencyTrace("translation-1", 1.0, 1.0),
            )
        )

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].text, "hello there")
        self.assertEqual(seen[0].trace_id, "translation-1")
        self.assertIn("translation-1", latency_traces)

    def test_translation_runtime_publishes_translation_ready(self):
        bus = EventBus()
        seen = []
        stats = {"errors": 0}
        latency_traces = {"translation-1": LatencyTrace("translation-1", 1.0, 1.0)}
        runtime = TranslationRuntime(bus, stats, latency_traces, lambda: None)
        runtime.client = FakeTranslator()
        bus.subscribe(TranslationReady, seen.append)

        asyncio.run(
            runtime._translate_and_publish(
                "translation-1",
                "hello",
                "en",
                latency_traces["translation-1"],
            )
        )

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].translated, "你好")
        self.assertEqual(seen[0].source_lang, "en")
        self.assertGreater(latency_traces["translation-1"].translation_finished_at, 0)


if __name__ == "__main__":
    unittest.main()
