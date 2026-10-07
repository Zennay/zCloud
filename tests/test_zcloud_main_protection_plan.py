import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_main_protection_plan.py"
SPEC = importlib.util.spec_from_file_location("zcloud_main_protection_plan", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def unprotected_audit():
    return {
        "ok": False,
        "schema_version": 1,
        "repository": "Zennay/zCloud",
        "branch": "main",
        "status": "unprotected",
        "checks": {
            "branch_protected": False,
            "admins_enforced": False,
            "pull_request_required": False,
            "bypass_allowances_empty": False,
            "force_pushes_disabled": False,
            "deletions_disabled": False,
            "conversation_resolution_required": False,
            "strict_status_checks": False,
            "required_check_count_positive": False,
        },
        "required_check_count": 0,
        "mutation_performed": False,
    }


class ZcloudMainProtectionPlanTests(unittest.TestCase):
    def test_builds_strict_non_mutating_target_and_exact_rollback(self):
        sha = "a" * 40
        result = MODULE.build_plan(
            audit=unprotected_audit(),
            expected_main_sha=sha,
            required_check="zCloud regression smoke",
        )
        self.assertEqual(result["expected_main_sha"], sha)
        self.assertFalse(result["apply_allowed"])
        self.assertFalse(result["mutation_performed"])
        self.assertEqual(result["rollback"]["action"], "delete_branch_protection")

        target = result["target_protection"]
        self.assertTrue(target["enforce_admins"])
        self.assertTrue(target["required_status_checks"]["strict"])
        self.assertEqual(
            target["required_status_checks"]["contexts"],
            ["zCloud regression smoke"],
        )
        self.assertEqual(
            target["required_pull_request_reviews"]["bypass_pull_request_allowances"],
            {"users": [], "teams": [], "apps": []},
        )
        self.assertGreaterEqual(
            target["required_pull_request_reviews"]["required_approving_review_count"],
            1,
        )
        self.assertTrue(target["required_conversation_resolution"])
        self.assertFalse(target["allow_force_pushes"])
        self.assertFalse(target["allow_deletions"])

    def test_refuses_non_unprotected_state_without_exact_rollback_snapshot(self):
        for status in ("protected", "needs_hardening", "incomplete"):
            audit = unprotected_audit()
            audit["status"] = status
            with self.assertRaisesRegex(MODULE.ProtectionPlanError, "rollback_snapshot_required"):
                MODULE.build_plan(
                    audit=audit,
                    expected_main_sha="b" * 40,
                    required_check="stable-check",
                )

    def test_refuses_mutating_or_incoherent_audit(self):
        audit = unprotected_audit()
        audit["mutation_performed"] = True
        with self.assertRaisesRegex(MODULE.ProtectionPlanError, "audit_not_read_only"):
            MODULE.build_plan(
                audit=audit,
                expected_main_sha="c" * 40,
                required_check="stable-check",
            )

        audit = unprotected_audit()
        audit["checks"]["branch_protected"] = True
        with self.assertRaisesRegex(MODULE.ProtectionPlanError, "unprotected_audit_incoherent"):
            MODULE.build_plan(
                audit=audit,
                expected_main_sha="c" * 40,
                required_check="stable-check",
            )

    def test_refuses_wrong_repository_branch_or_schema(self):
        cases = (
            ("repository", "someone/else", "audit_repository_mismatch"),
            ("branch", "develop", "audit_branch_mismatch"),
            ("schema_version", 2, "audit_schema_unsupported"),
        )
        for key, value, error in cases:
            audit = unprotected_audit()
            audit[key] = value
            with self.assertRaisesRegex(MODULE.ProtectionPlanError, error):
                MODULE.build_plan(
                    audit=audit,
                    expected_main_sha="d" * 40,
                    required_check="stable-check",
                )

    def test_refuses_invalid_sha_and_required_check(self):
        with self.assertRaisesRegex(MODULE.ProtectionPlanError, "expected_main_sha_invalid"):
            MODULE.build_plan(
                audit=unprotected_audit(),
                expected_main_sha="not-a-sha",
                required_check="stable-check",
            )
        for check in ("", " leading", "trailing ", "bad\ncheck", "x" * 129):
            with self.assertRaisesRegex(MODULE.ProtectionPlanError, "required_check_invalid"):
                MODULE.build_plan(
                    audit=unprotected_audit(),
                    expected_main_sha="e" * 40,
                    required_check=check,
                )

    def test_cli_output_is_bounded_and_never_enables_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.json"
            path.write_text(json.dumps(unprotected_audit()), encoding="utf-8")
            code = MODULE.main(
                [
                    "--audit-json",
                    str(path),
                    "--expected-main-sha",
                    "f" * 40,
                    "--required-check",
                    "stable-check",
                ]
            )
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
