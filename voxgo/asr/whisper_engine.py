"""
语音识别模块
使用 faster-whisper 进行本地语音转文字
"""

import asyncio
import gc
import inspect
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import numpy as np
import soxr
from faster_whisper import WhisperModel
try:
    from faster_whisper.utils import _MODELS as FASTER_WHISPER_MODEL_REPOS
except Exception:
    FASTER_WHISPER_MODEL_REPOS = {}
try:
    from faster_whisper.vad import VadOptions
except Exception:
    VadOptions = None
from loguru import logger

from voxgo.app_info import USER_AGENT


PROJECT_ROOT = Path(__file__).resolve().parents[2]


GENERAL_INITIAL_PROMPT = (
    "以下是实时语音字幕，内容可能来自 PC 游戏、Discord 语音、直播、视频、网页、"
    "广告或会议。请准确转写中文、英文以及中英文混杂内容；保留品牌名、产品名、"
    "人名、地名、游戏术语、应用名、文件格式、字母缩写和数字，例如 Speechify、"
    "Discord、Steam、PDFs、Google Docs、OpenAI、GG、FPS。"
)

GAME_INITIAL_PROMPT = (
    "以下是游戏语音聊天，可能包含中文、英文以及中英文混杂。请准确保留人名、"
    "地名、游戏术语、技能名、枪械名、英雄名、地图点位和字母缩写，例如 Discord、"
    "Steam、Valorant、Apex、GG、NT、WP、FPS。"
)

PROMPT_PROFILES = {
    "none": None,
    "off": None,
    "general": GENERAL_INITIAL_PROMPT,
    "game": GAME_INITIAL_PROMPT,
}

ASR_HALLUCINATION_PATTERNS = (
    "请准确转写",
    "请准确翻译",
    "实时语音字幕",
    "游戏语音聊天",
    "广告或会议",
    "中英文混杂",
    "thank you for watching",
    "thanks for watching",
    "感谢观看",
    "谢谢观看",
)
SHORT_ASR_FILLER_PATTERNS = {
    "uh",
    "um",
    "umm",
    "hmm",
    "hm",
    "ah",
    "oh",
    "er",
    "呃",
    "嗯",
    "啊",
    "哦",
    "额",
}
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[a-zA-Z]")
REPEAT_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)
REPEATED_NOISE_RUN_RE = re.compile(r"(.)\1{4,}", re.UNICODE)
GLOBAL_ASR_NO_SPEECH_SHORT_THRESHOLD = 0.65
GLOBAL_ASR_LOW_LOGPROB_SHORT_THRESHOLD = -1.10
GLOBAL_ASR_NOISE_TOKEN_NO_SPEECH_THRESHOLD = 0.30
GLOBAL_ASR_NOISE_TOKEN_LOGPROB_THRESHOLD = -0.80
GLOBAL_ASR_SHORT_COMPACT_MAX_LEN = 12
GLOBAL_ASR_ALLOWED_SHORT_TRANSCRIPTS = {
    "gg",
    "nt",
    "wp",
    "go",
    "no",
    "yes",
    "run",
    "push",
    "mid",
}
GLOBAL_ASR_SUSPICIOUS_SHORT_TRANSCRIPTS = {
    "you",
    "yoy",
    "thankyou",
    "thanks",
    "yeah",
    "okay",
    "ok",
    "music",
    "bam",
    "shoo",
    "shooo",
    "uh",
    "um",
    "umm",
    "hmm",
    "hm",
    "ah",
    "oh",
}
GLOBAL_ASR_NOISE_TOKENS = {
    "bam",
    "yoy",
    "shoo",
    "shooo",
}

FASTER_WHISPER_MODEL_FILES = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]
MODELSCOPE_BASE_URL = "https://www.modelscope.cn"
MODELSCOPE_REVISION = "master"
MODELSCOPE_MODEL_FILES = [
    "config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.txt",
]
MODELSCOPE_OPTIONAL_MODEL_FILES = [
    "preprocessor_config.json",
]
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
MODEL_DOWNLOAD_SOURCE_MODEL_SCOPE = "modelscope"
MODEL_DOWNLOAD_SOURCE_HUGGINGFACE = "huggingface"
MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT = "custom_hf_endpoint"
MODEL_DOWNLOAD_SOURCES = {
    MODEL_DOWNLOAD_SOURCE_MODEL_SCOPE,
    MODEL_DOWNLOAD_SOURCE_HUGGINGFACE,
    MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT,
}


class _TqdmOutputSink:
    def write(self, value):
        return len(value or "")

    def flush(self):
        pass


@dataclass
class WhisperConfig:
    model_size: str = "small"
    fast_model_size: str = ""
    pure_english_environment: bool = False
    enable_english_model: bool = False
    english_model_size: str = "small.en"
    fast_english_model_size: str = ""
    active_model_size: str = ""
    device: str = "cpu"
    compute_type: str = "auto"
    cpu_threads: int = 2
    num_workers: int = 1
    language: str = "auto"
    beam_size: int = 5
    vad_filter: bool = False
    vad_parameters: dict = None
    model_dir: str = ".models"
    model_download_source: str = ""
    model_download_endpoint: str = ""
    local_files_only: bool = False
    prompt_profile: str = "none"
    initial_prompt: str = ""
    condition_on_previous_text: bool = False
    temperature: float = 0.0
    no_speech_threshold: float = 0.6
    log_prob_threshold: float = -1.0
    compression_ratio_threshold: float = 2.4
    min_language_probability: float = 0.35
    normalize_audio: bool = True
    target_rms_dbfs: float = -20.0
    max_gain_db: float = 12.0
    min_gain_rms_dbfs: float = -50.0


