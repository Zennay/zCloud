import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-since-visit-proof.yml"


class SinceVisitWorkflowTests(unittest.TestCase):
    def test_exact_head_permanent_vps_contract(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.event.pull_request.head.sha || github.sha", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "${{ github.event.pull_request.head.sha }}"', text)

    def test_workflow_is_read_only_and_bounded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("--row-limit 500", text)
        self.assertIn("test \"$BEFORE\" = \"$AFTER\"", text)
        for forbidden in ("sudo ", "systemctl ", "gh api", "curl ", "INSERT INTO", "UPDATE ", "DELETE FROM"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_live_proof_enforces_privacy_shape(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('payload["schema_version"] == "since-visit-v1"', text)
        self.assertIn('payload["project_count"] <= 50', text)
        self.assertIn('forbidden={"action","blocker","phase","reason","error","evidence_json","next_gate","prompt"}', text)
        self.assertIn("ZCLOUD_SINCE_VISIT_VPS_GREEN=1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
