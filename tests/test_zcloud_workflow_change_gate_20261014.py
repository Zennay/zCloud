"""Offline negative cases for source-only workflow privilege gate."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

TARGET = Path(__file__).resolve().parents[1] / "scripts/zcloud_workflow_change_gate_20261014.py"
spec = importlib.util.spec_from_file_location("workflow_gate", TARGET)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

HOSTED = """name: hosted
on:
  pull_request:
    branches: [main]
jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - run: echo offline
"""
AUTO = """name: dangerous
on:
  push:
    branches: [main]
jobs:
  install:
    runs-on: [self-hosted, zcloud, vps]
    steps:
      - run: systemctl restart live
"""
MANUAL = """name: bounded
on:
  pull_request:
  workflow_dispatch:
    inputs:
      confirm_sha:
        type: string
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - run: echo source
  apply:
    if: >-
      github.event_name == 'workflow_dispatch' &&
      github.repository == 'Zennay/zCloud' &&
      github.ref == 'refs/heads/main' &&
      github.actor == 'Zennay' &&
      github.triggering_actor == 'Zennay' &&
      github.run_attempt == 1
    runs-on: [self-hosted, zcloud, vps]
    steps:
      - run: |
          test "$GITHUB_SHA" = "$CONFIRMED_SHA"
          git ls-remote origin refs/heads/main
"""


class WorkflowChangeGateTests(unittest.TestCase):
    def test_hosted_pr_passes(self):
        self.assertEqual(gate.judge(HOSTED), [])

    def test_push_selfhosted_denied(self):
        self.assertIn("install:nonhosted_without_manual_event", gate.judge(AUTO))

    def test_structural_manual_gate_passes(self):
        self.assertEqual(gate.judge(MANUAL), [])

    def test_missing_actor_denied(self):
        altered = MANUAL.replace("github.actor == 'Zennay'", "true")
        self.assertTrue(any("incomplete_manual_if" in p for p in gate.judge(altered)))

    def test_or_bypass_denied(self):
        altered = MANUAL.replace("github.actor == 'Zennay'", "github.actor == 'Zennay' || true")
        self.assertTrue(any("incomplete_manual_if" in p for p in gate.judge(altered)))

    def test_missing_main_check_denied(self):
        altered = MANUAL.replace("git ls-remote", "echo")
        self.assertTrue(any("no_exact_main_preflight" in p for p in gate.judge(altered)))

    def test_pull_request_selfhosted_denied(self):
        self.assertTrue(gate.judge(AUTO.replace("push:", "pull_request:")))

    def test_dynamic_runner_denied(self):
        self.assertTrue(gate.judge(HOSTED.replace("ubuntu-latest", "project-runner")))

    def test_inequality_bypass_denied(self):
        altered = MANUAL.replace("github.actor == 'Zennay'", "github.actor != 'Zennay'")
        self.assertIn("apply:incomplete_manual_if", gate.judge(altered))

    def test_wrong_triggering_actor_denied(self):
        altered = MANUAL.replace("github.triggering_actor == 'Zennay'", "github.triggering_actor == 'SomeoneElse'")
        self.assertIn("apply:incomplete_manual_if", gate.judge(altered))

    def test_second_attempt_denied(self):
        altered = MANUAL.replace("github.run_attempt == 1", "github.run_attempt == 2")
        self.assertIn("apply:incomplete_manual_if", gate.judge(altered))

    def test_wrong_repo_denied(self):
        altered = MANUAL.replace("github.repository == 'Zennay/zCloud'", "github.repository == 'other/repo'")
        self.assertIn("apply:incomplete_manual_if", gate.judge(altered))

    def test_wrong_branch_denied(self):
        altered = MANUAL.replace("github.ref == 'refs/heads/main'", "github.ref == 'refs/heads/testing'")
        self.assertIn("apply:incomplete_manual_if", gate.judge(altered))

    def test_missing_confirm_sha_input_denied(self):
        altered = MANUAL.replace("      confirm_sha:\n        type: string", "      other_input:\n        type: string")
        self.assertIn("apply:no_exact_main_preflight", gate.judge(altered))

    def test_missing_on_denied(self):
        self.assertEqual(gate.judge("jobs:\n  test:\n    runs-on: ubuntu-latest\n"), ["missing_literal_on"])

    def test_unknown_event_denied(self):
        self.assertEqual(gate.judge(HOSTED.replace("pull_request:", "custom_hook:")), ["unknown_event"])

    def test_missing_jobs_denied(self):
        self.assertEqual(gate.judge("on: [push]\n"), ["missing_jobs"])

    def test_inline_events_work(self):
        self.assertEqual(gate.judge(HOSTED.replace("on:\n  pull_request:\n    branches: [main]", "on: [pull_request]")), [])

    def test_exact_sha_required(self):
        with self.assertRaises(ValueError):
            gate.changed_workflows("main", "b" * 40)

    def test_path_traversal_denied(self):
        self.assertEqual(gate.evaluate([".github/workflows/../secrets.yml"]), ["invalid_workflow_path"])

    def test_diff_uses_only_workflow_paths(self):
        with mock.patch.object(gate.subprocess, "check_output", return_value=b".github/workflows/new.yml\0") as run:
            self.assertEqual(gate.changed_workflows("a" * 40, "b" * 40), [".github/workflows/new.yml"])
            self.assertEqual(run.call_args.args[0][-2:], ["--", ".github/workflows/"])


if __name__ == "__main__":
    unittest.main()