@dataclass
class TranscriptionResult:
    text: str
    language: str = ""
    language_probability: float = 0.0
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    compression_ratio: float = 0.0
    segment_count: int = 0
    duration_seconds: float = 0.0


@dataclass
class ModelDownloadProgress:
    status: str
    model_name: str
    repo_id: str = ""
    source: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
    percent: float = 0.0
    message: str = ""


class SpeechRecognizer:
    """Whisper 语音识别器"""

    def __init__(
        self,
        config: WhisperConfig = None,
        download_progress_callback: Optional[Callable[[ModelDownloadProgress], None]] = None,
    ):
        self.config = config or WhisperConfig()
        if self.config.vad_parameters is None:
            self.config.vad_parameters = DEFAULT_VAD_PARAMS.copy()
        self.config.vad_parameters = sanitize_vad_parameters(self.config.vad_parameters)
        self._download_progress_callback = download_progress_callback
        self._model: Optional[WhisperModel] = None
        self._model_path: Optional[str] = None
        self._loaded_model_size: str = ""
        self._initialized = False
        self._model_dir = Path(self.config.model_dir)
        if not self._model_dir.is_absolute():
            self._model_dir = PROJECT_ROOT / self._model_dir
        logger.info(f"Whisper 模型目录: {self._model_dir}")

    def initialize(self):
        """初始化 Whisper 模型"""
        effective_model = self._effective_model_size()
        if self._initialized and self._loaded_model_size == effective_model:
            return
        if self._initialized:
            logger.info(
                "Whisper effective model changed: {} -> {}, reloading",
                self._loaded_model_size or "unknown",
                effective_model,
            )
            self.cleanup()

        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_model_downloaded()
        self._release_model_loader_memory()
        last_error = None
        for device, compute_type in self._model_load_candidates():
            runtime_options = self._model_runtime_options(device)
            max_attempts = 2 if device == "cpu" else 1
            for attempt in range(1, max_attempts + 1):
                logger.info(
                    "加载 Whisper 模型: {} (device={}, compute_type={}, cpu_threads={}, num_workers={}, attempt={}/{})",
                    self._effective_model_size(),
                    device,
                    compute_type,
                    runtime_options.get("cpu_threads", "-"),
                    runtime_options.get("num_workers", "-"),
                    attempt,
                    max_attempts,
                )
                try:
                    self._model = WhisperModel(
                        self._model_path or self._effective_model_size(),
                        device=device,
                        compute_type=compute_type,
                        download_root=str(self._model_dir),
                        local_files_only=self.config.local_files_only,
                        **runtime_options,
                    )
                    self.config.device = device
                    self.config.compute_type = compute_type
                    self._loaded_model_size = effective_model
                    self._initialized = True
                    logger.info("Whisper 模型加载完成")
                    return
                except Exception as e:
                    last_error = e
                    self._release_model_loader_memory()
                    if attempt < max_attempts and self._is_memory_allocation_error(e):
                        logger.warning(
                            "Whisper 模型加载遇到内存分配失败，已清理内存并准备重试: {}",
                            e,
                        )
                        time.sleep(0.8)
                        continue
                    logger.warning(
                        "加载 Whisper 模型失败，将尝试下一个设备配置: device={}, compute_type={}, error={}",
                        device,
                        compute_type,
                        e,
                    )
                    break

        fallback_model = self._fallback_model_size(effective_model)
        if fallback_model:
            logger.warning(
                "English-specialized Whisper model failed, falling back to {}: {}",
                fallback_model,
                last_error,
            )
            self.config.active_model_size = "" if fallback_model == self._configured_model_size() else fallback_model
            self.cleanup()
            return self.initialize()

        raise RuntimeError("Whisper 模型加载失败，没有可用的设备配置") from last_error

    def _release_model_loader_memory(self):
        self._model = None
        gc.collect()

    def _is_memory_allocation_error(self, error: Exception) -> bool:
        text = str(error).lower()
        return "mkl_malloc" in text or "failed to allocate memory" in text or "bad allocation" in text

    def _ensure_model_downloaded(self):
        if self.config.local_files_only:
            logger.info("Whisper 已启用 local_files_only，仅使用本地模型缓存")
            return

        repo_id = self._model_repo_id()
        if not repo_id:
            logger.info("Whisper 模型不是可识别的 Hugging Face 仓库，跳过预下载进度: {}", self._effective_model_size())
            return

        download_source = self._effective_download_source()
        endpoint = self._effective_download_endpoint()
        source = describe_model_download_source(download_source, endpoint)
        self._emit_download_progress(
            "checking",
            repo_id,
            source,
            message="正在检查本地模型缓存",
        )
        logger.info("检查 Whisper 模型缓存: repo={}, source={}", repo_id, source)

        try:
            if self._use_local_huggingface_snapshot(repo_id, source):
                return
            if download_source == "modelscope":
                self._download_model_from_modelscope(repo_id, source)
            else:
                self._download_model_from_huggingface(repo_id, endpoint, source)
        except Exception as e:
            message = format_model_download_error(e, repo_id, source)
            self._emit_download_progress(
                "error",
                repo_id,
                source,
                message=message,
            )
            raise RuntimeError(message) from e

    def _use_local_huggingface_snapshot(self, repo_id: str, source: str) -> bool:
        try:
            from huggingface_hub import snapshot_download

            cached_path = snapshot_download(
                repo_id,
                cache_dir=str(self._model_dir),
                allow_patterns=FASTER_WHISPER_MODEL_FILES,
                local_files_only=True,
            )
            if self._snapshot_has_required_model_files(cached_path):
                self._model_path = cached_path
                self._emit_download_progress(
                    "complete",
                    repo_id,
                    source,
                    downloaded_bytes=0,
                    total_bytes=0,
                    percent=100.0,
                    message="已使用本地模型缓存",
                )
                logger.info("已使用本地 Whisper 模型缓存: {}", cached_path)
                return True
            logger.warning("本地 Whisper 模型缓存不完整，将重新下载: {}", cached_path)
        except Exception as cache_error:
            logger.info("未找到完整本地 Whisper 模型缓存，将在线下载: {}", cache_error)
        return False

    def _download_model_from_huggingface(self, repo_id: str, endpoint: str, source: str):
        from huggingface_hub import snapshot_download

        self._model_path = snapshot_download(
            repo_id,
            cache_dir=str(self._model_dir),
            allow_patterns=FASTER_WHISPER_MODEL_FILES,
            endpoint=endpoint or None,
            max_workers=4,
            tqdm_class=self._progress_tqdm_class(repo_id, source),
        )
        self._emit_download_progress(
            "complete",
            repo_id,
            source,
            downloaded_bytes=0,
            total_bytes=0,
            percent=100.0,
            message="模型缓存已就绪",
        )
        logger.info("Whisper 模型缓存已就绪: {}", repo_id)

    def _download_model_from_modelscope(self, repo_id: str, source: str):
        model_dir = self._modelscope_model_dir(repo_id)
        if self._snapshot_has_required_model_files(str(model_dir)):
            self._model_path = str(model_dir)
            self._emit_download_progress(
                "complete",
                repo_id,
                source,
                downloaded_bytes=0,
                total_bytes=0,
                percent=100.0,
                message="已使用 ModelScope 本地模型缓存",
            )
            logger.info("已使用 ModelScope 本地模型缓存: {}", model_dir)
            return

        model_dir.mkdir(parents=True, exist_ok=True)
        files = self._modelscope_model_files(repo_id)
        total_bytes = sum(int(file_info["Size"]) for file_info in files)
        downloaded_bytes = 0
        for file_info in files:
            dest = model_dir / file_info["Path"]
            expected_size = int(file_info["Size"])
            if dest.is_file() and dest.stat().st_size == expected_size:
                downloaded_bytes += expected_size

        self._emit_download_progress(
            "downloading",
            repo_id,
            source,
            downloaded_bytes=downloaded_bytes,
            total_bytes=total_bytes,
            percent=(downloaded_bytes / total_bytes * 100.0) if total_bytes else 0.0,
            message="正在从 ModelScope 下载模型文件",
        )

        for file_info in files:
            file_path = file_info["Path"]
            expected_size = int(file_info["Size"])
            dest = model_dir / file_path
            if dest.is_file() and dest.stat().st_size == expected_size:
                continue

            if dest.exists():
                dest.unlink()
            part_path = dest.with_suffix(dest.suffix + ".part")
            if part_path.exists():
                part_path.unlink()
            dest.parent.mkdir(parents=True, exist_ok=True)

            url = self._modelscope_resolve_url(repo_id, file_path)
            logger.info("从 ModelScope 下载模型文件: {} -> {}", file_path, dest)
            request = Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urlopen(request, timeout=30) as response, part_path.open("wb") as output:
                    while True:
                        chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded_bytes += len(chunk)
                        self._emit_download_progress(
                            "downloading",
                            repo_id,
                            source,
                            downloaded_bytes=downloaded_bytes,
                            total_bytes=total_bytes,
                            percent=(downloaded_bytes / total_bytes * 100.0) if total_bytes else 0.0,
                            message=f"正在下载 {file_path}",
                        )
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                raise RuntimeError(f"ModelScope 下载 {file_path} 失败: {error}") from error

            actual_size = part_path.stat().st_size
            if actual_size != expected_size:
                part_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"ModelScope 下载 {file_path} 不完整: {actual_size} / {expected_size} bytes"
                )
            part_path.replace(dest)

        if not self._snapshot_has_required_model_files(str(model_dir)):
            raise RuntimeError("ModelScope 模型文件下载完成后仍不完整")

        self._model_path = str(model_dir)
        self._emit_download_progress(
            "complete",
            repo_id,
            source,
            downloaded_bytes=total_bytes,
            total_bytes=total_bytes,
            percent=100.0,
            message="ModelScope 模型缓存已就绪",
        )
        logger.info("ModelScope 模型缓存已就绪: {}", model_dir)

    def _modelscope_model_files(self, repo_id: str) -> list:
        url = self._modelscope_file_list_url(repo_id)
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"ModelScope 文件列表获取失败: {error}") from error

        if int(payload.get("Code", 0)) != 200:
            raise RuntimeError(f"ModelScope 文件列表返回异常: {payload}")

        all_files = payload.get("Data", {}).get("Files", [])
        wanted = set(MODELSCOPE_MODEL_FILES + MODELSCOPE_OPTIONAL_MODEL_FILES)
        selected = [
            file_info
            for file_info in all_files
            if file_info.get("Path") in wanted
        ]
        found = {file_info.get("Path") for file_info in selected}
        missing = [name for name in MODELSCOPE_MODEL_FILES if name not in found]
        if missing:
            raise RuntimeError(f"ModelScope 仓库缺少必要模型文件: {', '.join(missing)}")
        return sorted(selected, key=lambda item: MODELSCOPE_MODEL_FILES.index(item["Path"]) if item["Path"] in MODELSCOPE_MODEL_FILES else 99)

    def _modelscope_model_dir(self, repo_id: str) -> Path:
        return self._model_dir / "modelscope" / repo_id.replace("\\", "/")

    def _modelscope_file_list_url(self, repo_id: str) -> str:
        return f"{MODELSCOPE_BASE_URL}/api/v1/models/{_quote_repo_id(repo_id)}/repo/files?Revision={MODELSCOPE_REVISION}"

    def _modelscope_resolve_url(self, repo_id: str, file_path: str) -> str:
        return f"{MODELSCOPE_BASE_URL}/models/{_quote_repo_id(repo_id)}/resolve/{MODELSCOPE_REVISION}/{quote(file_path, safe='/')}"

    def _snapshot_has_required_model_files(self, snapshot_path: str) -> bool:
        path = Path(snapshot_path)
        return (
            (path / "config.json").is_file()
            and (path / "model.bin").is_file()
            and (
                (path / "tokenizer.json").is_file()
                or any(path.glob("vocabulary.*"))
            )
        )

    def _model_repo_id(self) -> Optional[str]:
        model_size = (self._effective_model_size() or "").strip()
        if not model_size:
            return None

        model_path = Path(model_size).expanduser()
        if model_path.exists():
            return None

        if "/" in model_size.replace("\\", "/"):
            return model_size.replace("\\", "/")

        repo_id = FASTER_WHISPER_MODEL_REPOS.get(model_size)
        if repo_id:
            return repo_id
        return None

    def _effective_download_endpoint(self) -> str:
        configured = normalize_model_download_endpoint(
            getattr(self.config, "model_download_endpoint", "")
        )
        if configured:
            return configured
        return normalize_model_download_endpoint(os.environ.get("HF_ENDPOINT", ""))

    def _effective_download_source(self) -> str:
        return normalize_model_download_source(
            getattr(self.config, "model_download_source", "modelscope"),
            getattr(self.config, "model_download_endpoint", ""),
        )

    def _progress_tqdm_class(self, repo_id: str, source: str):
        callback = self._download_progress_callback
        model_name = self._effective_model_size()

        try:
            from tqdm.auto import tqdm as base_tqdm
        except Exception:
            return None

        if callback is None:
            return base_tqdm

        class DownloadProgressTqdm(base_tqdm):
            def __init__(self, *args, **kwargs):
                self._is_bytes_progress = kwargs.get("unit") == "B"
                kwargs["file"] = _TqdmOutputSink()
                kwargs.setdefault("leave", False)
                super().__init__(*args, **kwargs)
                if self._is_bytes_progress:
                    self._emit("downloading")

            def update(self, n=1):
                result = super().update(n)
                if self._is_bytes_progress:
                    self._emit("downloading")
                return result

            def refresh(self, *args, **kwargs):
                result = super().refresh(*args, **kwargs)
                if getattr(self, "_is_bytes_progress", False):
                    self._emit("downloading")
                return result

            def set_description(self, desc=None, refresh=True):
                result = super().set_description(desc=desc, refresh=refresh)
                if getattr(self, "_is_bytes_progress", False) and desc:
                    self._emit("complete" if "complete" in str(desc).lower() else "downloading")
                return result

            def _emit(self, status: str):
                total = int(self.total or 0)
                downloaded = int(self.n or 0)
                percent = (downloaded / total * 100.0) if total else 0.0
                callback(ModelDownloadProgress(
                    status=status,
                    model_name=model_name,
                    repo_id=repo_id,
                    source=source,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    percent=percent,
                    message="正在下载模型文件" if status == "downloading" else "模型下载完成",
                ))

        return DownloadProgressTqdm

    def _emit_download_progress(
        self,
        status: str,
        repo_id: str,
        source: str,
        downloaded_bytes: int = 0,
        total_bytes: int = 0,
        percent: float = 0.0,
        message: str = "",
    ):
        if self._download_progress_callback is None:
            return
        self._download_progress_callback(ModelDownloadProgress(
            status=status,
            model_name=self._effective_model_size(),
            repo_id=repo_id,
            source=source,
            downloaded_bytes=downloaded_bytes,
            total_bytes=total_bytes,
            percent=percent,
            message=message,
        ))

    def _model_load_candidates(self):
        configured_device = (self.config.device or "auto").strip().lower()
        if configured_device == "auto":
            if not self._is_cuda_runtime_available():
                logger.info("未检测到可用 CUDA 运行环境，Whisper 使用 CPU")
                return [("cpu", "int8")]
            return [
                ("cuda", self._compute_type_for_device("cuda")),
                ("cpu", "int8"),
            ]

        candidates = [(configured_device, self._compute_type_for_device(configured_device))]
        if configured_device != "cpu":
            candidates.append(("cpu", "int8"))
        elif candidates[0][1] != "int8":
            candidates.append(("cpu", "int8"))
        return candidates

    def _is_cuda_runtime_available(self) -> bool:
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except Exception as e:
            logger.debug("CUDA 运行环境不可用: {}", e)
            return False

    def _compute_type_for_device(self, device: str) -> str:
        configured = (self.config.compute_type or "auto").strip().lower()
        if configured in ("", "auto", "default"):
            return "float16" if device == "cuda" else "int8"
        return configured

    def _model_runtime_options(self, device: str) -> dict:
        cpu_threads = max(1, min(4, int(getattr(self.config, "cpu_threads", 2) or 2)))
        num_workers = max(1, min(2, int(getattr(self.config, "num_workers", 1) or 1)))
        options = {"num_workers": num_workers}
        if device == "cpu":
            options["cpu_threads"] = cpu_threads
        return options

    def _initial_prompt(self) -> Optional[str]:
        custom_prompt = (self.config.initial_prompt or "").strip()
        if custom_prompt:
            return custom_prompt
        profile = (self.config.prompt_profile or "none").strip().lower()
        return PROMPT_PROFILES.get(profile)

    def _resample_to_16k(self, audio_array: np.ndarray, sample_rate: int) -> np.ndarray:
        if sample_rate == 16000:
            return audio_array.astype(np.float32, copy=False)

        original_len = len(audio_array)
        try:
            resampled = soxr.resample(
                audio_array,
                sample_rate,
                16000,
                quality="soxr_hq",
            ).astype(np.float32, copy=False)
            resampled = _fix_resampled_length(
                resampled,
                int(np.ceil(original_len * 16000 / sample_rate)),
            )
            logger.debug(
                "soxr 重采样: {}Hz -> 16000Hz, {} -> {} 点",
                sample_rate,
                original_len,
                len(resampled),
            )
            return resampled
        except Exception as e:
            logger.warning("soxr 重采样失败，退回线性插值: {}", e)
            target_len = int(original_len * 16000 / sample_rate)
            x_old = np.linspace(0, 1, original_len, dtype=np.float32)
            x_new = np.linspace(0, 1, target_len, dtype=np.float32)
            resampled = np.interp(x_new, x_old, audio_array).astype(np.float32)
            logger.debug(
                "线性插值重采样: {}Hz -> 16000Hz, {} -> {} 点",
                sample_rate,
                original_len,
                len(resampled),
            )
            return resampled

    def _normalize_for_transcription(self, audio_array: np.ndarray) -> np.ndarray:
        if not self.config.normalize_audio or len(audio_array) == 0:
            return audio_array.astype(np.float32, copy=False)

        audio_array = audio_array.astype(np.float32, copy=False)
        rms = float(np.sqrt(np.mean(audio_array ** 2)))
        rms_dbfs = 20 * np.log10(max(rms, 1e-10))
        min_gain_rms = float(getattr(self.config, "min_gain_rms_dbfs", -50.0))
        target_rms = float(getattr(self.config, "target_rms_dbfs", -20.0))
        max_gain = max(0.0, float(getattr(self.config, "max_gain_db", 12.0)))

        if rms_dbfs < min_gain_rms:
            logger.debug("跳过识别前增益: rms={:.1f} dBFS 低于 {:.1f} dBFS", rms_dbfs, min_gain_rms)
            return audio_array

        gain_db = min(max_gain, max(0.0, target_rms - rms_dbfs))
        if gain_db <= 0.1:
            return audio_array

        gain = 10 ** (gain_db / 20)
        normalized = np.clip(audio_array * gain, -1.0, 1.0).astype(np.float32, copy=False)
        logger.debug(
            "识别前增益: rms={:.1f} dBFS -> target={:.1f} dBFS, gain={:.1f} dB",
            rms_dbfs,
            target_rms,
            gain_db,
        )
        return normalized

    def transcribe_audio_bytes(self, audio_bytes: bytes, sample_rate: int = 44100) -> str:
        """将音频字节转录为文字"""
        return self.transcribe_audio_bytes_with_language(audio_bytes, sample_rate).text

    def transcribe_audio_bytes_with_language(
        self,
        audio_bytes: bytes,
        sample_rate: int = 44100,
        language_override: str = None,
    ) -> TranscriptionResult:
        """将音频字节转录为文字，并返回 Whisper 检测到的语言。"""
        if not self._initialized:
            self.initialize()

        # 将字节转换为 numpy 数组
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # 重采样到 16kHz（Whisper 要求）
        audio_array = self._resample_to_16k(audio_array, sample_rate)
        audio_array = self._normalize_for_transcription(audio_array)

        # 转录
        start_time = time.time()
        configured_language = self.config.language if language_override is None else language_override
        language = None if configured_language in (None, "", "auto") else configured_language
        initial_prompt = self._initial_prompt()
        segments, info = self._model.transcribe(
            audio_array,
            language=language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            vad_parameters=self.config.vad_parameters if self.config.vad_filter else None,
            initial_prompt=initial_prompt,
            condition_on_previous_text=self.config.condition_on_previous_text,
            temperature=self.config.temperature,
            no_speech_threshold=self.config.no_speech_threshold,
            log_prob_threshold=self.config.log_prob_threshold,
            compression_ratio_threshold=self.config.compression_ratio_threshold
        )

        # 合并所有片段
        text_parts = []
        segment_count = 0
        avg_logprob_values = []
        no_speech_values = []
        compression_values = []
        for segment in segments:
            text_parts.append(segment.text.strip())
            segment_count += 1
            avg_logprob_values.append(float(getattr(segment, "avg_logprob", 0.0) or 0.0))
            no_speech_values.append(float(getattr(segment, "no_speech_prob", 0.0) or 0.0))
            compression_values.append(float(getattr(segment, "compression_ratio", 0.0) or 0.0))

        full_text = " ".join(text_parts)
        elapsed = time.time() - start_time

        detected_language = getattr(info, "language", "") or ""
        language_probability = float(getattr(info, "language_probability", 0.0) or 0.0)
        avg_logprob = sum(avg_logprob_values) / len(avg_logprob_values) if avg_logprob_values else 0.0
        no_speech_prob = max(no_speech_values) if no_speech_values else 0.0
        compression_ratio = max(compression_values) if compression_values else 0.0
        logger.debug(
            "transcription complete: chars={}, language={}, prob={:.2f}, avg_logprob={:.2f}, no_speech={:.2f}, compression={:.2f}, segments={}, elapsed={:.2f}s",
            len(full_text),
            detected_language,
            language_probability,
            avg_logprob,
            no_speech_prob,
            compression_ratio,
            segment_count,
            elapsed,
        )
        return TranscriptionResult(
            full_text,
            detected_language,
            language_probability,
            avg_logprob=avg_logprob,
            no_speech_prob=no_speech_prob,
            compression_ratio=compression_ratio,
            segment_count=segment_count,
            duration_seconds=len(audio_array) / 16000.0 if len(audio_array) else 0.0,
        )

    def transcribe_audio_file(self, audio_file: str) -> str:
        """转录音频文件"""
        if not self._initialized:
            self.initialize()

        start_time = time.time()
        language = None if self.config.language in (None, "", "auto") else self.config.language
        initial_prompt = self._initial_prompt()
        segments, info = self._model.transcribe(
            audio_file,
            language=language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            vad_parameters=self.config.vad_parameters if self.config.vad_filter else None,
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
            "model_size": self._effective_model_size(),
            "configured_model_size": self.config.model_size,
            "fast_model_size": getattr(self.config, "fast_model_size", ""),
            "english_model_size": getattr(self.config, "english_model_size", ""),
            "fast_english_model_size": getattr(self.config, "fast_english_model_size", ""),
            "device": self.config.device,
            "compute_type": self.config.compute_type,
            "language": self.config.language,
            "prompt_profile": self.config.prompt_profile,
        }

    def _effective_model_size(self) -> str:
        active = str(getattr(self.config, "active_model_size", "") or "").strip()
        if active:
            return active
        return self._configured_model_size()

    def _configured_model_size(self) -> str:
        return str(getattr(self.config, "model_size", "small") or "small").strip() or "small"

    def _fallback_model_size(self, failed_model: str) -> str:
        failed = str(failed_model or "").strip()
        configured = self._configured_model_size()
        english_models = {
            str(getattr(self.config, "english_model_size", "") or "").strip(),
            str(getattr(self.config, "fast_english_model_size", "") or "").strip(),
        }
        if failed and failed in english_models and failed != configured:
            return configured
        return ""

    def cleanup(self):
        """清理资源"""
        self._model = None
        self._model_path = None
        self._loaded_model_size = ""
        self._initialized = False


