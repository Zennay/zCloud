from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-project-bottleneck-proof.yml"


class ProjectBottleneckWorkflowTests(unittest.TestCase):
    def test_proof_is_exact_head_and_permanent_vps_only(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn('test "$(git rev-parse HEAD)"', text)
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("timeout-minutes: 6", text)

    def test_focused_tests_run_before_live_read(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        focused = "python3 -m unittest"
        live = "python3 scripts/zcloud_project_bottleneck_report.py"
        self.assertIn(focused, text)
        self.assertIn(live, text)
        self.assertLess(text.index(focused), text.index(live))
        self.assertIn("--project cloud", text)
        self.assertIn("runtime_root=/home/ubuntu/zennay-cloud", text)
        self.assertIn('$runtime_root/history.db', text)
        self.assertIn('$runtime_root/projects.json', text)

    def test_proof_has_no_runtime_mutation_route(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        forbidden = (
            "sudo ",
            "systemctl ",
            "INSERT INTO",
            "UPDATE portfolio_queue",
            "DELETE FROM",
            "portfolio_queue_finish",
            "--apply",
            "git push",
            "curl -X POST",
            "curl --request POST",
        )
        for token in forbidden:
            self.assertNotIn(token, text)

        self.assertIn('"blocker_text"', text)
        self.assertIn('"completion_criteria"', text)
        self.assertIn('"title"', text)


if __name__ == "__main__":
    unittest.main()
