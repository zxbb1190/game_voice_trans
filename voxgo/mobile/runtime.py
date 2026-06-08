import asyncio
import threading

from loguru import logger

from voxgo.mobile.server import MobileWebSocketManager


class MobileRuntime:
    def __init__(self, notify_user, write_crash_report, is_stopping):
        self._notify_user = notify_user
        self._write_crash_report = write_crash_report
        self._is_stopping = is_stopping
        self.server = None
        self.loop = None
        self.thread = None
        self.start_error = None

    def start(self, websocket_config):
        self.server = MobileWebSocketManager(websocket_config)
        self.start_error = None

        def _run():
            loop = asyncio.new_event_loop()
            self.loop = loop
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.server.start_server())
            except BaseException as exc:
                self.start_error = exc
                if not self._is_stopping():
                    self._write_crash_report("手机端服务启动失败", exc)
                    logger.exception("手机端服务启动失败: {}", exc)
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        self.thread = threading.Thread(target=_run, name="mobile-server", daemon=True)
        self.thread.start()
        if self.server.wait_until_ready(5):
            logger.info("手机端: {}", self.get_mobile_url())
            return

        if self.start_error:
            message = str(self.start_error)
        elif self.thread.is_alive():
            message = f"手机端服务启动超时，请检查 {websocket_config.port} 端口或防火墙"
        else:
            message = "手机端服务启动失败，未能监听端口"
        logger.warning(message)
        self._notify_user("手机端未启动", message, "错误")

    def get_mobile_url(self) -> str:
        if not self.server:
            return ""
        return self.server.get_mobile_url()

    def broadcast_translation(self, original: str, translated: str):
        if not self.server:
            return
        if self.loop and self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self.server.broadcast_translation(original, translated),
                self.loop,
            )
            future.add_done_callback(self._handle_broadcast_done)
            return
        logger.debug("手机端事件循环未运行，跳过本次手机端推送")

    def _handle_broadcast_done(self, future):
        try:
            future.result()
        except Exception as exc:
            logger.warning("手机端推送失败: {}", exc)

    def stop(self):
        if not self.server or not self.loop or not self.loop.is_running():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self.server.stop_server(), self.loop)
            future.result(timeout=3)
        except Exception as exc:
            logger.warning("手机端服务停止失败: {}", exc)
