import asyncio
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.responses import FileResponse

from mobile_server import MobileWebSocketManager, mobile_static_dir


class MobileServerAssetsTest(unittest.TestCase):
    def test_mobile_static_assets_exist(self):
        static_dir = mobile_static_dir()

        self.assertTrue((static_dir / "index.html").exists())
        self.assertTrue((static_dir / "styles.css").exists())
        self.assertTrue((static_dir / "app.js").exists())

        index_html = (static_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn("/mobile/static/styles.css", index_html)
        self.assertIn("/mobile/static/app.js", index_html)

        app_js = (static_dir / "app.js").read_text(encoding="utf-8")
        self.assertIn('data.type === "translation"', app_js)
        self.assertIn('data.type === "connected"', app_js)

    def test_mobile_route_serves_external_index_file(self):
        manager = MobileWebSocketManager()
        mobile_route = next(route for route in manager._app.routes if getattr(route, "path", "") == "/mobile")

        response = asyncio.run(mobile_route.endpoint())

        self.assertIsInstance(response, FileResponse)
        self.assertEqual(Path(response.path), mobile_static_dir() / "index.html")


if __name__ == "__main__":
    unittest.main()
