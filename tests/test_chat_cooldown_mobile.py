import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ChatCooldownAndMobileProjectTests(unittest.TestCase):
    def test_chatgpt_scheduler_checks_every_ten_minutes(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")

        self.assertIn("AUTONOMY_TICK_SECONDS = 600", server)
        self.assertIn("auto_continue_delay_seconds || 600", background)
        self.assertNotIn("AUTONOMY_TICK_SECONDS = 60\n", server)
        self.assertNotIn("auto_continue_delay_seconds || 300", background)

    def test_production_policy_never_auto_starts_chatgpt(self):
        policy = json.loads((ROOT / "autonomy-policy.json").read_text(encoding="utf-8"))

        self.assertFalse(policy["default"]["auto_start"])
        self.assertGreaterEqual(policy["default"]["continue_delay_seconds"], 600)
        for project_id, project in policy["projects"].items():
            self.assertFalse(project["auto_start"], project_id)
            self.assertGreaterEqual(project["continue_delay_seconds"], 600, project_id)

    def test_mobile_projects_are_visible_and_touch_safe(self):
        index = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="mobileProjectNav"', index)
        self.assertIn("mobileProjects.innerHTML=links", app)
        self.assertIn("(pointer: coarse)", app)
        self.assertIn('draggable="${draggable}"', app)
        self.assertIn(".mobile-project-nav{display:flex", css)
        self.assertIn(".mobile-project-nav a{min-height:44px", css)
        self.assertIn(".project-card-link{touch-action:manipulation}", css)


if __name__ == "__main__":
    unittest.main()
