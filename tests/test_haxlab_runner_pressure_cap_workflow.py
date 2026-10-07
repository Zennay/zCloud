import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-haxlab-runner-pressure-cap.yml"


class HaxLabRunnerPressureCapWorkflowTests(unittest.TestCase):
    def test_runtime_only_cap_matches_haxlab_contract(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("zcloud_vps_runner_guard.py --json", text)
        self.assertIn("actions.runner.Zennay-Haxlab.vps-bb300bba-haxlab.service", text)
        self.assertIn('systemctl set-property --runtime "$unit" CPUQuota=100% CPUWeight=100', text)
        self.assertNotIn("systemctl stop", text)
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("systemctl disable", text)
        self.assertNotIn("kill ", text)
        self.assertNotIn("pkill", text)

    def test_verifies_control_plane_after_live_cap(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("/api/runner-live", text)
        self.assertIn('firefox.get("active")', text)
        self.assertIn("ZCLOUD_PRESSURE_CAP_LIVE=", text)
        self.assertIn("CPUQuotaPerSecUSec", text)
        self.assertIn('test "$quota" = 1s', text)

    def test_change_is_bounded_to_debug_branch(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("debug/worker-continuity-20261006", text)
        self.assertIn("contents: read", text)
        self.assertIn("timeout-minutes: 6", text)


if __name__ == "__main__":
    unittest.main()
