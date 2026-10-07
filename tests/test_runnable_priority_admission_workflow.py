import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-runnable-priority-admission-proof.yml"


class RunnablePriorityAdmissionWorkflowTests(unittest.TestCase):
    def test_proof_uses_exact_head_on_permanent_vps(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("resource-policy.json", text)
        self.assertIn("project-contracts.json", text)
        self.assertIn("github.event.pull_request.head.sha || github.sha", text)
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "${{ github.event.pull_request.head.sha }}"',
            text,
        )

    def test_proof_is_read_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        for forbidden in (
            "systemctl ",
            "sudo ",
            "sqlite3 ",
            "gh api",
            "curl ",
            "/home/ubuntu/zennay-cloud/history.db",
            "git push",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_proof_keeps_blocked_turbo_project_out_of_candidates(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('"project_id": "lightup", "runnable": false', text)
        self.assertIn('excluded["lightup"]["declared_priority"] == "turbo"', text)
        self.assertIn('excluded["lightup"]["priority_source"] == "resource_policy"', text)
        self.assertIn('excluded["lightup"]["priority_weight"] == 0', text)
        self.assertIn('["ftmo", "haxlab"]', text)
        self.assertIn("ZCLOUD_RUNNABLE_PRIORITY_VPS_GREEN=1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
