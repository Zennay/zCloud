#!/usr/bin/env python3
"""Validate one coherent evidence transaction for future control-plane preflight admission."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

POLICY = "control-plane-preflight-evidence-coherence-v2"
MAX_BYTES = 64 * 1024
FUTURE_SKEW_SECONDS = 5
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
POLICY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,95}$")

REQUIRED_VERDICTS = {
    "dependency": "ready",
    "owner_coverage": "ready",
    "writer_window": "clear",
    "open_pr_ownership": "clear",
    "unpr_branch_ownership": "clear",
}
REQUIRED_POLICIES = {
    "dependency": "zcloud-capability-dependency-dag-v1",
    "owner_coverage": "zcloud-roadmap-owner-coverage-v1",
    "writer_window": "zcloud-serialized-writer-window-audit-v1",
    "open_pr_ownership": "zcloud-open-pr-overlap-audit-v1",
    "unpr_branch_ownership": "zcloud-unpr-branch-overlap-audit-v1",
}
TOP_KEYS = {
    "schema_version",
    "captured_at",
    "main_sha",
    "candidate_base_sha",
    "capability_id",
    "evidence",
}
EVIDENCE_KEYS = {
    "kind",
    "policy",
    "capability_id",
    "observed_at",
    "main_sha",
    "verdict",
    "complete",
}


class EvidenceError(ValueError):
    """Bounded fail-closed evidence error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _timestamp(value: Any, code: str) -> dt.datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise EvidenceError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceError(code)
    return parsed.astimezone(dt.timezone.utc)


def _sha(value: Any, code: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise EvidenceError(code)
    return value


def _token(value: Any, code: str) -> str:
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value):
        raise EvidenceError(code)
    return value


def load_snapshot(path: Path) -> dict[str, Any]:
    try:
        stat = path.lstat()
    except OSError as exc:
        raise EvidenceError("snapshot_unreadable") from exc
    if path.is_symlink():
        raise EvidenceError("snapshot_symlink_rejected")
    if not path.is_file():
        raise EvidenceError("snapshot_not_regular")
    if stat.st_size > MAX_BYTES:
        raise EvidenceError("snapshot_too_large")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EvidenceError("snapshot_unreadable") from exc
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise EvidenceError("snapshot_too_large")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceError("snapshot_invalid_json") from exc
    if not isinstance(payload, dict):
        raise EvidenceError("snapshot_not_object")
    return payload


def audit_snapshot(
    snapshot: dict[str, Any],
    *,
    now: dt.datetime | None = None,
    max_age_seconds: int = 300,
    max_evidence_span_seconds: int = 60,
) -> dict[str, Any]:
    if not 1 <= max_age_seconds <= 900:
        raise EvidenceError("max_age_invalid")
    if not 1 <= max_evidence_span_seconds <= 300:
        raise EvidenceError("max_evidence_span_invalid")
    if set(snapshot) != TOP_KEYS:
        raise EvidenceError("snapshot_keys_invalid")
    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 2:
        raise EvidenceError("schema_version_unsupported")

    now_utc = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    captured_at = _timestamp(snapshot["captured_at"], "captured_at_invalid")
    age = (now_utc - captured_at).total_seconds()
    if age < -FUTURE_SKEW_SECONDS:
        raise EvidenceError("snapshot_from_future")
    if age > max_age_seconds:
        raise EvidenceError("snapshot_stale")

    main_sha = _sha(snapshot["main_sha"], "main_sha_invalid")
    candidate_base_sha = _sha(
        snapshot["candidate_base_sha"], "candidate_base_sha_invalid"
    )
    if candidate_base_sha != main_sha:
        raise EvidenceError("candidate_base_mismatch")

    capability_id = _token(snapshot["capability_id"], "capability_id_invalid")
    rows = snapshot["evidence"]
    if not isinstance(rows, list):
        raise EvidenceError("evidence_invalid")
    if len(rows) != len(REQUIRED_VERDICTS):
        raise EvidenceError("evidence_count_invalid")

    seen: set[str] = set()
    bounded_rows: list[dict[str, str]] = []
    observed: list[dt.datetime] = []
    blocked: list[str] = []

    for row in rows:
        if not isinstance(row, dict) or set(row) != EVIDENCE_KEYS:
            raise EvidenceError("evidence_row_invalid")
        kind = row["kind"]
        if not isinstance(kind, str) or kind not in REQUIRED_VERDICTS:
            raise EvidenceError("evidence_kind_invalid")
        if kind in seen:
            raise EvidenceError("evidence_kind_duplicate")
        seen.add(kind)

        row_capability_id = _token(
            row["capability_id"], "evidence_capability_id_invalid"
        )
        if row_capability_id != capability_id:
            raise EvidenceError("evidence_capability_mismatch")

        policy = row["policy"]
        if not isinstance(policy, str) or not POLICY_RE.fullmatch(policy):
            raise EvidenceError("evidence_policy_invalid")
        if policy != REQUIRED_POLICIES[kind]:
            raise EvidenceError("evidence_policy_mismatch")
        if row["complete"] is not True:
            raise EvidenceError("evidence_incomplete")

        row_sha = _sha(row["main_sha"], "evidence_main_sha_invalid")
        if row_sha != main_sha:
            raise EvidenceError("evidence_main_mismatch")

        observed_at = _timestamp(row["observed_at"], "evidence_observed_at_invalid")
        if observed_at > captured_at:
            raise EvidenceError("evidence_after_capture")
        if (captured_at - observed_at).total_seconds() > max_evidence_span_seconds:
            raise EvidenceError("evidence_stale")
        observed.append(observed_at)

        verdict = row["verdict"]
        if not isinstance(verdict, str) or not TOKEN_RE.fullmatch(verdict):
            raise EvidenceError("evidence_verdict_invalid")
        if verdict != REQUIRED_VERDICTS[kind]:
            blocked.append(kind)

        bounded_rows.append(
            {
                "kind": kind,
                "policy": policy,
                "capability_id": row_capability_id,
                "verdict": verdict,
            }
        )

    if seen != set(REQUIRED_VERDICTS):
        raise EvidenceError("evidence_kinds_incomplete")

    evidence_span = (
        (max(observed) - min(observed)).total_seconds() if observed else 0.0
    )
    if evidence_span > max_evidence_span_seconds:
        raise EvidenceError("evidence_span_exceeded")

    blocked.sort()
    bounded_rows.sort(key=lambda row: row["kind"])
    status = "admit" if not blocked else "blocked"
    return {
        "ok": status == "admit",
        "policy": POLICY,
        "status": status,
        "main_sha": main_sha,
        "capability_id": capability_id,
        "required_kinds": sorted(REQUIRED_VERDICTS),
        "blocked_kinds": blocked,
        "evidence_span_seconds": round(evidence_span, 3),
        "evidence": bounded_rows,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--max-age-seconds", type=int, default=300)
    parser.add_argument("--max-evidence-span-seconds", type=int, default=60)
    parser.add_argument("--require-admit", action="store_true")
    args = parser.parse_args(argv)

    try:
        payload = audit_snapshot(
            load_snapshot(args.snapshot),
            max_age_seconds=args.max_age_seconds,
            max_evidence_span_seconds=args.max_evidence_span_seconds,
        )
    except EvidenceError as exc:
        payload = {
            "ok": False,
            "policy": POLICY,
            "status": "incomplete",
            "errors": [exc.code],
            "mutation_performed": False,
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 1 if args.require_admit else 2

    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if args.require_admit and payload["status"] != "admit":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
