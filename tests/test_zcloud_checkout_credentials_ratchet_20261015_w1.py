"""Real Git object regression tests for isolated checkout credential source ratchet."""
from __future__ import annotations
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
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

    def test_quoted_uses_ambiguity_denied(self):
        self.verify(workflow().replace("- uses:", '- "uses":'), "ambiguous_checkout")

    def test_unpinned_uses_denied(self):
        self.verify(workflow().replace(PIN, "v4"), "ambiguous_checkout")

    def test_folded_checkout_denied(self):
        self.verify(workflow().replace("uses: actions/checkout@", "uses: >-\n          actions/checkout@"),
                    "ambiguous_checkout")

    def test_checkout_in_comment_ignored(self):
        self.verify("# actions/checkout@v4\nname: simple\n")

    def test_inert_workflow_safe(self):
        self.verify("jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n")

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

    def test_binary_source_denied(self):
        path = Path(f"{WORK}/added.yml")
        path.write_bytes(b"name: \xff\n")
        with self.assertRaisesRegex(Denied, "invalid_utf8"):
            inspect(self.base, self.commit())

    def test_symlink_denied(self):
        os.symlink("existing.yml", Path(f"{WORK}/added.yml"))
        with self.assertRaisesRegex(Denied, "unsupported_source_type"):
            inspect(self.base, self.commit())

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