# VAD 参数配置
DEFAULT_VAD_PARAMS = {
    "threshold": 0.5,
    "min_speech_duration_ms": 250,
    "max_speech_duration_s": 8,
    "min_silence_duration_ms": 2000,
    "speech_pad_ms": 400
}


def sanitize_vad_parameters(vad_parameters: Optional[dict]) -> Optional[dict]:
    if not vad_parameters:
        return vad_parameters
    if VadOptions is None:
        return dict(vad_parameters)
    try:
        supported_keys = set(inspect.signature(VadOptions).parameters)
    except Exception:
        return dict(vad_parameters)
    cleaned = {
        key: value
        for key, value in dict(vad_parameters).items()
        if key in supported_keys
    }
    removed = sorted(set(vad_parameters) - set(cleaned))
    if removed:
        logger.debug("忽略当前 faster-whisper 不支持的 VAD 参数: {}", ", ".join(removed))
    return cleaned


def _fix_resampled_length(audio_array: np.ndarray, target_len: int) -> np.ndarray:
    """Match librosa's default fixed-length soxr output."""
    if len(audio_array) == target_len:
        return audio_array
    if len(audio_array) > target_len:
        return audio_array[:target_len].astype(np.float32, copy=False)
    return np.pad(audio_array, (0, target_len - len(audio_array))).astype(np.float32, copy=False)


