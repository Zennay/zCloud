import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_recovery as recovery


class RecoveryTests(unittest.TestCase):
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

    def test_capture_records_visible_lkg_without_runtime_secrets(self):
        manifest = self.capture()
        self.assertEqual("7/7 smoke green", manifest["evidence"])
        self.assertTrue((self.state / "last-known-good.json").exists())
        snap = self.state / "snapshots" / manifest["snapshot_id"] / "files"
        self.assertTrue((snap / "server.py").exists())
        self.assertFalse((snap / "history.db").exists())
        self.assertFalse((snap / ".watch-token").exists())
        self.assertTrue(manifest["mapping_fingerprint"]["available"])

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