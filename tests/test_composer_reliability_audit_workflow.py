from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-composer-reliability-audit.yml"


class ComposerReliabilityAuditWorkflowTests(unittest.TestCase):
    def test_workflow_is_hosted_read_only_and_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("self-hosted", text)
        self.assertNotIn("actions: write", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$HEAD_SHA"', text)

    def test_runtime_sources_are_only_inputs(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('"public/zcloud-worker.user.js"', text)
        self.assertIn('"firefox-extension/background.js"', text)
        for forbidden in ("systemctl", "sudo ", "runner-control", "curl -X", "git push"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
