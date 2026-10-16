"""Real-Git regressions for no-new github-script inline JavaScript expressions."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_github_script_eval_ratchet_20261016 import EvidenceError, evaluate, risks

SAFE = """name: sample
on: [pull_request]
jobs:
  proof:
    runs-on: ubuntu-latest
    steps:
      - name: Evaluate
        uses: actions/github-script@0123456789012345678901234567890123456789
        with:
          script: |
            const title = process.env.PR_TITLE;
            core.info(title.length.toString());
        env:
          PR_TITLE: @EXPR@ github.event.pull_request.title }}
""".replace("@EXPR@", "$" + "{{")
BAD = SAFE.replace("const title = process.env.PR_TITLE;", "const title = '@EXPR@ github.event.pull_request.title }}';").replace("@EXPR@", "$" + "{{")
BAD_INPUT = SAFE.replace("core.info(title.length.toString());", "core.info('@EXPR@ inputs.free_text }}');").replace("@EXPR@", "$" + "{{")
QUOTED = SAFE.replace(
    "          script: |\n            const title = process.env.PR_TITLE;\n            core.info(title.length.toString());",
    '          script: "core.info(@EXPR@ github.event.issue.title }});"'.replace("@EXPR@", "$" + "{{"),
)
ALIASED = SAFE.replace(
    "          script: |\n            const title = process.env.PR_TITLE;\n            core.info(title.length.toString());",
    "          script: *remote_code",
)
ESCAPED = SAFE.replace(
    "          script: |\n            const title = process.env.PR_TITLE;\n            core.info(title.length.toString());",
    r'          script: "core.info(\u0024{{ github.event.issue.title }});"',
)
OTHER = """name: harmless
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo "@EXPR@ github.event.pull_request.title }}"
""".replace("@EXPR@", "$" + "{{")


class GithubScriptRatchetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "ci@example.test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Source CI"], cwd=self.root, check=True)

    def run_git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root).decode().strip()

    def commit(self, source, path=".github/workflows/sample.yml"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        self.run_git("add", "--", path)
        self.run_git("commit", "-qm", "source candidate")
        return self.run_git("rev-parse", "HEAD")

    def evaluate(self, base, head):
        old = os.getcwd()
        os.chdir(self.root)
        try:
            return evaluate(base, head)
        finally:
            os.chdir(old)

    def test_clean_environment_indirection(self):
        a = self.commit(OTHER)
        b = self.commit(SAFE)
        receipt = self.evaluate(a, b)
        self.assertEqual(receipt["new_findings"], 0)
        self.assertFalse(receipt["release_authorized"])
        self.assertFalse(receipt["production_mutation_authorized"])

    def test_direct_event_script_is_denied(self):
        a = self.commit(SAFE)
        b = self.commit(BAD)
        self.assertEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_direct_input_script_is_denied(self):
        a = self.commit(SAFE)
        b = self.commit(BAD_INPUT)
        self.assertEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_quoted_scalar_direct_interpolation_denied(self):
        a = self.commit(SAFE)
        b = self.commit(QUOTED)
        self.assertEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_untrusted_expression_in_run_only_not_this_guard(self):
        a = self.commit(SAFE)
        b = self.commit(SAFE + "      - run: echo '@EXPR@ github.event.issue.title }}'\n".replace("@EXPR@", "$" + "{{"))
        self.assertEqual(self.evaluate(a, b)["new_findings"], 0)

    def test_edit_safe_script_is_accepted(self):
        a = self.commit(SAFE)
        b = self.commit(SAFE.replace("title.length", "title.trim().length"))
        self.assertEqual(self.evaluate(a, b)["new_findings"], 0)

    def test_insertion_legacy_script_is_allowed(self):
        a = self.commit(BAD)
        b = self.commit(BAD + "      - run: echo harmless\n")
        self.assertEqual(self.evaluate(a, b)["new_findings"], 0)

    def test_modifying_legacy_risky_step_is_denied(self):
        a = self.commit(BAD)
        b = self.commit(BAD.replace("github.event.pull_request.title", "github.event.issue.title"))
        self.assertEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_relocating_legacy_step_is_denied(self):
        a = self.commit(BAD)
        b = self.commit(BAD.replace("      - name: Evaluate", "\n      - name: Evaluate"))
        self.assertEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_removing_hazard_reduces_debt(self):
        a = self.commit(BAD)
        b = self.commit(SAFE)
        self.assertEqual(self.evaluate(a, b)["new_findings"], 0)

    def test_alias_script_is_denied(self):
        a = self.commit(SAFE)
        b = self.commit(ALIASED)
        self.assertEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_unicode_escape_in_script_is_denied(self):
        a = self.commit(SAFE)
        b = self.commit(ESCAPED)
        self.assertEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_anchor_redefinition_cannot_reuse_legacy_alias_waiver(self):
        a = self.commit("x-source: &remote_code core.info('safe')\n" + ALIASED)
        b = self.commit("x-source: &remote_code core.info('different')\n" + ALIASED)
        self.assertEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_aliased_github_script_uses_denied(self):
        a = self.commit(SAFE)
        anchor = "x-action: &githubscript actions/github-script@0123456789012345678901234567890123456789\n"
        b = self.commit(anchor + SAFE.replace(
            "uses: actions/github-script@0123456789012345678901234567890123456789",
            "uses: *githubscript",
        ))
        self.assertGreaterEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_flow_mapping_github_script_uses_denied(self):
        a = self.commit(SAFE)
        replacement = "      - {uses: actions/github-script@0123456789012345678901234567890123456789, with: {script: 'core.info(1);'}}"
        start = SAFE.index("      - name: Evaluate")
        stop = SAFE.index("        env:", start)
        b = self.commit(SAFE[:start] + replacement + "\n" + SAFE[stop:])
        self.assertGreaterEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_encoded_uses_key_denied(self):
        a = self.commit(SAFE)
        b = self.commit(SAFE.replace(
            "uses: actions/github-script@",
            r'"u\u0073es": actions/github-script@',
        ))
        self.assertGreaterEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_encoded_script_key_denied(self):
        a = self.commit(SAFE)
        b = self.commit(SAFE.replace("script: |", r'"scr\u0069pt": |'))
        self.assertGreaterEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_nested_composite_action_manifest_is_scanned(self):
        safe = """name: local
