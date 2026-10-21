import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-prechange-git-drift-proof.yml"


class PrechangeGitDriftWorkflowTests(unittest.TestCase):
    def text(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_proof_is_owner_same_repo_exact_head_and_read_only(self):
        text = self.text()
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn("fetch-depth: 513", text)
        self.assertIn(
            "git fetch --no-tags --depth=513 origin +refs/heads/main:refs/remotes/origin/main",
            text,
        )
        self.assertIn('test "$(git rev-parse HEAD)" = "$candidate"', text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("contents: write", text)

    def test_proof_only_reads_sanitized_prechange_evidence(self):
        text = self.text()
        self.assertIn("zcloud_vps_runner_guard.py --json", text)
        self.assertIn("python3 -m unittest -v tests.test_prechange_evidence", text)
        self.assertIn("python3 scripts/zcloud_prechange_evidence.py", text)
        self.assertIn('--candidate "$GITHUB_WORKSPACE"', text)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "DELETE FROM",
            "UPDATE ",
            "INSERT ",
            "--apply",
            "github.token",
            "secrets.",
        ):
            self.assertNotIn(forbidden, text)

    def test_trigger_is_bounded(self):
        text = self.text()
        self.assertIn("pull_request:\n    branches: [main]", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("timeout-minutes: 4", text)
        self.assertNotIn("push:\n", text)
        self.assertNotIn("schedule:", text)


if __name__ == "__main__":
    unittest.main()
