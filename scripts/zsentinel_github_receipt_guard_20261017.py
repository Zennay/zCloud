#!/usr/bin/env python3
"""Fail-closed offline GitHub run/job evidence gate for cross-project recovery triage.

Consumes a single trusted-collector JSON receipt; never calls GitHub, controls a
runner, edits a queue or authorizes deployment. GitHub 'waiting' / 'pending'
cannot be mistaken for an unavailable self-hosted runner merely by aging.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATUS = {"queued", "waiting", "pending", "requested", "in_progress", "completed"}
CONCLUSIONS = {
    "success", "failure", "cancelled", "timed_out", "action_required",
    "skipped", "neutral", "stale",
}
WAIT = {"waiting", "pending", "requested"}
FAIL = {"failure", "timed_out", "action_required"}
MAX_FILE_BYTES = 256_000
MAX_AGE = timedelta(minutes=30)
CLOCK_SKEW = timedelta(minutes=2)


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON keys refused")
        out[key] = value
    return out


def _timestamp(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(label + ": timezone-qualified timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(label + ": invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(label + ": timezone required")
    return parsed.astimezone(timezone.utc)


def _integer(value, label, *, allow_zero=False):
    if type(value) is not int or not (0 if allow_zero else 1) <= value < 2**63:
        raise ValueError(label + ": bounded integer required")
    return value


def _mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(label + ": object required")
    return value


def inspect_receipt(data, now=None):
    """Produce only a non-authorizing triage receipt, never a recovery decision."""
    data = _mapping(data, "receipt")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("schema_version must equal integer 1")
    project = data.get("project_id")
    if not isinstance(project, str) or not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", project) or len(project) > 64:
        raise ValueError("project_id must be a bounded ASCII slug")
    repo = data.get("repository")
    if not isinstance(repo, str) or len(repo) > 160 or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repo
    ) or ".." in repo or repo.endswith(("/", ".", "-")):
        raise ValueError("repository: canonical owner/repository required")
    run = _mapping(data.get("workflow_run"), "workflow_run")
    job = _mapping(data.get("job"), "job")
    run_id = _integer(run.get("id"), "workflow_run.id")
    job_id = _integer(job.get("id"), "job.id")
    if _integer(job.get("run_id"), "job.run_id") != run_id:
        raise ValueError("job.run_id: inconsistent workflow provenance")
    sha = run.get("head_sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("workflow_run.head_sha: exact lowercase SHA required")
    if job.get("head_sha") != sha:
        raise ValueError("job.head_sha: inconsistent immutable commit provenance")
    attempt = _integer(run.get("run_attempt"), "workflow_run.run_attempt")
    if "run_attempt" in job and _integer(job["run_attempt"], "job.run_attempt") != attempt:
        raise ValueError("job.run_attempt: inconsistent retry provenance")
    if run.get("repository") != repo or job.get("repository") != repo:
        raise ValueError("repository: explicit run/job origin mismatch")
    observed = _timestamp(data.get("observed_at"), "observed_at")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now: timezone required")
    now = now.astimezone(timezone.utc)
    if observed > now + CLOCK_SKEW or now - observed > MAX_AGE:
        raise ValueError("observed_at: stale/future snapshot")
    created = _timestamp(job.get("created_at"), "job.created_at")
    if created > observed + CLOCK_SKEW:
        raise ValueError("job.created_at: later than observation")
    status = job.get("status")
    if not isinstance(status, str) or status not in STATUS:
        raise ValueError("job.status: unrecognized lifecycle")
    conclusion = job.get("conclusion")
    if status == "completed":
        if not isinstance(conclusion, str) or conclusion not in CONCLUSIONS:
            raise ValueError("job.conclusion: recognized terminal outcome required")
    elif conclusion is not None:
        raise ValueError("job.conclusion: nonterminal job has final conclusion")
    runner_id = job.get("runner_id")
    if runner_id is not None:
        _integer(runner_id, "job.runner_id", allow_zero=True)
    runner_name = job.get("runner_name")
    if runner_name is not None and (not isinstance(runner_name, str) or
                                     len(runner_name) > 128 or not runner_name):
        raise ValueError("job.runner_name: bounded string or null required")
    assigned = runner_id not in (None, 0)
    if assigned != bool(runner_name):
        raise ValueError("job.runner_id/name: contradictory assignment")
    if status in WAIT:
        state, reason = "intentional_or_scheduler_wait", "nonqueued_gitHub_wait"
    elif status == "queued" and not assigned and observed - created >= timedelta(minutes=30):
        state, reason = "needs_independent_triage", "aged_unassigned_queue"
    elif status == "queued":
        state, reason = "in_progress", "queue_not_proven_stalled"
    elif status == "in_progress":
        state, reason = "in_progress", "running_job_not_runtime_recovery"
    elif conclusion in FAIL:
        state, reason = "needs_independent_triage", "terminal_job_failure"
    elif conclusion == "success":
        state, reason = "unknown", "ci_green_runtime_unverified"
    else:
        state, reason = "unknown", "non_success_terminal_not_runtime_recovery"
    fingerprint = hashlib.sha256(
        f"{repo}|{project}|{run_id}|{attempt}|{job_id}|{sha}".encode("utf-8")
    ).hexdigest()[:24]
    return {
        "schema_version": 1,
        "project_id": project,
        "state": state,
        "reason": reason,
        "evidence_fingerprint": fingerprint,
        "observed_at": observed.isoformat(),
        "repair_authorized": False,
        "deploy_authorized": False,
        "recovery_verified": False,
        "mutations_performed": False,
    }


def main():
    parser = argparse.ArgumentParser(description="Offline, non-authorizing GitHub triage")
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    try:
        if args.receipt.is_symlink():
            raise ValueError("symbolic link refused")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(args.receipt, flags)
        with os.fdopen(fd, "rb") as f:
            metadata = os.fstat(f.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_BYTES:
                raise ValueError("regular bounded input file required")
            raw = f.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("receipt exceeds size limit")
        receipt = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
        print(json.dumps(inspect_receipt(receipt), sort_keys=True))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        parser.exit(2, f"GitHub triage evidence rejected: {exc.__class__.__name__}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
