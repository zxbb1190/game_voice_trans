import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voxgo.asr.pipeline import CandidatePolicy, RecognitionModePolicy
from voxgo.audio.capture import LATENCY_MODE_ACCURATE, LATENCY_MODE_BALANCED, LATENCY_MODE_FAST, AudioConfig, SpeechSegment
from voxgo.config.schema import AppConfig
from voxgo.translation.base import TranslationConfig


def _low_margin_short_segment():
    return SpeechSegment(
        audio_data=b"\x01\x00" * 3200,
        sample_rate=16000,
        duration_seconds=0.2,
        voice_duration_seconds=0.15,
        block_count=1,
        voice_blocks=1,
        peak_rms_dbfs=-39.5,
        energy_threshold_dbfs=-40.0,
        noise_floor_dbfs=-48.0,
        reason="test",
    )


class LatencyModePolicyTest(unittest.TestCase):
    def test_latency_modes_change_timing_not_candidate_acceptance(self):
        policy = CandidatePolicy()
        segment = _low_margin_short_segment()

        for mode in ("fast", "balanced", "accurate"):
            with self.subTest(mode=mode):
                audio = AudioConfig(
                    latency_mode=mode,
                    min_segment_seconds=0.45,
                    min_segment_peak_margin_db=2.0,
                )
                decision = policy.classify(segment, audio)
                mode_policy = RecognitionModePolicy.from_audio_config(audio)

                self.assertTrue(decision.accepted)
                self.assertTrue(decision.low_confidence)
                self.assertTrue(decision.short_segment)
                self.assertGreaterEqual(mode_policy.pending_timeout_seconds, 0.3)
                self.assertLessEqual(mode_policy.pending_timeout_seconds, 1.0)

    def test_mode_policy_has_larger_queues_than_legacy_two_item_queue(self):
        for mode in ("fast", "balanced", "accurate"):
            with self.subTest(mode=mode):
                mode_policy = RecognitionModePolicy.from_audio_config(AudioConfig(latency_mode=mode))
                self.assertGreater(mode_policy.queue_size, 2)

    def test_latency_modes_have_separate_pending_waits(self):
        expectations = {
            LATENCY_MODE_FAST: 0.30,
            LATENCY_MODE_BALANCED: 0.55,
            LATENCY_MODE_ACCURATE: 1.00,
        }

        for mode, expected in expectations.items():
            with self.subTest(mode=mode):
                policy = RecognitionModePolicy.from_audio_config(AudioConfig(latency_mode=mode))
                self.assertAlmostEqual(policy.pending_timeout_seconds, expected)

    def test_english_direction_keeps_default_wait_without_pure_english_mode(self):
        from voxgo.asr.pipeline import SpeechPipeline
        from voxgo.asr.whisper_engine import WhisperConfig
        from voxgo.config.schema import DebugConfig
        from voxgo.runtime.events import EventBus

        config = AppConfig(
            audio=AudioConfig(latency_mode=LATENCY_MODE_FAST),
            whisper=WhisperConfig(language="auto"),
            translation=TranslationConfig(source_lang="en", target_lang="zh"),
            debug=DebugConfig(),
        )
        pipeline = SpeechPipeline(
            lambda: config,
            lambda: None,
            EventBus(),
            {"speech_detected": 0, "filtered_speech": 0, "dropped_speech": 0, "errors": 0},
            lambda: True,
            lambda: False,
            lambda: "translation-1",
            {},
            lambda *args: None,
        )

        mode_policy = pipeline._mode_policy(config)
        decision = CandidatePolicy().classify(_low_margin_short_segment(), config.audio)

        self.assertAlmostEqual(mode_policy.pending_timeout_seconds, 0.30)
        self.assertTrue(decision.accepted)
        self.assertTrue(decision.short_segment)

    def test_pure_english_environment_shortens_pending_wait_without_changing_acceptance(self):
        from voxgo.asr.pipeline import SpeechPipeline
        from voxgo.asr.whisper_engine import WhisperConfig
        from voxgo.config.schema import DebugConfig
        from voxgo.runtime.events import EventBus

        config = AppConfig(
            audio=AudioConfig(latency_mode=LATENCY_MODE_FAST),
            whisper=WhisperConfig(language="en", pure_english_environment=True),
            translation=TranslationConfig(source_lang="en", target_lang="zh"),
            debug=DebugConfig(),
        )
        pipeline = SpeechPipeline(
            lambda: config,
            lambda: None,
            EventBus(),
            {"speech_detected": 0, "filtered_speech": 0, "dropped_speech": 0, "errors": 0},
            lambda: True,
            lambda: False,
            lambda: "translation-1",
            {},
            lambda *args: None,
        )

        mode_policy = pipeline._mode_policy(config)
        decision = CandidatePolicy().classify(_low_margin_short_segment(), config.audio)

        self.assertAlmostEqual(mode_policy.pending_timeout_seconds, 0.25)
        self.assertTrue(decision.accepted)
        self.assertTrue(decision.short_segment)


if __name__ == "__main__":
    unittest.main()
