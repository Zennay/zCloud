import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ChatCooldownAndMobileProjectTests(unittest.TestCase):
    def test_vps_scheduler_checks_locally_without_spending_ai_every_minute(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")

        self.assertIn("AUTONOMY_TICK_SECONDS = 60", server)
        self.assertIn("min_ai_interval_seconds", server)
        self.assertIn('status("awaiting-vps-dispatch", {reason: "cycle-finished"})', background)
        self.assertIn("if (!vpsDispatchOnly && !SINGLE_RUN", background)
        self.assertNotIn('await send("autonomy-cooldown-complete")', background.split("if (finishSignalsReported && vpsDispatchOnly)", 1)[0])

    def test_production_policy_is_vps_led_with_bounded_ai_cadence(self):
        policy = json.loads((ROOT / "autonomy-policy.json").read_text(encoding="utf-8"))

        self.assertFalse(policy["default"]["auto_start"])
        self.assertEqual("vps", policy["default"]["dispatch_mode"])
        self.assertGreaterEqual(policy["default"]["min_ai_interval_seconds"], 600)

        expected_auto = {"cloud", "haxlab", "ftmo", "supa", "raiseai", "zssh"}
        for project_id, project in policy["projects"].items():
            self.assertEqual("vps", project["dispatch_mode"], project_id)
            self.assertEqual(project_id in expected_auto, project["auto_start"], project_id)
            self.assertGreaterEqual(project["min_ai_interval_seconds"], 600, project_id)

        self.assertEqual(600, policy["projects"]["supa"]["min_ai_interval_seconds"])
        self.assertGreaterEqual(policy["projects"]["raiseai"]["min_ai_interval_seconds"], 1800)
        self.assertGreaterEqual(policy["projects"]["zssh"]["min_ai_interval_seconds"], 1800)
        self.assertFalse(policy["projects"]["ulab"]["auto_start"])

    def test_mobile_projects_are_visible_and_touch_safe(self):
        index = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="mobileProjectNav"', index)
        self.assertIn("if(mobileProjects)mobileProjects.innerHTML=", app)
        self.assertIn("(pointer: coarse)", app)
        self.assertIn('draggable="${draggable}"', app)
        self.assertIn(".mobile-project-nav{display:flex", css)
        self.assertIn(".mobile-project-nav a{min-height:44px", css)
        self.assertIn(".project-card-link{touch-action:manipulation}", css)


if __name__ == "__main__":
    unittest.main()
