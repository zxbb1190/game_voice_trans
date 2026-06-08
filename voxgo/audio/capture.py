"""
音频捕获模块
使用 WASAPI Loopback 捕获系统音频输出
"""

import queue
import threading
import time
import wave
from dataclasses import dataclass
from typing import Optional, Callable

import numpy as np
try:
    import pyaudiowpatch as pyaudio
    HAS_WASAPI_LOOPBACK = True
except ImportError:
    import pyaudio
    HAS_WASAPI_LOOPBACK = False
from loguru import logger


LATENCY_MODE_FAST = "fast"
LATENCY_MODE_BALANCED = "balanced"
LATENCY_MODE_ACCURATE = "accurate"
LATENCY_MODE_CUSTOM = "custom"
LATENCY_MODES = {LATENCY_MODE_FAST, LATENCY_MODE_BALANCED, LATENCY_MODE_ACCURATE, LATENCY_MODE_CUSTOM}
SAFE_MAX_SPEECH_THRESHOLD_DBFS = -30.0
LATENCY_PRESET_MATCH_KEYS = (
    "chunk_duration_ms",
    "speech_threshold_blocks",
    "silence_limit_blocks",
    "max_buffer_blocks",
    "max_speech_seconds",
    "pre_roll_ms",
    "speech_idle_timeout_ms",
)

AUDIO_LATENCY_PRESETS = {
    LATENCY_MODE_FAST: {
        "chunk_duration_ms": 150,
        "speech_threshold_blocks": 2,
        "silence_limit_blocks": 3,
        "max_buffer_blocks": 100,
        "max_speech_seconds": 4.0,
        "pre_roll_ms": 350,
        "speech_idle_timeout_ms": 500,
        "min_segment_seconds": 0.30,
        "min_segment_peak_margin_db": 1.0,
    },
    LATENCY_MODE_BALANCED: {
        "chunk_duration_ms": 220,
        "speech_threshold_blocks": 2,
        "silence_limit_blocks": 4,
        "max_buffer_blocks": 120,
        "max_speech_seconds": 6.0,
        "pre_roll_ms": 450,
        "speech_idle_timeout_ms": 650,
        "min_segment_seconds": 0.35,
        "min_segment_peak_margin_db": 1.5,
    },
    LATENCY_MODE_ACCURATE: {
        "chunk_duration_ms": 300,
        "speech_threshold_blocks": 2,
        "silence_limit_blocks": 5,
        "max_buffer_blocks": 120,
        "max_speech_seconds": 8.0,
        "pre_roll_ms": 600,
        "speech_idle_timeout_ms": 900,
        "min_segment_seconds": 0.45,
        "min_segment_peak_margin_db": 2.0,
    },
}


def normalize_latency_mode(value: str) -> str:
    normalized = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": LATENCY_MODE_BALANCED,
        "default": LATENCY_MODE_BALANCED,
        "normal": LATENCY_MODE_BALANCED,
        "standard": LATENCY_MODE_BALANCED,
        "balanced": LATENCY_MODE_BALANCED,
        "balance": LATENCY_MODE_BALANCED,
        "均衡": LATENCY_MODE_BALANCED,
        "平衡": LATENCY_MODE_BALANCED,
        "fast": LATENCY_MODE_FAST,
        "quick": LATENCY_MODE_FAST,
        "speed": LATENCY_MODE_FAST,
        "ultra": LATENCY_MODE_FAST,
        "极速": LATENCY_MODE_FAST,
        "極速": LATENCY_MODE_FAST,
        "低延迟": LATENCY_MODE_FAST,
        "低延時": LATENCY_MODE_FAST,
        "low": LATENCY_MODE_FAST,
        "low_latency": LATENCY_MODE_FAST,
        "accurate": LATENCY_MODE_ACCURATE,
        "accuracy": LATENCY_MODE_ACCURATE,
        "quality": LATENCY_MODE_ACCURATE,
        "precise": LATENCY_MODE_ACCURATE,
        "准确": LATENCY_MODE_ACCURATE,
        "準確": LATENCY_MODE_ACCURATE,
        "高准确": LATENCY_MODE_ACCURATE,
        "高準確": LATENCY_MODE_ACCURATE,
        "custom": LATENCY_MODE_CUSTOM,
        "manual": LATENCY_MODE_CUSTOM,
        "自定义": LATENCY_MODE_CUSTOM,
        "自定義": LATENCY_MODE_CUSTOM,
    }
    return aliases.get(normalized, normalized if normalized in LATENCY_MODES else LATENCY_MODE_BALANCED)


