"""Backward-compatible translation module exports."""

from voxgo.translation import (  # noqa: F401
    GOOGLE_TRANSLATE_ENDPOINT,
    GameTranslator,
    ProviderTestResult,
    TRANSLATION_PROVIDERS,
    TranslationConfig,
    TranslationRequest,
    TranslationResult,
    TranslatorProvider,
    normalize_language_code,
    normalize_translation_provider,
)
from voxgo.translation.prompt import SYSTEM_PROMPT  # noqa: F401