runs:
  using: composite
  steps:
    - uses: actions/github-script@0123456789012345678901234567890123456789
      with:
        script: |
          core.info(process.env.MESSAGE);
"""
        a = self.commit(safe, path=".github/actions/local/action.yml")
        b = self.commit(safe.replace(
            "core.info(process.env.MESSAGE);",
            "core.info('@EXPR@ inputs.message }}');".replace("@EXPR@", "$" + "{{"),
        ), path=".github/actions/local/action.yml")
        self.assertEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_root_composite_action_manifest_is_scanned(self):
        safe = """name: local
runs:
  using: composite
  steps:
    - uses: actions/github-script@0123456789012345678901234567890123456789
      with:
        script: core.info(process.env.MESSAGE);
"""
        a = self.commit(safe, path="action.yaml")
        b = self.commit(safe.replace(
            "script: core.info(process.env.MESSAGE);",
            "script: core.info('@EXPR@ github.event.issue.title }}');".replace("@EXPR@", "$" + "{{"),
        ), path="action.yaml")
        self.assertEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_nonmanifest_yaml_is_not_scanned(self):
        a = self.commit("safe: yes\n", path="other/sample.yml")
        b = self.commit("unsafe: '@EXPR@ github.event.issue.title }}'\n".replace("@EXPR@", "$" + "{{"), path="other/sample.yml")
        self.assertEqual(self.evaluate(a, b)["new_findings"], 0)

    def test_unicode_escaped_github_script_action_name_is_denied(self):
        a = self.commit(SAFE)
        b = self.commit(SAFE.replace(
            "uses: actions/github-script@0123456789012345678901234567890123456789",
            r'uses: "actions/\u0067ithub-script@0123456789012345678901234567890123456789"',
        ))
        self.assertGreaterEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_unicode_escaped_uses_key_and_action_name_denied(self):
        a = self.commit(SAFE)
        b = self.commit(SAFE.replace(
            "uses: actions/github-script@0123456789012345678901234567890123456789",
            r'"u\u0073es": "actions/\u0067ithub-script@0123456789012345678901234567890123456789"',
        ))
        self.assertGreaterEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_explicit_yaml_script_mapping_key_is_denied(self):
        a = self.commit(SAFE)
        b = self.commit(SAFE.replace(
            "          script: |",
            "          ? script\n          : |",
        ))
        self.assertGreaterEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_inherited_with_mapping_alias_is_denied(self):
        a = self.commit(SAFE)
        start = SAFE.index("        with:")
        end = SAFE.index("        env:", start)
        b = self.commit(SAFE[:start] + "        with: *remote_script_properties\n" + SAFE[end:])
        self.assertGreaterEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_inherited_yaml_merge_key_is_denied(self):
        a = self.commit(SAFE)
        b = self.commit(SAFE.replace(
            "          script: |",
            "          <<: *remote_script_properties\n          script: |",
        ))
        self.assertGreaterEqual(self.evaluate(a, b)["new_findings"], 1)

    def test_script_in_unrelated_action_is_not_guarded(self):
        a = self.commit(OTHER)
        b = self.commit(OTHER + "\n# GitHub-script not invoked\n")
        self.assertEqual(self.evaluate(a, b)["new_findings"], 0)

    def test_invalid_sha_denied(self):
        a = self.commit(SAFE)
        b = self.commit(BAD)
        with self.assertRaises(EvidenceError):
            self.evaluate("zz" * 20, b)
        with self.assertRaises(EvidenceError):
            self.evaluate(b, b)

    def test_ancestry_denied(self):
        a = self.commit(SAFE)
        b = self.commit(BAD)
        with self.assertRaises(EvidenceError):
            self.evaluate(b, a)

    def test_symlink_workflow_denied(self):
        a = self.commit(SAFE)
        target = self.root / ".github/workflows/sample.yml"
        target.unlink()
        target.symlink_to("../somewhere")
        self.run_git("add", "-A")
        self.run_git("commit", "-qm", "bad symlink")
        b = self.run_git("rev-parse", "HEAD")
        with self.assertRaises(EvidenceError):
            self.evaluate(a, b)

    def test_git_namespace_cannot_change_source_objects(self):
        a = self.commit(SAFE)
        b = self.commit(BAD)
        old = os.environ.get("GIT_NAMESPACE")
        os.environ["GIT_NAMESPACE"] = "spoofed"
        try:
            self.assertEqual(self.evaluate(a, b)["new_findings"], 1)
        finally:
            if old is None:
                os.environ.pop("GIT_NAMESPACE", None)
            else:
                os.environ["GIT_NAMESPACE"] = old


if __name__ == "__main__":
    unittest.main()
