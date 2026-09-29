import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

enhancements_stub = types.ModuleType("enhancements")
enhancements_stub.init_db = lambda conn: None
enhancements_stub.load_resource_policy = lambda: {}
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
        self.original_queue_seed = server.PORTFOLIO_QUEUE_SEED_FILE
        server.DB = self.root / "history.db"
        server.AUTONOMY_POLICY_FILE = self.root / "autonomy-policy.json"
        server.PORTFOLIO_QUEUE_SEED_FILE = self.root / "portfolio-queue.seed.json"
        server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]", encoding="utf-8")
        self.hax_status = self.root / "haxlab-status.json"
        self.ftmo_status = self.root / "ftmo-status.json"
        self.write_policy()
        # Keep unrelated projects out of scheduler-focused tests unless a test
        # explicitly opens their VPS gate.
        self.hax_status.write_text(json.dumps({"state": "RUNNING"}), encoding="utf-8")
        self.ftmo_status.write_text(json.dumps({
            "ok": True,
            "research": {"next_stage": "blocked"},
            "paper_forward_shadow": {"action": "idle"},
        }), encoding="utf-8")
        server.init_db()

    def tearDown(self):
        server.DB = self.original_db
        server.AUTONOMY_POLICY_FILE = self.original_policy
        server.PORTFOLIO_QUEUE_SEED_FILE = self.original_queue_seed
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
                "cloud": {"mode": "manual", "auto_start": False},
                "raiseai": {"mode": "manual", "auto_start": False},
                "zssh": {"mode": "manual", "auto_start": False},
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

    def test_scheduler_bootstraps_once_and_manual_pause_wins(self):
        server.portfolio_queue_enqueue("supa", "scheduler boot", "P1", "prove start")
        with server.connect() as conn:
            conn.execute("UPDATE runner_targets SET active=0 WHERE project_id='supa'")
        result = server.autonomy_scheduler_tick()
        self.assertIn("supa", result["started"])
        with server.connect() as conn:
            target = conn.execute("SELECT active FROM runner_targets WHERE project_id='supa'").fetchone()
            pending = conn.execute(
                "SELECT COUNT(*) FROM runner_commands WHERE project_id='supa' AND action='start' AND status='pending'"
            ).fetchone()[0]
            conn.execute("UPDATE runner_targets SET active=0 WHERE project_id='supa'")
            conn.execute("UPDATE autonomy_runtime SET manual_pause=1 WHERE project_id='supa'")
        self.assertEqual(1, target["active"])
        self.assertEqual(1, pending)

        again = server.autonomy_scheduler_tick()
        self.assertNotIn("supa", again["started"])
        with server.connect() as conn:
            target = conn.execute("SELECT active FROM runner_targets WHERE project_id='supa'").fetchone()
        self.assertEqual(0, target["active"])

    def test_vps_scheduler_pushes_active_ai_worker_and_rate_limits_it(self):
        server.portfolio_queue_enqueue("supa", "scheduler push", "P1", "prove push")
        with server.connect() as conn:
            conn.execute("UPDATE runner_targets SET active=1 WHERE project_id='supa'")
            conn.execute("DELETE FROM runner_commands WHERE project_id='supa'")
        first = server.autonomy_scheduler_tick()
        self.assertIn("supa::w1", first["pushed"])
        with server.connect() as conn:
            pushes = conn.execute(
                "SELECT COUNT(*) FROM runner_commands WHERE project_id='supa::w1' AND action='push' AND status='pending'"
            ).fetchone()[0]
            runtime = conn.execute(
                "SELECT last_dispatch_at,last_reason FROM autonomy_runtime WHERE project_id='supa'"
            ).fetchone()
        self.assertEqual(1, pushes)
        self.assertIsNotNone(runtime["last_dispatch_at"])

        second = server.autonomy_scheduler_tick()
        self.assertNotIn("supa", second["pushed"])
        with server.connect() as conn:
            pushes = conn.execute(
                "SELECT COUNT(*) FROM runner_commands WHERE project_id='supa::w1' AND action='push' AND status='pending'"
            ).fetchone()[0]
        self.assertEqual(1, pushes)

    def test_per_worker_interval_waits_for_generation_boundary_and_five_minute_floor(self):
        ts = (datetime.now(timezone.utc) - timedelta(seconds=301)).isoformat()
        with server.connect() as conn:
            conn.execute("UPDATE runner_targets SET active=1 WHERE project_id='supa'")
            conn.execute("DELETE FROM runner_commands WHERE project_id='supa'")
            conn.execute(
                "UPDATE autonomy_runtime SET last_dispatch_at=?,manual_pause=0 WHERE project_id='supa'",
                (ts,),
            )
            conn.execute(
                "INSERT INTO runner_events(ts,event,project_id,worker_slot,generating,sending) VALUES(?,?,?,?,?,?)",
                (ts, "prompt-sent", "supa", 1, 0, 0),
            )
            conn.execute(
                "INSERT INTO runner_events(ts,event,project_id,worker_slot,generating,sending) VALUES(?,?,?,?,?,?)",
                (ts, "generation-started", "supa", 1, 1, 0),
            )

        self.assertFalse(server._autonomy_enqueue_push("supa", "still-generating", 0))

        with server.connect() as conn:
            conn.execute(
                "INSERT INTO runner_events(ts,event,project_id,worker_slot,generating,sending) VALUES(?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), "awaiting-vps-dispatch", "supa", 1, 0, 0),
            )

        self.assertTrue(server._autonomy_enqueue_push("supa", "generation-finished", 0))
        with server.connect() as conn:
            pushes = conn.execute(
                "SELECT COUNT(*) FROM runner_commands WHERE project_id='supa::w1' AND action='push' AND status='pending'"
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

    def test_local_executor_contract_never_dispatches_chatgpt_for_ftmo_or_haxlab(self):
        policy = json.loads(server.AUTONOMY_POLICY_FILE.read_text(encoding="utf-8"))
        policy["projects"]["ftmo"].update({
            "local_executor_owns_stages": True,
            "status_missing_fail_closed": True,
            "ai_stages": [],
        })
        policy["projects"]["haxlab"].update({
            "local_executor_owns_states": True,
            "status_missing_fail_closed": True,
            "ai_states": [],
        })
        server.AUTONOMY_POLICY_FILE.write_text(json.dumps(policy), encoding="utf-8")

        self.ftmo_status.write_text(json.dumps({
            "ok": True,
            "research": {"next_stage": "development"},
            "paper_forward_shadow": {"action": "idle"},
        }), encoding="utf-8")
        self.hax_status.write_text(json.dumps({"state": "NEEDS_AI"}), encoding="utf-8")

        self.assertFalse(server.project_autonomy_state("ftmo")["allow_ai"])
        self.assertEqual("ftmo_local_executor", server.project_autonomy_state("ftmo")["reason"])
        self.assertFalse(server.project_autonomy_state("haxlab")["allow_ai"])
        self.assertEqual("haxlab_local_executor", server.project_autonomy_state("haxlab")["reason"])

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
