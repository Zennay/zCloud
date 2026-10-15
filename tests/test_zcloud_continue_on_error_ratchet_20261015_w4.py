"""Real Git regression tests for the non-authorizing continue-on-error ratchet."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_continue_on_error_ratchet_20261015_w4 as gate


SAFE = """name: Safe
on: workflow_dispatch
permissions:
  contents: read
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - name: Read evidence
        continue-on-error: false
        run: echo ok
"""
LEGACY = SAFE.replace("continue-on-error: false", "continue-on-error: true")


class ContinueOnErrorGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / ".github/workflows/existing.yml"
        self.path.parent.mkdir(parents=True)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "ci@example.invalid")
        self._git("config", "user.name", "CI")
        self.path.write_text(SAFE, encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "base")
        self.base = self._git("rev-parse", "HEAD")

    def _git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode().strip()

    def _commit(self, value, *, name="existing.yml"):
        target = self.root / ".github/workflows" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "candidate")
        return self._git("rev-parse", "HEAD")

    def _reasons(self, value, name="existing.yml"):
        head = self._commit(value, name=name)
        return gate.scan(self.root, self.base, head)

    def test_safe_false_changed_workflow(self):
        self.assertEqual(self._reasons(SAFE + "\n# benign change\n"), {})

    def test_new_workflow_safe_false(self):
        self.assertEqual(self._reasons(SAFE, name="new.yml"), {})

    def test_new_workflow_omitting_continue_on_error(self):
        self.assertEqual(self._reasons(SAFE.replace("        continue-on-error: false\n", ""), name="new.yml"), {})

    def test_modified_workflow_true(self):
        self.assertIn("existing.yml", next(iter(self._reasons(LEGACY))))

    def test_added_workflow_true(self):
        self.assertTrue(self._reasons(LEGACY, name="new.yml"))

    def test_expression_denied(self):
        self.assertTrue(self._reasons(SAFE.replace("false", "${{ inputs.allow_failure }}")))

    def test_quoted_key_denied(self):
        self.assertTrue(self._reasons(SAFE.replace("continue-on-error: false", '"continue-on-error": true')))

    def test_single_quoted_key_denied(self):
        self.assertTrue(self._reasons(SAFE.replace("continue-on-error: false", "'continue-on-error': true")))

    def test_inline_mapping_denied(self):
        self.assertTrue(self._reasons(SAFE.replace("continue-on-error: false", "{continue-on-error: true}")))

    def test_duplicate_key_shadow_denied(self):
        self.assertTrue(self._reasons(SAFE.replace(
            "continue-on-error: false", "continue-on-error: false\n        continue-on-error: true"
        )))

    def test_yaml_merge_denied(self):
        self.assertTrue(self._reasons(SAFE + "\n  <<: *unsafe_defaults\n"))

    def test_escaped_key_denied(self):
        self.assertTrue(self._reasons(SAFE + '\n"continue\\u002don-error": true\n'))

    def test_true_in_comment_does_not_trigger(self):
        self.assertEqual(self._reasons(SAFE + "\n# continue-on-error: true\n"), {})

    def test_false_with_comment_safe(self):
        self.assertEqual(self._reasons(SAFE.replace("continue-on-error: false", "continue-on-error: false # explicit")), {})

    def test_no_changed_workflow_grandfathers_old_blob(self):
        self.path.write_text(LEGACY, encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "historical-baseline")
        base = self._git("rev-parse", "HEAD")
        (self.root / "readme.md").write_text("documentation\n")
        self._git("add", ".")
        self._git("commit", "-qm", "docs")
        self.assertEqual(gate.scan(self.root, base, self._git("rev-parse", "HEAD")), {})

    def test_changed_legacy_blob_is_denied(self):
        self.path.write_text(LEGACY, encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "historical-baseline")
        base = self._git("rev-parse", "HEAD")
        head = self._commit(LEGACY + "\n# edited\n")
        self.assertTrue(gate.scan(self.root, base, head))

    def test_rename_legacy_to_new_path_denied(self):
        self.path.write_text(LEGACY, encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "historical-baseline")
        base = self._git("rev-parse", "HEAD")
        self._git("mv", ".github/workflows/existing.yml", ".github/workflows/new.yml")
        self._git("commit", "-qm", "rename")
        head = self._git("rev-parse", "HEAD")
        self.assertTrue(gate.scan(self.root, base, head))

    def test_rejects_non_head_sha(self):
        head = self._commit(SAFE + "\n# change\n")
        with self.assertRaisesRegex(ValueError, "checkout_head_changed"):
            gate.scan(self.root, head, self.base)

    def test_rejects_malformed_sha(self):
        with self.assertRaisesRegex(ValueError, "invalid_sha"):
            gate.changed_workflows(self.root, "main", "bad")

    def test_symlink_workflow_rejected(self):
        self.path.unlink()
        self.path.symlink_to("../../readme.md")
        self._git("add", ".")
        self._git("commit", "-qm", "link")
        head = self._git("rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "unsafe_workflow_mode"):
            gate.scan(self.root, self.base, head)

    def test_unicode_escaped_initial_key_denied(self):
        self.assertTrue(self._reasons(SAFE + '\n"\\u0063ontinue-on-error": true\n'))
        self.assertTrue(gate.check_workflow('{"\\u0063ontinue-on-error": true}'))

    def test_explicit_yaml_mapping_key_denied(self):
        self.assertTrue(self._reasons(SAFE + "\n? continue-on-error\n: true\n"))

    def test_display_names_with_policy_phrase_are_safe(self):
        self.assertEqual(self._reasons(SAFE.replace(
            "name: Safe", "name: zCloud continue-on-error no-new-debt ratchet"
        )), {})
        self.assertEqual(gate.check_workflow("- name: explain continue-on-error risk"), [])

    def test_direct_source_key_matrix(self):
        for source, expected in (
            ("continue-on-error: true", True),
            ("continue-on-error: false", False),
            ("continue-on-error: FALSE", True),
            ("continue-on-error: 0", True),
            ("continue-on-error: ${{ true }}", True),
            ("'continue-on-error': false", False),
            ("continue-on-error: false # note", False),
            ("  # continue-on-error: true", False),
        ):
            with self.subTest(source=source):
                self.assertEqual(bool(gate.check_workflow(source)), expected)


if __name__ == "__main__":
    unittest.main()
