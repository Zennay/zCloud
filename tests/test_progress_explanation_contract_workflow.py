import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-progress-explanation-contract.yml"


class ProgressExplanationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_exact_head_read_only_checkout(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            self.text,
        )
        self.assertGreaterEqual(self.text.count("persist-credentials: false"), 2)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.text)

    def test_permanent_vps_proof_is_guarded(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.text)
        self.assertIn("zcloud_vps_runner_guard.py --json", self.text)
        self.assertIn("ZCLOUD_PROGRESS_EXPLANATION_VPS_GREEN=1", self.text)

    def test_proof_has_no_production_mutation_commands(self):
        for token in (
            "sudo ",
            "systemctl ",
            "service ",
            "sqlite3 ",
            "curl -X POST",
            "git push",
        ):
            self.assertNotIn(token, self.text)

    def test_real_progress_surfaces_trigger_gate(self):
        self.assertIn("'projects.json'", self.text)
        self.assertIn("'public/app.js'", self.text)


if __name__ == "__main__":
    unittest.main()
