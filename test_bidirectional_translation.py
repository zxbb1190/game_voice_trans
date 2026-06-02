"""
Smoke test bidirectional Chinese/English translation.
"""

import asyncio
import json
from pathlib import Path

from translator import GameTranslator, TranslationConfig


async def main():
    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    translation = config["translation"]
    translator = GameTranslator(
        TranslationConfig(
            api_key=translation["api_key"],
            model=translation["model"],
            endpoint=translation["endpoint"],
            max_tokens=translation.get("max_tokens", 120),
            temperature=translation.get("temperature", 0.1),
            context_messages=translation.get("context_messages", 0),
        )
    )
    try:
        samples = [
            ("en", "Are they pushing B site now?"),
            ("zh", "他们是不是已经转点去 B 点了？"),
        ]
        for language, text in samples:
            result = await translator.translate(text, language)
            print(f"{language}: {text} -> {result}")
    finally:
        await translator.close()


if __name__ == "__main__":
    asyncio.run(main())
