"""Real Git object regression tests for isolated checkout credential source ratchet."""
from __future__ import annotations
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from scripts import zcloud_checkout_credentials_ratchet_20261015_w1 as guard
from scripts.zcloud_checkout_credentials_ratchet_20261015_w1 import Denied, inspect, main

WORK = ".github/workflows"
PIN = "d23441a48e516b6c34aea4fa41551a30e30af803"

def workflow(explicit="false"):
    with_line = f"        with:\n          persist-credentials: {explicit}\n" if explicit is not None else ""
    return ("name: checkout test\non: pull_request\npermissions:\n  contents: read\n"
            "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - uses: actions/checkout@{PIN}\n"
            + with_line + "      - run: echo done\n")

class RealGitCheckoutRatchet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "ratchet-tests@example.invalid")
        self.git("config", "user.name", "ratchet-tests")
        self.write(f"{WORK}/existing.yml", "name: existing\n")
        self.base = self.commit()

    def tearDown(self):
        os.chdir(self.previous)
        self.tmp.cleanup()

    @staticmethod
    def git(*args):
        return subprocess.check_output(["git", *args], text=True).strip()

    @staticmethod
    def write(path, content):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def verify(self, content, expect_error=None, path=f"{WORK}/added.yml"):
        self.write(path, content)
        head = self.commit()
        if expect_error:
            with self.assertRaisesRegex(Denied, expect_error):
                inspect(self.base, head)
        else:
            self.assertEqual(inspect(self.base, head)["status"], "pass")
        return head

    def test_good_pinned_checkout_explicit_false(self):
        result = inspect(self.base, self.verify(workflow()))
        self.assertEqual(result["changed_sources_checked"], 1)
        self.assertFalse(result["authorizes_deploy"])
        self.assertFalse(result["mutation_performed"])

    def test_named_checkout_with_explicit_false_is_accepted(self):
        body = workflow().replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - name: Checkout source\n        uses: actions/checkout@{PIN}\n",
        )
        self.verify(body)

    def test_named_checkout_with_id_and_false_is_accepted(self):
        body = workflow().replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - name: Checkout source\n        id: source\n"
            f"        uses: actions/checkout@{PIN}\n",
        )
        self.verify(body)

    def test_named_checkout_omitted_false_denied(self):
        body = workflow(None).replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - name: Checkout source\n        uses: actions/checkout@{PIN}\n",
        )
        self.verify(body, "missing_explicit_false")

    def test_named_checkout_with_true_denied(self):
        body = workflow("true").replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - name: Checkout source\n        uses: actions/checkout@{PIN}\n",
        )
        self.verify(body, "persisted_credentials")

    def test_named_checkout_wrong_indent_denied(self):
        body = workflow().replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - name: Checkout source\n"
            f"          uses: actions/checkout@{PIN}\n",
        )
        self.verify(body, "ambiguous_checkout")

    def test_named_checkout_nested_under_unrelated_field_denied(self):
        body = workflow().replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - name: Checkout source\n        env:\n"
            f"          uses: actions/checkout@{PIN}\n",
        )
        self.verify(body, "ambiguous_checkout")

    def test_named_checkout_in_composite_action_is_accepted(self):
        body = (f"name: local action\nruns:\n  using: composite\n  steps:\n"
                f"    - name: Checkout safely\n      uses: actions/checkout@{PIN}\n"
                f"      with:\n        persist-credentials: false\n")
        self.verify(body, path=".github/actions/demo/action.yml")

    def test_duplicate_uses_after_checkout_denied(self):
        body = workflow().replace(
            "          persist-credentials: false\n",
            f"          persist-credentials: false\n"
            f"        uses: example/other@{PIN}\n",
        )
        self.verify(body, "ambiguous_step_uses")

    def test_duplicate_uses_before_named_checkout_denied(self):
        body = workflow().replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - name: Checkout source\n"
            f"        uses: example/other@{PIN}\n"
            f"        uses: actions/checkout@{PIN}\n",
        )
        self.verify(body, "ambiguous_step_uses")

    def test_adjacent_named_checkouts_each_required_false(self):
        body = workflow().replace(
            "      - run: echo done\n",
            f"      - name: Second checkout\n"
            f"        uses: actions/checkout@{PIN}\n",
        )
        self.verify(body, "missing_explicit_false")

    def test_checkout_step_introduced_by_id_passes(self):
        body = workflow().replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - id: cloned\n        uses: actions/checkout@{PIN}\n",
        )
        self.verify(body)

    def test_checkout_step_introduced_by_if_passes(self):
        body = workflow().replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - if: success()\n        uses: actions/checkout@{PIN}\n",
        )
        self.verify(body)

    def test_checkout_id_step_omitting_false_is_denied(self):
        body = workflow(None).replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - id: cloned\n        uses: actions/checkout@{PIN}\n",
        )
        self.verify(body, "missing_explicit_false")

    def test_checkout_if_step_with_true_is_denied(self):
        body = workflow("true").replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - if: success()\n        uses: actions/checkout@{PIN}\n",
        )
        self.verify(body, "persisted_credentials")

    def test_checkout_under_nested_env_is_not_a_step(self):
        body = workflow().replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - env: foo\n        uses: actions/checkout@{PIN}\n",
        )
        self.verify(body, "ambiguous_checkout")

    def test_duplicate_checkout_uses_under_id_denied(self):
        body = workflow().replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - id: cloned\n"
            f"        uses: example/something@{PIN}\n"
            f"        uses: actions/checkout@{PIN}\n",
        )
        self.verify(body, "ambiguous_step_uses")

    def test_no_checkout_safe(self):
        self.verify("name: simple\njobs:\n  smoke:\n    runs-on: ubuntu-latest\n")

    def test_missing_explicit_false_denied(self):
        self.verify(workflow(None), "missing_explicit_false")

    def test_true_denied(self):
        self.verify(workflow("true"), "persisted_credentials")

    def test_string_true_denied(self):
        self.verify(workflow("'true'"), "persisted_credentials")

    def test_expression_denied(self):
        self.verify(workflow("$" + "{{ inputs.persist }}"), "ambiguous_credentials")

    def test_two_checkouts_both_must_disable(self):
        bad = workflow().replace("      - run: echo done\n",
             f"      - uses: actions/checkout@{PIN}\n        with:\n          fetch-depth: 1\n")
        self.verify(bad, "missing_explicit_false")

    def test_duplicate_credential_denied(self):
        bad = workflow().replace("          persist-credentials: false\n",
                                 "          persist-credentials: false\n          persist-credentials: true\n")
        self.verify(bad, "persisted_credentials")

    def test_duplicate_inline_empty_with_does_not_override_safe_false(self):
        bad = workflow().replace(
            "          persist-credentials: false\n",
            "          persist-credentials: false\n        with: {}\n",
        )
        self.verify(bad, "ambiguous_checkout_with")

    def test_duplicate_aliased_with_does_not_override_safe_false(self):
        bad = workflow().replace(
            "          persist-credentials: false\n",
            "          persist-credentials: false\n        with: *defaults\n",
        )
        self.verify(bad, "ambiguous_checkout_with")

    def test_quoted_duplicate_with_map_denied(self):
        bad = workflow().replace(
            "          persist-credentials: false\n",
            '          persist-credentials: false\n        "with": {}\n',
        )
        self.verify(bad, "ambiguous_checkout_with")

    def test_single_quoted_with_block_denied(self):
        bad = workflow().replace("        with:\n", '        "with":\n')
        self.verify(bad, "opaque_checkout_with")

    def test_explicit_complex_with_key_denied(self):
        bad = workflow().replace(
            "          persist-credentials: false\n",
            "          persist-credentials: false\n        ? with\n        : {}\n",
        )
        self.verify(bad, "opaque_checkout_with")

    def test_merged_defaults_with_lookup_denied(self):
        bad = workflow().replace(
            "          persist-credentials: false\n",
            "          persist-credentials: false\n        <<: *defaults\n",
        )
        self.verify(bad, "opaque_checkout_merge")

    def test_tagged_duplicate_with_key_denied(self):
        bad = workflow().replace(
            "          persist-credentials: false\n",
            "          persist-credentials: false\n        !!str with: {}\n",
        )
        self.verify(bad, "opaque_checkout_with")

    def test_yaml_tag_uri_duplicate_with_key_denied(self):
        bad = workflow().replace(
            "          persist-credentials: false\n",
            "          persist-credentials: false\n"
            "        !<tag:yaml.org,2002:str> with: {}\n",
        )
        self.verify(bad, "opaque_checkout_with")

    def test_fully_escaped_tagged_with_key_denied(self):
        encoded = "w" + chr(92) + "u0069th"
        bad = workflow().replace(
            "          persist-credentials: false\n",
            '          persist-credentials: false\n'
            '        !!str "' + encoded + '": {}\n',
        )
        self.verify(bad, "opaque_escaped_yaml")

    def test_single_inline_with_map_denied(self):
        bad = workflow(None).replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - uses: actions/checkout@{PIN}\n"
            "        with: {persist-credentials: false}\n",
        )
        self.verify(bad, "opaque_checkout_with")

    def test_non_checkout_step_with_inline_map_is_not_credential_claim(self):
        self.verify("name: safe\njobs:\n  smoke:\n    steps:\n"
                    "      - uses: vendor/something@0123456789abcdef0123456789abcdef01234567\n"
                    "        with: {option: value}\n")

    def test_quoted_uses_ambiguity_denied(self):
        self.verify(workflow().replace("- uses:", '- "uses":'), "ambiguous_checkout")

    def test_unpinned_uses_denied(self):
        self.verify(workflow().replace(PIN, "v4"), "ambiguous_checkout")

    def test_folded_checkout_denied(self):
        self.verify(workflow().replace("uses: actions/checkout@", "uses: >-\n          actions/checkout@"),
                    "ambiguous_checkout")

    def test_nested_env_with_cannot_spoof_credentials(self):
        unsafe = workflow(None).replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - uses: actions/checkout@{PIN}\n"
            "        env:\n          with:\n            persist-credentials: false\n",
        )
        self.verify(unsafe, "nested_checkout_with")

    def test_nested_setting_cannot_spoof_direct_checkout_option(self):
        unsafe = workflow(None).replace(
            f"      - uses: actions/checkout@{PIN}\n",
            f"      - uses: actions/checkout@{PIN}\n"
            "        with:\n          misleading:\n            persist-credentials: false\n",
        )
        self.verify(unsafe, "credentials_outside_with")

    def test_yaml_unicode_encoded_checkout_at_sign_denied(self):
        # GitHub YAML decoding can turn \u0040 into @ *after* text scanning.
        encoded = workflow().replace("actions/checkout@", "actions/checkout" + chr(92) + "u0040")
        self.verify(encoded, "opaque_escaped_yaml")

    def test_yaml_unicode_encoded_checkout_name_denied(self):
        encoded = workflow().replace("actions/checkout@", "actions/chec" + chr(92) + "u006bout@")
        self.verify(encoded, "opaque_escaped_yaml")

    def test_encoded_uses_mapping_key_denied(self):
        encoded = workflow().replace("      - uses:", '      - "' + chr(92) + 'u0075ses":')
        self.verify(encoded, "opaque_escaped_yaml")

    def test_quoted_checkout_yaml_solidus_escape_is_denied(self):
        slash = chr(92) + "/"
        encoded = workflow(None).replace(
            f"actions/checkout@{PIN}", f'"actions{slash}checkout@{PIN}"',
        )
        self.verify(encoded, "opaque_escaped_yaml")

    def test_quoted_checkout_yaml_joined_lines_are_denied(self):
        backslash = chr(92)
        encoded = workflow(None).replace(
            f"actions/checkout@{PIN}",
            f'"actions/chec{backslash}\n          kout@{PIN}"',
        )
        self.verify(encoded, "opaque_escaped_yaml")

    def test_fully_escaped_tagged_uses_key_denied(self):
        backslash = chr(92)
        name = backslash + "u0075" + backslash + "u0073" + backslash + "u0065" + backslash + "u0073"
        encoded = workflow(None).replace(
            f"- uses: actions/checkout@{PIN}",
            f'- !!str "{name}": "actions{backslash}/checkout@{PIN}"',
        )
        self.verify(encoded, "opaque_escaped_yaml")

    def test_yaml_escaped_solidus_only_in_run_literal_is_inert(self):
        backslash = chr(92)
        source = ("jobs:\n  docs:\n    runs-on: ubuntu-latest\n    steps:\n"
                  "      - run: |\n"
                  f"          echo actions{backslash}/checkout@{PIN}\n")
        self.verify(source)

    def test_checkout_in_comment_ignored(self):
        self.verify("# actions/checkout@v4\nname: simple\n")

    def test_inert_workflow_safe(self):
        self.verify("jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n")

    def test_literal_run_block_does_not_create_checkout_step(self):
        body = ("name: harmless docs\njobs:\n  docs:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: |\n"
                f"          echo actions/checkout@{PIN}\n"
                "          echo \u0075ses: not-a-yaml-key\n")
        self.verify(body)

    def test_folded_run_block_does_not_create_checkout_step(self):
        body = ("jobs:\n  docs:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: >-\n"
                f"          echo actions/checkout@{PIN}\n")
        self.verify(body)

    def test_unsafe_real_checkout_after_run_block_still_denied(self):
        body = ("jobs:\n  docs:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: |\n"
                f"          echo actions/checkout@{PIN}\n"
                f"      - uses: actions/checkout@{PIN}\n")
        self.verify(body, "missing_explicit_false")

    def test_unindented_checkout_line_after_run_block_still_denied(self):
        body = ("jobs:\n  docs:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: |\n"
                "          echo commands\n"
                f"      - uses: actions/checkout@{PIN}\n"
                "        with: {}\n")
        self.verify(body, "opaque_checkout_with")

    def test_doc_workflow_run_with_escaped_example_remains_inert(self):
        body = ("jobs:\n  docs:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: |-\n"
                '          echo "uses" "actions/chec' + chr(92) + 'u006bout"\n')
        self.verify(body)

    def test_historical_unsafe_untouched_grandfathered(self):
        self.write(f"{WORK}/legacy.yml", workflow(None))
        self.base = self.commit()
        self.verify("name: safe\n")

    def test_edited_legacy_must_be_remediated(self):
        self.write(f"{WORK}/legacy.yml", workflow(None))
        self.base = self.commit()
        self.verify(workflow(None).replace("echo done", "echo changed"),
                    "missing_explicit_false", path=f"{WORK}/legacy.yml")

    def test_composite_action_checked(self):
        source = ("name: composite\nruns:\n  using: composite\n  steps:\n"
                  f"    - uses: actions/checkout@{PIN}\n")
        self.verify(source, "missing_explicit_false", path=".github/actions/demo/action.yml")

    def test_nonworkflow_docs_unaffected(self):
        self.verify("actions/checkout@v4\n", path="docs/example.md")
        self.assertEqual(inspect(self.base, self.git("rev-parse", "HEAD"))["changed_sources_checked"], 0)

    def test_valid_local_composite_action_tree_allowed(self):
        local_action = ("name: safe composite\nruns:\n  using: composite\n  steps:\n"
                        f"    - uses: actions/checkout@{PIN}\n"
                        "      with:\n        persist-credentials: false\n")
        self.verify(local_action, path=".github/actions/isolated/action.yml")

    def test_composite_action_root_symlink_denied(self):
        directory = Path(".github/actions")
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.symlink_to("../workflows")
        with self.assertRaisesRegex(Denied, "unsupported_action_root"):
            inspect(self.base, self.commit())

    def test_composite_action_subdir_symlink_denied(self):
        directory = Path(".github/actions")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "demo").symlink_to("../../workflows")
        with self.assertRaisesRegex(Denied, "unsupported_action_entry"):
            inspect(self.base, self.commit())

    def test_bidi_override_in_changed_workflow_denied(self):
        self.verify("name: safe" + chr(0x202e) + "\n", "ambiguous_source_control")

    def test_unicode_line_separator_in_changed_workflow_denied(self):
        self.verify("name: safe" + chr(0x2028) + "jobs: {}" + "\n",
                    "ambiguous_source_control")

    def test_unicode_next_line_control_denied(self):
        self.verify("name: safe" + chr(0x85) + "\n", "ambiguous_source_control")

    def test_null_byte_in_changed_workflow_denied(self):
        self.verify("name: safe" + chr(0) + "\n", "ambiguous_source_control")

    def test_accented_utf8_workflow_name_is_safe(self):
        self.verify("name: café\njobs:\n  verify:\n    runs-on: ubuntu-latest\n")

    def test_binary_source_denied(self):
        path = Path(f"{WORK}/added.yml")
        path.write_bytes(b"name: \xff\n")
        with self.assertRaisesRegex(Denied, "invalid_utf8"):
            inspect(self.base, self.commit())

    def test_symlink_denied(self):
        os.symlink("existing.yml", Path(f"{WORK}/added.yml"))
        with self.assertRaisesRegex(Denied, "unsupported_source_type"):
            inspect(self.base, self.commit())

    def test_deleted_workflow_root_denied(self):
        Path(f"{WORK}/existing.yml").unlink()
        with self.assertRaisesRegex(Denied, "missing_workflow_root"):
            inspect(self.base, self.commit())

    def test_workflow_root_symlink_denied(self):
        Path(f"{WORK}/existing.yml").unlink()
        Path(WORK).rmdir()
        Path(WORK).symlink_to("../actions")
        with self.assertRaisesRegex(Denied, "unsupported_workflow_root"):
            inspect(self.base, self.commit())

    def test_unresponsive_git_stdout_stops_at_scan_deadline(self):
        # A stalled stdout read used to hang forever before wait(timeout).
        true_popen = subprocess.Popen
        created = []
        def launch(*args, **kwargs):
            proc = true_popen([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)
            created.append(proc)
            return proc
        start = time.monotonic()
        with mock.patch.object(guard.subprocess, "Popen", side_effect=launch):
            with self.assertRaisesRegex(Denied, "git_source_timeout"):
                guard.git("cat-file", "-s", "deadbeef", deadline=time.monotonic() + 0.2)
        self.assertLess(time.monotonic() - start, 3)
        self.assertTrue(created)
        self.assertIsNotNone(created[0].poll(), "timeout must kill/reap child")

    def test_oversized_subprocess_stdout_stops_before_buffering(self):
        true_popen = subprocess.Popen
        created = []
        def launch(*args, **kwargs):
            proc = true_popen([sys.executable, "-c",
                               "import sys; sys.stdout.buffer.write(b'x' * 131072)"], **kwargs)
            created.append(proc)
            return proc
        with mock.patch.object(guard.subprocess, "Popen", side_effect=launch):
            with self.assertRaisesRegex(Denied, "git_output_limit"):
                guard.git("cat-file", "blob", "deadbeef", max_bytes=64)
        self.assertTrue(created)
        self.assertIsNotNone(created[0].poll(), "oversize must kill/reap child")

    def test_invalid_subprocess_limit_never_launches(self):
        with mock.patch.object(guard.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(Denied, "invalid_git_output_budget"):
                guard.git("rev-parse", "HEAD", max_bytes=True)
            launch.assert_not_called()

    def test_elapsed_global_scan_deadline_denied_before_git(self):
        self.write(f"{WORK}/added.yml", "name: extra\\n")
        head = self.commit()
        with mock.patch.object(guard, "MAX_SCAN_SECONDS", 0):
            with self.assertRaisesRegex(Denied, "scan_deadline_exceeded"):
                inspect(self.base, head)

    def test_oversized_workflow_content_denied(self):
        self.write(f"{WORK}/added.yml", "x" * (guard.MAX_FILE_BYTES + 1))
        with self.assertRaisesRegex(Denied, "oversized_source"):
            inspect(self.base, self.commit())

    def test_git_replace_ref_cannot_hide_unsafe_blob(self):
        # An untrusted local refs/replace object must not alter SHA-bound source.
        self.write(f"{WORK}/added.yml", workflow(None))
        head = self.commit()
        unsafe_oid = self.git("rev-parse", f"{head}:{WORK}/added.yml")
        self.write("safe-blob.yml", workflow("false"))
        safe_oid = self.git("hash-object", "-w", "safe-blob.yml")
        self.git("replace", unsafe_oid, safe_oid)
        with self.assertRaisesRegex(Denied, "missing_explicit_false"):
            inspect(self.base, head)

    def test_invalid_sha_denied(self):
        with self.assertRaisesRegex(Denied, "invalid_commit_identity"):
            inspect("main", self.base)
        with self.assertRaisesRegex(Denied, "invalid_commit_identity"):
            inspect(self.base, self.base)

    def test_redacted_deny_report_no_authority(self):
        self.write(f"{WORK}/added.yml", workflow("true") + "# PRIVATE_TEST_MARKER\n")
        head = self.commit()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--base", self.base, "--head", head])
        self.assertEqual(result, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "deny")
        self.assertFalse(report["authorizes_merge"])
        self.assertFalse(report["authorizes_deploy"])
        self.assertFalse(report["mutation_performed"])
        self.assertNotIn("PRIVATE_TEST_MARKER", output.getvalue())

if __name__ == "__main__":
    unittest.main()
