import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts import zcloud_recovery as recovery


class RecoveryTests(unittest.TestCase):
    def test_rollback_health_timeout_matches_systemd_start_budget(self):
        self.assertEqual(90.0, recovery.SERVICE_HEALTH_TIMEOUT_SECONDS)
        self.assertEqual(
            recovery.SERVICE_HEALTH_TIMEOUT_SECONDS,
            recovery.wait_service_healthy.__defaults__[0],
        )

    def test_health_probe_allows_slow_status_endpoint(self):
        self.assertEqual(
            8,
            recovery.http_healthy.__defaults__[1],
        )

    def test_lkg_capture_health_retries_transient_http_gap(self):
        self.assertEqual(30.0, recovery.LKG_CAPTURE_HEALTH_TIMEOUT_SECONDS)
        self.assertEqual(
            recovery.LKG_CAPTURE_HEALTH_TIMEOUT_SECONDS,
            recovery.wait_http_healthy.__defaults__[0],
        )
        with patch.object(
            recovery,
            "http_healthy",
            side_effect=[False, False, True],
        ) as probe, patch.object(recovery.time, "sleep") as sleep:
            self.assertTrue(
                recovery.wait_http_healthy(timeout=30.0, interval=0.5)
            )
        self.assertEqual(3, probe.call_count)
        self.assertEqual(2, sleep.call_count)


    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-recovery-test-")
        self.base = Path(self.tmp.name)
        self.root = self.base / "app"
        self.state = self.base / "state"
        self.root.mkdir()
        (self.root / "public").mkdir()
        (self.root / "scripts").mkdir()
        (self.root / "server.py").write_text("version-one\n")
        (self.root / "enhancements.py").write_text("helpers-one\n")
        (self.root / "projects.json").write_text('{"v":1}\n')
        (self.root / "project-layout.json").write_text('{"order":[]}\n')
        (self.root / "resource-policy.json").write_text('{"cloud":{"priority":"normal"}}\n')
        (self.root / "project-contracts.json").write_text('{"schema_version":1,"projects":{"cloud":{}}}\n')
        (self.root / "public" / "app.js").write_text("ui-one\n")
        (self.root / "scripts" / "job.py").write_text("job-one\n")
        (self.root / ".watch-token").write_text("secret-token\n")
        self._create_db("conversation-1")
        self.fake_service = {"active_state": "active", "sub_state": "running", "service": recovery.SERVICE}

    def tearDown(self):
        self.tmp.cleanup()

    def _create_db(self, conversation):
        c = sqlite3.connect(self.root / "history.db")
        c.execute("CREATE TABLE runner_targets(project_id TEXT, active INTEGER, worker_count INTEGER, conversation_id TEXT)")
        c.execute("CREATE TABLE runner_workers(project_id TEXT, worker_slot INTEGER, conversation_id TEXT)")
        c.execute("INSERT INTO runner_targets VALUES('cloud',1,1,?)", (conversation,))
        c.execute("INSERT INTO runner_workers VALUES('cloud',1,?)", (conversation,))
        c.commit(); c.close()

    def capture(self):
        return recovery.capture(
            self.root,
            self.state,
            "7/7 smoke green",
            require_health=False,
            service_state=self.fake_service,
            browser_state={"active_state": "active"},
        )

    def test_systemctl_command_uses_noninteractive_sudo_for_service_user(self):
        with patch.object(recovery.os, "geteuid", return_value=1000):
            self.assertEqual(
                ["sudo", "-n", "systemctl", "stop", recovery.SERVICE],
                recovery.systemctl_command("stop"),
            )
        with patch.object(recovery.os, "geteuid", return_value=0):
            self.assertEqual(
                ["systemctl", "start", recovery.SERVICE],
                recovery.systemctl_command("start"),
            )

    def test_capture_records_visible_lkg_without_runtime_secrets(self):
        manifest = self.capture()
        self.assertEqual("7/7 smoke green", manifest["evidence"])
        self.assertTrue((self.state / "last-known-good.json").exists())
        snap = self.state / "snapshots" / manifest["snapshot_id"] / "files"
        self.assertTrue((snap / "server.py").exists())
        self.assertTrue((snap / "project-contracts.json").exists())
        self.assertIn("project-contracts.json", manifest["config_hashes"])
        self.assertFalse((snap / "history.db").exists())
        self.assertFalse((snap / ".watch-token").exists())
        self.assertTrue(manifest["mapping_fingerprint"]["available"])

    def test_capture_allocates_unique_id_when_same_second_snapshot_exists(self):
        with patch.object(recovery, "snapshot_stamp", return_value="20261005T183309Z"):
            first = self.capture()
            second = self.capture()
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertTrue(
            second["snapshot_id"].startswith(first["snapshot_id"] + "-")
        )
        self.assertTrue(
            (self.state / "snapshots" / first["snapshot_id"] / "manifest.json").exists()
        )
        self.assertTrue(
            (self.state / "snapshots" / second["snapshot_id"] / "manifest.json").exists()
        )
        pointer = json.loads((self.state / "last-known-good.json").read_text())
        self.assertEqual(second["snapshot_id"], pointer["snapshot_id"])

    def test_capture_prunes_old_valid_snapshots_but_keeps_active_lkg(self):
        self.assertEqual(8, recovery.RECOVERY_SNAPSHOT_RETENTION)
        with patch.object(
            recovery,
            "snapshot_stamp",
            return_value="20261005T230000Z",
        ):
            manifests = [self.capture() for _ in range(10)]
        self.assertEqual(
            10,
            len({manifest["snapshot_id"] for manifest in manifests}),
        )
        snapshots = self.state / "snapshots"
        valid_dirs = sorted(
            path.name
            for path in snapshots.iterdir()
            if path.is_dir() and (path / "manifest.json").is_file()
        )
        self.assertEqual(8, len(valid_dirs))
        self.assertFalse((snapshots / manifests[0]["snapshot_id"]).exists())
        self.assertFalse((snapshots / manifests[1]["snapshot_id"]).exists())
        for manifest in manifests[2:]:
            self.assertTrue((snapshots / manifest["snapshot_id"]).exists())
        pointer = json.loads((self.state / "last-known-good.json").read_text())
        self.assertEqual(manifests[-1]["snapshot_id"], pointer["snapshot_id"])
        events = [
            json.loads(line)
            for line in (self.state / "recovery.log").read_text().splitlines()
        ]
        prune_events = [
            event for event in events
            if event.get("event") == "recovery_snapshots_pruned"
        ]
        self.assertTrue(prune_events)
        self.assertEqual(
            recovery.RECOVERY_SNAPSHOT_RETENTION,
            prune_events[-1]["retained"],
        )

    def test_pruning_preserves_unrecognized_snapshot_directories(self):
        unknown = self.state / "snapshots" / "manual-do-not-delete"
        unknown.mkdir(parents=True)
        (unknown / "notes.txt").write_text("operator evidence\n")
        manifests = [self.capture() for _ in range(10)]
        self.assertTrue(unknown.exists())
        self.assertTrue((unknown / "notes.txt").exists())
        valid = [
            path
            for path in (self.state / "snapshots").iterdir()
            if path.is_dir() and (path / "manifest.json").is_file()
        ]
        self.assertEqual(8, len(valid))
        pointer = json.loads((self.state / "last-known-good.json").read_text())
        self.assertEqual(manifests[-1]["snapshot_id"], pointer["snapshot_id"])

    def test_transaction_retention_removes_only_durably_terminal_transactions(self):
        self.assertEqual(8, recovery.RECOVERY_TRANSACTION_RETENTION)
        transactions = self.state / "transactions"
        transactions.mkdir(parents=True)
        entries = [
            ("tx-success-old", "rollback_succeeded", "2026-10-01T00:00:00+00:00"),
            ("tx-reverted-old", "rollback_failed_reverted", "2026-10-02T00:00:00+00:00"),
            ("tx-success-new", "rollback_succeeded", "2026-10-03T00:00:00+00:00"),
            ("tx-reverted-new", "rollback_failed_reverted", "2026-10-04T00:00:00+00:00"),
        ]
        for transaction_id, event, observed_at in entries:
            path = transactions / transaction_id
            path.mkdir()
            (path / "pre-rollback-files").mkdir()
            recovery.append_log(
                self.state,
                event,
                transaction_id=transaction_id,
            )
            rows = [
                json.loads(line)
                for line in (self.state / "recovery.log").read_text().splitlines()
            ]
            rows[-1]["time"] = observed_at
            (self.state / "recovery.log").write_text(
                "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
            )

        in_progress = transactions / "tx-in-progress"
        in_progress.mkdir()
        recovery.append_log(
            self.state,
            "rollback_started",
            transaction_id="tx-in-progress",
        )
        failed_revert = transactions / "tx-revert-failed"
        failed_revert.mkdir()
        recovery.append_log(
            self.state,
            "rollback_failed_revert_failed",
            transaction_id="tx-revert-failed",
        )
        terminal_then_restarted = transactions / "tx-terminal-then-restarted"
        terminal_then_restarted.mkdir()
        recovery.append_log(
            self.state,
            "rollback_succeeded",
            transaction_id="tx-terminal-then-restarted",
        )
        rows = [
            json.loads(line)
            for line in (self.state / "recovery.log").read_text().splitlines()
        ]
        rows[-1]["time"] = "2026-10-01T12:00:00+00:00"
        (self.state / "recovery.log").write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
        )
        recovery.append_log(
            self.state,
            "rollback_started",
            transaction_id="tx-terminal-then-restarted",
        )
        rows = [
            json.loads(line)
            for line in (self.state / "recovery.log").read_text().splitlines()
        ]
        rows[-1]["time"] = "2026-10-05T12:00:00+00:00"
        (self.state / "recovery.log").write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
        )

        terminal_then_revert_failed = transactions / "tx-terminal-then-revert-failed"
        terminal_then_revert_failed.mkdir()
        recovery.append_log(
            self.state,
            "rollback_succeeded",
            transaction_id="tx-terminal-then-revert-failed",
        )
        rows = [
            json.loads(line)
            for line in (self.state / "recovery.log").read_text().splitlines()
        ]
        rows[-1]["time"] = "2026-10-01T13:00:00+00:00"
        (self.state / "recovery.log").write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
        )
        recovery.append_log(
            self.state,
            "rollback_failed_revert_failed",
            transaction_id="tx-terminal-then-revert-failed",
        )
        rows = [
            json.loads(line)
            for line in (self.state / "recovery.log").read_text().splitlines()
        ]
        rows[-1]["time"] = "2026-10-05T13:00:00+00:00"
        (self.state / "recovery.log").write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
        )

        unknown = transactions / "manual-operator-evidence"
        unknown.mkdir()
        (unknown / "notes.txt").write_text("keep me\n")
        target = transactions / "symlink-target"
        target.mkdir()
        symlink = transactions / "tx-symlink"
        symlink.symlink_to(target, target_is_directory=True)
        recovery.append_log(
            self.state,
            "rollback_succeeded",
            transaction_id="tx-symlink",
        )

        removed = recovery.prune_recovery_transactions(self.state, retain=2)

        self.assertEqual(
            {"tx-success-old", "tx-reverted-old"},
            set(removed),
        )
        self.assertFalse((transactions / "tx-success-old").exists())
        self.assertFalse((transactions / "tx-reverted-old").exists())
        self.assertTrue((transactions / "tx-success-new").exists())
        self.assertTrue((transactions / "tx-reverted-new").exists())
        self.assertTrue(in_progress.exists())
        self.assertTrue(failed_revert.exists())
        self.assertTrue(terminal_then_restarted.exists())
        self.assertTrue(terminal_then_revert_failed.exists())
        self.assertTrue(unknown.exists())
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(target.exists())

    def test_capture_prunes_terminal_transaction_artifacts_and_logs_receipt(self):
        transactions = self.state / "transactions"
        transactions.mkdir(parents=True)
        for index in range(10):
            transaction_id = f"tx-{index:02d}"
            path = transactions / transaction_id
            path.mkdir()
            (path / "pre-rollback-files").mkdir()
            recovery.append_log(
                self.state,
                "rollback_succeeded",
                transaction_id=transaction_id,
            )

        manifest = self.capture()

        remaining = sorted(
            path.name
            for path in transactions.iterdir()
            if path.is_dir() and not path.is_symlink()
        )
        self.assertEqual(
            [f"tx-{index:02d}" for index in range(2, 10)],
            remaining,
        )
        events = [
            json.loads(line)
            for line in (self.state / "recovery.log").read_text().splitlines()
        ]
        prune = [
            event
            for event in events
            if event.get("event") == "recovery_transactions_pruned"
        ]
        self.assertEqual(1, len(prune))
        self.assertEqual(manifest["snapshot_id"], prune[0]["snapshot_id"])
        self.assertEqual(8, prune[0]["retained"])
        self.assertEqual(
            {"tx-00", "tx-01"},
            set(prune[0]["removed_transaction_ids"]),
        )

    def test_capture_remains_valid_when_transaction_retention_fails(self):
        with patch.object(
            recovery,
            "prune_recovery_transactions",
            side_effect=OSError("simulated transaction retention failure"),
        ):
            manifest = self.capture()

        pointer = json.loads((self.state / "last-known-good.json").read_text())
        self.assertEqual(manifest["snapshot_id"], pointer["snapshot_id"])
        events = [
            json.loads(line)
            for line in (self.state / "recovery.log").read_text().splitlines()
        ]
        failures = [
            event
            for event in events
            if event.get("event") == "recovery_transaction_prune_failed"
        ]
        self.assertEqual(1, len(failures))
        self.assertIn(
            "simulated transaction retention failure",
            failures[0]["error"],
        )

    def test_capture_remains_valid_when_retention_housekeeping_fails(self):
        with patch.object(
            recovery,
            "prune_recovery_snapshots",
            side_effect=OSError("simulated retention failure"),
        ):
            manifest = self.capture()

        pointer = json.loads((self.state / "last-known-good.json").read_text())
        self.assertEqual(manifest["snapshot_id"], pointer["snapshot_id"])
        self.assertTrue(
            (
                self.state
                / "snapshots"
                / manifest["snapshot_id"]
                / "manifest.json"
            ).exists()
        )
        events = [
            json.loads(line)
            for line in (self.state / "recovery.log").read_text().splitlines()
        ]
        failures = [
            event
            for event in events
            if event.get("event") == "recovery_snapshot_prune_failed"
        ]
        self.assertEqual(1, len(failures))
        self.assertIn("simulated retention failure", failures[0]["error"])

    def test_rollback_restores_source_and_preserves_chat_mapping(self):
        manifest = self.capture()
        before = recovery.mapping_fingerprint(self.root / "history.db")
        (self.root / "server.py").write_text("version-two\n")
        (self.root / "public" / "app.js").write_text("ui-two\n")
        (self.root / "public" / "new.js").write_text("new\n")
        (self.root / ".watch-token").write_text("new-secret\n")
        result = recovery.rollback(self.root, self.state, manage_service=False, verify_health=False)
        self.assertTrue(result["ok"])
        self.assertEqual(manifest["snapshot_id"], result["snapshot_id"])
        self.assertEqual("version-one\n", (self.root / "server.py").read_text())
        self.assertEqual("ui-one\n", (self.root / "public" / "app.js").read_text())
        self.assertFalse((self.root / "public" / "new.js").exists())
        self.assertEqual("new-secret\n", (self.root / ".watch-token").read_text())
        self.assertEqual(before["sha256"], recovery.mapping_fingerprint(self.root / "history.db")["sha256"])

    def test_mapping_fingerprint_tracks_chat_identity_not_runtime_allocation(self):
        before = recovery.mapping_fingerprint(self.root / "history.db")
        with sqlite3.connect(self.root / "history.db") as conn:
            conn.execute(
                "UPDATE runner_targets SET active=0, worker_count=5 WHERE project_id='cloud'"
            )
        self.assertEqual(
            before["sha256"],
            recovery.mapping_fingerprint(self.root / "history.db")["sha256"],
        )
        with sqlite3.connect(self.root / "history.db") as conn:
            conn.execute(
                "UPDATE runner_targets SET conversation_id='conversation-2' WHERE project_id='cloud'"
            )
        self.assertNotEqual(
            before["sha256"],
            recovery.mapping_fingerprint(self.root / "history.db")["sha256"],
        )

    def test_failed_rollback_reverts_to_pre_rollback_tree(self):
        self.capture()
        (self.root / "server.py").write_text("version-two\n")
        (self.root / "public" / "app.js").write_text("ui-two\n")
        with self.assertRaises(recovery.RecoveryError):
            recovery.rollback(self.root, self.state, manage_service=False, verify_health=False, test_fail_after=1)
        self.assertEqual("version-two\n", (self.root / "server.py").read_text())
        self.assertEqual("ui-two\n", (self.root / "public" / "app.js").read_text())
        events = [json.loads(line)["event"] for line in (self.state / "recovery.log").read_text().splitlines()]
        self.assertIn("rollback_failed_reverted", events)


if __name__ == "__main__":
    unittest.main(verbosity=2)