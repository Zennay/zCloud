"""Real Git adversarial regressions: source-only immutable-action pin ratchet."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.zcloud_actions_pin_ratchet_20261015 import (
    Refusal, compare, main, refs, snapshot, workflow_tree,
)


class ActionPinRatchetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.command("init", "-q")
        self.command("config", "user.name", "CI test")
        self.command("config", "user.email", "ci@example.invalid")
        self.path = self.root / ".github/workflows/proof.yml"
        self.path.parent.mkdir(parents=True)
        self.base = self.commit("name: proof\njobs:\n  demo:\n    steps:\n      - uses: actions/checkout@v4\n")

    def command(self, *parts):
        p = subprocess.run(["git", "-C", str(self.root), *parts],
                           check=True, capture_output=True, text=True)
        return p.stdout.strip()

    def commit(self, content):
        self.path.write_text(content, encoding="utf-8")
        self.command("add", ".github")
        self.command("commit", "-qm", "test")
        return self.command("rev-parse", "HEAD")

    def evaluate(self, base=None, head=None):
        result = compare(snapshot(self.root, base or self.base),
                         snapshot(self.root, head or self.command("rev-parse", "HEAD")))
        self.assertFalse(result["mutation_performed"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["deployment_authorized"])
        return result

    def test_existing_debt_allowed(self):
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_new_tag_denied(self):
        self.commit(self.path.read_text() + "      - uses: actions/cache@v4\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 1)

    def test_changed_tag_denied(self):
        self.commit(self.path.read_text().replace("checkout@v4", "checkout@v5"))
        self.assertEqual(self.evaluate()["new_unpinned"], 1)

    def test_pin_removes_debt(self):
        pin = "abcdef0123456789abcdef0123456789abcdef01"
        self.commit(self.path.read_text().replace("checkout@v4", "checkout@" + pin))
        self.assertEqual(self.evaluate()["head_unpinned"], 0)

    def test_local_and_digest_docker_allowed(self):
        self.commit("steps:\n - uses: ./local/action\n - uses: docker://alpine@sha256:" + "1" * 64 + "\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_docker_dynamic_and_missing_ref_denied(self):
        self.commit("steps:\n - uses: docker://alpine:latest\n - uses: " + "$" + "{{ inputs.action }}\n - uses: actions/setup-python\n")
        result = self.evaluate()
        self.assertEqual(result["new_unpinned"], 3)

    def test_run_block_is_not_action(self):
        self.commit("steps:\n - run: |\n     uses: actions/cache@v4\n - uses: actions/checkout@v4\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_quoted_action_and_comment_allowed(self):
        self.commit("steps:\n - uses: 'actions/checkout@" + "a" * 40 + "' # pin\n # uses: actions/cache@v4\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_symlink_workflow_denied(self):
        self.path.unlink()
        self.path.symlink_to("/tmp/nonexistent")
        self.command("add", ".github")
        self.command("commit", "-qm", "symlink")
        with self.assertRaisesRegex(Refusal, "nonregular_workflow"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_worktree_modified_uncommitted_not_used(self):
        self.path.write_text(self.path.read_text() + " - uses: actions/cache@v3\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_invalid_revision_refused(self):
        with self.assertRaisesRegex(Refusal, "invalid_revision"):
            workflow_tree(self.root, "main; rm -rf /")

    def test_new_workflow_debt_denied(self):
        (self.path.parent / "new.yml").write_text("steps:\n - uses: actions/cache@v4\n")
        self.command("add", ".github")
        self.command("commit", "-qm", "new")
        self.assertEqual(self.evaluate()["new_unpinned"], 1)

    def test_deleted_debt_allowed(self):
        self.path.unlink()
        self.command("add", "-u")
        self.command("commit", "-qm", "removed")
        self.assertEqual(self.evaluate()["new_unpinned"], 0)

    def test_duplicate_same_tag_increase_denied(self):
        self.commit(self.path.read_text() + " - uses: actions/checkout@v4\n")
        self.assertEqual(self.evaluate()["new_unpinned"], 1)

    def test_cli_denial_no_raw_ref_echo(self):
        self.commit(self.path.read_text() + " - uses: actions/cache@v4\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = main(["--repo-root", str(self.root), "--base", self.base,
                           "--head", self.command("rev-parse", "HEAD")])
        self.assertEqual(result, 1)
        self.assertNotIn("actions/cache@v4", out.getvalue())
        self.assertFalse(json.loads(out.getvalue())["release_authorized"])

    def test_cli_refusal_exit_two(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = main(["--repo-root", str(self.root), "--base", "bad", "--head", self.base])
        self.assertEqual(result, 2)
        self.assertFalse(json.loads(out.getvalue())["deployment_authorized"])

    def test_non_utf8_git_blob_refused(self):
        self.path.write_bytes(b"steps:\n - uses: \xff")
        self.command("add", ".github")
        self.command("commit", "-qm", "nonutf8")
        with self.assertRaisesRegex(Refusal, "workflow_non_utf8"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_nested_workflow_path_denied(self):
        deep = self.path.parent / "nested/test.yml"
        deep.parent.mkdir()
        deep.write_text("steps:\n - uses: actions/cache@v4\n")
        self.command("add", ".github")
        self.command("commit", "-qm", "nested")
        with self.assertRaisesRegex(Refusal, "unexpected_workflow_path"):
            snapshot(self.root, self.command("rev-parse", "HEAD"))

    def test_oversized_reference_denied(self):
        with self.assertRaisesRegex(Refusal, "action_ref_oversize"):
            refs("steps:\n - uses: " + "a" * 513, "proof.yml")


if __name__ == "__main__":
    unittest.main()