def normalize_model_download_endpoint(value: str) -> str:
    endpoint = (value or "").strip()
    if endpoint.lower() in ("official", "huggingface", "huggingface.co", "default", "none"):
        return ""
    if not endpoint:
        return ""
    aliases = {
        "hf-mirror": "https://hf-mirror.com",
        "hf-mirror.com": "https://hf-mirror.com",
        "mirror": "https://hf-mirror.com",
        "china": "https://hf-mirror.com",
        "cn": "https://hf-mirror.com",
    }
    endpoint = aliases.get(endpoint.lower(), endpoint)
    endpoint = endpoint.rstrip("/")
    if endpoint in ("https://huggingface.co", "http://huggingface.co"):
        return ""
    if not endpoint.startswith(("http://", "https://")):
        endpoint = "https://" + endpoint
    return endpoint.rstrip("/")


def normalize_model_download_source(source: str, endpoint: str = "") -> str:
    value = (source or "").strip().lower().replace("-", "_")
    normalized_endpoint = normalize_model_download_endpoint(endpoint)
    if not value and normalized_endpoint:
        return MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT
    aliases = {
        "": MODEL_DOWNLOAD_SOURCE_MODEL_SCOPE,
        "default": MODEL_DOWNLOAD_SOURCE_MODEL_SCOPE,
        "china": MODEL_DOWNLOAD_SOURCE_MODEL_SCOPE,
        "cn": MODEL_DOWNLOAD_SOURCE_MODEL_SCOPE,
        "domestic": MODEL_DOWNLOAD_SOURCE_MODEL_SCOPE,
        "modelscope": MODEL_DOWNLOAD_SOURCE_MODEL_SCOPE,
        "model_scope": MODEL_DOWNLOAD_SOURCE_MODEL_SCOPE,
        "ms": MODEL_DOWNLOAD_SOURCE_MODEL_SCOPE,
        "official": MODEL_DOWNLOAD_SOURCE_HUGGINGFACE,
        "huggingface": MODEL_DOWNLOAD_SOURCE_HUGGINGFACE,
        "huggingface.co": MODEL_DOWNLOAD_SOURCE_HUGGINGFACE,
        "hugging_face": MODEL_DOWNLOAD_SOURCE_HUGGINGFACE,
        "hf": MODEL_DOWNLOAD_SOURCE_HUGGINGFACE,
        "none": MODEL_DOWNLOAD_SOURCE_HUGGINGFACE,
        "custom": MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT,
        "custom_hf": MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT,
        "custom_huggingface": MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT,
        "custom_hugging_face": MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT,
        "custom_hf_endpoint": MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT,
    }
    if value in aliases:
        normalized = aliases[value]
    elif value in MODEL_DOWNLOAD_SOURCES:
        normalized = value
    else:
        normalized = MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT if normalized_endpoint else MODEL_DOWNLOAD_SOURCE_MODEL_SCOPE

    if normalized == MODEL_DOWNLOAD_SOURCE_HUGGINGFACE and normalized_endpoint:
        return MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT
    if normalized == MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT and not normalized_endpoint:
        return MODEL_DOWNLOAD_SOURCE_HUGGINGFACE
    return normalized


