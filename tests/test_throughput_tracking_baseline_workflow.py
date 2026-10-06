from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-throughput-baseline-proof.yml"


class ThroughputTrackingBaselineWorkflowTests(unittest.TestCase):
    def test_workflow_is_exact_head_permanent_vps_dry_run_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("github.event.pull_request.head.sha || github.sha", text)
        self.assertIn("ZCLOUD_THROUGHPUT_BASELINE_DRY_RUN_GREEN", text)
        self.assertIn("before_fingerprint=", text)
        self.assertIn("after_fingerprint=", text)
        self.assertIn('--started-at "now"', text)
        self.assertIn('receipt=payload["production_receipt"]', text)
        self.assertIn('"workflow_run_id"] == "37448947435"', text)
        self.assertIn(
            "group: zcloud-throughput-baseline-${{ github.event.pull_request.head.ref || github.ref_name }}",
            text,
        )
        self.assertIn("cancel-in-progress: true", text)

    def test_workflow_cannot_apply_baseline(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertNotIn("--apply", text)
        self.assertNotIn("INITIALIZE_THROUGHPUT_TRACKING_BASELINES", text)
        self.assertNotIn("INSERT INTO", text)
        self.assertNotIn("UPDATE ", text)
        self.assertNotIn("DELETE FROM", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("systemctl ", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
