"""
Smoke test SiliconFlow chat completions without printing secrets.
"""

import json
import asyncio
from pathlib import Path

import requests

from translator import GameTranslator, TranslationConfig


def main():
    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    translation = config["translation"]
    endpoint = translation["endpoint"]
    api_key = translation["api_key"]
    model = translation["model"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是翻译助手。只把英文翻译成中文，不要解释。"},
            {"role": "user", "content": "Translate to Chinese: Are they actually ahead of us?"},
        ],
        "max_tokens": 120,
        "temperature": 0.1,
        "stream": False,
        "enable_thinking": False,
    }

    print(f"endpoint={endpoint}")
    print(f"model={model}")
    response = requests.post(endpoint, headers=headers, json=payload, timeout=30)
    print(f"status={response.status_code}")
    print(f"trace={response.headers.get('x-siliconcloud-trace-id', '')}")
    print(response.text[:1000])
    response.raise_for_status()

    data = response.json()
    message = data["choices"][0]["message"]
    print("finish_reason=", data["choices"][0].get("finish_reason"))
    print("content_repr=", repr(message.get("content", "")))
    print("reasoning_repr=", repr(message.get("reasoning_content", "")))

    print("\nTesting GameTranslator.translate()")
    translator_config = TranslationConfig(
        api_key=api_key,
        model=model,
        endpoint=endpoint,
        max_tokens=translation.get("max_tokens", 200),
        temperature=translation.get("temperature", 0.3),
    )

    async def run_translator_test():
        translator = GameTranslator(translator_config)
        try:
            result = await translator.translate("Are they actually ahead of us?")
            print("translator_result_repr=", repr(result))
        finally:
            await translator.close()

    asyncio.run(run_translator_test())


if __name__ == "__main__":
    main()
