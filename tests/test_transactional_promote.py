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
    def test_candidate_postdeploy_canary_is_authoritative_for_default_path(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "zcloud_transactional_promote.py").read_text(encoding="utf-8")
        self.assertIn('candidate_postdeploy = candidate / "scripts/zcloud_postdeploy_canary.py"', source)
        self.assertIn("if postdeploy == DEFAULT_POSTDEPLOY and candidate_postdeploy.is_file()", source)
        self.assertIn("effective_postdeploy", source)
        self.assertIn('candidate_prechange = candidate / "scripts/zcloud_prechange_guard.py"', source)
        self.assertIn("if prechange == DEFAULT_PRECHANGE and candidate_prechange.is_file()", source)
        self.assertIn("effective_prechange", source)


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
        self._write(self.candidate, "lane_generator.py", "print('lane')\n")
        self._write(self.candidate, "project_runtime.py", "print('runtime')\n")
        self._write(
            self.candidate,
            "project-contracts.json",
            '{"schema_version":1,"resource_pools":{"disabled":{"slots":0}},"projects":{}}\n',
        )
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
                "project_runtime.py",
                "lane_generator.py",
                "project-contracts.json",
                "autonomy-policy.json",
                "portfolio_queue.seed.json",
                "vps-execution-policy.json",
            ],
        )
        self.assertIn("firefox-extension/recovery.js", result)
        self.assertIn("project_runtime.py", result)
        self.assertIn("lane_generator.py", result)
        self.assertIn("project-contracts.json", result)
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

    def test_write_set_excludes_selected_byte_identical_files(self):
        self._write(self.candidate, "same-a.py", "same\n")
        self._write(self.root, "same-a.py", "same\n")
        self._write(self.candidate, "same-b.py", "same\n")
        self._write(self.root, "same-b.py", "same\n")
        self._write(self.candidate, "changed.py", "new\n")
        self._write(self.root, "changed.py", "old\n")
        self._write(self.candidate, "lane_generator.py", "created\n")

        paths = ["same-a.py", "same-b.py", "changed.py", "lane_generator.py"]
        hashes = {
            rel: promote.sha256_file(self.candidate / rel)
            for rel in paths
        }
        self.assertEqual(
            ["changed.py", "lane_generator.py"],
            promote.promotion_write_paths(
                self.candidate,
                self.root,
                paths,
                hashes,
            ),
        )

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
        self.assertFalse(promote.needs_service_restart([
            "public/zcloud-worker.user.js",
            "public/app.js",
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

    def test_lkg_capture_can_use_candidate_recovery_helper(self):
        calls = []
        original_run = promote.run

        class Result:
            returncode = 0
            stdout = '{"format_version":1,"snapshot_id":"test"}'

        def fake_run(args, check=True):
            calls.append(args)
            return Result()

        helper = self.candidate / "scripts" / "zcloud_recovery.py"
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text("# helper\n")
        promote.run = fake_run
        try:
            result = promote.capture_lkg(
                self.root,
                Path(self.tmp.name) / "state",
                "green",
                recovery_script=helper,
            )
        finally:
            promote.run = original_run

        self.assertEqual("test", result["snapshot_id"])
        self.assertEqual(str(helper), calls[0][1])

    def test_prechange_only_allows_selected_replaceable_seed_drift(self):
        calls = []
        original_run = promote.run

        class Result:
            returncode = 0
            stdout = '{"ok":true,"checks":[]}'

        def fake_run(args, check=True):
            calls.append(args)
            return Result()

        promote.run = fake_run
        try:
            result = promote.run_prechange(
                Path("/bin/prechange"),
                self.root,
                Path(self.tmp.name) / "state",
                allowed_changes=["portfolio_queue.seed.json"],
            )
        finally:
            promote.run = original_run

        self.assertTrue(result["ok"])
        command = calls[0]
        self.assertEqual(1, command.count("--allow-change"))
        index = command.index("--allow-change")
        self.assertEqual("portfolio_queue.seed.json", command[index + 1])
        self.assertNotIn("server.py", promote.PRECHANGE_REPLACEABLE_DRIFT)
        self.assertEqual(
            {
                "portfolio_queue.seed.json",
                "public/zcloud-worker.user.js",
                "firefox-extension/background.js",
            },
            set(promote.PRECHANGE_REPLACEABLE_DRIFT),
        )
        self.assertEqual(
            {
                "server.py",
                "public/zcloud-worker.user.js",
                "firefox-extension/background.js",
            },
            set(promote.PRECHANGE_TRUSTED_ANCESTOR_DRIFT),
        )

    def test_audited_resource_policy_drift_requires_matching_post_lkg_audit(self):
        state = Path(self.tmp.name) / "audit-state"
        snapshot = state / "snapshots" / "lkg-1"
        files = snapshot / "files"
        files.mkdir(parents=True)
        (state / "last-known-good.json").parent.mkdir(parents=True, exist_ok=True)
        (state / "last-known-good.json").write_text(
            json.dumps({"snapshot_id": "lkg-1"}),
            encoding="utf-8",
        )
        (snapshot / "manifest.json").write_text(
            json.dumps({
                "snapshot_id": "lkg-1",
                "created_at": "2026-10-01T20:00:00+00:00",
            }),
            encoding="utf-8",
        )
        (files / "resource-policy.json").write_text(
            json.dumps({"ftmo": {"priority": "normal"}}),
            encoding="utf-8",
        )
        (self.root / "resource-policy.json").write_text(
            json.dumps({"ftmo": {"priority": "turbo"}}),
            encoding="utf-8",
        )
        db = self.root / "history.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE config_audit("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, "
                "config_key TEXT NOT NULL, target TEXT NOT NULL, old_value_json TEXT NOT NULL, "
                "new_value_json TEXT NOT NULL, result TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')"
            )
            conn.execute(
                "INSERT INTO config_audit(ts,actor,config_key,target,old_value_json,new_value_json,result,detail) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    "2026-10-01T21:00:00+00:00",
                    "dashboard@test",
                    "resource.priority",
                    "ftmo",
                    json.dumps("normal"),
                    json.dumps("turbo"),
                    "succeeded",
                    "saved",
                ),
            )

        self.assertTrue(
            promote.audited_runtime_config_drift_matches(
                self.root, state, "resource-policy.json"
            )
        )

        (self.root / "resource-policy.json").write_text(
            json.dumps({"ftmo": {"priority": "high"}}),
            encoding="utf-8",
        )
        self.assertFalse(
            promote.audited_runtime_config_drift_matches(
                self.root, state, "resource-policy.json"
            )
        )

    def test_audited_layout_and_catalog_drift_require_exact_post_lkg_audit(self):
        state = Path(self.tmp.name) / "audited-config-state"
        snapshot = state / "snapshots" / "lkg-config"
        files = snapshot / "files"
        files.mkdir(parents=True)
        (state / "last-known-good.json").write_text(
            json.dumps({"snapshot_id": "lkg-config"}), encoding="utf-8"
        )
        (snapshot / "manifest.json").write_text(
            json.dumps({
                "snapshot_id": "lkg-config",
                "created_at": "2026-10-01T20:00:00+00:00",
            }),
            encoding="utf-8",
        )

        baseline_layout = {"order": ["cloud", "ftmo"], "archived": []}
        current_layout = {"order": ["ftmo", "cloud"], "archived": ["cloud"]}
        baseline_projects = [{"id": "cloud", "name": "zCloud"}]
        current_projects = [
            {"id": "cloud", "name": "zCloud"},
            {"id": "ftmo", "name": "FTMO"},
        ]
        (files / "project-layout.json").write_text(
            json.dumps(baseline_layout), encoding="utf-8"
        )
        (files / "projects.json").write_text(
            json.dumps(baseline_projects), encoding="utf-8"
        )
        (self.root / "project-layout.json").write_text(
            json.dumps(current_layout), encoding="utf-8"
        )
        (self.root / "projects.json").write_text(
            json.dumps(current_projects), encoding="utf-8"
        )

        db = self.root / "history.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE config_audit("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, "
                "config_key TEXT NOT NULL, target TEXT NOT NULL, old_value_json TEXT NOT NULL, "
                "new_value_json TEXT NOT NULL, result TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')"
            )
            conn.executemany(
                "INSERT INTO config_audit(ts,actor,config_key,target,old_value_json,new_value_json,result,detail) "
                "VALUES(?,?,?,?,?,?,?,?)",
                [
                    (
                        "2026-10-01T21:00:00+00:00", "dashboard@test",
                        "project.layout", "portfolio", json.dumps(baseline_layout),
                        json.dumps(current_layout), "succeeded", "saved",
                    ),
                    (
                        "2026-10-01T21:01:00+00:00", "github-actions-vps-deploy",
                        "project.catalog", "portfolio", json.dumps(baseline_projects),
                        json.dumps(current_projects), "succeeded", "guarded promotion",
                    ),
                ],
            )

        self.assertTrue(
            promote.audited_runtime_config_drift_matches(
                self.root, state, "project-layout.json"
            )
        )
        self.assertTrue(
            promote.audited_runtime_config_drift_matches(
                self.root, state, "projects.json"
            )
        )

        (self.root / "project-layout.json").write_text(
            json.dumps({"order": ["cloud", "ftmo"], "archived": ["ftmo"]}),
            encoding="utf-8",
        )
        self.assertFalse(
            promote.audited_runtime_config_drift_matches(
                self.root, state, "project-layout.json"
            )
        )

    def test_audited_resource_policy_accepts_json_equivalent_bytes(self):
        state = Path(self.tmp.name) / "semantic-state"
        snapshot = state / "snapshots" / "lkg-semantic"
        files = snapshot / "files"
        files.mkdir(parents=True)
        (state / "last-known-good.json").write_text(
            json.dumps({"snapshot_id": "lkg-semantic"}), encoding="utf-8"
        )
        (snapshot / "manifest.json").write_text(
            json.dumps({"snapshot_id": "lkg-semantic", "created_at": "2026-10-01T20:00:00+00:00"}),
            encoding="utf-8",
        )
        (files / "resource-policy.json").write_text(
            '{"ftmo":{"priority":"turbo"},"supa":{"priority":"high"}}\n',
            encoding="utf-8",
        )
        (self.root / "resource-policy.json").write_text(
            '{\n  "supa": {"priority": "high"},\n  "ftmo": {"priority": "turbo"}\n}\n',
            encoding="utf-8",
        )
        self.assertTrue(
            promote.audited_runtime_config_drift_matches(
                self.root, state, "resource-policy.json"
            )
        )

    def test_audited_resource_policy_rejects_non_priority_semantic_drift(self):
        state = Path(self.tmp.name) / "semantic-state-other"
        snapshot = state / "snapshots" / "lkg-other"
        files = snapshot / "files"
        files.mkdir(parents=True)
        (state / "last-known-good.json").write_text(
            json.dumps({"snapshot_id": "lkg-other"}), encoding="utf-8"
        )
        (snapshot / "manifest.json").write_text(
            json.dumps({"snapshot_id": "lkg-other", "created_at": "2026-10-01T20:00:00+00:00"}),
            encoding="utf-8",
        )
        baseline = {"ftmo": {"priority": "normal", "weight": 1}}
        current = {"ftmo": {"priority": "turbo", "weight": 99}}
        (files / "resource-policy.json").write_text(json.dumps(baseline), encoding="utf-8")
        (self.root / "resource-policy.json").write_text(json.dumps(current), encoding="utf-8")
        db = self.root / "history.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE config_audit("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, "
                "config_key TEXT NOT NULL, target TEXT NOT NULL, old_value_json TEXT NOT NULL, "
                "new_value_json TEXT NOT NULL, result TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')"
            )
            conn.execute(
                "INSERT INTO config_audit(ts,actor,config_key,target,old_value_json,new_value_json,result,detail) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    "2026-10-01T21:00:00+00:00", "dashboard@test",
                    "resource.priority", "ftmo", json.dumps("normal"),
                    json.dumps("turbo"), "succeeded", "saved",
                ),
            )
        self.assertFalse(
            promote.audited_runtime_config_drift_matches(
                self.root, state, "resource-policy.json"
            )
        )

    def test_run_prechange_reconciles_only_audited_runtime_config_drift(self):
        calls = []
        original_run = promote.run
        original_audit = promote.audited_runtime_config_drift_matches

        class Result:
            def __init__(self, returncode, stdout):
                self.returncode = returncode
                self.stdout = stdout

        responses = [
            Result(
                2,
                '{"ok":false,"checks":[{"name":"managed_source_state","ok":false,'
                '"detail":{"changed":["resource-policy.json"],"allowed":[],'
                '"unexpected":["resource-policy.json"]}}]}',
            ),
            Result(0, '{"ok":true,"checks":[]}'),
        ]

        def fake_run(args, check=True):
            calls.append(args)
            return responses.pop(0)

        promote.run = fake_run
        promote.audited_runtime_config_drift_matches = (
            lambda root, state, rel: rel == "resource-policy.json"
        )
        try:
            result = promote.run_prechange(
                Path("/bin/prechange"),
                self.root,
                Path(self.tmp.name) / "state",
                candidate=self.candidate,
            )
        finally:
            promote.run = original_run
            promote.audited_runtime_config_drift_matches = original_audit

        self.assertTrue(result["ok"])
        self.assertEqual(2, len(calls))
        self.assertIn("resource-policy.json", calls[1])
        self.assertEqual(
            {"projects.json", "project-layout.json", "resource-policy.json"},
            set(promote.PRECHANGE_AUDITED_RUNTIME_DRIFT),
        )

    def test_prechange_reconciles_only_exact_reviewed_live_hash(self):
        calls = []
        original_run = promote.run
        (self.root / "server.py").write_text("reviewed-live-drift\n")
        (self.candidate / "server.py").write_text("current-green-server\n")
        expected = promote.sha256_file(self.root / "server.py")

        class Result:
            def __init__(self, returncode, stdout):
                self.returncode = returncode
                self.stdout = stdout

        responses = [
            Result(
                2,
                '{"ok":false,"checks":[{"name":"managed_source_state","ok":false,'
                '"detail":{"changed":["server.py"],"allowed":[],"unexpected":["server.py"]}}]}',
            ),
            Result(0, '{"ok":true,"checks":[]}'),
        ]

        def fake_run(args, check=True):
            calls.append(args)
            return responses.pop(0)

        promote.run = fake_run
        try:
            result = promote.run_prechange(
                Path("/bin/prechange"),
                self.root,
                Path(self.tmp.name) / "state",
                candidate=self.candidate,
                known_live_hashes={"server.py": expected},
            )
        finally:
            promote.run = original_run

        self.assertTrue(result["ok"])
        self.assertEqual(2, len(calls))
        self.assertIn("server.py", calls[1])

    def test_prechange_rejects_mismatched_reviewed_live_hash(self):
        original_run = promote.run
        (self.root / "server.py").write_text("different-live-drift\n")
        (self.candidate / "server.py").write_text("current-green-server\n")

        class Result:
            returncode = 2
            stdout = (
                '{"ok":false,"checks":[{"name":"managed_source_state","ok":false,'
                '"detail":{"changed":["server.py"],"allowed":[],"unexpected":["server.py"]}}]}'
            )

        promote.run = lambda args, check=True: Result()
        try:
            with self.assertRaises(promote.PromotionError):
                promote.run_prechange(
                    Path("/bin/prechange"),
                    self.root,
                    Path(self.tmp.name) / "state",
                    candidate=self.candidate,
                    known_live_hashes={"server.py": "0" * 64},
                )
        finally:
            promote.run = original_run

    def test_schema_validated_runtime_config_preservation_rejects_config_writes(self):
        for rel in ("projects.json", "project-layout.json"):
            with self.subTest(rel=rel):
                with self.assertRaisesRegex(
                    promote.PromotionError,
                    "schema-validated runtime config preservation cannot select files",
                ):
                    promote.promote(
                        self.candidate,
                        self.root,
                        Path(self.tmp.name) / "state",
                        [rel],
                        preserve_schema_validated_runtime_config=True,
                    )

    def test_schema_validated_runtime_config_preservation_stays_read_only(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "zcloud_transactional_promote.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            {"projects.json", "project-layout.json"},
            set(promote.SCHEMA_VALIDATED_RUNTIME_CONFIG_DRIFT),
        )
        validation = source.index("config_validation = run_config_validation")
        allowance = source.index("runtime_config_prechange_drift = sorted")
        guard = source.index("pre = run_prechange(")
        self.assertLess(validation, allowance)
        self.assertLess(allowance, guard)
        self.assertIn("--preserve-schema-validated-runtime-config", source)

    def test_explicit_prechange_drift_rejects_non_replaceable_paths(self):
        with self.assertRaises(promote.PromotionError):
            promote.promote(
                self.candidate,
                self.root,
                Path(self.tmp.name) / "state",
                ["server.py"],
                prechange_allow_changes=["server.py"],
            )

    def test_explicit_prechange_drift_requires_candidate_path(self):
        with self.assertRaises(promote.PromotionError):
            promote.promote(
                self.candidate,
                self.root,
                Path(self.tmp.name) / "state",
                ["server.py"],
                prechange_allow_changes=["public/zcloud-worker.user.js"],
            )

    def test_explicit_prechange_drift_is_merged_into_guard_allowlist(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "zcloud_transactional_promote.py"
        ).read_text(encoding="utf-8")
        self.assertIn("*explicit_prechange_drift", source)
        self.assertIn("--preserve-prechange-drift", source)
        self.assertIn(
            "if rel not in PRECHANGE_REPLACEABLE_DRIFT",
            source,
        )

    def test_prechange_reconciles_only_exact_candidate_aligned_drift(self):
        calls = []
        original_run = promote.run
        (self.root / "server.py").write_text("same-tested-bytes\n")
        (self.candidate / "server.py").write_text("same-tested-bytes\n")

        class Result:
            def __init__(self, returncode, stdout):
                self.returncode = returncode
                self.stdout = stdout

        responses = [
            Result(
                2,
                '{"ok":false,"checks":[{"name":"managed_source_state","ok":false,'
                '"detail":{"changed":["server.py"],"allowed":[],"unexpected":["server.py"]}}]}',
            ),
            Result(0, '{"ok":true,"checks":[]}'),
        ]

        def fake_run(args, check=True):
            calls.append(args)
            return responses.pop(0)

        promote.run = fake_run
        try:
            result = promote.run_prechange(
                Path("/bin/prechange"),
                self.root,
                Path(self.tmp.name) / "state",
                candidate=self.candidate,
            )
        finally:
            promote.run = original_run

        self.assertTrue(result["ok"])
        self.assertEqual(2, len(calls))
        self.assertNotIn("server.py", calls[0])
        self.assertIn("server.py", calls[1])
        index = calls[1].index("--allow-change")
        self.assertEqual("server.py", calls[1][index + 1])

    def test_prechange_reconciles_safe_drift_even_with_transient_health_failure(self):
        calls = []
        original_run = promote.run
        (self.root / "server.py").write_text("same-tested-bytes\n")
        (self.candidate / "server.py").write_text("same-tested-bytes\n")

        class Result:
            def __init__(self, returncode, stdout):
                self.returncode = returncode
                self.stdout = stdout

        responses = [
            Result(
                2,
                '{"ok":false,"checks":['
                '{"name":"managed_source_state","ok":false,"detail":'
                '{"changed":["server.py"],"allowed":[],"unexpected":["server.py"]}},'
                '{"name":"zcloud_http","ok":false,"detail":"temporary timeout"}]}',
            ),
            Result(0, '{"ok":true,"checks":[]}'),
        ]

        def fake_run(args, check=True):
            calls.append(args)
            return responses.pop(0)

        promote.run = fake_run
        try:
            result = promote.run_prechange(
                Path("/bin/prechange"),
                self.root,
                Path(self.tmp.name) / "state",
                candidate=self.candidate,
            )
        finally:
            promote.run = original_run

        self.assertTrue(result["ok"])
        self.assertEqual(2, len(calls))
        self.assertIn("server.py", calls[1])

    def test_prechange_keeps_divergent_managed_drift_fail_closed(self):
        calls = []
        original_run = promote.run
        (self.root / "server.py").write_text("unknown-live-drift\n")
        (self.candidate / "server.py").write_text("green-candidate\n")

        class Result:
            returncode = 2
            stdout = (
                '{"ok":false,"checks":[{"name":"managed_source_state","ok":false,'
                '"detail":{"changed":["server.py"],"allowed":[],"unexpected":["server.py"]}}]}'
            )

        def fake_run(args, check=True):
            calls.append(args)
            return Result()

        promote.run = fake_run
        try:
            with self.assertRaises(promote.PromotionError):
                promote.run_prechange(
                    Path("/bin/prechange"),
                    self.root,
                    Path(self.tmp.name) / "state",
                    candidate=self.candidate,
                )
        finally:
            promote.run = original_run

        self.assertEqual(1, len(calls))
        self.assertNotIn("server.py", calls[0])

    def test_ancestor_match_scans_beyond_64_commits_but_stays_bounded(self):
        live = self.root / "server.py"
        live.write_text("older-green-server\n")
        git_dir = self.candidate / ".git"
        git_dir.mkdir(exist_ok=True)

        calls = []
        original_run = promote.subprocess.run

        class Result:
            def __init__(self, returncode=0, stdout="", stdout_bytes=b""):
                self.returncode = returncode
                self.stdout = stdout
                if stdout_bytes:
                    self.stdout = stdout_bytes

        commits = [f"commit-{i}" for i in range(100)]
        live_bytes = live.read_bytes()

        def fake_run(args, **kwargs):
            calls.append(args)
            if "rev-list" in args:
                self.assertIn("--max-count=512", args)
                return Result(stdout="\n".join(commits) + "\n")
            commit = args[-1].split(":", 1)[0]
            if commit == commits[-1]:
                return Result(stdout_bytes=live_bytes)
            return Result(stdout_bytes=b"different\n")

        promote.subprocess.run = fake_run
        try:
            self.assertTrue(
                promote.matches_recent_first_parent_ancestor(
                    self.candidate, "server.py", live
                )
            )
        finally:
            promote.subprocess.run = original_run

        self.assertGreater(len(calls), 64)

    def test_prechange_reconciles_selected_recent_ancestor_drift(self):
        calls = []
        original_run = promote.run
        original_matcher = promote.matches_recent_first_parent_ancestor
        (self.root / "server.py").write_text("older-green-server\n")
        (self.candidate / "server.py").write_text("current-green-server\n")

        class Result:
            def __init__(self, returncode, stdout):
                self.returncode = returncode
                self.stdout = stdout

        responses = [
            Result(
                2,
                '{"ok":false,"checks":[{"name":"managed_source_state","ok":false,'
                '"detail":{"changed":["server.py"],"allowed":[],"unexpected":["server.py"]}}]}',
            ),
            Result(0, '{"ok":true,"checks":[]}'),
        ]

        def fake_run(args, check=True):
            calls.append(args)
            return responses.pop(0)

        promote.run = fake_run
        promote.matches_recent_first_parent_ancestor = (
            lambda candidate, rel, live: rel == "server.py"
        )
        try:
            result = promote.run_prechange(
                Path("/bin/prechange"),
                self.root,
                Path(self.tmp.name) / "state",
                candidate=self.candidate,
                trusted_ancestor_changes=["server.py"],
            )
        finally:
            promote.run = original_run
            promote.matches_recent_first_parent_ancestor = original_matcher

        self.assertTrue(result["ok"])
        self.assertEqual(2, len(calls))
        self.assertNotIn("server.py", calls[0])
        self.assertIn("server.py", calls[1])

    def test_prechange_reconciles_recent_browser_ancestor_drift_before_backend(self):
        calls = []
        original_run = promote.run
        original_matcher = promote.matches_recent_first_parent_ancestor
        live = self.root / "public/zcloud-worker.user.js"
        desired = self.candidate / "public/zcloud-worker.user.js"
        live.parent.mkdir(parents=True, exist_ok=True)
        desired.parent.mkdir(parents=True, exist_ok=True)
        live.write_text("older-green-worker\n")
        desired.write_text("current-green-worker\n")

        class Result:
            def __init__(self, returncode, stdout):
                self.returncode = returncode
                self.stdout = stdout

        responses = [
            Result(
                2,
                '{"ok":false,"checks":[{"name":"managed_source_state","ok":false,'
                '"detail":{"changed":["public/zcloud-worker.user.js"],"allowed":[],'
                '"unexpected":["public/zcloud-worker.user.js"]}}]}',
            ),
            Result(0, '{"ok":true,"checks":[]}'),
        ]

        def fake_run(args, check=True):
            calls.append(args)
            return responses.pop(0)

        promote.run = fake_run
        promote.matches_recent_first_parent_ancestor = (
            lambda candidate, rel, live_path: rel == "public/zcloud-worker.user.js"
        )
        try:
            result = promote.run_prechange(
                Path("/bin/prechange"),
                self.root,
                Path(self.tmp.name) / "state",
                candidate=self.candidate,
                trusted_ancestor_changes=["public/zcloud-worker.user.js"],
            )
        finally:
            promote.run = original_run
            promote.matches_recent_first_parent_ancestor = original_matcher

        self.assertTrue(result["ok"])
        self.assertEqual(2, len(calls))
        self.assertNotIn("public/zcloud-worker.user.js", calls[0])
        self.assertIn("public/zcloud-worker.user.js", calls[1])

    def test_prechange_opt_in_reconciles_any_recent_repo_ancestor_drift(self):
        calls = []
        original_run = promote.run
        original_matcher = promote.matches_recent_first_parent_ancestor
        live = self.root / "scripts/zcloud_healthcheck.py"
        desired = self.candidate / "scripts/zcloud_healthcheck.py"
        live.parent.mkdir(parents=True, exist_ok=True)
        desired.parent.mkdir(parents=True, exist_ok=True)
        live.write_text("older-green-healthcheck\n")
        desired.write_text("current-green-healthcheck\n")

        class Result:
            def __init__(self, returncode, stdout):
                self.returncode = returncode
                self.stdout = stdout

        responses = [
            Result(
                2,
                '{"ok":false,"checks":[{"name":"managed_source_state","ok":false,'
                '"detail":{"changed":["scripts/zcloud_healthcheck.py"],"allowed":[],'
                '"unexpected":["scripts/zcloud_healthcheck.py"]}}]}',
            ),
            Result(0, '{"ok":true,"checks":[]}'),
        ]

        def fake_run(args, check=True):
            calls.append(args)
            return responses.pop(0)

        promote.run = fake_run
        promote.matches_recent_first_parent_ancestor = (
            lambda candidate, rel, live_path: rel == "scripts/zcloud_healthcheck.py"
        )
        try:
            result = promote.run_prechange(
                Path("/bin/prechange"),
                self.root,
                Path(self.tmp.name) / "state",
                candidate=self.candidate,
                allow_recent_ancestor_drift=True,
            )
        finally:
            promote.run = original_run
            promote.matches_recent_first_parent_ancestor = original_matcher

        self.assertTrue(result["ok"])
        self.assertEqual(2, len(calls))
        self.assertNotIn("scripts/zcloud_healthcheck.py", calls[0])
        self.assertIn("scripts/zcloud_healthcheck.py", calls[1])

    def test_prechange_opt_in_still_rejects_unknown_managed_drift(self):
        calls = []
        original_run = promote.run
        original_matcher = promote.matches_recent_first_parent_ancestor
        live = self.root / "scripts/zcloud_healthcheck.py"
        desired = self.candidate / "scripts/zcloud_healthcheck.py"
        live.parent.mkdir(parents=True, exist_ok=True)
        desired.parent.mkdir(parents=True, exist_ok=True)
        live.write_text("manual-unknown-healthcheck\n")
        desired.write_text("current-green-healthcheck\n")

        class Result:
            returncode = 2
            stdout = (
                '{"ok":false,"checks":[{"name":"managed_source_state","ok":false,'
                '"detail":{"changed":["scripts/zcloud_healthcheck.py"],"allowed":[],'
                '"unexpected":["scripts/zcloud_healthcheck.py"]}}]}'
            )

        def fake_run(args, check=True):
            calls.append(args)
            return Result()

        promote.run = fake_run
        promote.matches_recent_first_parent_ancestor = (
            lambda candidate, rel, live_path: False
        )
        try:
            with self.assertRaises(promote.PromotionError):
                promote.run_prechange(
                    Path("/bin/prechange"),
                    self.root,
                    Path(self.tmp.name) / "state",
                    candidate=self.candidate,
                    allow_recent_ancestor_drift=True,
                )
        finally:
            promote.run = original_run
            promote.matches_recent_first_parent_ancestor = original_matcher

        self.assertEqual(1, len(calls))

    def test_prechange_rejects_unproven_ancestor_drift(self):
        calls = []
        original_run = promote.run
        original_matcher = promote.matches_recent_first_parent_ancestor
        (self.root / "server.py").write_text("unknown-server\n")
        (self.candidate / "server.py").write_text("current-green-server\n")

        class Result:
            returncode = 2
            stdout = (
                '{"ok":false,"checks":[{"name":"managed_source_state","ok":false,'
                '"detail":{"changed":["server.py"],"allowed":[],"unexpected":["server.py"]}}]}'
            )

        def fake_run(args, check=True):
            calls.append(args)
            return Result()

        promote.run = fake_run
        promote.matches_recent_first_parent_ancestor = (
            lambda candidate, rel, live: False
        )
        try:
            with self.assertRaises(promote.PromotionError):
                promote.run_prechange(
                    Path("/bin/prechange"),
                    self.root,
                    Path(self.tmp.name) / "state",
                    candidate=self.candidate,
                    trusted_ancestor_changes=["server.py"],
                )
        finally:
            promote.run = original_run
            promote.matches_recent_first_parent_ancestor = original_matcher

        self.assertEqual(1, len(calls))

    def test_service_health_timeout_matches_systemd_start_budget(self):
        self.assertEqual(90.0, promote.SERVICE_HEALTH_TIMEOUT_SECONDS)
        self.assertEqual(
            promote.SERVICE_HEALTH_TIMEOUT_SECONDS,
            promote.wait_http.__defaults__[0],
        )

    def test_health_request_timeout_matches_live_probe_budget(self):
        self.assertEqual(8.0, promote.HEALTH_REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(
            promote.HEALTH_REQUEST_TIMEOUT_SECONDS,
            promote.http_healthy.__defaults__[1],
        )

    def test_prechange_retries_transient_http_only_failure(self):
        calls = []
        original_run = promote.run
        original_sleep = promote.time.sleep

        class Result:
            def __init__(self, returncode, stdout):
                self.returncode = returncode
                self.stdout = stdout

        responses = [
            Result(
                2,
                '{"ok":false,"checks":[{"name":"zcloud_http","ok":false,"detail":"http://127.0.0.1:8765/api/status"}]}',
            ),
            Result(0, '{"ok":true,"checks":[{"name":"zcloud_http","ok":true}]}'),
        ]

        def fake_run(args, check=True):
            calls.append(args)
            return responses.pop(0)

        promote.run = fake_run
        promote.time.sleep = lambda _: None
        try:
            result = promote.run_prechange(
                Path("/bin/prechange"),
                self.root,
                Path(self.tmp.name) / "state",
                health_retry_seconds=1.0,
                retry_interval=0.01,
            )
        finally:
            promote.run = original_run
            promote.time.sleep = original_sleep

        self.assertTrue(result["ok"])
        self.assertEqual(2, len(calls))

    def test_prechange_does_not_retry_non_http_failure(self):
        calls = []
        original_run = promote.run

        class Result:
            returncode = 2
            stdout = '{"ok":false,"checks":[{"name":"source_tree","ok":false,"detail":"drift"}]}'

        def fake_run(args, check=True):
            calls.append(args)
            return Result()

        promote.run = fake_run
        try:
            with self.assertRaises(promote.PromotionError):
                promote.run_prechange(
                    Path("/bin/prechange"),
                    self.root,
                    Path(self.tmp.name) / "state",
                    health_retry_seconds=1.0,
                    retry_interval=0.01,
                )
        finally:
            promote.run = original_run

        self.assertEqual(1, len(calls))

    def _create_mapping_db(self, conversation="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"):
        db = self.root / "history.db"
        with sqlite3.connect(db) as conn:
            conn.executescript(
                """
                CREATE TABLE runner_targets(
                    project_id TEXT PRIMARY KEY,
                    active INTEGER,
                    worker_count INTEGER,
                    conversation_id TEXT
                );
                CREATE TABLE runner_workers(
                    project_id TEXT,
                    worker_slot INTEGER,
                    conversation_id TEXT,
                    provider TEXT NOT NULL DEFAULT 'chatgpt',
                    PRIMARY KEY(project_id,worker_slot)
                );
                CREATE TABLE runner_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    event TEXT,
                    target TEXT,
                    project_id TEXT,
                    worker_slot INTEGER
                );
                """
            )
            conn.execute(
                "INSERT INTO runner_targets VALUES('cloud',1,1,?)",
                (conversation,),
            )
            conn.execute(
                "INSERT INTO runner_workers(project_id,worker_slot,conversation_id,provider) "
                "VALUES('cloud',1,?,'chatgpt')",
                (conversation,),
            )
        return db

    def test_mapping_advance_accepts_exact_conversation_adoption_evidence(self):
        db = self._create_mapping_db()
        before = promote.mapping_snapshot(db)
        new_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO runner_events(ts,event,target,project_id,worker_slot) "
                "VALUES(?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    "conversation-adopted",
                    f"https://chatgpt.com/c/{new_id}",
                    "cloud",
                    1,
                ),
            )
            conn.execute(
                "UPDATE runner_workers SET conversation_id=? "
                "WHERE project_id='cloud' AND worker_slot=1",
                (new_id,),
            )
            conn.execute(
                "UPDATE runner_targets SET conversation_id=? WHERE project_id='cloud'",
                (new_id,),
            )
        after = promote.mapping_snapshot(db)
        evidence = promote.explain_mapping_advance(before, after, db)
        self.assertTrue(evidence["ok"], evidence)
        self.assertEqual("conversation_adopted", evidence["reason"])
        self.assertEqual("cloud", evidence["changes"][0]["project_id"])
        self.assertEqual(1, evidence["changes"][0]["worker_slot"])
        self.assertGreater(evidence["event_cursor_to"], evidence["event_cursor_from"])

    def test_mapping_advance_rejects_unexplained_conversation_change(self):
        db = self._create_mapping_db()
        before = promote.mapping_snapshot(db)
        new_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE runner_workers SET conversation_id=? "
                "WHERE project_id='cloud' AND worker_slot=1",
                (new_id,),
            )
            conn.execute(
                "UPDATE runner_targets SET conversation_id=? WHERE project_id='cloud'",
                (new_id,),
            )
        after = promote.mapping_snapshot(db)
        evidence = promote.explain_mapping_advance(before, after, db)
        self.assertFalse(evidence["ok"])
        self.assertIn("without_adoption", evidence["reason"])

    def test_mapping_advance_accepts_secondary_provider_switch_release(self):
        db = self._create_mapping_db()
        secondary = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO runner_workers(project_id,worker_slot,conversation_id,provider) "
                "VALUES('cloud',2,?,'chatgpt')",
                (secondary,),
            )
        before = promote.mapping_snapshot(db)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE runner_workers SET conversation_id='',provider='claude' "
                "WHERE project_id='cloud' AND worker_slot=2"
            )
        after = promote.mapping_snapshot(db)
        evidence = promote.explain_mapping_advance(before, after, db)
        self.assertTrue(evidence["ok"], evidence)
        self.assertEqual("provider_switch_release", evidence["reason"])
        self.assertEqual("provider_switch_release", evidence["changes"][0]["kind"])
        self.assertEqual("chatgpt", evidence["changes"][0]["old_provider"])
        self.assertEqual("claude", evidence["changes"][0]["new_provider"])

    def test_mapping_advance_rejects_secondary_clear_without_provider_change(self):
        db = self._create_mapping_db()
        secondary = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO runner_workers(project_id,worker_slot,conversation_id,provider) "
                "VALUES('cloud',2,?,'chatgpt')",
                (secondary,),
            )
        before = promote.mapping_snapshot(db)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE runner_workers SET conversation_id='' "
                "WHERE project_id='cloud' AND worker_slot=2"
            )
        after = promote.mapping_snapshot(db)
        evidence = promote.explain_mapping_advance(before, after, db)
        self.assertFalse(evidence["ok"])
        self.assertEqual("worker_conversation_cleared", evidence["reason"])

    def test_mapping_snapshot_ignores_runtime_allocation_like_canary_contract(self):
        db = self._create_mapping_db()
        before = promote.mapping_snapshot(db)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE runner_targets SET active=0,worker_count=7 WHERE project_id='cloud'"
            )
        after = promote.mapping_snapshot(db)
        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(before["targets"], after["targets"])

    def test_mapping_adoption_remains_valid_during_runtime_allocation_churn(self):
        db = self._create_mapping_db()
        before = promote.mapping_snapshot(db)
        new_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO runner_events(ts,event,target,project_id,worker_slot) "
                "VALUES(?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    "conversation-adopted",
                    f"https://chatgpt.com/c/{new_id}",
                    "cloud",
                    1,
                ),
            )
            conn.execute(
                "UPDATE runner_workers SET conversation_id=? "
                "WHERE project_id='cloud' AND worker_slot=1",
                (new_id,),
            )
            conn.execute(
                "UPDATE runner_targets SET conversation_id=?,active=0,worker_count=7 "
                "WHERE project_id='cloud'",
                (new_id,),
            )
        after = promote.mapping_snapshot(db)
        evidence = promote.explain_mapping_advance(before, after, db)
        self.assertTrue(evidence["ok"], evidence)
        self.assertEqual("conversation_adopted", evidence["reason"])

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
