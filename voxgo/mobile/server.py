"""
Mobile HTTP/WebSocket server for mirroring translations to a phone browser.
"""

import ipaddress
import json
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from voxgo.app_info import APP_NAME, SERVICE_NAME


@dataclass
class WebSocketConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    mobile_url: str = "http://localhost:8765/mobile"


def mobile_static_dir() -> Path:
    """Return the bundled mobile web assets directory."""
    if getattr(sys, "frozen", False):
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates = [
            bundle_dir / "voxgo" / "mobile" / "static",
            bundle_dir / "mobile" / "static",
            Path(sys.executable).parent / "voxgo" / "mobile" / "static",
            Path(sys.executable).parent / "mobile" / "static",
        ]
    else:
        candidates = [Path(__file__).resolve().parent / "static"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


class MobileWebSocketManager:
    """手机端 WebSocket 连接管理器"""

    def __init__(self, config: WebSocketConfig = None):
        self.config = config or WebSocketConfig()
        self._connections: Set[WebSocket] = set()
        self._app = FastAPI(title=f"{APP_NAME} Mobile")
        self._server = None
        self._static_dir = mobile_static_dir()
        self._app.mount(
            "/mobile/static",
            StaticFiles(directory=str(self._static_dir), check_dir=False),
            name="mobile-static",
        )
        self._setup_routes()

    def _setup_routes(self):
        """设置 FastAPI 路由"""

        @self._app.get("/")
        async def index():
            return {"status": "running", "service": SERVICE_NAME}

        @self._app.get("/mobile")
        async def mobile_page():
            """手机端页面"""
            index_file = self._static_dir / "index.html"
            if not index_file.exists():
                logger.error("手机端页面文件缺失: {}", index_file)
                raise HTTPException(status_code=500, detail="Mobile page asset is missing.")
            return FileResponse(str(index_file))

        @self._app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            self._connections.add(websocket)
            logger.info(f"手机端连接: {websocket.client.host}")

            try:
                await websocket.send_json({
                    "type": "connected",
                    "message": "已连接到 VoxGo 服务器",
                    "timestamp": time.time(),
                })

                while True:
                    data = await websocket.receive_text()
                    try:
                        msg = json.loads(data)
                        if msg.get("type") == "ping":
                            await websocket.send_json({"type": "pong"})
                    except Exception:
                        pass

            except WebSocketDisconnect:
                logger.info(f"手机端断开连接: {websocket.client.host}")
            finally:
                self._connections.discard(websocket)

    async def broadcast_translation(self, original: str, translated: str):
        """向所有连接的手机端广播翻译结果"""
        if not self._connections:
            logger.info("手机端无连接，跳过推送")
            return

        message = {
            "type": "translation",
            "original": original,
            "translated": translated,
            "timestamp": time.time(),
        }

        logger.info(f"推送翻译到手机端: {len(self._connections)} 个连接")
        disconnected = set()
        for connection in self._connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"发送到手机端失败: {e}")
                disconnected.add(connection)

        for connection in disconnected:
            self._connections.discard(connection)

    async def get_connection_count(self) -> int:
        """获取当前连接数"""
        return len(self._connections)

    async def start_server(self):
        """启动 WebSocket 服务器"""
        import uvicorn

        config = uvicorn.Config(
            self._app,
            host=self.config.host,
            port=self.config.port,
            log_level="info",
            log_config=None,
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        try:
            await self._server.serve()
        except SystemExit as exc:
            raise RuntimeError(f"手机端服务启动失败，端口 {self.config.port} 可能被占用") from exc

    async def stop_server(self):
        """停止服务器"""
        for connection in list(self._connections):
            try:
                await connection.close()
            except Exception:
                pass
        self._connections.clear()
        if self._server:
            self._server.should_exit = True

    def get_mobile_url(self) -> str:
        """获取手机端访问 URL"""
        if self.config.mobile_url and "localhost" not in self.config.mobile_url:
            return self.config.mobile_url
        host = self.config.host
        if host in ("0.0.0.0", "::", "", "localhost", "127.0.0.1"):
            host = self._get_lan_ip()
        return f"http://{host}:{self.config.port}/mobile"

    def is_running(self) -> bool:
        """Return whether the HTTP/WebSocket server is currently accepting connections."""
        return self._can_connect()

    def wait_until_ready(self, timeout_seconds: float = 5.0) -> bool:
        """Wait briefly for uvicorn to bind the listening socket."""
        deadline = time.time() + max(0.1, timeout_seconds)
        while time.time() < deadline:
            if self._can_connect():
                return True
            time.sleep(0.05)
        return False

    def _can_connect(self) -> bool:
        host = self.config.host
        if host in ("", "0.0.0.0", "::"):
            host = "127.0.0.1"
        try:
            with socket.create_connection((host, int(self.config.port)), timeout=0.3):
                return True
        except OSError:
            return False

    def _get_lan_ip(self) -> str:
        candidates = self._lan_ip_candidates()
        for candidate in candidates:
            if self._is_usable_ip(candidate):
                return candidate
        return "127.0.0.1"

    def _lan_ip_candidates(self):
        """Return likely LAN addresses, with the active default-route address first."""
        candidates = []
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                candidates.append(sock.getsockname()[0])
        except Exception:
            pass

        try:
            hostname = socket.gethostname()
            for item in socket.getaddrinfo(hostname, None, socket.AF_INET):
                candidates.append(item[4][0])
        except Exception:
            pass

        unique_candidates = []
        for candidate in candidates:
            if candidate not in unique_candidates:
                unique_candidates.append(candidate)
        return unique_candidates

    def _is_usable_ip(self, value: str) -> bool:
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            return False
        return not (ip.is_loopback or ip.is_link_local or ip.is_unspecified)


async def start_mobile_server(host: str = "0.0.0.0", port: int = 8765):
    """快速启动手机端服务器"""
    manager = MobileWebSocketManager(WebSocketConfig(host=host, port=port))
    logger.info(f"手机端服务器启动: {manager.get_mobile_url()}")
    await manager.start_server()
