#!/usr/bin/env python3
"""Bound autonomous zCloud self-improvement proposals without mission drift."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


PROPOSAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
EVIDENCE_REF_RE = re.compile(r"^[a-z0-9][a-z0-9:._/#-]{0,127}$")
ALLOWED_KINDS = {"backlog_idea", "mission_change", "safety_change"}
MAX_EVIDENCE_REFS = 12
MAX_INPUT_BYTES = 32_768


class ScopePolicyError(ValueError):
    pass


def _bounded_text(value: object, *, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise ScopePolicyError(f"{field} must be text")
    text = value.strip()
    if not (minimum <= len(text) <= maximum):
        raise ScopePolicyError(f"{field} length out of bounds")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
        raise ScopePolicyError(f"{field} contains control characters")
    return text


def _parse_evidence_refs(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ScopePolicyError("evidence_refs must be a list")
    if len(value) > MAX_EVIDENCE_REFS:
        raise ScopePolicyError("too many evidence_refs")
    refs: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ScopePolicyError("evidence_ref must be text")
        ref = item.strip().lower()
        if not EVIDENCE_REF_RE.fullmatch(ref):
            raise ScopePolicyError("invalid evidence_ref")
        if ref not in refs:
            refs.append(ref)
    return tuple(refs)


def evaluate(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ScopePolicyError("proposal must be an object")

    allowed = {
        "schema_version",
        "proposal_id",
        "kind",
        "summary",
        "evidence_refs",
    }
    unsupported = set(payload) - allowed
    if unsupported:
        raise ScopePolicyError("proposal contains unsupported fields")

    required = {"schema_version", "proposal_id", "kind", "summary"}
    if not required.issubset(payload):
        raise ScopePolicyError("proposal is incomplete")
    if payload["schema_version"] != 1:
        raise ScopePolicyError("unsupported schema_version")

    proposal_id = str(payload["proposal_id"] or "").strip().lower()
    if not PROPOSAL_ID_RE.fullmatch(proposal_id):
        raise ScopePolicyError("invalid proposal_id")

    kind = str(payload["kind"] or "").strip().lower()
    if kind not in ALLOWED_KINDS:
        raise ScopePolicyError("unsupported proposal kind")

    _bounded_text(payload["summary"], field="summary", minimum=8, maximum=240)
    refs = _parse_evidence_refs(payload.get("evidence_refs"))

    if kind == "backlog_idea":
        decision = "BACKLOG_APPEND_ALLOWED"
        reason = "bounded_backlog_idea"
    else:
        decision = "HUMAN_APPROVAL_REQUIRED"
        reason = "mission_change_requires_human" if kind == "mission_change" else "safety_change_requires_human"

    return {
        "schema_version": 1,
        "policy": "self-improvement-scope-v1",
        "proposal_id": proposal_id,
        "kind": kind,
        "decision": decision,
        "reason": reason,
        "evidence_ref_count": len(refs),
    }


def _load_input(path: Path | None) -> object:
    if path is None:
        return json.load(__import__("sys").stdin)

    if path.is_symlink() or not path.is_file():
        raise ScopePolicyError("input must be a regular non-symlink file")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ScopePolicyError("input unreadable") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise ScopePolicyError("input too large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ScopePolicyError("invalid input json") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-auto-allowed", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = evaluate(_load_input(args.input))
    except (ScopePolicyError, json.JSONDecodeError):
        print(json.dumps({"ok": False, "error": "INVALID_SCOPE_PROPOSAL"}, sort_keys=True))
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "ZCLOUD_SELF_IMPROVEMENT_SCOPE "
            f"decision={result['decision']} "
            f"kind={result['kind']} "
            f"evidence_refs={result['evidence_ref_count']}"
        )

    if args.require_auto_allowed and result["decision"] != "BACKLOG_APPEND_ALLOWED":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
