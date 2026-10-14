#!/usr/bin/env python3
"""Read-only, fail-closed admission evidence for the zCloud queue watchdog.

This is an independent reference for #1269 / #1326, NOT an implementation of
GitHub run cancellation, listener restart, or worker force-push. In particular,
multiple queued jobs on the same workflow are NOT proof of duplicate intent.
The existing privileged watchdog workflow is deliberately not imported or run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from typing import Any

POLICY = "zcloud-queue-watchdog-admission-v1"
REPO = "Zennay/zCloud"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_ALLOWED_STATES = {"queued", "in_progress", "waiting", "requested", "pending", "completed"}
_ALLOWED_EVENTS = {"push", "workflow_dispatch"}
_MAX_PAGES = 20
_MAX_RUNS = 500
_MAX_JOBS = 100


def _number(value: Any) -> bool:
    return type(value) is int and value > 0


def _sha(value: Any) -> bool:
    return type(value) is str and _SHA.fullmatch(value) is not None


def _utc(value: Any) -> datetime:
    if type(value) is not str or not value.endswith("Z") or len(value) > 40:
        raise ValueError("timestamp_not_utc_z")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp_not_utc")
    return parsed


def _fail(reason: str) -> dict[str, Any]:
    # A snapshot alone can NEVER authorize GitHub Actions writes.
    return {
        "policy": POLICY, "decision": "DENY", "reason": reason,
        "inventory_complete": False, "submitted_pagination_consistent": False, "candidate_duplicate_groups": [],
        "cancel_run_ids": [], "mutation_authorized": False,
        "production_verified": False,
    }


def assess(snapshot: Any, *, expected_main_sha: str, now: datetime) -> dict[str, Any]:
    """Classify a complete, exact-main, queue-only observation; never mutate."""
    if not isinstance(snapshot, dict) or not _sha(expected_main_sha):
        return _fail("invalid_input")
    if (
        snapshot.get("repository") != REPO
        or snapshot.get("main_sha_before") != expected_main_sha
        or snapshot.get("main_sha_after") != expected_main_sha
    ):
        return _fail("main_identity_mismatch")
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
        or now.utcoffset().total_seconds() != 0
    ):
        return _fail("invalid_now")
    try:
        observed = _utc(snapshot.get("observed_at"))
    except (TypeError, ValueError, OverflowError):
        return _fail("invalid_observation_time")
    age = (now - observed).total_seconds()
    if not 0 <= age <= 60:
        return _fail("stale_or_future_snapshot")
    pages = snapshot.get("run_pages")
    if type(pages) is not list or not 1 <= len(pages) <= _MAX_PAGES:
        return _fail("missing_or_excess_pages")

    run_ids: set[int] = set()
    job_ids: set[int] = set()
    grouped: dict[tuple[int, str, str, str], list[int]] = {}
    run_count = 0
    for page_index, page in enumerate(pages):
        if type(page) is not dict or type(page.get("runs")) is not list:
            return _fail("invalid_page")
        # Every page must carry an explicit next-page indicator, even when empty.
        if page.get("has_next") is not (page_index < len(pages) - 1):
            return _fail("incomplete_pagination")
        if len(page["runs"]) > 100:
            return _fail("oversized_page")
        for run in page["runs"]:
            run_count += 1
            if run_count > _MAX_RUNS or type(run) is not dict:
                return _fail("invalid_run_inventory")
            run_id = run.get("id")
            if not _number(run_id) or run_id in run_ids:
                return _fail("duplicate_or_invalid_run_id")
            run_ids.add(run_id)
            if (
                run.get("repository") != REPO
                or run.get("head_sha") != expected_main_sha
                or run.get("head_branch") != "main"
                or type(run.get("event")) is not str
                or run["event"] not in _ALLOWED_EVENTS
                or not _number(run.get("workflow_id"))
                or type(run.get("status")) is not str
                or run["status"] not in _ALLOWED_STATES
            ):
                return _fail("untrusted_or_ambiguous_run")
            if type(run.get("jobs_complete")) is not bool or not run["jobs_complete"]:
                return _fail("incomplete_job_inventory")
            jobs = run.get("jobs")
            if type(jobs) is not list or not 1 <= len(jobs) <= _MAX_JOBS:
                return _fail("invalid_job_inventory")
            all_queued = run["status"] == "queued"
            for job in jobs:
                if type(job) is not dict or not _number(job.get("id")):
                    return _fail("invalid_job_identity")
                if job["id"] in job_ids:
                    return _fail("duplicate_job_identity")
                job_ids.add(job["id"])
                if (
                    type(job.get("status")) is not str
                    or job["status"] not in _ALLOWED_STATES
                ):
                    return _fail("unknown_job_state")
                all_queued = all_queued and job["status"] == "queued"
                labels = job.get("labels")
                if type(labels) is not list or any(type(x) is not str for x in labels):
                    return _fail("invalid_runner_labels")
                if len(labels) > 16 or any(len(x) > 64 for x in labels):
                    return _fail("oversized_runner_labels")
                all_queued = all_queued and {"self-hosted", "zcloud", "vps"}.issubset(set(labels))
            # Cross-workflow and cross-event runs must never be considered dupes.
            # Intent identity is absent from GitHub's ordinary run listing;
            # the only output is a human-review advisory.
            if all_queued:
                key = (run["workflow_id"], run["event"], run["head_branch"], run["head_sha"])
                grouped.setdefault(key, []).append(run_id)
    duplicate_groups = [sorted(ids) for ids in grouped.values() if len(ids) > 1]
    duplicate_groups.sort()
    return {
        "policy": POLICY,
        "decision": "REVIEW_ONLY" if duplicate_groups else "NOOP",
        "reason": "no_authenticated_duplicate_intent_key",
        "inventory_complete": False, "submitted_pagination_consistent": True, "observed_run_count": run_count,
        "candidate_duplicate_groups": duplicate_groups,
        "cancel_run_ids": [], "mutation_authorized": False,
        "production_verified": False,
    }


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate_json_key")
        result[name] = value
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="Local read-only JSON snapshot file")
    parser.add_argument("--expected-main-sha", required=True)
    args = parser.parse_args()
    try:
        # Refuse excessive or streaming snapshots before JSON allocation.
        with open(args.snapshot, "rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("oversized_snapshot")
        snapshot = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))
        decision = assess(snapshot, expected_main_sha=args.expected_main_sha,
                          now=datetime.now(timezone.utc))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        decision = _fail("invalid_snapshot_file")
    print(json.dumps(decision, sort_keys=True, separators=(",", ":")))
    return 0 if decision["decision"] == "NOOP" else 1


if __name__ == "__main__":
    raise SystemExit(main())
