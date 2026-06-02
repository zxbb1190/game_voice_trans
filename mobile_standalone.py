"""
手机端服务器 - 独立启动
"""

import asyncio
import json
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI(title="Game Voice Translator Mobile")

connections = set()

@app.get("/")
async def index():
    return {"status": "running", "service": "game_voice_translator"}

@app.get("/mobile")
async def mobile_page():
    html = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🎮 游戏语音实时翻译</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #0f0f23 0%, #1a1a2e 100%);
                color: #00ff00; min-height: 100vh; padding: 20px;
            }
            .container { max-width: 600px; margin: 0 auto; }
            header {
                text-align: center; margin-bottom: 30px; padding: 20px;
                background: rgba(0,20,0,0.3); border-radius: 15px;
                border: 1px solid #00ff00; box-shadow: 0 0 20px rgba(0,255,0,0.2);
            }
            h1 { font-size: 24px; margin-bottom: 10px; text-shadow: 0 0 10px #00ff00; }
            .status {
                display: inline-block; padding: 5px 15px; color: white;
                border-radius: 20px; font-size: 14px; margin-top: 10px;
            }
            .status.connected { background: #0a0; }
            .status.disconnected { background: #a00; }
            .translation-container {
                background: rgba(0,0,0,0.7); border-radius: 10px;
                border: 1px solid #00ff00; padding: 20px; margin-bottom: 20px;
                min-height: 200px; overflow-y: auto;
                box-shadow: 0 0 15px rgba(0,255,0,0.1);
            }
            .translation-item {
                padding: 12px; margin-bottom: 10px;
                background: rgba(0,30,0,0.3); border-radius: 8px;
                border-left: 4px solid #00ff00;
                animation: fadeIn 0.3s ease-in;
            }
            .original { color: #aaa; font-size: 14px; margin-bottom: 5px; font-style: italic; }
            .translated { color: #00ff00; font-size: 16px; font-weight: bold; }
            .timestamp { color: #666; font-size: 12px; text-align: right; margin-top: 5px; }
            .controls { display: flex; gap: 10px; margin-top: 20px; }
            button {
                flex: 1; padding: 12px;
                background: linear-gradient(135deg, #00aa00, #008800);
                color: white; border: none; border-radius: 8px;
                font-size: 16px; cursor: pointer; transition: all 0.3s;
            }
            button:hover { background: linear-gradient(135deg, #00cc00, #00aa00); transform: translateY(-2px); }
            .clear-btn { background: linear-gradient(135deg, #aa0000, #880000); }
            .clear-btn:hover { background: linear-gradient(135deg, #cc0000, #aa0000); }
            @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
            .empty-state { text-align: center; color: #666; padding: 40px; font-size: 16px; }
            .connection-info { text-align: center; color: #888; font-size: 12px; margin-top: 20px; }
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
                <div class="empty-state" id="emptyState">⏳ 等待翻译结果...</div>
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
            function connect() {
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const host = window.location.host;
                ws = new WebSocket(`${protocol}//${host}/ws`);
                ws.onopen = function() {
                    document.getElementById('status').className = 'status connected';
                    document.getElementById('status').textContent = '已连接';
                    document.getElementById('connectionInfo').textContent = '已连接到服务器';
                };
                ws.onmessage = function(event) {
                    const data = JSON.parse(event.data);
                    const container = document.getElementById('translationContainer');
                    const emptyState = document.getElementById('emptyState');
                    if (emptyState) emptyState.remove();
                    const item = document.createElement('div');
                    item.className = 'translation-item';
                    item.innerHTML = `<div class="original">${escapeHtml(data.original)}</div>
                        <div class="translated">${escapeHtml(data.translated)}</div>
                        <div class="timestamp">${new Date(data.timestamp*1000).toLocaleTimeString('zh-CN')}</div>`;
                    container.insertBefore(item, container.firstChild);
                    const items = container.querySelectorAll('.translation-item');
                    if (items.length > 20) container.removeChild(items[items.length-1]);
                    container.scrollTop = 0;
                    document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString('zh-CN');
                };
                ws.onclose = function() {
                    document.getElementById('status').className = 'status disconnected';
                    document.getElementById('status').textContent = '连接断开';
                    setTimeout(connect, 3000);
                };
                ws.onerror = function() {
                    document.getElementById('status').className = 'status disconnected';
                    document.getElementById('status').textContent = '连接错误';
                };
            }
            function clearTranslations() {
                document.getElementById('translationContainer').innerHTML = '<div class="empty-state" id="emptyState">⏳ 等待翻译结果...</div>';
            }
            function reconnect() { if (ws) ws.close(); connect(); }
            function escapeHtml(text) { const div = document.createElement('div'); div.textContent = text; return div.innerHTML; }
            window.addEventListener('load', connect);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connections.add(websocket)
    try:
        await websocket.send_json({"type": "connected", "message": "已连接", "timestamp": time.time()})
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        connections.remove(websocket)

async def broadcast(original: str, translated: str):
    msg = {"type": "translation", "original": original, "translated": translated, "timestamp": time.time()}
    for conn in list(connections):
        try:
            await conn.send_json(msg)
        except:
            pass

if __name__ == "__main__":
    print("📱 手机端服务器启动: http://0.0.0.0:8765/mobile")
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")