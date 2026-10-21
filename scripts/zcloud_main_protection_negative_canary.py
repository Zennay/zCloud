#!/usr/bin/env python3
"""Offline planner for a non-destructive zCloud branch-protection canary."""

from __future__ import annotations
import argparse, json, re, stat
from pathlib import Path
from typing import Any

MAX_BYTES = 65536
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

class CanaryPlanError(ValueError):
    pass

def load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise CanaryPlanError("plan_symlink_rejected")
    try:
        meta = path.stat()
    except OSError as exc:
        raise CanaryPlanError("plan_unreadable") from exc
    if not stat.S_ISREG(meta.st_mode):
        raise CanaryPlanError("plan_not_regular")
    if meta.st_size > MAX_BYTES:
        raise CanaryPlanError("plan_too_large")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CanaryPlanError("plan_invalid_json") from exc
    if not isinstance(value, dict):
        raise CanaryPlanError("plan_not_object")
    return value

def require(value: bool, reason: str) -> None:
    if not value:
        raise CanaryPlanError(reason)

def validate_target_plan(plan: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    require(plan.get("schema_version") == 1, "plan_schema_unsupported")
    require(plan.get("policy") == "zcloud-main-protection-plan-v1", "plan_policy_mismatch")
    require(plan.get("repository") == "Zennay/zCloud", "plan_repository_mismatch")
    require(plan.get("branch") == "main", "plan_branch_mismatch")
    require(plan.get("prechange_status") == "unprotected", "plan_prechange_status_mismatch")
    require(plan.get("apply_allowed") is False, "plan_apply_must_be_disabled")
    require(plan.get("mutation_performed") is False, "plan_must_be_read_only")

    sha = plan.get("expected_main_sha")
    require(isinstance(sha, str) and SHA_RE.fullmatch(sha) is not None, "expected_main_sha_invalid")
    check = plan.get("required_check")
    require(isinstance(check, str) and check == check.strip() and 0 < len(check) <= 128, "required_check_invalid")
    require(all(32 <= ord(ch) < 127 for ch in check), "required_check_invalid")

    target = plan.get("target_protection")
    require(isinstance(target, dict), "target_protection_missing")
    status = target.get("required_status_checks")
    require(isinstance(status, dict) and status.get("strict") is True, "target_status_checks_not_strict")
    require(status.get("contexts") == [check], "target_required_check_mismatch")
    require(target.get("enforce_admins") is True, "target_admins_not_enforced")
    require(target.get("required_conversation_resolution") is True, "target_conversation_resolution_missing")
    require(target.get("allow_force_pushes") is False, "target_force_pushes_allowed")
    require(target.get("allow_deletions") is False, "target_deletions_allowed")

    reviews = target.get("required_pull_request_reviews")
    require(isinstance(reviews, dict), "target_pr_reviews_missing")
    require(reviews.get("required_approving_review_count") == 0, "target_review_count_mismatch")
    require(reviews.get("dismiss_stale_reviews") is False, "target_dismiss_stale_reviews_mismatch")
    require(reviews.get("require_code_owner_reviews") is False, "target_code_owner_reviews_mismatch")
    require(reviews.get("require_last_push_approval") is False, "target_last_push_approval_mismatch")
    require(reviews.get("bypass_pull_request_allowances") == {"users": [], "teams": [], "apps": []}, "target_bypass_allowances_not_empty")

    rollback = plan.get("rollback")
    require(isinstance(rollback, dict) and rollback.get("action") == "delete_branch_protection", "rollback_action_mismatch")
    return sha, check, target

def build_plan(target_plan: dict[str, Any]) -> dict[str, Any]:
    sha, check, target = validate_target_plan(target_plan)
    branch = f"zcloud-protection-canary/{sha[:12]}"
    canary_ref = f"refs/heads/{branch}"
    require(canary_ref != "refs/heads/main", "main_mutation_scope_forbidden")
    return {
        "schema_version": 1,
        "policy": "zcloud-main-protection-negative-canary-v1",
        "repository": "Zennay/zCloud",
        "protected_branch": "main",
        "expected_main_sha": sha,
        "required_check": check,
        "canary_branch": branch,
        "canary_base_sha": sha,
        "target_protection": target,
        "expected_direct_update_result": "rejected",
        "execution_sequence": [
            {"step": "reconfirm_main", "operation": "read_ref", "target": "refs/heads/main", "expect_sha": sha, "mutation": False},
            {"step": "create_canary_branch", "operation": "create_ref", "target": canary_ref, "source_sha": sha, "mutation": True},
            {"step": "apply_canary_protection", "operation": "put_branch_protection", "target": branch, "mutation": True},
            {"step": "verify_canary_protection", "operation": "read_branch_protection", "target": branch, "mutation": False},
            {"step": "prepare_benign_commit", "operation": "create_unreferenced_commit", "parent_sha": sha, "mutation": True},
            {"step": "attempt_direct_canary_update", "operation": "update_ref", "target": canary_ref, "force": False, "expect": "rejected", "mutation": True},
            {"step": "verify_canary_unchanged", "operation": "read_ref", "target": canary_ref, "expect_sha": sha, "mutation": False},
            {"step": "cleanup_canary_protection", "operation": "delete_branch_protection", "target": branch, "mutation": True, "always": True},
            {"step": "cleanup_canary_branch", "operation": "delete_ref", "target": canary_ref, "mutation": True, "always": True},
            {"step": "verify_main_unchanged", "operation": "read_ref", "target": "refs/heads/main", "expect_sha": sha, "mutation": False, "always": True},
        ],
        "failure_handling": {
            "unexpected_direct_update_success": "record_failure_then_cleanup",
            "cleanup_on_all_outcomes": True,
            "main_verification_on_all_outcomes": True,
        },
        "mutation_scope": {
            "allowed_refs": [canary_ref],
            "forbidden_refs": ["refs/heads/main"],
            "allow_unreferenced_commit_object": True,
            "allow_force": False,
            "allow_ruleset_mutation": False,
            "allow_main_protection_mutation": False,
        },
        "required_postconditions": {
            "main_ref_unchanged": True,
            "canary_direct_update_rejected": True,
            "canary_ref_unchanged_after_rejection": True,
            "cleanup_attempted_on_all_outcomes": True,
            "canary_protection_removed_before_branch_delete": True,
            "canary_branch_deleted": True,
        },
        "execution_allowed": False,
        "mutation_performed": False,
    }

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-plan-json", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_plan(load_json(args.target_plan_json))
    except CanaryPlanError as exc:
        print(json.dumps({"schema_version": 1, "policy": "zcloud-main-protection-negative-canary-v1", "status": "rejected", "errors": [str(exc)], "execution_allowed": False, "mutation_performed": False}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
