from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-main-protection-negative-canary-plan.yml"


class NegativeCanaryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_uses_permanent_vps_runner_and_guard(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)

    def test_checkout_is_immutable_and_credentials_are_not_persisted(self):
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_workflow_has_read_only_repository_permissions(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("pull-requests: write", self.text)
        self.assertNotIn("actions: write", self.text)

    def test_dedicated_proof_executes_only_offline_planner_and_tests(self):
        self.assertIn("tests.test_zcloud_main_protection_negative_canary", self.text)
        self.assertIn("tests.test_zcloud_main_protection_negative_canary_workflow", self.text)
        self.assertIn("scripts/zcloud_main_protection_negative_canary.py", self.text)
        self.assertIn("ZCLOUD_MAIN_PROTECTION_NEGATIVE_CANARY_PLAN_VPS_GREEN", self.text)
        for forbidden in (
            "gh api --method PUT",
            "gh api --method PATCH",
            "gh api --method DELETE",
            "git push",
            "update-ref",
            "delete-ref",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_cli_assertions_preserve_fail_closed_contract(self):
        self.assertIn('assert data["execution_allowed"] is False', self.text)
        self.assertIn('assert data["mutation_performed"] is False', self.text)
        self.assertIn('assert data["mutation_scope"]["forbidden_refs"] == ["refs/heads/main"]', self.text)
        self.assertIn('assert data["mutation_scope"]["allow_main_protection_mutation"] is False', self.text)
        self.assertIn('assert data["mutation_scope"]["allow_ruleset_mutation"] is False', self.text)
        self.assertIn('assert data["failure_handling"]["cleanup_on_all_outcomes"] is True', self.text)
        self.assertIn('assert data["required_postconditions"]["main_ref_unchanged"] is True', self.text)


if __name__ == "__main__":
    unittest.main()
