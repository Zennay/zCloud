import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_transactional_promote.py"
SPEC = importlib.util.spec_from_file_location("zcloud_transactional_promote", MODULE_PATH)
promote = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(promote)


class TransactionalPromotionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-promote-")
        base = Path(self.tmp.name)
        self.root = base / "live"
        self.candidate = base / "candidate"
        self.tx = base / "tx"
        for folder in (self.root, self.candidate):
            (folder / "public").mkdir(parents=True)
            (folder / "firefox-extension").mkdir(parents=True)
        self._write(self.root, "server.py", "old-server\n")
        self._write(self.root, "public/app.js", "old-app\n")
        self._write(self.root, "firefox-extension/background.js", "old-runner\n")
        self._write(self.root, "firefox-extension/manifest.json", '{"version":"old"}\n')
        self._write(self.candidate, "server.py", "new-server\n")
        self._write(self.candidate, "public/app.js", "new-app\n")
        self._write(self.candidate, "firefox-extension/background.js", "new-runner\n")
        self._write(self.candidate, "firefox-extension/manifest.json", '{"version":"new"}\n')
        self._write(self.candidate, "firefox-extension/recovery.js", "new-recovery\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, root, rel, value):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def test_validate_relpath_rejects_persistent_and_traversal_paths(self):
        for bad in (
            "../server.py",
            "/etc/passwd",
            ".git/config",
            "history.db",
            "history.db-wal",
            ".watch-token",
            "signals/test",
            "repos/project",
            "watch-tls.key",
        ):
            with self.subTest(path=bad):
                with self.assertRaises(promote.PromotionError):
                    promote.validate_relpath(bad)

    def test_candidate_must_be_separate_and_complete(self):
        with self.assertRaises(promote.PromotionError):
            promote.validate_candidate(self.root, self.root, ["server.py"])
        with self.assertRaises(promote.PromotionError):
            promote.validate_candidate(self.candidate, self.root, ["enhancements.py"])
        result = promote.validate_candidate(
            self.candidate, self.root, ["server.py", "public/app.js"]
        )
        self.assertEqual(
            hashlib.sha256(b"new-server\n").hexdigest(),
            result["server.py"],
        )

    def test_only_explicit_bootstrap_files_may_be_created(self):
        self._write(
            self.candidate,
            "autonomy-policy.json",
            '{"schema_version":1,"default":{"auto_start":false}}\n',
        )
        self._write(self.candidate, "portfolio_queue.seed.json", "[]\n")
        self._write(
            self.candidate,
            "vps-execution-policy.json",
            '{"schema_version":1,"enabled":true,"prompt_directive":"runner"}\n',
        )
        result = promote.validate_candidate(
            self.candidate,
            self.root,
            [
                "firefox-extension/recovery.js",
                "autonomy-policy.json",
                "portfolio_queue.seed.json",
                "vps-execution-policy.json",
            ],
        )
        self.assertIn("firefox-extension/recovery.js", result)
        self.assertIn("autonomy-policy.json", result)
        self.assertIn("portfolio_queue.seed.json", result)
        self.assertIn("vps-execution-policy.json", result)
        self._write(self.candidate, "firefox-extension/other-new.js", "nope\n")
        with self.assertRaises(promote.PromotionError):
            promote.validate_candidate(
                self.candidate,
                self.root,
                ["firefox-extension/other-new.js"],
            )

    def test_transactional_replace_promotes_all_requested_files(self):
        hashes = promote.transactional_replace(
            self.candidate,
            self.root,
            ["server.py", "public/app.js"],
            self.tx,
        )
        self.assertEqual("new-server\n", (self.root / "server.py").read_text())
        self.assertEqual("new-app\n", (self.root / "public/app.js").read_text())
        self.assertEqual(
            hashlib.sha256(b"new-app\n").hexdigest(),
            hashes["public/app.js"],
        )

    def test_partial_replace_failure_restores_original_bytes(self):
        with self.assertRaises(promote.PromotionError):
            promote.transactional_replace(
                self.candidate,
                self.root,
                ["server.py", "public/app.js", "firefox-extension/background.js"],
                self.tx,
                fail_after=2,
            )
        self.assertEqual("old-server\n", (self.root / "server.py").read_text())
        self.assertEqual("old-app\n", (self.root / "public/app.js").read_text())
        self.assertEqual(
            "old-runner\n",
            (self.root / "firefox-extension/background.js").read_text(),
        )

    def test_partial_replace_failure_removes_newly_created_file(self):
        with self.assertRaises(promote.PromotionError):
            promote.transactional_replace(
                self.candidate,
                self.root,
                ["firefox-extension/recovery.js", "server.py"],
                self.tx,
                fail_after=1,
            )
        self.assertFalse((self.root / "firefox-extension/recovery.js").exists())
        self.assertEqual("old-server\n", (self.root / "server.py").read_text())

    def test_blast_radius_classification_is_conservative_but_keeps_small_changes_free(self):
        low = promote.promotion_blast_radius(["server.py"])
        self.assertFalse(low["high"])
        self.assertEqual(["service"], low["planes"])

        cross = promote.promotion_blast_radius([
            "server.py",
            "firefox-extension/background.js",
        ])
        self.assertTrue(cross["high"])
        self.assertIn("service+browser", cross["reasons"])

        broad = promote.promotion_blast_radius([
            "public/a.js","public/b.js","public/c.js",
            "public/d.js","public/e.js","public/f.js",
        ])
        self.assertTrue(broad["high"])
        self.assertIn("six_or_more_files", broad["reasons"])

    def test_high_blast_gate_is_default_off_allows_live_ttl_and_reblocks_after_expiry(self):
        db = self.root / "history.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE feature_flags("
                "name TEXT PRIMARY KEY, enabled INTEGER NOT NULL, expires_at TEXT, "
                "updated_at TEXT NOT NULL, actor TEXT NOT NULL)"
            )

        # Low blast never requires a flag.
        gate = promote.enforce_blast_radius_gate(db, ["server.py"])
        self.assertFalse(gate["blast_radius"]["high"])
        self.assertEqual("not_required", gate["feature_flag"]["reason"])

        # Cross-plane promotion is blocked while the flag is absent/default-off.
        with self.assertRaises(promote.PromotionError):
            promote.enforce_blast_radius_gate(
                db, ["server.py", "firefox-extension/background.js"]
            )

        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO feature_flags VALUES(?,?,?,?,?)",
                (
                    promote.HIGH_BLAST_FLAG, 1, future,
                    datetime.now(timezone.utc).isoformat(), "test",
                ),
            )
        gate = promote.enforce_blast_radius_gate(
            db, ["server.py", "firefox-extension/background.js"]
        )
        self.assertTrue(gate["feature_flag"]["effective"])

        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE feature_flags SET expires_at=? WHERE name=?",
                (expired, promote.HIGH_BLAST_FLAG),
            )
        with self.assertRaises(promote.PromotionError):
            promote.enforce_blast_radius_gate(
                db, ["server.py", "firefox-extension/background.js"]
            )

    def test_firefox_only_paths_do_not_restart_zcloud_service(self):
        self.assertFalse(promote.needs_service_restart([
            "firefox-extension/background.js",
            "firefox-extension/manifest.json",
            "firefox-extension/recovery.js",
        ]))
        self.assertTrue(promote.needs_service_restart([
            "firefox-extension/background.js",
            "server.py",
        ]))

    def test_promotion_lock_is_fail_closed(self):
        state = Path(self.tmp.name) / "state"
        with promote.promotion_lock(state):
            with self.assertRaises(promote.PromotionError):
                with promote.promotion_lock(state):
                    pass

    def test_failed_firefox_reload_restores_runtime_set(self):
        runtime = Path(self.tmp.name) / "runtime" / "background.js"
        runtime.parent.mkdir()
        runtime.write_text("old-runtime\n")
        (runtime.parent / "manifest.json").write_text('{"version":"old"}\n')
        helper = Path(self.tmp.name) / "reload.mjs"
        helper.write_text("// helper\n")
        tx = Path(self.tmp.name) / "runtime-tx"
        tx.mkdir()
        original_run = promote.run

        def failing_run(args, check=True):
            raise promote.PromotionError("reload failed")

        promote.run = failing_run
        try:
            with self.assertRaises(promote.PromotionError):
                promote.sync_firefox_runtime(
                    self.candidate,
                    runtime,
                    helper,
                    tx,
                    [
                        "firefox-extension/background.js",
                        "firefox-extension/manifest.json",
                        "firefox-extension/recovery.js",
                    ],
                )
        finally:
            promote.run = original_run
        self.assertEqual("old-runtime\n", runtime.read_text())
        self.assertEqual(
            '{"version":"old"}\n',
            (runtime.parent / "manifest.json").read_text(),
        )
        self.assertFalse((runtime.parent / "recovery.js").exists())

    def test_config_changes_capture_only_real_selected_config_changes(self):
        self._write(self.root, "resource-policy.json", '{"cloud":{"priority":"normal"}}\n')
        self._write(self.candidate, "resource-policy.json", '{"cloud":{"priority":"high"}}\n')
        changes = promote.config_changes(
            self.candidate,
            self.root,
            ["resource-policy.json", "server.py"],
        )
        self.assertEqual(1, len(changes))
        change = changes[0]
        self.assertEqual("resource.policy", change["config_key"])
        self.assertEqual("portfolio", change["target"])
        self.assertEqual({"cloud": {"priority": "normal"}}, change["old_value"])
        self.assertEqual({"cloud": {"priority": "high"}}, change["new_value"])

        self._write(self.candidate, "resource-policy.json", '{"cloud":{"priority":"normal"}}\n')
        self.assertEqual(
            [],
            promote.config_changes(
                self.candidate,
                self.root,
                ["resource-policy.json"],
            ),
        )

    def test_config_audit_write_is_fail_closed_and_records_transaction(self):
        db = self.root / "history.db"
        change = {
            "path": "resource-policy.json",
            "config_key": "resource.policy",
            "target": "portfolio",
            "old_value": {"cloud": {"priority": "normal"}},
            "new_value": {"cloud": {"priority": "high"}},
        }
        with self.assertRaises(promote.PromotionError):
            promote.write_config_audit(
                db,
                [change],
                actor="deploy-test",
                result="succeeded",
                transaction_id="tx-missing-table",
            )

        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE config_audit("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, "
                "config_key TEXT NOT NULL, target TEXT NOT NULL, old_value_json TEXT NOT NULL, "
                "new_value_json TEXT NOT NULL, result TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')"
            )
        promote.write_config_audit(
            db,
            [change],
            actor="deploy-test",
            result="succeeded",
            transaction_id="tx-123",
            detail="POSTDEPLOY_GREEN",
        )
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT actor,config_key,target,old_value_json,new_value_json,result,detail "
                "FROM config_audit"
            ).fetchone()
        self.assertEqual("deploy-test", row[0])
        self.assertEqual("resource.policy", row[1])
        self.assertEqual("portfolio", row[2])
        self.assertEqual(change["old_value"], json.loads(row[3]))
        self.assertEqual(change["new_value"], json.loads(row[4]))
        self.assertEqual("succeeded", row[5])
        self.assertIn("tx=tx-123", row[6])
        self.assertIn("POSTDEPLOY_GREEN", row[6])

    def test_config_validation_uses_candidate_overlay_only_for_selected_paths(self):
        calls = []
        original_run = promote.run

        class Result:
            returncode = 0
            stdout = '{"ok":true,"errors":[]}'

        def fake_run(args, check=True):
            calls.append(args)
            return Result()

        promote.run = fake_run
        try:
            promote.run_config_validation(
                Path("/bin/config-validator"),
                candidate=self.candidate,
                root=self.root,
                paths=["server.py"],
            )
        finally:
            promote.run = original_run

        command = calls[0]
        self.assertEqual(
            str(self.candidate / "server.py"),
            command[command.index("--server") + 1],
        )
        self.assertEqual(
            str(self.root / "enhancements.py"),
            command[command.index("--enhancements") + 1],
        )
        self.assertEqual(
            str(self.root / "projects.json"),
            command[command.index("--projects") + 1],
        )

    def test_postdeploy_command_contract_requires_requested_features(self):
        calls = []
        original_run = promote.run

        class Result:
            returncode = 0
            stdout = '{"ok":true}'

        def fake_run(args, check=True):
            calls.append(args)
            return Result()

        promote.run = fake_run
        try:
            result = promote.run_postdeploy(
                Path("/bin/postdeploy"),
                root=self.root,
                expected_mapping_sha="mapping-sha",
                require_worker_read_model=True,
                require_incidents=True,
            )
        finally:
            promote.run = original_run
        self.assertTrue(result["ok"])
        command = calls[0]
        self.assertIn("--expect-mapping-sha", command)
        self.assertIn("mapping-sha", command)
        self.assertIn("--require-worker-read-model", command)
        self.assertIn("--require-incidents", command)


if __name__ == "__main__":
    unittest.main(verbosity=2)
