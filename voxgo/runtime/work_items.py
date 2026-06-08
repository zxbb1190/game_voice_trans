from dataclasses import dataclass

from voxgo.audio.capture import SpeechSegment


@dataclass
class LatencyTrace:
    item_id: str
    speech_detected_at: float
    queued_at: float
    dequeued_at: float = 0.0
    transcription_started_at: float = 0.0
    transcription_finished_at: float = 0.0
    translation_started_at: float = 0.0
    translation_finished_at: float = 0.0
    overlay_updated_at: float = 0.0

    def summary_ms(self) -> dict:
        wait_ms = self._elapsed_ms(self.queued_at, self.dequeued_at)
        recognition_ms = self._elapsed_ms(self.transcription_started_at, self.transcription_finished_at)
        translation_ms = self._elapsed_ms(self.translation_started_at, self.translation_finished_at)
        overlay_ms = self._elapsed_ms(self.translation_finished_at, self.overlay_updated_at)
        total_ms = self._elapsed_ms(self.speech_detected_at, self.overlay_updated_at)
        return {
            "wait_ms": wait_ms,
            "recognition_ms": recognition_ms,
            "translation_ms": translation_ms,
            "overlay_ms": overlay_ms,
            "total_ms": total_ms,
        }

    @staticmethod
    def _elapsed_ms(start: float, end: float) -> int:
        if not start or not end:
            return 0
        return int(round(max(0.0, end - start) * 1000))


@dataclass
class SpeechWorkItem:
    segment: SpeechSegment
    trace: LatencyTrace
