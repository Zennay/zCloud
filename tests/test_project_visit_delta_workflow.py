import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-project-visit-delta.yml"


class ProjectVisitDeltaWorkflowTests(unittest.TestCase):
    def test_permanent_vps_and_checkout_contract(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", text)

    def test_guard_precedes_live_database_read(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        guard = text.index("python3 scripts/zcloud_vps_runner_guard.py --json")
        live_db = text.index("DB=/home/ubuntu/zennay-cloud/history.db")
        delta = text.index("python3 scripts/zcloud_project_visit_delta.py")
        self.assertLess(guard, live_db)
        self.assertLess(live_db, delta)

    def test_live_proof_is_read_only_and_bounded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("test ! -L \"$DB\"", text)
        self.assertIn("--project cloud --since \"$SINCE\" --limit 50", text)
        self.assertIn("ZCLOUD_PROJECT_VISIT_DELTA_GREEN", text)
        for mutation in (
            "INSERT INTO project_state_receipts",
            "UPDATE project_state_receipts",
            "DELETE FROM project_state_receipts",
        ):
            self.assertNotIn(mutation, text)


if __name__ == "__main__":
    unittest.main()
