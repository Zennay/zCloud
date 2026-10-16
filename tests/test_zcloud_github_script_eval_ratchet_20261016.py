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
