import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from scripts import zcloud_scheduler_decision_report as decision


class SchedulerDecisionReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-scheduler-decision-")
        self.root = Path(self.tmp.name)
        self.db = self.root / "history.db"
        self.contracts = self.root / "project-contracts.json"
        self.meminfo = self.root / "meminfo"
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("CREATE TABLE runtime_settings(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT,actor TEXT)")
            c.execute("CREATE TABLE ai_global_slots(slot INTEGER PRIMARY KEY,project_id TEXT,worker_slot INTEGER,assigned_at TEXT)")
            c.execute("CREATE TABLE runner_targets(project_id TEXT PRIMARY KEY,worker_count INTEGER,active INTEGER)")
            c.execute("""CREATE TABLE portfolio_queue(
                queue_id TEXT PRIMARY KEY,project_id TEXT,priority TEXT,status TEXT,eligible INTEGER,
                worker_slot INTEGER,created_at TEXT,updated_at TEXT
            )""")
            c.commit()
        self.contracts.write_text(json.dumps({
            "defaults": {"ai_worker_cap": 1},
            "projects": {
                "lightup": {
                    "queue_mode": "execution",
                    "lane_profile": "security-lab",
                    "ai_worker_cap": 2,
                    "compute": {"priority": "turbo", "pool": "heavy", "protected": False},
                },
                "zguard": {
                    "queue_mode": "execution",
                    "lane_profile": "product",
                    "ai_worker_cap": 1,
                    "compute": {"priority": "normal", "pool": "build", "protected": False},
                },
                "ulab": {
                    "queue_mode": "human-gated",
                    "lane_profile": "human-gated",
                    "ai_worker_cap": 0,
                    "compute": {"priority": "background", "pool": "disabled", "protected": False},
                },
            },
        }), encoding="utf-8")
        self.write_meminfo(8192, 2048, 2048)
        self.now = datetime(2026, 10, 6, 9, 50, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def write_meminfo(self, available_mb, swap_total_mb, swap_free_mb):
        self.meminfo.write_text(
            "\n".join([
                "MemTotal:       12288000 kB",
                f"MemAvailable:  {available_mb * 1024} kB",
                f"SwapTotal:     {swap_total_mb * 1024} kB",
                f"SwapFree:      {swap_free_mb * 1024} kB",
            ]) + "\n",
            encoding="utf-8",
        )

    def setting(self, key, value):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT OR REPLACE INTO runtime_settings VALUES(?,?,?,?)", (key, str(value), self.now.isoformat(), "test"))
            c.commit()

    def queue(self, queue_id, project_id, priority="P2", status="queued", eligible=1, worker_slot=None):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute(
                "INSERT INTO portfolio_queue VALUES(?,?,?,?,?,?,?,?)",
                (queue_id, project_id, priority, status, eligible, worker_slot, self.now.isoformat(), self.now.isoformat()),
            )
            c.commit()

    def slot(self, slot, project_id, worker_slot=1):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute(
                "INSERT INTO ai_global_slots VALUES(?,?,?,?)",
                (slot, project_id, worker_slot, self.now.isoformat()),
            )
            c.commit()

    def target(self, project_id, worker_count, active=1):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute(
                "INSERT OR REPLACE INTO runner_targets VALUES(?,?,?)",
                (project_id, worker_count, active),
            )
            c.commit()

    def project(self, payload, project_id):
        return next(item for item in payload["projects"] if item["project_id"] == project_id)

    def test_provider_runtime_counts_define_global_pool_and_pool_full_reason(self):
        self.setting("dynamic_worker_chatgpt_count", 1)
        self.setting("dynamic_worker_claude_count", 0)
        self.slot(1, "lightup")
        self.queue("zguard-1", "zguard", "P2")
        payload = decision.report(self.db, self.contracts, self.meminfo, now=self.now)
        self.assertEqual(1, payload["pool"]["configured_limit"])
        self.assertEqual("provider_runtime_settings", payload["pool"]["limit_source"])
        zguard = self.project(payload, "zguard")
        self.assertEqual("waiting", zguard["decision_state"])
        self.assertIn("global_worker_pool_full", zguard["reason_codes"])

    def test_disabled_global_pool_does_not_erase_project_contract_cap(self):
        self.setting("dynamic_worker_limit", 0)
        self.queue("lightup-1", "lightup", "P0")
        payload = decision.report(self.db, self.contracts, self.meminfo, now=self.now)
        lightup = self.project(payload, "lightup")
        self.assertEqual("disabled", payload["pool"]["state"])
        self.assertEqual(2, lightup["contract_ai_worker_cap"])
        self.assertEqual(1, lightup["hard_cap"])
        self.assertIn("global_worker_pool_disabled", lightup["reason_codes"])
        self.assertNotIn("project_hard_cap_zero", lightup["reason_codes"])

    def test_pool_reports_stale_overallocation_explicitly(self):
        self.setting("dynamic_worker_limit", 1)
        self.slot(1, "lightup", 1)
        self.slot(2, "lightup", 2)
        payload = decision.report(self.db, self.contracts, self.meminfo, now=self.now)
        self.assertEqual("overallocated", payload["pool"]["state"])
        self.assertEqual(1, payload["pool"]["overallocated_by"])

    def test_memory_guard_explains_free_but_unadmitted_slot(self):
        self.setting("dynamic_worker_limit", 2)
        self.queue("zguard-1", "zguard", "P2")
        self.write_meminfo(1500, 0, 0)
        payload = decision.report(self.db, self.contracts, self.meminfo, now=self.now)
        self.assertEqual(2, payload["pool"]["free_slots"])
        self.assertEqual(0, payload["memory_admission"]["new_worker_capacity"])
        self.assertIn(
            "memory_admission_blocks_new_slot",
            self.project(payload, "zguard")["reason_codes"],
        )

    def test_requested_vs_allocated_capacity_is_explicit(self):
        self.setting("dynamic_worker_limit", 3)
        self.target("lightup", 2)
        self.slot(1, "lightup", 1)
        self.queue("lightup-2", "lightup", "P0")
        payload = decision.report(self.db, self.contracts, self.meminfo, now=self.now)
        lightup = self.project(payload, "lightup")
        self.assertEqual(2, lightup["requested_workers"])
        self.assertEqual(1, lightup["allocated_workers"])
        self.assertEqual(-1, lightup["allocation_delta"])
        self.assertIn("requested_capacity_unmet", lightup["reason_codes"])

    def test_inactive_target_requests_zero_capacity(self):
        self.setting("dynamic_worker_limit", 3)
        self.target("zguard", 2, active=0)
        payload = decision.report(self.db, self.contracts, self.meminfo, now=self.now)
        zguard = self.project(payload, "zguard")
        self.assertEqual(0, zguard["requested_workers"])
        self.assertEqual(0, zguard["allocation_delta"])

    def test_requested_capacity_is_not_called_unmet_without_ready_work(self):
        self.setting("dynamic_worker_limit", 3)
        self.target("lightup", 2)
        payload = decision.report(self.db, self.contracts, self.meminfo, now=self.now)
        lightup = self.project(payload, "lightup")
        self.assertEqual("configured_browser_capacity_not_allocator_demand", lightup["request_semantics"])
        self.assertNotIn("requested_capacity_unmet", lightup["reason_codes"])

    def test_recovery_hold_zeroes_effective_new_worker_capacity(self):
        self.setting("dynamic_worker_limit", 2)
        self.setting("worker_oom_recovery_hold_until", "2026-10-06T09:52:00+00:00")
        self.queue("zguard-1", "zguard", "P2")
        payload = decision.report(self.db, self.contracts, self.meminfo, now=self.now)
        self.assertGreater(payload["memory_admission"]["raw_new_worker_capacity"], 0)
        self.assertEqual(0, payload["memory_admission"]["new_worker_capacity"])
        self.assertEqual("2026-10-06T09:52:00+00:00", payload["memory_admission"]["recovery_hold_until"])
        self.assertIn(
            "recovery_hold_blocks_new_slot",
            self.project(payload, "zguard")["reason_codes"],
        )

    def test_read_only_connection_enables_query_only_defense_in_depth(self):
        with closing(decision._open_ro(self.db)) as c:
            self.assertEqual(1, c.execute("PRAGMA query_only").fetchone()[0])

    def test_non_execution_and_zero_cap_are_fail_closed_reasons(self):
        self.setting("dynamic_worker_limit", 2)
        self.queue("ulab-1", "ulab", "P1")
        payload = decision.report(self.db, self.contracts, self.meminfo, now=self.now)
        ulab = self.project(payload, "ulab")
        self.assertIn("queue_mode_non_execution", ulab["reason_codes"])
        self.assertIn("project_hard_cap_zero", ulab["reason_codes"])

    def test_candidate_order_matches_priority_then_age(self):
        self.setting("dynamic_worker_limit", 3)
        self.queue("zguard-old", "zguard", "P2")
        self.queue("lightup-high", "lightup", "P0")
        payload = decision.report(self.db, self.contracts, self.meminfo, now=self.now)
        self.assertEqual(
            "coarse_priority_and_manual_start_order_not_full_allocator_replay",
            payload["candidate_order_semantics"],
        )
        self.assertEqual("lightup-high", payload["candidate_order"][0]["queue_id"])
        self.assertIn(
            "higher_priority_queue_work_present",
            self.project(payload, "zguard")["reason_codes"],
        )

    def test_report_keeps_database_read_only(self):
        self.setting("dynamic_worker_limit", 1)
        self.queue("zguard-1", "zguard")
        before = self.db.stat().st_mtime_ns
        decision.report(self.db, self.contracts, self.meminfo, now=self.now)
        after = self.db.stat().st_mtime_ns
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
