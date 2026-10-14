"""Hosted-only trust regression for the historical HaxLab recovery workflow.

This module never imports or executes the live recovery workflow. It checks the
privileged GitHub Actions YAML as source text, with adversarial mutations. The
workflow itself stays with its serialized live owner (#856 / PWQ-41).
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".github/workflows/zcloud-haxlab-allocation-recovery.yml"

def failures(source: str) -> list[str]:
    problems: list[str] = []
    if "\non:\n" not in source or "\npermissions:\n" not in source:
        return ["missing_top_level_boundary"]
    triggers = source.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    if re.findall(r"(?m)^  ([A-Za-z_][A-Za-z_0-9-]*):", triggers) != ["workflow_dispatch"]:
        problems.append("manual_trigger_only")
    if "confirm_sha:" not in triggers or "required: true" not in triggers:
        problems.append("explicit_sha_input")
    if not re.search(r"(?m)^permissions:\n  contents: read\s*$", source):
        problems.append("read_only_token")
    jobs = source.split("\njobs:\n", 1)[1] if "\njobs:\n" in source else ""
    if re.findall(r"(?m)^  ([a-z][a-z0-9-]*):\s*$", jobs) != ["recover"]:
        problems.append("single_privileged_job")
    head = jobs.split("    steps:\n", 1)[0]
    for name, value in {
        "manual_event": "github.event_name == 'workflow_dispatch'",
        "canonical_repo": "github.repository == 'Zennay/zCloud'",
        "main_branch": "github.ref == 'refs/heads/main'",
        "owner": "github.actor == 'Zennay'",
        "triggering_owner": "github.triggering_actor == 'Zennay'",
        "first_attempt": "github.run_attempt == 1",
        "confirmed_sha": "github.event.inputs.confirm_sha == github.sha",
        "exact_runner": "runs-on: [self-hosted, zcloud, vps]",
    }.items():
        if value not in head:
            problems.append(name)
    checkout = jobs.split("      - name: Check out exact immutable main source", 1)
    early = checkout[1].split("      - name: Refuse stale source", 1)[0] if len(checkout) == 2 else ""
    for name, value in {
        "checkout_pin": "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
        "exact_checkout": "ref: " + "$" + "{{ github.sha }}",
        "no_credentials": "persist-credentials: false",
    }.items():
        if value not in early:
            problems.append(name)
    guard_step = jobs.split("      - name: Refuse stale source", 1)
    guard = guard_step[1].split("      - name: Recover only if service is down", 1)[0] if len(guard_step) == 2 else ""
    for name, value in {
        "before_write_head": 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        "before_write_main": 'test "$(git ls-remote origin refs/heads/main',
        "before_write_hostname": 'test "$(hostname)" = "vps-bb300bba"',
        "before_write_user": 'test "$(id -un)" = "ubuntu"',
        "before_write_runner": "python3 scripts/zcloud_vps_runner_guard.py --json",
    }.items():
        if value not in guard:
            problems.append(name)
    action = jobs.split("      - name: Recover only if service is down", 1)
    body = action[1] if len(action) == 2 else ""
    write = "sudo -n systemctl start zennay-cloud.service"
    before = body.split(write, 1)[0] if write in body else ""
    for name, value in {
        "fresh_event": 'test "$GITHUB_EVENT_NAME" = workflow_dispatch',
        "fresh_repo": 'test "$GITHUB_REPOSITORY" = Zennay/zCloud',
        "fresh_actor": 'test "$GITHUB_ACTOR" = Zennay',
        "fresh_triggering_actor": 'test "$GITHUB_TRIGGERING_ACTOR" = Zennay',
        "fresh_attempt": 'test "$GITHUB_RUN_ATTEMPT" = 1',
        "fresh_main": 'test "$live_main" = "$GITHUB_SHA"',
        "fresh_runner": "python3 scripts/zcloud_vps_runner_guard.py --json >/dev/null",
        "only_if_inactive": "if ! systemctl is-active --quiet zennay-cloud.service; then",
    }.items():
        if value not in before:
            problems.append(name)
    if not before or write not in body:
        problems.append("bounded_idempotent_start")
    if re.search(r"(?m)^\s*(?:sudo\s+-n\s+)?systemctl\s+(?:restart|stop|kill)\s+zennay-cloud\.service\b", source):
        problems.append("destructive_service_operation")
    if re.search(r"(?:/api/dynamic-workers|urllib\.request\.Request\([^)]*method=['\"]POST|curl\s+[^\n]*-X\s+(?:POST|PUT|DELETE))", source):
        problems.append("global_queue_mutation")
    if 'print("HAXLAB_P1_STATUS",json.dumps({' not in body:
        problems.append("redacted_queue_summary")
    if re.search(r"(?m)^\s*print\(.+(?:json\.dumps\(item\)|json\.dumps\(queue\)|str\(item\))", body):
        problems.append("raw_queue_log")
    return problems

class HaxlabLiveRecoveryGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = TARGET.read_text(encoding="utf-8")

    def test_main_workflow_is_contract_compliant(self):
        self.assertEqual([], failures(self.source))

    def test_auto_event_rejected(self):
        for event in ("pull_request", "push", "workflow_run", "schedule"):
            with self.subTest(event=event):
                bad = self.source.replace("  workflow_dispatch:", f"  {event}:", 1)
                self.assertIn("manual_trigger_only", failures(bad))

    def test_extra_auto_event_rejected(self):
        bad = self.source.replace("  workflow_dispatch:", "  push:\n  workflow_dispatch:", 1)
        self.assertIn("manual_trigger_only", failures(bad))

    def test_no_generic_runner(self):
        self.assertIn("exact_runner", failures(self.source.replace(
            "runs-on: [self-hosted, zcloud, vps]", "runs-on: self-hosted", 1)))

    def test_owner_and_first_attempt_are_mandatory(self):
        for guard in ("github.actor == 'Zennay'", "github.triggering_actor == 'Zennay'",
                      "github.run_attempt == 1", "github.event.inputs.confirm_sha == github.sha"):
            with self.subTest(guard=guard):
                self.assertNotEqual([], failures(self.source.replace(guard, "true", 1)))

    def test_before_write_gate_mandatory(self):
        for guard in ('test "$(hostname)" = "vps-bb300bba"',
                      'test "$(id -un)" = "ubuntu"',
                      'test "$live_main" = "$GITHUB_SHA"',
                      "python3 scripts/zcloud_vps_runner_guard.py --json >/dev/null"):
            with self.subTest(guard=guard):
                self.assertNotEqual([], failures(self.source.replace(guard, "echo unchecked")))

    def test_no_restart_or_global_queue_reconciliation(self):
        self.assertIn("destructive_service_operation", failures(self.source.replace(
            "sudo -n systemctl start zennay-cloud.service",
            "sudo -n systemctl restart zennay-cloud.service", 1)))
        self.assertIn("global_queue_mutation", failures(
            self.source + '\n          curl -X POST http://127.0.0.1:8765/api/dynamic-workers'))

    def test_credentials_and_source_revision_are_immutable(self):
        self.assertIn("no_credentials", failures(self.source.replace(
            "persist-credentials: false", "persist-credentials: true", 1)))
        self.assertIn("exact_checkout", failures(self.source.replace(
            "ref: " + "$" + "{{ github.sha }}", "ref: main", 1)))

    def test_no_raw_haxlab_queue_details(self):
        bad = self.source.replace(
            'print("HAXLAB_P1_STATUS",json.dumps({',
            'print("HAXLAB_P1_STATUS",json.dumps(item))')
        self.assertIn("redacted_queue_summary", failures(bad))
        self.assertIn("raw_queue_log", failures(bad))

if __name__ == "__main__":
    unittest.main()
