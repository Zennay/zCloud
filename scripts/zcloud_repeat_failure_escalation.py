#!/usr/bin/env python3
"""Deterministic policy for escalating repeated failed fixes to diagnosis."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_HISTORY = 20
EVENTS = {"fix_failed", "fix_succeeded", "diagnosed"}


class EscalationPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Attempt:
    fingerprint: str
    event: str

    @classmethod
    def from_mapping(cls, value: object) -> "Attempt":
        if not isinstance(value, dict):
            raise EscalationPolicyError("attempt must be an object")
        fingerprint = str(value.get("fingerprint") or "").strip().lower()
        event = str(value.get("event") or "").strip().lower()
        if not FINGERPRINT_RE.fullmatch(fingerprint):
            raise EscalationPolicyError("fingerprint must be a lowercase SHA-256 hex digest")
        if event not in EVENTS:
            raise EscalationPolicyError("unsupported attempt event")
        if set(value) - {"fingerprint", "event"}:
            raise EscalationPolicyError("attempt contains unsupported fields")
        return cls(fingerprint=fingerprint, event=event)


def parse_attempts(payload: object) -> list[Attempt]:
    if not isinstance(payload, dict) or set(payload) != {"attempts"}:
        raise EscalationPolicyError("payload must contain only attempts")
    raw = payload["attempts"]
    if not isinstance(raw, list):
        raise EscalationPolicyError("attempts must be a list")
    if len(raw) > MAX_HISTORY:
        raise EscalationPolicyError(f"attempt history exceeds {MAX_HISTORY}")
    return [Attempt.from_mapping(item) for item in raw]


def decision(attempts: list[Attempt]) -> dict:
    """Return the next permitted action without exposing the fingerprint."""
    if not attempts:
        return {
            "decision": "FIX_ALLOWED",
            "reason": "no_prior_attempts",
            "consecutive_failed_fixes": 0,
        }

    current = attempts[-1].fingerprint
    failed = 0
    for attempt in reversed(attempts):
        if attempt.fingerprint != current:
            break
        if attempt.event in {"fix_succeeded", "diagnosed"}:
            break
        if attempt.event == "fix_failed":
            failed += 1

    if attempts[-1].event == "fix_succeeded":
        return {
            "decision": "FIX_ALLOWED",
            "reason": "issue_resolved",
            "consecutive_failed_fixes": 0,
        }
    if attempts[-1].event == "diagnosed":
        return {
            "decision": "FIX_ALLOWED",
            "reason": "diagnosis_completed",
            "consecutive_failed_fixes": 0,
        }
    if failed >= 2:
        return {
            "decision": "DIAGNOSE_REQUIRED",
            "reason": "same_issue_failed_twice",
            "consecutive_failed_fixes": failed,
        }
    return {
        "decision": "FIX_ALLOWED",
        "reason": "bounded_fix_budget_remaining",
        "consecutive_failed_fixes": failed,
    }


def evaluate_payload(payload: object) -> dict:
    attempts = parse_attempts(payload)
    result = decision(attempts)
    return {
        "schema_version": 1,
        "policy": "repeat-failure-diagnosis-v1",
        "attempts_observed": len(attempts),
        **result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-fix-allowed", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.input:
            if args.input.is_symlink() or not args.input.is_file():
                raise EscalationPolicyError("input must be a regular non-symlink file")
            payload = json.loads(args.input.read_text(encoding="utf-8"))
        else:
            payload = json.load(__import__("sys").stdin)
        result = evaluate_payload(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, EscalationPolicyError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "ZCLOUD_REPEAT_FAILURE_POLICY "
            f"decision={result['decision']} "
            f"reason={result['reason']} "
            f"attempts={result['attempts_observed']}"
        )
    if args.require_fix_allowed and result["decision"] != "FIX_ALLOWED":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
