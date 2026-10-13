"""Offline functional tests for the standalone read-only worker reconciler."""
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "zcloud_control_plane_worker_reconcile.py"
spec = importlib.util.spec_from_file_location("zcloud_worker_reconcile", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def live(items=None):
    return {"chatgpt_runners": {
        "cloud": {"workers": list(items or [])}
    }}


def worker(**updates):
    item = {"worker_id": "cloud::w1", "worker_slot": 1, "active": True,
            "state": "live", "generating": False, "sending": False, "age_seconds": 20}
    item.update(updates)
    return item


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "history.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE ai_global_slots(slot INTEGER,project_id TEXT,worker_slot INTEGER)")
            conn.execute("CREATE TABLE runner_workers(project_id TEXT,worker_slot INTEGER,desired_state TEXT,conversation_id TEXT)")
            conn.execute("CREATE TABLE runner_commands(id INTEGER PRIMARY KEY,project_id TEXT,action TEXT,status TEXT,created_at TEXT,updated_at TEXT,result TEXT)")
            conn.execute("CREATE TABLE runner_targets(project_id TEXT PRIMARY KEY,active INTEGER,worker_count INTEGER)")
            conn.execute("CREATE TABLE portfolio_queue(queue_id TEXT PRIMARY KEY,project_id TEXT,worker_slot INTEGER,status TEXT,claim_expires TEXT,updated_at TEXT)")
            conn.execute("INSERT INTO portfolio_queue VALUES('private-claim-id','cloud',1,'running',NULL,'2026-10-13')")
            conn.execute("INSERT INTO runner_targets VALUES('cloud',1,2)")
            conn.execute("INSERT INTO ai_global_slots VALUES(1,'cloud',1)")
            conn.execute("INSERT INTO runner_workers VALUES('cloud',1,'running','private-conversation')")
        self.roster = module.read_roster(self.db)

    def diagnose(self, payload=None):
        return module.reconcile(self.roster, payload if payload is not None else live([worker()]))

    def test_healthy_assigned_idle(self):
        result = self.diagnose()
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["allocated_count"], 1)
        self.assertEqual(result["recent_idle_count"], 1)
        self.assertFalse(result["authorizes_mutation"])
        self.assertNotIn("private-conversation", json.dumps(result))

    def test_actively_generating_is_not_stalled(self):
        result = self.diagnose(live([worker(generating=True, age_seconds=20)]))
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["busy_count"], 1)

    def test_idle_older_than_live_api_window_is_not_healthy(self):
        # A truly live worker has <=90s since its last event. The server
        # switches to stale/offline thereafter; contradictory fixtures deny.
        result = self.diagnose(live([worker(age_seconds=301)]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problems"][0]["code"], "worker_activity_unknown")
        self.assertFalse(result["authorizes_mutation"])

    def test_missing_observation_is_not_assumed_missing_tab(self):
        result = self.diagnose(live([]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problems"][0]["code"], "active_worker_not_observed")
        self.assertIn("do not prove missing tabs", result["limitations"][0])

    def test_explicitly_inactive_worker_is_unobserved(self):
        result = self.diagnose(live([worker(active=False)]))
        self.assertEqual(result["state"], "degraded")

    def test_active_configured_worker_can_still_be_offline(self):
        result = self.diagnose(live([worker(state="offline", age_seconds=999999)]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problems"][0]["code"], "worker_not_live")
        self.assertEqual(result["recent_idle_count"], 0)

    def test_stale_worker_is_not_reported_healthy(self):
        result = self.diagnose(live([worker(state="stale", age_seconds=200)]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problems"][0]["code"], "worker_not_live")

    def test_missing_heartbeat_age_is_unknown(self):
        result = self.diagnose(live([worker(age_seconds=None)]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problems"][0]["code"], "worker_activity_unknown")

    def test_starting_worker_is_attention_not_verified_idle(self):
        result = self.diagnose(live([worker(state="starting", age_seconds=None)]))
        self.assertEqual(result["state"], "attention")
        self.assertEqual(result["problems"][0]["code"], "worker_not_ready")

    def test_missing_boolean_activity_is_unknown(self):
        item = worker()
        item.pop("sending")
        result = self.diagnose(live([item]))
        self.assertEqual(result["problems"][0]["code"], "worker_activity_unknown")

    def test_paused_target_does_not_prompt(self):
        self.roster["targets"][("cloud", 1)] = "paused"
        result = self.diagnose()
        self.assertEqual(result["problems"][0]["code"], "allocated_but_not_runnable")

    def test_orphan_allocation(self):
        self.roster["targets"].clear()
        result = self.diagnose()
        self.assertEqual(result["problems"][0]["code"], "allocation_without_target")

    def test_multiple_independent_slots(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO ai_global_slots VALUES(2,'cloud',2)")
            conn.execute("INSERT INTO runner_workers VALUES('cloud',2,'running','redacted')")
        roster = module.read_roster(self.db)
        report = module.reconcile(roster, live([
            worker(), {"worker_id": "cloud::w2", "worker_slot": 2, "active": True,
                       "state": "live", "age_seconds": 15,
                       "generating": True, "sending": False}
        ]))
        self.assertEqual(report["allocated_count"], 2)
        self.assertEqual(report["busy_count"], 1)

    def test_unallocated_live_worker_never_implies_scheduler_permission(self):
        report = self.diagnose(live([worker(),
            {"worker_id": "cloud::w2", "worker_slot": 2, "active": True, "state": "live",
             "age_seconds": 15, "generating": True, "sending": False}
        ]))
        self.assertFalse(report["authorizes_mutation"])
        self.assertEqual(report["allocated_count"], 1)

    def test_stale_pending_push_detected_without_command_body(self):
        from datetime import datetime, timezone
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO runner_commands VALUES(1,'cloud::w1','push','pending',?,?,?)",
                         ("2026-10-13T17:00:00+00:00", "2026-10-13T17:00:00+00:00", "SECRET_COMMAND_RESULT"))
        roster = module.read_roster(self.db)
        at = datetime(2026, 10, 13, 17, 3, tzinfo=timezone.utc)
        result = module.reconcile(roster, live([worker()]), now=at)
        self.assertEqual(result["stale_pending_dispatch_count"], 1)
        self.assertIn("pending_dispatch_over_two_minutes", result["problem_counts"])
        self.assertNotIn("SECRET_COMMAND_RESULT", json.dumps(result))

    def test_recent_pending_command_is_counted_not_stalled(self):
        from datetime import datetime, timezone
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO runner_commands VALUES(1,'cloud::w1','start','pending',?,?,?)",
                         ("2026-10-13T17:00:00+00:00", "2026-10-13T17:00:00+00:00", "private"))
        at = datetime(2026, 10, 13, 17, 1, tzinfo=timezone.utc)
        result = module.reconcile(module.read_roster(self.db), live([worker()]), now=at)
        self.assertEqual(result["pending_dispatch_count"], 1)
        self.assertEqual(result["stale_pending_dispatch_count"], 0)
        self.assertEqual(result["state"], "ok")

    def test_future_pending_command_timestamp_fails_closed(self):
        from datetime import datetime, timezone
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO runner_commands VALUES(1,'cloud::w1','force_start','pending',?,?,?)",
                         ("2026-10-14T17:00:00+00:00", "2026-10-14T17:00:00+00:00", "private"))
        at = datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc)
        result = module.reconcile(module.read_roster(self.db), live([worker()]), now=at)
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problem_counts"]["pending_age_unverifiable"], 1)

    def test_legacy_project_scoped_pending_command_is_not_a_worker_binding(self):
        from datetime import datetime, timezone
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO runner_commands VALUES(1,'cloud','start','pending',?,?,?)",
                         ("2026-10-12T17:00:00+00:00", "2026-10-12T17:00:00+00:00", "legacy"))
        at = datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc)
        result = module.reconcile(module.read_roster(self.db), live([worker()]), now=at)
        self.assertEqual(result["pending_dispatch_count"], 0)
        self.assertEqual(result["state"], "ok")

    def test_non_pending_completed_dispatch_is_not_stale(self):
        from datetime import datetime, timezone
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO runner_commands VALUES(1,'cloud::w1','start','succeeded',?,?,?)",
                         ("2026-10-13T13:00:00+00:00", "2026-10-13T13:00:00+00:00", "private"))
        at = datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc)
        result = module.reconcile(module.read_roster(self.db), live([worker()]), now=at)
        self.assertEqual(result["pending_dispatch_count"], 0)
        self.assertEqual(result["state"], "ok")

    def test_generation_stalled_is_not_successful_busy_evidence(self):
        result = self.diagnose(live([worker(state="stalled", generating=True,
                                            age_seconds=12)]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["busy_count"], 0)
        self.assertEqual(result["problem_counts"]["generation_stalled"], 1)

    def test_stale_generating_state_cannot_override_offline(self):
        result = self.diagnose(live([worker(state="offline", generating=True,
                                            age_seconds=5000)]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problem_counts"]["worker_not_live"], 1)
        self.assertEqual(result["busy_count"], 0)

    def test_missing_live_state_fails_closed(self):
        item = worker()
        item.pop("state")
        result = self.diagnose(live([item]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problem_counts"]["worker_state_unknown"], 1)

    def test_unrecognised_live_state_fails_closed(self):
        result = self.diagnose(live([worker(state="connected-but-not-proven")]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problem_counts"]["worker_state_unknown"], 1)

    def test_live_age_boundary(self):
        self.assertEqual(self.diagnose(live([worker(age_seconds=90)]))["state"], "ok")
        self.assertEqual(self.diagnose(live([worker(age_seconds=91)]))["state"], "degraded")

    def test_slot_outside_configured_worker_count_is_degraded(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO ai_global_slots VALUES(2,'cloud',2)")
            conn.execute("INSERT INTO runner_workers VALUES('cloud',2,'running','redacted')")
            conn.execute("UPDATE runner_targets SET worker_count=1 WHERE project_id='cloud'")
        roster = module.read_roster(self.db)
        report = module.reconcile(roster, live([worker(),
            {"worker_id": "cloud::w2", "worker_slot": 2, "state": "live",
             "active": True, "generating": False, "sending": False, "age_seconds": 20}
        ]))
        self.assertEqual(report["state"], "degraded")
        self.assertEqual(report["problem_counts"]["allocation_outside_worker_count"], 1)

    def test_missing_project_target_is_not_a_valid_worker_allocation(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM runner_targets WHERE project_id='cloud'")
        report = module.reconcile(module.read_roster(self.db), live([worker()]))
        self.assertEqual(report["problem_counts"]["allocation_without_project_target"], 1)

    def test_invalid_project_target_boolean_and_count_fail_closed(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE runner_targets SET active=2 WHERE project_id='cloud'")
        with self.assertRaisesRegex(module.InvalidEvidence, "invalid project active"):
            module.read_roster(self.db)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE runner_targets SET active=1,worker_count=0 WHERE project_id='cloud'")
        with self.assertRaisesRegex(module.InvalidEvidence, "invalid target worker count"):
            module.read_roster(self.db)

    def test_missing_project_targets_table_denied(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE runner_targets")
        with self.assertRaisesRegex(module.InvalidEvidence, "missing runner_targets"):
            module.read_roster(self.db)

    def test_global_slot_queue_project_mismatch_is_degraded(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE portfolio_queue SET project_id='supa' WHERE worker_slot=1")
        result = module.reconcile(module.read_roster(self.db), live([worker()]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problem_counts"]["queue_claim_allocation_mismatch"], 1)
        self.assertNotIn("private-claim-id", json.dumps(result))

    def test_multiple_queue_claims_in_one_global_slot_are_rejected(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO portfolio_queue VALUES('private-second','supa',1,'claimed',NULL,'2026-10-13')")
        result = module.reconcile(module.read_roster(self.db), live([worker()]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problem_counts"]["multiple_live_queue_claims"], 1)
        self.assertNotIn("private-second", json.dumps(result))

    def test_claimed_task_without_any_allocated_worker_is_attention(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM ai_global_slots")
        result = module.reconcile(module.read_roster(self.db), live([]))
        self.assertEqual(result["state"], "attention")
        self.assertEqual(result["allocated_count"], 0)
        self.assertEqual(result["problem_counts"]["live_queue_claim_without_global_slot"], 1)
        self.assertFalse(result["authorizes_mutation"])

    def test_unallocated_double_claim_is_degraded_without_leaking_queue_ids(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM ai_global_slots")
            conn.execute("INSERT INTO portfolio_queue VALUES('sensitive-queue-id','supa',1,'claimed',NULL,'2026-10-13')")
        result = module.reconcile(module.read_roster(self.db), live([]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problem_counts"]["multiple_live_queue_claims"], 1)
        self.assertNotIn("sensitive-queue-id", json.dumps(result))

    def test_missing_queue_claim_cannot_certify_worker_is_ready(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM portfolio_queue")
        result = module.reconcile(module.read_roster(self.db), live([worker()]))
        self.assertEqual(result["state"], "attention")
        self.assertEqual(result["problem_counts"]["allocation_without_live_queue_claim"], 1)
        self.assertFalse(result["authorizes_mutation"])

    def test_expired_queue_claim_does_not_certify_current_ownership(self):
        from datetime import datetime, timezone
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE portfolio_queue SET claim_expires='2026-10-12T23:00:00+00:00'")
        at = datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc)
        result = module.reconcile(module.read_roster(self.db), live([worker()]), now=at)
        self.assertEqual(result["state"], "attention")
        self.assertEqual(result["problem_counts"]["allocation_without_live_queue_claim"], 1)

    def test_bad_queue_claim_deadline_fails_closed_without_leaking(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE portfolio_queue SET claim_expires='not-a-timestamp'")
        result = module.reconcile(module.read_roster(self.db), live([worker()]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problem_counts"]["queue_claim_deadline_unverifiable"], 1)
        self.assertNotIn("not-a-timestamp", json.dumps(result))

    def test_queue_change_during_http_read_requires_retry(self):
        def change_claim(_fixture):
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE portfolio_queue SET project_id='supa' WHERE worker_slot=1")
            return live([worker()])
        with mock.patch.object(module, "read_live", side_effect=change_claim):
            with self.assertRaisesRegex(module.InvalidEvidence, "scheduler changed"):
                module.observe(self.db)

    def test_queue_claim_does_not_leak_raw_evidence_on_cli(self):
        fixture = Path(self.tmp.name) / "live-queue-fixture.json"
        fixture.write_text(json.dumps(live([worker()])), encoding="utf-8")
        result = subprocess.run([sys.executable, str(SOURCE),
                                 "--db", str(self.db), "--runner-live-fixture", str(fixture)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "ok")
        self.assertNotIn("private-claim-id", result.stdout)

    def test_mismatched_worker_id_and_reported_slot_rejected(self):
        with self.assertRaisesRegex(module.InvalidEvidence, "disagree"):
            self.diagnose(live([worker(worker_id="cloud::w2", worker_slot=1)]))

    def test_mismatched_identity_cannot_claim_different_worker_slot(self):
        with self.assertRaisesRegex(module.InvalidEvidence, "disagree"):
            self.diagnose(live([worker(worker_id="cloud::w1", worker_slot=2)]))

    def test_project_only_alias_cannot_impersonate_canonical_worker_slot(self):
        with self.assertRaisesRegex(module.InvalidEvidence, "unmatchable"):
            self.diagnose(live([worker(worker_id="cloud", worker_slot=1)]))

    def test_extremely_nested_live_json_fails_closed(self):
        raw = b'{"chatgpt_runners":' + b'[' * 1600 + b'0' + b']' * 1600 + b'}'
        with self.assertRaises(module.InvalidEvidence):
            module._strict_json(raw)

    def test_noncanonical_worker_identity_rejected(self):
        with self.assertRaisesRegex(module.InvalidEvidence, "noncanonical"):
            self.diagnose(live([worker(worker_id="cloud::w01")]))

    def test_cross_project_worker_identity_rejected(self):
        with self.assertRaisesRegex(module.InvalidEvidence, "unmatchable"):
            self.diagnose(live([worker(worker_id="supa::w1")]))

    def test_unknown_desired_state_fails_closed_before_reporting_healthy(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE runner_workers SET desired_state='forever-running'")
        with self.assertRaisesRegex(module.InvalidEvidence, "invalid desired worker state"):
            module.read_roster(self.db)

    def test_live_observation_bracketed_by_consistent_sqlite_rosters(self):
        with mock.patch.object(module, "read_live", return_value=live([worker()])):
            with mock.patch.object(module, "read_roster", wraps=module.read_roster) as read:
                result = module.observe(self.db)
        self.assertEqual(result["state"], "ok")
        self.assertEqual(read.call_count, 2)

    def test_roster_reallocation_during_api_read_denies_false_missing_worker(self):
        def change_allocation(_fixture):
            # Simulate real scheduler churn BETWEEN SQLite reads.
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE ai_global_slots SET project_id='supa' WHERE slot=1")
            return live([worker()])
        with mock.patch.object(module, "read_live", side_effect=change_allocation):
            with self.assertRaisesRegex(module.InvalidEvidence, "scheduler changed"):
                module.observe(self.db)

    def test_pending_dispatch_arriving_during_api_read_denies_false_healthy(self):
        def insert_pending(_fixture):
            with sqlite3.connect(self.db) as conn:
                conn.execute(
                    "INSERT INTO runner_commands VALUES(1,'cloud::w1','push','pending',?,?,?)",
                    ("2026-10-13T17:00:00+00:00", "2026-10-13T17:00:00+00:00", "secret"),
                )
            return live([worker()])
        with mock.patch.object(module, "read_live", side_effect=insert_pending):
            with self.assertRaisesRegex(module.InvalidEvidence, "scheduler changed"):
                module.observe(self.db)

    def test_duplicate_live_worker_rejected(self):
        with self.assertRaises(module.InvalidEvidence):
            self.diagnose(live([worker(), worker()]))

    def test_non_boolean_flags_rejected(self):
        with self.assertRaises(module.InvalidEvidence):
            self.diagnose(live([worker(generating="false")]))

    def test_duplicate_global_allocation_rejected(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO ai_global_slots VALUES(2,'cloud',1)")
        with self.assertRaises(module.InvalidEvidence):
            module.read_roster(self.db)

    def test_bool_or_zero_slot_rejected(self):
        self.assertRaises(module.InvalidEvidence, module._positive_int, True, "slot")
        self.assertRaises(module.InvalidEvidence, module._positive_int, 0, "slot")

    def test_schema_missing_fails_closed(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE runner_workers")
        with self.assertRaises(module.InvalidEvidence):
            module.read_roster(self.db)

    def test_symlink_database_denied(self):
        alias = Path(self.tmp.name) / "alias.db"
        alias.symlink_to(self.db)
        with self.assertRaises(module.InvalidEvidence):
            module.read_roster(alias)

    def test_empty_roster_is_valid_with_empty_runner_collection(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM ai_global_slots")
        result = module.reconcile(module.read_roster(self.db), live([]))
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["allocated_count"], 0)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(module.InvalidEvidence):
            module._strict_json(b'{"chatgpt_runners":{},"chatgpt_runners":{}}')

    def test_nonfinite_json_rejected(self):
        with self.assertRaises(module.InvalidEvidence):
            module._strict_json(b'{"chatgpt_runners":NaN}')

    def test_json_nesting_exact_boundary_is_accepted(self):
        level = module.MAX_JSON_DEPTH - 1  # top-level object counts as one
        raw = b'{"chatgpt_runners":' + b'[' * level + b'0' + b']' * level + b'}'
        value = module._strict_json(raw)
        self.assertIn("chatgpt_runners", value)

    def test_json_nesting_over_limit_denied(self):
        level = module.MAX_JSON_DEPTH
        raw = b'{"chatgpt_runners":' + b'[' * level + b'0' + b']' * level + b'}'
        with self.assertRaisesRegex(module.InvalidEvidence, "nesting exceeds limit"):
            module._strict_json(raw)

    def test_quoted_brackets_and_escaped_quotes_do_not_increment_nesting(self):
        tricky = "[(]} brackets " + chr(92) + chr(34) + " quote { } and backslash " + chr(92)
        value = {"chatgpt_runners": {}, "untrusted_ignored_metadata": tricky}
        raw = json.dumps(value).encode("utf-8")
        self.assertEqual(module._strict_json(raw), value)

    def test_exponent_overflow_cannot_bypass_nonfinite_json_denial(self):
        with self.assertRaisesRegex(module.InvalidEvidence, "non-finite JSON number"):
            module._strict_json(b'{"chatgpt_runners":{},"foo":1e999}')
        with self.assertRaisesRegex(module.InvalidEvidence, "non-finite JSON number"):
            module._strict_json(b'{"chatgpt_runners":{},"foo":-1e999}')

    def test_normal_finite_fraction_still_accepted(self):
        payload = module._strict_json(b'{"chatgpt_runners":{},"foo":1.25e-2}')
        self.assertEqual(payload["foo"], 0.0125)

    def test_oversize_json_rejected(self):
        with self.assertRaises(module.InvalidEvidence):
            module._strict_json(b'{' + b' ' * module.MAX_RESPONSE_BYTES + b'}')

    def test_fixture_cli_is_readonly_and_machine_readable(self):
        fixture = Path(self.tmp.name) / "live.json"
        fixture.write_text(json.dumps(live([worker()])), encoding="utf-8")
        before = self.db.read_bytes()
        result = subprocess.run(
            [sys.executable, str(SOURCE), "--db", str(self.db),
             "--runner-live-fixture", str(fixture)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "ok")
        self.assertEqual(before, self.db.read_bytes())
        with sqlite3.connect(self.db) as conn:
            stored = conn.execute("SELECT conversation_id FROM runner_workers").fetchone()[0]
        self.assertEqual(stored, "private-conversation")
        self.assertNotIn("private-conversation", result.stdout)

    def test_invalid_fixture_cli_fails_closed_with_no_private_data(self):
        fixture = Path(self.tmp.name) / "bad.json"
        fixture.write_bytes(b'{"chatgpt_runners":{},"chatgpt_runners":{}}')
        result = subprocess.run(
            [sys.executable, str(SOURCE), "--db", str(self.db),
             "--runner-live-fixture", str(fixture)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["state"], "unknown")
        self.assertNotIn("private-conversation", result.stdout)


if __name__ == "__main__":
    unittest.main()
