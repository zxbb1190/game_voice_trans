import asyncio
import html
import time

from loguru import logger

from .base import ProviderTestResult, TranslationRequest, TranslationResult, TranslatorProvider, short_error_text


GOOGLE_TRANSLATE_ENDPOINT = "https://translation.googleapis.com/language/translate/v2"
GOOGLE_LANGUAGE_CODES = {"en": "en", "zh": "zh-CN"}


class GoogleCloudProvider(TranslatorProvider):
    name = "google"

    async def test(self) -> ProviderTestResult:
        return ProviderTestResult(ok=True, message="Provider is configured")

    def missing_api_key_message(self) -> str:
        return "[未翻译] API Key 未配置，请在设置里填写 Google Cloud Translation API Key"

    def _google_language_code(self, language: str) -> str:
        return GOOGLE_LANGUAGE_CODES.get(language, language)

    async def translate(self, request: TranslationRequest, session) -> TranslationResult:
        params = {
            "key": self.config.api_key.strip(),
            "q": request.text,
            "source": self._google_language_code(request.source_lang),
            "target": self._google_language_code(request.target_lang),
            "format": "text",
        }
        headers = {"Content-Type": "application/json; charset=utf-8"}
        start_time = time.time()

        try:
            async with session.post(GOOGLE_TRANSLATE_ENDPOINT, params=params, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    translations = ((data.get("data") or {}).get("translations") or [])
                    translation = ""
                    if translations:
                        translation = html.unescape((translations[0].get("translatedText") or "").strip())
                    elapsed = time.time() - start_time
                    if not translation:
                        logger.warning("Google 翻译 API 返回空译文: {}", data)
                    direction = f"{request.source_lang}->{request.target_lang}"
                    logger.info(
                        f"Google 翻译({direction}): {request.text[:50]}... → {translation[:50]}... ({elapsed:.2f}s)"
                    )
                    return TranslationResult(
                        translated=translation,
                        source_lang=request.source_lang,
                        target_lang=request.target_lang,
                        provider=self.name,
                        elapsed_seconds=elapsed,
                        raw=data,
                    )

                error_text = await response.text()
                logger.error(f"Google 翻译 API 错误: {response.status} - {error_text}")
                detail = short_error_text(error_text)
                if detail:
                    translated = f"[翻译错误 {response.status}] {detail}"
                else:
                    translated = f"[翻译错误 {response.status}] Google Translation API 返回错误"
                return TranslationResult(translated, request.source_lang, request.target_lang, self.name)

        except asyncio.TimeoutError:
            logger.error("Google 翻译 API 超时")
            return TranslationResult(
                f"[翻译超时] API 请求超过 {self.config.timeout_seconds:g} 秒，请检查网络或 Google Cloud Translation 状态",
                request.source_lang,
                request.target_lang,
                self.name,
            )
        except Exception as e:
            logger.error(f"Google 翻译异常: {e}")
            return TranslationResult(f"[翻译失败] {str(e)[:180]}", request.source_lang, request.target_lang, self.name)