def describe_model_download_source(source: str = "", endpoint: str = "") -> str:
    raw_source = (source or "").strip()
    source_aliases = {
        "",
        "default",
        "china",
        "cn",
        "domestic",
        "modelscope",
        "model_scope",
        "ms",
        "official",
        "huggingface",
        "huggingface.co",
        "hugging_face",
        "hf",
        "none",
        "custom",
        "custom_hf",
        "custom_huggingface",
        "custom_hugging_face",
        "custom_hf_endpoint",
    }
    source_key = raw_source.lower().replace("-", "_")
    if endpoint == "" and raw_source and source_key not in source_aliases and source_key not in MODEL_DOWNLOAD_SOURCES:
        endpoint = raw_source
        source = MODEL_DOWNLOAD_SOURCE_CUSTOM_HF_ENDPOINT

    source = normalize_model_download_source(source, endpoint)
    endpoint = normalize_model_download_endpoint(endpoint)
    if source == MODEL_DOWNLOAD_SOURCE_MODEL_SCOPE:
        return "ModelScope 国内源"
    if source == MODEL_DOWNLOAD_SOURCE_HUGGINGFACE:
        return "官方 Hugging Face"
    if endpoint == "https://hf-mirror.com":
        return "自定义 Hugging Face Endpoint: hf-mirror.com"
    return f"自定义 Hugging Face Endpoint: {endpoint}" if endpoint else "自定义 Hugging Face Endpoint"


