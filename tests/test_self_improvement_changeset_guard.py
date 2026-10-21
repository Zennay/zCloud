import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_self_improvement_changeset_guard.py"
SPEC = importlib.util.spec_from_file_location("changeset_guard", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(guard)

A = "a" * 40
B = "b" * 40


class SelfImprovementChangeSetGuardTests(unittest.TestCase):
    def test_pr_accepts_distinct_branch_with_material_change_set(self):
        result = guard.validate_pr(
            base_sha=A,
            head_sha=B,
            base_ref="main",
            head_ref="feature/control-plane-slice",
            changed_files=["scripts/example.py", "tests/test_example.py"],
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["separate_head_ref"])
        self.assertEqual(result["change_count"], 2)

    def test_pr_rejects_protected_or_same_head_ref(self):
        for head_ref in ("main", "master"):
            with self.subTest(head_ref=head_ref):
                with self.assertRaisesRegex(guard.GuardFailure, "head_ref_is_protected_base"):
                    guard.validate_pr(
                        base_sha=A,
                        head_sha=B,
                        base_ref="main",
                        head_ref=head_ref,
                        changed_files=["x.py"],
                    )

    def test_pr_rejects_empty_or_unsafe_change_set(self):
        with self.assertRaisesRegex(guard.GuardFailure, "empty_change_set"):
            guard.validate_pr(
                base_sha=A,
                head_sha=B,
                base_ref="main",
                head_ref="feature/x",
                changed_files=[],
            )
        with self.assertRaisesRegex(guard.GuardFailure, "unsafe_changed_path"):
            guard.validate_pr(
                base_sha=A,
                head_sha=B,
                base_ref="main",
                head_ref="feature/x",
                changed_files=["../escape.py"],
            )

    def test_pr_rejects_invalid_or_equal_revisions(self):
        with self.assertRaisesRegex(guard.GuardFailure, "invalid_head_sha"):
            guard.validate_pr(
                base_sha=A,
                head_sha="short",
                base_ref="main",
                head_ref="feature/x",
                changed_files=["x.py"],
            )
        with self.assertRaisesRegex(guard.GuardFailure, "head_equals_base"):
            guard.validate_pr(
                base_sha=A,
                head_sha=A,
                base_ref="main",
                head_ref="feature/x",
                changed_files=["x.py"],
            )

    def test_push_accepts_exact_commit_associated_with_merged_main_pr(self):
        result = guard.validate_push(
            head_sha=B,
            ref="refs/heads/main",
            associated_prs=[
                {
                    "merged_at": "2026-10-06T16:00:00Z",
                    "base": {"ref": "main"},
                }
            ],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["merged_main_pr_count"], 1)

    def test_push_rejects_no_merged_main_pr(self):
        bad_payloads = (
            [],
            [{"merged_at": None, "base": {"ref": "main"}}],
            [{"merged_at": "2026-10-06T16:00:00Z", "base": {"ref": "release"}}],
        )
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(guard.GuardFailure, "main_push_without_merged_pr"):
                    guard.validate_push(
                        head_sha=B,
                        ref="refs/heads/main",
                        associated_prs=payload,
                    )

    def test_push_rejects_wrong_ref_and_malformed_payload(self):
        with self.assertRaisesRegex(guard.GuardFailure, "push_ref_not_main"):
            guard.validate_push(head_sha=B, ref="refs/heads/dev", associated_prs=[])
        with self.assertRaisesRegex(guard.GuardFailure, "invalid_associated_pr_payload"):
            guard.validate_push(
                head_sha=B,
                ref="refs/heads/main",
                associated_prs={"not": "a list"},
            )


if __name__ == "__main__":
    unittest.main()
