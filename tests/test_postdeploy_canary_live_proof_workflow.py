import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-postdeploy-canary-live-proof.yml"


class PostdeployCanaryLiveProofWorkflowTests(unittest.TestCase):
    def text(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_pr_trigger_is_narrow_and_self_hosted_proof_is_read_only(self):
        text = self.text()
        self.assertIn("scripts/zcloud_postdeploy_canary.py", text)
        self.assertIn("tests/test_postdeploy_canary.py", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.actor == 'Zennay' &&", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("zcloud_transactional_promote.py", text)
        self.assertNotIn("zcloud_runtime.py", text)

    def test_candidate_code_checks_live_runtime_without_mutation(self):
        text = self.text()
        checkout = "ref: ${{ github.event.pull_request.head.sha || github.sha }}"
        self.assertIn(checkout, text)
        self.assertIn("python3 -m unittest -v tests.test_postdeploy_canary", text)
        self.assertIn("python3 scripts/zcloud_postdeploy_canary.py", text)
        self.assertIn("--root /home/ubuntu/zennay-cloud", text)
        self.assertIn("--db /home/ubuntu/zennay-cloud/history.db", text)
        self.assertIn("--base-url http://127.0.0.1:8765", text)
        self.assertIn("ZCLOUD_POSTDEPLOY_CANARY_LIVE_PROOF_GREEN", text)

    def test_evidence_upload_is_bounded(self):
        text = self.text()
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn("if-no-files-found: error", text)
        self.assertIn("retention-days: 14", text)
        self.assertIn("timeout-minutes: 5", text)


if __name__ == "__main__":
    unittest.main()