def _values_match(left, right) -> bool:
    try:
        return abs(float(left) - float(right)) < 0.001
    except Exception:
        return left == right


def infer_latency_mode(config) -> str:
    """Infer whether existing tuning matches a built-in latency preset."""
    configured_raw = getattr(config, "latency_mode", "")
    configured = normalize_latency_mode(configured_raw)
    if configured_raw and configured in LATENCY_MODES:
        return configured
    for mode, preset in AUDIO_LATENCY_PRESETS.items():
        if all(_values_match(getattr(config, key, None), preset[key]) for key in LATENCY_PRESET_MATCH_KEYS):
            return mode
    return LATENCY_MODE_CUSTOM


def apply_audio_latency_preset(config) -> str:
    """Apply built-in latency tuning in-place and return the normalized mode."""
    mode = normalize_latency_mode(getattr(config, "latency_mode", LATENCY_MODE_BALANCED))
    setattr(config, "latency_mode", mode)
    preset = AUDIO_LATENCY_PRESETS.get(mode)
    if not preset:
        return mode
    for key, value in preset.items():
        if hasattr(config, key):
            setattr(config, key, value)
    return mode


@dataclass
class AudioConfig:
    latency_mode: str = ""
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration_ms: int = 220
    silence_threshold: float = -40.0
    speech_threshold_blocks: int = 2
    silence_limit_blocks: int = 4
    max_buffer_blocks: int = 120
    max_speech_seconds: float = 6.0
    pre_roll_ms: int = 450
    speech_idle_timeout_ms: int = 650
    soft_silence_margin_db: float = 10.0
    soft_silence_gate_margin_db: float = 5.0
    noise_calibration_seconds: float = 2.0
    noise_margin_db: float = 7.0
    min_speech_threshold: float = -45.0
    max_speech_threshold: float = SAFE_MAX_SPEECH_THRESHOLD_DBFS
    min_segment_seconds: float = 0.35
    min_segment_peak_margin_db: float = 1.5
    input_device_index: Optional[int] = None
    input_device_name: str = ""
    input_device_id: str = ""
    format: int = pyaudio.paInt16
    initial_noise_floor_dbfs: Optional[float] = None
    initial_energy_threshold_dbfs: Optional[float] = None


@dataclass
class SpeechSegment:
    audio_data: bytes
    sample_rate: int
    duration_seconds: float
    voice_duration_seconds: float
    block_count: int
    voice_blocks: int
    peak_rms_dbfs: float
    energy_threshold_dbfs: float
    noise_floor_dbfs: Optional[float]
    reason: str


def _device_name(info) -> str:
    return str(info.get("name", "") or "").strip()


def _normalize_device_name(name: str) -> str:
    return (name or "").strip().casefold()


def _is_loopback_info(info, is_loopback=False) -> bool:
    return bool(is_loopback or info.get("isLoopbackDevice"))


def stable_device_id(info, is_loopback=False) -> str:
    """Return a stable-ish device identity that does not depend on list position."""
    device_type = "loopback" if _is_loopback_info(info, is_loopback) else "input"
    host_api = info.get("hostApi", "")
    name = _normalize_device_name(_device_name(info))
    return f"{device_type}:{host_api}:{name}"


def calculate_rms_dbfs(audio_np: np.ndarray) -> float:
    """Calculate RMS in dBFS for int16 PCM samples."""
    if len(audio_np) == 0:
        return -120.0
    normalized = audio_np.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(normalized ** 2)))
    return 20 * np.log10(max(rms, 1e-10))


def _float_or_default(value, default: float) -> float:
    return float(default if value is None else value)


def should_drop_speech_segment(segment: SpeechSegment, config: AudioConfig) -> str:
    """Return a human-readable drop reason for obvious non-speech triggers."""
    if not segment or not getattr(segment, "audio_data", b""):
        return "空音频片段"

    has_capture_metadata = bool(getattr(segment, "block_count", 0) or getattr(segment, "voice_blocks", 0))
    if not has_capture_metadata:
        return ""

    try:
        min_seconds = max(0.0, float(getattr(config, "min_segment_seconds", 0.45) or 0.0))
    except Exception:
        min_seconds = 0.45
    voice_seconds = float(getattr(segment, "voice_duration_seconds", 0.0) or 0.0)
    if voice_seconds <= 0:
        voice_seconds = float(getattr(segment, "duration_seconds", 0.0) or 0.0)
    if min_seconds > 0 and voice_seconds < min_seconds:
        return f"语音活跃时长 {voice_seconds:.2f}s < {min_seconds:.2f}s"

    try:
        min_peak_margin = max(0.0, float(getattr(config, "min_segment_peak_margin_db", 2.0) or 0.0))
    except Exception:
        min_peak_margin = 2.0
    peak_margin = float(getattr(segment, "peak_rms_dbfs", -120.0)) - float(
        getattr(segment, "energy_threshold_dbfs", -120.0)
    )
    if min_peak_margin > 0 and peak_margin < min_peak_margin:
        return f"峰值余量 {peak_margin:.1f} dB < {min_peak_margin:.1f} dB"

    return ""


