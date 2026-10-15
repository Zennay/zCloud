"""Real-Git tests for source-only reusable-workflow secret inheritance ratchet."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.zcloud_reusable_secrets_guard_20261015_w3 import (
    Denied, has_secret_inherit, inspect, main,
)

ROOT = ".github/workflows"


class WorkflowSecretInheritanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = Path.cwd()
        os.chdir(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "tests@localhost")
        self.git("config", "user.name", "workflow-ratchet-tests")
        self.write("README.md", "baseline\n")
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    @staticmethod
    def git(*args):
        return subprocess.check_output(["git", *args], text=True)

    @staticmethod
    def write(path, text):
        f = Path(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD").strip()

    def test_simple_new_workflow_passes(self):
        self.write(f"{ROOT}/safe.yml", "name: safe\njobs:\n  smoke:\n    runs-on: ubuntu-latest\n")
        head = self.commit()
        res = inspect(self.base, head)
        self.assertEqual(res["status"], "pass")
        self.assertEqual(res["changed_workflows_checked"], 1)
        self.assertFalse(res["authorizes_deploy"])
        self.assertFalse(res["mutation_performed"])

    def test_introduced_inherit_denied(self):
        self.write(f"{ROOT}/unsafe.yml", "jobs:\n  call:\n    uses: test/workflow.yml@abc\n    secrets: inherit\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "inherit_secrets"):
            inspect(self.base, head)

    def test_quoted_inherit_denied(self):
        for snippet in ("secrets: 'inherit'", '"secrets": "inherit"', "secrets: { foo: inherit }"):
            with self.subTest(snippet=snippet):
                self.assertTrue(has_secret_inherit(f"jobs:\n  a:\n    {snippet}\n"))

    def test_comment_only_does_not_block(self):
        self.assertFalse(has_secret_inherit("# secrets: inherit\nname: safe\n"))

    def test_indented_yaml_scalar_denied(self):
        self.assertTrue(has_secret_inherit("secrets:\n  inherit\n"))

    def test_missing_secret_keyword_safe(self):
        self.assertFalse(has_secret_inherit("run: echo inherit\n"))

    def test_historical_unmodified_unsafe_blob_grandfathered(self):
        self.write(f"{ROOT}/legacy.yml", "jobs:\n  call:\n    secrets: inherit\n")
        self.base = self.commit()
        self.write(f"{ROOT}/new.yml", "name: new-safe\n")
        head = self.commit()
        result = inspect(self.base, head)
        self.assertEqual(result["changed_workflows_checked"], 1)

    def test_edited_legacy_inheritance_denied(self):
        self.write(f"{ROOT}/legacy.yml", "jobs:\n  call:\n    secrets: inherit\n")
        self.base = self.commit()
        self.write(f"{ROOT}/legacy.yml", "name: edited\njobs:\n  call:\n    secrets: inherit\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "inherit_secrets"):
            inspect(self.base, head)

    def test_non_workflow_not_scanned(self):
        self.write("docs/reusable-example.md", "secrets: inherit\n")
        head = self.commit()
        self.assertEqual(inspect(self.base, head)["changed_workflows_checked"], 0)

    def test_invalid_utf8_fails_closed(self):
        path = Path(f"{ROOT}/binary.yml")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"name: \xff\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "utf8"):
            inspect(self.base, head)

    def test_oversized_workflow_denied(self):
        self.write(f"{ROOT}/huge.yaml", "x" * 524289)
        head = self.commit()
        with self.assertRaisesRegex(Denied, "oversized"):
            inspect(self.base, head)

    def test_symlink_workflow_denied(self):
        path = Path(f"{ROOT}/link.yml")
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink("../../README.md", path)
        head = self.commit()
        with self.assertRaisesRegex(Denied, "unsupported_workflow"):
            inspect(self.base, head)

    def test_invalid_commit_identity_denied(self):
        with self.assertRaisesRegex(Denied, "invalid_commit_identity"):
            inspect(self.base, self.base)
        with self.assertRaisesRegex(Denied, "invalid_commit_identity"):
            inspect("../main", self.base)

    def test_cli_redacts_workflow_and_denies(self):
        self.write(f"{ROOT}/leak.yml", "jobs:\n  call:\n    secrets: inherit  # SENSITIVE_MARKER\n")
        head = self.commit()
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            status = main(["--base", self.base, "--head", head])
        self.assertEqual(status, 1)
        self.assertIn('"authorizes_deploy": false', sink.getvalue())
        self.assertNotIn("SENSITIVE_MARKER", sink.getvalue())

    def test_deleted_legacy_inheritance_is_not_new_exposure(self):
        self.write(f"{ROOT}/legacy.yml", "secrets: inherit\n")
        self.base = self.commit()
        Path(f"{ROOT}/legacy.yml").unlink()
        head = self.commit()
        self.assertEqual(inspect(self.base, head)["changed_workflows_checked"], 0)


if __name__ == "__main__":
    unittest.main()
