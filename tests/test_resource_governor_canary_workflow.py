import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-resource-governor-canary.yml"


class ResourceGovernorCanaryWorkflowTests(unittest.TestCase):
    def test_canary_tracks_current_main_and_guards_self_hosted_pull_requests(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("push:\n    branches: [main]", text)
        self.assertIn("pull_request:\n    branches: [main]", text)
        self.assertNotIn("worker/cloud-resource-governor-vps-canary-20261004", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )

    def test_canary_is_bounded_to_temporary_state_on_permanent_vps_runner(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('mktemp -d "$RUNNER_TEMP/zcloud-resource-canary.', text)
        self.assertNotIn("/home/ubuntu/zennay-cloud/history.db", text)
        self.assertIn("scripts/zcloud_runtime.py", text)
        self.assertIn("scripts/zcloud_governed_exec.py", text)
        self.assertIn("trap cleanup EXIT", text)

    def test_canary_proves_heavy_contention_and_protected_capacity(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("--project ftmo", text)
        self.assertIn("--project haxlab", text)
        self.assertIn("--project cloud", text)
        self.assertIn('test "$blocked_rc" -eq 75', text)
        self.assertIn('test ! -e "$blocked_marker"', text)
        self.assertIn("ZCLOUD_PROTECTED_POOL_RESPONSIVE_GREEN", text)
        self.assertIn("curl -fsS --max-time 4 http://127.0.0.1:8765/", text)

    def test_canary_proves_contract_limits_and_no_leaked_leases(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('payload["cpus"] == 1', text)
        self.assertIn('payload["memory"] == 1536 * 1024 * 1024', text)
        self.assertIn('payload["memory"] == 3072 * 1024 * 1024', text)
        self.assertIn('payload["leases"] == []', text)
        self.assertIn('payload["pools"]["heavy"]["used"] == 0', text)
        self.assertIn('payload["pools"]["protected"]["used"] == 0', text)
        self.assertIn("ZCLOUD_RESOURCE_GOVERNOR_VPS_CANARY_GREEN", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
