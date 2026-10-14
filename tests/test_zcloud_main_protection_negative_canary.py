import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_main_protection_negative_canary.py"
SPEC = importlib.util.spec_from_file_location("zcloud_main_protection_negative_canary", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

def target_plan():
    return {
        "schema_version": 1,
        "policy": "zcloud-main-protection-plan-v1",
        "repository": "Zennay/zCloud",
        "branch": "main",
        "expected_main_sha": "a" * 40,
        "required_check": "critical-runner-flows",
        "prechange_status": "unprotected",
        "target_protection": {
            "required_status_checks": {"strict": True, "contexts": ["critical-runner-flows"]},
            "enforce_admins": True,
            "required_pull_request_reviews": {
                "dismiss_stale_reviews": False,
                "require_code_owner_reviews": False,
                "required_approving_review_count": 0,
                "require_last_push_approval": False,
                "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
            },
            "restrictions": None,
            "required_conversation_resolution": True,
            "allow_force_pushes": False,
            "allow_deletions": False,
        },
        "rollback": {"action": "delete_branch_protection", "reason": "prechange_state_was_unprotected"},
        "apply_allowed": False,
        "mutation_performed": False,
    }

class NegativeCanaryPlanTests(unittest.TestCase):
    def test_plan_never_targets_main_for_mutation(self):
        plan = MODULE.build_plan(target_plan())
        self.assertEqual(plan["canary_branch"], "zcloud-protection-canary/aaaaaaaaaaaa")
        self.assertFalse(plan["execution_allowed"])
        self.assertFalse(plan["mutation_performed"])
        self.assertEqual(plan["mutation_scope"]["forbidden_refs"], ["refs/heads/main"])
        for step in plan["execution_sequence"]:
            if step["mutation"] and step.get("operation") != "create_unreferenced_commit":
                self.assertNotEqual(step.get("target"), "refs/heads/main")
        attempt = next(x for x in plan["execution_sequence"] if x["step"] == "attempt_direct_canary_update")
        self.assertEqual(attempt["expect"], "rejected")
        self.assertFalse(attempt["force"])

    def test_cleanup_is_mandatory_even_if_rejection_fails(self):
        plan = MODULE.build_plan(target_plan())
        steps = {x["step"]: x for x in plan["execution_sequence"]}
        self.assertTrue(plan["failure_handling"]["cleanup_on_all_outcomes"])
        self.assertEqual(plan["failure_handling"]["unexpected_direct_update_success"], "record_failure_then_cleanup")
        self.assertTrue(steps["cleanup_canary_protection"]["always"])
        self.assertTrue(steps["cleanup_canary_branch"]["always"])
        self.assertTrue(steps["verify_main_unchanged"]["always"])

        order = [x["step"] for x in plan["execution_sequence"]]
        self.assertLess(order.index("cleanup_canary_protection"), order.index("cleanup_canary_branch"))
        self.assertEqual(order[-1], "verify_main_unchanged")

    def test_unreferenced_commit_is_declared_as_mutation_but_cannot_move_main(self):
        plan = MODULE.build_plan(target_plan())
        step = next(x for x in plan["execution_sequence"] if x["step"] == "prepare_benign_commit")
        self.assertTrue(step["mutation"])
        self.assertTrue(plan["mutation_scope"]["allow_unreferenced_commit_object"])
        self.assertNotIn("refs/heads/main", plan["mutation_scope"]["allowed_refs"])

    def test_rejects_weakened_target_contract(self):
        cases = [
            ("enforce_admins", False, "target_admins_not_enforced"),
            ("required_conversation_resolution", False, "target_conversation_resolution_missing"),
            ("allow_force_pushes", True, "target_force_pushes_allowed"),
            ("allow_deletions", True, "target_deletions_allowed"),
        ]
        for key, value, error in cases:
            plan = target_plan()
            plan["target_protection"][key] = value
            with self.assertRaisesRegex(MODULE.CanaryPlanError, error):
                MODULE.build_plan(plan)

    def test_rejects_bypass_or_wrong_required_check(self):
        plan = target_plan()
        plan["target_protection"]["required_pull_request_reviews"]["bypass_pull_request_allowances"]["users"] = ["someone"]
        with self.assertRaisesRegex(MODULE.CanaryPlanError, "target_bypass_allowances_not_empty"):
            MODULE.build_plan(plan)

        plan = target_plan()
        plan["target_protection"]["required_status_checks"]["contexts"] = ["other-check"]
        with self.assertRaisesRegex(MODULE.CanaryPlanError, "target_required_check_mismatch"):
            MODULE.build_plan(plan)

    def test_rejects_live_or_wrong_repo_plan(self):
        for key, value, error in (
            ("apply_allowed", True, "plan_apply_must_be_disabled"),
            ("mutation_performed", True, "plan_must_be_read_only"),
            ("repository", "other/repo", "plan_repository_mismatch"),
            ("branch", "develop", "plan_branch_mismatch"),
        ):
            plan = target_plan()
            plan[key] = value
            with self.assertRaisesRegex(MODULE.CanaryPlanError, error):
                MODULE.build_plan(plan)

    def test_cli_emits_bounded_non_executable_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "target.json"
            path.write_text(json.dumps(target_plan()), encoding="utf-8")
            self.assertEqual(MODULE.main(["--target-plan-json", str(path)]), 0)

if __name__ == "__main__":
    unittest.main()
