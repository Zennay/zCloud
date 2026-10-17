"""Real Git regression matrix for read-only workflow_run provenance gate."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from scripts import zcloud_workflow_run_token_ratchet_20261015_w5 as gate

SAFE = """name: Guarded
on:
  workflow_run:
    workflows: [lint]
    types: [completed]
permissions:
  contents: read
jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL,
        timeout=10).decode().strip()

class WorkflowRunTokenRatchetTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.file = self.root / ".github/workflows/existing.yml"
        self.file.parent.mkdir(parents=True)
        git(self.root, "init", "-qb", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        self.file.write_text(SAFE, encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "baseline")
        self.base = git(self.root, "rev-parse", "HEAD")

    def edit(self, value, path=None):
        target = self.file.parent / path if path else self.file
        target.write_text(value, encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "candidate")
        return gate.scan(self.root, self.base, git(self.root, "rev-parse", "HEAD"))

    def test_existing_explicit_read_only_passes(self):
        self.assertEqual(self.edit(SAFE + "# edited\n"), {})

    def test_new_read_only_passes(self):
        self.assertEqual(self.edit(SAFE, "new.yml"), {})

    def test_omitted_root_permissions_fail(self):
        self.assertTrue(self.edit(SAFE.replace("permissions:\n  contents: read\n", "")))

    def test_write_at_root_fails(self):
        self.assertTrue(self.edit(SAFE.replace("contents: read", "contents: write")))

    def test_job_level_write_fails(self):
        self.assertTrue(self.edit(SAFE.replace("    runs-on:", "    permissions:\n      actions: write\n    runs-on:")))

    def test_root_write_all_fails(self):
        self.assertTrue(self.edit(SAFE.replace("permissions:\n  contents: read", "permissions: write-all")))

    def test_root_read_all_passes(self):
        self.assertEqual(self.edit(SAFE.replace("permissions:\n  contents: read", "permissions: read-all")), {})

    def test_inline_event_list_fails_without_permissions(self):
        src = SAFE.replace("on:\n  workflow_run:\n    workflows: [lint]\n    types: [completed]", "on: [push, workflow_run]")
        self.assertTrue(self.edit(src.replace("permissions:\n  contents: read\n", "")))

    def test_inline_event_map_fails_without_permissions(self):
        src = SAFE.replace("on:\n  workflow_run:\n    workflows: [lint]\n    types: [completed]", "on: {workflow_run: {types: [completed]}}")
        self.assertTrue(self.edit(src.replace("permissions:\n  contents: read\n", "")))

    def test_quoted_on_key_fails_without_permissions(self):
        self.assertTrue(self.edit(SAFE.replace("on:\n", "'on':\n").replace("permissions:\n  contents: read\n", "")))

    def test_multiline_event_list_fails_without_permissions(self):
        src = SAFE.replace("on:\n  workflow_run:\n    workflows: [lint]\n    types: [completed]", "on: [\n  push,\n  workflow_run\n]")
        self.assertTrue(self.edit(src.replace("permissions:\n  contents: read\n", "")))

    def test_event_alias_fails_closed(self):
        self.assertTrue(self.edit(SAFE.replace("on:\n  workflow_run:\n    workflows: [lint]\n    types: [completed]", "on: *untrusted")))

    def test_event_merge_fails_closed(self):
        self.assertTrue(self.edit(SAFE.replace("  workflow_run:\n    workflows: [lint]\n    types: [completed]", "  <<: *aliased")))

    def test_quoted_permissions_do_not_fake_root_readonly(self):
        self.assertTrue(self.edit(SAFE.replace("permissions:", "'permissions':")))

    def test_push_event_outside_policy(self):
        src = SAFE.replace("  workflow_run:\n    workflows: [lint]\n    types: [completed]", "  push:\n    branches: [main]")
        self.assertEqual(self.edit(src.replace("contents: read", "contents: write")), {})

    def test_name_with_event_label_is_not_trigger(self):
        self.assertEqual(self.edit("name: workflow_run\non: push\npermissions:\n  contents: write\n"), {})

    def test_shell_examples_are_not_triggers(self):
        src = "on: push\npermissions:\n  contents: write\njobs:\n  audit:\n    steps:\n      - run: |\n          echo workflow_run\n"
        self.assertEqual(self.edit(src), {})

    def test_shell_block_dedent_still_checks_job_write(self):
        src = SAFE.replace("- run: echo ok", "- run: |\n          echo ordinary\n    permissions:\n      actions: write")
        self.assertTrue(self.edit(src))

    def test_unchanged_legacy_grandfathered(self):
        self.file.write_text(SAFE.replace("contents: read", "contents: write"), encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "legacy")
        base = git(self.root, "rev-parse", "HEAD")
        (self.root / "README.md").write_text("hello")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "docs")
        self.assertEqual(gate.scan(self.root, base, git(self.root, "rev-parse", "HEAD")), {})

    def test_changed_legacy_denied(self):
        self.file.write_text(SAFE.replace("contents: read", "contents: write"), encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "legacy")
        base = git(self.root, "rev-parse", "HEAD")
        self.file.write_text(SAFE.replace("contents: read", "contents: write") + "# changed")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "edited")
        self.assertTrue(gate.scan(self.root, base, git(self.root, "rev-parse", "HEAD")))

    def test_symlink_denied(self):
        self.file.unlink()
        self.file.symlink_to("../../README.md")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "symlink")
        with self.assertRaisesRegex(ValueError, "unsafe_workflow_mode"):
            gate.scan(self.root, self.base, git(self.root, "rev-parse", "HEAD"))

    def test_wrong_checkout_denied(self):
        with self.assertRaisesRegex(ValueError, "wrong_checkout"):
            gate.paths_changed(self.root, self.base, "f" * 40)

    def test_invalid_revision_denied(self):
        with self.assertRaisesRegex(ValueError, "invalid_revision"):
            gate.paths_changed(self.root, "main", self.base)

    def test_job_permissions_alias_is_not_implicitly_safe(self):
        candidate = SAFE.replace("    runs-on:", "    permissions: *write_permissions\n    runs-on:")
        self.assertTrue(self.edit(candidate))

    def test_tagged_job_permission_is_not_implicitly_safe(self):
        candidate = SAFE.replace("    runs-on:", "    permissions:\n      contents: !!str write\n    runs-on:")
        self.assertTrue(self.edit(candidate))

    def test_quoted_job_write_is_not_implicitly_safe(self):
        candidate = SAFE.replace("    runs-on:", '    permissions:\n      contents: "write"\n    runs-on:')
        self.assertTrue(self.edit(candidate))

    def test_yaml_permission_anchor_is_not_implicitly_safe(self):
        candidate = SAFE.replace("    runs-on:", "    permissions: &privileged\n    runs-on:")
        self.assertTrue(self.edit(candidate))

    def test_workflow_revalidates_both_live_refs_before_and_after(self):
        p = (Path(__file__).resolve().parents[1] /
             ".github/workflows/zcloud-workflow-run-token-no-new-debt-20261015-w5.yml")
        workflow = p.read_text(encoding="utf-8")
        self.assertEqual(workflow.count("git ls-remote --exit-code"), 4)
        self.assertEqual(workflow.count('test "$remote_main" = "$EXACT_BASE"'), 2)
        self.assertEqual(workflow.count('test "$remote_head" = "$EXACT_HEAD"'), 2)
        self.assertLess(workflow.index('test "$remote_main" = "$EXACT_BASE"'),
                        workflow.index("Checkout immutable PR source"))
        self.assertIn('timeout 15 git ls-remote --exit-code origin "refs/heads/$HEAD_REF"', workflow)

    def test_invalid_utf8_workflow_blob_fails_closed(self):
        self.file.write_bytes(b"on: workflow_run\npermissions:\n  contents: \xff\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "invalid-utf8")
        with self.assertRaises(UnicodeDecodeError):
            gate.scan(self.root, self.base, git(self.root, "rev-parse", "HEAD"))

    def test_oversized_workflow_blob_fails_before_reading(self):
        self.file.write_text(SAFE + ("#" * (gate.LIMIT + 1)), encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "oversized")
        with self.assertRaisesRegex(ValueError, "workflow_too_large"):
            gate.scan(self.root, self.base, git(self.root, "rev-parse", "HEAD"))

    def test_workflow_directory_symlink_fails_closed(self):
        self.file.unlink()
        (self.root / ".github/workflows").rmdir()
        (self.root / ".github/workflows").symlink_to("../")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "dir-symlink")
        with self.assertRaisesRegex(ValueError, "workflow_directory_not_tree"):
            gate.scan(self.root, self.base, git(self.root, "rev-parse", "HEAD"))

    def test_comment_only_policy_does_not_create_trigger(self):
        src = "name: Without triggers\non: push\n# on: workflow_run\npermissions: write-all\n"
        self.assertEqual(self.edit(src), {})

    def test_inline_root_readonly_mapping_passes(self):
        src = SAFE.replace("permissions:\n  contents: read", "permissions: {contents: read}")
        self.assertEqual(self.edit(src), {})

    def test_inline_root_write_mapping_fails(self):
        src = SAFE.replace("permissions:\n  contents: read", "permissions: {contents: read, actions: write}")
        self.assertTrue(self.edit(src))

    def test_git_output_is_limited_during_streaming(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as directory:
            fake_git = Path(directory) / "git"
            fake_git.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "sys.stdout.write('A'*150000)\n", encoding="utf-8"
            )
            fake_git.chmod(0o755)
            with patch.dict(os.environ, {"PATH": directory + os.pathsep + os.environ["PATH"]}):
                with self.assertRaisesRegex(ValueError, "git_output_limit"):
                    gate.git(self.root, "status", limit=1024)

    def test_hung_git_subprocess_fails_closed_and_is_killed(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as directory:
            fake_git = Path(directory) / "git"
            fake_git.write_text(
                "#!/usr/bin/env python3\n"
                "import time\n"
                "time.sleep(60)\n", encoding="utf-8"
            )
            fake_git.chmod(0o755)
            with patch.dict(os.environ, {"PATH": directory + os.pathsep + os.environ["PATH"]}):
                # Simulate deadline expiry before the first readable byte.
                import time
                initial = time.monotonic()
                with patch.object(gate.time, "monotonic", side_effect=[initial, initial + 16]):
                    with self.assertRaisesRegex(TimeoutError, "git_elapsed_deadline"):
                        gate.git(self.root, "status", limit=512)

    def test_invalid_git_output_limit_fails_closed(self):
        for bad in (0, -1, True, "1024"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "invalid_git_limit"):
                    gate.git(self.root, "status", limit=bad)

    def test_block_sequence_workflow_run_denies_implicit_write(self):
        candidate = SAFE.replace(
            "on:\n  workflow_run:\n    workflows: [lint]\n    types: [completed]",
            "on:\n  - push\n  - workflow_run",
        ).replace("permissions:\n  contents: read\n", "")
        self.assertTrue(self.edit(candidate))

    def test_quoted_block_sequence_workflow_run_denied(self):
        candidate = SAFE.replace(
            "on:\n  workflow_run:\n    workflows: [lint]\n    types: [completed]",
            'on:\n  - "workflow_run"',
        ).replace("permissions:\n  contents: read\n", "")
        self.assertTrue(self.edit(candidate))

    def test_source_policy_matrix(self):
        for value, unsafe in [
            (SAFE, False),
            (SAFE.replace("contents: read", "contents: write"), True),
            (SAFE.replace("permissions:\n  contents: read\n", ""), True),
            (SAFE.replace("permissions:\n  contents: read", "permissions: read-all"), False),
            (SAFE.replace("permissions:\n  contents: read", "permissions: write-all"), True),
        ]:
            with self.subTest(value=value[:70]):
                self.assertEqual(bool(gate.classify_workflow(value)), unsafe)

if __name__ == "__main__":
    unittest.main()
