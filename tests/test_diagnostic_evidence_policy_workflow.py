from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-diagnostic-evidence-policy.yml"


class DiagnosticEvidencePolicyWorkflowTests(unittest.TestCase):
    def test_policy_gate_is_hosted_read_only_and_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("self-hosted", text)
        self.assertNotIn("actions: write", text)
        self.assertNotIn("statuses: write", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$HEAD_SHA"', text)

    def test_pr_gate_compares_base_and_head_and_fails_on_new_debt(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("--base-ref \"$BASE_SHA\"", text)
        self.assertIn("--head-ref \"$HEAD_SHA\"", text)
        self.assertIn("--fail-on-new-ungated", text)
        self.assertIn("github.event.pull_request.base.sha", text)
        self.assertIn("github.event.pull_request.head.sha", text)

    def test_policy_scope_includes_workflow_changes(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('- ".github/workflows/**"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
