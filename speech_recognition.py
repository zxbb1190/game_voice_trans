"""
语音识别模块
使用 faster-whisper 进行本地语音转文字
"""

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from faster_whisper import WhisperModel
from loguru import logger


@dataclass
class WhisperConfig:
    model_size: str = "small"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str = "en"
    beam_size: int = 5
    vad_filter: bool = True
    vad_parameters: dict = None
    model_dir: str = ".models"
    local_files_only: bool = False
    initial_prompt: str = ""
    condition_on_previous_text: bool = False
    temperature: float = 0.0
    no_speech_threshold: float = 0.6
    log_prob_threshold: float = -1.0
    compression_ratio_threshold: float = 2.4


@dataclass
class TranscriptionResult:
    text: str
    language: str = ""
    language_probability: float = 0.0


class SpeechRecognizer:
    """Whisper 语音识别器"""

    def __init__(self, config: WhisperConfig = None):
        self.config = config or WhisperConfig()
        self._model: Optional[WhisperModel] = None
        self._initialized = False
        self._model_dir = Path(self.config.model_dir)
        if not self._model_dir.is_absolute():
            self._model_dir = Path(__file__).parent / self._model_dir
        logger.info(f"Whisper 模型目录: {self._model_dir}")

    def initialize(self):
        """初始化 Whisper 模型"""
        if self._initialized:
            return

        logger.info(
            "加载 Whisper 模型: {} (device={}, compute_type={})",
            self.config.model_size,
            self.config.device,
            self.config.compute_type
        )
        self._model_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._model = WhisperModel(
                self.config.model_size,
                device=self.config.device,
                compute_type=self.config.compute_type,
                download_root=str(self._model_dir),
                local_files_only=self.config.local_files_only
            )
            self._initialized = True
            logger.info("Whisper 模型加载完成")
        except Exception as e:
            logger.exception(f"加载 Whisper 模型失败: {e}")
            logger.warning("尝试降级到 CPU 模式")
            self.config.device = "cpu"
            self.config.compute_type = "int8"
            self._model = WhisperModel(
                self.config.model_size,
                device=self.config.device,
                compute_type=self.config.compute_type,
                download_root=str(self._model_dir),
                local_files_only=self.config.local_files_only
            )
            self._initialized = True
            logger.info("Whisper 模型加载完成 (CPU 模式)")

    def transcribe_audio_bytes(self, audio_bytes: bytes, sample_rate: int = 44100) -> str:
        """将音频字节转录为文字"""
        return self.transcribe_audio_bytes_with_language(audio_bytes, sample_rate).text

    def transcribe_audio_bytes_with_language(self, audio_bytes: bytes, sample_rate: int = 44100) -> TranscriptionResult:
        """将音频字节转录为文字，并返回 Whisper 检测到的语言。"""
        if not self._initialized:
            self.initialize()

        # 将字节转换为 numpy 数组
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # 重采样到 16kHz（Whisper 要求）
        if sample_rate != 16000:
            target_len = int(len(audio_array) * 16000 / sample_rate)
            # 简单线性插值重采样，并确保 float32 类型
            x_old = np.linspace(0, 1, len(audio_array), dtype=np.float32)
            x_new = np.linspace(0, 1, target_len, dtype=np.float32)
            audio_array = np.interp(x_new, x_old, audio_array).astype(np.float32)
            logger.debug(f'重采样: {sample_rate}Hz -> 16000Hz, {len(audio_array)} -> {target_len} 点')

        # 转录
        start_time = time.time()
        language = None if self.config.language in (None, "", "auto") else self.config.language
        initial_prompt = self.config.initial_prompt or None
        segments, info = self._model.transcribe(
            audio_array,
            language=language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            vad_parameters=self.config.vad_parameters,
            initial_prompt=initial_prompt,
            condition_on_previous_text=self.config.condition_on_previous_text,
            temperature=self.config.temperature,
            no_speech_threshold=self.config.no_speech_threshold,
            log_prob_threshold=self.config.log_prob_threshold,
            compression_ratio_threshold=self.config.compression_ratio_threshold
        )

        # 合并所有片段
        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())

        full_text = " ".join(text_parts)
        elapsed = time.time() - start_time

        detected_language = getattr(info, "language", "") or ""
        language_probability = float(getattr(info, "language_probability", 0.0) or 0.0)
        logger.debug(
            "转录完成: {} 字符, language={}, prob={:.2f}, 耗时: {:.2f}s",
            len(full_text),
            detected_language,
            language_probability,
            elapsed
        )
        return TranscriptionResult(full_text, detected_language, language_probability)

    def transcribe_audio_file(self, audio_file: str) -> str:
        """转录音频文件"""
        if not self._initialized:
            self.initialize()

        start_time = time.time()
        language = None if self.config.language in (None, "", "auto") else self.config.language
        initial_prompt = self.config.initial_prompt or None
        segments, info = self._model.transcribe(
            audio_file,
            language=language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            initial_prompt=initial_prompt,
            condition_on_previous_text=self.config.condition_on_previous_text,
            temperature=self.config.temperature,
            no_speech_threshold=self.config.no_speech_threshold,
            log_prob_threshold=self.config.log_prob_threshold,
            compression_ratio_threshold=self.config.compression_ratio_threshold
        )

        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())

        full_text = " ".join(text_parts)
        elapsed = time.time() - start_time

        logger.info(f"文件转录完成: {len(full_text)} 字符, 耗时: {elapsed:.2f}s")
        return full_text

    async def transcribe_async(self, audio_bytes: bytes, sample_rate: int = 44100) -> str:
        """异步转录"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.transcribe_audio_bytes, audio_bytes, sample_rate
        )

    def get_model_info(self) -> dict:
        """获取模型信息"""
        if not self._initialized:
            return {"status": "not_initialized"}
        return {
            "model_size": self.config.model_size,
            "device": self.config.device,
            "compute_type": self.config.compute_type,
            "language": self.config.language
        }

    def cleanup(self):
        """清理资源"""
        self._model = None
        self._initialized = False


# VAD 参数配置
DEFAULT_VAD_PARAMS = {
    "threshold": 0.5,
    "min_speech_duration_ms": 250,
    "max_speech_duration_s": float("inf"),
    "min_silence_duration_ms": 2000,
    "window_size_samples": 1024,
    "speech_pad_ms": 400
}
