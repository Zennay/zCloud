import unittest
from pathlib import Path

from scripts.zssh_main_protection_attestation_admission import (
    CANONICAL_ACTOR,
    CANONICAL_MAIN_REF,
    CANONICAL_REPOSITORY,
    evaluate_attestation_admission,
)


ROOT = Path(__file__).resolve().parents[1]
LIVE_WORKFLOW = ROOT / ".github" / "workflows" / "zssh-attest-main-protection.yml"
PROOF_WORKFLOW = ROOT / ".github" / "workflows" / "zssh-main-protection-attestation-admission.yml"


class ZsshMainProtectionAttestationAdmissionTests(unittest.TestCase):
    def test_canonical_main_push_is_live_allowed(self):
        result = evaluate_attestation_admission(
            event_name="push",
            repository=CANONICAL_REPOSITORY,
            ref=CANONICAL_MAIN_REF,
            actor=CANONICAL_ACTOR,
        )
        self.assertEqual(result["mode"], "LIVE_ALLOWED")
        self.assertIs(result["live_mutation_allowed"], True)
        self.assertEqual(result["reason"], "trusted_main_event")

    def test_canonical_main_dispatch_is_live_allowed(self):
        result = evaluate_attestation_admission(
            event_name="workflow_dispatch",
            repository=CANONICAL_REPOSITORY,
            ref=CANONICAL_MAIN_REF,
            actor=CANONICAL_ACTOR,
        )
        self.assertEqual(result["mode"], "LIVE_ALLOWED")
        self.assertIs(result["live_mutation_allowed"], True)

    def test_non_main_or_wrong_actor_live_context_is_rejected(self):
        wrong_ref = evaluate_attestation_admission(
            event_name="workflow_dispatch",
            repository=CANONICAL_REPOSITORY,
            ref="refs/heads/feature/not-main",
            actor=CANONICAL_ACTOR,
        )
        wrong_actor = evaluate_attestation_admission(
            event_name="push",
            repository=CANONICAL_REPOSITORY,
            ref=CANONICAL_MAIN_REF,
            actor="someone-else",
        )
        self.assertEqual(wrong_ref["mode"], "REJECTED")
        self.assertEqual(wrong_actor["mode"], "REJECTED")
        self.assertIs(wrong_ref["live_mutation_allowed"], False)
        self.assertIs(wrong_actor["live_mutation_allowed"], False)

    def test_same_repo_owner_pr_is_validate_only(self):
        result = evaluate_attestation_admission(
            event_name="pull_request",
            repository=CANONICAL_REPOSITORY,
            ref="refs/pull/999/merge",
            actor=CANONICAL_ACTOR,
            pull_request_head_repository=CANONICAL_REPOSITORY,
        )
        self.assertEqual(result["mode"], "VALIDATE_ONLY")
        self.assertIs(result["live_mutation_allowed"], False)
        self.assertEqual(result["reason"], "trusted_same_repo_pr")

    def test_fork_or_untrusted_pr_is_rejected_without_echo(self):
        result = evaluate_attestation_admission(
            event_name="pull_request",
            repository=CANONICAL_REPOSITORY,
            ref="refs/pull/999/merge",
            actor="someone-else",
            pull_request_head_repository="fork-owner/fork-repo",
        )
        self.assertEqual(result["mode"], "REJECTED")
        self.assertIs(result["live_mutation_allowed"], False)
        rendered = repr(result)
        self.assertNotIn("someone-else", rendered)
        self.assertNotIn("fork-owner", rendered)

    def test_malformed_inputs_fail_closed(self):
        with self.assertRaises(TypeError):
            evaluate_attestation_admission(
                event_name=True,
                repository=CANONICAL_REPOSITORY,
                ref=CANONICAL_MAIN_REF,
                actor=CANONICAL_ACTOR,
            )
        with self.assertRaises(ValueError):
            evaluate_attestation_admission(
                event_name="push",
                repository=CANONICAL_REPOSITORY,
                ref=CANONICAL_MAIN_REF,
                actor="x" * 257,
            )

    def test_existing_live_workflow_preserves_current_mutation_safety_baseline(self):
        text = LIVE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("cancel-in-progress: false", text)
        guard = text.index("python3 scripts/zcloud_vps_runner_guard.py --json")
        first_gh_api = text.index("gh api")
        self.assertLess(guard, first_gh_api)
        self.assertIn("ZSSH_MAIN_PROTECTION_VERIFIED", text)

    def test_hosted_proof_is_read_only_and_exact_head(self):
        text = PROOF_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("actions: write", text)
        self.assertNotIn("contents: write", text)
        self.assertIn("EXPECTED_SHA: ${{ github.event.pull_request.head.sha }}", text)
        self.assertIn("uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)


if __name__ == "__main__":
    unittest.main()
