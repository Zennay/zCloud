import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GlobalChatAllocatorAndMobileProjectTests(unittest.TestCase):
    def test_vps_scheduler_runs_fast_with_a_hard_global_two_worker_cap(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")

        self.assertIn("AUTONOMY_TICK_SECONDS = 5", server)
        self.assertIn("MAX_CHATGPT_WORKERS = 2", server)
        self.assertIn("def _global_chatgpt_allocation", server)
        self.assertIn("setInterval(refreshTargets, 5000);", background)
        self.assertIn('status("awaiting-vps-dispatch", {reason: "cycle-finished"})', background)
        self.assertIn("if (!vpsDispatchOnly && !SINGLE_RUN", background)

    def test_production_policy_is_vps_led_without_dispatch_cooldown(self):
        policy = json.loads((ROOT / "autonomy-policy.json").read_text(encoding="utf-8"))

        self.assertTrue(policy["default"]["auto_start"])
        self.assertEqual("vps", policy["default"]["dispatch_mode"])
        self.assertEqual(0, policy["default"]["min_ai_interval_seconds"])

        expected_auto = {"cloud", "haxlab", "ftmo", "supa", "raiseai", "zssh"}
        for project_id, project in policy["projects"].items():
            self.assertEqual("vps", project["dispatch_mode"], project_id)
            self.assertEqual(project_id in expected_auto, project["auto_start"], project_id)
            self.assertEqual(0, project["min_ai_interval_seconds"], project_id)

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
