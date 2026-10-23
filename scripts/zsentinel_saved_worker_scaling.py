#!/usr/bin/env python3
"""Turn a saved zCloud worker-scaling JSON receipt into a private offline handoff.

This tool is deliberately read-only with respect to the source report and has
no network, queue, worker, scheduler, merge, dispatch, or deployment behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_INPUT_BYTES = 1_048_576
MAX_OUTPUT_BYTES = 1_048_576
MAX_PROJECTS = 64
MAX_WORKERS_PER_PROJECT = 64
MAX_LIST_ITEMS = 64
STALE_AFTER_SECONDS = 6 * 60 * 60
MAX_FUTURE_SKEW_SECONDS = 120
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SOURCE_STATUSES = {"observed", "partial", "unavailable"}
COVERAGE_STATUSES = {"observed", "partial", "unavailable", "unknown"}
HEARTBEAT_STATES = {"fresh", "stale", "future", "unknown", "no_heartbeat"}
ASSESSMENT_STATES = {
    "insufficient_data", "single_worker", "diminishing_returns",
    "useful_scaling", "inconclusive",
}


def _private_directory(path: Path) -> int:
    """Open an existing owner-private directory without following symlinks."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("directory_path_must_be_absolute_and_normal")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("directory_unavailable") from exc
    if resolved != path:
        raise ValueError("symlink_directory_path_refused")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise ValueError("directory_unavailable") from exc
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise ValueError("directory_not_owner_private")
    return fd


