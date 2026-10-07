#!/usr/bin/env python3
"""Build a non-mutating zCloud main-protection apply/rollback plan."""

from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path
from typing import Any

MAX_BYTES = 64 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ProtectionPlanError(ValueError):
    """Raised when offline protection planning evidence is unsafe."""


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ProtectionPlanError("audit_symlink_rejected")
    try:
        meta = path.stat()
    except OSError as exc:
        raise ProtectionPlanError("audit_unreadable") from exc
    if not stat.S_ISREG(meta.st_mode):
        raise ProtectionPlanError("audit_not_regular")
    if meta.st_size > MAX_BYTES:
        raise ProtectionPlanError("audit_too_large")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProtectionPlanError("audit_unreadable") from exc
    if len(raw) > MAX_BYTES:
        raise ProtectionPlanError("audit_too_large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProtectionPlanError("audit_invalid_json") from exc
    if not isinstance(value, dict):
        raise ProtectionPlanError("audit_not_object")
    return value


def _validate_required_check(value: str) -> str:
    if not isinstance(value, str):
        raise ProtectionPlanError("required_check_invalid")
    if value != value.strip() or not value or len(value) > 128:
        raise ProtectionPlanError("required_check_invalid")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ProtectionPlanError("required_check_invalid")
    return value


def _validate_unprotected_audit(audit: dict[str, Any]) -> None:
    if audit.get("repository") != "Zennay/zCloud":
        raise ProtectionPlanError("audit_repository_mismatch")
    if audit.get("branch") != "main":
        raise ProtectionPlanError("audit_branch_mismatch")
    if audit.get("schema_version") != 1:
        raise ProtectionPlanError("audit_schema_unsupported")
    if audit.get("mutation_performed") is not False:
        raise ProtectionPlanError("audit_not_read_only")
    if audit.get("status") != "unprotected":
        raise ProtectionPlanError("rollback_snapshot_required")
    if audit.get("ok") is not False:
        raise ProtectionPlanError("unprotected_audit_incoherent")
    checks = audit.get("checks")
    if not isinstance(checks, dict) or checks.get("branch_protected") is not False:
        raise ProtectionPlanError("unprotected_audit_incoherent")
    if audit.get("required_check_count") != 0:
        raise ProtectionPlanError("unprotected_audit_incoherent")


def build_plan(
    *,
    audit: dict[str, Any],
    expected_main_sha: str,
    required_check: str,
) -> dict[str, Any]:
    _validate_unprotected_audit(audit)
    if not isinstance(expected_main_sha, str) or not SHA_RE.fullmatch(expected_main_sha):
        raise ProtectionPlanError("expected_main_sha_invalid")
    required_check = _validate_required_check(required_check)

    target = {
        "required_status_checks": {
            "strict": True,
            "contexts": [required_check],
        },
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 1,
            "bypass_pull_request_allowances": {
                "users": [],
                "teams": [],
                "apps": [],
            },
        },
        "restrictions": None,
        "required_conversation_resolution": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }
    return {
        "schema_version": 1,
        "policy": "zcloud-main-protection-plan-v1",
        "repository": "Zennay/zCloud",
        "branch": "main",
        "expected_main_sha": expected_main_sha,
        "required_check": required_check,
        "prechange_status": "unprotected",
        "target_protection": target,
        "rollback": {
            "action": "delete_branch_protection",
            "reason": "prechange_state_was_unprotected",
        },
        "apply_allowed": False,
        "apply_requirements": [
            "reconfirm_exact_main_sha",
            "prove_persistent_vps_admin",
            "reconfirm_serialized_writer_window_clear",
            "reconfirm_zero_required_direct_main_writers",
            "prove_required_check_current_and_pr_green",
            "capture_prechange_governance_evidence",
            "explicit_apply_lane_only",
        ],
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an offline zCloud main-protection target and rollback plan"
    )
    parser.add_argument("--audit-json", type=Path, required=True)
    parser.add_argument("--expected-main-sha", required=True)
    parser.add_argument("--required-check", required=True)
    args = parser.parse_args(argv)

    try:
        audit = _load_json(args.audit_json)
        result = build_plan(
            audit=audit,
            expected_main_sha=args.expected_main_sha,
            required_check=args.required_check,
        )
    except ProtectionPlanError as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "policy": "zcloud-main-protection-plan-v1",
                    "status": "rejected",
                    "errors": [str(exc)],
                    "apply_allowed": False,
                    "mutation_performed": False,
                },
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
