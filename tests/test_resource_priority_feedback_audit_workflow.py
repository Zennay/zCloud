import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-resource-priority-feedback-audit.yml"


class ResourcePriorityFeedbackWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_least_privilege_and_exact_head(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("actions: write", self.text)
        self.assertEqual(
            2,
            self.text.count(
                "uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
            ),
        )
        self.assertEqual(
            2,
            self.text.count(
                "ref: ${{ github.event.pull_request.head.sha || github.sha }}"
            ),
        )
        self.assertEqual(2, self.text.count("persist-credentials: false"))
        self.assertEqual(
            2,
            self.text.count(
                "python3 -m unittest -v tests.test_resource_priority_feedback_audit "
                "tests.test_resource_priority_feedback_audit_workflow"
            ),
        )

    def test_untrusted_pull_requests_never_reach_permanent_runner(self):
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn(
            "github.actor == 'Zennay' && github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", self.text)

    def test_proof_is_read_only(self):
        forbidden = (
            "sudo ",
            "systemctl ",
            "service ",
            "sqlite3 ",
            "curl ",
            "wget ",
            "gh api ",
            "git push",
            "git reset",
            "git clean",
            "/api/runner-control",
            "/api/resource-priority",
        )
        for marker in forbidden:
            self.assertNotIn(marker, self.text, marker)

    def test_timeout_and_concurrency_are_bounded(self):
        self.assertGreaterEqual(self.text.count("timeout-minutes:"), 2)
        self.assertIn("cancel-in-progress: true", self.text)
        self.assertIn(
            "group: zcloud-resource-priority-feedback-${{ github.event.pull_request.number || github.ref }}",
            self.text,
        )

    def test_test_file_changes_retrigger_the_proof(self):
        self.assertIn("- tests/test_resource_priority_feedback_audit.py", self.text)
        self.assertIn(
            "- tests/test_resource_priority_feedback_audit_workflow.py",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()
