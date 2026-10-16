#!/usr/bin/env python3
"""zSentinel: bounded, read-only stuck-project diagnosis from verified observations.

Input JSON is an evidence snapshot produced by a trusted operator/collector.
No network, shell, GitHub/Notion writes or service mutations occur here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
PAUSED = frozenset({"paused", "parked", "human-blocked"})
RUNNING = frozenset({"running", "in_progress"})
COMPLETED = frozenset({"completed", "failure", "success", "cancelled"})
RESULTS = frozenset({"success", "failure", "cancelled", "timed_out", "action_required"})
MAX_PROJECTS = 200


def utc_time(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}: explicit timestamp required")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}: invalid timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{field}: timezone required")
    return dt.astimezone(UTC)


def required_time(data: dict[str, Any], key: str, now: datetime, where: str) -> datetime:
    dt = utc_time(data.get(key), where + "." + key)
    if dt > now + timedelta(minutes=2):
        raise ValueError(f"{where}.{key}: future timestamp; refusing clock-spoofed evidence")
    return dt


def incident_id(project: str, kind: str, sha: str = "", identity: str = "") -> str:
    token = f"{project}|{kind}|{sha}" + (f"|{identity}" if identity else "")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]


def github_job_identity(github: dict[str, Any], project_id: str) -> str:
    """Separate incidents on distinct GitHub runs/jobs without exposing their raw evidence."""
    parts = []
    for key in ("run_id", "job_id"):
        value = github.get(key)
        if value is None:
            continue
        if type(value) is not int or not 1 <= value <= (2**63 - 1):
            raise ValueError(f"{project_id}.github.{key}: positive integer ID required")
        parts.append(f"{key}:{value}")
    return "|".join(parts)


def verdict(project: str, state: str, reason: str, action: str,
            kind: str = "", sha: str = "", identity: str = "") -> dict[str, str]:
    item = {"project_id": project, "state": state, "reason": reason, "next_action": action}
    if kind:
        item["incident_id"] = incident_id(project, kind, sha, identity)
    return item


def classify(project: dict[str, Any], now: datetime) -> dict[str, str]:
    if not isinstance(project, dict):
        raise ValueError("projects[] must contain objects")
    pid = project.get("id")
    if not isinstance(pid, str) or len(pid) > 64 or not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", pid):
        raise ValueError("project id must be a bounded slug")
    notion = project.get("notion", {})
    github = project.get("github", {})
    runtime = project.get("runtime", {})
    if not all(isinstance(x, dict) for x in (notion, github, runtime)):
        raise ValueError(f"{pid}: sources must be objects")
    # No inferred recovery from a calendar wait or a human stop gate.
    posture = str(notion.get("posture", "")).lower()
    if posture in PAUSED:
        return verdict(pid, "intentional_wait", "notion_project_posture",
                       "Preserve the existing Notion stop gate; work on another owned issue.")
    if notion.get("human_gate") is True:
        return verdict(pid, "intentional_wait", "explicit_human_gate",
                       "Do not bypass the documented approval or device-acceptance gate.")
    if notion.get("wait_until") is not None:
        when = utc_time(notion["wait_until"], pid + ".notion.wait_until")
        if when > now:
            return verdict(pid, "intentional_wait", "scheduled_wait_not_elapsed",
                           "Recheck after the frozen/wait boundary; do not generate false incidents.")
    if github.get("active_owner_pr") is True or runtime.get("ownership_locked") is True:
        return verdict(pid, "owner_held", "active_owner_or_serialized_release",
                       "Do not edit the owner's files; add bounded diagnostic evidence to the existing issue.")
    # Every GitHub job diagnosis must be anchored in a fresh observed snapshot.
    status = github.get("job_status")
    if status is not None:
        if status not in RUNNING | COMPLETED | {"queued", "waiting", "pending"}:
            raise ValueError(f"{pid}.github.job_status: unsupported value")
        observed = required_time(github, "observed_at", now, pid + ".github")
        if now - observed > timedelta(minutes=30):
            return verdict(pid, "unknown", "stale_github_observation",
                           "Refresh GitHub run/job metadata before diagnosing or intervening.")
        sha = github.get("head_sha", "")
        if sha and (not isinstance(sha, str) or len(sha) != 40
                    or any(ch not in "0123456789abcdef" for ch in sha)):
            raise ValueError(f"{pid}.github.head_sha: canonical lowercase full SHA required")
        identity = github_job_identity(github, pid)
        if status in {"queued", "waiting", "pending"}:
            queued = required_time(github, "queued_at", now, pid + ".github")
            if github.get("runner_assigned") is False and now - queued >= timedelta(minutes=30):
                return verdict(pid, "stalled", "github_job_unassigned",
                               "Check exact labels, runner group/repo access and listener from an independent route. Do not restart the missing runner from its own queued job.",
                               "github-unassigned", sha, identity)
            return verdict(pid, "in_progress", "github_queue_not_yet_actionable",
                           "Continue observing; queue delay alone does not prove a broken runner.")
        if status in COMPLETED:
            conclusion = github.get("conclusion")
            if conclusion not in RESULTS:
                raise ValueError(f"{pid}.github.conclusion: explicit conclusion required")
            if status in {"success", "failure", "cancelled"} and status != conclusion:
                raise ValueError(f"{pid}.github.conclusion: contradictory terminal status")
            if conclusion in {"failure", "timed_out", "action_required"}:
                return verdict(pid, "stalled", "github_workflow_failed",
                               "Inspect the exact-head failed job log; reproduce and fix an independently owned scoped defect, then verify fresh CI.",
                               "workflow-failed", sha, identity)
            if conclusion == "cancelled":
                return verdict(pid, "unknown", "github_job_cancelled",
                               "Resolve cancellation reason and owner before retrying.")
    state = runtime.get("execution_state")
    if state == "running":
        hb = required_time(runtime, "heartbeat_at", now, pid + ".runtime")
        seconds = runtime.get("heartbeat_interval_seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool) or not 15 <= seconds <= 3600:
            raise ValueError(f"{pid}.runtime.heartbeat_interval_seconds: 15..3600 required")
        if now - hb > timedelta(seconds=max(seconds * 3, 300)):
            return verdict(pid, "stalled", "runtime_progress_missing",
                           "Read project claim identity and progress events; reproduce staleness before proposing an owner-scoped repair.",
                           "runtime-no-progress")
        return verdict(pid, "in_progress", "recent_runtime_heartbeat",
                       "Keep existing worker ownership; verify material progress, not heartbeat alone.")
    if state in ("paused", "waiting", "blocked"):
        return verdict(pid, "intentional_wait" if state != "blocked" else "unknown",
                       "runtime_state_" + state, "Check the canonical queue claim and explicit release gates.")
    if status == "completed" and github.get("conclusion") == "success":
        return verdict(pid, "unknown", "ci_green_runtime_not_verified",
                       "Do not call this recovered without runtime/acceptance evidence.")
    return verdict(pid, "unknown", "insufficient_progress_evidence",
                   "Collect a fresh Notion/project, GitHub and runtime snapshot; do not invent a fix.")


def diagnose(snapshot: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    if type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    recorded = required_time(snapshot, "observed_at", now, "snapshot")
    if now - recorded > timedelta(minutes=30):
        raise ValueError("snapshot is stale; refresh sources")
    projects = snapshot.get("projects")
    if not isinstance(projects, list) or not 1 <= len(projects) <= MAX_PROJECTS:
        raise ValueError("projects must be a nonempty bounded array")
    ids = [project.get("id") if isinstance(project, dict) else None for project in projects]
    if any(not isinstance(pid, str) for pid in ids):
        raise ValueError("invalid project identifiers: bounded string slug required")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate project identifiers are not allowed")
    reports = [classify(project, now) for project in projects]
    return {
        "schema_version": 1,
        "observed_at": recorded.isoformat(),
        "mutations_performed": False,
        "deploy_authorized": False,
        "incidents": [item for item in reports if item["state"] == "stalled"],
        "projects": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only evidence-based portfolio diagnoses")
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    # Bounded input; no arbitrary file/output writes.
    if args.snapshot.stat().st_size > 1_000_000:
        parser.error("snapshot size exceeds 1MB")
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        print(json.dumps(diagnose(snapshot), sort_keys=True, indent=2))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"zSentinel evidence rejected: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
