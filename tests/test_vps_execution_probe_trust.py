import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/zcloud-vps-execution-probe.yml")


class VpsExecutionProbeTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pull_request_self_hosted_probe_is_owner_same_repo_guarded(self):
        self.assertIn("github.actor == 'Zennay'", self.text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.text,
        )
        self.assertIn(
            "EXPECTED_ZCLOUD_SHA: ${{ github.event.pull_request.head.sha || github.event.workflow_run.head_sha || github.sha }}",
            self.text,
        )

    def test_all_self_hosted_checkout_paths_verify_exact_revision(self):
        self.assertIn("ref: ${{ github.sha }}", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', self.text)
        self.assertIn("ref: ${{ env.EXPECTED_ZCLOUD_SHA }}", self.text)
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"',
            self.text,
        )
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)

    def test_runner_topology_does_not_emit_full_command_lines(self):
        self.assertIn("=== RUNNER PROCESS NAMES ===", self.text)
        self.assertIn("lstart=,comm=", self.text)
        self.assertNotIn("lstart=,cmd=", self.text)

    def test_evidence_upload_action_is_immutable(self):
        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1",
            self.text,
        )
        self.assertNotIn("actions/upload-artifact@v4", self.text)


if __name__ == "__main__":
    unittest.main()
