"""Offline safety checks for test-only lane CI; no VPS/release authority."""
from pathlib import Path
import unittest

FILE = Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-auto-lane-generator.yml"
PIN = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
HEAD = "${{ github.event.pull_request.head.sha || github.sha }}"
REPO = "${{ github.repository }}"
PATHS = ("lane_generator.py", "server.py", "projects.json",
         "tests/test_lane_generator.py", "tests/test_portfolio_queue_backend.py",
         "tests/test_task_claims.py", "tests/test_zcloud_auto_lane_hosted_trust_20261014.py",
         ".github/workflows/zcloud-auto-lane-generator.yml")
COMMANDS = ("python3 -m unittest -v tests.test_lane_generator",
            "python3 -m unittest -v tests.test_portfolio_queue_backend",
            "python3 -m unittest -v tests.test_task_claims",
            "python3 -m unittest -v tests.test_zcloud_auto_lane_hosted_trust_20261014")


def errors(text):
    """A source check: missing proof fails closed and is never runtime proof."""
    issues = []
    try:
        header, job = text.split("\n  lane-regression:\n", 1)
        event = header.split("\npermissions:", 1)[0]
    except ValueError:
        return ["missing_job"]
    if "\npermissions:\n  contents: read\n" not in header or "contents: write" in text:
        issues.append("permissions")
    if "    runs-on: ubuntu-latest\n" not in job or "self-hosted" in text:
        issues.append("runner")
    if PIN not in job or "actions/checkout@v4" in text:
        issues.append("pin")
    if "persist-credentials: false" not in job or "persist-credentials: true" in job:
        issues.append("credentials")
    if "ref: " + HEAD not in job:
        issues.append("ref")
    gates = ("github.repository == 'Zennay/zCloud'",
             "github.event.pull_request.head.repo.full_name == 'Zennay/zCloud'",
             "github.event.pull_request.base.repo.full_name == 'Zennay/zCloud'")
    if any(g not in job.split("    runs-on:", 1)[0] for g in gates):
        issues.append("owner")
    sha_check = 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"'
    proof = (f"EXPECTED_SHA: {HEAD}", f"EXPECTED_REPOSITORY: {REPO}",
             'test "$EXPECTED_REPOSITORY" = "Zennay/zCloud"',
             '[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]', sha_check)
    if any(p not in job for p in proof):
        issues.append("sha_gate")
    gate_at = job.find(sha_check)
    code_at = job.find("python3 -m py_compile")
    if gate_at < 0 or code_at <= gate_at:
        issues.append("gate_order")
    if "  push:\n    branches: [main, 'worker/**']" not in event or \
       "  pull_request:\n    branches: [main]" not in event or \
       "  workflow_dispatch:" not in event:
        issues.append("events")
    if any(f"  {forbidden}:" in event for forbidden in ("workflow_run", "schedule", "pull_request_target")):
        issues.append("privileged_event")
    if any(event.count("      - " + name + "\n") != 2 for name in PATHS):
        issues.append("paths")
    if any(command not in job for command in COMMANDS):
        issues.append("tests")
    return issues


class LaneHostedTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FILE.read_text(encoding="utf-8")

    def test_current_workflow(self):
        self.assertEqual([], errors(self.source))

    def test_denies_untrusted_runner(self):
        self.assertIn("runner", errors(self.source.replace("runs-on: ubuntu-latest", "runs-on: self-hosted")))

    def test_denies_mutable_checkout(self):
        self.assertIn("pin", errors(self.source.replace(PIN, "actions/checkout@v4")))

    def test_denies_persisted_credentials(self):
        self.assertIn("credentials", errors(self.source.replace("persist-credentials: false", "persist-credentials: true")))

    def test_denies_branch_checkout(self):
        self.assertIn("ref", errors(self.source.replace("ref: " + HEAD, "ref: main")))

    def test_denies_foreign_pr(self):
        self.assertIn("owner", errors(self.source.replace(
            "github.event.pull_request.head.repo.full_name == 'Zennay/zCloud'", "true")))

    def test_denies_removed_exact_head(self):
        self.assertIn("sha_gate", errors(self.source.replace(
            'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', "echo bypassed")))

    def test_denies_code_before_sha_gate(self):
        gate = 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"'
        bad = self.source.replace(gate, "echo deferred")
        bad = bad.replace("run: python3 -m py_compile lane_generator.py server.py",
                          "run: |\n          python3 -m py_compile lane_generator.py server.py\n          " + gate)
        self.assertIn("gate_order", errors(bad))

    def test_denies_write_permissions(self):
        self.assertIn("permissions", errors(self.source.replace("contents: read", "contents: write")))

    def test_denies_privileged_trigger(self):
        self.assertIn("privileged_event", errors(self.source.replace(
            "\npermissions:\n", "\n  workflow_run:\n    workflows: [Any]\npermissions:\n")))

    def test_denies_missing_lane_test(self):
        self.assertIn("tests", errors(self.source.replace("python3 -m unittest -v tests.test_task_claims", "echo skipped")))

    def test_denies_missing_trigger_path(self):
        self.assertIn("paths", errors(self.source.replace(
            "      - tests/test_task_claims.py\n", "", 1)))


if __name__ == "__main__":
    unittest.main()
