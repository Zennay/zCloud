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
        self.write(f"{ROOT}/baseline.yml", "name: baseline\n")
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

    def test_complex_yaml_key_denied_from_real_git(self):
        self.write(f"{ROOT}/complex.yml", "? secrets\n: inherit\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "inherit_secrets"):
            inspect(self.base, head)

    def test_unicode_escaped_key_denied_from_real_git(self):
        self.write(f"{ROOT}/encoded.yaml", '"se\\u0063rets": "in\\u0068erit"\n')
        head = self.commit()
        with self.assertRaisesRegex(Denied, "inherit_secrets"):
            inspect(self.base, head)

    def test_unicode_escape_in_opaque_yaml_is_conservatively_denied(self):
        self.write(f"{ROOT}/opaque.yml", 'name: "\\u0073ensitive"\n')
        head = self.commit()
        with self.assertRaisesRegex(Denied, "inherit_secrets"):
            inspect(self.base, head)

    def test_ci_runs_for_every_main_pr_without_path_filter(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-reusable-secret-inheritance-ratchet-20261015.yml").read_text()
        event = workflow.split("permissions:", 1)[0]
        self.assertIn("pull_request:", event)
        self.assertIn("branches: [main]", event)
        self.assertNotIn("    paths:", event)
        self.assertNotIn("    paths-ignore:", event)

    def test_ci_binds_both_remote_refs_before_and_after_scan(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-reusable-secret-inheritance-ratchet-20261015.yml").read_text()
        exact_main = """git ls-remote --exit-code origin refs/heads/main)"""
        exact_head = """git ls-remote --exit-code origin "refs/heads/$HEAD_REF")"""
        self.assertEqual(workflow.count(exact_main), 2)
        self.assertEqual(workflow.count(exact_head), 2)
        self.assertEqual(workflow.count("refs/heads/$HEAD_REF"), 4)
        self.assertIn("git check-ref-format --branch", workflow)
        self.assertIn("HEAD_REF: ${{ github.event.pull_request.head.ref }}", workflow)
        self.assertIn("git rev-parse HEAD", workflow)

    def test_ci_is_hosted_readonly_never_promotes(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-reusable-secret-inheritance-ratchet-20261015.yml").read_text()
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertNotIn("contents: write", workflow)

    def test_exact_remote_ref_receipt_does_not_accept_moving_branch(self):
        remote = Path(self.tmp.name) / ".git" / "proof-remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "origin", self.base + ":refs/heads/main")
        original = self.git("ls-remote", "--exit-code", "origin", "refs/heads/main").strip()
        self.assertEqual(original, self.base + "\trefs/heads/main")
        self.write("README.md", "revision moved\n")
        new_head = self.commit()
        self.git("push", "-q", "origin", new_head + ":refs/heads/main")
        updated = self.git("ls-remote", "--exit-code", "origin", "refs/heads/main").strip()
        self.assertNotEqual(updated, original)
        self.assertEqual(updated, new_head + "\trefs/heads/main")

    def test_missing_workflow_directory_fails_closed(self):
        Path(f"{ROOT}/baseline.yml").unlink()
        Path(ROOT).rmdir()
        head = self.commit()
        with self.assertRaisesRegex(Denied, "missing_workflow_directory"):
            inspect(self.base, head)

    def test_symlinked_workflows_root_fails_closed(self):
        Path(f"{ROOT}/baseline.yml").unlink()
        Path(ROOT).rmdir()
        os.symlink("../README.md", ROOT)
        head = self.commit()
        with self.assertRaisesRegex(Denied, "non_directory_workflow_root"):
            inspect(self.base, head)

    def test_symlinked_dotgithub_root_fails_closed(self):
        Path(f"{ROOT}/baseline.yml").unlink()
        Path(ROOT).rmdir()
        Path(".github").rmdir()
        os.symlink("README.md", ".github")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "non_directory_workflow_root"):
            inspect(self.base, head)

    def test_clean_directory_root_keeps_readonly_receipt(self):
        self.write(f"{ROOT}/new.yaml", "name: additional\\n")
        head = self.commit()
        self.assertEqual(inspect(self.base, head)["workflows_examined"], 2)
        self.assertFalse(inspect(self.base, head)["mutation_performed"])

    def test_directory_with_only_readme_fails_closed(self):
        Path(f"{ROOT}/baseline.yml").unlink()
        self.write(f"{ROOT}/README.md", "workflow definitions disabled\\n")
        head = self.commit()
        with self.assertRaisesRegex(Denied, "empty_workflow_inventory"):
            inspect(self.base, head)

    def test_deletion_of_one_workflow_preserves_remaining_inventory(self):
        self.write(f"{ROOT}/retired.yml", "name: retired\\n")
        self.base = self.commit()
        Path(f"{ROOT}/retired.yml").unlink()
        head = self.commit()
        result = inspect(self.base, head)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["workflows_examined"], 1)

    def test_deleted_legacy_inheritance_is_not_new_exposure(self):
        self.write(f"{ROOT}/legacy.yml", "secrets: inherit\n")
        self.base = self.commit()
        Path(f"{ROOT}/legacy.yml").unlink()
        head = self.commit()
        self.assertEqual(inspect(self.base, head)["changed_workflows_checked"], 0)


if __name__ == "__main__":
    unittest.main()
