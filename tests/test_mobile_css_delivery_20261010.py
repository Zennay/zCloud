"""Ensure that the phone design is actually delivered by the current server route."""
import ast
import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
MARKER = "/* zCloud design-system.css bundled for the server's static-file allowlist */"


class MobileCssDeliveryTests(unittest.TestCase):
    def test_every_dashboard_css_href_is_in_the_server_allowlist(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        html = (PUBLIC / "index.html").read_text(encoding="utf-8")
        static_route = next((line.strip() for line in server.splitlines() if line.strip().startswith("files={")), None)
        self.assertIsNotNone(static_route, "Cannot verify served static routes")
        paths = set(ast.literal_eval(static_route.partition("=")[2]))
        links = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
        self.assertGreaterEqual(len(links), 2)
        for url in links:
            self.assertIn(urlsplit(url).path, paths,
                          f"Unserved CSS route referenced from dashboard: {url}")
        self.assertNotIn("/design-system.css", [urlsplit(url).path for url in links])
        self.assertIn("/enhancements.css?v=r4", links)

    def test_complete_design_source_is_bundled_in_delivered_styles(self):
        css = (PUBLIC / "enhancements.css").read_text(encoding="utf-8")
        source = (PUBLIC / "design-system.css").read_text(encoding="utf-8")
        self.assertEqual(css.count(MARKER), 1)
        self.assertTrue(css.endswith(MARKER + "\n" + source.strip() + "\n"),
                        "Update the served bundle whenever design-system.css changes")
        self.assertIn(".global-worker-actions", source)
        self.assertIn(".global-worker-access", source)


if __name__ == "__main__":
    unittest.main()
