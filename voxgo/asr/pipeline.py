import os
import queue
import re
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

from voxgo.audio.capture import (
    LATENCY_MODE_ACCURATE,
    LATENCY_MODE_BALANCED,
    LATENCY_MODE_FAST,
    SpeechSegment,
    normalize_latency_mode,
)
from voxgo.asr.whisper_engine import normalize_transcript_for_repeat, should_drop_transcription_result
from voxgo.runtime.events import TranscriptReady
from voxgo.runtime.work_items import LatencyTrace, SpeechWorkItem


SHORT_SEGMENT_PENDING_SECONDS = 0.8
LOW_CONFIDENCE_PEAK_MARGIN_DB = 1.0
SUSPECT_TRANSCRIPT_TTL_SECONDS = 12.0
SUSPECT_SHORT_TRANSCRIPTS = {
    "you",
    "thankyou",
    "thanks",
    "yeah",
    "okay",
    "ok",
    "music",
}
ALLOW_WEAK_SHORT_TRANSCRIPTS = {
    "go",
    "no",
    "yes",
    "run",
    "push",
    "mid",
    "gg",
    "nt",
    "wp",
}


@dataclass(frozen=True)
class CandidateDecision:
    accepted: bool
    labels: tuple
    reason: str = ""
    low_confidence: bool = False
    short_segment: bool = False

    @property
    def should_dump_low_confidence(self) -> bool:
        return self.low_confidence or self.short_segment


@dataclass(frozen=True)
class RecognitionModePolicy:
    mode: str
    queue_size: int
    pending_timeout_seconds: float
    allow_fast_output: bool
    busy_weak_delay_seconds: float = 0.0
    busy_weak_stale_seconds: float = 0.0

    @classmethod
    def from_audio_config(cls, audio_config) -> "RecognitionModePolicy":
        mode = normalize_latency_mode(getattr(audio_config, "latency_mode", LATENCY_MODE_BALANCED))
        if mode == LATENCY_MODE_FAST:
            return cls(
                mode=mode,
                queue_size=4,
                pending_timeout_seconds=0.30,
                allow_fast_output=True,
                busy_weak_delay_seconds=0.50,
                busy_weak_stale_seconds=1.60,
            )
        if mode == LATENCY_MODE_ACCURATE:
            return cls(mode=mode, queue_size=8, pending_timeout_seconds=1.00, allow_fast_output=False)
        return cls(
            mode=LATENCY_MODE_BALANCED,
            queue_size=6,
            pending_timeout_seconds=0.45,
            allow_fast_output=False,
            busy_weak_delay_seconds=0.70,
            busy_weak_stale_seconds=2.20,
        )


class CandidatePolicy:
    """Classify captured segments without dropping short or low-margin speech."""

    def classify(self, segment: SpeechSegment, audio_config=None) -> CandidateDecision:
        fatal_reason = self.fatal_drop_reason(segment)
        if fatal_reason:
            return CandidateDecision(False, ("invalid",), fatal_reason)

        labels = ["candidate"]
        low_confidence = False
        short_segment = False

        if not self._has_capture_metadata(segment):
            return CandidateDecision(True, tuple(labels), "legacy/no_capture_metadata")

        voice_seconds = self._voice_duration(segment)
        if voice_seconds < SHORT_SEGMENT_PENDING_SECONDS:
            labels.append("short_segment")
            short_segment = True

        peak_margin = self._peak_margin(segment)
        if peak_margin < LOW_CONFIDENCE_PEAK_MARGIN_DB:
            labels.append("low_confidence")
            low_confidence = True

        if low_confidence:
            reason = f"peak_margin={peak_margin:.1f}dB"
        elif short_segment:
            reason = f"voice_duration={voice_seconds:.2f}s"
        else:
            reason = "accepted"
        return CandidateDecision(True, tuple(labels), reason, low_confidence, short_segment)

    @staticmethod
    def fatal_drop_reason(segment: SpeechSegment) -> str:
        if not segment:
            return "empty_segment"
        audio_data = getattr(segment, "audio_data", b"") or b""
        if not audio_data:
            return "empty_audio"
        if len(audio_data) < 2 or len(audio_data) % 2:
            return f"invalid_pcm_bytes={len(audio_data)}"
        sample_rate = int(getattr(segment, "sample_rate", 0) or 0)
        if sample_rate <= 0:
            return "invalid_sample_rate"
        duration = float(getattr(segment, "duration_seconds", 0.0) or 0.0)
        if duration <= 0 and len(audio_data) // 2 <= 0:
            return "no_effective_samples"
        return ""

    @staticmethod
    def _has_capture_metadata(segment: SpeechSegment) -> bool:
        return bool(getattr(segment, "block_count", 0) or getattr(segment, "voice_blocks", 0))

    @staticmethod
    def _voice_duration(segment: SpeechSegment) -> float:
        voice_seconds = float(getattr(segment, "voice_duration_seconds", 0.0) or 0.0)
        if voice_seconds <= 0:
            voice_seconds = float(getattr(segment, "duration_seconds", 0.0) or 0.0)
        return max(0.0, voice_seconds)

    @staticmethod
    def _peak_margin(segment: SpeechSegment) -> float:
        return float(getattr(segment, "peak_rms_dbfs", -120.0) or -120.0) - float(
            getattr(segment, "energy_threshold_dbfs", -120.0) or -120.0
        )


class PendingSegmentBuffer:
    def __init__(self, timeout_seconds: float = 0.8):
        self.timeout_seconds = timeout_seconds
        self._pending: Optional[SpeechWorkItem] = None
        self._pending_at = 0.0

    @property
    def pending(self) -> Optional[SpeechWorkItem]:
        return self._pending

    def has_pending(self) -> bool:
        return self._pending is not None

    def clear(self) -> Optional[SpeechWorkItem]:
        item = self._pending
        self._pending = None
        self._pending_at = 0.0
        return item

    def add_or_merge(self, work_item: SpeechWorkItem, now: float = None) -> tuple:
        now = now or time.time()
        if self._pending is None:
            self._pending = work_item
            self._pending_at = now
            return "pending", work_item

        merged = merge_speech_work_items(self._pending, work_item, "pending_merge")
        self._pending = None
        self._pending_at = 0.0
        return "merged", merged

    def pop_expired(self, now: float = None) -> Optional[SpeechWorkItem]:
        if self._pending is None:
            return None
        now = now or time.time()
        if now - self._pending_at < self.timeout_seconds:
            return None
        return self.clear()


class BusyWeakCandidateBuffer(PendingSegmentBuffer):
    def add_or_merge(self, work_item: SpeechWorkItem, now: float = None) -> tuple:
        now = now or time.time()
        if self._pending is None:
            self._pending = work_item
            self._pending_at = now
            return "pending", work_item

        merged = merge_speech_work_items(self._pending, work_item, "queue_busy_merge")
        self._pending = merged
        return "merged", merged

    def age_seconds(self, now: float = None) -> float:
        if self._pending is None:
            return 0.0
        now = now or time.time()
        return max(0.0, now - self._pending_at)


