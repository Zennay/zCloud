"""GitHub-hosted-only zCloud regression workflow trust and coverage matrix.

Checks immutable source admission and that the main regression executes
Worker Cockpit JS behavior, not only the Python dashboard assertions.
This test never invokes a VPS runner or production API.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-regression-smoke.yml"
CHECKOUT_PIN = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
SHA_EXPR = "github.event.pull_request.head.sha || github.sha"

CONDITIONS = (
    "github.repository == 'Zennay/zCloud'",
    "github.event_name == 'pull_request'",
    "github.event.pull_request.head.repo.full_name == github.repository",
    "github.event.pull_request.base.repo.full_name == github.repository",
    "github.event_name == 'push'",
    "github.ref == 'refs/heads/main'",
    "startsWith(github.ref, 'refs/heads/worker/')",
    "github.event_name == 'workflow_dispatch'",
    "github.actor == 'Zennay'",
    "github.triggering_actor == 'Zennay'",
    "github.run_attempt == 1",
)

def failures(source: str) -> list[str]:
    issues = []
    if source.count("\npermissions:\n") != 1 or source.count("\njobs:\n") != 1:
        return ["ambiguous_root"]
    if source.count("\non:\n") != 1:
        issues.append("event_root")
    before_jobs, jobs = source.split("\njobs:\n", 1)
    admission, critical = jobs.split("\n  critical-runner-flows:\n", 1) if "\n  critical-runner-flows:\n" in jobs else ("", "")
    guard = critical.split("    if: >-", 1)[1].split("    runs-on:", 1)[0] if "    if: >-" in critical else ""
    steps = critical.split("    steps:\n", 1)[1] if "    steps:\n" in critical else ""
    for condition in CONDITIONS:
        if condition not in guard:
            issues.append("event:" + condition)
    # Both push and manual admission have independent main-ref checks.
    main_condition = "github.ref == 'refs/heads/main'"
    if guard.count(main_condition) != 2 and "event:" + main_condition not in issues:
        issues.append("event:" + main_condition)
    if re.findall(r"(?m)^  ([a-z][a-z-]*):\s*$", jobs) != ["trusted-event", "critical-runner-flows"]:
        issues.append("two_hosted_jobs")
    if "needs: trusted-event" not in critical:
        issues.append("requires_admission")
    for boundary in (
        "test \"$SOURCE_REPO\" = Zennay/zCloud",
        "test \"$HEAD_REPO\" = \"$SOURCE_REPO\"",
        "test \"$BASE_REPO\" = \"$SOURCE_REPO\"",
        "test \"$GITHUB_BASE_REF\" = main",
        "test \"$SOURCE_ACTOR\" = Zennay",
        "test \"$RESTART_ACTOR\" = Zennay",
        "test \"$ATTEMPT\" = 1",
    ):
        if boundary not in admission:
            issues.append("admission:" + boundary)
    if "actions/checkout" in admission or "github.token" in admission:
        issues.append("untrusted_code_before_admission")
    runner_values = re.findall(r"(?m)^    runs-on:\s*(\S.*?)\s*$", jobs)
    if runner_values != ["ubuntu-latest", "ubuntu-latest"]:
        issues.append("hosted_only")
    if "cancel-in-progress: false" not in before_jobs:
        issues.append("noncancelling")
    permissions = before_jobs.split("\npermissions:\n", 1)[1].split("\nconcurrency:\n", 1)[0] if "\npermissions:\n" in before_jobs and "\nconcurrency:\n" in before_jobs else ""
    if re.findall(r"(?m)^  ([a-z-]+):\s*([a-z-]+)\s*$", permissions) != [("contents", "read")]:
        issues.append("read_only")
    if re.search(r"(?m)^    permissions:\s*$", jobs):
        issues.append("job_permission_override")
    if CHECKOUT_PIN not in steps or ("ref: " + "$" + "{{ " + SHA_EXPR + " }}") not in steps:
        issues.append("exact_checkout")
    if "persist-credentials: false" not in steps:
        issues.append("no_credentials")
    sha = ('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"')
    if sha not in steps:
        issues.append("exact_sha")
    for guard_value in (
        'test "$EXPECTED_REPOSITORY" = "Zennay/zCloud"',
        'test "$HEAD_REPOSITORY" = "$EXPECTED_REPOSITORY"',
        'test "$BASE_REPOSITORY" = "$EXPECTED_REPOSITORY"',
        'test "$main_sha" = "$EXPECTED_SHA"',
    ):
        if guard_value not in steps:
            issues.append("runtime:" + guard_value)
    for test in (
        "tests.test_deployops_regression_smoke_hosted_trust_20261014_w5",
        "python3 -m unittest discover -v",
        "node --check public/enhancements.js",
        "node --check public/app.js",
        "node tests/test_worker_debug_panel.js",
        "node tests/test_worker_tab_identity_guard.js",
        "node --check public/zcloud-worker.user.js",
    ):
        if test not in steps:
            issues.append("coverage:" + test)
    return issues

class HostedRegressionSmokeTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_current_hosted_smoke_is_accepted(self):
        self.assertEqual([], failures(self.source))

    def test_each_pr_push_and_manual_provenance_check_matters(self):
        for fragment in CONDITIONS:
            with self.subTest(fragment=fragment):
                variant = self.source.replace(fragment, "false", 1)
                self.assertIn("event:" + fragment, failures(variant))

    def test_fork_pr_fails_an_unskipped_hosted_admission_job(self):
        self.assertIn("  trusted-event:", self.source)
        self.assertIn("    needs: trusted-event", self.source)
        self.assertLess(self.source.index("  trusted-event:"),
                        self.source.index("  critical-runner-flows:"))
        for guard in (
            'test "$HEAD_REPO" = "$SOURCE_REPO"',
            'test "$BASE_REPO" = "$SOURCE_REPO"',
            'test "$GITHUB_BASE_REF" = main',
        ):
            with self.subTest(guard=guard):
                mutated = self.source.replace(guard, "echo permissive", 1)
                self.assertIn("admission:" + guard, failures(mutated))

    def test_offline_suite_runs_after_admission_and_before_project_code(self):
        self.assertLess(self.source.index("Verify exact immutable revision"),
                        self.source.index("Test exact-head regression-workflow admission"))
        self.assertLess(self.source.index("Test exact-head regression-workflow admission"),
                        self.source.index("Run critical zCloud smoke suite"))
        self.assertLess(self.source.index("Verify Worker Cockpit rendering"),
                        self.source.index("Verify Firefox crash-recovery contract"))

    def test_no_self_hosted_runner_or_privileged_tokens(self):
        bad = self.source.replace("runs-on: ubuntu-latest",
                                  "runs-on: [self-hosted, zcloud, vps]", 1)
        self.assertIn("hosted_only", failures(bad))
        bad = self.source.replace("contents: read", "contents: write", 1)
        self.assertIn("read_only", failures(bad))

    def test_no_duplicate_runner_selector_or_privileged_job_override(self):
        doubled = self.source.replace(
            "    runs-on: ubuntu-latest",
            "    runs-on: ubuntu-latest\n    runs-on: [self-hosted, zcloud, vps]", 1)
        self.assertIn("hosted_only", failures(doubled))
        override = self.source.replace(
            "    timeout-minutes: 5",
            "    permissions:\n      actions: write\n    timeout-minutes: 5", 1)
        self.assertIn("job_permission_override", failures(override))
        token = self.source.replace(
            "  contents: read",
            "  contents: read\n  actions: write", 1)
        self.assertIn("read_only", failures(token))

    def test_cancellation_of_in_progress_proofs_is_forbidden(self):
        self.assertIn("noncancelling", failures(
            self.source.replace("cancel-in-progress: false", "cancel-in-progress: true", 1)))

    def test_floating_or_unbound_checkout_is_forbidden(self):
        self.assertIn("exact_checkout", failures(
            self.source.replace(CHECKOUT_PIN, "actions/checkout@v4", 1)))
        head = "ref: " + "$" + "{{ " + SHA_EXPR + " }}"
        self.assertIn("exact_checkout", failures(self.source.replace(head, "ref: main", 1)))
        self.assertIn("no_credentials", failures(
            self.source.replace("persist-credentials: false", "persist-credentials: true", 1)))

    def test_missing_ui_js_regression_fails(self):
        for statement in ("node --check public/enhancements.js",
                          "node --check public/app.js",
                          "node tests/test_worker_debug_panel.js"):
            with self.subTest(statement=statement):
                self.assertIn("coverage:" + statement,
                              failures(self.source.replace(statement, "echo skipped", 1)))

    def test_manual_revalidation_does_not_mutate_main(self):
        self.assertIn("git ls-remote --exit-code origin refs/heads/main", self.source)
        self.assertIn('test "$main_sha" = "$EXPECTED_SHA"', self.source)
        self.assertNotIn("runs-on: self-hosted", self.source)
        self.assertNotIn("sudo -n", self.source)

if __name__ == "__main__":
    unittest.main()
