import json
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import aiohttp


@dataclass
class TranslationConfig:
    provider: str = "openai_compatible"
    api_key: str = ""
    model: str = "tencent/Hunyuan-MT-7B"
    endpoint: str = "https://api.siliconflow.cn/v1/chat/completions"
    max_tokens: int = 80
    temperature: float = 0.0
    source_lang: str = "en"
    target_lang: str = "zh"
    context_messages: int = 0
    timeout_seconds: float = 12.0
    max_concurrent_requests: int = 2


@dataclass
class TranslationRequest:
    text: str
    source_lang: str
    target_lang: str
    detected_language: str = ""


@dataclass
class TranslationResult:
    translated: str
    source_lang: str
    target_lang: str
    provider: str
    elapsed_seconds: float = 0.0
    raw: Optional[dict] = None


@dataclass
class ProviderTestResult:
    ok: bool
    message: str
    elapsed_ms: int = 0


TRANSLATION_PROVIDERS = {
    "openai_compatible": "OpenAI 兼容",
    "google": "Google Cloud Translation",
}
LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "英语": "en",
    "zh": "zh",
    "zh-cn": "zh",
    "zh-tw": "zh",
    "cmn": "zh",
    "yue": "zh",
    "chinese": "zh",
    "中文": "zh",
}
OPPOSITE_LANGUAGE = {"en": "zh", "zh": "en"}
ZH_RE = re.compile(r"[\u4e00-\u9fff]")
PLACEHOLDER_API_KEYS = {
    "",
    "YOUR_API_KEY",
    "YOUR_SILICONFLOW_API_KEY",
    "YOUR_OPENAI_COMPATIBLE_API_KEY",
    "YOUR_GOOGLE_TRANSLATE_API_KEY",
    "YOUR_GOOGLE_CLOUD_TRANSLATION_API_KEY",
}
LOCAL_ENDPOINT_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def normalize_translation_provider(value: str, default: str = "openai_compatible") -> str:
    value = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "openai": "openai_compatible",
        "openai_compat": "openai_compatible",
        "compatible": "openai_compatible",
        "chat_completions": "openai_compatible",
        "siliconflow": "openai_compatible",
        "google_translate": "google",
        "google_translation": "google",
        "google_cloud": "google",
        "google_cloud_translation": "google",
        "cloud_translation": "google",
    }
    value = aliases.get(value, value)
    return value if value in TRANSLATION_PROVIDERS else default


def normalize_language_code(value: str, default: str = "") -> str:
    value = (value or "").strip().lower()
    normalized = LANGUAGE_ALIASES.get(value, value)
    return normalized if normalized in OPPOSITE_LANGUAGE else default


def detect_language(text: str, configured_source_lang: str = "", detected_language: str = "") -> str:
    configured = normalize_language_code(configured_source_lang)
    if configured:
        return configured
    lang = (detected_language or "").lower()
    if lang in ("zh", "zh-cn", "zh-tw", "chinese", "cmn", "yue"):
        return "zh"
    if lang in ("en", "eng", "english"):
        return "en"
    zh_count = len(ZH_RE.findall(text or ""))
    return "zh" if zh_count >= max(1, len(text.strip()) // 5) else "en"


def target_language(configured_target_lang: str, source_language: str) -> str:
    configured = normalize_language_code(configured_target_lang)
    if configured and configured != source_language:
        return configured
    return OPPOSITE_LANGUAGE.get(source_language, "zh")


def is_placeholder_api_key(api_key: str) -> bool:
    return (api_key or "").strip() in PLACEHOLDER_API_KEYS


def normalized_chat_endpoint(endpoint: str) -> str:
    endpoint = (endpoint or "").strip().rstrip("/")
    if not endpoint:
        endpoint = TranslationConfig.endpoint.rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    return f"{endpoint}/chat/completions"


def endpoint_requires_api_key(endpoint: str) -> bool:
    host = (urlparse(normalized_chat_endpoint(endpoint)).hostname or "").lower()
    return host not in LOCAL_ENDPOINT_HOSTS


def short_error_text(error_text: str) -> str:
    text = (error_text or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            parts = [
                str(error.get("code") or "").strip(),
                str(error.get("message") or "").strip(),
            ]
            text = " ".join(part for part in parts if part)
        elif isinstance(data, dict):
            parts = [
                str(data.get("code") or "").strip(),
                str(data.get("message") or data.get("detail") or "").strip(),
            ]
            text = " ".join(part for part in parts if part)
    except Exception:
        pass
    text = " ".join(text.split())
    return text[:220]


class TranslatorProvider:
    name = ""

    def __init__(self, config: TranslationConfig):
        self.config = config

    async def test(self) -> ProviderTestResult:
        raise NotImplementedError

    async def translate(self, request: TranslationRequest, session: aiohttp.ClientSession) -> TranslationResult:
        raise NotImplementedError

    def missing_api_key_message(self) -> str:
        return "[未翻译] API Key 未配置，请在设置里填写 OpenAI 兼容 API Key"

    def requires_api_key(self) -> bool:
        return True

