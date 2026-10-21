import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_browser_project_isolation_contract as isolation


ROOT = Path(__file__).resolve().parents[1]


class BrowserProjectIsolationContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-browser-isolation-")
        self.root = Path(self.tmp.name)
        (self.root / "public").mkdir(parents=True)
        (self.root / "firefox-extension").mkdir(parents=True)
        shutil.copy2(ROOT / "public" / "zcloud-worker.user.js", self.root / "public" / "zcloud-worker.user.js")
        shutil.copy2(ROOT / "firefox-extension" / "background.js", self.root / "firefox-extension" / "background.js")

    def tearDown(self):
        self.tmp.cleanup()

    def test_current_browser_sources_satisfy_project_isolation_contract(self):
        payload = isolation.audit(self.root)
        self.assertEqual("green", payload["state"])
        self.assertEqual([], payload["failed_checks"])
        self.assertGreaterEqual(payload["check_count"], 20)

    def test_userscript_rejects_foreign_and_secondary_base_commands(self):
        self.assertTrue(isolation.userscript_accepts("ftmo::w2", "ftmo::w2", "ftmo", 2))
        self.assertFalse(isolation.userscript_accepts("supa::w1", "ftmo::w2", "ftmo", 2))
        self.assertTrue(isolation.userscript_accepts("ftmo", "ftmo::w1", "ftmo", 1))
        self.assertFalse(isolation.userscript_accepts("ftmo", "ftmo::w2", "ftmo", 2))

    def test_extension_routes_only_matching_project_worker_keys(self):
        keys = isolation.extension_targets(
            "ftmo",
            {"supa::w1", "raiseai::w1"},
            ["supa::w1", "ftmo::w1", "ftmo::w2", "raiseai::w1"],
        )
        self.assertEqual(["ftmo::w1", "ftmo::w2"], keys)

    def test_missing_same_worker_guard_fails_closed(self):
        path = self.root / "public" / "zcloud-worker.user.js"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "const sameWorker = command.project_id === target.project_id;",
            "const sameWorker = true;",
            1,
        )
        path.write_text(text, encoding="utf-8")
        payload = isolation.audit(self.root)
        self.assertEqual("failed", payload["state"])
        self.assertIn("userscript_same_worker_only", payload["failed_checks"])

    def test_secondary_worker_cannot_inherit_base_project_commands(self):
        path = self.root / "public" / "zcloud-worker.user.js"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "const basePush = command.project_id === target.base_project_id && Number(target.worker_slot || 1) === 1;",
            "const basePush = command.project_id === target.base_project_id;",
            1,
        )
        path.write_text(text, encoding="utf-8")
        payload = isolation.audit(self.root)
        self.assertEqual("failed", payload["state"])
        self.assertIn("userscript_base_command_primary_only", payload["failed_checks"])

    def test_extension_cross_project_dispatch_regression_is_detected(self):
        path = self.root / "firefox-extension" / "background.js"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'if (command.action === "push") await pushProject(command.project_id, command.id);',
            'if (command.action === "push") await pushProject("cloud", command.id);',
            1,
        )
        path.write_text(text, encoding="utf-8")
        payload = isolation.audit(self.root)
        self.assertEqual("failed", payload["state"])
        self.assertIn("extension_push_routes_same_project", payload["failed_checks"])

    def test_replacement_handoff_must_match_sender_tab_project(self):
        path = self.root / "firefox-extension" / "background.js"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'if (!target || target.project_id !== message.projectId) return {ok:false, reason:"worker-tab-mismatch"};',
            'if (!target) return {ok:false, reason:"worker-tab-mismatch"};',
            1,
        )
        path.write_text(text, encoding="utf-8")
        payload = isolation.audit(self.root)
        self.assertEqual("failed", payload["state"])
        self.assertIn("handoff_rejects_cross_project_sender", payload["failed_checks"])

    def test_tab_listener_must_reject_foreign_project_messages(self):
        path = self.root / "firefox-extension" / "background.js"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            'if (!message || message.projectId !== cfg.projectId) return;',
            'if (!message) return;',
            1,
        )
        path.write_text(text, encoding="utf-8")
        payload = isolation.audit(self.root)
        self.assertEqual("failed", payload["state"])
        self.assertIn("tab_listener_rejects_foreign_project_messages", payload["failed_checks"])

    def test_symlinked_browser_source_is_rejected(self):
        path = self.root / "public" / "zcloud-worker.user.js"
        real = self.root / "public" / "real.user.js"
        path.rename(real)
        path.symlink_to(real.name)
        with self.assertRaises(isolation.ContractError):
            isolation.audit(self.root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
