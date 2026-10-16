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

    def test_second_identical_dispatch_in_existing_workflow_is_denied(self):
        base = self.commit(DANGEROUS)
        head = self.commit(DANGEROUS.replace(
            "gh workflow run unsafe.yml",
            "gh workflow run unsafe.yml\n          gh workflow run unsafe.yml"))
        report = evaluate(self.repo, base, head)
        self.assertEqual(report["previous_findings"], 1)
        self.assertEqual(report["current_findings"], 2)
        self.assertEqual(report["new_findings"], [WORKFLOW])

    def test_changed_dispatch_target_is_new_risk(self):
        base = self.commit(DANGEROUS)
        head = self.commit(DANGEROUS.replace("unsafe.yml", "different.yml"))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_benign_adjacent_edit_preserves_legacy_fingerprint(self):
        base = self.commit(DANGEROUS)
        head = self.commit(DANGEROUS + "\n# unrelated note\n")
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [])

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

    def test_inline_trigger_alias_and_ambiguous_list_deny(self):
        malformed = (
            "on: [*untrusted]\njobs:\n",
            "on: [push, *untrusted]\njobs:\n",
            "on: [workflow_run, *untrusted]\njobs:\n",
            "on: {push: {}, workflow_run: *untrusted}\njobs:\n",
            "on: {<<: *defaults, workflow_run: {}}\njobs:\n",
            "on: [push, workflow_run, workflow_run]\njobs:\n",
            "on: [push, !custom]\njobs:\n",
            "on: []\njobs:\n",
        )
        for source in malformed:
            with self.subTest(source=source), self.assertRaises(EvidenceError):
                workflow_run_event(source)

    def test_literal_inline_trigger_collections_still_classify(self):
        for source in (
            "on: [workflow_run]\njobs:\n",
            "on: ['push', \"workflow_run\"]\njobs:\n",
            "on: {workflow_run: {types: [completed]}, push: {}}\njobs:\n",
        ):
            with self.subTest(source=source):
                self.assertTrue(workflow_run_event(source))

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

    def test_dispatch_flag_order_and_implicit_post(self):
        commands = (
            "gh api /repos/Zennay/zCloud/actions/workflows/x.yml/dispatches -X POST",
            "gh api /repos/Zennay/zCloud/actions/workflows/x.yml/dispatches -f ref=main",
            "gh api --method=POST /repos/Zennay/zCloud/dispatches",
            "curl https://api.github.com/repos/Zennay/zCloud/dispatches -X POST",
            "curl https://api.github.com/repos/Zennay/zCloud/dispatches --data '{ }'",
            "gh -R Zennay/zCloud workflow run x.yml",
            "gh --repo=Zennay/zCloud run rerun 123",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_read_only_dispatch_endpoint_is_not_a_post(self):
        for command in (
            "gh api /repos/Zennay/zCloud/dispatches",
            "curl https://api.github.com/repos/Zennay/zCloud/dispatches",
            "gh api -X GET /repos/Zennay/zCloud/dispatches",
            "curl -X GET https://api.github.com/repos/Zennay/zCloud/dispatches",
        ):
            with self.subTest(command=command):
                self.assertFalse(classify(TEMPLATE.replace("echo harmless", command)))


    def test_dispatch_actions_are_counted_as_recursive_sites(self):
        for action in (
            "peter-evans/repository-dispatch@v3",
            "benc-uk/workflow-dispatch@v1",
            "convictional/trigger-workflow-and-wait@v1",
        ):
            with self.subTest(action=action):
                source = TEMPLATE.replace(
                    "      - run: |\n          echo harmless",
                    "      - uses: " + action + "\n        with:\n          event-type: ping",
                )
                self.assertTrue(classify(source))
        self.assertFalse(classify(TEMPLATE.replace(
            "      - run: |\n          echo harmless",
            "      - uses: actions/checkout@v4",
        )))

    def test_dispatch_action_changed_input_is_new_debt(self):
        source = TEMPLATE.replace(
            "      - run: |\n          echo harmless",
            "      - name: Release notice\n        uses: peter-evans/repository-dispatch@v3\n        with:\n          event-type: trigger",
        )
        base = self.commit(source)
        head = self.commit(source.replace("event-type: trigger", "event-type: different"))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_dispatch_action_reordered_step_keys_detected(self):
        source = TEMPLATE.replace(
            "      - run: |\n          echo harmless",
            "      - with:\n          event-type: ping\n        uses: peter-evans/repository-dispatch@v3",
        )
        self.assertTrue(classify(source))

    def test_dispatch_action_duplication_is_new_debt(self):
        original_step = "      - uses: peter-evans/repository-dispatch@v3"
        source = TEMPLATE.replace(
            "      - run: |\n          echo harmless", original_step)
        base = self.commit(source)
        head = self.commit(source.replace(original_step, original_step+"\n"+original_step))
        report = evaluate(self.repo, base, head)
        self.assertEqual(report["previous_findings"], 1)
        self.assertEqual(report["current_findings"], 2)
        self.assertEqual(report["new_findings"], [WORKFLOW])

    def test_on_variants(self):
        self.assertTrue(workflow_run_event("on: [push, workflow_run]\njobs:\n"))
        self.assertTrue(workflow_run_event('"on": {workflow_run: {types: [completed]}}\njobs:\n'))
        self.assertFalse(workflow_run_event("on: [push]\njobs:\n"))


if __name__ == "__main__":
    unittest.main()
