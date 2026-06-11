import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voxgo.update.checker import (
    UpdateSettings,
    _direct_opener_for_local_url,
    check_for_update,
    compare_versions,
    parse_update_manifest,
    should_check_for_update,
)


class UpdateCheckerTest(unittest.TestCase):
    def test_version_compare_handles_v_prefix_and_prerelease(self):
        self.assertGreater(compare_versions("v0.2.1", "0.2.0"), 0)
        self.assertEqual(compare_versions("0.2.1", "v0.2.1"), 0)
        self.assertLess(compare_versions("0.2.1-beta.1", "0.2.1"), 0)

    def test_parse_update_manifest_normalizes_notes_and_version(self):
        info = parse_update_manifest(
            {
                "latest": "v0.3.0",
                "channel": "beta",
                "notes": "- one\n- two",
                "download_lite_url": "https://example.com/lite.zip",
                "download_full_cuda_url": "https://example.com/full-cuda.zip",
                "cuda_runtime_url": "https://example.com/cuda-runtime.zip",
                "sha256_full_cuda": "abc123",
            }
        )

        self.assertEqual(info.latest, "0.3.0")
        self.assertEqual(info.channel, "beta")
        self.assertEqual(info.notes, ["one", "two"])
        self.assertEqual(info.download_lite_url, "https://example.com/lite.zip")
        self.assertEqual(info.download_full_cuda_url, "https://example.com/full-cuda.zip")
        self.assertEqual(info.cuda_runtime_url, "https://example.com/cuda-runtime.zip")
        self.assertEqual(info.sha256_full_cuda, "abc123")

    def test_check_for_update_reports_available_current_and_ignored(self):
        fetcher = lambda url: {"latest": "0.3.0", "channel": "stable", "title": "VoxGo v0.3.0"}

        available = check_for_update("0.2.1", UpdateSettings(), fetcher=fetcher)
        self.assertEqual(available.status, "available")
        self.assertEqual(available.update.latest, "0.3.0")

        current = check_for_update("0.3.0", UpdateSettings(), fetcher=fetcher)
        self.assertEqual(current.status, "current")

        ignored = check_for_update(
            "0.2.1",
            UpdateSettings(ignored_version="v0.3.0"),
            fetcher=fetcher,
        )
        self.assertEqual(ignored.status, "ignored")

    def test_manual_check_works_when_auto_check_disabled(self):
        fetcher = lambda url: {"latest": "0.3.0", "channel": "stable"}
        settings = UpdateSettings(enabled=False)

        self.assertEqual(check_for_update("0.2.1", settings, fetcher=fetcher).status, "disabled")
        self.assertEqual(check_for_update("0.2.1", settings, fetcher=fetcher, manual=True).status, "available")

    def test_daily_check_interval(self):
        settings = UpdateSettings(enabled=True, last_check_at=100)

        self.assertFalse(should_check_for_update(settings, now=100 + 60))
        self.assertTrue(should_check_for_update(settings, now=100 + 24 * 60 * 60))

    def test_local_manifest_urls_bypass_proxy(self):
        self.assertIsNotNone(_direct_opener_for_local_url("http://127.0.0.1:8010/update.json"))
        self.assertIsNotNone(_direct_opener_for_local_url("http://localhost:8010/update.json"))
        self.assertIsNone(_direct_opener_for_local_url("https://voxgo.cn/update.json"))


if __name__ == "__main__":
    unittest.main()