def format_model_download_error(error: Exception, repo_id: str, source: str) -> str:
    error_text = str(error).strip() or repr(error)
    return (
        "模型下载失败\n"
        f"模型: {repo_id}\n"
        f"来源: {source}\n"
        f"错误: {type(error).__name__}: {error_text}\n"
        "可以在设置里切换模型下载源后重启；大陆网络优先选择 ModelScope 国内源。"
        "如果程序已退出，也可以编辑 user_settings.json 或 config.json 里的 "
        "whisper.model_download_source 后再启动，或改用 full 包。"
    )


def _quote_repo_id(repo_id: str) -> str:
    return quote((repo_id or "").replace("\\", "/"), safe="/")


def is_likely_asr_hallucination(text: str) -> bool:
    normalized = " ".join((text or "").strip().casefold().split())
    if not normalized:
        return True
    if normalized in ASR_HALLUCINATION_PATTERNS:
        return True
    return any(pattern in normalized for pattern in ASR_HALLUCINATION_PATTERNS)


def normalize_transcript_for_repeat(text: str) -> str:
    """Normalize ASR text for short-window duplicate suppression."""
    normalized = " ".join((text or "").strip().casefold().split())
    return REPEAT_NORMALIZE_RE.sub("", normalized)


def _is_repeated_noise_transcript(text: str) -> bool:
    compact = normalize_transcript_for_repeat(text)
    if len(compact) < 6:
        return False
    if REPEATED_NOISE_RUN_RE.search(compact):
        return True
    return len(set(compact)) <= 2 and len(compact) >= 8


