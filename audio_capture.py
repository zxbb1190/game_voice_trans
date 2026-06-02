"""
音频捕获模块
使用 WASAPI Loopback 捕获系统音频输出
"""

import queue
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


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration_ms: int = 30
    silence_threshold: float = -40.0
    speech_threshold_blocks: int = 8
    silence_limit_blocks: int = 20
    max_buffer_blocks: int = 500
    max_speech_seconds: float = 8.0
    input_device_index: Optional[int] = None
    input_device_name: str = ""
    input_device_id: str = ""
    format: int = pyaudio.paInt16


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
        self._stream_channels = max(1, self.config.channels)
        self._capture_sample_rate = self.config.sample_rate
        self.selected_device = None
        self._silence_counter = 0
        self._speech_threshold = self.config.speech_threshold_blocks
        self._silence_limit = self.config.silence_limit_blocks
        self._energy_threshold = self.config.silence_threshold
        self._max_buffer_blocks = self.config.max_buffer_blocks
        self._max_speech_seconds = float(self.config.max_speech_seconds or 0)

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

    def process_audio(self) -> Optional[bytes]:
        """处理音频队列，检测语音片段"""
        frames = []
        while not self._audio_queue.empty():
            try:
                data = self._audio_queue.get_nowait()
                frames.append(data)
            except queue.Empty:
                break

        if not frames:
            return None

        audio_data = b"".join(frames)
        logger.debug(f'收到音频块: {len(audio_data)} 字节')

        # 基于能量的语音活动检测（固定阈值高于视频背景音）
        audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
        if len(audio_np) == 0:
            return None
        rms = 20 * np.log10(np.sqrt(np.mean(audio_np ** 2)) + 1e-10)
        is_speech = rms > self._energy_threshold
        logger.debug(f'RMS: {rms:.1f} dB, 阈值: {self._energy_threshold}, 语音: {is_speech}')

        if is_speech:
            self._speech_buffer.append(audio_data)
            self._speech_buffer_samples += len(audio_np)
            self._silence_counter = 0
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
            self._silence_counter += 1
            logger.debug(f'静音计数: {self._silence_counter}/{self._silence_limit}')
            if (self._silence_counter >= self._silence_limit
                    and len(self._speech_buffer) < self._speech_threshold):
                logger.debug(f'丢弃过短片段: {len(self._speech_buffer)} 块')
                self._reset_speech_buffer()

        # 检测语音片段结束
        if (self._silence_counter >= self._silence_limit
                and len(self._speech_buffer) >= self._speech_threshold):
            return self._emit_speech_buffer("静音结束")

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
        self._silence_counter = 0

    def _emit_speech_buffer(self, reason: str) -> Optional[bytes]:
        speech_data = b"".join(self._speech_buffer)
        duration = self._buffer_duration_seconds()
        block_count = len(self._speech_buffer)
        logger.info(
            '检测到语音片段: {} 字节, {:.1f}s, {} 块 ({})',
            len(speech_data),
            duration,
            block_count,
            reason,
        )
        self._reset_speech_buffer()

        if self._on_speech_callback:
            self._on_speech_callback(speech_data)

        return speech_data

    def save_audio(self, data: bytes, filepath: str):
        """保存音频到文件"""
        with wave.open(filepath, "wb") as wf:
            wf.setnchannels(self.config.channels)
            wf.setsampwidth(self._audio.get_sample_size(self.config.format))
            wf.setframerate(self.config.sample_rate)
            wf.writeframes(data)
