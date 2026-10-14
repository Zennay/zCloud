"""Executed offline regression for emergency source-only ratchet (no live calls)."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import zcloud_emergency_entrypoint_ratchet_20261014 as policy

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts/zcloud_emergency_entrypoint_ratchet_20261014.py"
FILES = {name: (ROOT / policy.WORKFLOW_ROOT / name).read_text(encoding="utf-8")
         for name in policy.POLICY}
QUEUE = "zcloud-hosted-emergency-queue-sweep.yml"
ADDON = "zcloud-emergency-addon-hot-reload.yml"
WORKER = "zcloud-worker-prompt-stop-20261013.yml"


def altered(name: str, before: str, after: str) -> list[str]:
    text = FILES[name]
    if before not in text:
        raise AssertionError("fixture changed: " + name)
    return policy.inspect_text(name, text.replace(before, after, 1))


def altered_live(name: str, before: str, after: str) -> list[str]:
    source = FILES[name]
    marker = "- name: " + policy.POLICY[name]["step"]
    if marker not in source:
        raise AssertionError("missing live-step fixture: " + name)
    head, tail = source.split(marker, 1)
    if before not in tail:
        raise AssertionError("fixture is not in sensitive step: " + name)
    return policy.inspect_text(name, head + marker + tail.replace(before, after, 1))


class EmergencySourceRatchetTests(unittest.TestCase):
    def test_existing_three_trusted_main_contracts_pass(self):
        self.assertEqual(3, len(FILES))
        self.assertEqual({}, {n: errors for n, errors in policy.inspect_repo(ROOT).items() if errors})

    def test_automatic_or_unknown_event_regressions_denied(self):
        for name in FILES:
            for trigger in ("push", "schedule", "workflow_run", "pull_request_target"):
                with self.subTest(name=name, trigger=trigger):
                    errors = altered(name, "\n  workflow_dispatch:\n",
                                     f"\n  {trigger}:\n    branches: [main]\n  workflow_dispatch:\n")
                    self.assertIn("automatic_or_unknown_trigger", errors)

    def test_new_write_token_permissions_rejected_for_all_three(self):
        for name in FILES:
            with self.subTest(name=name):
                errors = altered(name, "  contents: read\n", "  contents: write\n")
                self.assertIn("token_permissions_not_readonly", errors)

    def test_queue_cannot_regain_global_cancellation_or_status_write(self):
        self.assertIn("queue_mutation_authority_reappeared",
                      altered(QUEUE, 'method="GET"', 'method="POST"'))
        self.assertIn("queue_mutation_authority_reappeared",
                      altered(QUEUE, "  actions: read", "  actions: write"))
        self.assertIn("privileged_queue_observer_runner",
                      altered_live(QUEUE, "    runs-on: ubuntu-latest",
                                   "    runs-on: [self-hosted, zcloud, vps]"))

    def test_missing_retrigger_guards_denied_before_private_actions(self):
        for name in FILES:
            with self.subTest(name=name):
                self.assertIn("missing_job_admission",
                              altered(name, "github.triggering_actor == 'Zennay'",
                                      "github.triggering_actor == 'other'"))
                self.assertIn("missing_job_admission",
                              altered(name, "github.run_attempt == 1", "github.run_attempt >= 1"))

    def test_private_worker_read_cannot_lose_consent(self):
        self.assertIn("missing_explicit_optin",
                      altered(WORKER, "inputs.approve_readonly_diagnostic == true",
                              "inputs.approve_readonly_diagnostic != false"))
        self.assertIn("missing_in_step_consent",
                      altered_live(WORKER, 'test "$APPROVE_DIAGNOSTIC" = "true"',
                              'test "$APPROVE_DIAGNOSTIC" != "false"'))

    def test_live_addon_reload_cannot_lose_explicit_consent(self):
        self.assertIn("missing_explicit_optin",
                      altered(ADDON, "inputs.approve_addon_reload == true",
                              "inputs.approve_addon_reload != false"))
        self.assertIn("missing_in_step_consent",
                      altered_live(ADDON, 'test "$APPROVE_RELOAD" = "true"',
                              'test "$APPROVE_RELOAD" != "false"'))

    def test_current_main_must_be_fetched_inside_sensitive_step(self):
        for name in FILES:
            with self.subTest(name=name):
                errors = altered(name, "timeout 15 git ls-remote --exit-code origin refs/heads/main",
                                 "printf '%s' \"$GITHUB_SHA\"")
                # Some workflows also have an early preflight; remove the occurrence
                # from the actual live step to ensure it cannot rely on that preflight.
                original = FILES[name]
                start = original.index("- name: " + policy.POLICY[name]["step"])
                edited = original[:start] + original[start:].replace(
                    "timeout 15 git ls-remote --exit-code origin refs/heads/main",
                    "printf '%s' \"$GITHUB_SHA\"", 1)
                self.assertIn("missing_in_step_fresh_main",
                              policy.inspect_text(name, edited))
                self.assertIsInstance(errors, list)

    def test_checkout_provenance_and_job_admission_are_required(self):
        for name in FILES:
            with self.subTest(name=name):
                self.assertIn("checkout_provenance_regressed",
                              altered(name, "persist-credentials: false", "persist-credentials: true"))
                self.assertIn("missing_job_admission",
                              altered(name, "inputs.confirm_sha == github.sha",
                                      "inputs.confirm_sha != github.sha"))

    def test_symlink_and_absent_workflow_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            workflows = Path(td) / policy.WORKFLOW_ROOT
            workflows.mkdir(parents=True)
            for name, source in FILES.items():
                (workflows / name).write_text(source, encoding="utf-8")
            self.assertEqual({}, {n: x for n, x in policy.inspect_repo(Path(td)).items() if x})
            missing = workflows / WORKER
            missing.unlink()
            self.assertIn("missing_or_symlink_workflow", policy.inspect_repo(Path(td))[WORKER])
            missing.symlink_to(workflows / ADDON)
            self.assertIn("missing_or_symlink_workflow", policy.inspect_repo(Path(td))[WORKER])

    def test_cli_is_bounded_read_only_and_no_secrets_logged(self):
        result = subprocess.run([sys.executable, str(SRC), "--repo-root", str(ROOT)],
                                text=True, capture_output=True, timeout=5, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(3, payload["inspected_workflows"])
        self.assertEqual(0, payload["finding_count"])
        self.assertEqual({}, payload["findings"])
        self.assertFalse(payload["mutation_performed"])
        self.assertFalse(payload["live_vps_checked"])
        self.assertNotIn("GITHUB_TOKEN", result.stdout)
        self.assertNotIn("http://127.0.0.1:8765", result.stdout)


if __name__ == "__main__":
    unittest.main()
