import queue
import threading
import time
from collections import deque

from loguru import logger

from voxgo.audio.capture import SpeechSegment, should_drop_speech_segment
from voxgo.asr.whisper_engine import should_drop_transcription_result
from voxgo.runtime.events import TranscriptReady
from voxgo.runtime.work_items import LatencyTrace, SpeechWorkItem


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
        self._processing_lock = threading.Lock()
        self._queue = queue.Queue(maxsize=2)
        self._stop_token = object()
        self._worker_thread = None
        self._recent_transcripts = deque(maxlen=12)

    def remember_transcript(self, text: str):
        self._recent_transcripts.append((time.time(), text))

    def on_speech_detected(self, speech_segment):
        if self._is_paused() or not self._is_running():
            return
        config = self._config_getter()
        segment = self._coerce_speech_segment(speech_segment, config.audio.sample_rate)
        self._stats["speech_detected"] += 1
        drop_reason = should_drop_speech_segment(segment, config.audio)
        if drop_reason:
            self._stats["filtered_speech"] += 1
            logger.info(
                "丢弃疑似误触发音频片段: {}，voice={:.2f}s, total={:.2f}s, peak={:.1f} dBFS, gate={:.1f} dBFS, cut={}",
                drop_reason,
                segment.voice_duration_seconds,
                segment.duration_seconds,
                segment.peak_rms_dbfs,
                segment.energy_threshold_dbfs,
                segment.reason,
            )
            return
        now = time.time()
        work_item = SpeechWorkItem(
            segment=segment,
            trace=LatencyTrace(item_id="", speech_detected_at=now, queued_at=now),
        )
        try:
            self._queue.put_nowait(work_item)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._stats["dropped_speech"] += 1
                logger.warning("语音处理队列已满，丢弃最旧片段以保持实时性")
            except queue.Empty:
                pass
            try:
                work_item.trace.queued_at = time.time()
                self._queue.put_nowait(work_item)
            except queue.Full:
                self._stats["dropped_speech"] += 1
                logger.warning("语音处理队列仍然已满，丢弃当前片段")

    def start(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_thread = threading.Thread(
            target=self._worker,
            name="speech-worker",
            daemon=True,
        )
        self._worker_thread.start()
        logger.info("语音处理队列已启动")

    def stop(self) -> bool:
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
            logger.warning("语音处理线程仍在结束中，跳过模型清理以避免资源释放冲突")
            return False
        self._worker_thread = None
        return True

    def _worker(self):
        while True:
            work_item = self._queue.get()
            if work_item is self._stop_token:
                return
            self._process(work_item)

    def _process(self, work_item):
        try:
            if self._is_paused() or not self._is_running():
                return
            config = self._config_getter()
            segment, trace = self._normalize_work_item(work_item, config.audio.sample_rate)
            drop_reason = should_drop_speech_segment(segment, config.audio)
            if drop_reason:
                self._stats["filtered_speech"] += 1
                logger.info("处理前丢弃疑似误触发音频片段: {}", drop_reason)
                return
            trace.dequeued_at = time.time()
            t0 = time.time()
            trace.transcription_started_at = t0
            logger.info(
                "开始识别语音片段: voice={:.2f}s, total={:.2f}s, peak={:.1f} dBFS, gate={:.1f} dBFS, cut={}",
                segment.voice_duration_seconds,
                segment.duration_seconds,
                segment.peak_rms_dbfs,
                segment.energy_threshold_dbfs,
                segment.reason,
            )
            recognizer = self._recognizer_getter()
            with self._processing_lock:
                result = recognizer.transcribe_audio_bytes_with_language(
                    segment.audio_data,
                    sample_rate=segment.sample_rate or config.audio.sample_rate,
                )
            trace.transcription_finished_at = time.time()
            text = result.text
            if not text or len(text.strip()) < 2:
                self._stats["filtered_speech"] += 1
                logger.info(
                    "识别结果为空或过短: text_len={}, lang={}, prob={:.2f}, voice={:.2f}s, total={:.2f}s, peak={:.1f} dBFS, gate={:.1f} dBFS",
                    len(text.strip()) if text else 0,
                    result.language or "unknown",
                    result.language_probability,
                    segment.voice_duration_seconds,
                    segment.duration_seconds,
                    segment.peak_rms_dbfs,
                    segment.energy_threshold_dbfs,
                )
                return
            drop_reason = should_drop_transcription_result(
                result,
                expected_language=config.whisper.language,
                recent_texts=self._recent_transcript_texts(),
                config=config.whisper,
            )
            if drop_reason:
                self._stats["filtered_speech"] += 1
                logger.info(
                    "丢弃疑似误识别文本: {}，text={}, lang={}, prob={:.2f}",
                    drop_reason,
                    text[:120],
                    result.language or "unknown",
                    result.language_probability,
                )
                return
            logger.info(
                "[识别] {} (lang={}, prob={:.2f}, {:.1f}s)",
                text[:80],
                result.language or "unknown",
                result.language_probability,
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
                )
            )
        except Exception as exc:
            self._stats["errors"] += 1
            logger.exception("处理失败: {}", exc)
            self._notify_user("处理失败", str(exc), "错误")

    def _normalize_work_item(self, work_item, sample_rate: int):
        if isinstance(work_item, SpeechWorkItem):
            return work_item.segment, work_item.trace
        if isinstance(work_item, SpeechSegment):
            now = time.time()
            return work_item, LatencyTrace(item_id="", speech_detected_at=now, queued_at=now)
        now = time.time()
        return (
            self._coerce_speech_segment(work_item, sample_rate),
            LatencyTrace(item_id="", speech_detected_at=now, queued_at=now),
        )

    def _recent_transcript_texts(self, now: float = None) -> list:
        now = now or time.time()
        recent = [
            (created_at, text)
            for created_at, text in self._recent_transcripts
            if now - created_at <= 8.0
        ]
        self._recent_transcripts = deque(recent, maxlen=12)
        return [text for _, text in recent]

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
            reason="兼容旧音频字节",
        )
