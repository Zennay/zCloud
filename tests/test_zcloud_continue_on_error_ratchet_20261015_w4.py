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

    def test_replacing_workflow_directory_with_link_is_denied(self):
        self.path.unlink()
        (self.root / ".github/workflows").rmdir()
        (self.root / ".github/workflows").symlink_to("../")
        self._git("add", "-A")
        self._git("commit", "-qm", "replace-workflow-root")
        head = self._git("rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "unsafe_workflow_root"):
            gate.changed_workflows(self.root, self.base, head)

    def test_oversized_workflow_blob_denied_before_reading(self):
        head = self._commit(SAFE + ("#" * (gate.MAX_WORKFLOW + 1)))
        with self.assertRaisesRegex(ValueError, "workflow_blob_too_large"):
            gate.scan(self.root, self.base, head)

    def test_invalid_utf8_workflow_is_denied(self):
        self.path.write_bytes(b"name: broken\xff\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "invalid-bytes")
        head = self._git("rev-parse", "HEAD")
        with self.assertRaises(UnicodeDecodeError):
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

    def test_literal_run_block_examples_are_inert(self):
        candidate = SAFE + (
            "\n      - run: |\n"
            "          printf 'continue-on-error: true\\n'\n"
            "          echo '# continue-on-error: true'\n"
        )
        self.assertEqual(self._reasons(candidate), {})

    def test_folded_run_block_examples_are_inert(self):
        candidate = SAFE + (
            "\n      - run: >-\n"
            "          echo 'continue-on-error: true'\n"
            "          echo 'reference only'\n"
        )
        self.assertEqual(self._reasons(candidate), {})

    def test_checkout_after_literal_block_still_denied(self):
        candidate = SAFE + (
            "\n      - run: |-\n"
            "          echo 'continue-on-error: true'\n"
            "      - name: actual fail-open check\n"
            "        continue-on-error: true\n"
            "        run: false\n"
        )
        self.assertTrue(self._reasons(candidate))

    def test_nested_run_block_does_not_hide_sibling_key(self):
        self.assertTrue(gate.check_workflow(
            "  run: |\n    echo harmless\n  continue-on-error: true\n"
        ))

    def test_ambiguous_custom_block_key_not_skipped(self):
        self.assertTrue(gate.check_workflow(
            "  information: |\n    continue-on-error: true\n"
        ))

    def test_invalid_git_output_budget_fails_closed(self):
        for limit in (0, -1, True, "10"):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(ValueError, "invalid_git_limit"):
                    gate.git(self.root, "status", limit=limit)

    def test_git_subprocess_output_is_bounded_while_reading(self):
        with tempfile.TemporaryDirectory() as bindir:
            binary = Path(bindir) / "git"
            binary.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "sys.stdout.write('A' * 500000)\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            from unittest.mock import patch
            import os
            with patch.dict(os.environ, {"PATH": bindir + os.pathsep + os.environ["PATH"]}):
                with self.assertRaisesRegex(ValueError, "git_output_limit"):
                    gate.git(self.root, "status", limit=1024)

    def test_git_process_hanging_before_first_byte_times_out(self):
        with tempfile.TemporaryDirectory() as bindir:
            binary = Path(bindir) / "git"
            binary.write_text(
                "#!/usr/bin/env python3\n"
                "import time\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            from unittest.mock import patch
            import os
            with patch.dict(os.environ, {"PATH": bindir + os.pathsep + os.environ["PATH"]}):
                with patch.object(gate, "GIT_DEADLINE_SECONDS", 0.25):
                    with self.assertRaisesRegex(TimeoutError, "git_elapsed_deadline"):
                        gate.git(self.root, "status", limit=512)

    def test_yaml_tab_indentation_is_rejected(self):
        self.assertTrue(gate.check_workflow("\tcontinue-on-error: false\n"))
        self.assertTrue(gate.check_workflow("  \t- name: nested\n"))

    def test_quoted_display_label_with_policy_example_is_inert(self):
        changed = SAFE.replace("name: Safe", 'name: "continue-on-error: true"')
        self.assertEqual(self._reasons(changed), {})
        self.assertEqual(
            gate.check_workflow('      - name: "continue-on-error: true"'), []
        )

    def test_invisible_bidi_and_line_separators_rejected(self):
        for text in ("\u202e", "\u200b", "\u2028", "\u2029", "\ufeff"):
            with self.subTest(codepoint=ord(text)):
                # Reuse the same file only for a separate fresh temp Git test:
                # each variant must be rejected at the read boundary.
                with tempfile.TemporaryDirectory() as directory:
                    temp = Path(directory)
                    subprocess.run(["git", "init", "-q", "-b", "main", str(temp)],
                                   check=True, capture_output=True)
                    subprocess.run(["git", "-C", str(temp), "config", "user.email",
                                    "ci@example.invalid"], check=True)
                    subprocess.run(["git", "-C", str(temp), "config", "user.name",
                                    "CI"], check=True)
                    workflows = temp / ".github/workflows"
                    workflows.mkdir(parents=True)
                    (workflows / "check.yml").write_text(SAFE, encoding="utf-8")
                    subprocess.run(["git", "-C", str(temp), "add", "."], check=True)
                    subprocess.run(["git", "-C", str(temp), "commit", "-qm", "base"],
                                   check=True)
                    base = subprocess.check_output(
                        ["git", "-C", str(temp), "rev-parse", "HEAD"]
                    ).decode().strip()
                    (workflows / "check.yml").write_text(SAFE + "# " + text + "\n",
                                                          encoding="utf-8")
                    subprocess.run(["git", "-C", str(temp), "add", "."], check=True)
                    subprocess.run(["git", "-C", str(temp), "commit", "-qm", "changed"],
                                   check=True)
                    head = subprocess.check_output(
                        ["git", "-C", str(temp), "rev-parse", "HEAD"]
                    ).decode().strip()
                    with self.assertRaisesRegex(ValueError, "invisible_or_directional_unicode"):
                        gate.scan(temp, base, head)

    def test_sibling_mapping_after_run_block_cannot_hide_failure(self):
        source = (
            "jobs:\n  job:\n    steps:\n"
            "      - run: |\n"
            "          echo ordinary\n"
            "        continue-on-error: true\n"
        )
        self.assertTrue(gate.check_workflow(source))

    def test_sibling_mapping_after_folded_run_block_cannot_hide_failure(self):
        source = (
            "jobs:\n  job:\n    steps:\n"
            "      - run: >-\n"
            "          echo shell\n"
            "        continue-on-error: true\n"
        )
        self.assertTrue(gate.check_workflow(source))

    def test_sibling_policy_at_same_indent_as_run_header_denied(self):
        source = (
            "      - run: |\n"
            "          echo harmless\n"
            "      continue-on-error: true\n"
        )
        self.assertTrue(gate.check_workflow(source))

    def test_sibling_mapping_with_false_stays_allowed(self):
        source = (
            "      - run: |\n"
            "          echo ordinary\n"
            "        continue-on-error: false\n"
        )
        self.assertEqual(gate.check_workflow(source), [])

    def test_nested_shell_body_is_inert_after_first_indent(self):
        source = (
            "      - run: |\n"
            "          echo 'continue-on-error: true'\n"
            "            continue-on-error: true\n"
        )
        self.assertEqual(gate.check_workflow(source), [])

    def test_explicit_indent_indicator_never_skips_policy_syntax(self):
        self.assertTrue(gate.check_workflow(
            "      - run: |2\n"
            "          echo 'continue-on-error: true'\n"
            "        continue-on-error: true\n"
        ))

    def test_cli_reports_safe_error_codes_without_exposing_exception(self):
        import contextlib
        import io
        from unittest.mock import patch
        for exception, code in (
            (ValueError("git_output_limit"), "git_output_limit"),
            (ValueError("TOKEN=topsecret"), "untrusted_source_io"),
            (TimeoutError("TOKEN=topsecret"), "git_elapsed_deadline"),
            (subprocess.CalledProcessError(1, ["git", "TOKEN=topsecret"]), "git_command_failed"),
            (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "TOKEN=topsecret"), "invalid_workflow_utf8"),
        ):
            with self.subTest(code=code):
                stream = io.StringIO()
                with patch.object(gate, "scan", side_effect=exception):
                    with contextlib.redirect_stderr(stream):
                        outcome = gate.main(["--base", self.base, "--head", self.base])
                self.assertEqual(outcome, 2)
                self.assertIn(f"[{code}]", stream.getvalue())
                self.assertNotIn("topsecret", stream.getvalue())

    def test_workflow_checks_live_main_and_pr_head_before_and_after(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/zcloud-continue-on-error-no-new-debt-20261015-w4.yml"
                    ).read_text(encoding="utf-8")
        self.assertEqual(workflow.count("git ls-remote --exit-code"), 4)
        self.assertEqual(workflow.count('test "$remote_main" = "$EXACT_BASE"'), 2)
        self.assertEqual(workflow.count('test "$remote_head" = "$EXACT_HEAD"'), 2)
        self.assertIn("https://github.com/Zennay/zCloud.git refs/heads/main", workflow)
        self.assertIn('timeout 12 git ls-remote --exit-code origin "refs/heads/$HEAD_BRANCH"', workflow)
        self.assertLess(workflow.index('test "$remote_main" = "$EXACT_BASE"'),
                        workflow.index("Checkout exact PR SHA without credentials"))

    def test_hosted_pr_admission_fails_instead_of_skipping(self):
        source = (Path(__file__).resolve().parents[1] /
                  ".github/workflows/zcloud-continue-on-error-no-new-debt-20261015-w4.yml"
                  ).read_text(encoding="utf-8")
        self.assertIn("Reject untrusted pull request before checkout", source)
        self.assertIn('test "$EVENT_KIND" = pull_request', source)
        self.assertIn('test "$HEAD_REPOSITORY" = "$SOURCE_REPOSITORY"', source)
        self.assertIn('test "$BASE_REPOSITORY" = "$SOURCE_REPOSITORY"', source)
        self.assertIn('test "$EVENT_BASE" = main', source)
        self.assertNotIn("  source-only:\n    if:", source)
        self.assertLess(source.index("Reject untrusted pull request"),
                        source.index("Checkout exact PR SHA"))

    def test_direct_source_key_matrix(self):
        for source, expected in (
            ("continue-on-error: true", True),
            ("continue-on-error: false", False),
            ("continue-on-error: FALSE", True),
            ("continue-on-error: 0", True),
            ("continue-on-error: ${{ true }}", True),
            ("'continue-on-error': false", False),
            ("continue-on-error: false # note", False),
            ("continue-on-error: false#not-a-yaml-comment", True),
            ("  # continue-on-error: true", False),
        ):
            with self.subTest(source=source):
                self.assertEqual(bool(gate.check_workflow(source)), expected)


if __name__ == "__main__":
    unittest.main()
