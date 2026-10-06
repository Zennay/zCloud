import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/inspect-live-worker-prompts.yml")


class LivePromptDiagnosticWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pull_request_validation_is_hosted_only(self):
        self.assertIn("validate:\n    if: github.event_name == 'pull_request'", self.text)
        self.assertIn("inspect:\n    if: github.event_name != 'pull_request'", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)

    def test_live_inspection_uses_exact_permanent_runner(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            self.text,
        )
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)

    def test_prompt_contents_are_not_logged(self):
        self.assertNotIn('"prompt": cfg.get("prompt")', self.text)
        self.assertNotIn("LIVE_PROMPTS_JSON=", self.text)
        self.assertIn('"prompt_length": len(prompt)', self.text)
        self.assertIn('"prompt_sha256": hashlib.sha256(', self.text)
        self.assertIn("LIVE_PROMPT_METADATA_JSON=", self.text)

    def test_diagnostic_remains_read_only(self):
        self.assertNotIn("systemctl restart", self.text)
        self.assertNotIn("systemctl stop", self.text)
        self.assertNotIn("systemctl start", self.text)
        self.assertNotIn("sudo ", self.text)


if __name__ == "__main__":
    unittest.main()
