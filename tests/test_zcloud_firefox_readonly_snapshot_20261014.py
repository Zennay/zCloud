"""Real temporary-SQLite and source regression for safe Firefox diagnostics."""
from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/zcloud_firefox_readonly_snapshot_20261014.py"
WORKFLOW = (ROOT / ".github/workflows/"
            "zcloud-firefox-worker-recovery-probe-20261013.yml")
spec = importlib.util.spec_from_file_location("safe_ff_sqlite", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class ReadOnlyFirefoxDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "history.db"
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE runner_workers (secret TEXT)")
            conn.execute("CREATE TABLE portfolio_queue (private_prompt TEXT)")
            conn.execute("INSERT INTO runner_workers VALUES ('test-worker-secret')")
            conn.execute("INSERT INTO portfolio_queue VALUES ('test-prompt-secret')")
            conn.commit()

    def test_real_sqlite_count_without_private_rows(self):
        result = module.snapshot(self.path)
        self.assertEqual(result["runner_worker_rows"], 1)
        self.assertEqual(result["queue_rows"], 1)
        self.assertTrue(result["read_only"])
        self.assertFalse(result["mutations_performed"])
        self.assertNotIn("test-prompt-secret", json.dumps(result))
        self.assertNotIn("test-worker-secret", json.dumps(result))

    def test_vfs_connection_refuses_writes(self):
        with module.open_readonly(self.path) as conn:
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone(), (1,))
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO runner_workers VALUES ('changed')")

    def test_file_integrity_and_rows_unchanged(self):
        before = self.path.read_bytes()
        self.assertEqual(1, module.snapshot(self.path)["queue_rows"])
        self.assertEqual(before, self.path.read_bytes())
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT private_prompt FROM portfolio_queue")
                             .fetchone()[0], "test-prompt-secret")

    def test_missing_table_is_unknown_not_zero(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TABLE portfolio_queue")
        result = module.snapshot(self.path)
        self.assertIsNone(result["queue_rows"])
        self.assertEqual(result["runner_worker_rows"], 1)

    def test_missing_db_never_created(self):
        missing = Path(self.tmp.name) / "not-exists.db"
        with self.assertRaises((OSError, sqlite3.Error)):
            module.snapshot(missing)
        self.assertFalse(missing.exists())

    def test_symlink_db_rejected(self):
        symlink = Path(self.tmp.name) / "link.db"
        symlink.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "symlink"):
            module.snapshot(symlink)

    def test_symlink_ancestor_rejected(self):
        fake = Path(self.tmp.name) / "alias"
        fake.symlink_to(Path(self.tmp.name), target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            module.snapshot(fake / "history.db")

    def test_relative_path_denied(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            module.snapshot(Path("history.db"))

    def test_only_explicit_tables_and_no_authorization(self):
        result = module.snapshot(self.path)
        self.assertFalse(result["worker_generation_verified"])
        self.assertFalse(result["production_healthy_verified"])
        self.assertEqual(("runner_workers", "portfolio_queue"), module.SAFE_TABLES)
        self.assertNotIn("authorized", json.dumps(result).lower())

    def test_main_error_does_not_echo_details(self):
        with patch.object(module, "DEFAULT_DB", Path(self.tmp.name) / "missing-secret.db"):
            out = io.StringIO()
            with redirect_stdout(out):
                status = module.main()
        self.assertNotEqual(status, 0)
        self.assertEqual(out.getvalue().strip(), "FIREFOX_DIAGNOSTIC_SQLITE_UNAVAILABLE=1")

    def test_pr_only_hosted_and_manual_vps(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("  pull_request:", source)
        self.assertIn("runs-on: ubuntu-latest", source)
        self.assertIn("github.event_name == 'workflow_dispatch'", source)
        self.assertIn("github.actor == 'Zennay'", source)
        self.assertIn("github.ref == 'refs/heads/main'", source)
        self.assertIn("github.repository == 'Zennay/zCloud'", source)

    def test_no_backend_mutating_get_or_raw_identity_logging(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn('urlopen("http://127.0.0.1:8765/api/runner-targets"', source)
        self.assertNotIn("RUNNER_TARGETS=", source)
        self.assertNotIn("ALLOCATED_WORKER_KEYS=", source)
        self.assertNotIn("LIVE_PROJECT_RUNNER_ADDONS=", source)
        self.assertIn("FIREFOX_DIAGNOSTIC_SQLITE=", SCRIPT.read_text(encoding="utf-8"))

    def test_no_live_execution_without_exact_current_main(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', source)
        self.assertIn('test "$(git ls-remote --exit-code origin refs/heads/main | cut -f1)" = "$GITHUB_SHA"', source)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", source)
        self.assertIn("persist-credentials: false", source)


if __name__ == "__main__":
    unittest.main()
