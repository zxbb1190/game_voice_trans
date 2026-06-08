import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main
import translator
from voxgo.app import VoxGoApp
from voxgo.translation import GameTranslator, TranslationConfig
from voxgo.translation.google import GoogleCloudProvider
from voxgo.translation.openai_compatible import OpenAICompatibleProvider
from voxgo.translation.registry import create_provider


class TranslationProviderRegistryTest(unittest.TestCase):
    def test_legacy_entry_points_still_export_new_facade(self):
        self.assertIs(main.VoxGoApp, VoxGoApp)
        self.assertIs(translator.GameTranslator, GameTranslator)
        self.assertIs(translator.TranslationConfig, TranslationConfig)

    def test_registry_creates_configured_provider(self):
        self.assertIsInstance(
            create_provider(TranslationConfig(provider="openai_compatible")),
            OpenAICompatibleProvider,
        )
        self.assertIsInstance(
            create_provider(TranslationConfig(provider="google")),
            GoogleCloudProvider,
        )

    def test_translator_returns_structured_result_for_missing_google_key(self):
        async def run():
            client = GameTranslator(TranslationConfig(provider="google", api_key=""))
            try:
                return await client.translate_result("Are they pushing B site now?", "en")
            finally:
                await client.close()

        import asyncio

        result = asyncio.run(run())
        self.assertIn("Google Cloud Translation API Key", result.translated)
        self.assertEqual(result.source_lang, "en")
        self.assertEqual(result.target_lang, "zh")


if __name__ == "__main__":
    unittest.main()
