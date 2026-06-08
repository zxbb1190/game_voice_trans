import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voxgo.runtime.events import AppNotice, EventBus, TranscriptReady, TranslationReady


class RuntimeEventsTest(unittest.TestCase):
    def test_event_bus_dispatches_by_event_type(self):
        bus = EventBus()
        seen = []

        bus.subscribe(TranscriptReady, lambda event: seen.append(("transcript", event.text)))
        bus.subscribe(TranslationReady, lambda event: seen.append(("translation", event.translated)))

        bus.publish(AppNotice(level="状态", message="ignored"))
        bus.publish(TranscriptReady(text="hello", language="en", trace_id="t1"))
        bus.publish(TranslationReady(original="hello", translated="你好", source_lang="en", target_lang="zh", trace_id="t1"))

        self.assertEqual(seen, [("transcript", "hello"), ("translation", "你好")])


if __name__ == "__main__":
    unittest.main()

