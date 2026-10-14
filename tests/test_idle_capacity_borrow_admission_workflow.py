import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-idle-capacity-borrow-proof.yml"


class IdleCapacityBorrowWorkflowTests(unittest.TestCase):
    def test_proof_uses_exact_head_on_permanent_vps(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.event.pull_request.head.sha || github.sha", text)
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "${{ github.event.pull_request.head.sha }}"',
            text,
        )

    def test_proof_is_read_only_and_non_mutating(self):
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

    def test_proof_locks_reserve_and_pressure_semantics(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('"protected_reserve_cpu_cores": 1.5', text)
        self.assertIn('"pressure_state": "compute_busy"', text)
        self.assertIn('"project_id": "cloud"', text)
        self.assertIn('"protected": true', text)
        self.assertIn("ZCLOUD_IDLE_BORROW_VPS_GREEN=1", text)
        self.assertIn('"borrowed_cpu_cores"] == 2.5', text)
        self.assertIn('"remaining_idle_cpu_cores"] == 0.0', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
