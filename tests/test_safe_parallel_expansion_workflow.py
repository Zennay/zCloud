import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-safe-parallel-expansion-proof.yml"


class SafeParallelExpansionWorkflowTests(unittest.TestCase):
    def test_exact_head_permanent_vps_gate(self):
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

    def test_gate_is_read_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        for forbidden in (
            "systemctl ",
            "sudo ",
            "sqlite3 ",
            "gh api",
            "curl ",
            "git push",
            "/home/ubuntu/zennay-cloud/history.db",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_proof_locks_safe_parallel_semantics(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('"idle_borrowable_cpu_cores": 4', text)
        self.assertIn('"safe_parallel_jobs": 2', text)
        self.assertIn('"project_parallel_cap": 4', text)
        self.assertIn('"admitted_jobs"] == 4', text)
        self.assertIn('"admitted_cpu_cores"] == 4.0', text)
        self.assertIn("ZCLOUD_SAFE_PARALLEL_VPS_GREEN=1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
