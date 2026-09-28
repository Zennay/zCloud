import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GlobalWorkerPoolAndMobileProjectTests(unittest.TestCase):
    def test_vps_scheduler_has_exactly_two_global_ai_slots_without_cooldown(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")

        self.assertIn("AUTONOMY_TICK_SECONDS = 2", server)
        self.assertIn("GLOBAL_CHATGPT_WORKER_LIMIT = 2", server)
        self.assertIn("MAX_CHATGPT_WORKERS = 2", server)
        self.assertIn("def global_worker_allocation(", server)
        self.assertIn("ai_global_slots", server)
        self.assertIn('status("awaiting-vps-dispatch", {reason: "cycle-finished"})', background)
        self.assertIn('"autonomy-priority"', background)
        self.assertIn("ZCLOUD_PRIORITY:", background)
        self.assertIn("setInterval(refreshTargets, 5000)", background)
        self.assertIn("Math.max(0, Number(cfg.auto_continue_delay_seconds ?? 0)", background)

    def test_production_policy_is_vps_led_and_has_no_ai_cadence_cooldown(self):
        policy = json.loads((ROOT / "autonomy-policy.json").read_text(encoding="utf-8"))

        self.assertTrue(policy["default"]["auto_start"])
        self.assertEqual("vps", policy["default"]["dispatch_mode"])
        self.assertEqual(0, policy["default"]["min_ai_interval_seconds"])
        self.assertEqual(0, policy["default"]["continue_delay_seconds"])

        for project_id, project in policy["projects"].items():
            self.assertEqual("vps", project["dispatch_mode"], project_id)
            self.assertEqual(0, project["min_ai_interval_seconds"], project_id)
            self.assertEqual(0, project["continue_delay_seconds"], project_id)
            self.assertEqual(project_id != "ulab", project["auto_start"], project_id)

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
