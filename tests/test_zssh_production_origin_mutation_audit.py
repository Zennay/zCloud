import unittest
from pathlib import Path

from scripts.zssh_production_origin_mutation_audit import audit_workflow_text


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zssh-production-origin-readiness.yml"
AUDIT_WORKFLOW = ROOT / ".github" / "workflows" / "zssh-production-origin-mutation-audit.yml"


class ZsshProductionOriginMutationAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live_text = WORKFLOW.read_text(encoding="utf-8")

    def test_current_workflow_is_detected_as_truthfulness_gap(self):
        result = audit_workflow_text(self.live_text)
        self.assertFalse(result["truthful_public_gateway_mutation_claim"])
        self.assertTrue(result["known_mutating_reviewer_helper_invoked"])
        self.assertTrue(result["declares_no_public_gateway_mutation"])
        self.assertIn("mutating_reviewer_helper_with_false_claim", result["reason_codes"])

    def test_current_workflow_also_records_other_mutation_surfaces(self):
        result = audit_workflow_text(self.live_text)
        self.assertTrue(result["runtime_receipt_write_present"])
        self.assertTrue(result["github_status_write_present"])
        self.assertIn("runtime_receipt_write_present", result["reason_codes"])
        self.assertIn("github_status_write_present", result["reason_codes"])

    def test_truthful_true_claim_is_not_flagged(self):
        text = self.live_text.replace('"public_gateway_state_mutated": False', '"public_gateway_state_mutated": True')
        result = audit_workflow_text(text)
        self.assertTrue(result["truthful_public_gateway_mutation_claim"])
        self.assertNotIn("mutating_reviewer_helper_with_false_claim", result["reason_codes"])

    def test_non_mutating_workflow_with_false_claim_is_not_flagged(self):
        text = self.live_text.replace('bash "$source_root/deploy/prepare-reviewer-target.sh" "$source_root" > "$reviewer_report"', 'printf "{}\\n" > "$reviewer_report"')
        result = audit_workflow_text(text)
        self.assertTrue(result["truthful_public_gateway_mutation_claim"])
        self.assertFalse(result["known_mutating_reviewer_helper_invoked"])

    def test_audit_output_is_bounded_codes_not_workflow_content(self):
        result = audit_workflow_text(self.live_text)
        rendered = repr(result)
        self.assertNotIn("zssh.cheapgpt.shop", rendered)
        self.assertNotIn("reviewer-agent-public.json", rendered)
        self.assertNotIn("198.244.191.182", rendered)

    def test_hosted_audit_workflow_is_read_only_exact_head(self):
        text = AUDIT_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("statuses: write", text)
        self.assertNotIn("actions: write", text)
        self.assertIn("EXPECTED_SHA: ${{ github.event.pull_request.head.sha }}", text)
        self.assertIn("uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)
        self.assertIn("scripts/zssh_production_origin_mutation_audit.py", text)
        self.assertNotIn("--require-truthful", text)


if __name__ == "__main__":
    unittest.main()
