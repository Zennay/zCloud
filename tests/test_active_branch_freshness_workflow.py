from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-active-branch-freshness-proof.yml"


class ActiveBranchFreshnessWorkflowTests(unittest.TestCase):
    def test_proof_uses_exact_head_and_permanent_vps(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", text)
        self.assertIn('test "$(git rev-parse HEAD)"', text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("timeout-minutes: 6", text)

    def test_live_proof_uses_read_only_github_and_sqlite_entrypoint(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("GH_TOKEN: ${{ github.token }}", text)
        self.assertIn("gh auth status --hostname github.com", text)
        self.assertIn("scripts/zcloud_active_branch_freshness.py", text)
        self.assertIn("--db /home/ubuntu/zennay-cloud/history.db", text)
        self.assertIn('"owner_id"', text)
        self.assertIn('"metadata_json"', text)

        forbidden = (
            "sudo ",
            "systemctl ",
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
            "portfolio_queue_finish",
            "--apply",
            "git push",
            "gh api --method POST",
            "gh api --method PATCH",
            "gh api --method DELETE",
        )
        for token in forbidden:
            self.assertNotIn(token, text)

    def test_focused_tests_run_before_live_read(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        focused = "python3 -m unittest -v"
        live = "python3 scripts/zcloud_active_branch_freshness.py"
        self.assertIn(focused, text)
        self.assertIn(live, text)
        self.assertLess(text.index(focused), text.index(live))


if __name__ == "__main__":
    unittest.main()