def _read_private_source(path: Path) -> tuple[bytes, str]:
    if not path.is_absolute() or ".." in path.parts or not path.name:
        raise ValueError("input_path_must_be_absolute_and_normal")
    parent_fd = _private_directory(path.parent)
    fd = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(path.name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise ValueError("input_unavailable_or_symlink") from exc
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("input_not_regular")
        if before.st_uid != os.getuid() or before.st_mode & 0o077:
            raise ValueError("input_not_owner_private")
        if before.st_size < 0 or before.st_size > MAX_INPUT_BYTES:
            raise ValueError("input_too_large")
        chunks = []
        remaining = MAX_INPUT_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        try:
            named = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise ValueError("input_identity_changed") from exc
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        identity_named = (named.st_dev, named.st_ino, named.st_size, named.st_mtime_ns, named.st_ctime_ns)
        if identity_before != identity_after or identity_before != identity_named:
            raise ValueError("input_identity_changed")
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("input_too_large")
        return raw, hashlib.sha256(raw).hexdigest()
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("nonfinite_json_number")


def _timestamp(value) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp_missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("timestamp_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp_timezone_missing")
    return parsed.astimezone(timezone.utc)


def _safe_text(value, limit=300) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = "".join(ch if ch in "\t " or ord(ch) >= 32 else " " for ch in value)
    return " ".join(cleaned.split())[:limit]


def _native_int(value, *, minimum=0, maximum=1_000_000):
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError("invalid_integer_field")
    return value


def _number(value, *, minimum=None, maximum=None):
    if type(value) not in (int, float):
        raise ValueError("invalid_numeric_field")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("invalid_numeric_field") from exc
    if not math.isfinite(number):
        raise ValueError("invalid_numeric_field")
    if minimum is not None and number < minimum:
        raise ValueError("numeric_field_below_minimum")
    if maximum is not None and number > maximum:
        raise ValueError("numeric_field_above_maximum")
    return round(number, 3)


def _slot_list(value):
    if not isinstance(value, list) or len(value) > MAX_LIST_ITEMS:
        raise ValueError("invalid_slot_list")
    slots = [_native_int(item, minimum=1) for item in value]
    if len(slots) != len(set(slots)):
        raise ValueError("duplicate_worker_slot")
    return sorted(slots)


def _metric_snapshot(worker):
    if not isinstance(worker, dict):
        raise ValueError("invalid_worker_record")
    slot = _native_int(worker.get("worker_slot"), minimum=1)
    metrics = worker.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("invalid_worker_metrics")
    freshness = metrics.get("heartbeat_freshness", "unknown")
    if not isinstance(freshness, str) or freshness not in HEARTBEAT_STATES:
        freshness = "unknown"
    heartbeat = metrics.get("last_heartbeat")
    if heartbeat is not None:
        try:
            heartbeat = _timestamp(heartbeat).isoformat()
        except ValueError:
            heartbeat = None
            freshness = "unknown"
    age = metrics.get("last_heartbeat_age_seconds")
    if age is not None:
        try:
            age = _native_int(age)
        except ValueError:
            age = None
            freshness = "unknown"
    throughput = metrics.get("throughput_per_observed_hour")
    if throughput is not None:
        throughput = _number(throughput, minimum=0)
    return {
        "worker_slot": slot,
        "observed_seconds": _native_int(metrics.get("observed_seconds", 0)),
        "completed_generations": _native_int(metrics.get("completed_generations", 0)),
        "throughput_per_observed_hour": throughput,
        "working_pct": _number(metrics.get("working_pct", 0), minimum=0, maximum=100),
        "idle_pct": _number(metrics.get("idle_pct", 0), minimum=0, maximum=100),
        "blocked_pct": _number(metrics.get("blocked_pct", 0), minimum=0, maximum=100),
        "blocked_events": _native_int(metrics.get("blocked_events", 0)),
        "last_heartbeat": heartbeat,
        "last_heartbeat_age_seconds": age,
        "heartbeat_freshness": freshness,
    }


def _project_snapshot(project):
    if not isinstance(project, dict):
        raise ValueError("invalid_project_record")
    project_id = project.get("project_id")
    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError("invalid_project_id")
    desired = project.get("desired_workers")
    if desired is not None:
        desired = _native_int(desired, minimum=1)
    desired_source = project.get("desired_workers_source", "legacy_unknown")
    if not isinstance(desired_source, str) or desired_source not in {"observed", "unavailable", "legacy_unknown"}:
        desired_source = "legacy_unknown"
    if desired is None and desired_source == "observed":
        desired_source = "unavailable"
    elif desired is not None and desired_source == "unavailable":
        desired = None

    coverage = project.get("worker_coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    coverage_state = _safe_text(coverage.get("state"), 80) or "unknown"
    if coverage_state not in {
        "complete_fresh", "missing_worker_slots", "stale_heartbeats",
        "unknown_heartbeat_freshness", "desired_worker_count_out_of_range",
        "desired_worker_count_unknown",
    }:
        coverage_state = "unknown"
    observed_slots = _slot_list(project.get("observed_worker_slots", []))
    missing_slots = _slot_list(coverage.get("missing_worker_slots", []))
    stale_slots = _slot_list(coverage.get("stale_worker_slots", []))
    unknown_slots = _slot_list(coverage.get("unknown_heartbeat_worker_slots", []))

    assessment = project.get("assessment")
    assessment = assessment if isinstance(assessment, dict) else {}
    assessment_state = assessment.get("state")
    if not isinstance(assessment_state, str) or assessment_state not in ASSESSMENT_STATES:
        assessment_state = "insufficient_data"
    assessment_reason = _safe_text(assessment.get("reason"), 500) or "Geen onderbouwde schaalbeoordeling beschikbaar."

    duplicate = project.get("duplicate_work_prevented")
    if duplicate is not None:
        duplicate = _native_int(duplicate)
    conflict_source = project.get("claim_conflict_source_status", "unknown")
    if not isinstance(conflict_source, str) or conflict_source not in COVERAGE_STATUSES:
        conflict_source = "unknown"

    workers = project.get("workers", [])
    if not isinstance(workers, list) or len(workers) > MAX_WORKERS_PER_PROJECT:
        raise ValueError("worker_cardinality_exceeded")
    worker_rows = [_metric_snapshot(item) for item in workers]
    worker_slots = [item["worker_slot"] for item in worker_rows]
    if len(worker_slots) != len(set(worker_slots)):
        raise ValueError("duplicate_worker_slot")
    if not set(worker_slots).issubset(set(observed_slots)):
        raise ValueError("worker_slots_conflict_with_project_observation")

    return {
        "project_id": project_id,
        "desired_workers": desired,
        "desired_workers_source": desired_source,
        "observed_worker_slots": observed_slots,
        "worker_coverage_state": coverage_state,
        "missing_worker_slots": missing_slots,
        "stale_worker_slots": stale_slots,
        "unknown_heartbeat_worker_slots": unknown_slots,
        "assessment_state": assessment_state,
        "assessment_reason": assessment_reason,
        "duplicate_work_prevented": duplicate,
        "claim_conflict_source_status": conflict_source,
        "workers": sorted(worker_rows, key=lambda item: item["worker_slot"]),
    }


def normalize_report(data, raw_sha256, now=None):
    if not isinstance(data, dict):
        raise ValueError("report_root_not_object")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated = _timestamp(data.get("generated_at"))
    raw_age = (now - generated).total_seconds()
    if raw_age < -MAX_FUTURE_SKEW_SECONDS:
        freshness = "future"
        age_seconds = None
    elif raw_age < 0:
        freshness = "clock_skew"
        age_seconds = 0
    elif raw_age > STALE_AFTER_SECONDS:
        freshness = "stale"
        age_seconds = round(raw_age)
    else:
        freshness = "fresh"
        age_seconds = round(raw_age)

    status = data.get("source_status")
    if status is None:
        status = "legacy_unknown"
        source_complete = False
        source_reasons = ["legacy_report_without_source_coverage"]
        coverage = {"runner_events": "unknown", "runner_targets": "unknown", "alerts": "unknown"}
    else:
        if not isinstance(status, str) or status not in SOURCE_STATUSES:
            raise ValueError("invalid_source_status")
        source_complete = data.get("source_complete", False)
        if type(source_complete) is not bool:
            raise ValueError("invalid_source_complete")
        raw_reasons = data.get("source_reasons", [])
        if not isinstance(raw_reasons, list) or len(raw_reasons) > 20:
            raise ValueError("invalid_source_reasons")
        source_reasons = [_safe_text(item, 100) for item in raw_reasons]
        if any(not item for item in source_reasons):
            raise ValueError("invalid_source_reason")
        raw_coverage = data.get("source_coverage", {})
        if not isinstance(raw_coverage, dict):
            raise ValueError("invalid_source_coverage")
        coverage = {}
        for key in ("runner_events", "runner_targets", "alerts"):
            value = raw_coverage.get(key, "unknown")
            coverage[key] = value if isinstance(value, str) and value in COVERAGE_STATUSES else "unknown"
        if status == "observed" and not source_complete:
            status = "partial"
            source_reasons.append("observed_status_without_complete_source")
        if status in {"partial", "unavailable"} and source_complete:
            source_complete = False
            source_reasons.append("source_completeness_conflict")
        if status == "observed" and source_reasons:
            status = "partial"
            source_complete = False
            source_reasons.append("observed_status_has_source_reasons")
        if status == "observed" and source_complete and any(
            coverage.get(key) != "observed" for key in ("runner_events", "runner_targets", "alerts")
        ):
            status = "partial"
            source_complete = False
            source_reasons.append("source_coverage_incomplete")
        if status == "partial" and not source_reasons:
            source_reasons.append("source_incomplete")
        if status == "unavailable" and not source_reasons:
            source_reasons.append("source_unavailable")

    rows = data.get("telemetry_rows")
    if rows is not None:
        rows = _native_int(rows)
    window_empty = data.get("window_empty")
    if window_empty is not None and type(window_empty) is not bool:
        raise ValueError("invalid_window_empty")
    if rows is not None and window_empty is not None and window_empty != (rows == 0):
        if status == "observed":
            status = "partial"
        source_complete = False
        source_reasons.append("window_empty_conflict")
    if status == "unavailable" and rows not in (None, 0):
        source_reasons.append("unavailable_source_has_telemetry_rows")

    raw_projects = data.get("projects", [])
    if not isinstance(raw_projects, list) or len(raw_projects) > MAX_PROJECTS:
        raise ValueError("project_cardinality_exceeded")
    projects = [_project_snapshot(project) for project in raw_projects]
    project_ids = [project["project_id"] for project in projects]
    if len(project_ids) != len(set(project_ids)):
        raise ValueError("duplicate_project_id")

    if status == "unavailable" and projects:
        source_complete = False
        source_reasons.append("unavailable_source_contains_project_rows")

    for project in projects:
        project["assessment_context_complete"] = bool(
            freshness == "fresh"
            and coverage.get("runner_events") == "observed"
            and coverage.get("runner_targets") == "observed"
            and project["desired_workers_source"] == "observed"
            and project["worker_coverage_state"] == "complete_fresh"
        )

    return {
        "schema_version": "zsentinel-saved-worker-scaling-handoff-v1",
        "source_sha256": raw_sha256,
        "source_generated_at": generated.isoformat(),
        "source_age_seconds": age_seconds,
        "source_freshness": freshness,
        "source_status": status,
        "source_complete": source_complete,
        "source_reasons": sorted(set(source_reasons)),
        "source_coverage": coverage,
        "telemetry_rows": rows,
        "window_empty": window_empty,
        "projects": projects,
        "assessment_context_complete": bool(
            projects and all(project["assessment_context_complete"] for project in projects)
        ),
        "claim_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "dispatch_authorized": False,
        "scale_change_authorized": False,
        "active_claim_verified": False,
    }


def _md(value) -> str:
    text = _safe_text(str(value), 600)
    for char in ("\\", chr(96), "*", "_", "[", "]", "<", ">", "|", "#"):
        text = text.replace(char, "\\" + char)
    return text


def render_markdown(report):
    lines = [
        "# Saved worker-scaling handoff",
        "",
        f"- Source SHA-256: {report['source_sha256']}",
        f"- Source observation: {report['source_generated_at']}",
        f"- Source freshness: **{report['source_freshness']}**",
        f"- Source status: **{report['source_status']}** (complete: {str(report['source_complete']).lower()})",
        f"- Telemetry rows: {report['telemetry_rows']}; empty window: {report['window_empty']}",
        f"- Assessment context complete: {str(report['assessment_context_complete']).lower()}",
        "",
        "> Historical, read-only evidence only. This handoff does not prove a live worker/claim and never authorizes claim, dispatch, scaling, merge or deployment.",
        "",
        "## Source coverage",
        "",
    ]
    for key, value in report["source_coverage"].items():
        lines.append(f"- {key}: **{value}**")
    for reason in report["source_reasons"]:
        lines.append(f"- Source limitation: {_md(reason)}")
    if report["source_freshness"] != "fresh":
        lines.extend(["", "**Do not treat these metrics as current:** the saved source is stale or its clock is uncertain."])
    if not report["projects"]:
        lines.extend(["", "No project cards were present in this saved report. This is not proof that no workers exist."])
    for project in report["projects"]:
        lines.extend([
            "",
            f"## Project {_md(project['project_id'])}",
            "",
            f"- Desired workers: {_md(project['desired_workers'] if project['desired_workers'] is not None else 'unknown')} (source: {_md(project['desired_workers_source'])})",
            f"- Coverage: **{_md(project['worker_coverage_state'])}**",
            f"- Observed slots: {_md(', '.join(map(str, project['observed_worker_slots'])) or 'none')}",
            f"- Missing slots: {_md(', '.join(map(str, project['missing_worker_slots'])) or 'unknown/none reported')}",
            f"- Stale heartbeat slots: {_md(', '.join(map(str, project['stale_worker_slots'])) or 'none reported')}",
            f"- Unknown heartbeat slots: {_md(', '.join(map(str, project['unknown_heartbeat_worker_slots'])) or 'none reported')}",
            f"- Assessment: **{_md(project['assessment_state'])}** — {_md(project['assessment_reason'])}",
            f"- Assessment inputs complete and fresh: {str(project['assessment_context_complete']).lower()}",
            f"- Duplicate-work alerts: {_md(project['duplicate_work_prevented'] if project['duplicate_work_prevented'] is not None else 'unknown')} (source: {_md(project['claim_conflict_source_status'])})",
            "",
            "| Slot | Heartbeat | Age (s) | Observed (s) | Completed | Throughput/hour | Working % | Idle % | Blocked % |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
        ])
        if not project["workers"]:
            lines.append("| — | unknown | — | — | — | — | — | — | — |")
        for worker in project["workers"]:
            lines.append(
                f"| {worker['worker_slot']} | {worker['heartbeat_freshness']} | "
                f"{worker['last_heartbeat_age_seconds'] if worker['last_heartbeat_age_seconds'] is not None else '—'} | "
                f"{worker['observed_seconds']} | {worker['completed_generations']} | "
                f"{worker['throughput_per_observed_hour'] if worker['throughput_per_observed_hour'] is not None else '—'} | "
                f"{worker['working_pct']} | {worker['idle_pct']} | {worker['blocked_pct']} |"
            )
    lines.append("")
    return "\n".join(lines)


def render_html(report):
    esc = lambda value: html.escape(str(value), quote=True)
    coverage = "".join(
        f"<li><code>{esc(key)}</code>: <strong>{esc(value)}</strong></li>"
        for key, value in report["source_coverage"].items()
    )
    reasons = "".join(f"<li>{esc(reason)}</li>" for reason in report["source_reasons"])
    projects = []
    for project in report["projects"]:
        worker_rows = []
        for worker in project["workers"]:
            worker_rows.append(
                "<tr>"
                f"<td>{worker['worker_slot']}</td>"
                f"<td>{esc(worker['heartbeat_freshness'])}</td>"
                f"<td>{esc(worker['last_heartbeat_age_seconds'] if worker['last_heartbeat_age_seconds'] is not None else 'unknown')}</td>"
                f"<td>{worker['observed_seconds']}</td>"
                f"<td>{worker['completed_generations']}</td>"
                f"<td>{esc(worker['throughput_per_observed_hour'] if worker['throughput_per_observed_hour'] is not None else 'unknown')}</td>"
                f"<td>{worker['working_pct']}</td><td>{worker['idle_pct']}</td><td>{worker['blocked_pct']}</td>"
                "</tr>"
            )
        if not worker_rows:
            worker_rows.append("<tr><td colspan='9'>No worker rows in this saved report.</td></tr>")
        projects.append(
            "<section class='card'>"
            f"<h2>Project <code>{esc(project['project_id'])}</code></h2>"
            f"<p>Desired workers: <strong>{esc(project['desired_workers'] if project['desired_workers'] is not None else 'unknown')}</strong> "
            f"(source: {esc(project['desired_workers_source'])}) · Coverage: <strong>{esc(project['worker_coverage_state'])}</strong></p>"
            f"<p>Observed slots: {esc(', '.join(map(str, project['observed_worker_slots'])) or 'none')} · "
            f"Missing: {esc(', '.join(map(str, project['missing_worker_slots'])) or 'unknown/none reported')} · "
            f"Stale: {esc(', '.join(map(str, project['stale_worker_slots'])) or 'none reported')} · "
            f"Unknown heartbeat: {esc(', '.join(map(str, project['unknown_heartbeat_worker_slots'])) or 'none reported')}</p>"
            f"<p>Assessment: <strong>{esc(project['assessment_state'])}</strong> — {esc(project['assessment_reason'])}</p>"
            f"<p>Assessment inputs complete and fresh: <strong>{str(project['assessment_context_complete']).lower()}</strong> · "
            f"Duplicate-work alerts: <strong>{esc(project['duplicate_work_prevented'] if project['duplicate_work_prevented'] is not None else 'unknown')}</strong> "
            f"(source: {esc(project['claim_conflict_source_status'])})</p>"
            "<div class='table-wrap'><table><thead><tr><th>Slot</th><th>Heartbeat</th><th>Age (s)</th><th>Observed (s)</th><th>Completed</th><th>Throughput/h</th><th>Working %</th><th>Idle %</th><th>Blocked %</th></tr></thead>"
            f"<tbody>{''.join(worker_rows)}</tbody></table></div></section>"
        )
    warning = ""
    if report["source_freshness"] != "fresh" or report["source_status"] != "observed" or not report["source_complete"]:
        warning = "<p class='warning'>Source is stale, partial, unavailable, or legacy. Do not treat it as a current or complete fleet observation.</p>"
    if not projects:
        projects.append("<section class='card'><h2>No project cards</h2><p>An empty report is not proof that no workers exist.</p></section>")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Saved worker-scaling handoff</title>"
        "<style>body{font:16px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#182230}"
        ".card{border:1px solid #cbd5e1;border-radius:12px;padding:1rem;margin:1rem 0;overflow:hidden}"
        ".warning{background:#fff3cd;padding:1rem;border-radius:8px}table{border-collapse:collapse;width:100%;font-size:.9rem}"
        "th,td{text-align:left;border-bottom:1px solid #d8dee8;padding:.45rem}th{background:#f1f5f9}"
        ".table-wrap{overflow-x:auto}code{overflow-wrap:anywhere}li{margin:.25rem 0}</style></head><body>"
        "<h1>Saved worker-scaling handoff</h1>"
        f"<p>Source SHA-256: <code>{esc(report['source_sha256'])}</code></p>"
        f"<p>Observed at: <code>{esc(report['source_generated_at'])}</code> · Freshness: <strong>{esc(report['source_freshness'])}</strong></p>"
        f"<p>Source status: <strong>{esc(report['source_status'])}</strong> · Complete: <strong>{str(report['source_complete']).lower()}</strong> · "
        f"Telemetry rows: {esc(report['telemetry_rows'] if report['telemetry_rows'] is not None else 'unknown')} · "
        f"Empty window: {esc(report['window_empty'] if report['window_empty'] is not None else 'unknown')}</p>"
        f"<p>Assessment context complete and fresh: <strong>{str(report['assessment_context_complete']).lower()}</strong></p>"
        f"{warning}<h2>Source coverage</h2><ul>{coverage}{reasons}</ul>"
        "<p><strong>Read-only historical evidence:</strong> no live claim verification, dispatch, scaling action, merge or deployment authority.</p>"
        f"{''.join(projects)}</body></html>"
    )


def _write_private_output(path: Path, content: bytes):
    if not path.is_absolute() or ".." in path.parts or not path.name:
        raise ValueError("output_path_must_be_absolute_and_normal")
    if len(content) > MAX_OUTPUT_BYTES:
        raise ValueError("output_too_large")
    parent_fd = _private_directory(path.parent)
    fd = None
    created_identity = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise ValueError("output_already_exists") from exc
        except OSError as exc:
            raise ValueError("output_create_failed") from exc
        info = os.fstat(fd)
        created_identity = (info.st_dev, info.st_ino)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("output_not_private_regular_file")
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise ValueError("output_write_incomplete")
            view = view[written:]
        os.fsync(fd)
        final_info = os.fstat(fd)
        if (final_info.st_dev, final_info.st_ino) != created_identity or final_info.st_mode & 0o077:
            raise ValueError("output_identity_changed")
        os.fsync(parent_fd)
    except Exception:
        if created_identity is not None:
            try:
                current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == created_identity:
                    os.unlink(path.name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
            except OSError:
                pass
        raise
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a private offline handoff from a saved zCloud worker-scaling report."
    )
    parser.add_argument("--input", required=True, type=Path, help="absolute path to an owner-private JSON report")
    parser.add_argument("--output", required=True, type=Path, help="new file in an owner-private directory; never overwritten")
    parser.add_argument("--format", choices=("html", "markdown", "json"), default="html")
    args = parser.parse_args()
    try:
        raw, digest = _read_private_source(args.input)
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        report = normalize_report(data, digest)
        if args.format == "html":
            rendered = render_html(report)
        elif args.format == "markdown":
            rendered = render_markdown(report)
        else:
            rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        _write_private_output(args.output, rendered.encode("utf-8"))
    except ValueError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, json.JSONDecodeError):
        print("error: input_or_output_unavailable", file=sys.stderr)
        return 2
    print(
        "Saved private worker-scaling handoff "
        f"(sha256={digest}, format={args.format}, projects={len(report['projects'])}, "
        f"source_status={report['source_status']}, freshness={report['source_freshness']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
