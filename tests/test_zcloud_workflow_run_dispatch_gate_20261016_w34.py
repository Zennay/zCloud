"""Real Git-fixture regressions for source-only workflow_run recursion admission."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.zcloud_workflow_run_dispatch_gate_20261016_w34 import (
    EvidenceError, classify, evaluate, main, workflow_run_event,
)

WORKFLOW = ".github/workflows/loop.yml"
TEMPLATE = """name: offline proof
on:
  workflow_run:
    workflows: [zCloud regression]
    types: [completed]
permissions:
  contents: read
jobs:
  proof:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo harmless
"""
DANGEROUS = TEMPLATE.replace("echo harmless", "gh workflow run unsafe.yml")


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Gate test")
        self.git("config", "user.email", "gate@example.invalid")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo).decode().strip()

    def commit(self, content, name=WORKFLOW):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_new_recursion_rejected(self):
        base = self.commit(TEMPLATE)
        head = self.commit(DANGEROUS)
        report = evaluate(self.repo, base, head)
        self.assertEqual(report["new_findings"], [WORKFLOW])
        self.assertFalse(report["production_mutation_authorized"])
        self.assertFalse(report["merge_authorized"])
        self.assertEqual(main(["--root", str(self.repo), "--base", base, "--head", head]), 1)

    def test_unchanged_legacy_is_inventory_only(self):
        base = self.commit(DANGEROUS)
        head = self.commit(DANGEROUS + "\n# only comment\n")
        report = evaluate(self.repo, base, head)
        self.assertEqual(report["previous_findings"], 1)
        self.assertEqual(report["new_findings"], [])

    def test_reduced_debt_is_allowed(self):
        base = self.commit(DANGEROUS)
        head = self.commit(TEMPLATE)
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [])

    def test_safe_trigger_does_not_count(self):
        base = self.commit(TEMPLATE.replace("workflow_run:", "push:"))
        head = self.commit(DANGEROUS.replace("workflow_run:", "push:"))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [])

    def test_real_git_addition_and_symlink_fail_closed(self):
        base = self.commit(TEMPLATE)
        head = self.commit(DANGEROUS, ".github/workflows/new.yml")
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [".github/workflows/new.yml"])
        (self.repo / WORKFLOW).unlink()
        (self.repo / WORKFLOW).symlink_to("/etc/passwd")
        self.git("add", ".")
        self.git("commit", "-qm", "symlink")
        with self.assertRaises(EvidenceError):
            evaluate(self.repo, base, self.git("rev-parse", "HEAD"))

    def test_nonancestor_rejected(self):
        base = self.commit(TEMPLATE)
        head = self.commit(DANGEROUS)
        with self.assertRaises(EvidenceError):
            evaluate(self.repo, head, base)

    def test_malformed_sha_rejected(self):
        base = self.commit(TEMPLATE)
        with self.assertRaises(EvidenceError):
            evaluate(self.repo, "main", base)

    def test_missing_duplicate_or_aliased_on_rejected(self):
        for src in ["name: no event\n", "on: push\non: workflow_run\n",
                    "on: *danger\n"]:
            with self.assertRaises(EvidenceError):
                workflow_run_event(src)

    def test_run_body_detection(self):
        self.assertTrue(classify(DANGEROUS))
        self.assertTrue(classify(TEMPLATE.replace("echo harmless", "gh run rerun 123")))
        self.assertTrue(classify(TEMPLATE.replace("echo harmless", "gh api -X POST /repos/Zennay/zCloud/actions/workflows/a.yml/dispatches")))
        self.assertTrue(classify(TEMPLATE.replace("echo harmless", "curl -X POST https://api.github.com/repos/Zennay/zCloud/dispatches")))
        self.assertTrue(classify(TEMPLATE.replace("echo harmless", "octokit.rest.actions.createWorkflowDispatch({})")))
        self.assertFalse(classify(TEMPLATE.replace("echo harmless", "# gh workflow run old.yml\necho safe")))
        self.assertFalse(classify(TEMPLATE.replace("echo harmless", "echo safe\n# gh workflow run old.yml")))
        self.assertFalse(classify(TEMPLATE.replace("echo harmless", "echo safe").replace("on:\n  workflow_run:", "on: [push]")))
        self.assertFalse(classify(TEMPLATE + "\n# gh workflow run docs\n"))

    def test_on_variants(self):
        self.assertTrue(workflow_run_event("on: [push, workflow_run]\njobs:\n"))
        self.assertTrue(workflow_run_event('"on": {workflow_run: {types: [completed]}}\njobs:\n'))
        self.assertFalse(workflow_run_event("on: [push]\njobs:\n"))


if __name__ == "__main__":
    unittest.main()
