"""Executed regression for bounded no-write SQLite worker diagnostics (#1341)."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sqlite3
import tempfile
from unittest import mock
import unittest

from scripts import zcloud_worker_prompt_safe_diagnostic as module

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows/zcloud-worker-prompt-stop-20261013.yml"
SECRET = "SECRET_TOKEN_never-expose-a-prompt-or-worker-id"


def populate(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE runner_workers(id INTEGER PRIMARY KEY, project_id TEXT);
            CREATE TABLE runner_commands(id INTEGER PRIMARY KEY, status TEXT, action TEXT);
            CREATE TABLE runner_events(id INTEGER PRIMARY KEY, event TEXT, payload TEXT);
        """)
        db.execute("INSERT INTO runner_workers(project_id) VALUES (?)", (SECRET,))
        db.execute("INSERT INTO runner_commands(status,action) VALUES (?,?)", ("pending", SECRET))
        db.execute("INSERT INTO runner_events(event,payload) VALUES (?,?)", ("generation-started", SECRET))
        db.execute("INSERT INTO runner_events(event,payload) VALUES (?,?)", (SECRET, SECRET))


class WorkerPromptSafeDiagnosticTests(unittest.TestCase):
    def test_actual_sqlite_read_only_snapshot_does_not_mutate_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "workers.db"
            populate(path)
            with sqlite3.connect(path) as conn:
                before = [conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                          for table in ("runner_workers", "runner_commands", "runner_events")]
            snapshot = module.read_sqlite(path)
            with sqlite3.connect(path) as conn:
                after = [conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                         for table in ("runner_workers", "runner_commands", "runner_events")]
            self.assertEqual(before, after)
            self.assertTrue(snapshot["direct_sqlite_read_only"])
            self.assertFalse(snapshot["mutation_performed"])
            self.assertEqual(1, snapshot["registered_workers"])
            self.assertEqual(1, snapshot["commands_pending"])
            self.assertEqual(1, snapshot["events_generation_started"])
            self.assertEqual(2, snapshot["sampled_events"])
            self.assertNotIn(SECRET, json.dumps(snapshot))

    def test_executed_main_does_not_emit_sensitive_snapshot_data(self):
        out = io.StringIO()
        with mock.patch.object(module, "read_sqlite", return_value={
                "registered_workers": 2, "mutation_performed": False
            }), mock.patch.object(module, "local_host_summary", return_value={
                "firefox_process_seen": True
            }), redirect_stdout(out):
            module.main()
        self.assertNotIn(SECRET, out.getvalue())
        result = json.loads(out.getvalue().split("=", 1)[1])
        self.assertEqual(2, result["registered_workers"])

    def test_input_strings_never_passthrough(self):
        snapshot = module.summarize_rows(1, [SECRET, "pending"], [SECRET, "generation-finished"])
        self.assertNotIn(SECRET, json.dumps(snapshot))
        self.assertEqual(1, snapshot["commands_pending"])
        self.assertEqual(1, snapshot["events_generation_finished"])

    def test_malformed_or_unbounded_values_denied(self):
        for workers in (True, -1, "1", 999999):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                module.summarize_rows(workers, [], [])
        for collection in (None, [{}], [SECRET] * (module.MAX_ROWS + 1)):
            with self.subTest(collection_type=type(collection).__name__), self.assertRaises(ValueError):
                module.summarize_rows(1, collection, [])

    def test_missing_file_and_symlinks_denied_without_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.db"
            with self.assertRaises(ValueError):
                module.read_sqlite(missing)
            self.assertFalse(missing.exists())
            target = Path(tmp) / "safe.db"
            populate(target)
            link = Path(tmp) / "alias.db"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                module.read_sqlite(link)

    def test_database_without_required_schema_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.db"
            with sqlite3.connect(path):
                pass
            with self.assertRaises(sqlite3.OperationalError):
                module.read_sqlite(path)

    def test_backend_get_endpoints_never_called(self):
        script = (ROOT / "scripts/zcloud_worker_prompt_safe_diagnostic.py").read_text(encoding="utf-8")
        self.assertNotIn("urllib.request", script)
        self.assertNotIn("requests.", script)
        self.assertNotIn("/api/runner-targets", script.split("def read_sqlite(", 1)[1])
        self.assertNotIn("/api/worker-debug", script.split("def read_sqlite(", 1)[1])
        self.assertIn("PRAGMA query_only=ON", script)
        self.assertIn("?mode=ro", script)
        self.assertNotIn("subprocess.Popen(", script)
        self.assertNotIn("os.kill(", script)

    def test_hosted_pr_never_runs_vps_inspection(self):
        source = WF.read_text(encoding="utf-8")
        self.assertIn("if: github.event_name == 'pull_request'", source)
        self.assertIn("runs-on: ubuntu-latest", source)
        self.assertIn("github.repository == 'Zennay/zCloud'", source)
        self.assertIn("github.ref == 'refs/heads/main'", source)
        self.assertIn("github.event_name == 'workflow_dispatch' && github.actor == 'Zennay'", source)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', source)
        self.assertNotIn("actions: write", source)
        self.assertNotIn("push:\n", source)
        self.assertNotIn("pull_request_target:", source)


if __name__ == "__main__":
    unittest.main()
