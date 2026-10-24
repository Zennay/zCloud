import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-backpressure-advice-proof.yml"


class BackpressureAdviceWorkflowTests(unittest.TestCase):
    def test_workflow_is_exact_head_guarded_and_least_privilege(self):
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
        self.assertIn("ZCLOUD_STACKED_FULL_REGRESSION_HOST_PRESSURE_ONLY", text)
        self.assertIn("ZCLOUD_STACKED_AUTONOMY_MEMORY_NEUTRAL_GREEN", text)
        self.assertIn("test_scheduler_bootstraps_once_and_manual_pause_wins", text)
        self.assertIn("test_vps_scheduler_pushes_active_ai_worker_and_rate_limits_it", text)
        self.assertIn("mod.server.worker_memory_status = lambda", text)
        self.assertNotIn("|| true", text)

    def test_live_advice_has_no_mutation_route(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("ZCLOUD_BACKPRESSURE_ADVICE_READONLY_GREEN", text)
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
