"""
Regression test for translating repeatedly on one persistent asyncio loop.
"""

import json
import asyncio
import threading
import time
from pathlib import Path

from translator import GameTranslator, TranslationConfig


def main():
    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    translation = config["translation"]
    loop = asyncio.new_event_loop()

    def run_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    while not loop.is_running():
        time.sleep(0.01)

    translator = GameTranslator(
        TranslationConfig(
            api_key=translation["api_key"],
            model=translation["model"],
            endpoint=translation["endpoint"],
            max_tokens=translation.get("max_tokens", 200),
            temperature=translation.get("temperature", 0.3),
        )
    )

    try:
        for text in [
            "Good as the battery technology and honestly what",
            "Are they actually ahead of us?",
        ]:
            future = asyncio.run_coroutine_threadsafe(translator.translate(text), loop)
            print(repr(future.result(timeout=45)))

        close_future = asyncio.run_coroutine_threadsafe(translator.close(), loop)
        close_future.result(timeout=3)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)
        loop.close()


if __name__ == "__main__":
    main()
