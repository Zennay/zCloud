from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-high-blast-radius-adr.yml"


class HighBlastRadiusAdrGuardWorkflowTests(unittest.TestCase):
    def test_workflow_is_hosted_read_only_and_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertIn("branches: [main]", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("self-hosted", text)
        self.assertNotIn("actions: write", text)
        self.assertNotIn("statuses: write", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("fetch-depth: 0", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$HEAD_SHA"', text)
        self.assertIn('git cat-file -e "$BASE_SHA^{commit}"', text)

    def test_workflow_enforces_policy_without_runtime_mutation(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("--require-covered", text)
        self.assertIn("--base-sha", text)
        self.assertIn("--head-sha", text)
        for forbidden in (
            "systemctl",
            "sudo ",
            "history.db",
            "sqlite3",
            "gh api",
            "curl ",
            "runner-control",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
