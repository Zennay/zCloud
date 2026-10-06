from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-vps-execution-probe.yml"


class VpsExecutionProbeTriggerTests(unittest.TestCase):
    def test_recovery_changes_trigger_pull_request_vps_probe(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        pull_request = text[text.index("  pull_request:"):text.index("\npermissions:")]
        self.assertIn("- scripts/zcloud_recovery.py", pull_request)
        self.assertIn("- tests/test_zcloud_recovery.py", pull_request)
        self.assertIn("- scripts/zcloud_safe_idle_retry_wait.py", pull_request)
        self.assertIn("- tests/test_safe_idle_retry_wait.py", pull_request)
        self.assertIn("- tests/test_safe_idle_retry_workflow.py", pull_request)
        self.assertIn("- .github/workflows/zcloud-safe-idle-retry.yml", pull_request)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
