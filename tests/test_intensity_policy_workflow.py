import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-intensity-policy-proof.yml"


class IntensityPolicyWorkflowTests(unittest.TestCase):
    def test_proof_runs_on_permanent_vps_with_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.event.pull_request.head.sha || github.sha", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "${{ github.event.pull_request.head.sha }}"', text)

    def test_proof_is_read_only_and_bounded_to_checked_out_contract(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("/home/ubuntu/zennay-cloud/history.db", text)
        self.assertNotIn("systemctl ", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("sqlite3 ", text)
        self.assertNotIn("gh api", text)
        self.assertNotIn("curl ", text)

    def test_proof_checks_protected_heavy_and_disabled_examples(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("--project cloud --intensity 100", text)
        self.assertIn("--project ftmo --intensity 75", text)
        self.assertIn("--project ulab --intensity 100", text)
        self.assertIn('cloud["scheduler"]["burst_eligible"] is False', text)
        self.assertIn('ulab["scheduler"]["cpu_target_cores"] == 0.0', text)
        self.assertIn("ZCLOUD_INTENSITY_POLICY_VPS_GREEN=1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
