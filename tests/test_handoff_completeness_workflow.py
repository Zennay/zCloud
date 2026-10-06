import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-handoff-completeness-proof.yml"


class HandoffCompletenessWorkflowTests(unittest.TestCase):
    def test_hosted_validation_then_exact_head_vps_proof(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("permanent-vps-proof:\n    needs: validate", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertGreaterEqual(text.count("persist-credentials: false"), 2)
        self.assertGreaterEqual(text.count("github.event.pull_request.head.sha || github.sha"), 2)

    def test_workflow_has_read_only_permissions_and_no_mutation_commands(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "gh api",
            "curl ",
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
            "--require-ready",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_live_proof_exposes_only_bounded_shape(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('payload["privacy_contract"] == "reason-codes-only"', text)
        self.assertIn('allowed={"project_id","state","receipt_id","observed_at","missing"}', text)
        self.assertIn("ZCLOUD_HANDOFF_COMPLETENESS_VPS_GREEN=1", text)
        self.assertIn("ZCLOUD_HANDOFF_COMPLETENESS_READY=", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
