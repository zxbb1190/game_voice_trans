"""
主程序 - 游戏语音实时翻译器
整合音频捕获、语音识别、翻译和浮窗展示
"""

import asyncio
import os
import json
import signal
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import keyboard
from loguru import logger

from audio_capture import SystemAudioCapture, AudioConfig, list_input_devices
from speech_recognition import SpeechRecognizer, WhisperConfig
from translator import GameTranslator, TranslationConfig
from mobile_server import MobileWebSocketManager, WebSocketConfig


@dataclass
class OverlayConfig:
    font_size: int = 16
    font_family: str = "Microsoft YaHei"
    text_color: str = "#00FF00"
    bg_color: str = "#000000AA"
    position: str = "bottom"
    max_lines: int = 5
    fade_duration: int = 5
    window_width: int = 500
    window_height: int = 200
    opacity: float = 0.85
    original_text_color: str = "#B7C4D8"
    show_original: bool = True
    draggable: bool = True
    mobile_url: str = ""


@dataclass
class HotkeyConfig:
    toggle_overlay: str = "ctrl+shift+t"
    toggle_translation: str = "ctrl+alt+s"
    clear_history: str = "ctrl+alt+c"


@dataclass
class AppConfig:
    audio: AudioConfig = None
    whisper: WhisperConfig = None
    translation: TranslationConfig = None
    overlay: OverlayConfig = None
    websocket: WebSocketConfig = None
    hotkeys: HotkeyConfig = None


