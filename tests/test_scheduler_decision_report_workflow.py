import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-scheduler-decision-report.yml"


class SchedulerDecisionReportWorkflowTests(unittest.TestCase):
    def test_validation_runs_on_permanent_self_hosted_zcloud_runner(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", text)

    def test_live_probe_is_read_only_and_checks_snapshot_hash_unchanged(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('sqlite3.connect(f"file:{sys.argv[1]}?mode=ro"', text)
        self.assertIn("source.backup(target)", text)
        self.assertIn('--db "$snapshot"', text)
        self.assertIn('before=$(sha256sum "$snapshot"', text)
        self.assertIn('after=$(sha256sum "$snapshot"', text)
        self.assertIn('test "$before" = "$after"', text)
        self.assertNotIn("--db \"$live_db\"", text)
        for forbidden in ("sudo ", "systemctl ", "sqlite3 ", "DELETE FROM", "UPDATE portfolio_queue"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
