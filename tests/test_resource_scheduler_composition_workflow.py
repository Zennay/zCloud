from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-resource-scheduler-composition-proof.yml"


class ResourceSchedulerCompositionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_read_only_and_exact_checkout(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("github.event.pull_request.head.sha || github.sha", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$expected"', self.text)

    def test_hosted_validation_gates_self_hosted_proof(self):
        self.assertIn("  validate:\n    runs-on: ubuntu-latest", self.text)
        self.assertIn("  prove:\n    needs: validate", self.text)
        hosted = self.text.index("  validate:")
        proof = self.text.index("  prove:")
        self.assertLess(hosted, proof)
        self.assertIn("tests.test_resource_scheduler_composition_cli", self.text)

    def test_pull_request_self_hosted_execution_is_owner_and_same_repo_guarded(self):
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)

    def test_proof_is_non_mutating(self):
        forbidden = (
            "git push",
            "systemctl ",
            "sqlite3 ",
            "/api/runner/",
            "/api/resource-priority",
            "curl -X POST",
            "curl --request POST",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.text)
        self.assertIn('"runtime_mutation":false', self.text)
        self.assertIn('"mutation_performed":false', self.text)


if __name__ == "__main__":
    unittest.main()
