#!/usr/bin/env python3
"""Offline deny-first release evidence receipt for zCloud deploy-ops.

This is a source-only diagnostic, NOT release authority. Never dispatches a
workflow, contacts GitHub, modifies a queue, or writes to the VPS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

MAX_BYTES = 65536
MAX_AGE_SECONDS = 600
SHA = re.compile(r"[0-9a-f]{40}\Z")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
RUN_URL = re.compile(r"https://github[.]com/Zennay/zCloud/actions/runs/[1-9][0-9]*\Z")
JOB_URL = re.compile(r"https://github[.]com/Zennay/zCloud/actions/runs/[1-9][0-9]*/job/[1-9][0-9]*\Z")
ROOT_KEYS = {"schema", "candidate_sha", "canonical_main_sha", "observed_at",
             "ci", "review", "protection", "runner", "locks", "release"}
NESTED_KEYS = {
    "ci": {"head_sha", "conclusion", "run_url"},
    "review": {"head_sha", "approved", "independent"},
    "protection": {"main_sha", "enforced", "reviews_required"},
    "runner": {"main_sha", "runner_id", "labels", "job_url"},
    "locks": {"main_sha", "production_writer_jobs", "all_serialized"},
    "release": {"main_sha", "owner_approved", "exclusive_window"},
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("nonfinite_json")


def decode_receipt(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_BYTES:
        raise ValueError("receipt_too_large")
    try:
        result = json.loads(raw.decode("utf-8", errors="strict"),
                            object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid_json") from error
    if type(result) is not dict:
        raise ValueError("invalid_schema")
    return result


def _valid_sha(value: Any) -> bool:
    return type(value) is str and SHA.fullmatch(value) is not None


def _exact_shape(value: Any, keys: set[str]) -> bool:
    return type(value) is dict and set(value) == keys


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _utc_stamp(value: Any) -> datetime | None:
    if type(value) is not str or STAMP.fullmatch(value) is None:
        return None
    try:
        stamp = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if stamp.utcoffset() != timezone.utc.utcoffset(stamp):
        return None
    return stamp


def evaluate(receipt: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Classify untrusted claimed evidence without ever authorizing a live write."""
    reason_codes: list[str] = []
    output: dict[str, Any] = {
        "contract": "zcloud-release-evidence-v1",
        "evidence_source": "untrusted_offline_receipt",
        "live_sources_verified": False,
        "offline_claims_complete": False,
        "production_mutation_authorized": False,
        "reason_codes": reason_codes,
    }
    if (not _exact_shape(receipt, ROOT_KEYS) or
            receipt.get("schema") != "zcloud-release-evidence-v1" or
            any(not _exact_shape(receipt.get(k), shape)
                for k, shape in NESTED_KEYS.items())):
        reason_codes.append("invalid_schema")
        return output

    candidate = receipt["candidate_sha"]
    main = receipt["canonical_main_sha"]
    if not _valid_sha(candidate) or not _valid_sha(main) or candidate != main:
        reason_codes.append("candidate_not_exact_current_main")

    clock = now if now is not None else datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("now_must_be_timezone_aware")
    stamp = _utc_stamp(receipt["observed_at"])
    if stamp is None or not (0 <= (clock - stamp).total_seconds() <= MAX_AGE_SECONDS):
        reason_codes.append("stale_or_invalid_observation")

    ci = receipt["ci"]
    if (ci["head_sha"] != candidate or not _valid_sha(ci["head_sha"]) or
            ci["conclusion"] != "success" or
            type(ci["run_url"]) is not str or not RUN_URL.fullmatch(ci["run_url"])):
        reason_codes.append("ci_not_exact_head_success")

    review = receipt["review"]
    if (review["head_sha"] != candidate or
            review["approved"] is not True or review["independent"] is not True):
        reason_codes.append("independent_review_missing")

    protection = receipt["protection"]
    if (protection["main_sha"] != main or protection["enforced"] is not True or
            protection["reviews_required"] is not True):
        reason_codes.append("main_protection_missing")

    runner = receipt["runner"]
    if (runner["main_sha"] != main or not _positive_int(runner["runner_id"]) or
            type(runner["labels"]) is not list or
            not all(type(label) is str for label in runner["labels"]) or
            not {"self-hosted", "zcloud", "vps"}.issubset(set(
                label for label in runner["labels"] if type(label) is str)) or
            type(runner["job_url"]) is not str or
            not JOB_URL.fullmatch(runner["job_url"])):
        reason_codes.append("vps_runner_assignment_unproven")

    locks = receipt["locks"]
    if (locks["main_sha"] != main or
            type(locks["production_writer_jobs"]) is not int or
            locks["production_writer_jobs"] != 7 or
            locks["all_serialized"] is not True):
        reason_codes.append("production_writer_lock_incomplete")

    release = receipt["release"]
    if (release["main_sha"] != main or release["owner_approved"] is not True or
            release["exclusive_window"] is not True):
        reason_codes.append("serialized_release_window_unapproved")

    output["offline_claims_complete"] = not reason_codes
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Local bounded JSON receipt path")
    args = parser.parse_args(argv)
    try:
        path = Path(args.input)
        if path.is_symlink() or not path.is_file():
            raise ValueError("unsafe_or_missing_receipt")
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        result = evaluate(decode_receipt(raw))
    except (OSError, ValueError, TypeError) as error:
        allowed = {"receipt_too_large", "duplicate_json_key", "nonfinite_json",
                   "invalid_json", "invalid_schema", "unsafe_or_missing_receipt"}
        code = str(error) if str(error) in allowed else "unreadable_receipt"
        result = {"contract": "zcloud-release-evidence-v1",
                  "evidence_source": "untrusted_offline_receipt",
                  "offline_claims_complete": False,
                  "production_mutation_authorized": False,
                  "reason_codes": [code]}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["offline_claims_complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
