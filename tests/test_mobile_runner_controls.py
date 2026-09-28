import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MobileRunnerControlsTests(unittest.TestCase):
    def test_mobile_project_controls_are_rendered(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function mobileRunnerControls", app)
        self.assertIn('data-runner-toggle="', app)
        self.assertIn('data-runner-workers="', app)
        self.assertIn("${mobileRunnerControls(p)}${runnerControls(p)}", app)
        self.assertIn("${mobileRunnerControls(p,'detail')}${workerDetailPanel(p)}", app)

    def test_mobile_controls_have_visible_responsive_styles(self):
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".mobile-runner-controls{display:none}", css)
        self.assertIn("@media(max-width:760px)", css)
        self.assertIn(".mobile-runner-controls{display:grid", css)
        self.assertIn(".mobile-runner-button", css)
        self.assertIn(".mobile-runner-workers input", css)


if __name__ == "__main__":
    unittest.main()
