"""
主程序 - VoxGo
整合音频捕获、语音识别、翻译和浮窗展示
"""

import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from voxgo.runtime.dll_paths import configure_local_dll_paths

configure_local_dll_paths()

from loguru import logger

from voxgo.app_info import APP_NAME, APP_VERSION
from voxgo.audio.capture import AudioConfig
from voxgo.asr.whisper_engine import (
    ModelDownloadProgress,
    SpeechRecognizer,
    WhisperConfig,
    normalize_model_download_endpoint,
    normalize_model_download_source,
)
from voxgo.translation import (
    TranslationConfig,
    normalize_translation_provider,
    should_skip_translation_for_language,
)
from voxgo.update.checker import UpdateSettings
from voxgo.i18n import normalize_ui_language, ui_text
from voxgo.config.loader import (
    apply_language_runtime_policy,
    load_config as load_app_config,
    migrate_runtime_defaults,
    save_user_settings,
    sync_language_flow,
    sync_whisper_vad_limit,
)
from voxgo.config.schema import (
    AppConfig,
    HotkeyConfig,
    LANGUAGE_NAMES,
    OverlayConfig,
    RuntimeConfig,
    normalize_whisper_device as _normalize_whisper_device,
)
from voxgo.mobile.runtime import MobileRuntime
from voxgo.update.runtime import UpdateRuntime
from voxgo.runtime.banner import print_startup_banner
from voxgo.runtime.cuda_runtime import (
    detect_nvidia_gpu,
    download_and_install_cuda_runtime,
    has_local_cuda_runtime,
    resolve_cuda_runtime_download_url,
)
from voxgo.runtime.events import AppNotice, EventBus, TranscriptReady, TranslationReady
from voxgo.runtime.hotkeys import HotkeyManager
from voxgo.runtime.settings_controller import OverlaySettingsController
from voxgo.diagnostics.reporter import DiagnosticsReporter
from voxgo.asr.model_download_notice import ModelDownloadNoticeFormatter
from voxgo.asr.pipeline import SpeechPipeline
from voxgo.audio.runtime import AudioRuntime
from voxgo.translation.runtime import TranslationRuntime
from voxgo.ui.tray_controller import TrayController


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class VoxGoApp:

    def __init__(self, config_path: str = None):
        self._diagnostics = DiagnosticsReporter(PROJECT_ROOT)
        self._setup_logging()
        self.config = self._load_config(config_path)
        self._speech_recognizer: Optional[SpeechRecognizer] = None
        self._overlay = None
        self._mobile = MobileRuntime(
            self._notify_user,
            self._write_crash_report,
            lambda: self._stopping,
        )
        self._qt_app = None
        self._tray = TrayController(self)
        self._settings_controller = OverlaySettingsController(self)
        self._audio = AudioRuntime(
            lambda: self.config,
            self._on_speech_detected,
            self._notify_user,
            self._write_crash_report,
        )
        self._updates = UpdateRuntime(
            APP_VERSION,
            lambda: self.config,
            self._migrate_runtime_defaults,
            self._save_user_settings,
            self._notify_user,
        )
        self._audio_timer = None
        self._startup_thread: Optional[threading.Thread] = None
        self._cuda_runtime_download_thread: Optional[threading.Thread] = None
        self._startup_signals = None
        self._backend_ready = False
        self._running = False
        self._paused = False
        self._stopping = False
        self._translation_item_seq = 0
        self._hotkeys = HotkeyManager(self._notify_user)
        self._pending_notices = []
        self._event_bus = EventBus()
        self._event_bus.subscribe(AppNotice, self._handle_app_notice)
        self._event_bus.subscribe(TranscriptReady, self._handle_transcript_ready)
        self._event_bus.subscribe(TranslationReady, self._handle_translation_ready)
        self._latency_traces = {}
        self._last_latency_summary = {}
        self._last_audio_device = (
            self.config.audio.input_device_id,
            self.config.audio.input_device_index,
            self.config.audio.input_device_name,
            self.config.audio.latency_mode,
            self.config.audio.chunk_duration_ms,
            self.config.audio.speech_threshold_blocks,
            self.config.audio.silence_limit_blocks,
            self.config.audio.max_buffer_blocks,
            self.config.audio.max_speech_seconds,
            self.config.audio.pre_roll_ms,
            self.config.audio.speech_idle_timeout_ms,
        )
        self._last_translation_settings = (
            normalize_translation_provider(self.config.translation.provider),
            self.config.translation.api_key,
            self.config.translation.model,
            self.config.translation.endpoint,
        )
        self._last_language_flow = (
            self.config.translation.source_lang,
            self.config.translation.target_lang,
        )
        self._language_flow_revision = 1
        self._last_whisper_device = _normalize_whisper_device(self.config.whisper.device)
        self._last_model_download_source = (
            normalize_model_download_source(
                getattr(self.config.whisper, "model_download_source", "modelscope"),
                getattr(self.config.whisper, "model_download_endpoint", ""),
            ),
            normalize_model_download_endpoint(
                getattr(self.config.whisper, "model_download_endpoint", "")
            ),
        )
        self._model_download_notice_started = False
        self._last_model_download_update = 0.0
        self._model_download_notice_formatter = ModelDownloadNoticeFormatter(lambda: self.config)
        self._stats = {
            "audio_chunks": 0, "speech_detected": 0,
            "transcriptions": 0, "translations": 0, "errors": 0,
            "dropped_speech": 0, "filtered_speech": 0,
            "skipped_translations": 0,
        }
        self._translation = TranslationRuntime(
            self._event_bus,
            self._stats,
            self._latency_traces,
            lambda: self._overlay,
        )
        self._translation.set_language_revision_getter(lambda: self._language_flow_revision)
        self._speech_pipeline = SpeechPipeline(
            lambda: self.config,
            lambda: self._speech_recognizer,
            self._event_bus,
            self._stats,
            lambda: self._running,
            lambda: self._paused,
            self._next_translation_item_id,
            self._latency_traces,
            self._notify_user,
            lambda: self._language_flow_revision,
        )

    def _load_config(self, config_path: str = None) -> AppConfig:
        return load_app_config(config_path, self._runtime_dir())

    def _migrate_runtime_defaults(self, config: AppConfig, preserve_existing_audio_tuning: bool = True):
        migrate_runtime_defaults(config, preserve_existing_audio_tuning=preserve_existing_audio_tuning)

    def _save_user_settings(self):
        save_user_settings(self.config, self._runtime_dir())

    def _sync_language_flow(self, config: AppConfig = None):
        config = config or self.config
        result = sync_language_flow(config)
        apply_language_runtime_policy(config)
        return result

    def _sync_whisper_vad_limit(self, config: AppConfig = None):
        config = config or self.config
        sync_whisper_vad_limit(config)

    def _translation_config_snapshot(self, source_lang: str = "", target_lang: str = "") -> TranslationConfig:
        base = self.config.translation
        snapshot = TranslationConfig(
            provider=getattr(base, "provider", TranslationConfig.provider),
            api_key=getattr(base, "api_key", TranslationConfig.api_key),
            model=getattr(base, "model", TranslationConfig.model),
            endpoint=getattr(base, "endpoint", TranslationConfig.endpoint),
            max_tokens=getattr(base, "max_tokens", TranslationConfig.max_tokens),
            temperature=getattr(base, "temperature", TranslationConfig.temperature),
            source_lang=source_lang or getattr(base, "source_lang", TranslationConfig.source_lang),
            target_lang=target_lang or getattr(base, "target_lang", TranslationConfig.target_lang),
            context_messages=getattr(base, "context_messages", TranslationConfig.context_messages),
            timeout_seconds=getattr(base, "timeout_seconds", TranslationConfig.timeout_seconds),
            max_concurrent_requests=getattr(
                base,
                "max_concurrent_requests",
                TranslationConfig.max_concurrent_requests,
            ),
            skip_language_mismatch=getattr(base, "skip_language_mismatch", TranslationConfig.skip_language_mismatch),
            language_gate_min_probability=getattr(
                base,
                "language_gate_min_probability",
                TranslationConfig.language_gate_min_probability,
            ),
            language_gate_short_text_min_probability=getattr(
                base,
                "language_gate_short_text_min_probability",
                TranslationConfig.language_gate_short_text_min_probability,
            ),
            language_gate_short_text_chars=getattr(
                base,
                "language_gate_short_text_chars",
                TranslationConfig.language_gate_short_text_chars,
            ),
            enable_local_phrase_cache=getattr(
                base,
                "enable_local_phrase_cache",
                TranslationConfig.enable_local_phrase_cache,
            ),
            local_phrase_cache=getattr(base, "local_phrase_cache", TranslationConfig.local_phrase_cache),
        )
        return snapshot

    def _handle_language_flow_changed(self, source_lang: str, target_lang: str):
        previous_model = self._current_recognizer_model_size()
        self._language_flow_revision += 1
        self._translation.clear_context()
        self._translation.set_language_revision_getter(lambda: self._language_flow_revision)
        self._speech_pipeline.reset_for_language_switch(
            source_lang,
            target_lang,
            self._language_flow_revision,
        )
        cleared_audio_blocks = self._audio.clear_pending_audio()
        current_model = self._effective_whisper_model_size()
        if self._speech_recognizer and previous_model and current_model != previous_model:
            self._speech_recognizer.config = self.config.whisper
            self._speech_recognizer.cleanup()
            logger.info(
                "Whisper model will reload for language direction: {} -> {}",
                previous_model,
                current_model,
            )
        elif self._speech_recognizer:
            self._speech_recognizer.config = self.config.whisper
        logger.info(
            "language flow changed: revision={}, direction={}->{}, cleared_audio_blocks={}",
            self._language_flow_revision,
            source_lang,
            target_lang,
            cleared_audio_blocks,
        )

    def _effective_whisper_model_size(self) -> str:
        active = str(getattr(self.config.whisper, "active_model_size", "") or "").strip()
        if active:
            return active
        return str(getattr(self.config.whisper, "model_size", "small") or "small").strip() or "small"

    def _current_recognizer_model_size(self) -> str:
        if self._speech_recognizer:
            loaded = str(getattr(self._speech_recognizer, "_loaded_model_size", "") or "").strip()
            if loaded:
                return loaded
        if self._speech_recognizer and hasattr(self._speech_recognizer, "_effective_model_size"):
            return self._speech_recognizer._effective_model_size()
        return self._effective_whisper_model_size()

    def _refresh_cached_settings(self):
        self._last_audio_device = (
            self.config.audio.input_device_id,
            self.config.audio.input_device_index,
            self.config.audio.input_device_name,
            self.config.audio.latency_mode,
            self.config.audio.chunk_duration_ms,
            self.config.audio.speech_threshold_blocks,
            self.config.audio.silence_limit_blocks,
            self.config.audio.max_buffer_blocks,
            self.config.audio.max_speech_seconds,
            self.config.audio.pre_roll_ms,
            self.config.audio.speech_idle_timeout_ms,
        )
        self._last_translation_settings = (
            normalize_translation_provider(self.config.translation.provider),
            self.config.translation.api_key,
            self.config.translation.model,
            self.config.translation.endpoint,
        )
        self._last_language_flow = (
            self.config.translation.source_lang,
            self.config.translation.target_lang,
        )
        self._last_whisper_device = _normalize_whisper_device(self.config.whisper.device)
        self._last_model_download_source = (
            normalize_model_download_source(
                getattr(self.config.whisper, "model_download_source", "modelscope"),
                getattr(self.config.whisper, "model_download_endpoint", ""),
            ),
            normalize_model_download_endpoint(
                getattr(self.config.whisper, "model_download_endpoint", "")
            ),
        )

    def _setup_logging(self):
        self._diagnostics.setup_logging()

    def _runtime_dir(self) -> Path:
        return self._diagnostics.runtime_dir()

    def _write_crash_report(self, title: str, exc: Exception, detail: str = ""):
        self._diagnostics.write_crash_report(title, exc, detail)

    def _show_error_dialog(self, title: str, message: str):
        self._diagnostics.show_error_dialog(title, message)

    def _notify_user(self, title: str, message: str, level: str = "状态"):
        self._event_bus.publish(AppNotice(level=level, title=title, message=message))

    def _handle_app_notice(self, event: AppNotice):
        title = (event.title or "").strip()
        message = (event.message or "").strip()
        level = (event.level or "状态").strip()
        if not title and not message:
            return
        original = f"[{level}] {title}" if title else f"[{level}]"
        if self._overlay:
            self._overlay.add_translation(original, message)
        else:
            self._pending_notices.append((original, message))

    def _handle_transcript_ready(self, event: TranscriptReady):
        self._stats["transcriptions"] += 1
        self._speech_pipeline.remember_transcript(event.text)
        trace = self._latency_traces.get(event.trace_id)
        language_probability = getattr(event, "language_probability", 0.0)
        event_revision = int(getattr(event, "language_revision", 0) or 0)
        source_lang = getattr(event, "source_lang", "") or getattr(self.config.translation, "source_lang", "")
        target_lang = getattr(event, "target_lang", "") or getattr(self.config.translation, "target_lang", "")
        if event_revision and event_revision != self._language_flow_revision:
            if trace:
                trace.translation_started_at = time.time()
                trace.translation_finished_at = trace.translation_started_at
                self._latency_traces.pop(event.trace_id, None)
            self._stats["skipped_translations"] = self._stats.get("skipped_translations", 0) + 1
            logger.info(
                "translation skipped: stale_language_flow, item_revision={}, current_revision={}, snapshot={}->{}, current={}->{}, text={}",
                event_revision,
                self._language_flow_revision,
                source_lang or "unknown",
                target_lang or "unknown",
                getattr(self.config.translation, "source_lang", "") or "unknown",
                getattr(self.config.translation, "target_lang", "") or "unknown",
                event.text[:80],
            )
            return
        gate_config = self._translation_config_snapshot(source_lang, target_lang)
        skip_reason = should_skip_translation_for_language(
            event.text,
            source_lang,
            event.language,
            language_probability,
            gate_config,
        )
        if skip_reason:
            if trace:
                trace.translation_started_at = time.time()
                trace.translation_finished_at = trace.translation_started_at
                self._latency_traces.pop(event.trace_id, None)
            self._stats["skipped_translations"] = self._stats.get("skipped_translations", 0) + 1
            logger.info(
                "translation skipped: {}, detected={}, expected={}, prob={:.2f}, text={}",
                skip_reason,
                event.language or "unknown",
                source_lang or "unknown",
                float(language_probability or 0.0),
                event.text[:80],
            )
            return
        if self._overlay:
            self._overlay.add_translation_with_id(event.trace_id, event.text, "...正在翻译")
        logger.info(
            "language gate accepted: detected={}, expected={}, prob={:.2f}, snapshot={}->{}, revision={}",
            event.language or "unknown",
            source_lang or "unknown",
            float(language_probability or 0.0),
            source_lang or "unknown",
            target_lang or "unknown",
            event_revision,
        )
        self._start_async_translation(
            event.trace_id,
            event.text,
            event.language,
            language_probability,
            source_lang,
            target_lang,
            event_revision,
            trace,
        )

    def _handle_translation_ready(self, event: TranslationReady):
        event_revision = int(getattr(event, "language_revision", 0) or 0)
        if event_revision and event_revision != self._language_flow_revision:
            self._latency_traces.pop(event.trace_id, None)
            if self._overlay and hasattr(self._overlay, "remove_translation"):
                self._overlay.remove_translation(event.trace_id)
            self._stats["skipped_translations"] = self._stats.get("skipped_translations", 0) + 1
            logger.info(
                "translation result ignored: stale_language_flow, item_revision={}, current_revision={}, source={}, target={}, text={}",
                event_revision,
                self._language_flow_revision,
                event.source_lang or "unknown",
                event.target_lang or "unknown",
                event.original[:80],
            )
            return
        translated = event.translated
        is_notice = translated.startswith("[翻译") or translated.startswith("[未翻译]")
        if is_notice:
            if translated.startswith("[翻译"):
                self._stats["errors"] += 1
            if self._overlay:
                self._overlay.update_translation(event.trace_id, translated)
            else:
                self._notify_user(
                    "翻译服务异常",
                    translated,
                    "错误" if translated.startswith("[翻译") else "提示",
                )
        else:
            self._stats["translations"] += 1
            if self._overlay:
                logger.info("更新浮窗翻译")
                self._overlay.update_translation(event.trace_id, translated)

        if self._mobile.server:
            self._broadcast_to_mobile(event.original, translated)

    def _notify_model_download_progress(self, progress: ModelDownloadProgress):
        self._model_download_notice_formatter.record_progress(progress)
        now = time.time()
        if progress.status == "downloading" and now - self._last_model_download_update < 0.5:
            return
        self._last_model_download_update = now

        message = self._model_download_notice_formatter.format(progress)
        item_id = "model-download-progress"
        original = "[状态] 模型下载"
        if self._overlay:
            if self._model_download_notice_started:
                self._overlay.update_translation(item_id, message)
            else:
                self._overlay.add_translation_with_id(item_id, original, message)
                self._model_download_notice_started = True
        else:
            self._pending_notices.append((original, message))

    def _notify_recognition_device_fallback(self, reason: str, message: str):
        logger.warning("recognition device fallback: reason={}, message={}", reason, message)
        self._notify_user("识别设备已降级到 CPU", message, "警告")

    def _prepare_cuda_runtime_for_gpu_selection(self, ui_language: str) -> bool:
        ui_language = normalize_ui_language(ui_language)
        if has_local_cuda_runtime():
            return True

        detection = detect_nvidia_gpu()
        adapter_names = ", ".join(detection.adapter_names) or ui_text(
            ui_language,
            "未检测到显卡名称",
            "No display adapter name detected",
        )
        if not detection.has_nvidia:
            detail = ui_text(
                ui_language,
                f"当前显卡: {adapter_names}\nGPU 识别模式需要 NVIDIA CUDA，AMD/Intel 显卡暂不支持。已保持原来的识别设备。",
                f"Current GPU: {adapter_names}\nGPU recognition requires NVIDIA CUDA. AMD/Intel GPUs are not supported. VoxGo kept the previous recognition device.",
            )
            if detection.error:
                detail += ui_text(
                    ui_language,
                    f"\n检测详情: {detection.error}",
                    f"\nDetection detail: {detection.error}",
                )
            self._notify_user(
                ui_text(ui_language, "GPU 模式不可用", "GPU Mode Unavailable"),
                detail,
                ui_text(ui_language, "警告", "Warning"),
            )
            logger.warning(
                "CUDA device selection rejected: adapters={}, error={}",
                detection.adapter_names,
                detection.error,
            )
            return False

        self._start_cuda_runtime_download(ui_language, adapter_names)
        return True

    def _start_cuda_runtime_download(self, ui_language: str, adapter_names: str):
        if self._cuda_runtime_download_thread and self._cuda_runtime_download_thread.is_alive():
            self._notify_user(
                ui_text(ui_language, "CUDA 运行库下载中", "CUDA Runtime Downloading"),
                ui_text(
                    ui_language,
                    "已检测到 NVIDIA 显卡，CUDA 运行库正在后台下载。完成后重启 VoxGo 即可启用 GPU。",
                    "An NVIDIA GPU was detected. The CUDA runtime is downloading in the background. Restart VoxGo after it finishes to enable GPU.",
                ),
                ui_text(ui_language, "状态", "Status"),
            )
            return
        self._notify_user(
            ui_text(ui_language, "正在准备 GPU 模式", "Preparing GPU Mode"),
            ui_text(
                ui_language,
                f"已检测到 NVIDIA 显卡: {adapter_names}\n当前包未内置 CUDA DLL，VoxGo 将从发布页下载 CUDA 运行库。",
                f"NVIDIA GPU detected: {adapter_names}\nThis package does not include CUDA DLLs. VoxGo will download the CUDA runtime from the release page.",
            ),
            ui_text(ui_language, "状态", "Status"),
        )
        self._cuda_runtime_download_thread = threading.Thread(
            target=self._download_cuda_runtime_worker,
            args=(ui_language,),
            name="cuda-runtime-download",
            daemon=True,
        )
        self._cuda_runtime_download_thread.start()

    def _download_cuda_runtime_worker(self, ui_language: str):
        last_notice_at = 0.0

        def _progress(downloaded: int, total: int):
            nonlocal last_notice_at
            now = time.time()
            if total <= 0 or now - last_notice_at < 10:
                return
            last_notice_at = now
            self._notify_user(
                ui_text(ui_language, "CUDA 运行库下载中", "CUDA Runtime Downloading"),
                ui_text(
                    ui_language,
                    f"{downloaded / 1024 / 1024:.0f} / {total / 1024 / 1024:.0f} MB",
                    f"{downloaded / 1024 / 1024:.0f} / {total / 1024 / 1024:.0f} MB",
                ),
                ui_text(ui_language, "状态", "Status"),
            )

        try:
            download_url = resolve_cuda_runtime_download_url()
            self._notify_user(
                ui_text(ui_language, "CUDA 运行库下载中", "CUDA Runtime Downloading"),
                download_url,
                ui_text(ui_language, "状态", "Status"),
            )
            result = download_and_install_cuda_runtime(progress_callback=_progress, url=download_url)
            logger.info(
                "CUDA runtime installed: dir={}, dlls={}, downloaded_bytes={}",
                result.runtime_dir,
                result.dlls,
                result.downloaded_bytes,
            )
            self._notify_user(
                ui_text(ui_language, "CUDA 运行库已安装", "CUDA Runtime Installed"),
                ui_text(
                    ui_language,
                    f"CUDA DLL 已安装到:\n{result.runtime_dir}\n重启 VoxGo 后将启用 NVIDIA GPU 识别。",
                    f"CUDA DLLs were installed to:\n{result.runtime_dir}\nRestart VoxGo to enable NVIDIA GPU recognition.",
                ),
                ui_text(ui_language, "状态", "Status"),
            )
        except Exception as exc:
            logger.exception("CUDA runtime download/install failed: {}", exc)
            self._notify_user(
                ui_text(ui_language, "CUDA 运行库下载失败", "CUDA Runtime Download Failed"),
                ui_text(
                    ui_language,
                    f"GPU 模式暂时无法启用，启动时会降级到 CPU。\n原因: {exc}",
                    f"GPU mode cannot be enabled yet. VoxGo will fall back to CPU on startup.\nReason: {exc}",
                ),
                ui_text(ui_language, "错误", "Error"),
            )

    def _flush_pending_notices(self):
        if not self._overlay:
            return
        for original, message in self._pending_notices:
            self._overlay.add_translation(original, message)
        self._pending_notices.clear()

    def _on_speech_detected(self, speech_segment):
        self._speech_pipeline.on_speech_detected(speech_segment)

    def _start_speech_worker(self):
        self._speech_pipeline.start()

    def _stop_speech_worker(self):
        return self._speech_pipeline.stop()

    def _setup_hotkeys(self):
        self._hotkeys.setup(
            self.config.hotkeys,
            {
                "toggle_overlay": self._toggle_overlay,
                "clear_history": self._clear_history,
                "toggle_translation": self._toggle_translation,
                "toggle_lock": self._toggle_lock,
                "toggle_compact": self._toggle_compact_mode,
            },
        )

    def _remove_hotkeys(self):
        self._hotkeys.remove_all()

    def _toggle_overlay(self):
        if self._overlay:
            logger.info("触发热键: 切换浮窗")
            self._overlay._signals.toggle_visibility.emit()
            self._notify_user("热键", f"已触发: {self.config.hotkeys.toggle_overlay}", "状态")
            self._sync_tray_state()

    def _clear_history(self):
        if self._overlay:
            logger.info("触发热键: 清空历史")
            self._overlay._signals.clear_history.emit()
            self._notify_user("翻译历史", "已清空", "状态")

    def _toggle_translation(self):
        self._paused = not self._paused
        logger.info(f"翻译{'暂停' if self._paused else '恢复'}")
        if self._overlay:
            self._overlay.set_paused(self._paused)
        self._sync_tray_state()
        self._notify_user("翻译状态", "翻译暂停" if self._paused else "翻译恢复", "状态")

    def _toggle_lock(self):
        if self._overlay:
            logger.info("触发热键: 锁定/解锁浮窗")
            self._overlay._signals.toggle_lock.emit()
            self._sync_tray_state()

    def _toggle_compact_mode(self):
        if self._overlay:
            logger.info("触发热键: 切换紧凑浮窗")
            self._overlay._signals.toggle_compact.emit()
            self._sync_tray_state()

    def _start_qt(self):
        from PyQt5.QtWidgets import QApplication, QMenu, QSystemTrayIcon
        from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, Qt, QTimer
        from PyQt5.QtGui import QIcon
        from voxgo.ui.overlay_window import GameOverlay

        class StartupSignals(QObject):
            backend_ready = pyqtSignal()
            backend_failed = pyqtSignal(str)

            def __init__(self, owner):
                super().__init__()
                self._owner = owner

            @pyqtSlot()
            def finish_backend_startup(self):
                self._owner._finish_backend_startup()

            @pyqtSlot(str)
            def handle_backend_startup_failure(self, message: str):
                self._owner._handle_backend_startup_failure(message)

        self._qt_app = QApplication.instance() or QApplication(sys.argv)
        self._qt_app.setApplicationName(APP_NAME)
        self._qt_app.setApplicationDisplayName(APP_NAME)
        icon_path = PROJECT_ROOT / "assets" / "voxgo.ico"
        if icon_path.exists():
            self._qt_app.setWindowIcon(QIcon(str(icon_path)))
        self._app_icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self._startup_signals = StartupSignals(self)
        self._startup_signals.backend_ready.connect(
            self._startup_signals.finish_backend_startup,
            Qt.QueuedConnection,
        )
        self._startup_signals.backend_failed.connect(
            self._startup_signals.handle_backend_startup_failure,
            Qt.QueuedConnection,
        )
        mobile_url = self._mobile.get_mobile_url()
        if mobile_url:
            self.config.overlay.mobile_url = mobile_url
        self._overlay = GameOverlay(
            config=self.config.overlay,
            hotkeys=self.config.hotkeys,
            audio_config=self.config.audio,
            translation_config=self.config.translation,
            audio_devices=[],
            whisper_config=self.config.whisper,
            app_config=self.config.app,
            debug_config=self.config.debug,
            update_config=self.config.update,
            app_version=APP_VERSION,
            runtime_dir=str(self._runtime_dir()),
            get_last_latency_summary=self._get_last_latency_summary,
            on_settings_changed=self._apply_overlay_settings,
            on_audio_devices_refresh=self._list_audio_devices,
            on_update_check_requested=self._request_update_check,
            on_update_version_ignored=self._ignore_update_version,
            on_shutdown_requested=self._request_shutdown,
            on_overlay_updated=self._on_overlay_updated,
        )
        self._overlay.show()
        self._setup_tray_icon(QSystemTrayIcon, QMenu)
        self._flush_pending_notices()
        QTimer.singleShot(300, self._refresh_overlay_audio_devices)
        QTimer.singleShot(1200, lambda: self._request_update_check(manual=False))
        logger.info("浮窗已启动")

    def _setup_tray_icon(self, tray_cls=None, menu_cls=None):
        if tray_cls is None or menu_cls is None:
            from PyQt5.QtWidgets import QMenu, QSystemTrayIcon
            tray_cls = QSystemTrayIcon
            menu_cls = QMenu
        self._tray.setup(
            tray_cls,
            menu_cls,
            self._qt_app,
            getattr(self, "_app_icon", None) or self._qt_app.windowIcon(),
            self._overlay,
        )

    def _sync_tray_state(self):
        self._tray.sync_state(self._overlay)

    def _tray_toggle_overlay(self):
        self._toggle_overlay()

    def _tray_toggle_compact_mode(self):
        if self._overlay:
            self._overlay.toggle_compact_mode()
            self.config.overlay = self._overlay.config
        self._save_user_settings()
        self._sync_tray_state()

    def _tray_open_settings(self):
        if self._overlay:
            self._overlay.show()
            self._overlay.raise_()
            self._overlay.show_settings()
        self._sync_tray_state()

    def _tray_show_fullscreen_help(self):
        if self._overlay:
            self._overlay.show()
            self._overlay.raise_()
            self._overlay.show_fullscreen_help()
        self._sync_tray_state()

    def _handle_tray_activated(self, reason):
        try:
            from PyQt5.QtWidgets import QSystemTrayIcon
            if reason == QSystemTrayIcon.Trigger:
                self._tray_toggle_overlay()
        except Exception:
            return

    def _start_backend_after_setup(self):
        self._sync_language_flow()
        self._sync_whisper_vad_limit()
        self.config.app.setup_completed = True
        self._save_user_settings()
        self._refresh_cached_settings()
        self._notify_user(
            "设置已保存",
            "正在后台加载语音识别和翻译服务",
            "状态",
        )
        self._start_backend_thread()

    def _request_shutdown(self):
        logger.info("收到退出按钮请求")
        self._tray.hide()
        if self._qt_app:
            self._qt_app.quit()

    def _apply_overlay_settings(
        self,
        overlay_config: OverlayConfig,
        hotkey_config: HotkeyConfig,
        audio_config: AudioConfig,
        translation_config: TranslationConfig,
        whisper_config: WhisperConfig,
        app_config: RuntimeConfig,
        update_config: UpdateSettings,
    ):
        self._settings_controller.apply(
            overlay_config,
            hotkey_config,
            audio_config,
            translation_config,
            whisper_config,
            app_config,
            update_config,
        )

    def _start_mobile(self):
        self._mobile.start(self.config.websocket)

    def _start_backend_thread(self):
        if self._startup_thread and self._startup_thread.is_alive():
            return
        self._startup_thread = threading.Thread(
            target=self._initialize_backend_services,
            name="startup-loader",
            daemon=True,
        )
        self._startup_thread.start()
        logger.info("后台启动线程已启动")

    def _initialize_backend_services(self):
        try:
            self._notify_user("语音识别模型", "正在后台加载 Whisper 模型，首次运行可能需要等待", "状态")
            self._speech_recognizer = SpeechRecognizer(
                self.config.whisper,
                self._notify_model_download_progress,
                self._notify_recognition_device_fallback,
            )
            self._translation.initialize(self.config.translation)
            self._speech_recognizer.initialize()
            if self._stopping:
                return
            self._backend_ready = True
            if self._startup_signals:
                self._startup_signals.backend_ready.emit()
        except Exception as e:
            self._stats["errors"] += 1
            self._write_crash_report("启动失败", e)
            logger.exception(f"启动失败: {e}")
            if self._startup_signals:
                self._startup_signals.backend_failed.emit(str(e))

    def _finish_backend_startup(self):
        if self._stopping or self._running:
            return
        self._setup_hotkeys()
        signal.signal(signal.SIGINT, lambda s, f: self.stop())
        signal.signal(signal.SIGTERM, lambda s, f: self.stop())

        self._running = True
        self._start_speech_worker()
        self._print_banner()
        self._notify_user(
            "程序已启动",
            (
                f"当前方向: {LANGUAGE_NAMES[self.config.translation.source_lang]} "
                f"→ {LANGUAGE_NAMES[self.config.translation.target_lang]}\n"
                f"手机端: {self._mobile.get_mobile_url()}"
            ),
            "状态",
        )

        try:
            self._start_audio_capture()
        except Exception as e:
            self._stats["errors"] += 1
            self._show_audio_startup_warning(e)

        from PyQt5.QtCore import QTimer

        self._audio_timer = QTimer()
        self._audio_timer.timeout.connect(self._process_audio_tick)
        self._audio_timer.start(100)
        logger.info("后端服务已启动")

    def _handle_backend_startup_failure(self, message: str):
        if self._stopping:
            return
        self._notify_user("启动失败", message, "错误")
        self._show_error_dialog(
            f"{APP_NAME} 启动失败",
            f"程序启动失败，已在程序目录生成 crash_report.txt。\n\n{message}",
        )
        if self._qt_app:
            self._qt_app.quit()

    def _start_audio_capture(self, notice_title: str = "音频捕获已启动", reuse_noise_gate: bool = False):
        self._audio.start(notice_title, reuse_noise_gate=reuse_noise_gate)

    def _restart_audio_capture(self, reuse_noise_gate: bool = False):
        def _handle_error(exc):
            self._stats["errors"] += 1
            self._show_audio_startup_warning(exc)

        self._audio.restart(_handle_error, reuse_noise_gate=reuse_noise_gate)

    def _list_audio_devices(self):
        return self._audio.list_devices()

    def _refresh_overlay_audio_devices(self):
        if self._overlay and not self._stopping:
            self._overlay.request_audio_device_refresh()

    def _request_update_check(self, manual: bool = False):
        self._updates.request(bool(manual), self._overlay, lambda: self._stopping)

    def _ignore_update_version(self, version: str):
        self._updates.ignore_version(version)

    def _show_audio_startup_warning(self, exc: Exception):
        detail = (
            "音频采集启动失败，程序已继续运行，但暂时不会识别游戏声音。\n"
            "请在 Windows 声音设置里启用“立体声混音 / Stereo Mix”，"
            "或安装/选择可采集系统声音的虚拟声卡后重启本程序。\n"
            f"错误: {exc}"
        )
        self._write_crash_report("音频采集启动失败", exc, detail)
        logger.exception(detail)
        self._notify_user("音频采集未启动", detail, "错误")

    def _next_translation_item_id(self) -> str:
        self._translation_item_seq += 1
        return f"translation-{self._translation_item_seq}"

    def _start_async_translation(
        self,
        item_id: str,
        text: str,
        detected_language: str = "",
        language_probability: float = 0.0,
        source_lang: str = "",
        target_lang: str = "",
        language_revision: int = 0,
        trace=None,
    ):
        self._translation.translate_async(
            item_id,
            text,
            detected_language,
            language_probability,
            source_lang,
            target_lang,
            language_revision,
            trace,
        )

    def _on_overlay_updated(self, item_id: str):
        trace = self._latency_traces.pop(item_id, None)
        if not trace:
            return
        trace.overlay_updated_at = time.time()
        self._last_latency_summary = trace.summary_ms()
        if getattr(self.config.debug, "enabled", False):
            logger.info(
                "[延迟] item={} wait={}ms recognition={}ms translation={}ms overlay={}ms total={}ms mode={} labels={} model={} device={} compute={} threads={} fast_path={}/{}",
                item_id,
                self._last_latency_summary.get("wait_ms", 0),
                self._last_latency_summary.get("recognition_ms", 0),
                self._last_latency_summary.get("translation_ms", 0),
                self._last_latency_summary.get("overlay_ms", 0),
                self._last_latency_summary.get("total_ms", 0),
                self._last_latency_summary.get("latency_mode", ""),
                self._last_latency_summary.get("candidate_labels", ""),
                self._last_latency_summary.get("whisper_model_size", ""),
                self._last_latency_summary.get("whisper_device", ""),
                self._last_latency_summary.get("whisper_compute_type", ""),
                self._last_latency_summary.get("whisper_cpu_threads", 0),
                self._last_latency_summary.get("fast_path_allowed", False),
                self._last_latency_summary.get("fast_path_ready", False),
            )

    def _get_last_latency_summary(self) -> dict:
        return dict(self._last_latency_summary or {})

    def _broadcast_to_mobile(self, original: str, translated: str):
        self._mobile.broadcast_translation(original, translated)

    def _process_audio_tick(self):
        self._audio.process_tick(self._running, self._paused)

    def _print_banner(self):
        print_startup_banner(self.config, self._mobile.get_mobile_url())

    def start(self):
        logger.info("正在启动...")
        try:
            self._start_mobile()
            self._start_qt()
            if getattr(self.config.app, "setup_completed", False):
                self._notify_user(
                    "正在启动",
                    "浮窗已显示，正在后台加载语音识别和翻译服务",
                    "状态",
                )
                self._start_backend_thread()
            else:
                self._notify_user(
                    "首次启动",
                    "请先完成翻译接口和音频设备测试",
                    "状态",
                )
                if self._overlay:
                    self._overlay.show_first_run_wizard(self._start_backend_after_setup)

            self._qt_app.exec_()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            self._write_crash_report("启动失败", e)
            logger.exception(f"启动失败: {e}")
            self._notify_user("启动失败", str(e), "错误")
            self._show_error_dialog(
                f"{APP_NAME} 启动失败",
                f"程序启动失败，已在程序目录生成 crash_report.txt。\n\n{e}",
            )
        finally:
            self.stop()

    def stop(self):
        if self._stopping:
            return
        self._stopping = True
        logger.info("正在停止...")
        self._running = False
        if self._audio_timer:
            self._audio_timer.stop()
        self._audio.stop()
        speech_worker_stopped = self._stop_speech_worker()
        self._remove_hotkeys()
        startup_in_progress = self._startup_thread and self._startup_thread.is_alive()
        if startup_in_progress:
            logger.warning("后台启动仍在进行，跳过模型清理以避免资源释放冲突")
        if self._speech_recognizer and speech_worker_stopped and not startup_in_progress:
            self._speech_recognizer.cleanup()
        self._mobile.stop()
        self._translation.close(cleanup_allowed=speech_worker_stopped and not startup_in_progress)
        if self._overlay:
            self._overlay.close()
        self._tray.hide()
        if self._qt_app:
            self._qt_app.quit()

        if sys.stdout is not None:
            print(f"\n翻译统计: 识别 {self._stats['transcriptions']} 条, "
                  f"翻译 {self._stats['translations']} 条, "
                  f"跳过 {self._stats.get('skipped_translations', 0)} 条, "
                  f"过滤 {self._stats['filtered_speech']} 段, 错误 {self._stats['errors']} 次")
        logger.info("已停止")


def main():
    config_path = None
    default_config = PROJECT_ROOT / "config.json"
    if default_config.exists():
        config_path = str(default_config)

    app = VoxGoApp(config_path)
    app.start()


if __name__ == "__main__":
    main()