def _is_global_short_or_suspicious_transcript(text: str) -> bool:
    stripped = (text or "").strip()
    compact = normalize_transcript_for_repeat(stripped)
    if not compact:
        return True

    lowered = compact.casefold()
    if lowered in GLOBAL_ASR_ALLOWED_SHORT_TRANSCRIPTS:
        return False
    if lowered in GLOBAL_ASR_SUSPICIOUS_SHORT_TRANSCRIPTS:
        return True
    if len(compact) <= GLOBAL_ASR_SHORT_COMPACT_MAX_LEN:
        return True

    words = [part for part in re.split(r"\s+", stripped) if part]
    return len(words) <= 2 and len(compact) <= GLOBAL_ASR_SHORT_COMPACT_MAX_LEN + 4


def _is_suspicious_noise_token(text: str) -> bool:
    compact = normalize_transcript_for_repeat(text)
    if not compact:
        return True
    lowered = compact.casefold()
    return lowered in GLOBAL_ASR_NOISE_TOKENS or lowered in SHORT_ASR_FILLER_PATTERNS


def _normalize_filter_language(value: str) -> str:
    value = (value or "").strip().lower()
    aliases = {
        "english": "en",
        "eng": "en",
        "chinese": "zh",
        "zh-cn": "zh",
        "zh-tw": "zh",
        "cmn": "zh",
        "yue": "zh",
    }
    return aliases.get(value, value if value in {"en", "zh"} else "")