def list_input_devices():
    """Return system-audio loopback devices first, then normal inputs."""
    audio = pyaudio.PyAudio()
    devices = []
    seen_indexes = set()

    def append_device(info, is_loopback=False):
        index = int(info.get("index"))
        if index in seen_indexes:
            return
        channels = int(info.get("maxInputChannels", 0) or 0)
        if channels <= 0:
            return
        seen_indexes.add(index)
        devices.append({
            "index": index,
            "name": info.get("name", ""),
            "device_id": stable_device_id(info, is_loopback),
            "host_api": int(info.get("hostApi", -1)),
            "channels": channels,
            "sample_rate": int(float(info.get("defaultSampleRate", 0) or 0)),
            "is_loopback": bool(is_loopback or info.get("isLoopbackDevice")),
        })

    try:
        if HAS_WASAPI_LOOPBACK and hasattr(audio, "get_loopback_device_info_generator"):
            for info in audio.get_loopback_device_info_generator():
                append_device(info, is_loopback=True)

        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            append_device(info)
    finally:
        audio.terminate()
    return devices


class AudioLevelMonitor:
    """Lightweight live level monitor for setup and diagnostics."""

    def __init__(self, config: AudioConfig = None, on_level: Optional[Callable[[dict], None]] = None):
        self.config = config or AudioConfig()
        self._on_level = on_level
        self._selector: Optional[SystemAudioCapture] = None
        self._stream: Optional[pyaudio.Stream] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stream_channels = 1
        self._sample_rate = self.config.sample_rate
        self.selected_device = None
        self.peak_dbfs = -120.0

    def start(self):
        if self._running:
            return
        self._selector = SystemAudioCapture(self.config)
        device_index = self._selector.find_loopback_device()
        if device_index is None:
            raise RuntimeError("未找到可用的音频输入设备")

        self._stream_channels = self._selector._stream_channels
        self._sample_rate = self._selector._capture_sample_rate
        self.selected_device = self._selector.selected_device
        frames_per_buffer = max(256, int(self._sample_rate * 0.05))
        self._stream = self._selector._audio.open(
            format=self.config.format,
            channels=self._stream_channels,
            rate=self._sample_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=frames_per_buffer,
        )
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, name="audio-level-monitor", daemon=True)
        self._thread.start()
        logger.info("音频测试已启动: {}Hz/{}ch", self._sample_rate, self._stream_channels)

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None
        if self._stream:
            try:
                self._stream.stop_stream()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._selector:
            try:
                self._selector._audio.terminate()
            except Exception:
                pass
            self._selector = None
        logger.info("音频测试已停止")

    def _read_loop(self):
        frames_per_buffer = max(256, int(self._sample_rate * 0.05))
        while self._running and self._stream:
            try:
                data = self._stream.read(frames_per_buffer, exception_on_overflow=False)
                samples = np.frombuffer(data, dtype=np.int16)
                if self._stream_channels > 1:
                    try:
                        samples = samples.reshape(-1, self._stream_channels).mean(axis=1).astype(np.int16)
                    except ValueError:
                        pass
                rms = calculate_rms_dbfs(samples)
                self.peak_dbfs = max(self.peak_dbfs, rms)
                detected = rms > float(getattr(self.config, "silence_threshold", -40.0) or -40.0)
                payload = {
                    "rms_dbfs": rms,
                    "peak_dbfs": self.peak_dbfs,
                    "detected": detected,
                    "sample_rate": self._sample_rate,
                    "channels": self._stream_channels,
                    "device": self.selected_device,
                }
                if self._on_level:
                    self._on_level(payload)
            except Exception as e:
                if self._running and self._on_level:
                    self._on_level({"error": str(e), "device": self.selected_device})
                break
        self._running = False


