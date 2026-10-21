import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-self-project-contract.yml"
VPS_WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-self-project-contract-vps.yml"


class SelfProjectContractWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.vps_text = VPS_WORKFLOW.read_text(encoding="utf-8")

    def test_runs_only_on_github_hosted_runner(self):
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertNotIn("self-hosted", self.text)

    def test_checkout_is_immutable_exact_head_and_credentials_are_disabled(self):
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            self.text,
        )
        self.assertIn("github.event.pull_request.head.sha || github.sha", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.text)

    def test_permissions_are_read_only_and_no_mutating_workflow_hooks_exist(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        forbidden = (
            "workflow_run:",
            "push:",
            "schedule:",
            "repository_dispatch:",
            "sudo ",
            "systemctl ",
            "sqlite3 ",
            "curl -X POST",
        )
        for token in forbidden:
            self.assertNotIn(token, self.text)

    def test_layout_changes_trigger_contract_gate(self):
        self.assertIn("'project-layout.json'", self.text)

    def test_vps_proof_is_guarded_read_only_and_exact_head(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.vps_text)
        self.assertIn("github.actor == 'Zennay'", self.vps_text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.vps_text)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", self.vps_text)
        self.assertIn("persist-credentials: false", self.vps_text)
        self.assertIn("zcloud_vps_runner_guard.py --json", self.vps_text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.vps_text)
        self.assertIn("ZCLOUD_SELF_PROJECT_VPS_GREEN=1", self.vps_text)
        for token in ("sudo ", "systemctl ", "service ", "sqlite3 ", "curl -X POST"):
            self.assertNotIn(token, self.vps_text)

    def test_gate_runs_both_contract_test_modules_and_repo_audit(self):
        self.assertIn("tests.test_self_project_contract", self.text)
        self.assertIn("tests.test_self_project_contract_workflow", self.text)
        self.assertIn("python3 scripts/zcloud_self_project_contract.py", self.text)


if __name__ == "__main__":
    unittest.main()
