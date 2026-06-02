"""
Compare SiliconFlow model latency for short translation prompts.
"""

import asyncio
import json
import time
from pathlib import Path

import aiohttp


CANDIDATES = [
    "Qwen/Qwen3.5-4B",
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2.5-3B-Instruct",
    "Qwen/Qwen2.5-1.5B-Instruct",
    "THUDM/glm-4-9b-chat",
    "deepseek-ai/DeepSeek-V3",
]


async def try_model(session, endpoint, api_key, model, text):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是实时字幕翻译器。只输出译文。"},
            {"role": "user", "content": f"Translate to English: {text}"},
        ],
        "max_tokens": 80,
        "temperature": 0.1,
        "stream": False,
        "enable_thinking": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    started = time.time()
    try:
        async with session.post(endpoint, headers=headers, json=payload) as response:
            body = await response.text()
            elapsed = time.time() - started
            if response.status != 200:
                print(f"{model}: HTTP {response.status} in {elapsed:.2f}s {body[:180]}")
                return
            data = json.loads(body)
            content = data["choices"][0]["message"].get("content", "")
            print(f"{model}: OK {elapsed:.2f}s -> {content[:80]}")
    except Exception as exc:
        elapsed = time.time() - started
        print(f"{model}: FAIL {elapsed:.2f}s {type(exc).__name__}: {exc}")


async def main():
    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    translation = config["translation"]
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for model in CANDIDATES:
            await try_model(
                session,
                translation["endpoint"],
                translation["api_key"],
                model,
                "我都搞不懂为什么要玩部落。",
            )


if __name__ == "__main__":
    asyncio.run(main())
