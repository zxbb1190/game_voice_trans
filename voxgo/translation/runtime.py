import asyncio
import threading
import time
from typing import Optional

from loguru import logger

from voxgo.runtime.events import TranslationReady
from voxgo.runtime.work_items import LatencyTrace
from voxgo.translation import GameTranslator, TranslationConfig


class TranslationRuntime:
    def __init__(self, event_bus, stats, latency_traces, overlay_getter):
        self._event_bus = event_bus
        self._stats = stats
        self._latency_traces = latency_traces
        self._overlay_getter = overlay_getter
        self.client: Optional[GameTranslator] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None

    def initialize(self, config: TranslationConfig):
        self.client = GameTranslator(config)
        self.start_loop()

    def update_config(self, config: TranslationConfig):
        if self.client:
            self.client.config = config

    def clear_context(self):
        if self.client:
            self.client.clear_context()

    def start_loop(self):
        if self.loop and self.loop.is_running():
            return

        def _run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()

        self.thread = threading.Thread(target=_run, daemon=True)
        self.thread.start()
        while not self.loop or not self.loop.is_running():
            time.sleep(0.01)
        logger.info("翻译事件循环已启动")

    def translate_async(
        self,
        item_id: str,
        text: str,
        detected_language: str = "",
        trace: Optional[LatencyTrace] = None,
    ):
        if self.loop and self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._translate_and_publish(item_id, text, detected_language, trace),
                self.loop,
            )
            future.add_done_callback(
                lambda done, current_item_id=item_id: self._handle_task_done(
                    current_item_id,
                    done,
                )
            )
            return

        def _run():
            try:
                self._run_in_private_event_loop(
                    self._translate_and_publish(item_id, text, detected_language, trace)
                )
            except Exception as exc:
                self._handle_error(item_id, exc)

        threading.Thread(target=_run, name="translation-fallback", daemon=True).start()

    def close(self, cleanup_allowed: bool = True):
        if not cleanup_allowed:
            return
        if self.client:
            try:
                if self.loop and self.loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(self.client.close(), self.loop)
                    future.result(timeout=3)
                else:
                    self._run_in_private_event_loop(self.client.close())
            except Exception as exc:
                logger.warning("翻译器关闭失败: {}", exc)
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        if self.loop and not self.loop.is_closed():
            self.loop.close()

    @staticmethod
    def _run_in_private_event_loop(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    async def _translate_and_publish(
        self,
        item_id: str,
        text: str,
        detected_language: str = "",
        trace: Optional[LatencyTrace] = None,
    ):
        if not self.client:
            raise RuntimeError("翻译器未初始化")
        t0 = time.time()
        trace = trace or self._latency_traces.get(item_id)
        if trace:
            trace.translation_started_at = t0
        result = await self.client.translate_result(text, detected_language)
        translated = "" if result is None else result.translated
        if trace:
            trace.translation_finished_at = time.time()
        if not translated or not translated.strip():
            translated = f"[翻译为空] {text}"
            logger.warning("翻译返回空结果，显示识别文本")
        source_lang = getattr(result, "source_lang", detected_language or "")
        target_lang = getattr(result, "target_lang", "")

        elapsed = time.time() - t0
        logger.info("[翻译] {} ({:.1f}s)", translated[:80], elapsed)
        self._event_bus.publish(
            TranslationReady(
                original=text,
                translated=translated,
                source_lang=source_lang,
                target_lang=target_lang,
                trace_id=item_id,
            )
        )

    def _handle_task_done(self, item_id: str, future):
        try:
            future.result()
        except Exception as exc:
            logger.exception("异步翻译任务失败: {}", exc)
            self._handle_error(item_id, exc, already_logged=True)

    def _handle_error(self, item_id: str, exc: Exception, already_logged: bool = False):
        if not already_logged:
            logger.exception("翻译任务失败: {}", exc)
        self._stats["errors"] += 1
        trace = self._latency_traces.get(item_id)
        if trace and not trace.translation_finished_at:
            trace.translation_finished_at = time.time()
        overlay = self._overlay_getter()
        if overlay:
            overlay.update_translation(item_id, f"[翻译失败] {str(exc)[:180]}")
