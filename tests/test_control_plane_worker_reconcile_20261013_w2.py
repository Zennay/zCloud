"""Offline functional tests for the standalone read-only worker reconciler."""
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "zcloud_control_plane_worker_reconcile.py"
spec = importlib.util.spec_from_file_location("zcloud_worker_reconcile", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def live(items=None):
    return {"time": datetime.now(timezone.utc).isoformat(), "chatgpt_runners": {
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
            conn.execute("CREATE TABLE ai_global_slots(slot INTEGER,project_id TEXT,worker_slot INTEGER,assigned_at TEXT)")
            conn.execute("CREATE TABLE runner_workers(project_id TEXT,worker_slot INTEGER,desired_state TEXT,conversation_id TEXT)")
            conn.execute("CREATE TABLE runner_commands(id INTEGER PRIMARY KEY,project_id TEXT,action TEXT,status TEXT,created_at TEXT,updated_at TEXT,result TEXT)")
            conn.execute("CREATE TABLE runner_targets(project_id TEXT PRIMARY KEY,active INTEGER,worker_count INTEGER)")
            conn.execute("CREATE TABLE portfolio_queue(queue_id TEXT PRIMARY KEY,project_id TEXT,worker_slot INTEGER,status TEXT,claim_expires TEXT,updated_at TEXT)")
            conn.execute("INSERT INTO portfolio_queue VALUES('private-claim-id','cloud',1,'running',NULL,'2026-10-13')")
            conn.execute("INSERT INTO runner_targets VALUES('cloud',1,2)")
            conn.execute("INSERT INTO ai_global_slots VALUES(1,'cloud',1,'2026-10-13T17:00:00Z')")
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
            conn.execute("INSERT INTO ai_global_slots VALUES(2,'cloud',2,'2026-10-13T17:00:00Z')")
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

    def test_appearing_active_without_persisted_global_slot_is_attention_only(self):
        payload = live([
            worker(),
            {"worker_id": "cloud::w2", "worker_slot": 2, "active": True,
             "state": "live", "generating": False, "sending": False, "age_seconds": 20}
        ])
        result = self.diagnose(payload)
        self.assertEqual(result["state"], "attention")
        self.assertEqual(result["problem_counts"]["active_observation_without_persisted_slot"], 1)
        self.assertEqual(result["allocated_count"], 1)
        self.assertFalse(result["authorizes_mutation"])

    def test_unallocated_inactive_worker_does_not_trigger_orphan_warning(self):
        payload = live([
            worker(),
            {"worker_id": "cloud::w2", "worker_slot": 2, "active": False,
             "state": "paused", "generating": False, "sending": False, "age_seconds": None}
        ])
        result = self.diagnose(payload)
        self.assertEqual(result["state"], "ok")
        self.assertNotIn("active_observation_without_persisted_slot", result["problem_counts"])

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

    def test_scheduler_expands_worker_count_to_cover_second_allocation(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO ai_global_slots VALUES(2,'cloud',2,'2026-10-13T17:00:00Z')")
            conn.execute("INSERT INTO runner_workers VALUES('cloud',2,'running','redacted')")
            conn.execute("UPDATE runner_targets SET worker_count=1 WHERE project_id='cloud'")
        roster = module.read_roster(self.db)
        report = module.reconcile(roster, live([worker(),
            {"worker_id": "cloud::w2", "worker_slot": 2, "state": "live",
             "active": True, "generating": False, "sending": False, "age_seconds": 20}
        ]))
        self.assertEqual(report["state"], "attention")
        self.assertEqual(report["problem_counts"]["worker_count_expanded_by_allocation"], 1)
        self.assertNotIn("allocation_outside_projected_worker_count", report["problem_counts"])

    def test_high_ordinal_outside_effective_projected_count_is_degraded(self):
        # Runtime count = max(configured 1, allocated count 2) = 2.
        # A third local ordinal is invisible even if it holds a global slot.
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO ai_global_slots VALUES(2,'cloud',3,'2026-10-13T17:00:00Z')")
            conn.execute("INSERT INTO runner_workers VALUES('cloud',3,'running','private')")
            conn.execute("UPDATE runner_targets SET worker_count=1 WHERE project_id='cloud'")
        roster = module.read_roster(self.db)
        result = module.reconcile(roster, live([worker()]))
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["problem_counts"]["allocation_outside_projected_worker_count"], 1)

    def test_single_allocated_high_ordinal_not_in_projected_count(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE ai_global_slots SET worker_slot=2 WHERE slot=1")
            conn.execute("INSERT INTO runner_workers VALUES('cloud',2,'running','private')")
            conn.execute("UPDATE runner_targets SET worker_count=1 WHERE project_id='cloud'")
        result = module.reconcile(module.read_roster(self.db), live([]))
        self.assertEqual(result["problem_counts"]["allocation_outside_projected_worker_count"], 1)

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

    def test_aba_reassignment_to_same_worker_detected_by_slot_watermark(self):
        def reassign_same_identity(_fixture):
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE ai_global_slots SET assigned_at='2026-10-13T17:05:00Z' WHERE slot=1")
            return live([worker()])
        with mock.patch.object(module, "read_live", side_effect=reassign_same_identity):
            with self.assertRaisesRegex(module.InvalidEvidence, "scheduler changed"):
                module.observe(self.db)

    def test_same_project_queue_claim_replacement_detected_by_private_id(self):
        def change_queue_identity(_fixture):
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE portfolio_queue SET queue_id='new-private-claim' WHERE worker_slot=1")
            return live([worker()])
        with mock.patch.object(module, "read_live", side_effect=change_queue_identity):
            with self.assertRaisesRegex(module.InvalidEvidence, "scheduler changed"):
                module.observe(self.db)

    def test_same_project_queue_claim_refresh_detected_by_watermark(self):
        def touch_claim(_fixture):
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE portfolio_queue SET updated_at='2026-10-13T17:06:00Z' WHERE worker_slot=1")
            return live([worker()])
        with mock.patch.object(module, "read_live", side_effect=touch_claim):
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
            conn.execute("INSERT INTO ai_global_slots VALUES(2,'cloud',1,'2026-10-13T17:00:00Z')")
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

    def test_fully_drained_queue_and_empty_roster_is_valid(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM ai_global_slots")
            conn.execute("UPDATE portfolio_queue SET status='done' WHERE worker_slot=1")
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

    def test_escaped_surrogate_value_is_denied_before_reconciliation(self):
        raw = b'{"chatgpt_runners":{},"ignored":"\\uD800"}'
        with self.assertRaisesRegex(module.InvalidEvidence, "invalid Unicode surrogate"):
            module._strict_json(raw)

    def test_escaped_surrogate_key_is_denied(self):
        raw = b'{"chatgpt_runners":{},"\\uDC00":"value"}'
        with self.assertRaisesRegex(module.InvalidEvidence, "invalid Unicode surrogate"):
            module._strict_json(raw)

    def test_valid_escaped_surrogate_pair_decodes_normally(self):
        raw = b'{"chatgpt_runners":{},"emoji":"\\uD83D\\uDE80"}'
        self.assertEqual(module._strict_json(raw)["emoji"], "🚀")

    def test_live_http_opener_disables_environment_proxy(self):
        # No actual network: inspect the configured transport while an
        # adversarial proxy is present in the worker process environment.
        from urllib.request import ProxyHandler
        fake_response = mock.MagicMock()
        fake_response.status = 200
        fake_response.headers.get_content_type.return_value = "application/json"
        fake_response.read.return_value = json.dumps(live([worker()])).encode()
        fake_opener = mock.MagicMock()
        fake_opener.open.return_value.__enter__.return_value = fake_response
        with mock.patch.dict("os.environ", {"HTTP_PROXY": "http://untrusted-proxy.invalid:9090"}):
            with mock.patch.object(module.urllib.request, "build_opener",
                                   return_value=fake_opener) as factory:
                result = module.read_live()
        self.assertIn("chatgpt_runners", result)
        handler, redirect = factory.call_args.args
        self.assertIsInstance(handler, ProxyHandler)
        self.assertEqual(handler.proxies, {})
        self.assertIsInstance(redirect, module._NoRedirect)
        request = fake_opener.open.call_args.args[0]
        self.assertEqual(request.full_url, module.API)
        self.assertEqual(request.get_method(), "GET")

    def test_oversize_json_rejected(self):
        with self.assertRaises(module.InvalidEvidence):
            module._strict_json(b'{' + b' ' * module.MAX_RESPONSE_BYTES + b'}')

    def _serve_live_response(self, *, status=200, content_type="application/json",
                             body=None, location=None):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        content = (json.dumps(live([worker()])).encode() if body is None else body)
        recorded = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                recorded.append((self.command, self.path))
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                if location is not None:
                    self.send_header("Location", location)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                try:
                    self.wfile.write(content)
                except BrokenPipeError:
                    pass

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/api/runner-live", recorded

    def test_real_localhost_get_ignores_hostile_http_proxy(self):
        url, recorded = self._serve_live_response()
        with mock.patch.object(module, "API", url):
            with mock.patch.dict("os.environ", {
                "HTTP_PROXY": "http://nonexistent-proxy.invalid:1337",
                "HTTPS_PROXY": "http://nonexistent-proxy.invalid:1337",
                "ALL_PROXY": "http://nonexistent-proxy.invalid:1337",
                "NO_PROXY": "",
            }):
                result = module.read_live()
        self.assertIn("chatgpt_runners", result)
        self.assertEqual(recorded, [("GET", "/api/runner-live")])

    def test_real_localhost_replayed_snapshot_denied(self):
        snapshot = live([worker()])
        snapshot["time"] = (datetime.now(timezone.utc) - timedelta(minutes=8)).isoformat()
        url, recorded = self._serve_live_response(body=json.dumps(snapshot).encode())
        with mock.patch.object(module, "API", url):
            with self.assertRaisesRegex(module.InvalidEvidence, "server time unverified"):
                module.read_live()
        self.assertEqual(recorded, [("GET", "/api/runner-live")])

    def test_real_localhost_future_snapshot_denied(self):
        snapshot = live([worker()])
        snapshot["time"] = (datetime.now(timezone.utc) + timedelta(minutes=8)).isoformat()
        url, _recorded = self._serve_live_response(body=json.dumps(snapshot).encode())
        with mock.patch.object(module, "API", url):
            with self.assertRaisesRegex(module.InvalidEvidence, "server time unverified"):
                module.read_live()

    def test_real_localhost_missing_server_timestamp_denied(self):
        snapshot = live([worker()])
        snapshot.pop("time")
        url, _recorded = self._serve_live_response(body=json.dumps(snapshot).encode())
        with mock.patch.object(module, "API", url):
            with self.assertRaisesRegex(module.InvalidEvidence, "server time missing"):
                module.read_live()

    def test_real_localhost_naive_server_timestamp_denied(self):
        snapshot = live([worker()])
        snapshot["time"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        url, _recorded = self._serve_live_response(body=json.dumps(snapshot).encode())
        with mock.patch.object(module, "API", url):
            with self.assertRaisesRegex(module.InvalidEvidence, "server time unverified"):
                module.read_live()

    def test_real_localhost_http_redirect_is_denied(self):
        url, recorded = self._serve_live_response(status=302,
                                                  location="http://untrusted.invalid/")
        with mock.patch.object(module, "API", url):
            with self.assertRaisesRegex(module.InvalidEvidence, "redirected"):
                module.read_live()
        self.assertEqual(recorded, [("GET", "/api/runner-live")])

    def test_real_localhost_non_json_content_type_fails_closed(self):
        url, recorded = self._serve_live_response(content_type="text/html")
        with mock.patch.object(module, "API", url):
            with self.assertRaisesRegex(module.InvalidEvidence, "application/json"):
                module.read_live()
        self.assertEqual(recorded, [("GET", "/api/runner-live")])

    def test_real_localhost_oversized_json_is_denied(self):
        url, recorded = self._serve_live_response(body=b'{' + b' ' * (module.MAX_RESPONSE_BYTES + 10))
        with mock.patch.object(module, "API", url):
            with self.assertRaisesRegex(module.InvalidEvidence, "oversized"):
                module.read_live()
        self.assertEqual(recorded, [("GET", "/api/runner-live")])

    def test_main_runner_live_get_keeps_worker_target_reconciliation_off(self):
        # Static contract, not proof of live database permissions. #1341 owns
        # broader GET writer remediation in the serialized server.py lane.
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("if u.path=='/api/runner-live':", source)
        self.assertIn("target_cfg=runner_targets()", source)
        self.assertIn("runner_statuses(target_cfg=target_cfg)", source)
        self.assertIn("runner_worker_targets(base=target_cfg,reconcile=False)", source)

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
        payload = json.loads(result.stdout)
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["evidence_mode"], "offline_fixture")
        self.assertFalse(payload["browser_generation_verified"])
        self.assertEqual(before, self.db.read_bytes())
        with sqlite3.connect(self.db) as conn:
            stored = conn.execute("SELECT conversation_id FROM runner_workers").fetchone()[0]
        self.assertEqual(stored, "private-conversation")
        self.assertNotIn("private-conversation", result.stdout)

    def test_live_cli_transport_error_keeps_evidence_provenance(self):
        # The real localhost service is intentionally not started; verify
        # failure provenance deterministically by patching read_live in-process.
        with mock.patch.object(module, "read_roster", return_value=self.roster):
            with mock.patch.object(module, "read_live",
                                   side_effect=module.InvalidEvidence("offline")):
                out = []
                with mock.patch.object(module, "print", side_effect=lambda *a, **k: out.append(a[0]),
                                       create=True):
                    code = module.main(["--db", str(self.db)])
        self.assertEqual(code, 2)
        report = json.loads(out[0])
        self.assertEqual(report["state"], "unknown")
        self.assertEqual(report["evidence_mode"], "loopback_http")
        self.assertFalse(report["browser_generation_verified"])
        self.assertNotIn("offline", out[0])

    def test_invalid_fixture_cli_fails_closed_with_no_private_data(self):
        fixture = Path(self.tmp.name) / "bad.json"
        fixture.write_bytes(b'{"chatgpt_runners":{},"chatgpt_runners":{}}')
        result = subprocess.run(
            [sys.executable, str(SOURCE), "--db", str(self.db),
             "--runner-live-fixture", str(fixture)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        failure = json.loads(result.stdout)
        self.assertEqual(failure["state"], "unknown")
        self.assertEqual(failure["evidence_mode"], "offline_fixture")
        self.assertFalse(failure["browser_generation_verified"])
        self.assertNotIn("private-conversation", result.stdout)


if __name__ == "__main__":
    unittest.main()
