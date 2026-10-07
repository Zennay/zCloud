#!/usr/bin/env python3
"""Audit zCloud main branch protection without mutating repository governance."""

from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path
from typing import Any

MAX_BYTES = 128 * 1024


class ProtectionEvidenceError(ValueError):
    """Raised when branch-protection evidence is malformed or unsafe."""


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ProtectionEvidenceError("protection_symlink_rejected")
    try:
        meta = path.stat()
    except OSError as exc:
        raise ProtectionEvidenceError("protection_unreadable") from exc
    if not stat.S_ISREG(meta.st_mode):
        raise ProtectionEvidenceError("protection_not_regular")
    if meta.st_size > MAX_BYTES:
        raise ProtectionEvidenceError("protection_too_large")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProtectionEvidenceError("protection_unreadable") from exc
    if len(raw) > MAX_BYTES:
        raise ProtectionEvidenceError("protection_too_large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProtectionEvidenceError("protection_invalid_json") from exc
    if not isinstance(value, dict):
        raise ProtectionEvidenceError("protection_not_object")
    return value


def _enabled(value: Any) -> bool:
    return isinstance(value, dict) and value.get("enabled") is True


def audit_protection(
    *,
    branch_protected: bool,
    protection: dict[str, Any] | None,
) -> dict[str, Any]:
    if type(branch_protected) is not bool:
        raise ProtectionEvidenceError("branch_protected_invalid")

    if not branch_protected:
        if protection not in (None, {}):
            raise ProtectionEvidenceError("unprotected_with_protection_payload")
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

    if not isinstance(protection, dict):
        raise ProtectionEvidenceError("protected_without_protection_payload")

    reviews = protection.get("required_pull_request_reviews")
    allowances = reviews.get("bypass_pull_request_allowances") if isinstance(reviews, dict) else None
    if allowances is None:
        bypass_empty = isinstance(reviews, dict)
    elif isinstance(allowances, dict):
        bypass_empty = not any(
            bool(allowances.get(kind))
            for kind in ("users", "teams", "apps")
        )
    else:
        bypass_empty = False

    status_checks = protection.get("required_status_checks")
    if isinstance(status_checks, dict):
        checks = status_checks.get("checks")
        contexts = status_checks.get("contexts")
        check_count = (
            len(checks)
            if isinstance(checks, list)
            else len(contexts)
            if isinstance(contexts, list)
            else 0
        )
        strict_status_checks = status_checks.get("strict") is True
    else:
        check_count = 0
        strict_status_checks = False

    checks_out = {
        "branch_protected": True,
        "admins_enforced": _enabled(protection.get("enforce_admins")),
        "pull_request_required": isinstance(reviews, dict),
        "bypass_allowances_empty": bypass_empty,
        "force_pushes_disabled": not _enabled(protection.get("allow_force_pushes")),
        "deletions_disabled": not _enabled(protection.get("allow_deletions")),
        "conversation_resolution_required": _enabled(
            protection.get("required_conversation_resolution")
        ),
        "strict_status_checks": strict_status_checks,
        "required_check_count_positive": check_count > 0,
    }
    complete = all(checks_out.values())

    return {
        "ok": complete,
        "schema_version": 1,
        "repository": "Zennay/zCloud",
        "branch": "main",
        "status": "protected" if complete else "needs_hardening",
        "checks": checks_out,
        "required_check_count": check_count,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit preventive protection of Zennay/zCloud main"
    )
    parser.add_argument(
        "--branch-protected",
        choices=("true", "false"),
        required=True,
    )
    parser.add_argument("--protection-json", type=Path)
    parser.add_argument("--require-protected", action="store_true")
    args = parser.parse_args(argv)

    branch_protected = args.branch_protected == "true"
    try:
        protection = (
            _load_json(args.protection_json)
            if args.protection_json is not None
            else None
        )
        result = audit_protection(
            branch_protected=branch_protected,
            protection=protection,
        )
    except ProtectionEvidenceError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "schema_version": 1,
                    "repository": "Zennay/zCloud",
                    "branch": "main",
                    "status": "incomplete",
                    "errors": [str(exc)],
                    "mutation_performed": False,
                },
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps(result, sort_keys=True))
    if args.require_protected and result["status"] != "protected":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
