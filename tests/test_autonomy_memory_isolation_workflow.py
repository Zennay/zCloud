import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-autonomy-memory-isolation.yml"


class AutonomyMemoryIsolationWorkflowTests(unittest.TestCase):
    def test_workflow_is_exact_head_read_only_and_permanent_vps_bound(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn("github.triggering_actor == 'Zennay'", text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("python3 -m unittest -v tests.test_autonomy_policy", text)
        self.assertIn("ZCLOUD_AUTONOMY_MEMORY_ISOLATION_GREEN", text)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "sqlite3 ",
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
            "--apply",
            "git push",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
