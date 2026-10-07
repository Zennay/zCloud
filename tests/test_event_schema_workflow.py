from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-event-schema-contract.yml"
PINNED_CHECKOUT = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"


class EventSchemaWorkflowSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_permanent_runner_and_least_privilege(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn("contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("actions: write", self.text)
        self.assertNotIn("pull-requests: write", self.text)
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )

    def test_checkout_is_exact_head_pinned_and_non_persistent(self):
        self.assertIn(PINNED_CHECKOUT, self.text)
        self.assertNotIn("actions/checkout@v", self.text)
        self.assertIn("github.event.pull_request.head.sha || github.sha", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_live_validation_is_guarded_bounded_and_read_only(self):
        self.assertIn("timeout-minutes: 6", self.text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", self.text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', self.text)
        self.assertIn("zcloud_event_schema_validate.py", self.text)
        self.assertIn("connection_total_changes", self.text)
        self.assertIn("query_only", self.text)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
            "REPLACE INTO",
            "DROP TABLE",
            "ALTER TABLE",
        ):
            self.assertNotIn(forbidden, self.text)


if __name__ == "__main__":
    unittest.main()
