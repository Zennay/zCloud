#!/usr/bin/env python3
"""Deterministically select one executable zCloud backlog item by impact/risk.

Pure policy only: no queue writes, repository mutation, prompts, patches or
commands are accepted in the input contract.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from fractions import Fraction
from pathlib import Path

SCHEMA_VERSION = "backlog-selection-v1"
MAX_INPUT_BYTES = 64 * 1024
MAX_CANDIDATES = 100
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,79}$")
REQUIRED_KEYS = {
    "id",
    "impact",
    "risk",
    "executable",
    "claimed",
    "human_gate",
    "conflict",
}


def _real_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if value < 1 or value > 5:
        raise ValueError(f"{label} must be between 1 and 5")
    return value


def _real_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _candidate(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("candidate must be an object")
    if set(raw) != REQUIRED_KEYS:
        missing = sorted(REQUIRED_KEYS - set(raw))
        extra = sorted(set(raw) - REQUIRED_KEYS)
        raise ValueError(
            "candidate fields must match contract"
            + (f"; missing={','.join(missing)}" if missing else "")
            + (f"; extra={','.join(extra)}" if extra else "")
        )
    candidate_id = raw["id"]
    if not isinstance(candidate_id, str) or not ID_RE.fullmatch(candidate_id):
        raise ValueError("candidate id must be canonical")
    return {
        "id": candidate_id,
        "impact": _real_int(raw["impact"], "impact"),
        "risk": _real_int(raw["risk"], "risk"),
        "executable": _real_bool(raw["executable"], "executable"),
        "claimed": _real_bool(raw["claimed"], "claimed"),
        "human_gate": _real_bool(raw["human_gate"], "human_gate"),
        "conflict": _real_bool(raw["conflict"], "conflict"),
    }


def _exclusion(candidate: dict) -> str | None:
    if not candidate["executable"]:
        return "not_executable"
    if candidate["claimed"]:
        return "already_claimed"
    if candidate["human_gate"]:
        return "human_gate"
    if candidate["conflict"]:
        return "active_conflict"
    return None


def select(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "candidates"}:
        raise ValueError("top-level fields must be schema_version and candidates")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise ValueError("schema_version must equal integer 1")
    rows = payload["candidates"]
    if not isinstance(rows, list):
        raise ValueError("candidates must be a list")
    if not rows:
        raise ValueError("candidates must not be empty")
    if len(rows) > MAX_CANDIDATES:
        raise ValueError(f"candidate count exceeds {MAX_CANDIDATES}")

    candidates = [_candidate(row) for row in rows]
    ids = [row["id"] for row in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate ids must be unique")

    excluded = Counter()
    eligible = []
    for row in candidates:
        reason = _exclusion(row)
        if reason:
            excluded[reason] += 1
        else:
            eligible.append(row)

    if not eligible:
        return {
            "schema_version": SCHEMA_VERSION,
            "decision": "NO_ELIGIBLE",
            "candidate_count": len(candidates),
            "eligible_count": 0,
            "selected_id": None,
            "selected_score": None,
            "excluded_counts": dict(sorted(excluded.items())),
        }

    # Highest impact/risk ratio wins. Exact Fraction avoids floating-point drift.
    # Equal ratios prefer greater impact, then lower absolute risk, then ID.
    chosen = sorted(
        eligible,
        key=lambda row: (
            -Fraction(row["impact"], row["risk"]),
            -row["impact"],
            row["risk"],
            row["id"],
        ),
    )[0]
    ratio = Fraction(chosen["impact"], chosen["risk"])
    return {
        "schema_version": SCHEMA_VERSION,
        "decision": "SELECTED",
        "candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "selected_id": chosen["id"],
        "selected_score": {
            "impact": chosen["impact"],
            "risk": chosen["risk"],
            "ratio_numerator": ratio.numerator,
            "ratio_denominator": ratio.denominator,
        },
        "excluded_counts": dict(sorted(excluded.items())),
    }


def _load_bytes(path: Path | None) -> bytes:
    if path is None:
        data = __import__("sys").stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        if path.is_symlink():
            raise ValueError("input path must not be a symlink")
        if not path.is_file():
            raise ValueError("input path must be a regular file")
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"input exceeds {MAX_INPUT_BYTES} bytes")
        with path.open("rb") as handle:
            data = handle.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"input exceeds {MAX_INPUT_BYTES} bytes")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Select one zCloud backlog item by impact/risk")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--require-selected", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    raw = _load_bytes(args.input)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit("invalid UTF-8 JSON input") from exc
    result = select(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if (result["decision"] == "SELECTED" or not args.require_selected) else 3


if __name__ == "__main__":
    raise SystemExit(main())