class WeakCandidateTranscriptFilter:
    def __init__(self, ttl_seconds: float = SUSPECT_TRANSCRIPT_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._suspects = deque(maxlen=32)

    def drop_reason(self, work_item: SpeechWorkItem, result, now: float = None) -> str:
        now = now or time.time()
        self._prune(now)
        text = (getattr(result, "text", "") or "").strip()
        compact = normalize_transcript_for_repeat(text)
        if not compact:
            return "weak_candidate_empty_compact_text"

        segment = work_item.segment
        labels = set(work_item.candidate_labels or ())
        voice_seconds = float(getattr(segment, "voice_duration_seconds", 0.0) or 0.0)
        total_seconds = float(getattr(segment, "duration_seconds", 0.0) or 0.0)
        voice_ratio = voice_seconds / max(0.001, total_seconds)
        vad_confidence = float(getattr(segment, "vad_confidence", 0.0) or 0.0)
        vad_blocks = int(getattr(segment, "vad_voice_blocks", 0) or 0)
        peak_margin = float(getattr(segment, "peak_rms_dbfs", -120.0) or -120.0) - float(
            getattr(segment, "energy_threshold_dbfs", -120.0) or -120.0
        )
        avg_logprob = float(getattr(result, "avg_logprob", 0.0) or 0.0)
        no_speech_prob = float(getattr(result, "no_speech_prob", 0.0) or 0.0)
        compression_ratio = float(getattr(result, "compression_ratio", 0.0) or 0.0)
        compact_len = len(compact)

        weak_capture = (
            work_item.low_confidence
            or work_item.short_segment
            or "low_confidence" in labels
            or "short_segment" in labels
            or voice_seconds < 0.7
            or vad_confidence <= 0.25
            or vad_blocks <= 1
            or voice_ratio < 0.35
            or peak_margin < 1.0
        )
        weak_whisper = (
            no_speech_prob >= 0.55
            or avg_logprob <= -0.75
            or compression_ratio >= 2.4
        )
        suspect_text = compact in SUSPECT_SHORT_TRANSCRIPTS
        allowed_short = compact in ALLOW_WEAK_SHORT_TRANSCRIPTS

        if allowed_short and not (no_speech_prob >= 0.75 or avg_logprob <= -1.2):
            return ""

        if weak_capture and suspect_text:
            self._remember(compact, now)
            return (
                "weak_candidate_suspect_phrase "
                f"(text={compact}, voice={voice_seconds:.2f}s, vad={vad_confidence:.2f}, "
                f"ratio={voice_ratio:.2f}, peak_margin={peak_margin:.1f}dB)"
            )

        if weak_capture and weak_whisper and compact_len <= 10:
            self._remember(compact, now)
            return (
                "weak_candidate_low_asr_confidence "
                f"(text={compact}, avg_logprob={avg_logprob:.2f}, no_speech={no_speech_prob:.2f}, "
                f"compression={compression_ratio:.2f})"
            )

        if weak_capture and self._recent_suspect_count(compact, now) >= 1 and compact_len <= 24:
            self._remember(compact, now)
            return f"weak_candidate_repeated_suspect_phrase (text={compact})"

        if weak_capture and compact_len <= 1 and not allowed_short:
            self._remember(compact, now)
            return f"weak_candidate_tiny_transcript (text={compact})"

        return ""

    def _remember(self, compact: str, now: float):
        if compact:
            self._suspects.append((now, compact))

    def _recent_suspect_count(self, compact: str, now: float) -> int:
        return sum(1 for created_at, text in self._suspects if text == compact and now - created_at <= self.ttl_seconds)

    def _prune(self, now: float):
        self._suspects = deque(
            ((created_at, text) for created_at, text in self._suspects if now - created_at <= self.ttl_seconds),
            maxlen=32,
        )


class DebugAudioDumper:
    def __init__(self, config_getter):
        self._config_getter = config_getter

    def dump_if_enabled(self, segment: SpeechSegment, reason: str, flag_name: str) -> Optional[Path]:
        config = self._config_getter()
        debug = getattr(config, "debug", None)
        if (
            not debug
            or not bool(getattr(debug, "enabled", False))
            or not (
                bool(getattr(debug, "save_failed_audio", False))
                or bool(getattr(debug, flag_name, False))
            )
        ):
            return None
        return self.dump(segment, reason, getattr(debug, "diagnostics_audio_dir", "diagnostics/audio"))

    def dump(self, segment: SpeechSegment, reason: str, directory: str = "diagnostics/audio") -> Optional[Path]:
        try:
            root = Path(directory or "diagnostics/audio")
            if not root.is_absolute():
                root = Path.cwd() / root
            root.mkdir(parents=True, exist_ok=True)
            path = root / self._filename(segment, reason)
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(max(1, int(getattr(segment, "sample_rate", 16000) or 16000)))
                wf.writeframes(getattr(segment, "audio_data", b"") or b"")
            logger.info("dumped debug wav: {}", path)
            return path
        except Exception as exc:
            logger.warning("failed to dump debug wav for {}: {}", reason, exc)
            return None

    @staticmethod
    def _filename(segment: SpeechSegment, reason: str) -> str:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        millis = int((time.time() % 1) * 1000)
        safe_reason = _safe_filename(reason or "segment")
        peak = _format_metric(getattr(segment, "peak_rms_dbfs", -120.0))
        gate = _format_metric(getattr(segment, "energy_threshold_dbfs", -120.0))
        voice = _format_metric(getattr(segment, "voice_duration_seconds", 0.0), scale=100)
        total = _format_metric(getattr(segment, "duration_seconds", 0.0), scale=100)
        return (
            f"{timestamp}-{millis:03d}_{safe_reason}_peak{peak}_gate{gate}_"
            f"voice_duration{voice}_total_duration{total}.wav"
        )


class SpeechPipeline:
    def __init__(
        self,
        config_getter,
        recognizer_getter,
        event_bus,
        stats,
        is_running,
        is_paused,
        next_item_id,
        latency_traces,
        notify_user,
        language_revision_getter=None,
    ):
        self._config_getter = config_getter
        self._recognizer_getter = recognizer_getter
        self._event_bus = event_bus
        self._stats = stats
        self._is_running = is_running
        self._is_paused = is_paused
        self._next_item_id = next_item_id
        self._latency_traces = latency_traces
        self._notify_user = notify_user
        self._language_revision_getter = language_revision_getter or (lambda: 0)
        self._processing_lock = threading.Lock()
        initial_policy = RecognitionModePolicy.from_audio_config(config_getter().audio)
        self._queue_size = max(8, initial_policy.queue_size)
        self._queue = queue.Queue(maxsize=self._queue_size)
        self._stop_token = object()
        self._worker_thread = None
        self._recent_transcripts = deque(maxlen=12)
        self._weak_transcript_filter = WeakCandidateTranscriptFilter()
        self._candidate_policy = CandidatePolicy()
        self._pending_buffer = PendingSegmentBuffer(initial_policy.pending_timeout_seconds)
        self._busy_weak_buffer = BusyWeakCandidateBuffer(initial_policy.busy_weak_delay_seconds)
        self._debug_audio = DebugAudioDumper(config_getter)
        self._recognition_busy = False

    def remember_transcript(self, text: str):
        self._recent_transcripts.append((time.time(), text))

    def reset_for_language_switch(self, source_lang: str = "", target_lang: str = "", revision: int = 0):
        dropped = []
        pending = self._pending_buffer.clear()
        if pending:
            dropped.append(("pending", pending))
        busy = self._busy_weak_buffer.clear()
        if busy:
            dropped.append(("busy_weak", busy))

        restore = []
        for item in self._drain_queue_items():
            if item is self._stop_token:
                restore.append(item)
            elif isinstance(item, SpeechWorkItem):
                dropped.append(("queued", item))
        self._restore_queue_items(restore)
        self._recent_transcripts.clear()
        self._weak_transcript_filter = WeakCandidateTranscriptFilter()

        for reason, item in dropped:
            self._stats["dropped_speech"] = self._stats.get("dropped_speech", 0) + 1
            self._debug_audio.dump_if_enabled(item.segment, f"language_switch_{reason}", "save_dropped_audio")
        logger.info(
            "language flow reset: revision={}, direction={}->{}, dropped_pending_queue={}",
            revision,
            source_lang or "unknown",
            target_lang or "unknown",
            len(dropped),
        )

    def on_speech_detected(self, speech_segment):
        if self._is_paused() or not self._is_running():
            return
        config = self._config_getter()
        mode_policy = self._mode_policy(config)
        segment = self._coerce_speech_segment(speech_segment, config.audio.sample_rate)
        self._stats["speech_detected"] += 1

        decision = self._candidate_policy.classify(segment, config.audio)
        if not decision.accepted:
            self._stats["filtered_speech"] += 1
            logger.info(
                "candidate dropped: {}, voice={:.2f}s, total={:.2f}s, peak={:.1f} dBFS, gate={:.1f} dBFS, cut={}",
                decision.reason,
                segment.voice_duration_seconds,
                segment.duration_seconds,
                segment.peak_rms_dbfs,
                segment.energy_threshold_dbfs,
                segment.reason,
            )
            self._debug_audio.dump_if_enabled(segment, decision.reason, "save_dropped_audio")
            return

        now = time.time()
        work_item = SpeechWorkItem(
            segment=segment,
            trace=self._make_latency_trace(now, config),
            candidate_labels=decision.labels,
            candidate_reason=decision.reason,
            low_confidence=decision.low_confidence,
            short_segment=decision.short_segment,
        )
        self._apply_language_snapshot(work_item, config)
        self._refresh_trace_metadata(work_item, config, mode_policy)
        self._log_candidate(work_item)
        if decision.should_dump_low_confidence:
            dumped = self._debug_audio.dump_if_enabled(
                segment,
                _debug_reason(work_item, "low_confidence_candidate"),
                "save_low_confidence_audio",
            )
            work_item.dumped_low_confidence = bool(dumped)

        self._flush_expired_pending(now)
        if self._pending_buffer.has_pending():
            action, item = self._pending_buffer.add_or_merge(work_item, now)
            if action == "merged":
                logger.info(
                    "segment merged: voice={:.2f}s, total={:.2f}s, peak={:.1f} dBFS, gate={:.1f} dBFS, reason={}",
                    item.segment.voice_duration_seconds,
                    item.segment.duration_seconds,
                    item.segment.peak_rms_dbfs,
                    item.segment.energy_threshold_dbfs,
                    item.segment.reason,
                )
                self._enqueue_with_backpressure(item, mode_policy)
                return
        if self._should_pending(work_item):
            self._handle_pending(work_item, now, mode_policy)
            return
        self._enqueue_with_backpressure(work_item, mode_policy)

    def start(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_thread = threading.Thread(
            target=self._worker,
            name="speech-worker",
            daemon=True,
        )
        self._worker_thread.start()
        logger.info("speech processing queue started")

    def stop(self) -> bool:
        pending = self._pending_buffer.clear()
        if pending:
            self._enqueue_with_backpressure(pending, self._mode_policy(self._config_getter()))
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        try:
            self._queue.put_nowait(self._stop_token)
        except queue.Full:
            pass
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=8)
        if self._worker_thread and self._worker_thread.is_alive():
            logger.warning("speech processing thread is still stopping; skip model cleanup to avoid resource race")
            return False
        self._worker_thread = None
        return True

    def _worker(self):
        while True:
            try:
                work_item = self._queue.get(timeout=0.1)
            except queue.Empty:
                self._flush_expired_pending(time.time())
                self._flush_busy_weak_buffer(time.time())
                continue
            if work_item is self._stop_token:
                return
            self._flush_expired_pending(time.time())
            self._recognition_busy = True
            self._process(work_item)
            self._recognition_busy = False
            self._flush_busy_weak_buffer(time.time())

    def _process(self, work_item):
        try:
            if self._is_paused() or not self._is_running():
                return
            config = self._config_getter()
            segment, trace, item = self._normalize_work_item(work_item, config.audio.sample_rate)
            self._apply_language_snapshot(item, config)
            mode_policy = self._mode_policy(config)
            self._refresh_trace_metadata(item, config, mode_policy)
            fatal_reason = self._candidate_policy.fatal_drop_reason(segment)
            if fatal_reason:
                self._stats["filtered_speech"] += 1
                logger.info("candidate dropped before whisper: {}", fatal_reason)
                self._debug_audio.dump_if_enabled(segment, fatal_reason, "save_dropped_audio")
                return
            trace.dequeued_at = time.time()
            t0 = time.time()
            trace.transcription_started_at = t0
            logger.info(
                "sent to whisper: labels={}, direction={}->{}, revision={}, whisper_language={}, voice={:.2f}s, total={:.2f}s, peak={:.1f} dBFS, gate={:.1f} dBFS, cut={}",
                ",".join(item.candidate_labels or ("candidate",)),
                item.source_lang or "unknown",
                item.target_lang or "unknown",
                item.language_revision,
                item.whisper_language or "auto",
                segment.voice_duration_seconds,
                segment.duration_seconds,
                segment.peak_rms_dbfs,
                segment.energy_threshold_dbfs,
                segment.reason,
            )
            recognizer = self._recognizer_getter()
            self._refresh_trace_recognizer_metadata(trace, recognizer)
            with self._processing_lock:
                result = self._transcribe_with_language_snapshot(
                    recognizer,
                    segment.audio_data,
                    segment.sample_rate or config.audio.sample_rate,
                    item.whisper_language,
                )
            self._refresh_trace_recognizer_metadata(trace, recognizer)
            trace.transcription_finished_at = time.time()
            text = result.text
            current_revision = int(self._language_revision_getter() or 0)
            if item.language_revision and current_revision and item.language_revision != current_revision:
                self._stats["dropped_speech"] = self._stats.get("dropped_speech", 0) + 1
                logger.info(
                    "stale language flow result dropped: item_revision={}, current_revision={}, direction={}->{}, text={}, lang={}, prob={:.2f}",
                    item.language_revision,
                    current_revision,
                    item.source_lang or "unknown",
                    item.target_lang or "unknown",
                    (text or "")[:80],
                    result.language or "unknown",
                    result.language_probability,
                )
                self._debug_audio.dump_if_enabled(segment, "stale_language_flow", "save_dropped_audio")
                return
            if not text or len(text.strip()) < 2:
                self._stats["filtered_speech"] += 1
                logger.info(
                    "empty asr: text_len={}, lang={}, prob={:.2f}, voice={:.2f}s, total={:.2f}s, peak={:.1f} dBFS, gate={:.1f} dBFS",
                    len(text.strip()) if text else 0,
                    result.language or "unknown",
                    result.language_probability,
                    segment.voice_duration_seconds,
                    segment.duration_seconds,
                    segment.peak_rms_dbfs,
                    segment.energy_threshold_dbfs,
                )
                self._debug_audio.dump_if_enabled(segment, "empty_asr", "save_empty_asr_audio")
                return
            vad_drop_reason = self._vad_whisper_drop_reason(segment, result)
            if vad_drop_reason:
                self._stats["filtered_speech"] += 1
                logger.info(
                    "filtered transcription: {}, text={}, lang={}, prob={:.2f}, avg_logprob={:.2f}, no_speech={:.2f}, compression={:.2f}",
                    vad_drop_reason,
                    text[:120],
                    result.language or "unknown",
                    result.language_probability,
                    getattr(result, "avg_logprob", 0.0),
                    getattr(result, "no_speech_prob", 0.0),
                    getattr(result, "compression_ratio", 0.0),
                )
                self._debug_audio.dump_if_enabled(segment, vad_drop_reason, "save_dropped_audio")
                return
            weak_drop_reason = self._weak_transcript_filter.drop_reason(item, result)
            if weak_drop_reason:
                self._stats["filtered_speech"] += 1
                logger.info(
                    "filtered transcription: {}, text={}, labels={}, lang={}, prob={:.2f}, avg_logprob={:.2f}, no_speech={:.2f}, compression={:.2f}",
                    weak_drop_reason,
                    text[:120],
                    ",".join(item.candidate_labels or ("candidate",)),
                    result.language or "unknown",
                    result.language_probability,
                    getattr(result, "avg_logprob", 0.0),
                    getattr(result, "no_speech_prob", 0.0),
                    getattr(result, "compression_ratio", 0.0),
                )
                self._debug_audio.dump_if_enabled(segment, weak_drop_reason, "save_dropped_audio")
                return
            drop_reason = should_drop_transcription_result(
                result,
                expected_language=item.whisper_language,
                recent_texts=self._recent_transcript_texts(),
                config=config.whisper,
            )
            if drop_reason:
                self._stats["filtered_speech"] += 1
                logger.info(
                    "filtered transcription: {}, text={}, lang={}, prob={:.2f}, avg_logprob={:.2f}, no_speech={:.2f}, compression={:.2f}",
                    drop_reason,
                    text[:120],
                    result.language or "unknown",
                    result.language_probability,
                    getattr(result, "avg_logprob", 0.0),
                    getattr(result, "no_speech_prob", 0.0),
                    getattr(result, "compression_ratio", 0.0),
                )
                self._debug_audio.dump_if_enabled(segment, drop_reason, "save_dropped_audio")
                return
            forced_language_drop_reason = self._forced_language_drop_reason(item, result)
            if forced_language_drop_reason:
                self._stats["filtered_speech"] += 1
                logger.info(
                    "filtered transcription: {}, text={}, expected={}, lang={}, prob={:.2f}, avg_logprob={:.2f}, no_speech={:.2f}, compression={:.2f}",
                    forced_language_drop_reason,
                    text[:120],
                    item.source_lang or item.whisper_language or "unknown",
                    result.language or "unknown",
                    result.language_probability,
                    getattr(result, "avg_logprob", 0.0),
                    getattr(result, "no_speech_prob", 0.0),
                    getattr(result, "compression_ratio", 0.0),
                )
                self._debug_audio.dump_if_enabled(segment, forced_language_drop_reason, "save_dropped_audio")
                return
            logger.info(
                "[recognition] {} (lang={}, prob={:.2f}, expected={}, direction={}->{}, revision={}, avg_logprob={:.2f}, no_speech={:.2f}, {:.1f}s)",
                text[:80],
                result.language or "unknown",
                result.language_probability,
                item.source_lang or item.whisper_language or "unknown",
                item.source_lang or "unknown",
                item.target_lang or "unknown",
                item.language_revision,
                getattr(result, "avg_logprob", 0.0),
                getattr(result, "no_speech_prob", 0.0),
                time.time() - t0,
            )

            item_id = self._next_item_id()
            trace.item_id = item_id
            self._latency_traces[item_id] = trace
            self._event_bus.publish(
                TranscriptReady(
                    text=text,
                    language=result.language,
                    trace_id=item_id,
                    language_probability=result.language_probability,
                    source_lang=item.source_lang,
                    target_lang=item.target_lang,
                    whisper_language=item.whisper_language,
                    language_revision=item.language_revision,
                    avg_logprob=getattr(result, "avg_logprob", 0.0),
                    no_speech_prob=getattr(result, "no_speech_prob", 0.0),
                    compression_ratio=getattr(result, "compression_ratio", 0.0),
                )
            )
        except Exception as exc:
            self._stats["errors"] += 1
            logger.exception("speech processing failed: {}", exc)
            self._notify_user("处理失败", str(exc), "错误")

    def _normalize_work_item(self, work_item, sample_rate: int):
        if isinstance(work_item, SpeechWorkItem):
            return work_item.segment, work_item.trace, work_item
        if isinstance(work_item, SpeechSegment):
            now = time.time()
            config = self._config_getter()
            item = SpeechWorkItem(
                work_item,
                self._make_latency_trace(now, config),
                candidate_labels=("candidate",),
            )
            self._apply_language_snapshot(item, config)
            return item.segment, item.trace, item
        now = time.time()
        config = self._config_getter()
        item = SpeechWorkItem(
            self._coerce_speech_segment(work_item, sample_rate),
            self._make_latency_trace(now, config),
            candidate_labels=("candidate",),
        )
        self._apply_language_snapshot(item, config)
        return item.segment, item.trace, item

    def _make_latency_trace(self, now: float, config) -> LatencyTrace:
        translation = getattr(config, "translation", None)
        whisper = getattr(config, "whisper", None)
        source_lang = str(getattr(translation, "source_lang", "") or "")
        target_lang = str(getattr(translation, "target_lang", "") or "")
        whisper_language = str(getattr(whisper, "language", "") or "")
        revision = int(self._language_revision_getter() or 0)
        return LatencyTrace(
            item_id="",
            speech_detected_at=now,
            queued_at=now,
            source_lang=source_lang,
            target_lang=target_lang,
            whisper_language=whisper_language,
            language_revision=revision,
            whisper_model_size=_trace_whisper_model_size(whisper),
            whisper_device=str(getattr(whisper, "device", "") or ""),
            whisper_compute_type=str(getattr(whisper, "compute_type", "") or ""),
            whisper_cpu_threads=_trace_cpu_threads(whisper),
        )

    def _apply_language_snapshot(self, item: SpeechWorkItem, config):
        translation = getattr(config, "translation", None)
        whisper = getattr(config, "whisper", None)
        if not item.source_lang:
            item.source_lang = str(getattr(item.trace, "source_lang", "") or getattr(translation, "source_lang", "") or "")
        if not item.target_lang:
            item.target_lang = str(getattr(item.trace, "target_lang", "") or getattr(translation, "target_lang", "") or "")
        if not item.whisper_language:
            item.whisper_language = str(
                getattr(item.trace, "whisper_language", "") or getattr(whisper, "language", "") or ""
            )
        if not item.language_revision:
            item.language_revision = int(getattr(item.trace, "language_revision", 0) or self._language_revision_getter() or 0)
        item.trace.source_lang = item.source_lang
        item.trace.target_lang = item.target_lang
        item.trace.whisper_language = item.whisper_language
        item.trace.language_revision = item.language_revision

    def _refresh_trace_metadata(
        self,
        item: SpeechWorkItem,
        config,
        mode_policy: RecognitionModePolicy = None,
    ):
        trace = item.trace
        segment = item.segment
        whisper = getattr(config, "whisper", None)
        mode_policy = mode_policy or self._mode_policy(config)
        trace.latency_mode = mode_policy.mode
        trace.candidate_labels = tuple(item.candidate_labels or ("candidate",))
        trace.segment_voice_seconds = max(0.0, float(getattr(segment, "voice_duration_seconds", 0.0) or 0.0))
        trace.segment_total_seconds = max(0.0, float(getattr(segment, "duration_seconds", 0.0) or 0.0))
        trace.whisper_model_size = _trace_whisper_model_size(whisper)
        trace.whisper_device = str(getattr(whisper, "device", "") or "")
        trace.whisper_compute_type = str(getattr(whisper, "compute_type", "") or "")
        trace.whisper_cpu_threads = _trace_cpu_threads(whisper)
        trace.fast_path_allowed = item.source_lang == "en" and item.target_lang == "zh"
        trace.fast_path_ready = trace.fast_path_allowed and trace.whisper_model_size.endswith(".en")

    @staticmethod
    def _refresh_trace_recognizer_metadata(trace: LatencyTrace, recognizer):
        config = getattr(recognizer, "config", None)
        if not config:
            return
        try:
            if hasattr(recognizer, "_effective_model_size"):
                trace.whisper_model_size = str(recognizer._effective_model_size() or trace.whisper_model_size)
        except Exception:
            pass
        trace.whisper_device = str(
            getattr(recognizer, "runtime_device", "") or getattr(config, "device", trace.whisper_device)
            or trace.whisper_device
        )
        trace.whisper_compute_type = str(
            getattr(recognizer, "runtime_compute_type", "")
            or getattr(config, "compute_type", trace.whisper_compute_type)
            or trace.whisper_compute_type
        )
        if hasattr(recognizer, "_resolved_cpu_threads"):
            try:
                trace.whisper_cpu_threads = int(recognizer._resolved_cpu_threads())
                return
            except Exception:
                pass
        trace.whisper_cpu_threads = _trace_cpu_threads(config)

    @staticmethod
    def _transcribe_with_language_snapshot(recognizer, audio_data: bytes, sample_rate: int, language: str):
        transcribe = recognizer.transcribe_audio_bytes_with_language
        try:
            return transcribe(audio_data, sample_rate=sample_rate, language_override=language)
        except TypeError as exc:
            if "language_override" not in str(exc):
                raise
            return transcribe(audio_data, sample_rate=sample_rate)

    def _recent_transcript_texts(self, now: float = None) -> list:
        now = now or time.time()
        recent = [
            (created_at, text)
            for created_at, text in self._recent_transcripts
            if now - created_at <= 8.0
        ]
        self._recent_transcripts = deque(recent, maxlen=12)
        return [text for _, text in recent]

    def _mode_policy(self, config) -> RecognitionModePolicy:
        policy = RecognitionModePolicy.from_audio_config(config.audio)
        source_lang = str(getattr(getattr(config, "translation", None), "source_lang", "") or "").strip().lower()
        target_lang = str(getattr(getattr(config, "translation", None), "target_lang", "") or "").strip().lower()
        pure_english = bool(getattr(getattr(config, "whisper", None), "pure_english_environment", False))
        if source_lang == "en" and target_lang == "zh":
            policy = self._pure_english_mode_policy(policy) if pure_english else self._english_realtime_mode_policy(policy)
        self._pending_buffer.timeout_seconds = policy.pending_timeout_seconds
        self._busy_weak_buffer.timeout_seconds = max(0.0, policy.busy_weak_delay_seconds)
        self._ensure_queue_capacity(policy.queue_size)
        return policy

    @staticmethod
    def _english_realtime_mode_policy(policy: RecognitionModePolicy) -> RecognitionModePolicy:
        if policy.mode == LATENCY_MODE_FAST:
            return RecognitionModePolicy(
                mode=policy.mode,
                queue_size=policy.queue_size,
                pending_timeout_seconds=0.25,
                allow_fast_output=policy.allow_fast_output,
                busy_weak_delay_seconds=0.42,
                busy_weak_stale_seconds=1.50,
            )
        if policy.mode == LATENCY_MODE_BALANCED:
            return RecognitionModePolicy(
                mode=policy.mode,
                queue_size=policy.queue_size,
                pending_timeout_seconds=0.40,
                allow_fast_output=policy.allow_fast_output,
                busy_weak_delay_seconds=0.60,
                busy_weak_stale_seconds=1.80,
            )
        return policy

    @staticmethod
    def _pure_english_mode_policy(policy: RecognitionModePolicy) -> RecognitionModePolicy:
        if policy.mode == LATENCY_MODE_FAST:
            return RecognitionModePolicy(
                mode=policy.mode,
                queue_size=policy.queue_size,
                pending_timeout_seconds=0.20,
                allow_fast_output=policy.allow_fast_output,
                busy_weak_delay_seconds=0.35,
                busy_weak_stale_seconds=1.30,
            )
        if policy.mode == LATENCY_MODE_BALANCED:
            return RecognitionModePolicy(
                mode=policy.mode,
                queue_size=policy.queue_size,
                pending_timeout_seconds=0.35,
                allow_fast_output=policy.allow_fast_output,
                busy_weak_delay_seconds=0.50,
                busy_weak_stale_seconds=1.60,
            )
        return policy

    def _ensure_queue_capacity(self, queue_size: int):
        queue_size = max(2, int(queue_size or 2))
        if queue_size <= self._queue_size:
            return
        if self._worker_thread and self._worker_thread.is_alive():
            return
        replacement = queue.Queue(maxsize=queue_size)
        while True:
            try:
                replacement.put_nowait(self._queue.get_nowait())
            except queue.Empty:
                break
            except queue.Full:
                break
        self._queue = replacement
        self._queue_size = queue_size

    def _should_pending(self, work_item: SpeechWorkItem) -> bool:
        return float(getattr(work_item.segment, "voice_duration_seconds", 0.0) or 0.0) < SHORT_SEGMENT_PENDING_SECONDS

    def _handle_pending(self, work_item: SpeechWorkItem, now: float, mode_policy: RecognitionModePolicy):
        action, item = self._pending_buffer.add_or_merge(work_item, now)
        if action == "pending":
            logger.info(
                "segment pending: wait={:.0f}ms, voice={:.2f}s, total={:.2f}s, labels={}",
                mode_policy.pending_timeout_seconds * 1000,
                item.segment.voice_duration_seconds,
                item.segment.duration_seconds,
                ",".join(item.candidate_labels or ()),
            )
            return
        logger.info(
            "segment merged: voice={:.2f}s, total={:.2f}s, peak={:.1f} dBFS, gate={:.1f} dBFS, reason={}",
            item.segment.voice_duration_seconds,
            item.segment.duration_seconds,
            item.segment.peak_rms_dbfs,
            item.segment.energy_threshold_dbfs,
            item.segment.reason,
        )
        self._enqueue_with_backpressure(item, mode_policy)

    def _flush_expired_pending(self, now: float):
        expired = self._pending_buffer.pop_expired(now)
        if not expired:
            return
        logger.info(
            "segment pending timeout: voice={:.2f}s, total={:.2f}s, labels={}",
            expired.segment.voice_duration_seconds,
            expired.segment.duration_seconds,
            ",".join(expired.candidate_labels or ()),
        )
        self._enqueue_with_backpressure(expired, self._mode_policy(self._config_getter()))

    def _flush_busy_weak_buffer(self, now: float, force: bool = False):
        pending = self._busy_weak_buffer.pending
        if not pending:
            return
        mode_policy = self._mode_policy(self._config_getter())
        stale_seconds = max(0.0, float(mode_policy.busy_weak_stale_seconds or 0.0))
        age_seconds = self._busy_weak_buffer.age_seconds(now)
        if not force:
            if self._recognition_busy or self._queue.qsize() > 0:
                if stale_seconds and age_seconds > stale_seconds:
                    dropped = self._busy_weak_buffer.clear()
                    self._stats["dropped_speech"] += 1
                    logger.warning(
                        "queue busy weak candidate dropped after stale delay: age={:.0f}ms, labels={}, voice={:.2f}s, total={:.2f}s",
                        age_seconds * 1000,
                        ",".join(dropped.candidate_labels or ()),
                        dropped.segment.voice_duration_seconds,
                        dropped.segment.duration_seconds,
                    )
                    self._debug_audio.dump_if_enabled(dropped.segment, "queue_busy_weak_stale", "save_dropped_audio")
                return

        item = self._busy_weak_buffer.pop_expired(now)
        if item is None and force:
            item = self._busy_weak_buffer.clear()
        if item is None:
            return
        logger.info(
            "queue busy weak candidate released: force={}, voice={:.2f}s, total={:.2f}s, labels={}",
            force,
            item.segment.voice_duration_seconds,
            item.segment.duration_seconds,
            ",".join(item.candidate_labels or ()),
        )
        self._enqueue_with_backpressure(item, mode_policy, protect_busy_weak=False)

    def _enqueue_with_backpressure(
        self,
        work_item: SpeechWorkItem,
        mode_policy: RecognitionModePolicy,
        protect_busy_weak: bool = True,
    ):
        self._ensure_queue_capacity(mode_policy.queue_size)
        work_item.trace.queued_at = time.time()
        if protect_busy_weak and self._should_buffer_busy_weak_candidate(work_item, mode_policy):
            self._buffer_busy_weak_candidate(work_item, mode_policy, time.time())
            return
        self._flush_busy_weak_buffer(time.time())
        if not self._queue.full() and self._try_prioritize_strong_candidate(work_item):
            return
        try:
            self._queue.put_nowait(work_item)
            return
        except queue.Full:
            pass

        if self._try_merge_with_queued_short_segment(work_item):
            return

        dropped = self._drop_one_queued_item_for_realtime(weak_only=self._is_weak_work_item(work_item))
        if not dropped and self._is_weak_work_item(work_item):
            self._stats["dropped_speech"] += 1
            logger.warning(
                "speech queue full: dropped current weak candidate, labels={}, voice={:.2f}s, total={:.2f}s",
                ",".join(work_item.candidate_labels or ()),
                work_item.segment.voice_duration_seconds,
                work_item.segment.duration_seconds,
            )
            self._debug_audio.dump_if_enabled(work_item.segment, "queue_full_current_weak", "save_dropped_audio")
            return
        if dropped:
            self._stats["dropped_speech"] += 1
            logger.warning(
                "speech queue full: dropped queued segment, labels={}, voice={:.2f}s, total={:.2f}s",
                ",".join(dropped.candidate_labels or ()),
                dropped.segment.voice_duration_seconds,
                dropped.segment.duration_seconds,
            )
            self._debug_audio.dump_if_enabled(dropped.segment, "queue_full_dropped", "save_dropped_audio")
        try:
            work_item.trace.queued_at = time.time()
            self._queue.put_nowait(work_item)
        except queue.Full:
            self._stats["dropped_speech"] += 1
            logger.warning(
                "speech queue full: dropped current segment, labels={}, voice={:.2f}s, total={:.2f}s",
                ",".join(work_item.candidate_labels or ()),
                work_item.segment.voice_duration_seconds,
                work_item.segment.duration_seconds,
            )
            self._debug_audio.dump_if_enabled(work_item.segment, "queue_full_current", "save_dropped_audio")

    def _should_buffer_busy_weak_candidate(self, work_item: SpeechWorkItem, mode_policy: RecognitionModePolicy) -> bool:
        if not self._is_weak_work_item(work_item):
            return False
        if float(getattr(mode_policy, "busy_weak_delay_seconds", 0.0) or 0.0) <= 0:
            return False
        return self._recognition_busy or self._queue.qsize() > 0 or self._busy_weak_buffer.has_pending()

    def _buffer_busy_weak_candidate(self, work_item: SpeechWorkItem, mode_policy: RecognitionModePolicy, now: float):
        self._busy_weak_buffer.timeout_seconds = max(0.0, mode_policy.busy_weak_delay_seconds)
        action, item = self._busy_weak_buffer.add_or_merge(work_item, now)
        if action == "merged":
            logger.info(
                "queue busy weak candidate merged: wait={:.0f}ms, voice={:.2f}s, total={:.2f}s, labels={}",
                mode_policy.busy_weak_delay_seconds * 1000,
                item.segment.voice_duration_seconds,
                item.segment.duration_seconds,
                ",".join(item.candidate_labels or ()),
            )
            return
        logger.info(
            "queue busy weak candidate delayed: wait={:.0f}ms, queue={}, recognizer_busy={}, voice={:.2f}s, total={:.2f}s, labels={}",
            mode_policy.busy_weak_delay_seconds * 1000,
            self._queue.qsize(),
            self._recognition_busy,
            item.segment.voice_duration_seconds,
            item.segment.duration_seconds,
            ",".join(item.candidate_labels or ()),
        )

    def _try_prioritize_strong_candidate(self, work_item: SpeechWorkItem) -> bool:
        if self._is_weak_work_item(work_item):
            return False
        items = self._drain_queue_items()
        if not items:
            return False
        if self._stop_token in items:
            self._restore_queue_items(items)
            return False
        insert_index = None
        for index, item in enumerate(items):
            if self._is_weak_work_item(item):
                insert_index = index
                break
        if insert_index is None:
            self._restore_queue_items(items)
            return False
        items.insert(insert_index, work_item)
        self._restore_queue_items(items)
        logger.info(
            "speech queue prioritized strong candidate before weak queued items: voice={:.2f}s, labels={}",
            work_item.segment.voice_duration_seconds,
            ",".join(work_item.candidate_labels or ()),
        )
        return True

    def _try_merge_with_queued_short_segment(self, work_item: SpeechWorkItem) -> bool:
        items = self._drain_queue_items()
        if not items:
            return False

        merged = False
        replacement = []
        for item in items:
            if not merged and self._can_merge_short_segments(item, work_item):
                replacement.append(merge_speech_work_items(item, work_item, "queue_full_merge"))
                merged = True
            else:
                replacement.append(item)
        self._restore_queue_items(replacement)
        if merged:
            logger.info("segment merged: queue_full short candidates")
        return merged

    def _drop_one_queued_item_for_realtime(self, weak_only: bool = False) -> Optional[SpeechWorkItem]:
        items = self._drain_queue_items()
        if not items:
            return None
        if self._stop_token in items:
            self._restore_queue_items(items)
            return None
        drop_index = None
        for index, item in enumerate(items):
            if self._is_weak_work_item(item):
                drop_index = index
                break
        if drop_index is None:
            if weak_only:
                self._restore_queue_items(items)
                return None
            drop_index = 0
        dropped = items.pop(drop_index)
        self._restore_queue_items(items)
        return dropped

    def _drain_queue_items(self) -> list:
        items = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is self._stop_token:
                items.append(item)
                break
            items.append(item)
        return items

    def _restore_queue_items(self, items: Iterable):
        for item in items:
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                if isinstance(item, SpeechWorkItem):
                    self._stats["dropped_speech"] += 1
                    logger.warning("speech queue restore overflow: dropped segment")
                    self._debug_audio.dump_if_enabled(item.segment, "queue_restore_overflow", "save_dropped_audio")

    @staticmethod
    def _can_merge_short_segments(left: SpeechWorkItem, right: SpeechWorkItem) -> bool:
        if not isinstance(left, SpeechWorkItem) or not isinstance(right, SpeechWorkItem):
            return False
        if not (SpeechPipeline._is_weak_work_item(left) and SpeechPipeline._is_weak_work_item(right)):
            return False
        left_voice = float(getattr(left.segment, "voice_duration_seconds", 0.0) or 0.0)
        right_voice = float(getattr(right.segment, "voice_duration_seconds", 0.0) or 0.0)
        return left_voice < SHORT_SEGMENT_PENDING_SECONDS or right_voice < SHORT_SEGMENT_PENDING_SECONDS

    @staticmethod
    def _is_weak_work_item(item) -> bool:
        if not isinstance(item, SpeechWorkItem):
            return False
        labels = set(item.candidate_labels or ())
        return bool(
            getattr(item, "low_confidence", False)
            or getattr(item, "short_segment", False)
            or "low_confidence" in labels
            or "short_segment" in labels
        )

    @staticmethod
    def _vad_whisper_drop_reason(segment: SpeechSegment, result) -> str:
        activity_source = str(getattr(segment, "activity_source", "") or "")
        vad_blocks = int(getattr(segment, "vad_voice_blocks", 0) or 0)
        vad_confidence = float(getattr(segment, "vad_confidence", 0.0) or 0.0)
        if activity_source != "energy" or vad_blocks > 0 or vad_confidence > 0:
            return ""
        text = (getattr(result, "text", "") or "").strip()
        compact_len = len(re.sub(r"[\W_]+", "", text, flags=re.UNICODE))
        probability = float(getattr(result, "language_probability", 0.0) or 0.0)
        if probability < 0.75 or compact_len <= 8:
            return (
                "energy_only_without_vad_voice "
                f"(vad={vad_confidence:.2f}, prob={probability:.2f}, text_len={compact_len})"
            )
        return ""

    @staticmethod
    def _forced_language_drop_reason(item: SpeechWorkItem, result) -> str:
        expected = _normalize_pipeline_language(item.source_lang or item.whisper_language)
        forced = _normalize_pipeline_language(item.whisper_language)
        detected = _normalize_pipeline_language(getattr(result, "language", ""))
        if forced not in {"en", "zh"} or not expected:
            return ""
        if detected and detected != expected:
            return ""

        text = (getattr(result, "text", "") or "").strip()
        compact = normalize_transcript_for_repeat(text)
        if not compact:
            return ""

        segment = item.segment
        block_count = int(getattr(segment, "block_count", 0) or 0)
        vad_blocks = int(getattr(segment, "vad_voice_blocks", 0) or 0)
        vad_confidence = float(getattr(segment, "vad_confidence", 0.0) or 0.0)
        if vad_confidence <= 0 and block_count > 0:
            vad_confidence = vad_blocks / max(1, block_count)
        weak_vad = block_count > 0 and (vad_blocks <= 1 or vad_confidence < 0.75)
        weak_capture = weak_vad or SpeechPipeline._is_weak_work_item(item)

        avg_logprob = float(getattr(result, "avg_logprob", 0.0) or 0.0)
        no_speech_prob = float(getattr(result, "no_speech_prob", 0.0) or 0.0)
        compression_ratio = float(getattr(result, "compression_ratio", 0.0) or 0.0)
        compact_len = len(compact)

        if expected == "zh":
            if weak_capture and no_speech_prob >= 0.55 and avg_logprob <= -0.75 and compact_len <= 72:
                return (
                    "forced_language_low_asr_confidence "
                    f"(expected=zh, vad={vad_confidence:.2f}, avg_logprob={avg_logprob:.2f}, "
                    f"no_speech={no_speech_prob:.2f})"
                )
            if no_speech_prob >= 0.72 and avg_logprob <= -0.65 and compact_len <= 96:
                return (
                    "forced_language_high_no_speech "
                    f"(expected=zh, avg_logprob={avg_logprob:.2f}, no_speech={no_speech_prob:.2f})"
                )

        if expected == "en":
            lowered = compact.casefold()
            suspect_short = lowered in SUSPECT_SHORT_TRANSCRIPTS
            if weak_capture and suspect_short and (no_speech_prob >= 0.35 or avg_logprob <= -0.70):
                return (
                    "forced_language_suspect_phrase "
                    f"(expected=en, text={lowered}, vad={vad_confidence:.2f}, "
                    f"avg_logprob={avg_logprob:.2f}, no_speech={no_speech_prob:.2f})"
                )
            if weak_capture and no_speech_prob >= 0.72 and avg_logprob <= -0.90 and compact_len <= 48:
                return (
                    "forced_language_low_asr_confidence "
                    f"(expected=en, vad={vad_confidence:.2f}, avg_logprob={avg_logprob:.2f}, "
                    f"no_speech={no_speech_prob:.2f})"
                )

        if weak_capture and compression_ratio >= 2.4 and avg_logprob <= -0.55 and compact_len <= 96:
            return (
                "forced_language_high_compression "
                f"(expected={expected}, compression={compression_ratio:.2f}, avg_logprob={avg_logprob:.2f})"
            )
        return ""

    @staticmethod
    def _coerce_speech_segment(speech_segment, sample_rate: int) -> SpeechSegment:
        if isinstance(speech_segment, SpeechSegment):
            return speech_segment
        audio_data = speech_segment or b""
        sample_rate = max(1, int(sample_rate or 16000))
        duration = (len(audio_data) // 2) / sample_rate
        return SpeechSegment(
            audio_data=audio_data,
            sample_rate=sample_rate,
            duration_seconds=duration,
            voice_duration_seconds=duration,
            block_count=0,
            voice_blocks=0,
            peak_rms_dbfs=-120.0,
            energy_threshold_dbfs=-120.0,
            noise_floor_dbfs=None,
            reason="legacy audio bytes",
        )

    @staticmethod
    def _log_candidate(work_item: SpeechWorkItem):
        segment = work_item.segment
        if work_item.low_confidence:
            logger.info(
                "candidate low confidence: labels={}, reason={}, voice={:.2f}s, total={:.2f}s, peak={:.1f} dBFS, gate={:.1f} dBFS",
                ",".join(work_item.candidate_labels or ()),
                work_item.candidate_reason,
                segment.voice_duration_seconds,
                segment.duration_seconds,
                segment.peak_rms_dbfs,
                segment.energy_threshold_dbfs,
            )
        else:
            logger.info(
                "candidate accepted: labels={}, reason={}, voice={:.2f}s, total={:.2f}s, peak={:.1f} dBFS, gate={:.1f} dBFS",
                ",".join(work_item.candidate_labels or ()),
                work_item.candidate_reason,
                segment.voice_duration_seconds,
                segment.duration_seconds,
                segment.peak_rms_dbfs,
                segment.energy_threshold_dbfs,
            )


def merge_speech_work_items(left: SpeechWorkItem, right: SpeechWorkItem, reason: str = "merged") -> SpeechWorkItem:
    candidate_labels = tuple(sorted(set(left.candidate_labels + right.candidate_labels + ("merged",))))
    return SpeechWorkItem(
        segment=merge_speech_segments(left.segment, right.segment, reason),
        trace=LatencyTrace(
            item_id="",
            speech_detected_at=min(left.trace.speech_detected_at, right.trace.speech_detected_at),
            queued_at=min(left.trace.queued_at or time.time(), right.trace.queued_at or time.time()),
            source_lang=left.source_lang or getattr(left.trace, "source_lang", ""),
            target_lang=left.target_lang or getattr(left.trace, "target_lang", ""),
            whisper_language=left.whisper_language or getattr(left.trace, "whisper_language", ""),
            language_revision=left.language_revision or getattr(left.trace, "language_revision", 0),
            latency_mode=getattr(left.trace, "latency_mode", "") or getattr(right.trace, "latency_mode", ""),
            candidate_labels=candidate_labels,
            whisper_model_size=getattr(left.trace, "whisper_model_size", "")
            or getattr(right.trace, "whisper_model_size", ""),
            whisper_device=getattr(left.trace, "whisper_device", "") or getattr(right.trace, "whisper_device", ""),
            whisper_compute_type=getattr(left.trace, "whisper_compute_type", "")
            or getattr(right.trace, "whisper_compute_type", ""),
            whisper_cpu_threads=int(
                getattr(left.trace, "whisper_cpu_threads", 0) or getattr(right.trace, "whisper_cpu_threads", 0) or 0
            ),
            fast_path_allowed=bool(
                getattr(left.trace, "fast_path_allowed", False) or getattr(right.trace, "fast_path_allowed", False)
            ),
            fast_path_ready=bool(
                getattr(left.trace, "fast_path_ready", False) or getattr(right.trace, "fast_path_ready", False)
            ),
        ),
        candidate_labels=candidate_labels,
        candidate_reason=reason,
        low_confidence=left.low_confidence or right.low_confidence,
        short_segment=False,
        dumped_low_confidence=left.dumped_low_confidence or right.dumped_low_confidence,
        source_lang=left.source_lang or getattr(left.trace, "source_lang", ""),
        target_lang=left.target_lang or getattr(left.trace, "target_lang", ""),
        whisper_language=left.whisper_language or getattr(left.trace, "whisper_language", ""),
        language_revision=left.language_revision or getattr(left.trace, "language_revision", 0),
    )


def merge_speech_segments(left: SpeechSegment, right: SpeechSegment, reason: str = "merged") -> SpeechSegment:
    sample_rate = int(getattr(left, "sample_rate", 0) or getattr(right, "sample_rate", 0) or 16000)
    audio_data = (getattr(left, "audio_data", b"") or b"") + (getattr(right, "audio_data", b"") or b"")
    duration = float(getattr(left, "duration_seconds", 0.0) or 0.0) + float(
        getattr(right, "duration_seconds", 0.0) or 0.0
    )
    voice_duration = float(getattr(left, "voice_duration_seconds", 0.0) or 0.0) + float(
        getattr(right, "voice_duration_seconds", 0.0) or 0.0
    )
    block_count = int(getattr(left, "block_count", 0) or 0) + int(getattr(right, "block_count", 0) or 0)
    voice_blocks = int(getattr(left, "voice_blocks", 0) or 0) + int(getattr(right, "voice_blocks", 0) or 0)
    vad_voice_blocks = int(getattr(left, "vad_voice_blocks", 0) or 0) + int(
        getattr(right, "vad_voice_blocks", 0) or 0
    )
    energy_voice_blocks = int(getattr(left, "energy_voice_blocks", 0) or 0) + int(
        getattr(right, "energy_voice_blocks", 0) or 0
    )
    peak = max(
        float(getattr(left, "peak_rms_dbfs", -120.0) or -120.0),
        float(getattr(right, "peak_rms_dbfs", -120.0) or -120.0),
    )
    gate_values = [
        float(value)
        for value in (
            getattr(left, "energy_threshold_dbfs", None),
            getattr(right, "energy_threshold_dbfs", None),
        )
        if value is not None
    ]
    gate = max(gate_values) if gate_values else -120.0
    noise_values = [
        float(value)
        for value in (
            getattr(left, "noise_floor_dbfs", None),
            getattr(right, "noise_floor_dbfs", None),
        )
        if value is not None
    ]
    noise_floor = min(noise_values) if noise_values else None
    return SpeechSegment(
        audio_data=audio_data,
        sample_rate=sample_rate,
        duration_seconds=duration,
        voice_duration_seconds=voice_duration,
        block_count=block_count,
        voice_blocks=voice_blocks,
        peak_rms_dbfs=peak,
        energy_threshold_dbfs=gate,
        noise_floor_dbfs=noise_floor,
        reason=f"{reason}:{getattr(left, 'reason', '')}+{getattr(right, 'reason', '')}",
        vad_voice_blocks=vad_voice_blocks,
        energy_voice_blocks=energy_voice_blocks,
        vad_confidence=vad_voice_blocks / max(1, block_count),
        activity_source="vad" if vad_voice_blocks else ("energy" if energy_voice_blocks else "unknown"),
    )


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value or "segment"))
    return cleaned.strip("._")[:80] or "segment"


