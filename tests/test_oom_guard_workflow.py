import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/zcloud-oom-guard-vps-validation.yml")


class OomGuardWorkflowTests(unittest.TestCase):
    def test_uses_permanent_vps_runner_and_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("ref: ${{ env.EXPECTED_SHA }}", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)

    def test_runner_guard_precedes_live_memory_probe(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        guard = "python3 scripts/zcloud_vps_runner_guard.py --json"
        probe = "Probe live VPS memory and swap without changing runtime"
        self.assertIn(guard, text)
        self.assertIn(probe, text)
        self.assertLess(text.index(guard), text.index(probe))

    def test_workflow_remains_read_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("continue-on-error", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("systemctl ", text)
        self.assertNotIn("docker ", text)
        self.assertNotIn("zcloud-vps-deploy", text)


if __name__ == "__main__":
    unittest.main()
