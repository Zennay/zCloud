import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

enhancements_stub = types.ModuleType("enhancements")
enhancements_stub.init_db = lambda conn: None
_previous_enhancements = sys.modules.get("enhancements")
sys.modules["enhancements"] = enhancements_stub

import server

if _previous_enhancements is None:
    sys.modules.pop("enhancements", None)
else:
    sys.modules["enhancements"] = _previous_enhancements


class AutonomyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-autonomy-")
        self.root = Path(self.tmp.name)
        self.original_db = server.DB
        self.original_policy = server.AUTONOMY_POLICY_FILE
        server.DB = self.root / "history.db"
        server.AUTONOMY_POLICY_FILE = self.root / "autonomy-policy.json"
        self.hax_status = self.root / "haxlab-status.json"
        self.ftmo_status = self.root / "ftmo-status.json"
        self.write_policy()
        server.init_db()

    def tearDown(self):
        server.DB = self.original_db
        server.AUTONOMY_POLICY_FILE = self.original_policy
        self.tmp.cleanup()

    def write_policy(self):
        payload = {
            "schema_version": 1,
            "default": {
                "mode": "manual",
                "auto_start": False,
                "dispatch_mode": "vps",
                "continue_delay_seconds": 300,
                "min_ai_interval_seconds": 300,
                "wait_vps_seconds": 900,
                "wait_human_seconds": 21600,
                "complete_recheck_seconds": 86400,
            },
            "projects": {
                "haxlab": {
                    "mode": "haxlab_status",
                    "auto_start": True,
                    "status_file": str(self.hax_status),
                    "ai_states": ["NEEDS_AI"],
                },
                "ftmo": {
                    "mode": "ftmo_status",
                    "auto_start": True,
                    "status_file": str(self.ftmo_status),
                    "ai_stages": ["development", "await_preregistration"],
                },
                "ulab": {"mode": "external_gate", "auto_start": False},
                "supa": {
                    "mode": "ai_worker",
                    "auto_start": True,
                    "dispatch_mode": "vps",
                    "min_ai_interval_seconds": 300,
                },
            },
        }
        server.AUTONOMY_POLICY_FILE.write_text(json.dumps(payload), encoding="utf-8")

    def test_haxlab_only_calls_ai_when_vps_requests_it(self):
        self.hax_status.write_text(json.dumps({"state": "RUNNING"}), encoding="utf-8")
        state = server.project_autonomy_state("haxlab")
        self.assertFalse(state["allow_ai"])
        self.assertEqual("haxlab_vps_running", state["reason"])

        self.hax_status.write_text(json.dumps({"state": "NEEDS_AI"}), encoding="utf-8")
        state = server.project_autonomy_state("haxlab")
        self.assertTrue(state["allow_ai"])
        self.assertEqual("haxlab_needs_ai", state["reason"])

    def test_ftmo_uses_research_stage_and_yields_to_local_paper_job(self):
        base = {
            "ok": True,
            "research": {"next_stage": "development"},
            "paper_forward_shadow": {"action": "idle"},
        }
        self.ftmo_status.write_text(json.dumps(base), encoding="utf-8")
        self.assertTrue(server.project_autonomy_state("ftmo")["allow_ai"])

        base["paper_forward_shadow"]["action"] = "running"
        self.ftmo_status.write_text(json.dumps(base), encoding="utf-8")
        state = server.project_autonomy_state("ftmo")
        self.assertFalse(state["allow_ai"])
        self.assertEqual("ftmo_paper_running", state["reason"])

        base["paper_forward_shadow"]["action"] = "idle"
        base["research"]["next_stage"] = "blocked"
        self.ftmo_status.write_text(json.dumps(base), encoding="utf-8")
        self.assertFalse(server.project_autonomy_state("ftmo")["allow_ai"])

    def test_external_gate_does_not_burn_chatgpt(self):
        state = server.project_autonomy_state("ulab")
        self.assertFalse(state["allow_ai"])
        self.assertEqual("external_or_human_gate", state["reason"])

    def test_wait_vps_marker_temporarily_closes_generic_ai_gate(self):
        before = server.project_autonomy_state("supa")
        self.assertTrue(before["allow_ai"])
        with server.connect() as conn:
            conn.execute(
                "INSERT INTO runner_events(ts,event,project_id,worker_slot,generating,sending) VALUES(?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), "autonomy-wait-vps", "supa", 1, 0, 0),
            )
        after = server.project_autonomy_state("supa")
        self.assertFalse(after["allow_ai"])
        self.assertEqual("autonomy-wait-vps", after["reason"])
        self.assertIsNotNone(after["hold"])

    def test_global_scheduler_allocates_at_most_two_and_manual_pause_wins(self):
        self.hax_status.write_text(json.dumps({"state": "NEEDS_AI"}), encoding="utf-8")
        self.ftmo_status.write_text(json.dumps({
            "ok": True,
            "research": {"next_stage": "development"},
            "paper_forward_shadow": {"action": "idle"},
        }), encoding="utf-8")

        result = server.autonomy_scheduler_tick()
        self.assertLessEqual(len(result["allocation"]["assignments"]), 2)
        with server.connect() as conn:
            rows = conn.execute(
                "SELECT project_id,active,worker_count FROM runner_targets WHERE active=1"
            ).fetchall()
        self.assertLessEqual(sum(int(row["worker_count"]) for row in rows), 2)

        allocated = list(dict.fromkeys(result["allocation"]["assignments"]))
        self.assertTrue(allocated)
        paused = allocated[0]
        with server.connect() as conn:
            conn.execute("UPDATE autonomy_runtime SET manual_pause=1 WHERE project_id=?", (paused,))
        again = server.autonomy_scheduler_tick()
        self.assertNotIn(paused, again["allocation"]["assignments"])

    def test_vps_scheduler_pushes_allocated_ai_worker_without_time_cooldown(self):
        # Remove competing projects so Supa receives both global slots deterministically.
        for project_id in ("haxlab", "ftmo", "cloud", "raiseai", "zssh"):
            server._autonomy_initialize_project(project_id)
            with server.connect() as conn:
                conn.execute("UPDATE autonomy_runtime SET manual_pause=1 WHERE project_id=?", (project_id,))
        with server.connect() as conn:
            conn.execute("DELETE FROM runner_commands WHERE project_id='supa'")
        first = server.autonomy_scheduler_tick()
        self.assertIn("supa", first["allocation"]["assignments"])
        self.assertIn("supa", first["pushed"])
        with server.connect() as conn:
            pushes = conn.execute(
                "SELECT COUNT(*) FROM runner_commands WHERE project_id='supa' AND action='push' AND status='pending'"
            ).fetchone()[0]
        self.assertEqual(1, pushes)

        # A pending command still protects against duplicate sends; there is no time-based cooldown.
        second = server.autonomy_scheduler_tick()
        self.assertNotIn("supa", second["pushed"])
        with server.connect() as conn:
            pushes = conn.execute(
                "SELECT COUNT(*) FROM runner_commands WHERE project_id='supa' AND action='push' AND status='pending'"
            ).fetchone()[0]
        self.assertEqual(1, pushes)

    def test_vps_signal_project_does_not_push_ai_while_local_work_is_running(self):
        self.hax_status.write_text(json.dumps({"state": "RUNNING"}), encoding="utf-8")
        with server.connect() as conn:
            conn.execute("UPDATE runner_targets SET active=1 WHERE project_id='haxlab'")
            conn.execute("DELETE FROM runner_commands WHERE project_id='haxlab'")
        result = server.autonomy_scheduler_tick()
        self.assertNotIn("haxlab", result["pushed"])
        with server.connect() as conn:
            pushes = conn.execute(
                "SELECT COUNT(*) FROM runner_commands WHERE project_id='haxlab' AND action='push' AND status='pending'"
            ).fetchone()[0]
        self.assertEqual(0, pushes)

    def test_runner_targets_expose_vps_dispatch_contract(self):
        target = server.runner_targets()["supa"]
        self.assertTrue(target["vps_dispatch_only"])
        self.assertEqual(300, target["ai_dispatch_interval_seconds"])

    def test_firefox_runner_waits_for_vps_after_ai_cycle(self):
        background = (Path(__file__).resolve().parents[1] / "firefox-extension" / "background.js").read_text(encoding="utf-8")
        self.assertIn('status("awaiting-vps-dispatch", {reason: "cycle-finished"})', background)
        self.assertIn("if (!vpsDispatchOnly && !SINGLE_RUN", background)
        self.assertIn('status("vps-dispatch-ready", {reason: "awaiting-vps-command"})', background)


if __name__ == "__main__":
    unittest.main()
