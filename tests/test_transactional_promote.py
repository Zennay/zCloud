import hashlib
import importlib.util
import tempfile
import unittest
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
        self._write(self.candidate, "server.py", "new-server\n")
        self._write(self.candidate, "public/app.js", "new-app\n")
        self._write(self.candidate, "firefox-extension/background.js", "new-runner\n")

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

    def test_promotion_lock_is_fail_closed(self):
        state = Path(self.tmp.name) / "state"
        with promote.promotion_lock(state):
            with self.assertRaises(promote.PromotionError):
                with promote.promotion_lock(state):
                    pass

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
