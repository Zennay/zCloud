import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-one-worker-testing-activation.yml"


class OneWorkerTestingActivationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.validate = cls.text[
            cls.text.index("  validate:"):cls.text.index("  activate:")
        ]
        cls.activate = cls.text[cls.text.index("  activate:"):]

    def test_pr_validation_is_hosted_only_and_exact_head(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn("if: github.event_name == 'pull_request'", self.validate)
        self.assertIn("runs-on: ubuntu-latest", self.validate)
        self.assertNotIn("self-hosted", self.validate)
        self.assertIn("github.event.pull_request.head.sha", self.validate)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            self.validate,
        )
        self.assertIn("persist-credentials: false", self.validate)

    def test_live_activation_accepts_only_trusted_main_deploy_workflow_run(self):
        gate = (
            "github.event_name == 'workflow_run' "
            "&& github.event.workflow_run.conclusion == 'success' "
            "&& github.event.workflow_run.head_branch == 'main' "
            "&& github.event.workflow_run.head_repository.full_name == github.repository "
            "&& github.event.workflow_run.event == 'workflow_run'"
        )
        self.assertIn(gate, self.activate)
        self.assertNotIn("pull_request_target", self.text)

    def test_live_activation_uses_permanent_runner_and_exact_revision(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.activate)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            self.activate,
        )
        self.assertIn("ref: ${{ github.event.workflow_run.head_sha }}", self.activate)
        self.assertIn("persist-credentials: false", self.activate)
        self.assertIn('test "$(git rev-parse HEAD)" = "$expected"', self.activate)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', self.activate)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.activate)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", self.activate)

    def test_runner_guard_precedes_every_live_mutation_surface(self):
        guard = self.activate.index("scripts/zcloud_vps_runner_guard.py --json")
        firefox = self.activate.index("systemctl --user restart chatgpt-firefox.service")
        control = self.activate.index('path == "/api/runner-control"')
        self.assertLess(guard, firefox)
        self.assertLess(guard, control)

    def test_permissions_remain_read_only(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("actions: write", self.text)

    def test_existing_activation_contract_is_preserved(self):
        for marker in (
            'workflows: ["zCloud VPS deploy"]',
            "Honor dashboard-owned dynamic worker pool",
            '"action": "start"',
            '"action": "new_chat"',
            "invalid dashboard dynamic worker limit",
            "allocation exceeds dashboard limit",
            "worker start did not return command id",
            "no live Firefox heartbeat observed for current allocation",
        ):
            self.assertIn(marker, self.text)


if __name__ == "__main__":
    unittest.main()