class GameVoiceTranslator:

    def __init__(self, config_path: str = None):
        self._setup_logging()
        self.config = self._load_config(config_path)
        self._audio_capture: Optional[SystemAudioCapture] = None
        self._speech_recognizer: Optional[SpeechRecognizer] = None
        self._translator: Optional[GameTranslator] = None
        self._overlay = None
        self._mobile_server: Optional[MobileWebSocketManager] = None
        self._qt_app = None
        self._audio_timer = None
        self._mobile_loop: Optional[asyncio.AbstractEventLoop] = None
        self._mobile_thread: Optional[threading.Thread] = None
        self._translation_loop: Optional[asyncio.AbstractEventLoop] = None
        self._translation_thread: Optional[threading.Thread] = None
        self._running = False
        self._paused = False
        self._stopping = False
        self._processing_lock = threading.Lock()
        self._hotkey_handles = []
        self._stats = {
            "audio_chunks": 0, "speech_detected": 0,
            "transcriptions": 0, "translations": 0, "errors": 0
        }

    def _load_config(self, config_path: str = None) -> AppConfig:
        default_config = AppConfig(
            audio=AudioConfig(), whisper=WhisperConfig(),
            translation=TranslationConfig(), overlay=OverlayConfig(),
            websocket=WebSocketConfig(), hotkeys=HotkeyConfig()
        )
        if config_path and Path(config_path).exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for section in ["audio", "whisper", "translation", "overlay", "websocket", "hotkeys"]:
                    if section in data:
                        target = getattr(default_config, section)
                        for k, v in data[section].items():
                            if hasattr(target, k):
                                setattr(target, k, v)
                logger.info(f"已加载配置: {config_path}")
            except Exception as e:
                logger.error(f"配置加载失败: {e}")
        self._load_user_settings(default_config)
        return default_config

    def _load_user_settings(self, config: AppConfig):
        settings_path = self._runtime_dir() / "user_settings.json"
        if not settings_path.exists():
            return
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for section in ["audio", "overlay", "hotkeys"]:
                if section in data:
                    target = getattr(config, section)
                    for key, value in data[section].items():
                        if hasattr(target, key):
                            setattr(target, key, value)
            logger.info("已加载用户设置: {}", settings_path)
        except Exception as e:
            logger.warning("用户设置加载失败: {}", e)

    def _save_user_settings(self):
        settings_path = self._runtime_dir() / "user_settings.json"
        data = {
            "audio": {
                "input_device_index": self.config.audio.input_device_index,
                "input_device_name": self.config.audio.input_device_name,
            },
            "overlay": {
                "font_size": self.config.overlay.font_size,
                "text_color": self.config.overlay.text_color,
                "original_text_color": self.config.overlay.original_text_color,
                "window_width": self.config.overlay.window_width,
                "window_height": self.config.overlay.window_height,
                "opacity": self.config.overlay.opacity,
                "show_original": self.config.overlay.show_original,
            },
            "hotkeys": {
                "toggle_overlay": self.config.hotkeys.toggle_overlay,
                "toggle_translation": self.config.hotkeys.toggle_translation,
                "clear_history": self.config.hotkeys.clear_history,
            },
        }
        try:
            settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("用户设置保存失败: {}", e)

    def _setup_logging(self):
        logger.remove()
        log_target = sys.stderr if sys.stderr is not None else open(os.devnull, "w", encoding="utf-8")
        logger.add(
            log_target,
            level="INFO",
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        )

    def _runtime_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).parent
        return Path(__file__).parent

    def _write_crash_report(self, title: str, exc: Exception, detail: str = ""):
        report_path = self._runtime_dir() / "crash_report.txt"
        content = [
            title,
            time.strftime("%Y-%m-%d %H:%M:%S"),
            detail,
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        ]
        try:
            report_path.write_text("\n\n".join(part for part in content if part), encoding="utf-8")
        except Exception:
            pass

    def _show_error_dialog(self, title: str, message: str):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
        except Exception:
            pass

    def _on_speech_detected(self, audio_data: bytes):
        if self._paused or not self._running:
            return
        self._stats["speech_detected"] += 1
        threading.Thread(target=self._process_speech, args=(audio_data,), daemon=True).start()

    def _process_speech(self, audio_data: bytes):
        try:
            if self._paused or not self._running:
                return
            t0 = time.time()
            with self._processing_lock:
                result = self._speech_recognizer.transcribe_audio_bytes_with_language(
                    audio_data,
                    sample_rate=self.config.audio.sample_rate
                )
            text = result.text
            if not text or len(text.strip()) < 2:
                return
            self._stats["transcriptions"] += 1
            logger.info(
                "[识别] {} (lang={}, prob={:.2f}, {:.1f}s)",
                text[:80],
                result.language or "unknown",
                result.language_probability,
                time.time()-t0
            )

            t0 = time.time()
            translated = self._translate_text(text, result.language)
            if not translated or not translated.strip():
                translated = f"[翻译为空] {text}"
                logger.warning("翻译返回空结果，显示识别文本")
            if translated:
                self._stats["translations"] += 1
                logger.info(f"[翻译] {translated[:80]} ({time.time()-t0:.1f}s)")
                if self._overlay:
                    logger.info("推送翻译到浮窗")
                    self._overlay.add_translation(text, translated)
                if self._mobile_server:
                    self._broadcast_to_mobile(text, translated)
        except Exception as e:
            self._stats["errors"] += 1
            logger.exception(f"处理失败: {e}")

    def _setup_hotkeys(self):
        try:
            self._remove_hotkeys()
            hotkeys = self.config.hotkeys
            self._hotkey_handles = [
                keyboard.add_hotkey(hotkeys.toggle_overlay, self._toggle_overlay),
                keyboard.add_hotkey(hotkeys.clear_history, self._clear_history),
                keyboard.add_hotkey(hotkeys.toggle_translation, self._toggle_translation),
            ]
            logger.info(
                "热键已注册: {}/{}/{}",
                hotkeys.toggle_overlay,
                hotkeys.clear_history,
                hotkeys.toggle_translation,
            )
        except Exception as e:
            logger.exception(f"热键注册失败: {e}")

    def _remove_hotkeys(self):
        for handle in self._hotkey_handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                pass
        self._hotkey_handles = []

    def _toggle_overlay(self):
        if self._overlay:
            logger.info("触发热键: 切换浮窗")
            self._overlay._signals.toggle_visibility.emit()

    def _clear_history(self):
        if self._overlay:
            logger.info("触发热键: 清空历史")
            self._overlay._signals.clear_history.emit()

    def _toggle_translation(self):
        self._paused = not self._paused
        logger.info(f"翻译{'暂停' if self._paused else '恢复'}")

    def _start_qt(self):
        from PyQt5.QtWidgets import QApplication
        from overlay import GameOverlay

        self._qt_app = QApplication.instance() or QApplication(sys.argv)
        if self._mobile_server:
            self.config.overlay.mobile_url = self._mobile_server.get_mobile_url()
        self._overlay = GameOverlay(
            self.config.overlay,
            self.config.hotkeys,
            self.config.audio,
            self._list_audio_devices(),
            self._apply_overlay_settings,
            self._list_audio_devices,
        )
        self._overlay.show()
        logger.info("浮窗已启动")

    def _apply_overlay_settings(self, overlay_config: OverlayConfig, hotkey_config: HotkeyConfig, audio_config: AudioConfig):
        previous_device = (
            self.config.audio.input_device_index,
            self.config.audio.input_device_name,
        )
        self.config.overlay = overlay_config
        self.config.hotkeys = hotkey_config
        self.config.audio.input_device_index = audio_config.input_device_index
        self.config.audio.input_device_name = audio_config.input_device_name
        self._setup_hotkeys()
        self._save_user_settings()
        current_device = (
            self.config.audio.input_device_index,
            self.config.audio.input_device_name,
        )
        if self._running and current_device != previous_device:
            self._restart_audio_capture()
        logger.info(
            "浮窗设置已应用: opacity={:.2f}, text_color={}, show_original={}, audio_device={} {}, hotkeys={}/{}/{}",
            overlay_config.opacity,
            overlay_config.text_color,
            overlay_config.show_original,
            self.config.audio.input_device_index,
            self.config.audio.input_device_name,
            hotkey_config.toggle_overlay,
            hotkey_config.clear_history,
            hotkey_config.toggle_translation,
        )

    def _start_mobile(self):
        self._mobile_server = MobileWebSocketManager(self.config.websocket)
        def _run():
            self._mobile_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._mobile_loop)
            self._mobile_loop.run_until_complete(self._mobile_server.start_server())
        self._mobile_thread = threading.Thread(target=_run, daemon=True)
        self._mobile_thread.start()
        logger.info(f"手机端: {self._mobile_server.get_mobile_url()}")

    def _start_translation_loop(self):
        def _run():
            self._translation_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._translation_loop)
            self._translation_loop.run_forever()

        self._translation_thread = threading.Thread(target=_run, daemon=True)
        self._translation_thread.start()
        while not self._translation_loop or not self._translation_loop.is_running():
            time.sleep(0.01)
        logger.info("翻译事件循环已启动")

    def _start_audio_capture(self):
        if self._audio_capture:
            self._audio_capture.stop()
            self._audio_capture = None
        self._audio_capture = SystemAudioCapture(self.config.audio)
        self._audio_capture.set_speech_callback(self._on_speech_detected)
        self._audio_capture.start()

    def _restart_audio_capture(self):
        try:
            self._start_audio_capture()
            if self._overlay:
                self._overlay.add_translation(
                    "音频设备已切换",
                    f"当前设备: [{self.config.audio.input_device_index}] {self.config.audio.input_device_name}"
                    if self.config.audio.input_device_index is not None
                    else "当前设备: 自动选择"
                )
        except Exception as e:
            self._stats["errors"] += 1
            self._show_audio_startup_warning(e)

    def _list_audio_devices(self):
        try:
            return list_input_devices()
        except Exception as e:
            self._write_crash_report("音频设备枚举失败", e)
            logger.warning("音频设备枚举失败: {}", e)
            return []

    def _show_audio_startup_warning(self, exc: Exception):
        detail = (
            "音频采集启动失败，程序已继续运行，但暂时不会识别游戏声音。\n"
            "请在 Windows 声音设置里启用“立体声混音 / Stereo Mix”，"
            "或安装/选择可采集系统声音的虚拟声卡后重启本程序。\n"
            f"错误: {exc}"
        )
        self._write_crash_report("音频采集启动失败", exc, detail)
        logger.exception(detail)
        if self._overlay:
            self._overlay.add_translation("音频采集未启动", detail)

    def _translate_text(self, text: str, detected_language: str = "") -> str:
        if self._translation_loop and self._translation_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._translator.translate(text, detected_language),
                self._translation_loop
            )
            return future.result(timeout=45)
        return asyncio.run(self._translator.translate(text, detected_language))

    def _broadcast_to_mobile(self, original: str, translated: str):
        if not self._mobile_server:
            return
        if self._mobile_loop and self._mobile_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._mobile_server.broadcast_translation(original, translated),
                self._mobile_loop
            )
        else:
            asyncio.run(self._mobile_server.broadcast_translation(original, translated))

    def _process_audio_tick(self):
        if not self._running or self._paused:
            return
        if self._audio_capture:
            self._audio_capture.process_audio()

    def _print_banner(self):
        if sys.stdout is None:
            return
        hotkeys = self.config.hotkeys
        print("""
╔══════════════════════════════════════════════╗
║       游戏语音实时翻译器  v1.0            ║
╠══════════════════════════════════════════════╣
║  热键:                                       ║
║    {toggle_overlay:<14} 切换浮窗显示/隐藏       ║
║    {clear_history:<14} 清空翻译历史            ║
║    {toggle_translation:<14} 暂停/恢复翻译       ║
╠══════════════════════════════════════════════╣
║  手机端: {url}  ║
╠══════════════════════════════════════════════╣
║  按 Ctrl+C 停止                               ║
╚══════════════════════════════════════════════╝
""".format(
            toggle_overlay=hotkeys.toggle_overlay,
            clear_history=hotkeys.clear_history,
            toggle_translation=hotkeys.toggle_translation,
            url=self._mobile_server.get_mobile_url()
        ))

    def start(self):
        logger.info("正在启动...")
        try:
            self._speech_recognizer = SpeechRecognizer(self.config.whisper)
            self._translator = GameTranslator(self.config.translation)
            self._start_translation_loop()
            self._speech_recognizer.initialize()

            self._start_mobile()
            self._start_qt()
            self._setup_hotkeys()
            signal.signal(signal.SIGINT, lambda s, f: self.stop())
            signal.signal(signal.SIGTERM, lambda s, f: self.stop())

            self._running = True
            self._print_banner()

            try:
                self._start_audio_capture()
            except Exception as e:
                self._stats["errors"] += 1
                self._show_audio_startup_warning(e)

            from PyQt5.QtCore import QTimer

            self._audio_timer = QTimer()
            self._audio_timer.timeout.connect(self._process_audio_tick)
            self._audio_timer.start(100)

            self._qt_app.exec_()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            self._write_crash_report("启动失败", e)
            logger.exception(f"启动失败: {e}")
            self._show_error_dialog(
                "Game Voice Translator 启动失败",
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
        if self._audio_capture:
            self._audio_capture.stop()
        self._remove_hotkeys()
        if self._speech_recognizer:
            self._speech_recognizer.cleanup()
        if self._mobile_server and self._mobile_loop and self._mobile_loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._mobile_server.stop_server(),
                    self._mobile_loop
                )
                future.result(timeout=3)
            except Exception as e:
                logger.warning(f"手机端服务停止失败: {e}")
        if self._translator:
            try:
                if self._translation_loop and self._translation_loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        self._translator.close(),
                        self._translation_loop
                    )
                    future.result(timeout=3)
                else:
                    asyncio.run(self._translator.close())
            except Exception as e:
                logger.warning(f"翻译器关闭失败: {e}")
        if self._translation_loop and self._translation_loop.is_running():
            self._translation_loop.call_soon_threadsafe(self._translation_loop.stop)
        if self._translation_thread and self._translation_thread.is_alive():
            self._translation_thread.join(timeout=3)
        if self._translation_loop and not self._translation_loop.is_closed():
            self._translation_loop.close()
        if self._overlay:
            self._overlay.close()
        if self._qt_app:
            self._qt_app.quit()

        if sys.stdout is not None:
            print(f"\n翻译统计: 识别 {self._stats['transcriptions']} 条, "
                  f"翻译 {self._stats['translations']} 条, 错误 {self._stats['errors']} 次")
        logger.info("已停止")


def main():
    config_path = None
    default_config = Path(__file__).parent / "config.json"
    if default_config.exists():
        config_path = str(default_config)

    app = GameVoiceTranslator(config_path)
    app.start()


if __name__ == "__main__":
    main()
