let ws = null;
let reconnectAttempts = 0;

const maxReconnectAttempts = 10;
const reconnectDelay = 3000;

function connect() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = function () {
        console.log("WebSocket 连接成功");
        updateStatus("connected", "已连接");
        updateConnectionInfo("已连接到服务器");
        reconnectAttempts = 0;
    };

    ws.onmessage = function (event) {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (error) {
            console.error("无法解析 WebSocket 消息:", error);
            return;
        }

        if (data.type === "translation") {
            addTranslation(data);
            updateLastUpdate();
        } else if (data.type === "connected") {
            updateConnectionInfo(data.message || "已连接到服务器");
        }
    };

    ws.onerror = function (event) {
        console.error("WebSocket 错误:", event);
        updateStatus("disconnected", "连接错误");
    };

    ws.onclose = function () {
        console.log("WebSocket 连接关闭");
        updateStatus("disconnected", "连接断开");
        updateConnectionInfo("连接断开，正在重连...");

        if (reconnectAttempts < maxReconnectAttempts) {
            reconnectAttempts += 1;
            const delay = reconnectDelay * Math.min(reconnectAttempts, 5);
            console.log(`将在 ${delay}ms 后重连... (尝试 ${reconnectAttempts}/${maxReconnectAttempts})`);
            setTimeout(connect, delay);
        }
    };
}

function addTranslation(data) {
    const container = document.getElementById("translationContainer");
    const emptyState = document.getElementById("emptyState");

    if (emptyState) {
        emptyState.remove();
    }

    const item = document.createElement("div");
    item.className = "translation-item";
    item.innerHTML = `
        <div class="original">${escapeHtml(data.original || "")}</div>
        <div class="translated">${escapeHtml(data.translated || "")}</div>
        <div class="timestamp">${formatTime(data.timestamp || Date.now() / 1000)}</div>
    `;

    container.insertBefore(item, container.firstChild);

    const items = container.querySelectorAll(".translation-item");
    if (items.length > 20) {
        container.removeChild(items[items.length - 1]);
    }

    container.scrollTop = 0;
}

function clearTranslations() {
    const container = document.getElementById("translationContainer");
    container.innerHTML = '<div class="empty-state" id="emptyState">等待翻译结果...</div>';
}

function reconnect() {
    if (ws) {
        ws.onclose = null;
        ws.close();
        ws = null;
    }
    reconnectAttempts = 0;
    connect();
}

function updateStatus(status, text) {
    const statusEl = document.getElementById("status");
    statusEl.className = `status ${status}`;
    statusEl.textContent = text;
}

function updateConnectionInfo(text) {
    document.getElementById("connectionInfo").textContent = text;
}

function updateLastUpdate() {
    const now = new Date();
    document.getElementById("lastUpdate").textContent = now.toLocaleTimeString("zh-CN");
}

function formatTime(timestamp) {
    const date = new Date(timestamp * 1000);
    return date.toLocaleTimeString("zh-CN");
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

window.addEventListener("load", function () {
    document.getElementById("clearButton").addEventListener("click", clearTranslations);
    document.getElementById("reconnectButton").addEventListener("click", reconnect);
    connect();
});

window.addEventListener("beforeunload", function () {
    if (ws) {
        ws.close();
    }
});
