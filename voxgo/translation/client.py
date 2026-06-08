import asyncio
import time
from typing import List, Optional

import aiohttp
from loguru import logger

from .base import (
    TranslationConfig,
    TranslationRequest,
    detect_language,
    is_placeholder_api_key,
    normalize_translation_provider,
    target_language,
)
from .registry import create_provider


class GameTranslator:
    """VoxGo translator facade with plugin-style providers."""

    def __init__(self, config: TranslationConfig = None):
        self.config = config or TranslationConfig()
        self._session: Optional[aiohttp.ClientSession] = None
        self._request_semaphore: Optional[asyncio.Semaphore] = None
        self._request_semaphore_limit = 0
        self._request_semaphore_loop = None
        self._translation_count = 0
        self._total_time = 0
        self._provider_key = ""
        self._provider = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds))
        return self._session

    def _provider_name(self) -> str:
        return normalize_translation_provider(getattr(self.config, "provider", "openai_compatible"))

    def _current_provider(self):
        provider_key = self._provider_name()
        if self._provider is None or self._provider_key != provider_key or self._provider.config is not self.config:
            self._provider_key = provider_key
            self._provider = create_provider(self.config)
        return self._provider

    def detect_language(self, text: str, detected_language: str = "") -> str:
        return detect_language(text, self.config.source_lang, detected_language)

    def get_target_language(self, source_language: str) -> str:
        return target_language(self.config.target_lang, source_language)

    def _max_concurrent_requests(self) -> int:
        try:
            value = int(getattr(self.config, "max_concurrent_requests", 2) or 2)
        except Exception:
            value = 2
        return max(1, min(4, value))

    def _translation_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        limit = self._max_concurrent_requests()
        if (
            self._request_semaphore is None
            or self._request_semaphore_limit != limit
            or self._request_semaphore_loop is not loop
        ):
            self._request_semaphore = asyncio.Semaphore(limit)
            self._request_semaphore_limit = limit
            self._request_semaphore_loop = loop
        return self._request_semaphore

    async def translate_result(self, text: str, detected_language: str = ""):
        if not text or not text.strip():
            source_language = self.detect_language(text, detected_language)
            return None

        provider = self._current_provider()
        if is_placeholder_api_key(self.config.api_key) and provider.requires_api_key():
            logger.warning("API Key 未配置，返回原文")
            source_language = self.detect_language(text, detected_language)
            target = self.get_target_language(source_language)
            from .base import TranslationResult

            return TranslationResult(provider.missing_api_key_message(), source_language, target, provider.name)

        source_language = self.detect_language(text, detected_language)
        target = self.get_target_language(source_language)
        request = TranslationRequest(text=text, source_lang=source_language, target_lang=target, detected_language=detected_language)
        async with self._translation_semaphore():
            session = await self._get_session()
            result = await provider.translate(request, session)
            if result.translated and not result.translated.startswith("["):
                self._translation_count += 1
                self._total_time += result.elapsed_seconds
            return result

    async def translate(self, text: str, detected_language: str = "") -> str:
        result = await self.translate_result(text, detected_language)
        return "" if result is None else result.translated

    async def translate_batch(self, texts: List[str]) -> List[str]:
        tasks = [self.translate(text) for text in texts]
        return await asyncio.gather(*tasks)

    async def translate_streaming(self, text: str):
        result = await self.translate(text)
        yield result

    def get_stats(self) -> dict:
        avg_time = self._total_time / self._translation_count if self._translation_count > 0 else 0
        return {
            "total_translations": self._translation_count,
            "average_time": round(avg_time, 3),
            "total_time": round(self._total_time, 2),
        }

    def clear_context(self):
        provider = self._current_provider()
        if hasattr(provider, "clear_context"):
            provider.clear_context()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
