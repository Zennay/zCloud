import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-resource-allocation-report-proof.yml"


class ResourceAllocationWorkflowTests(unittest.TestCase):
    def test_workflow_is_exact_head_guarded_and_least_privilege(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn("github.triggering_actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository", text
        )
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", text
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)

    def test_live_probe_uses_read_only_report_and_checks_db_immutability(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("scripts/zcloud_resource_allocation_report.py", text)
        self.assertIn("mtime_ns", text)
        self.assertIn("ZCLOUD_RESOURCE_ALLOCATION_READONLY_GREEN", text)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "sqlite3 ",
            "git push",
            "--apply",
            "INSERT INTO",
            "UPDATE resource_leases",
            "DELETE FROM resource_leases",
            "REPLACE INTO",
            "DROP TABLE",
            "ALTER TABLE",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