class SystemAudioCapture:
    """WASAPI Loopback 音频捕获"""

    def __init__(self, config: AudioConfig = None):
        self.config = config or AudioConfig()
        self._audio = pyaudio.PyAudio()
        self._stream: Optional[pyaudio.Stream] = None
        self._running = False
        self._audio_queue = queue.Queue()
        self._on_speech_callback: Optional[Callable] = None
        self._speech_buffer = []
        self._speech_buffer_samples = 0
        self._speech_voice_samples = 0
        self._speech_voice_blocks = 0
        self._speech_peak_rms = None
        self._last_tail_silence_reason = ""
        self._stream_channels = max(1, self.config.channels)
        self._capture_sample_rate = self.config.sample_rate
        self.selected_device = None
        self._silence_counter = 0
        self._speech_threshold = self.config.speech_threshold_blocks
        self._silence_limit = self.config.silence_limit_blocks
        configured_min_speech_threshold = _float_or_default(self.config.min_speech_threshold, -45.0)
        configured_max_speech_threshold = _float_or_default(
            self.config.max_speech_threshold,
            SAFE_MAX_SPEECH_THRESHOLD_DBFS,
        )
        if configured_min_speech_threshold > configured_max_speech_threshold:
            configured_min_speech_threshold, configured_max_speech_threshold = (
                configured_max_speech_threshold,
                configured_min_speech_threshold,
            )
        self._max_speech_threshold = min(
            configured_max_speech_threshold,
            SAFE_MAX_SPEECH_THRESHOLD_DBFS,
        )
        self._min_speech_threshold = min(configured_min_speech_threshold, self._max_speech_threshold)
        if configured_max_speech_threshold > self._max_speech_threshold + 0.1:
            logger.info(
                "语音门限上限已收紧: configured={:.1f} dBFS, effective={:.1f} dBFS",
                configured_max_speech_threshold,
                self._max_speech_threshold,
            )
        self._static_energy_threshold = self._clamp_threshold(
            _float_or_default(self.config.silence_threshold, -40.0)
        )
        self._energy_threshold = self._static_energy_threshold
        self._noise_calibration_seconds = max(
            0.0,
            _float_or_default(self.config.noise_calibration_seconds, 0.0),
        )
        self._noise_margin_db = _float_or_default(self.config.noise_margin_db, 7.0)
        self._noise_rms_values = []
        self._noise_samples = 0
        self._noise_floor = None
        self._noise_calibrated = self._noise_calibration_seconds <= 0
        initial_gate = getattr(self.config, "initial_energy_threshold_dbfs", None)
        initial_floor = getattr(self.config, "initial_noise_floor_dbfs", None)
        if initial_gate is not None:
            try:
                requested_gate = float(initial_gate)
                self._energy_threshold = self._clamp_threshold(requested_gate)
                self._noise_floor = float(initial_floor) if initial_floor is not None else None
                self._noise_calibrated = True
                gate_note = ""
                if requested_gate > self._energy_threshold + 0.1:
                    gate_note = f" (从 {requested_gate:.1f} dBFS 收紧)"
                logger.info(
                    "沿用上次背景噪声门限: noise_floor={}, speech_threshold={:.1f} dBFS{}",
                    f"{self._noise_floor:.1f} dBFS" if self._noise_floor is not None else "unknown",
                    self._energy_threshold,
                    gate_note,
                )
            except Exception:
                pass
        self._max_buffer_blocks = self.config.max_buffer_blocks
        self._max_speech_seconds = float(self.config.max_speech_seconds or 0)
        self._pre_roll_ms = max(0.0, _float_or_default(getattr(self.config, "pre_roll_ms", 600), 600.0))
        self._speech_idle_timeout_ms = max(
            0.0,
            _float_or_default(getattr(self.config, "speech_idle_timeout_ms", 900), 900.0),
        )
        self._soft_silence_margin_db = max(
            0.0,
            _float_or_default(getattr(self.config, "soft_silence_margin_db", 10.0), 10.0),
        )
        self._soft_silence_gate_margin_db = max(
            0.0,
            _float_or_default(getattr(self.config, "soft_silence_gate_margin_db", 5.0), 5.0),
        )
        self._pre_roll_buffer = []
        self._pre_roll_samples = 0
        self._last_audio_activity_at = None

    def find_loopback_device(self) -> Optional[int]:
        """Find the configured or most likely system-audio input device."""
        configured = self._configured_device_candidates()
        if configured:
            selected = self._first_usable_device(configured)
            if selected is not None:
                return selected
            logger.error("已选择的音频设备不可用，已停止自动切换到其他设备")
            return None
        if self._has_configured_device():
            logger.error("已选择的音频设备未找到，已停止自动切换到其他设备")
            return None

        candidates = self._auto_device_candidates()
        selected = self._first_usable_device(candidates)
        if selected is not None:
            return selected

        logger.error("未找到可用音频输入设备")
        return None

    def _has_configured_device(self) -> bool:
        return (
            getattr(self.config, "input_device_index", None) is not None
            or bool((getattr(self.config, "input_device_name", "") or "").strip())
            or bool((getattr(self.config, "input_device_id", "") or "").strip())
        )

    def _default_loopback_candidates(self):
        if not HAS_WASAPI_LOOPBACK:
            return []
        candidates = []

        if hasattr(self._audio, "get_default_wasapi_loopback"):
            try:
                info = self._audio.get_default_wasapi_loopback()
                candidates.append((int(info["index"]), info))
            except Exception as e:
                logger.debug("默认 WASAPI loopback 获取失败: {}", e)

        try:
            wasapi_info = self._audio.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_output_index = wasapi_info.get("defaultOutputDevice")
            if default_output_index is not None and default_output_index >= 0:
                output_info = self._audio.get_device_info_by_index(default_output_index)
                if output_info.get("isLoopbackDevice"):
                    candidates.append((int(output_info["index"]), output_info))
                elif hasattr(self._audio, "get_loopback_device_info_generator"):
                    output_name = output_info.get("name", "")
                    for loopback in self._audio.get_loopback_device_info_generator():
                        if output_name and output_name in loopback.get("name", ""):
                            candidates.append((int(loopback["index"]), loopback))
                            break
        except Exception as e:
            logger.debug("默认输出设备 loopback 匹配失败: {}", e)

        unique = []
        seen = set()
        for index, info in candidates:
            if index not in seen:
                seen.add(index)
                unique.append((index, info))
        return unique

    def _configured_device_candidates(self):
        candidates = []
        configured_index = self.config.input_device_index
        configured_name = _normalize_device_name(self.config.input_device_name)
        configured_device_id = str(getattr(self.config, "input_device_id", "") or "").strip()
        entries = self._input_device_entries()

        if configured_device_id:
            for index, info in entries:
                if stable_device_id(info) == configured_device_id:
                    candidates.append((index, info))
                    break

        if configured_name:
            for index, info in entries:
                name = _normalize_device_name(_device_name(info))
                if configured_name == name and not any(item[0] == index for item in candidates):
                    candidates.append((index, info))

        # Older settings only stored the numeric PortAudio index. Use it last,
        # and only when it still points at the same named device, to avoid
        # silently opening a neighboring device after USB/WASAPI re-enumeration.
        if configured_index is not None:
            try:
                info = self._audio.get_device_info_by_index(int(configured_index))
                current_name = _normalize_device_name(_device_name(info))
                name_matches = not configured_name or configured_name == current_name
                if int(info.get("maxInputChannels", 0) or 0) > 0 and name_matches:
                    candidates.append((int(configured_index), info))
                elif configured_name and configured_name != current_name:
                    logger.warning(
                        "已保存的音频设备索引 [{}] 当前对应 [{}]，不是 [{}]，已忽略该索引",
                        configured_index,
                        _device_name(info),
                        self.config.input_device_name,
                    )
            except Exception as e:
                logger.warning("已保存的音频设备索引不可用: {} ({})", configured_index, e)

        if configured_name:
            for index, info in entries:
                name = _normalize_device_name(_device_name(info))
                if configured_name in name:
                    if not any(item[0] == index for item in candidates):
                        candidates.append((index, info))

        return candidates

    def _input_device_entries(self):
        entries = []
        seen_indexes = set()

        def append(info):
            index = int(info.get("index"))
            if index in seen_indexes:
                return
            if int(info.get("maxInputChannels", 0) or 0) <= 0:
                return
            seen_indexes.add(index)
            entries.append((index, info))

        if HAS_WASAPI_LOOPBACK and hasattr(self._audio, "get_loopback_device_info_generator"):
            for info in self._audio.get_loopback_device_info_generator():
                append(info)

        for index in range(self._audio.get_device_count()):
            append(self._audio.get_device_info_by_index(index))

        return entries

    def _auto_device_candidates(self):
        devices = []
        for index, info in self._default_loopback_candidates():
            devices.append((0, index, info))

        if HAS_WASAPI_LOOPBACK and hasattr(self._audio, "get_loopback_device_info_generator"):
            for info in self._audio.get_loopback_device_info_generator():
                devices.append((1, int(info["index"]), info))

        preferred_keywords = [
            "立体声混音", "stereo mix", "what u hear", "wave out",
            "cable", "voicemeeter", "virtual", "loopback", "monitor",
        ]
        for index in range(self._audio.get_device_count()):
            info = self._audio.get_device_info_by_index(index)
            if int(info.get("maxInputChannels", 0) or 0) <= 0:
                continue
            name = info.get("name", "")
            lowered = name.lower()
            preferred = any(keyword in lowered for keyword in preferred_keywords)
            score = 2 if preferred else 10
            devices.append((score, index, info))

        unique = []
        seen = set()
        for score, index, info in sorted(devices, key=lambda item: (item[0], item[1])):
            if index in seen:
                continue
            seen.add(index)
            unique.append((index, info))
        return unique

    def _stream_parameter_candidates(self, info):
        channels = int(info.get("maxInputChannels", 0) or 0)
        default_rate = int(float(info.get("defaultSampleRate", 0) or self.config.sample_rate))
        is_loopback = bool(info.get("isLoopbackDevice"))

        if is_loopback:
            # WASAPI loopback devices are most reliable at their native rate/channels.
            yield max(1, min(channels, 2)), default_rate
            yield 1, default_rate
            yield 1, self.config.sample_rate
        else:
            yield max(1, min(self.config.channels, channels)), self.config.sample_rate
            yield max(1, min(channels, 2)), default_rate

    def _first_usable_device(self, candidates) -> Optional[int]:
        for idx, info in candidates:
            for channels, sample_rate in self._stream_parameter_candidates(info):
                try:
                    test_stream = self._audio.open(
                        format=self.config.format,
                        channels=channels,
                        rate=sample_rate,
                        input=True,
                        input_device_index=idx,
                        frames_per_buffer=512,
                    )
                    test_stream.close()
                    self._stream_channels = channels
                    self._capture_sample_rate = sample_rate
                    device_type = "系统声音" if info.get("isLoopbackDevice") else "输入设备"
                    self.selected_device = {
                        "index": idx,
                        "name": info.get("name", ""),
                        "device_id": stable_device_id(info),
                        "type": device_type,
                        "sample_rate": sample_rate,
                        "channels": channels,
                        "is_loopback": bool(info.get("isLoopbackDevice")),
                    }
                    logger.info(
                        "选中{} [{}]: {} ({}Hz/{}ch)",
                        device_type,
                        idx,
                        info.get("name", ""),
                        sample_rate,
                        channels,
                    )
                    return idx
                except Exception as e:
                    logger.warning(
                        "音频设备 [{}] 不可用: {} ({}Hz/{}ch, {})",
                        idx,
                        info.get("name", ""),
                        sample_rate,
                        channels,
                        e,
                    )
        return None

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """音频回调"""
        if status:
            logger.warning(f"音频状态: {status}")
        if self._stream_channels > 1:
            samples = np.frombuffer(in_data, dtype=np.int16)
            try:
                samples = samples.reshape(-1, self._stream_channels)
                mono = samples.mean(axis=1).astype(np.int16)
                in_data = mono.tobytes()
            except ValueError:
                logger.warning("音频通道数据长度异常，按原始数据处理")
        self._audio_queue.put(in_data)
        return (None, pyaudio.paContinue)

    def start(self):
        """开始音频捕获"""
        device_index = self.find_loopback_device()
        if device_index is None:
            raise RuntimeError("未找到可用的音频输入设备")

        self.config.sample_rate = self._capture_sample_rate
        self.config.channels = 1
        self._stream = self._audio.open(
            format=self.config.format,
            channels=self._stream_channels,
            rate=self._capture_sample_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=int(
                self._capture_sample_rate * self.config.chunk_duration_ms / 1000
            ),
            stream_callback=self._audio_callback,
        )

        self._running = True
        self._stream.start_stream()
        logger.info("音频捕获已启动: {}Hz/{}ch -> mono", self._capture_sample_rate, self._stream_channels)

    def stop(self):
        """停止音频捕获"""
        self._running = False
        if self._stream:
            try:
                self._stream.stop_stream()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._audio:
            self._audio.terminate()
        logger.info("音频捕获已停止")

    def set_speech_callback(self, callback: Callable):
        """设置语音检测回调"""
        self._on_speech_callback = callback

    def current_noise_gate(self):
        return self._noise_floor, self._energy_threshold, self._noise_calibrated

    def process_audio(self) -> Optional[SpeechSegment]:
        """处理音频队列，检测语音片段"""
        frames = []
        while not self._audio_queue.empty():
            try:
                data = self._audio_queue.get_nowait()
                frames.append(data)
            except queue.Empty:
                break

        if not frames:
            return self._handle_idle_timeout(time.monotonic())

        audio_data = b"".join(frames)
        self._last_audio_activity_at = time.monotonic()
        logger.debug(f'收到音频块: {len(audio_data)} 字节')

        # 基于 dBFS 能量的语音活动检测，启动后会用背景噪声自动校准阈值。
        audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
        if len(audio_np) == 0:
            return None
        rms = calculate_rms_dbfs(audio_np)
        self._update_noise_gate(rms, len(audio_np))
        is_speech = rms > self._energy_threshold
        tail_silence_reason = ""
        if self._speech_buffer and self._speech_peak_rms is not None:
            enough_voice = self._speech_voice_blocks >= self._speech_threshold
            relative_tail = (
                self._soft_silence_margin_db > 0
                and rms <= self._speech_peak_rms - self._soft_silence_margin_db
            )
            near_noise_gate = (
                self._soft_silence_gate_margin_db > 0
                and rms <= self._energy_threshold + self._soft_silence_gate_margin_db
                and rms <= self._speech_peak_rms - min(4.0, self._soft_silence_margin_db)
            )
            if enough_voice and (relative_tail or near_noise_gate):
                is_speech = False
                tail_silence_reason = "尾音下降" if relative_tail else "接近噪声门限"
                self._last_tail_silence_reason = tail_silence_reason
        logger.debug(
            'RMS: {:.1f} dBFS, 阈值: {:.1f} dBFS, 噪声: {}, 语音: {}{}',
            rms,
            self._energy_threshold,
            f'{self._noise_floor:.1f} dBFS' if self._noise_floor is not None else '校准中',
            is_speech,
            f' ({tail_silence_reason})' if tail_silence_reason else '',
        )

        if is_speech:
            if not self._speech_buffer and self._pre_roll_buffer:
                for pre_roll_data, pre_roll_samples in self._pre_roll_buffer:
                    self._speech_buffer.append(pre_roll_data)
                    self._speech_buffer_samples += pre_roll_samples
                logger.debug(
                    '补入语音开头预缓冲: {} 块, {:.0f}ms',
                    len(self._pre_roll_buffer),
                    self._pre_roll_samples * 1000 / max(1, int(self._capture_sample_rate or self.config.sample_rate or 16000)),
                )
                self._clear_pre_roll()
            self._speech_buffer.append(audio_data)
            self._speech_buffer_samples += len(audio_np)
            self._speech_voice_samples += len(audio_np)
            self._speech_voice_blocks += 1
            self._speech_peak_rms = rms if self._speech_peak_rms is None else max(self._speech_peak_rms, rms)
            self._silence_counter = 0
            self._last_tail_silence_reason = ""
            logger.debug(
                '语音缓冲区: {} 块, {:.1f}s',
                len(self._speech_buffer),
                self._buffer_duration_seconds(),
            )
            should_split, reason = self._should_force_split()
            if should_split:
                logger.warning('连续有声，强制切分语音片段: {}', reason)
                return self._emit_speech_buffer(reason)
        elif self._speech_buffer:
            self._speech_buffer.append(audio_data)
            self._speech_buffer_samples += len(audio_np)
            self._silence_counter += 1
            if not self._last_tail_silence_reason:
                self._last_tail_silence_reason = tail_silence_reason or "低于语音门限"
            logger.debug(f'静音计数: {self._silence_counter}/{self._silence_limit}')
            if (self._silence_counter >= self._silence_limit
                    and self._speech_voice_blocks < self._speech_threshold):
                logger.debug(f'丢弃过短片段: {self._speech_voice_blocks} 个语音块')
                self._reset_speech_buffer()
        else:
            self._append_pre_roll(audio_data, len(audio_np))

        # 检测语音片段结束
        if (self._silence_counter >= self._silence_limit
                and self._speech_voice_blocks >= self._speech_threshold):
            reason = "静音结束"
            if self._last_tail_silence_reason:
                reason = f"{reason}/{self._last_tail_silence_reason}"
            return self._emit_speech_buffer(reason)

        return None

    def _clamp_threshold(self, threshold: float) -> float:
        return max(self._min_speech_threshold, min(self._max_speech_threshold, float(threshold)))

    def _update_noise_gate(self, rms: float, sample_count: int):
        if self._noise_calibrated:
            return
        self._noise_rms_values.append(rms)
        self._noise_samples += sample_count
        sample_rate = max(1, int(self._capture_sample_rate or self.config.sample_rate or 16000))
        if self._noise_samples < self._noise_calibration_seconds * sample_rate:
            return

        # Use a low percentile so a brief voice/game burst during startup does
        # not raise the floor enough to swallow quiet speech.
        self._noise_floor = float(np.percentile(self._noise_rms_values, 20))
        dynamic_threshold = self._noise_floor + self._noise_margin_db
        self._energy_threshold = self._clamp_threshold(dynamic_threshold)
        self._noise_calibrated = True
        gate_note = ""
        if dynamic_threshold > self._energy_threshold + 0.1:
            gate_note = f" (动态值 {dynamic_threshold:.1f} dBFS 已收紧)"
        logger.info(
            "背景噪声校准完成: noise_floor={:.1f} dBFS, speech_threshold={:.1f} dBFS{}",
            self._noise_floor,
            self._energy_threshold,
            gate_note,
        )

    def _append_pre_roll(self, audio_data: bytes, sample_count: int):
        if self._pre_roll_ms <= 0 or sample_count <= 0:
            return
        self._pre_roll_buffer.append((audio_data, sample_count))
        self._pre_roll_samples += sample_count
        sample_rate = max(1, int(self._capture_sample_rate or self.config.sample_rate or 16000))
        max_samples = max(1, int(sample_rate * self._pre_roll_ms / 1000))
        while len(self._pre_roll_buffer) > 1 and self._pre_roll_samples > max_samples:
            _, removed_samples = self._pre_roll_buffer.pop(0)
            self._pre_roll_samples -= removed_samples

    def _clear_pre_roll(self):
        self._pre_roll_buffer.clear()
        self._pre_roll_samples = 0

    def _handle_idle_timeout(self, now: float) -> Optional[bytes]:
        if self._last_audio_activity_at is None:
            return None

        idle_ms = (now - self._last_audio_activity_at) * 1000
        if not self._speech_buffer:
            if self._pre_roll_buffer and idle_ms >= max(self._pre_roll_ms, self._speech_idle_timeout_ms):
                self._clear_pre_roll()
            return None

        if self._speech_idle_timeout_ms <= 0 or idle_ms < self._speech_idle_timeout_ms:
            return None

        if self._speech_voice_blocks >= self._speech_threshold:
            return self._emit_speech_buffer(f"空闲 {idle_ms:.0f}ms")

        logger.debug("空闲超时，丢弃过短片段: {} 个语音块", self._speech_voice_blocks)
        self._reset_speech_buffer()
        return None

    def _buffer_duration_seconds(self) -> float:
        sample_rate = max(1, int(self.config.sample_rate or self._capture_sample_rate or 16000))
        return self._speech_buffer_samples / sample_rate

    def _should_force_split(self):
        if self._max_speech_seconds > 0 and self._buffer_duration_seconds() >= self._max_speech_seconds:
            return True, f"达到最长 {self._max_speech_seconds:g}s"
        if self._max_buffer_blocks > 0 and len(self._speech_buffer) >= self._max_buffer_blocks:
            return True, f"达到块数上限 {self._max_buffer_blocks}"
        return False, ""

    def _reset_speech_buffer(self):
        self._speech_buffer.clear()
        self._speech_buffer_samples = 0
        self._speech_voice_samples = 0
        self._speech_voice_blocks = 0
        self._speech_peak_rms = None
        self._last_tail_silence_reason = ""
        self._silence_counter = 0

    def _emit_speech_buffer(self, reason: str) -> Optional[SpeechSegment]:
        speech_data = b"".join(self._speech_buffer)
        sample_rate = max(1, int(self.config.sample_rate or self._capture_sample_rate or 16000))
        duration = self._buffer_duration_seconds()
        voice_duration = self._speech_voice_samples / sample_rate
        block_count = len(self._speech_buffer)
        voice_blocks = self._speech_voice_blocks
        peak_rms = float(self._speech_peak_rms if self._speech_peak_rms is not None else -120.0)
        segment = SpeechSegment(
            audio_data=speech_data,
            sample_rate=sample_rate,
            duration_seconds=duration,
            voice_duration_seconds=voice_duration,
            block_count=block_count,
            voice_blocks=voice_blocks,
            peak_rms_dbfs=peak_rms,
            energy_threshold_dbfs=float(self._energy_threshold),
            noise_floor_dbfs=self._noise_floor,
            reason=reason,
        )
        logger.info(
            '检测到语音片段: {} 字节, {:.1f}s/{:.1f}s 语音, {} 块/{} 语音块, peak={:.1f} dBFS, gate={:.1f} dBFS ({})',
            len(speech_data),
            duration,
            voice_duration,
            block_count,
            voice_blocks,
            peak_rms,
            self._energy_threshold,
            reason,
        )
        self._reset_speech_buffer()

        if self._on_speech_callback:
            self._on_speech_callback(segment)

        return segment

    def save_audio(self, data: bytes, filepath: str):
        """保存音频到文件"""
        with wave.open(filepath, "wb") as wf:
            wf.setnchannels(self.config.channels)
            wf.setsampwidth(self._audio.get_sample_size(self.config.format))
            wf.setframerate(self.config.sample_rate)
            wf.writeframes(data)
