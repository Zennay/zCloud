import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PortfolioQueueWorkerPoolAndMobileProjectTests(unittest.TestCase):
    def test_vps_scheduler_has_one_global_ai_slot_with_global_guarded_interval(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")

        self.assertIn("AUTONOMY_TICK_SECONDS = 5", server)
        self.assertIn("GLOBAL_CHATGPT_WORKER_LIMIT = 1", server)
        self.assertIn("MAX_CHATGPT_WORKERS = 1", server)
        self.assertIn("PORTFOLIO_AI_COOLDOWN_SECONDS = 300", server)
        self.assertIn("def portfolio_queue_allocate(", server)
        self.assertIn("def _global_dispatch_interval_due(", server)
        self.assertIn("def _worker_prompt_interval_due(", server)
        self.assertIn("def _autonomy_enqueue_worker_push(", server)
        self.assertIn("min_interval_seconds=max(PORTFOLIO_AI_COOLDOWN_SECONDS", server)
        self.assertIn("def global_worker_allocation(", server)
        self.assertIn("ai_global_slots", server)
        self.assertIn('status("awaiting-vps-dispatch", {reason: "cycle-finished"})', background)
        self.assertIn("setInterval(refreshTargets, 5000)", background)
        self.assertIn("pendingInitialDispatches", background)
        self.assertIn("forceInitialDispatch", background)
        self.assertIn("vpsDispatchOnly && !forceInitialDispatch", background)
        self.assertIn("if latest and (bool(latest['generating']) or bool(latest['sending'])):", server)
        self.assertIn("if prompt_id and ready_id < prompt_id:", server)
        self.assertIn("A prompt from an older browser session must not permanently block", server)
        self.assertIn("latest_id <= prompt_id or latest_generating or latest_sending", server)
        self.assertIn('"runner-auto-paused"', background)
        self.assertIn("WEAK_CYCLE_LIMIT = 2", background)
        self.assertIn("SHORT_CYCLE_MS = 60 * 1000", background)
        self.assertIn("repeated-short-or-null-result", background)
        self.assertIn("repeated-no-generation", background)
        self.assertIn("QUALITY_RETRY_LIMIT = 1", background)
        self.assertIn("quality-retry-scheduled", background)
        self.assertIn("same-assignment-quality-recovery", background)
        self.assertIn("missingQueueEvidence", background)
        self.assertIn("worker_auto_paused", server)

    def test_production_policy_has_no_time_debounce_and_waits_for_generation_boundary(self):
        policy = json.loads((ROOT / "autonomy-policy.json").read_text(encoding="utf-8"))

        self.assertTrue(policy["default"]["auto_start"])
        self.assertEqual("vps", policy["default"]["dispatch_mode"])
        self.assertEqual(300, policy["default"]["min_ai_interval_seconds"])
        self.assertEqual(300, policy["default"]["continue_delay_seconds"])

        for project_id, project in policy["projects"].items():
            self.assertEqual("vps", project["dispatch_mode"], project_id)
            self.assertEqual(300, project["min_ai_interval_seconds"], project_id)
            self.assertEqual(300, project["continue_delay_seconds"], project_id)
            self.assertEqual(project_id != "ulab", project["auto_start"], project_id)

    def test_worker_prompt_uses_vps_sqlite_queue_and_evidence_done_gate(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("portfolio_queue", server)
        self.assertIn("queue_backend':'sqlite'", server)
        self.assertIn("zCloud SQLite op de VPS is de enige scheduling/source-of-truth", server)
        self.assertIn("Query Notion NIET om een queue-item te kiezen", server)
        self.assertIn("Done alleen wanneer ALLE Completion Criteria bewezen zijn", server)
        self.assertIn("status-only/read-only cyclus is ongeldig", server)
        self.assertIn("CONTINUE is VERBODEN", server)
        self.assertIn("ZCLOUD_QUEUE_RESULT", server)
        self.assertIn("ZCLOUD_QUEUE_EVIDENCE", server)

    def test_global_worker_identity_follows_queue_owned_slot(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("def _current_global_slot_map()", server)
        self.assertIn("Persist queue-owned portfolio slot identities exactly as assigned by SQLite", server)
        self.assertIn("'global_worker_slot':global_slot", server)
        self.assertIn("Portfolio Worker {global_slot}/{GLOBAL_CHATGPT_WORKER_LIMIT}", server)

    def test_browser_reports_queue_result_to_vps(self):
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")
        self.assertIn("ZCLOUD_QUEUE_ITEM:", background)
        self.assertIn("ZCLOUD_QUEUE_RESULT:", background)
        self.assertIn("ZCLOUD_QUEUE_EVIDENCE:", background)
        self.assertIn('"portfolio-queue-result"', background)
        self.assertIn("globalWorkerSlot", background)
        self.assertIn("nextTask", background)

    def test_wait_markers_still_fail_open_to_continue_when_invalid(self):
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")
        self.assertIn("ZCLOUD_WAIT_EVIDENCE", background)
        self.assertIn("queue=no-eligible", background)
        self.assertIn("invalid-wait-vps-without-run-evidence", background)
        self.assertIn("invalid-wait-human-without-gate-evidence", background)
        self.assertIn('syncStatus("autonomy-continue"', background)

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
