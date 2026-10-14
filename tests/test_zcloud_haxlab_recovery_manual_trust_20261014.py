"""Offline no-resurrection regression for legacy live HaxLab recovery (#633).

This static guard does not grant production admission. It only prevents an
untrusted event, source revision or runner from silently regaining a write path.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-haxlab-allocation-recovery.yml"
CHECKOUT = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
SHA_EXPR = "${{ github.sha }}"
SHA_INPUT = "${{ inputs.confirm_sha }}"


def trust_issues(source):
    issues = []
    try:
        header, job = source.split("\n  recover:\n", 1)
        events = header.split("\npermissions:", 1)[0]
    except ValueError:
        return ["shape"]
    if "  workflow_dispatch:\n" not in events or "      confirm_sha:\n" not in events:
        issues.append("manual_confirm")
    if any("\n  " + name + ":" in events for name in
           ("push", "pull_request", "pull_request_target", "workflow_run", "schedule", "repository_dispatch")):
        issues.append("automatic_mutation")
    if "\npermissions:\n  contents: read\n" not in header:
        issues.append("least_privilege")
    admission = job.split("    runs-on:", 1)[0]
    required = (
        "github.event_name == 'workflow_dispatch'",
        "github.repository == 'Zennay/zCloud'",
        "github.ref == 'refs/heads/main'",
        "github.actor == 'Zennay'",
        "github.triggering_actor == 'Zennay'",
        "github.run_attempt == 1",
        "github.event.inputs.confirm_sha == github.sha",
    )
    if any(x not in admission for x in required):
        issues.append("owner_admission")
    if "    runs-on: [self-hosted, zcloud, vps]" not in job:
        issues.append("permanent_runner")
    if CHECKOUT not in job or "actions/checkout@v4" in job:
        issues.append("immutable_checkout")
    if "ref: " + SHA_EXPR not in job:
        issues.append("checkout_ref")
    if "persist-credentials: false" not in job or "persist-credentials: true" in job:
        issues.append("persisted_credentials")
    required_preflight = (
        "EXPECTED_SHA: " + SHA_EXPR,
        "CONFIRMED_SHA: " + SHA_INPUT,
        'test "$EXPECTED_REPO" = "Zennay/zCloud"',
        '[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]',
        'test "$CONFIRMED_SHA" = "$EXPECTED_SHA"',
        'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        'test "$(git ls-remote origin refs/heads/main | awk \'{print $1}\')" = "$EXPECTED_SHA"',
        'test "$(hostname)" = "vps-bb300bba"',
        'test "$(id -un)" = "ubuntu"',
        "python3 scripts/zcloud_vps_runner_guard.py --json",
    )
    if any(x not in job for x in required_preflight):
        issues.append("fresh_main_gate")
    preflight = job.find("python3 scripts/zcloud_vps_runner_guard.py --json")
    write_start = job.find("sudo -n systemctl restart zennay-cloud.service")
    if preflight < 0 or write_start <= preflight:
        issues.append("preflight_order")
    if 'print("HAXLAB_P1_LIVE"' in job or 'print("ALLOCATION_LIVE"' in job:
        issues.append("raw_queue_leak")
    if 'print("HAXLAB_P1_STATUS"' not in job:
        issues.append("sanitized_queue_status")
    if 'print("POOL_RECONCILED",json.dumps(reconcile' in job or 'print("POOL_RECONCILE_ACK"' not in job:
        issues.append("raw_reconcile_payload")
    if 'reconcile.get("error")' not in job or 'reconcile.get("ok") is False' not in job:
        issues.append("reconcile_failure_guard")
    if 'post("/api/dynamic-workers",same)' not in job:
        issues.append("existing_functionality")
    return issues


class LegacyHaxLabRecoveryTrust(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_current_source_is_manual_owner_bound_and_redacted(self):
        self.assertEqual([], trust_issues(self.source))

    def test_automatic_push_resurrection_is_denied(self):
        old = self.source.replace("  workflow_dispatch:\n", "  push:\n    branches: [main]\n  workflow_dispatch:\n")
        self.assertIn("automatic_mutation", trust_issues(old))

    def test_pr_resurrection_is_denied(self):
        old = self.source.replace("  workflow_dispatch:\n", "  pull_request:\n  workflow_dispatch:\n")
        self.assertIn("automatic_mutation", trust_issues(old))

    def test_dispatch_requires_confirmed_sha(self):
        old = self.source.replace("      confirm_sha:\n", "      dry_run:\n")
        self.assertIn("manual_confirm", trust_issues(old))

    def test_wrong_actor_or_retry_denied(self):
        for target in ("github.actor == 'Zennay'", "github.triggering_actor == 'Zennay'",
                       "github.run_attempt == 1"):
            with self.subTest(target=target):
                self.assertIn("owner_admission", trust_issues(self.source.replace(target, "true")))

    def test_cross_repo_and_old_ref_denied(self):
        for target in ("github.repository == 'Zennay/zCloud'", "github.ref == 'refs/heads/main'",
                       "github.event.inputs.confirm_sha == github.sha"):
            with self.subTest(target=target):
                self.assertIn("owner_admission", trust_issues(self.source.replace(target, "true")))

    def test_unpinned_checkout_or_branch_ref_denied(self):
        self.assertIn("immutable_checkout", trust_issues(self.source.replace(CHECKOUT, "actions/checkout@v4")))
        self.assertIn("checkout_ref", trust_issues(self.source.replace("ref: " + SHA_EXPR, "ref: main")))

    def test_persisted_credentials_denied(self):
        self.assertIn("persisted_credentials", trust_issues(
            self.source.replace("persist-credentials: false", "persist-credentials: true")))

    def test_untrusted_runner_denied(self):
        self.assertIn("permanent_runner", trust_issues(
            self.source.replace("runs-on: [self-hosted, zcloud, vps]", "runs-on: self-hosted")))

    def test_stale_main_or_missing_host_verification_denied(self):
        for target in ('test "$(git ls-remote origin refs/heads/main | awk \'{print $1}\')" = "$EXPECTED_SHA"',
                       'test "$(hostname)" = "vps-bb300bba"',
                       "python3 scripts/zcloud_vps_runner_guard.py --json"):
            with self.subTest(target=target):
                self.assertIn("fresh_main_gate", trust_issues(self.source.replace(target, "echo trust_me")))

    def test_raw_queue_telemetry_is_denied(self):
        self.assertIn("raw_queue_leak", trust_issues(
            self.source.replace('print("HAXLAB_P1_STATUS"', 'print("ALLOCATION_LIVE"')))

    def test_full_reconcile_json_leak_denied(self):
        raw = self.source.replace('print("POOL_RECONCILE_ACK"',
                                  'print("POOL_RECONCILED",json.dumps(reconcile,sort_keys=True)); print("POOL_RECONCILE_ACK"')
        self.assertIn("raw_reconcile_payload", trust_issues(raw))

    def test_error_reconcile_must_abort(self):
        bad = self.source.replace('reconcile.get("error")', "False")
        self.assertIn("reconcile_failure_guard", trust_issues(bad))

    def test_existing_queue_path_preserved(self):
        self.assertIn("post(\"/api/dynamic-workers\",same)", self.source)


if __name__ == "__main__":
    unittest.main()
