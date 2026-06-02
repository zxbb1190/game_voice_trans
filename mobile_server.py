"""
WebSocket 服务器
用于向手机端推送实时翻译结果
"""

import asyncio
import json
import socket
import time
from dataclasses import dataclass
from typing import Set, Dict, Any

import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from loguru import logger


@dataclass
class WebSocketConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    mobile_url: str = "http://localhost:8765/mobile"


class MobileWebSocketManager:
    """手机端 WebSocket 连接管理器"""

    def __init__(self, config: WebSocketConfig = None):
        self.config = config or WebSocketConfig()
        self._connections: Set[WebSocket] = set()
        self._app = FastAPI(title="Game Voice Translator Mobile")
        self._server = None
        self._setup_routes()

    def _setup_routes(self):
        """设置 FastAPI 路由"""

        @self._app.get("/")
        async def index():
            return {"status": "running", "service": "game_voice_translator"}

        @self._app.get("/mobile")
        async def mobile_page():
            """手机端页面"""
            html = """
            <!DOCTYPE html>
            <html lang="zh-CN">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>🎮 游戏语音实时翻译</title>
                <style>
                    * {
                        margin: 0;
                        padding: 0;
                        box-sizing: border-box;
                    }
                    body {
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                        background: linear-gradient(135deg, #0f0f23 0%, #1a1a2e 100%);
                        color: #00ff00;
                        min-height: 100vh;
                        padding: 20px;
                    }
                    .container {
                        max-width: 600px;
                        margin: 0 auto;
                    }
                    header {
                        text-align: center;
                        margin-bottom: 30px;
                        padding: 20px;
                        background: rgba(0, 20, 0, 0.3);
                        border-radius: 15px;
                        border: 1px solid #00ff00;
                        box-shadow: 0 0 20px rgba(0, 255, 0, 0.2);
                    }
                    h1 {
                        font-size: 24px;
                        margin-bottom: 10px;
                        text-shadow: 0 0 10px #00ff00;
                    }
                    .status {
                        display: inline-block;
                        padding: 5px 15px;
                        background: #0a0;
                        color: white;
                        border-radius: 20px;
                        font-size: 14px;
                        margin-top: 10px;
                    }
                    .status.connected {
                        background: #0a0;
                    }
                    .status.disconnected {
                        background: #a00;
                    }
                    .translation-container {
                        background: rgba(0, 0, 0, 0.7);
                        border-radius: 10px;
                        border: 1px solid #00ff00;
                        padding: 20px;
                        margin-bottom: 20px;
                        min-height: 200px;
                        overflow-y: auto;
                        box-shadow: 0 0 15px rgba(0, 255, 0, 0.1);
                    }
                    .translation-item {
                        padding: 12px;
                        margin-bottom: 10px;
                        background: rgba(0, 30, 0, 0.3);
                        border-radius: 8px;
                        border-left: 4px solid #00ff00;
                        animation: fadeIn 0.3s ease-in;
                    }
                    .original {
                        color: #aaa;
                        font-size: 14px;
                        margin-bottom: 5px;
                        font-style: italic;
                    }
                    .translated {
                        color: #00ff00;
                        font-size: 16px;
                        font-weight: bold;
                    }
                    .timestamp {
                        color: #666;
                        font-size: 12px;
                        text-align: right;
                        margin-top: 5px;
                    }
                    .controls {
                        display: flex;
                        gap: 10px;
                        margin-top: 20px;
                    }
                    button {
                        flex: 1;
                        padding: 12px;
                        background: linear-gradient(135deg, #00aa00, #008800);
                        color: white;
                        border: none;
                        border-radius: 8px;
                        font-size: 16px;
                        cursor: pointer;
                        transition: all 0.3s;
                    }
                    button:hover {
                        background: linear-gradient(135deg, #00cc00, #00aa00);
                        transform: translateY(-2px);
                    }
                    button:active {
                        transform: translateY(0);
                    }
                    .clear-btn {
                        background: linear-gradient(135deg, #aa0000, #880000);
                    }
                    .clear-btn:hover {
                        background: linear-gradient(135deg, #cc0000, #aa0000);
                    }
                    @keyframes fadeIn {
                        from { opacity: 0; transform: translateY(10px); }
                        to { opacity: 1; transform: translateY(0); }
                    }
                    .empty-state {
                        text-align: center;
                        color: #666;
                        padding: 40px;
                        font-size: 16px;
                    }
                    .connection-info {
                        text-align: center;
                        color: #888;
                        font-size: 12px;
                        margin-top: 20px;
                    }
                </style>
            </head>
            <body>
                <div class="container">
                    <header>
                        <h1>🎮 游戏语音实时翻译</h1>
                        <p>实时接收游戏内语音翻译结果</p>
                        <div id="status" class="status disconnected">未连接</div>
                    </header>
                    
                    <div class="translation-container" id="translationContainer">
                        <div class="empty-state" id="emptyState">
                            ⏳ 等待翻译结果...
                        </div>
                    </div>
                    
                    <div class="controls">
                        <button onclick="clearTranslations()">清空记录</button>
                        <button class="clear-btn" onclick="reconnect()">重新连接</button>
                    </div>
                    
                    <div class="connection-info">
                        <p>连接状态: <span id="connectionInfo">正在连接...</span></p>
                        <p>最后更新: <span id="lastUpdate">-</span></p>
                    </div>
                </div>
                
                <script>
                    let ws = null;
                    let reconnectAttempts = 0;
                    const maxReconnectAttempts = 10;
                    const reconnectDelay = 3000;
                    
                    function connect() {
                        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                        const host = window.location.host;
                        const wsUrl = `${protocol}//${host}/ws`;
                        
                        ws = new WebSocket(wsUrl);
                        
                        ws.onopen = function(event) {
                            console.log('WebSocket 连接成功');
                            updateStatus('connected', '已连接');
                            reconnectAttempts = 0;
                        };
                        
                        ws.onmessage = function(event) {
                            const data = JSON.parse(event.data);
                            addTranslation(data);
                            updateLastUpdate();
                        };
                        
                        ws.onerror = function(event) {
                            console.error('WebSocket 错误:', event);
                            updateStatus('disconnected', '连接错误');
                        };
                        
                        ws.onclose = function(event) {
                            console.log('WebSocket 连接关闭');
                            updateStatus('disconnected', '连接断开');
                            
                            if (reconnectAttempts < maxReconnectAttempts) {
                                reconnectAttempts++;
                                const delay = reconnectDelay * Math.min(reconnectAttempts, 5);
                                console.log(`将在 ${delay}ms 后重连... (尝试 ${reconnectAttempts}/${maxReconnectAttempts})`);
                                setTimeout(connect, delay);
                            }
                        };
                    }
                    
                    function addTranslation(data) {
                        const container = document.getElementById('translationContainer');
                        const emptyState = document.getElementById('emptyState');
                        
                        if (emptyState) {
                            emptyState.remove();
                        }
                        
                        const item = document.createElement('div');
                        item.className = 'translation-item';
                        item.innerHTML = `
                            <div class="original">${escapeHtml(data.original)}</div>
                            <div class="translated">${escapeHtml(data.translated)}</div>
                            <div class="timestamp">${formatTime(data.timestamp)}</div>
                        `;
                        
                        container.insertBefore(item, container.firstChild);
                        
                        // 限制显示数量
                        const items = container.querySelectorAll('.translation-item');
                        if (items.length > 20) {
                            container.removeChild(items[items.length - 1]);
                        }
                        
                        // 滚动到顶部
                        container.scrollTop = 0;
                    }
                    
                    function clearTranslations() {
                        const container = document.getElementById('translationContainer');
                        container.innerHTML = '<div class="empty-state" id="emptyState">⏳ 等待翻译结果...</div>';
                    }
                    
                    function reconnect() {
                        if (ws) {
                            ws.close();
                        }
                        connect();
                    }
                    
                    function updateStatus(status, text) {
                        const statusEl = document.getElementById('status');
                        statusEl.className = `status ${status}`;
                        statusEl.textContent = text;
                    }
                    
                    function updateConnectionInfo(text) {
                        document.getElementById('connectionInfo').textContent = text;
                    }
                    
                    function updateLastUpdate() {
                        const now = new Date();
                        document.getElementById('lastUpdate').textContent = 
                            now.toLocaleTimeString('zh-CN');
                    }
                    
                    function formatTime(timestamp) {
                        const date = new Date(timestamp * 1000);
                        return date.toLocaleTimeString('zh-CN');
                    }
                    
                    function escapeHtml(text) {
                        const div = document.createElement('div');
                        div.textContent = text;
                        return div.innerHTML;
                    }
                    
                    // 页面加载时连接
                    window.addEventListener('load', function() {
                        connect();
                        updateConnectionInfo('已连接到服务器');
                    });
                    
                    // 防止页面关闭时连接未关闭
                    window.addEventListener('beforeunload', function() {
                        if (ws) {
                            ws.close();
                        }
                    });
                </script>
            </body>
            </html>
            """
            return HTMLResponse(content=html)

        @self._app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            self._connections.add(websocket)
            logger.info(f"手机端连接: {websocket.client.host}")

            try:
                # 发送连接确认
                await websocket.send_json({
                    "type": "connected",
                    "message": "已连接到游戏语音翻译服务器",
                    "timestamp": time.time()
                })

                # 保持连接
                while True:
                    data = await websocket.receive_text()
                    # 处理客户端消息（如果需要）
                    try:
                        msg = json.loads(data)
                        if msg.get("type") == "ping":
                            await websocket.send_json({"type": "pong"})
                    except:
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
            "timestamp": time.time()
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
            log_level="info"
        )
        self._server = uvicorn.Server(config)
        await self._server.serve()

    async def stop_server(self):
        """停止服务器"""
        # 关闭所有连接
        for connection in list(self._connections):
            try:
                await connection.close()
            except:
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

    def _get_lan_ip(self) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
        except Exception:
            return "127.0.0.1"


# 快速启动函数
async def start_mobile_server(host: str = "0.0.0.0", port: int = 8765):
    """快速启动手机端服务器"""
    manager = MobileWebSocketManager(WebSocketConfig(host=host, port=port))
    logger.info(f"手机端服务器启动: {manager.get_mobile_url()}")
    await manager.start_server()
