import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/deploy-zbrowse-bridge.yml")


class ZBrowseBridgeDeployWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pull_requests_validate_without_live_deploy(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn("validate:\n    if: github.event_name == 'pull_request'", self.text)
        self.assertIn("deploy:\n    if: github.event_name != 'pull_request'", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_live_deploy_is_exact_head_and_permanent_runner_guarded(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            self.text,
        )
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)

    def test_zguard_source_is_resolved_once_and_deployed_by_sha(self):
        self.assertIn(
            'git ls-remote "$ZGUARD_REMOTE" refs/heads/main',
            self.text,
        )
        self.assertIn('git -C /opt/zbrowse-source fetch --depth=1 origin "$ZGUARD_SHA"', self.text)
        self.assertIn('git -C /opt/zbrowse-source reset --hard "$ZGUARD_SHA"', self.text)
        self.assertIn(
            'test "$(git -C /opt/zbrowse-source rev-parse HEAD)" = "$ZGUARD_SHA"',
            self.text,
        )
        self.assertNotIn("git reset --hard origin/main", self.text)
        self.assertNotIn("git clone https://github.com/Zennay/zGuard.git", self.text)

    def test_permissions_remain_read_only(self):
        self.assertIn("permissions:\n  contents: read", self.text)


if __name__ == "__main__":
    unittest.main()
