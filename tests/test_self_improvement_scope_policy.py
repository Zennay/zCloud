import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_self_improvement_scope_policy.py"
SPEC = importlib.util.spec_from_file_location("scope_policy", SCRIPT)
policy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(policy)


def proposal(kind="backlog_idea", **overrides):
    value = {
        "schema_version": 1,
        "proposal_id": "cloud-idea-001",
        "kind": kind,
        "summary": "Add bounded evidence for a newly observed reliability improvement.",
        "evidence_refs": ["gh:issue/701", "metric:worker_recovery"],
    }
    value.update(overrides)
    return value


class SelfImprovementScopePolicyTests(unittest.TestCase):
    def test_backlog_idea_is_auto_admissible(self):
        result = policy.evaluate(proposal())
        self.assertEqual(result["decision"], "BACKLOG_APPEND_ALLOWED")
        self.assertEqual(result["reason"], "bounded_backlog_idea")
        self.assertEqual(result["evidence_ref_count"], 2)

    def test_mission_and_safety_changes_require_human_approval(self):
        for kind, reason in (
            ("mission_change", "mission_change_requires_human"),
            ("safety_change", "safety_change_requires_human"),
        ):
            with self.subTest(kind=kind):
                result = policy.evaluate(proposal(kind))
                self.assertEqual(result["decision"], "HUMAN_APPROVAL_REQUIRED")
                self.assertEqual(result["reason"], reason)

    def test_policy_rejects_hidden_patch_or_approval_fields(self):
        for field in ("mission_patch", "safety_patch", "approved", "apply_now", "raw_prompt"):
            with self.subTest(field=field):
                value = proposal()
                value[field] = "unsafe"
                with self.assertRaisesRegex(policy.ScopePolicyError, "unsupported fields"):
                    policy.evaluate(value)

    def test_policy_rejects_unknown_kind_or_schema(self):
        with self.assertRaisesRegex(policy.ScopePolicyError, "unsupported proposal kind"):
            policy.evaluate(proposal("architecture_override"))
        with self.assertRaisesRegex(policy.ScopePolicyError, "unsupported schema_version"):
            policy.evaluate(proposal(schema_version=2))

    def test_summary_and_identifier_are_bounded(self):
        with self.assertRaisesRegex(policy.ScopePolicyError, "summary length out of bounds"):
            policy.evaluate(proposal(summary="short"))
        with self.assertRaisesRegex(policy.ScopePolicyError, "invalid proposal_id"):
            policy.evaluate(proposal(proposal_id="../escape"))

    def test_evidence_refs_are_bounded_unique_machine_refs(self):
        result = policy.evaluate(
            proposal(evidence_refs=["GH:ISSUE/701", "gh:issue/701", "notion:handoff"])
        )
        self.assertEqual(result["evidence_ref_count"], 2)

        with self.assertRaisesRegex(policy.ScopePolicyError, "too many evidence_refs"):
            policy.evaluate(
                proposal(evidence_refs=[f"metric:item_{index}" for index in range(13)])
            )
        with self.assertRaisesRegex(policy.ScopePolicyError, "invalid evidence_ref"):
            policy.evaluate(proposal(evidence_refs=["raw evidence with spaces"]))

    def test_result_never_echoes_summary_or_raw_evidence(self):
        secretish = "Do not echo this operator-only narrative into logs."
        result = policy.evaluate(
            proposal(summary=secretish, evidence_refs=["gh:issue/701"])
        )
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn(secretish, encoded)
        self.assertNotIn("gh:issue/701", encoded)

    def test_cli_require_auto_allowed_fails_closed_for_protected_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proposal.json"
            path.write_text(json.dumps(proposal("safety_change")), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(path),
                    "--json",
                    "--require-auto-allowed",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(completed.returncode, 1)
        result = json.loads(completed.stdout)
        self.assertEqual(result["decision"], "HUMAN_APPROVAL_REQUIRED")

    def test_cli_auto_allows_only_backlog_idea(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proposal.json"
            path.write_text(json.dumps(proposal()), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(path),
                    "--json",
                    "--require-auto-allowed",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["decision"], "BACKLOG_APPEND_ALLOWED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
