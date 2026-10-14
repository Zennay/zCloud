from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-historical-queue-finalizer-state-proof.yml"


class HistoricalQueueFinalizerStateProofWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_proof_is_read_only_and_permanent_vps_scoped(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)
        self.assertIn("mode=ro", self.text)
        self.assertIn("PRAGMA query_only=ON", self.text)
        self.assertNotIn("actions/checkout@", self.text)
        self.assertNotIn("workflow_dispatch:", self.text)
        self.assertNotIn("portfolio_queue_finish", self.text)
        self.assertNotIn("UPDATE portfolio_queue", self.text)
        self.assertNotIn("INSERT INTO portfolio_queue", self.text)
        self.assertNotIn("DELETE FROM portfolio_queue", self.text)

    def test_proof_requires_same_repo_owner_pr(self) -> None:
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )
        self.assertIn("github.event.pull_request.user.login == 'Zennay'", self.text)
        self.assertIn('head_repo.get("full_name") != "Zennay/zCloud"', self.text)
        self.assertIn('user.get("login") != "Zennay"', self.text)
        self.assertIn('base.get("ref") != "main"', self.text)
        self.assertIn('head.get("sha") != sys.argv[2]', self.text)

    def test_proof_covers_only_uncertain_historical_finalizers(self) -> None:
        for queue_id in (
            "zssh-ftmo-safe-gate-3b512f56",
            "zssh-ftmo-verify72-runner-recovery-20261002",
            "zssh-ftmo-cpu-attribution-proof",
        ):
            self.assertIn(queue_id, self.text)
        self.assertIn('status == "done" and not eligible and worker_slot is None', self.text)
        self.assertIn("conn.total_changes != 0", self.text)
        self.assertIn("HISTORICAL_QUEUE_FINALIZER_STATE_GREEN=1", self.text)


if __name__ == "__main__":
    unittest.main()
