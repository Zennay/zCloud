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
    # Duplicate YAML roots can shadow earlier safe event and permission mappings.
    for key in ("on", "permissions", "jobs"):
        if len(re.findall(rf"(?m)^{key}:\s*$", source)) != 1:
            problems.append("single_root_" + key)
    if "\non:\n" not in source or "\npermissions:\n" not in source:
        return ["missing_top_level_boundary"]
    triggers = source.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    if re.findall(r"(?m)^  ([A-Za-z_][A-Za-z_0-9-]*):", triggers) != ["workflow_dispatch"]:
        problems.append("manual_trigger_only")
    if "confirm_sha:" not in triggers or "required: true" not in triggers:
        problems.append("explicit_sha_input")
    if not re.search(r"(?m)^permissions:\n  contents: read\s*$", source):
        problems.append("read_only_token")
    policy = source.split("\npermissions:\n", 1)[1].split("\njobs:\n", 1)[0] if "\njobs:\n" in source else ""
    if re.findall(r"(?m)^  ([A-Za-z_-]+):\s*(\S+)\s*$", policy) != [("contents", "read")]:
        problems.append("exact_least_privilege")
    jobs = source.split("\njobs:\n", 1)[1] if "\njobs:\n" in source else ""
    if re.findall(r"(?m)^  ([a-z][a-z0-9-]*):\s*$", jobs) != ["recover"]:
        problems.append("single_privileged_job")
    if re.search(r"(?m)^    permissions:\s*$", jobs):
        problems.append("job_permission_override")
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
    # Ignore comment-only threat descriptions, never actual shell/Python calls.
    executable = "\n".join(line for line in source.splitlines()
                           if not line.lstrip().startswith("#"))
    if re.search(r"(?:/api/dynamic-workers|urllib\.request\.Request\([^)]*method=['\"]POST|curl\s+[^\n]*-X\s+(?:POST|PUT|DELETE))", executable):
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

    def test_ambiguous_yaml_roots_and_privilege_override_denied(self):
        for key in ("on", "permissions", "jobs"):
            with self.subTest(root=key):
                modified = self.source + f"\n{key}:\n  push:\n"
                self.assertIn("single_root_" + key, failures(modified))
        self.assertIn("exact_least_privilege", failures(
            self.source.replace("  contents: read", "  contents: write", 1)))
        self.assertIn("exact_least_privilege", failures(
            self.source.replace("  contents: read", "  actions: write\n  contents: read", 1)))
        self.assertIn("job_permission_override", failures(
            self.source.replace("    timeout-minutes: 8",
                                "    permissions:\n      actions: write\n    timeout-minutes: 8", 1)))

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

HOSTED_GUARD = ROOT / ".github/workflows/zcloud-haxlab-recovery-hosted-guard-20261014-w4.yml"

def hosted_failures(source: str) -> list[str]:
    issues = []
    triggers = source.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0] if "\non:\n" in source else ""
    for expected in ("  pull_request:", "  push:", "    branches: [main]",
                     "zcloud-haxlab-allocation-recovery.yml",
                     "tests/test_deployops_haxlab_live_recovery_guard_20261014_w4.py"):
        if expected not in triggers:
            issues.append("missing_trigger:" + expected)
    if "workflow_dispatch:" in triggers or "workflow_run:" in triggers:
        issues.append("unexpected_privileged_trigger")
    job = source.split("\njobs:\n", 1)[1] if "\njobs:\n" in source else ""
    for label, token in {
        "job_canonical": "github.repository == 'Zennay/zCloud'",
        "job_exact_same_repo": "github.event.pull_request.head.repo.full_name == github.repository",
        "job_single_attempt": "github.run_attempt == 1",
        "hosted_only": "runs-on: ubuntu-latest",
        "pinned_checkout": "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
        "immutable_revision": "ref: " + "$" + "{{ github.event.pull_request.head.sha || github.sha }}",
        "drop_credentials": "persist-credentials: false",
        "source_sha": 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        "test_invoked": "python3 -m unittest -v tests.test_deployops_haxlab_live_recovery_guard_20261014_w4",
    }.items():
        if token not in job:
            issues.append(label)
    if not re.search(r"(?m)^permissions:\n  contents: read\s*$", source):
        issues.append("token_read_only")
    if re.search(r"(?m)^\s*-\s*name:.*(?:deploy|restart|install)\s*$", job, re.I):
        issues.append("privileged_job")
    if re.search(r"(?m)^\s*(?:sudo|systemctl|curl -X POST)\b", job):
        issues.append("unexpected_runtime_mutation")
    return issues

class HaxlabHostedVerificationIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = HOSTED_GUARD.read_text(encoding="utf-8")

    def test_hosted_guard_has_no_privileged_actions(self):
        self.assertEqual([], hosted_failures(self.workflow))

    def test_extra_privileged_dispatch_rejected(self):
        s = self.workflow.replace("  pull_request:", "  workflow_dispatch:\n  pull_request:", 1)
        self.assertIn("unexpected_privileged_trigger", hosted_failures(s))

    def test_self_hosted_runner_rejected(self):
        s = self.workflow.replace("runs-on: ubuntu-latest", "runs-on: [self-hosted, zcloud, vps]", 1)
        self.assertIn("hosted_only", hosted_failures(s))

    def test_credential_retention_and_unpinned_checkout_rejected(self):
        self.assertIn("drop_credentials", hosted_failures(self.workflow.replace(
            "persist-credentials: false", "persist-credentials: true", 1)))
        self.assertIn("pinned_checkout", hosted_failures(self.workflow.replace(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            "actions/checkout@v4", 1)))

    def test_explicit_mutation_command_rejected(self):
        self.assertIn("unexpected_runtime_mutation", hosted_failures(
            self.workflow + "\n          sudo -n systemctl restart zennay-cloud.service\n"))

if __name__ == "__main__":
    unittest.main()