def _format_metric(value, scale: int = 10) -> str:
    try:
        return str(int(round(float(value) * scale)))
    except Exception:
        return "na"


def _normalize_pipeline_language(value: str) -> str:
    value = (value or "").strip().lower()
    aliases = {
        "english": "en",
        "eng": "en",
        "zh-cn": "zh",
        "zh-tw": "zh",
        "chinese": "zh",
        "cmn": "zh",
        "yue": "zh",
    }
    return aliases.get(value, value if value in {"en", "zh"} else "")


def _debug_reason(work_item: SpeechWorkItem, fallback: str) -> str:
    labels = "_".join(work_item.candidate_labels or ())
    reason = work_item.candidate_reason or fallback
    return f"{labels}_{reason}" if labels else reason


def _trace_whisper_model_size(whisper) -> str:
    active = str(getattr(whisper, "active_model_size", "") or "").strip()
    if active:
        return active
    return str(getattr(whisper, "model_size", "") or "").strip()


def _trace_cpu_threads(whisper) -> int:
    if not whisper:
        return 0
    if not bool(getattr(whisper, "auto_cpu_threads", True)):
        try:
            return max(1, min(8, int(getattr(whisper, "cpu_threads", 2) or 2)))
        except Exception:
            return 2

    logical_cpus = os.cpu_count() or 4
    if logical_cpus >= 16:
        return 6
    if logical_cpus >= 8:
        return 4
    if logical_cpus >= 4:
        return 3
    return 2
