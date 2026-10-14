"""Trust and privacy contract for the live worker prompt diagnostic."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "inspect-live-worker-prompts.yml"


class InspectLiveWorkerPromptsWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_is_hosted_and_read_only(self):
        validate = self.text.split("  validate:", 1)[1].split("\n  inspect:", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", validate)
        self.assertIn("runs-on: ubuntu-latest", validate)
        self.assertNotIn("self-hosted", validate)
        for token in ("127.0.0.1:8765", "/home/ubuntu/zennay-cloud", "systemctl", "sudo -n"):
            with self.subTest(token=token):
                self.assertNotIn(token, validate)

    def test_live_diagnostic_uses_exact_permanent_runner_checkout(self):
        inspect = self.text.split("\n  inspect:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", inspect)
        self.assertIn("github.repository == 'Zennay/zCloud'", inspect)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", inspect)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", inspect)
        self.assertIn("persist-credentials: false", inspect)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', inspect)
        self.assertIn('test "$(id -un)" = "ubuntu"', inspect)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", inspect)

    def test_runner_guard_precedes_live_api_and_candidate_read(self):
        inspect = self.text.split("\n  inspect:", 1)[1]
        guard = inspect.index("python3 scripts/zcloud_vps_runner_guard.py --json")
        api = inspect.index("http://127.0.0.1:8765/api/runner-targets")
        candidate = inspect.index('candidate_path=Path("server.py")')
        self.assertLess(guard, api)
        self.assertLess(guard, candidate)

    def test_prompt_contents_are_redacted_from_logs(self):
        self.assertNotIn('"prompt": cfg.get("prompt")', self.text)
        self.assertNotIn("LIVE_PROMPTS_JSON=", self.text)
        self.assertIn('"prompt_present": bool(prompt_text)', self.text)
        self.assertIn('"prompt_length": len(prompt_text)', self.text)
        self.assertIn('"prompt_sha256": hashlib.sha256', self.text)
        self.assertIn("LIVE_PROMPT_METADATA_JSON=", self.text)

    def test_existing_drift_contract_is_preserved(self):
        for token in (
            'live_path=Path("/home/ubuntu/zennay-cloud/server.py")',
            'candidate_path=Path("server.py")',
            '"project_runner_prompt","project_worker_prompt"',
            "SERVER_REMAINDER_EQUAL",
            "live server drift is broader than the two worker prompt functions",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.text)

    def test_live_diagnostic_is_non_cancelling_and_explicitly_green(self):
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("ZCLOUD_LIVE_PROMPT_DIAGNOSTIC_GREEN=1", self.text)


if __name__ == "__main__":
    unittest.main()