def _is_short_anomalous_transcript(text: str) -> bool:
    stripped = (text or "").strip()
    compact = normalize_transcript_for_repeat(stripped)
    if not compact:
        return True

    lowered = compact.casefold()
    if lowered in SHORT_ASR_FILLER_PATTERNS:
        return True

    allowed_short = {
        "gg",
        "nt",
        "wp",
        "ok",
        "go",
        "no",
        "yes",
        "run",
        "push",
        "mid",
    }
    if lowered in allowed_short:
        return False

    if len(compact) <= 1:
        return True

    has_cjk = bool(CJK_RE.search(stripped))
    has_latin = bool(LATIN_RE.search(stripped))
    has_digit = any(char.isdigit() for char in stripped)
    if len(compact) <= 3 and not has_digit:
        if not has_cjk and not has_latin:
            return True
        if len(set(compact)) == 1:
            return True

    return False


def should_drop_transcription_result(
    result: TranscriptionResult,
    expected_language: str = "",
    recent_texts=None,
    config: WhisperConfig = None,
) -> str:
    """Return a drop reason for likely ASR false positives after recognition."""
    text = (getattr(result, "text", "") or "").strip()
    if not text:
        return "识别文本为空"
    no_speech_prob = float(getattr(result, "no_speech_prob", 0.0) or 0.0)
    avg_logprob = float(getattr(result, "avg_logprob", 0.0) or 0.0)
    if _is_repeated_noise_transcript(text):
        return "global_asr_repeated_noise"
    if _is_suspicious_noise_token(text) and (
        no_speech_prob >= GLOBAL_ASR_NOISE_TOKEN_NO_SPEECH_THRESHOLD
        or avg_logprob <= GLOBAL_ASR_NOISE_TOKEN_LOGPROB_THRESHOLD
    ):
        return (
            "global_asr_noise_token "
            f"no_speech={no_speech_prob:.2f} avg_logprob={avg_logprob:.2f}"
        )
    if no_speech_prob >= GLOBAL_ASR_NO_SPEECH_SHORT_THRESHOLD and _is_global_short_or_suspicious_transcript(text):
        return f"global_asr_no_speech_short no_speech={no_speech_prob:.2f}"
    if avg_logprob <= GLOBAL_ASR_LOW_LOGPROB_SHORT_THRESHOLD and _is_global_short_or_suspicious_transcript(text):
        return f"global_asr_low_logprob_short avg_logprob={avg_logprob:.2f}"
    if is_likely_asr_hallucination(text):
        return "疑似 ASR 幻觉文本"
    if _is_short_anomalous_transcript(text):
        return "疑似短异常文本"

    compact = normalize_transcript_for_repeat(text)
    if compact:
        recent = list(recent_texts or [])
        recent_compact = [normalize_transcript_for_repeat(item) for item in recent]
        if compact in recent_compact and len(compact) <= 36:
            return "短时间重复识别文本"

    min_probability = float(getattr(config, "min_language_probability", 0.35) or 0.0)
    if min_probability > 0:
        probability = float(getattr(result, "language_probability", 0.0) or 0.0)
        detected = _normalize_filter_language(getattr(result, "language", ""))
        expected = _normalize_filter_language(expected_language)
        if 0 < probability < min_probability:
            if not expected or not detected or detected != expected or len(compact) <= 24:
                return f"语言置信度 {probability:.2f} < {min_probability:.2f}"

    return ""
