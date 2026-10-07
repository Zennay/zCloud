import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-retry-policy-proof.yml"


class RetryPolicyWorkflowTests(unittest.TestCase):
    def test_proof_is_exact_head_guarded_and_read_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('Path("/home/ubuntu/zennay-cloud/history.db")', text)
        self.assertIn("st_mtime_ns", text)
        self.assertIn("ZCLOUD_RETRY_POLICY_READONLY_GREEN=1", text)

        for forbidden in (
            "INSERT INTO ",
            "UPDATE portfolio_queue",
            "DELETE FROM ",
            "init_db(",
            "sudo ",
            "systemctl restart",
            "systemctl stop",
        ):
            self.assertNotIn(forbidden, text)

    def test_live_proof_reports_writer_gap_without_enforcing_it_yet(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("--require-writer", text)
        self.assertIn('"writer_retry_contract_complete"', text)
        self.assertIn('"active_rows_at_or_over_retry_budget"', text)


if __name__ == "__main__":
    unittest.main()
