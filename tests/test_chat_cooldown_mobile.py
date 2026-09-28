import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PortfolioQueueWorkerPoolAndMobileProjectTests(unittest.TestCase):
    def test_vps_scheduler_has_exactly_two_global_ai_slots_with_small_debounce(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")

        self.assertIn("AUTONOMY_TICK_SECONDS = 2", server)
        self.assertIn("GLOBAL_CHATGPT_WORKER_LIMIT = 2", server)
        self.assertIn("MAX_CHATGPT_WORKERS = 2", server)
        self.assertIn("PORTFOLIO_AI_COOLDOWN_SECONDS = 120", server)
        self.assertIn("def global_worker_allocation(", server)
        self.assertIn("ai_global_slots", server)
        self.assertIn('status("awaiting-vps-dispatch", {reason: "cycle-finished"})', background)
        self.assertIn("setInterval(refreshTargets, 5000)", background)

    def test_production_policy_uses_two_minute_ai_debounce_not_project_quotas(self):
        policy = json.loads((ROOT / "autonomy-policy.json").read_text(encoding="utf-8"))

        self.assertTrue(policy["default"]["auto_start"])
        self.assertEqual("vps", policy["default"]["dispatch_mode"])
        self.assertEqual(120, policy["default"]["min_ai_interval_seconds"])
        self.assertEqual(120, policy["default"]["continue_delay_seconds"])

        for project_id, project in policy["projects"].items():
            self.assertEqual("vps", project["dispatch_mode"], project_id)
            self.assertEqual(120, project["min_ai_interval_seconds"], project_id)
            self.assertEqual(120, project["continue_delay_seconds"], project_id)
            self.assertEqual(project_id != "ulab", project["auto_start"], project_id)

    def test_worker_prompt_uses_global_notion_queue_and_evidence_done_gate(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("Portfolio Work Queue", server)
        self.assertIn("4162fac179f44fcbbe4072a183d2b440", server)
        self.assertIn("is géén vaste projecttoewijzing", server)
        self.assertIn("claim het item vóór inhoudelijk werk", server)
        self.assertIn("Done is alleen toegestaan", server)
        self.assertIn("Evidence concrete, verifieerbare", server)
        self.assertIn("NO-OP/GIVE-UP GUARD", server)
        self.assertIn("toolbeperking op één pad is op zichzelf geen blocker", server)
        self.assertIn("ZCLOUD_WAIT_EVIDENCE", server)
        self.assertIn("queue=no-eligible", server)
        self.assertIn("Er bestaat geen vaste", server)
        self.assertIn("controleer eerst of jouw stabiele portfolio Worker 1/2", server)
        self.assertIn("Alleen een nieuw P0 Critical item mag veilig preëmpten", server)

    def test_global_worker_identity_survives_project_tab_reallocation(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("def _current_global_slot_map()", server)
        self.assertIn("Keep a surviving worker on the same global Worker 1/2 identity", server)
        self.assertIn("'global_worker_slot':global_slot", server)
        self.assertIn("Portfolio Worker {global_slot}/{GLOBAL_CHATGPT_WORKER_LIMIT}", server)

    def test_wait_markers_require_machine_checkable_evidence(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")
        self.assertIn("ZCLOUD_WAIT_EVIDENCE", background)
        self.assertIn("queue=no-eligible", background)
        self.assertIn("invalid-wait-vps-without-run-evidence", background)
        self.assertIn("invalid-wait-human-without-gate-evidence", background)
        self.assertIn(r"text.match(/ZCLOUD_WAIT_EVIDENCE:\s*([^\n]+)/i)", background)
        self.assertIn(r"/(?:^|;)\s*queue=no-eligible(?:;|$)/i", background)
        self.assertIn('syncStatus("autonomy-continue"', background)
        self.assertIn("ZCLOUD_WORK_PROJECT:", server)

    def test_browser_routes_project_specific_evidence_from_queue_marker(self):
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")
        self.assertIn("ZCLOUD_WORK_PROJECT:", background)
        self.assertIn('workProject !== "cloud"', background)
        self.assertIn('baseProjectId: "cloud"', background)
        self.assertIn('"portfolio-work-project"', background)

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
