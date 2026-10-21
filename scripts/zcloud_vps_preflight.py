#!/usr/bin/env python3
"""Read-only VPS deployment preflight. No service, Git, API or queue mutations."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from html import escape
from contextlib import closing
import json
import io
import tempfile
import hashlib
import os
import stat
import re
import sqlite3
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from zcloud_vps_host_pressure import host_pressure_snapshot, pressure_findings
from zcloud_vps_pressure_trend import compare_host_pressure
from zcloud_vps_preflight_markdown import render as render_markdown

RUNNER_UNIT = "actions.runner.Zennay-zCloud.zcloud-vps-1.service"
TRAINING_FREE_RESERVE_BYTES = 10 * 1024 ** 3
MAX_PREVIOUS_REPORT_AGE_SECONDS = 24 * 60 * 60


def command(*argv: str, cwd: Path | None = None, timeout: int = 8) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            env={**{key: value for key, value in os.environ.items() if not key.startswith("GIT_")}, "GIT_OPTIONAL_LOCKS": "0"},
            check=False,
        )
        return completed.returncode, completed.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def meminfo() -> dict[str, int]:
    result = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, sep, rest = line.partition(":")
            if sep and key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                result[key] = int(rest.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return {}
    return result


def listener_pids() -> list[int]:
    """Only detect the dedicated zCloud Listener; never expose raw process arguments."""
    result = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return result
    for proc in entries:
        if not proc.name.isdigit():
            continue
        try:
            if (proc / "comm").read_text().strip() != "Runner.Listener":
                continue
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            program = argv[0].decode("utf-8", "replace").lower() if argv else ""
            if ("zcloud" in program or "zcloud-vps-1" in program) and program.endswith("/runner.listener"):
                result.append(int(proc.name))
        except (OSError, ValueError):
            continue
    return sorted(result)


def dedicated_worker_pids() -> list[int]:
    """Observe zCloud Runner.Worker processes, without emitting their arguments."""
    found: list[int] = []
    try:
        processes = list(Path("/proc").iterdir())
    except OSError:
        return found
    for proc in processes:
        if not proc.name.isdigit():
            continue
        try:
            if (proc / "comm").read_text().strip() != "Runner.Worker":
                continue
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            program = argv[0].decode("utf-8", "replace").lower() if argv else ""
            if ("zcloud" in program or "zcloud-vps-1" in program) and program.endswith("/runner.worker"):
                found.append(int(proc.name))
        except (OSError, ValueError):
            continue
    return sorted(found)


def service_observation() -> dict[str, object]:
    """Observe both systemd managers without confusing a missing unit with idle.

    Always identify which manager supplied a loaded unit. Contradictory
    manager states are a blocked release, not a preferred answer.
    """
    observed: dict[str, str] = {}
    for manager, prefix in (("user", ("systemctl", "--user")), ("system", ("systemctl",))):
        rc, output = command(*prefix, "show", RUNNER_UNIT,
                             "--property=LoadState", "--property=ActiveState", timeout=4)
        if rc:
            continue
        props = {}
        for line in output.splitlines():
            key, sep, value = line.partition("=")
            if sep and key in {"LoadState", "ActiveState"} and key not in props:
                props[key] = value.strip()
        if props.get("LoadState") not in {"loaded", "generated", "transient"}:
            continue
        state = props.get("ActiveState")
        if state in {"active", "inactive", "failed", "activating", "deactivating"}:
            observed[manager] = state
    unique = set(observed.values())
    # Even two matching "active" units can compete for the same runner
    # registration, so duplicate loaded manager ownership is ambiguous.
    state = ("ambiguous" if len(observed) > 1 else next(iter(unique)) if unique else "unavailable")
    return {"state": state, "manager": next(iter(observed)) if len(observed) == 1 else
            "both" if len(observed) == 2 else "unverified",
            "loaded_managers": sorted(observed)}


def service_state() -> str:
    """Backwards-compatible state read; detailed reports use service_observation."""
    return str(service_observation()["state"])


def optional_storage_inventory(root: Path) -> dict:
    """Budgeted, opt-in du summary of known locations; never searches or deletes files."""
    home = root.parent
    targets = (
        ("project_checkout", root),
        ("zcloud_runner_install", home / "actions-runner-zcloud"),
        ("user_cache", home / ".cache"),
        ("gradle_cache", home / ".gradle"),
        ("raiseai_build", home / "raiseai-build"),
    )
    measured = []
    incomplete = []
    for label, path in targets:
        # Missing and symlinked roots are unobserved, never proven zero.
        if not path.exists() or path.is_symlink():
            incomplete.append(label)
            continue
        code, output = command("du", "-sx", "-B1", "--", str(path), timeout=3)
        first = output.split(maxsplit=1)[0] if output else ""
        if code != 0 or not first.isdigit():
            incomplete.append(label)
            continue
        measured.append({"category": label, "size_gib": round(int(first) / (1024 ** 3), 2)})
    return {"mode": "bounded_opt_in", "measured": sorted(measured, key=lambda row: -row["size_gib"]),
            "incomplete": incomplete, "scope": "five known directories; excludes all other paths",
            "cleanup_authorized": False}



def archive_mount_readiness(mount_path: Path = Path("/home/ubuntu/VPS-Archief")) -> dict:
    """Observe one configured archive mount; never probe by writing a file.

    A FUSE mount can advertise rw even when its rclone client was started
    --read-only. Therefore "mounted" never proves it can accept new data.
    """
    outcome = {
        "mount": str(mount_path),
        "state": "unknown",
        "mount_type": None,
        "rclone_read_only": None,
        "archive_write_authorized": False,
        "transfer_performed": False,
    }
    if mount_path.is_symlink():
        outcome["state"] = "unsafe_symlink"
        return outcome
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        outcome["state"] = "mountinfo_unavailable"
        return outcome
    matched = None
    for line in lines:
        left, separator, right = line.partition(" - ")
        fields, detail = left.split(), right.split()
        if not separator or len(fields) < 6 or len(detail) < 3:
            continue
        # This operator-owned path has no mountinfo escapes/spaces.
        if fields[4] == str(mount_path):
            matched = (fields, detail)
            break
    if matched is None:
        outcome["state"] = "not_mounted"
        return outcome
    fields, detail = matched
    outcome["mount_type"] = detail[0]
    options = set(fields[5].split(",")) | set(detail[2].split(","))
    if "ro" in options:
        outcome["state"] = "mount_declares_read_only"
        return outcome

    # Detect an explicit rclone --read-only client option, without outputting
    # process arguments or assuming absence of the flag proves writability.
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                argv = (proc / "cmdline").read_bytes().split(b"\0")
            except (OSError, PermissionError):
                continue
            if (argv and b"rclone" in argv[0].lower() and b"mount" in argv
                    and os.fsencode(str(mount_path)) in argv
                    and (b"--read-only" in argv or b"--read-only=true" in argv)):
                outcome["rclone_read_only"] = True
                outcome["state"] = "rclone_client_read_only"
                return outcome
    except OSError:
        pass
    outcome["state"] = "mounted_write_unverified"
    return outcome


def local_source_scope(root: Path) -> dict:
    """Opaque, advisory boot+checkout binding for private saved observations.

    A digest of a locally observed boot UUID is not an attestation: an
    operator-supplied prior file can still be forged. Never emit the raw
    host UUID, machine-id, absolute checkout path or process arguments.
    """
    source = {"state": "unverified", "fingerprint_sha256": None,
              "attested": False, "release_authorized": False}
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii").strip().lower()
        if not re.fullmatch(
                r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", boot):
            return source
        checkout = os.fsencode(root.resolve(strict=True))
        digest = hashlib.sha256(
            boot.encode("ascii") + b"\\0" + checkout + b"\\0" +
            RUNNER_UNIT.encode("ascii")).hexdigest()
    except (OSError, UnicodeError, ValueError, RuntimeError):
        return source
    return {"state": "local_boot_checkout_observed",
            "fingerprint_sha256": digest,
            "attested": False, "release_authorized": False}



def worker_safety_snapshot(root: Path) -> dict:
    """Advisory live workload snapshot from a strictly read-only SQLite handle.

    No attempt is made to correlate browser heartbeat with real text progress.
    Counts cannot grant safe-idle, drain, service restart or release approval.
    """
    evidence = {
        "state": "unknown", "source": "sqlite_mode_ro", "queued_claims": None,
        "durable_claims": None, "fresh_generating_slots": None,
        "fresh_observed_slots": None, "events_sample_complete": False,
        "safe_idle_verified": False, "worker_interruption_authorized": False,
        "read_performed": False,
    }
    path = root / "history.db"
    try:
        if path.is_symlink() or not path.is_file():
            evidence["state"] = "database_unavailable"
            return evidence
        # A separate read-only connection prevents accidental schema setup,
        # queue reconciliation or mutating GET side effects.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=2000")
            conn.execute("BEGIN")
            claims = conn.execute(
                "SELECT COUNT(*) FROM portfolio_queue WHERE status='claimed'"
            ).fetchone()[0]
            ownership = conn.execute(
                "SELECT COUNT(*) FROM task_claims"
            ).fetchone()[0]
            recent = conn.execute(
                "SELECT project_id,worker_slot,ts,generating "
                "FROM runner_events ORDER BY id DESC LIMIT 301"
            ).fetchall()
            conn.execute("ROLLBACK")
        evidence["read_performed"] = True
        evidence["queued_claims"] = claims
        evidence["durable_claims"] = ownership
        latest = {}
        now_utc = datetime.now(timezone.utc)
        for project, slot, event_time, generating in recent[:300]:
            key = (project, slot)
            if key in latest:
                continue
            if not isinstance(event_time, str):
                evidence["state"] = "invalid_event_clock"
                return evidence
            try:
                at = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                if at.tzinfo is None or at.utcoffset() is None:
                    raise ValueError("naive")
                age = (now_utc - at).total_seconds()
            except (TypeError, ValueError, OverflowError):
                evidence["state"] = "invalid_event_clock"
                return evidence
            if age < -30:
                evidence["state"] = "future_event_clock"
                return evidence
            latest[key] = (age, generating)
        fresh = [v for v in latest.values() if -30 <= v[0] <= 300]
        evidence["fresh_observed_slots"] = len(fresh)
        evidence["fresh_generating_slots"] = sum(v[1] == 1 for v in fresh)
        # A sample is complete only when the first excluded event is old.
        # Even a complete browser sample cannot prove independent safe idle.
        if len(recent) <= 300:
            evidence["events_sample_complete"] = True
        else:
            try:
                oldest_included = datetime.fromisoformat(
                    recent[299][2].replace("Z", "+00:00"))
                oldest_excluded = datetime.fromisoformat(
                    recent[300][2].replace("Z", "+00:00"))
                evidence["events_sample_complete"] = (
                    oldest_included.tzinfo is not None
                    and oldest_excluded.tzinfo is not None
                    and now_utc - oldest_excluded > timedelta(seconds=300))
            except (TypeError, ValueError, OverflowError, AttributeError):
                evidence["events_sample_complete"] = False
        if not evidence["events_sample_complete"]:
            evidence["state"] = "event_window_incomplete"
        elif claims or ownership or evidence["fresh_generating_slots"]:
            evidence["state"] = "active_work_observed"
        else:
            evidence["state"] = "no_active_evidence_unverified"
        return evidence
    except (OSError, sqlite3.Error, TypeError, ValueError):
        evidence["state"] = "database_unavailable"
        return evidence


def actionable_recovery(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    """Human-readable, non-authorizing recovery ordering; never executes the actions."""
    codes = {entry["code"] for entry in findings}
    recommendations: list[dict[str, str]] = []

    def recommend(code: str, owner: str, why: str, action: str) -> None:
        recommendations.append({
            "code": code, "owner": owner, "reason": why, "next_action": action,
            "changes_host": "no", "authorization_required": "yes",
        })

    if codes & {"LOW_DISK", "TRAINING_DISK_RESERVE_BELOW", "SWAP_EXHAUSTED", "LOW_AVAILABLE_MEMORY", "MEMORY_PRESSURE", "SWAP_TELEMETRY_INCOMPLETE",
                "MEMORY_UNOBSERVABLE", "DISK_UNOBSERVABLE", "STORAGE_TRIAGE_PARTIAL"}:
        recommend("CAPACITY_FIRST", "VPS resource owner",
                  "Insufficient or unverified resource headroom can cause repeat OOM crashes or incomplete releases.",
                  "Inspect disk/cache ownership and memory pressure read-only; agree on safe cleanup or resizing before a controlled runner recovery. Never automatically delete or clear swap.")
    if codes & {"HOST_CPU_SATURATED", "HOST_CPU_BUSY", "HOST_MEMORY_STALL",
                "HOST_IO_STALL", "HOST_PRESSURE_INCOMPLETE"}:
        recommend("HOST_CONCURRENCY_OWNER", "Shared VPS training and runner scheduler owner",
                  "Read-only CPU or PSI evidence shows contention or an incomplete host sample, not attribution to a specific worker.",
                  "Preserve live training and jobs. Inspect who controls future concurrent admissions and resource budgets via the existing owner before scheduling heavy new work. Do not kill, renice, pause or restart workers from this report.")
    if codes & {"ZCLOUD_RUNNER_MISSING", "RUNNER_UNIT_NOT_ACTIVE", "RUNNER_UNIT_UNOBSERVABLE", "RUNNER_UNIT_AMBIGUOUS", "RUNNER_UNIT_TRANSITIONING", "RUNNER_LISTENER_DUPLICATED", "RUNNER_WORKER_PROCESS_OBSERVED"}:
        recommend("RUNNER_OWNER", "zCloud Actions runner owner (#1326)",
                  "The dedicated listener/registered runner may be unavailable independently of other project runners.",
                  "Inspect the dedicated service and assigned jobs with the authorized runner owner. Restore only under the existing idle and account-identity gates; independently confirm a job was assigned.")
    if codes & {"TRACKED_CHANGES", "UNTRACKED_FILES", "NOT_MAIN", "CHECKOUT_STATUS_UNAVAILABLE"}:
        recommend("WORKTREE_RECONCILE", "zCloud production checkout owner",
                  "Uncommitted work and untracked owner files must not be replaced by a deployment or pull.",
                  "Identify current active edits and ownership; reconcile via the existing serialized release process. Never reset, clean or force-pull this checkout.")
    if codes & {"MAIN_SHA_MISMATCH", "REMOTE_MAIN_UNBOUND", "CHECKOUT_UNAVAILABLE"}:
        recommend("SOURCE_PROVENANCE", "zCloud release owner",
                  "Exact remote-main provenance has not been established for this host.",
                  "Obtain a fresh independent protected-main SHA and compare. Require existing exact-head CI, review and release admission before deployment.")
    if "ARCHIVE_COLD_TIER_NOT_READY" in codes or "ARCHIVE_WRITE_UNVERIFIED" in codes:
        recommend("ARCHIVE_ROUTE_OWNER", "VPS archive / storage owner",
                  "An external mount is not proof that writes or safe cleanup are possible.",
                  "Verify encrypted archival destination rights and owner authorization separately; never delete source data before remote checksum verification.")
    if codes & {"ACTIVE_WORK_REQUIRES_DRAIN", "WORKER_EVIDENCE_INCOMPLETE",
                "WORKER_IDLE_NOT_PROVEN"}:
        recommend("WORKER_SAFE_IDLE_OWNER", "Serialized zCloud worker/release owner (#580/#1089)",
                  "Current queue claims or browser-reported generation may still be active, or monitoring is incomplete.",
                  "Preserve active claims and browser sessions; use existing owner-gated drain and independently confirmed idle evidence before a release. Do not kill workers.")
    if not recommendations:
        recommend("EXTERNAL_RELEASE_GATES", "zCloud release owner",
                  "A local read-only preflight cannot authorize a production release.",
                  "Verify GitHub branch protection, exact-head CI, independent review, serialized writer lock, runner assignment and live acceptance separately.")
    return recommendations


# Existing recovery priorities grouped by the exact immutable finding-code families.
# Human owner handoff is derived only from current observation and validated
# previous codes; it never decides releases, takes a claim or invokes recovery.
RECOVERY_FAMILIES = {
    "CAPACITY_FIRST": frozenset({
        "LOW_DISK", "TRAINING_DISK_RESERVE_BELOW", "SWAP_EXHAUSTED",
        "LOW_AVAILABLE_MEMORY", "MEMORY_PRESSURE", "SWAP_TELEMETRY_INCOMPLETE",
        "MEMORY_UNOBSERVABLE", "DISK_UNOBSERVABLE", "STORAGE_TRIAGE_PARTIAL"}),
    "HOST_CONCURRENCY_OWNER": frozenset({
        "HOST_CPU_SATURATED", "HOST_CPU_BUSY", "HOST_MEMORY_STALL",
        "HOST_IO_STALL", "HOST_PRESSURE_INCOMPLETE"}),
    "RUNNER_OWNER": frozenset({
        "ZCLOUD_RUNNER_MISSING", "RUNNER_UNIT_NOT_ACTIVE",
        "RUNNER_UNIT_UNOBSERVABLE", "RUNNER_UNIT_AMBIGUOUS",
        "RUNNER_UNIT_TRANSITIONING", "RUNNER_LISTENER_DUPLICATED",
        "RUNNER_WORKER_PROCESS_OBSERVED"}),
    "WORKTREE_RECONCILE": frozenset({
        "TRACKED_CHANGES", "UNTRACKED_FILES", "NOT_MAIN",
        "CHECKOUT_STATUS_UNAVAILABLE"}),
    "SOURCE_PROVENANCE": frozenset({
        "MAIN_SHA_MISMATCH", "REMOTE_MAIN_UNBOUND", "CHECKOUT_UNAVAILABLE"}),
    "ARCHIVE_ROUTE_OWNER": frozenset({
        "ARCHIVE_COLD_TIER_NOT_READY", "ARCHIVE_WRITE_UNVERIFIED"}),
    "WORKER_SAFE_IDLE_OWNER": frozenset({
        "ACTIVE_WORK_REQUIRES_DRAIN", "WORKER_EVIDENCE_INCOMPLETE",
        "WORKER_IDLE_NOT_PROVEN"}),
}


def operator_handoff(report: dict) -> list[dict[str, object]]:
    """Bounded, source-derived problem/action/status cards; always advisory.

    An omitted optional probe is not proof of recovery, and old snapshot notes
    are never read into the cards. Each card names the current responsible
    owner instead of attempting an automatic runner/worker recovery.
    """
    current = {row["code"]: row["severity"] for row in report["findings"]}
    comparison = report.get("comparison") or {}
    newly_observed = set(comparison.get("new_findings", []))
    persisted = set(comparison.get("still_present", []))
    escalated = {row["code"] for row in comparison.get("severity_changes", [])
                 if row["current"] == "block"}
    unknown_recovery = set(comparison.get("recovery_unverified_findings", []))
    stale_comparison = comparison.get("resolution_credit_allowed") is False
    cards: list[dict[str, object]] = []
    if comparison and stale_comparison:
        cards.append({
            "code": "BASELINE_SCOPE_REVIEW",
            "owner": "VPS resource / serialized release owner",
            "problem": "A prior snapshot cannot establish current recovery when its "
                       "boot/checkout source differs, is unknown, or is too old.",
            "action": "Obtain a new same-boot read-only source observation with the "
                      "responsible owner. Preserve prior evidence; do not infer "
                      "service recovery or authorize a release.",
            "status": "recovery_unverified",
            "trend": comparison.get("snapshot_freshness", "source_unverified"),
            "finding_codes": [],
            "recovery_performed": False,
            "release_authorized": False,
        })

    for recommendation in report["recovery_priorities"][:8]:
        family = RECOVERY_FAMILIES.get(recommendation["code"], frozenset())
        active = sorted(family & current.keys())
        status = ("owner_release_gate" if recommendation["code"] == "EXTERNAL_RELEASE_GATES"
                  else "active_blocker" if any(current[x] == "block" for x in active)
                  else "active_warning" if active else "observation_incomplete")
        if stale_comparison:
            trend = comparison.get("snapshot_freshness", "no_resolution_credit")
        elif active and family & escalated:
            trend = "escalated_to_blocker"
        elif active and family & newly_observed:
            trend = "newly_observed"
        elif active and family & persisted:
            trend = "still_observed"
        else:
            trend = "previous_comparison_unavailable" if not comparison else "no_confirmed_change"
        cards.append({
            "code": recommendation["code"],
            "owner": recommendation["owner"],
            "problem": recommendation["reason"],
            "action": recommendation["next_action"],
            "status": status,
            "trend": trend,
            "finding_codes": active,
            "recovery_performed": False,
            "release_authorized": False,
        })
    # Explicitly preserve vanished findings whose sources or authority cannot
    # independently prove recovery. Never label them fixed or omit them.
    for family_name, codes in RECOVERY_FAMILIES.items():
        missing = sorted(codes & unknown_recovery)
        if not missing:
            continue
        cards.append({
            "code": family_name + "_RECOVERY_UNVERIFIED",
            "owner": "Original incident owner / serialized release owner",
            "problem": "Prior source blocker has not been independently re-observed or resolved.",
            "action": "Repeat the same permitted read-only source observation and obtain original owner evidence; do not change services, queues or release state.",
            "status": "recovery_unverified",
            "trend": "source_missing_or_not_authoritative",
            "finding_codes": missing,
            "recovery_performed": False,
            "release_authorized": False,
        })
    # A future source can add an incident code before its owner mapping is
    # updated. Do not silently drop that historical unknown from the handoff.
    covered = set().union(*RECOVERY_FAMILIES.values())
    unmatched = sorted(unknown_recovery - covered)
    if unmatched:
        cards.append({
            "code": "UNMAPPED_RECOVERY_UNVERIFIED",
            "owner": "Serialized release owner / original issue owner",
            "problem": "Previously observed source findings lack verified recovery evidence or an existing specialized owner route.",
            "action": "Inspect the original finding with its accountable owner and reproduce only the permitted read-only observation. Do not infer safe release.",
            "status": "recovery_unverified",
            "trend": "source_missing_or_unmapped",
            "finding_codes": unmatched[:64],
            "recovery_performed": False,
            "release_authorized": False,
        })
    return cards[:16]


def measure(root: Path, expected_main_sha: str | None = None, disk_hotspots: bool = False, archive_readiness: bool = False, worker_safety: bool = False, host_pressure: bool = False) -> dict:
    findings: list[dict[str, str]] = []
    def mark(code: str, severity: str, detail: str) -> None:
        findings.append({"code": code, "severity": severity, "detail": detail})

    git_rc, branch = command("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    sha_rc, sha = command("git", "rev-parse", "HEAD", cwd=root)
    if git_rc or sha_rc or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        mark("CHECKOUT_UNAVAILABLE", "block", "Cannot establish local Git checkout identity.")
        branch, sha = "unknown", None
    if expected_main_sha is None:
        mark("REMOTE_MAIN_UNBOUND", "warn", "No independently verified current-main SHA supplied.")
    elif sha != expected_main_sha:
        mark("MAIN_SHA_MISMATCH", "block", "Local checkout HEAD differs from supplied main SHA.")
    status_rc, tracked = command("git", "status", "--porcelain=v1", "--untracked-files=no", cwd=root)
    untracked_rc, untracked = command("git", "ls-files", "--others", "--exclude-standard", cwd=root)
    tracked_count = len(tracked.splitlines()) if status_rc == 0 and tracked else 0
    untracked_count = len(untracked.splitlines()) if untracked_rc == 0 and untracked else 0
    if status_rc != 0 or untracked_rc != 0:
        mark("CHECKOUT_STATUS_UNAVAILABLE", "block", "Cannot prove the checkout is clean.")
    if tracked_count:
        mark("TRACKED_CHANGES", "block", f"{tracked_count} tracked checkout entries changed; preserve owner edits.")
    if untracked_count:
        mark("UNTRACKED_FILES", "block", f"{untracked_count} untracked checkout entries present; do not overwrite.")
    if branch != "main":
        mark("NOT_MAIN", "block", "Live checkout is not on main.")

    pids = listener_pids()
    worker_processes = dedicated_worker_pids()
    if worker_processes:
        mark("RUNNER_WORKER_PROCESS_OBSERVED", "block",
             "A dedicated Runner.Worker process exists; do not restart or promote while the runner may be busy.")
    systemd = service_observation()
    unit = str(systemd["state"])
    if not pids:
        mark("ZCLOUD_RUNNER_MISSING", "block", "Dedicated zCloud Runner.Listener not observed.")
    elif len(pids) > 1:
        mark("RUNNER_LISTENER_DUPLICATED", "block",
             "Multiple dedicated zCloud listeners observed; runner registration ownership is ambiguous.")
    if unit in {"failed", "inactive"}:
        mark("RUNNER_UNIT_NOT_ACTIVE", "block", f"zCloud runner unit state: {unit}.")
    elif unit in {"activating", "deactivating"}:
        mark("RUNNER_UNIT_TRANSITIONING", "block",
             "Dedicated runner service is still transitioning; no stable release admission.")
    elif unit == "ambiguous":
        mark("RUNNER_UNIT_AMBIGUOUS", "block",
             "Dedicated runner differs between system and user systemd managers; owner reconciliation required.")
    elif unit == "unavailable":
        mark("RUNNER_UNIT_UNOBSERVABLE", "warn", "No loaded dedicated unit could be verified in either systemd manager.")

    memory = meminfo()
    if {"MemTotal", "MemAvailable"} <= memory.keys():
        available_pct = round(100 * memory["MemAvailable"] / max(memory["MemTotal"], 1), 1)
        if available_pct < 10:
            mark("LOW_AVAILABLE_MEMORY", "block", "Available RAM below 10%.")
        elif available_pct < 20:
            mark("MEMORY_PRESSURE", "warn", "Available RAM below 20%.")
    else:
        available_pct = None
        mark("MEMORY_UNOBSERVABLE", "warn", "Cannot read memory accounting.")

    if {"SwapTotal", "SwapFree"} <= memory.keys() and 0 <= memory["SwapFree"] <= memory["SwapTotal"]:
        if memory["SwapTotal"] > 0:
            swap_pct = round(100 * (1 - memory["SwapFree"] / memory["SwapTotal"]), 1)
            if swap_pct >= 95:
                mark("SWAP_EXHAUSTED", "block", "Swap utilisation >=95%.")
        else:
            swap_pct = None  # Explicit zero swap is valid: no swap utilisation percentage.
    else:
        swap_pct = None
        mark("SWAP_TELEMETRY_INCOMPLETE", "warn",
             "Swap total/free counters are missing or contradictory; capacity is not fully observed.")

    try:
        volume = os.statvfs(root)
        disk_free_bytes = volume.f_bavail * volume.f_frsize
        disk_free_pct = round(100 * volume.f_bavail / max(volume.f_blocks, 1), 1)
        if disk_free_bytes < TRAINING_FREE_RESERVE_BYTES:
            mark("TRAINING_DISK_RESERVE_BELOW", "block",
                 "Filesystem available bytes below the operational 10 GiB training reserve; protect active workloads.")
        if disk_free_pct < 10:
            mark("LOW_DISK", "block", "Filesystem free space below 10%.")
    except OSError:
        disk_free_pct = None
        disk_free_bytes = None
        mark("DISK_UNOBSERVABLE", "warn", "Cannot read filesystem space.")

    inventory = optional_storage_inventory(root) if disk_hotspots else None
    if inventory is not None and inventory["incomplete"]:
        mark("STORAGE_TRIAGE_PARTIAL", "warn",
             "Some bounded disk summaries timed out or were unavailable; do not infer zero usage.")
    archive = archive_mount_readiness() if archive_readiness else None
    if archive is not None:
        if archive["state"] == "mounted_write_unverified":
            mark("ARCHIVE_WRITE_UNVERIFIED", "warn",
                 "External mount exists, but actual archive write permission is not proven.")
        else:
            mark("ARCHIVE_COLD_TIER_NOT_READY", "warn",
                 "External archive is missing, read-only or unavailable for verified relocation.")
    worker_snapshot = worker_safety_snapshot(root) if worker_safety else None
    if worker_snapshot is not None:
        if worker_snapshot["state"] == "active_work_observed":
            mark("ACTIVE_WORK_REQUIRES_DRAIN", "block",
                 "Claims or fresh browser-reported generation observed. Preserve work and use serialized owner-gated safe idle.")
        elif worker_snapshot["state"] not in {"no_active_evidence_unverified"}:
            mark("WORKER_EVIDENCE_INCOMPLETE", "block",
                 "Current worker/queue observation is incomplete or untrusted; release must remain blocked.")
        else:
            mark("WORKER_IDLE_NOT_PROVEN", "warn",
                 "No active browser/queue evidence in this bounded sample does not establish safe idle.")
    pressure = host_pressure_snapshot() if host_pressure else None
    if pressure is not None:
        findings.extend(pressure_findings(pressure))
    blocked = any(f["severity"] == "block" for f in findings)
    recovery = actionable_recovery(findings)
    report = {
        "schema": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only", "release_authorized": False,
        "source_scope": local_source_scope(root),
        "result": "blocked" if blocked else ("partial" if findings else "preflight_clear"),
        "checkout": {"branch": branch, "sha": sha, "expected_main_sha": expected_main_sha, "tracked_changes": tracked_count,
                     "untracked_entries": untracked_count},
        "runner": {"unit": RUNNER_UNIT, "unit_state": unit,
                   "unit_manager": systemd["manager"],
                   "loaded_managers": systemd["loaded_managers"],
                   "listener_pids": pids,
                   "worker_process_count": len(worker_processes)},
        "resources": {"available_ram_pct": available_pct, "swap_used_pct": swap_pct,
                      "disk_free_pct": disk_free_pct, "disk_free_bytes": disk_free_bytes,
                      "training_reserve_bytes": TRAINING_FREE_RESERVE_BYTES,
                      "training_reserve_gap_bytes": max(0, TRAINING_FREE_RESERVE_BYTES - disk_free_bytes)
                      if disk_free_bytes is not None else None},
        "findings": findings,
        "recovery_priorities": recovery,
        "storage_inventory": inventory,
        "archive_readiness": archive,
        "worker_safety": worker_snapshot,
        "host_pressure": pressure,
        "next_step": "Obtain existing owner/release approval; never use this report as deploy authorization.",
    }
    report["release_gates"] = release_gate_matrix(report)
    report["owner_handoff"] = operator_handoff(report)
    return report


def release_gate_matrix(report: dict) -> list[dict[str, str]]:
    """One bounded decision board; local observations never imply deploy approval."""
    findings = {finding["code"] for finding in report["findings"]}
    checkout = report["checkout"]
    runner = report["runner"]
    resources = report["resources"]
    worker = report.get("worker_safety")
    archive = report.get("archive_readiness")
    rows: list[dict[str, str]] = []

    def add(gate_id: str, status: str, owner: str, explanation: str) -> None:
        rows.append({"gate": gate_id, "status": status, "owner": owner,
                     "explanation": explanation, "authorizes_release": False})

    if "CHECKOUT_UNAVAILABLE" in findings or "MAIN_SHA_MISMATCH" in findings:
        source_status = "blocked"
    elif "REMOTE_MAIN_UNBOUND" in findings or checkout["expected_main_sha"] is None:
        source_status = "unknown"
    else:
        source_status = "local_match_only"
    add("exact_main_checkout", source_status, "Production release owner",
        "Source comparison does not prove signed main protection or approved deployment.")
    if {"CHECKOUT_STATUS_UNAVAILABLE", "NOT_MAIN", "TRACKED_CHANGES", "UNTRACKED_FILES"} & findings:
        clean_status = "blocked"
    else:
        clean_status = "locally_clear"
    add("preserve_checkout", clean_status, "Checkout/working-tree owner",
        "Never overwrite an uncommitted or independently owned change.")
    if (len(runner["listener_pids"]) != 1 or runner["worker_process_count"] > 0
            or runner["unit_state"] in {"failed", "inactive", "ambiguous", "activating", "deactivating"}):
        runner_status = "blocked"
    elif runner["unit_state"] == "unavailable":
        runner_status = "unit_unverified"
    else:
        runner_status = "process_observed_only"
    add("runner_identity", runner_status, "zCloud Actions runner owner (#1326)",
        "Listener existence does not prove current GitHub job assignment.")
    if {"LOW_DISK", "TRAINING_DISK_RESERVE_BELOW", "SWAP_EXHAUSTED", "LOW_AVAILABLE_MEMORY",
        "HOST_CPU_SATURATED", "HOST_MEMORY_STALL", "HOST_IO_STALL"} & findings:
        capacity_status = "blocked"
    elif "SWAP_TELEMETRY_INCOMPLETE" in findings or "HOST_PRESSURE_INCOMPLETE" in findings or None in (resources["available_ram_pct"], resources["disk_free_pct"]):
        capacity_status = "unknown"
    elif "HOST_CPU_BUSY" in findings:
        capacity_status = "contention_observed"
    else:
        capacity_status = "locally_observed_only"
    add("host_capacity", capacity_status, "VPS resource owner",
        "RAM, disk and opt-in CPU/PSI observations are advisory; capacity is not reserved for release.")
    if worker is None:
        worker_status = "not_inspected"
    elif worker["state"] not in {"no_active_evidence_unverified"}:
        worker_status = "blocked"
    else:
        worker_status = "owner_gate_required"
    add("worker_safe_idle", worker_status, "Serialized worker/runtime owner (#580/#1089)",
        "Browser telemetry cannot independently prove safe idle or authorize interruption.")
    if archive is None:
        archive_status = "not_inspected"
    else:
        archive_status = "write_not_authorized"
    add("external_archive", archive_status, "Encrypted archive/storage owner",
        "Mounted FUSE storage cannot prove transfer rights, hashes or retention permission.")
    for gate_id, explanation in (
        ("required_exact_head_ci", "GitHub-hosted and self-hosted required checks must be read separately on the current exact HEAD."),
        ("independent_review", "An independent authorized reviewer must approve the exact PR HEAD."),
        ("serialized_release", "Protected-main admission, active claims, exclusive writer and live acceptance are separate controls."),
    ):
        add(gate_id, "external_gate_required", "Authorized release/reviewer owner", explanation)
    return rows


def compare_snapshot(path: Path, current: dict) -> dict:
    """Compare code sets only; never trust free-text content from an older report."""
    try:
        # Pin the baseline inode for the entire read. Separate is_symlink()
        # and read_text() calls allow a path replacement between the checks.
        # This private receipt is data, never a source of release authority.
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > 131072:
            raise ValueError("invalid snapshot")
        if not hasattr(os, "O_NOFOLLOW"):
            raise ValueError("secure no-follow read unavailable")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    or opened.st_size > 131072):
                raise ValueError("snapshot source identity changed")
            with os.fdopen(os.dup(fd), "rb") as input_stream:
                raw_baseline = input_stream.read(131073)
            after = os.fstat(fd)
            if (len(raw_baseline) > 131072 or
                    opened.st_size != after.st_size or
                    opened.st_mtime_ns != after.st_mtime_ns):
                raise ValueError("snapshot changed while being read")
        finally:
            os.close(fd)

        def unique_json_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate snapshot JSON property")
                result[key] = value
            return result

        def reject_json_constant(_value):
            raise ValueError("non-standard snapshot numeric constant")

        prior = json.loads(raw_baseline.decode("utf-8"),
                           object_pairs_hook=unique_json_object,
                           parse_constant=reject_json_constant)
        if (not isinstance(prior, dict) or prior.get("schema") != 1
                or prior.get("mode") != "read_only"
                or prior.get("release_authorized") is not False):
            raise ValueError("invalid snapshot")
        observed_at = prior.get("observed_at")
        if not isinstance(observed_at, str) or len(observed_at) > 64:
            raise ValueError("invalid snapshot")
        previous_time = datetime.fromisoformat(observed_at)
        current_time = datetime.fromisoformat(current["observed_at"])
        if previous_time.tzinfo is None or previous_time.utcoffset() is None or previous_time > current_time:
            raise ValueError("invalid snapshot")
        snapshot_age_seconds = int((current_time - previous_time).total_seconds())
        stale_baseline = snapshot_age_seconds > MAX_PREVIOUS_REPORT_AGE_SECONDS
        before_scope, current_scope = prior.get("source_scope"), current.get("source_scope")
        same_scope = (
            type(before_scope) is dict and type(current_scope) is dict
            and before_scope.get("state") == "local_boot_checkout_observed"
            and current_scope.get("state") == "local_boot_checkout_observed"
            and before_scope.get("attested") is False
            and current_scope.get("attested") is False
            and before_scope.get("release_authorized") is False
            and current_scope.get("release_authorized") is False
            and type(before_scope.get("fingerprint_sha256")) is str
            and re.fullmatch(r"[0-9a-f]{64}", before_scope["fingerprint_sha256"]) is not None
            and before_scope["fingerprint_sha256"] == current_scope.get("fingerprint_sha256")
        )
        scope_status = (
            "same_operator_supplied_boot_checkout" if same_scope else
            "different_observed_source_scope" if (
                type(before_scope) is dict and type(current_scope) is dict
                and before_scope.get("state") == "local_boot_checkout_observed"
                and current_scope.get("state") == "local_boot_checkout_observed"
                and re.fullmatch(r"[0-9a-f]{64}",
                                 str(before_scope.get("fingerprint_sha256"))) is not None
                and re.fullmatch(r"[0-9a-f]{64}",
                                 str(current_scope.get("fingerprint_sha256"))) is not None
            ) else "scope_unavailable_or_legacy_baseline"
        )
        rows = prior.get("findings")
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("invalid snapshot")
        old: dict[str, str] = {}
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("code"), str)
                    or not re.fullmatch(r"[A-Z0-9_]{3,64}", row["code"])
                    or row["code"] in old
                    or row.get("severity") not in {"block", "warn"}):
                raise ValueError("invalid snapshot")
            old[row["code"]] = row["severity"]
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("Previous report is not a supported bounded zCloud read-only snapshot.") from None
    new = {item["code"]: item["severity"] for item in current["findings"]}
    changed = sorted(set(old) & set(new))
    # Optional reads can disappear simply because the next invocation omitted
    # their flags. Missing measurement is NOT remediation of an old blocker.
    optional_sources = {
        "STORAGE_TRIAGE_PARTIAL": "storage_inventory",
        "ARCHIVE_COLD_TIER_NOT_READY": "archive_readiness",
        "ARCHIVE_WRITE_UNVERIFIED": "archive_readiness",
        "ACTIVE_WORK_REQUIRES_DRAIN": "worker_safety",
        "WORKER_EVIDENCE_INCOMPLETE": "worker_safety",
        "WORKER_IDLE_NOT_PROVEN": "worker_safety",
        "HOST_CPU_SATURATED": "host_pressure",
        "HOST_CPU_BUSY": "host_pressure",
        "HOST_MEMORY_STALL": "host_pressure",
        "HOST_IO_STALL": "host_pressure",
        "HOST_PRESSURE_INCOMPLETE": "host_pressure",
    }
    ram_codes = {"LOW_AVAILABLE_MEMORY", "MEMORY_PRESSURE"}
    disk_codes = {"LOW_DISK", "TRAINING_DISK_RESERVE_BELOW"}
    disappeared = set(old) - set(new)
    def reobserved(code: str) -> bool:
        source = optional_sources.get(code)
        if source and current.get(source) is None:
            return False
        # A failed optional read cannot certify that an earlier live blocker
        # cleared. Even a quiet browser sample cannot prove worker safe-idle.
        worker = current.get("worker_safety") or {}
        if code in {"ACTIVE_WORK_REQUIRES_DRAIN", "WORKER_IDLE_NOT_PROVEN"}:
            return False  # Only an independently authorized owner can prove idle.
        if code == "WORKER_EVIDENCE_INCOMPLETE" and worker.get("state") not in {
                "active_work_observed", "no_active_evidence_unverified"}:
            return False
        if code in {"ARCHIVE_COLD_TIER_NOT_READY", "ARCHIVE_WRITE_UNVERIFIED"}:
            return False  # This tool never verifies a writable encrypted archive.
        if code == "RUNNER_LISTENER_DUPLICATED" and len(
                current.get("runner", {}).get("listener_pids", [])) != 1:
            return False
        if code == "RUNNER_WORKER_PROCESS_OBSERVED":
            return False  # Process absence is not a job completion or safe-idle proof.
        pressure = current.get("host_pressure") or {}
        if code in {"HOST_CPU_SATURATED", "HOST_CPU_BUSY"} and pressure.get("load_per_cpu") is None:
            return False
        if code == "HOST_MEMORY_STALL" and pressure.get("memory_full_avg10_pct") is None:
            return False
        if code == "HOST_IO_STALL" and pressure.get("io_full_avg10_pct") is None:
            return False
        if code == "HOST_PRESSURE_INCOMPLETE" and pressure.get("state") != "observed":
            return False
        resources = current.get("resources") or {}
        if code in ram_codes and resources.get("available_ram_pct") is None:
            return False
        if code == "SWAP_EXHAUSTED" and resources.get("swap_used_pct") is None:
            return False
        if code in disk_codes and resources.get("disk_free_bytes") is None:
            return False
        if code in {"TRACKED_CHANGES", "UNTRACKED_FILES"} and (
                "CHECKOUT_STATUS_UNAVAILABLE" in new or "CHECKOUT_UNAVAILABLE" in new):
            return False
        if code in {"RUNNER_UNIT_NOT_ACTIVE", "RUNNER_UNIT_AMBIGUOUS",
                    "RUNNER_UNIT_TRANSITIONING"} and current["runner"]["unit_state"] == "unavailable":
            return False
        return True
    # An old baseline may still provide context, but a disappearance against
    # a report older than one day is not a current recovery observation.
    confirmed_resolved = sorted(
        code for code in disappeared
        if not stale_baseline and same_scope and reobserved(code))
    not_reobserved = sorted(disappeared - set(confirmed_resolved))
    return {
        "new_findings": sorted(set(new) - set(old)),
        "resolved_findings": confirmed_resolved,
        "recovery_unverified_findings": not_reobserved,
        "still_present": changed,
        "severity_changes": [
            {"code": code, "previous": old[code], "current": new[code]}
            for code in changed if old[code] != new[code]
        ],
        "unchanged": old == new,
        "snapshot_age_seconds": snapshot_age_seconds,
        "baseline_sha256": hashlib.sha256(raw_baseline).hexdigest(),
        "baseline_bytes": len(raw_baseline),
        "baseline_integrity": "single_open_nofollow_stable_inode_duplicate_json_rejected",
        "snapshot_freshness": ("stale_baseline_no_resolution_credit" if stale_baseline
                                else "source_scope_unverified_no_resolution_credit"
                                if not same_scope else "fresh_same_scope_within_24h"),
        "source_scope_comparison": scope_status,
        "resolution_credit_allowed": not stale_baseline and same_scope,
        "source": "local_operator_supplied_snapshot",
        "host_pressure_trend": (
            compare_host_pressure(
                prior.get("host_pressure"), current.get("host_pressure"),
                (current_time - previous_time).total_seconds())
            if same_scope else {
                "status": "source_scope_not_bound",
                "trend": "unknown", "metrics": [],
                "elapsed_seconds": snapshot_age_seconds,
                "source": "operator_supplied_local_snapshots_unverified",
                "release_authorized": False,
            }),
    }


def render_html(report: dict) -> str:
    """Self-contained, escaped, offline operator view; never embeds raw process arguments."""
    esc = lambda value: escape(str(value), quote=True)
    state = report["result"]
    label = {"blocked": "Deployment blocked", "partial": "Incomplete evidence",
             "preflight_clear": "Local preflight clear"}.get(state, "Unverified")
    counts = {"block": 0, "warn": 0}
    for finding in report["findings"]:
        if finding["severity"] in counts:
            counts[finding["severity"]] += 1

    def item(text: str, value: object) -> str:
        return f"<dt>{esc(text)}</dt><dd>{esc(value)}</dd>"

    def val(value: object) -> str:
        return "Not measured" if value is None else str(value)

    release = report.get("release_gates", [])
    release_html = (
        '<section aria-labelledby="gates"><h2 id="gates">Release decision board</h2>'
        '<p class="muted">A locally clear gate is not permission to deploy.</p><dl>'
        + "".join(
            item(row["gate"], row["status"] + " · " + row["owner"])
            for row in release
        )
        + '</dl></section>'
    )
    findings = "".join(
        f'<li class="finding"><strong>{esc(f["code"])}</strong>'
        f' <span class="{esc(f["severity"])}">{esc(f["severity"].upper())}</span>'
        f'<p>{esc(f["detail"])}</p></li>'
        for f in report["findings"]
    ) or "<li>No local findings recorded. This does not authorize a release.</li>"
    actions = "".join(
        f'<li><strong>{esc(a["code"])}</strong><p class="owner">Owner: {esc(a["owner"])}</p>'
        f'<p>{esc(a["reason"])}</p><p>{esc(a["next_action"])}</p></li>'
        for a in report["recovery_priorities"]
    )
    handoff_html = (
        '<section aria-labelledby="owner-handoff"><h2 id="owner-handoff">'
        'zSentinel · owner recovery handoff</h2>'
        '<p class="muted">Three lines per observed blocker. Advisory only: '
        'no recovery, claim, or release occurred.</p><ol>'
        + "".join(
            '<li class="finding">'
            '<p><strong>Problem (' + esc(card["code"]) + '):</strong> ' +
            esc(card["problem"]) + '</p>'
            '<p><strong>Action (' + esc(card["owner"]) + '):</strong> ' +
            esc(card["action"]) + '</p>'
            '<p><strong>Status:</strong> ' + esc(card["status"]) +
            ' · ' + esc(card["trend"]) + ' · ' +
            esc(", ".join(card["finding_codes"]) or "external owner gates") + '</p>'
            '</li>'
            for card in report.get("owner_handoff", [])
        ) + '</ol></section>'
    )
    comparison = report.get("comparison")
    if comparison is None:
        comparison_html = ""
    else:
        def shown(values: list[str]) -> str:
            return ", ".join(esc(x) for x in values) or "None"
        comparison_html = (
            '<section aria-labelledby="diff"><h2 id="diff">Changes since previous report</h2>'
            f'<dl>{item("Previous snapshot freshness", comparison.get("snapshot_freshness", "unknown"))}'
            f'{item("Previous snapshot age (seconds)", comparison.get("snapshot_age_seconds", "unknown"))}'
            f'{item("Boot/checkout source comparison", comparison.get("source_scope_comparison", "unknown"))}'
            f'{item("Exact prior source SHA-256", comparison.get("baseline_sha256", "unknown"))}'
            f'{item("Exact prior source bytes", comparison.get("baseline_bytes", "unknown"))}'
            f'{item("Prior source read integrity", comparison.get("baseline_integrity", "unknown"))}'
            f'{item("Resolution credit allowed", comparison.get("resolution_credit_allowed", "unknown"))}'
            f'{item("New findings", shown(comparison["new_findings"]))}'
            f'{item("Confirmed resolved", shown(comparison["resolved_findings"]))}'
            f'{item("Recovery unverified", shown(comparison["recovery_unverified_findings"]))}'
            f'{item("Still present", shown(comparison["still_present"]))}'
            f'{item("Severity changed", shown([x["code"] + ": " + x["previous"] + " → " + x["current"] for x in comparison["severity_changes"]]))}</dl>'
            '<p class="muted">An omitted/failed measurement never proves recovery. Previous report is locally supplied, not independent runtime attestation.</p></section>'
        )
    trend_html = ""
    if comparison is not None:
        trend = comparison["host_pressure_trend"]
        rows = "".join(
            item(metric["label"], str(metric["previous"]) + " → " +
                 str(metric["current"]) + " (Δ " + str(metric["delta"]) + ")")
            for metric in trend["metrics"]
        )
        trend_html = (
            '<section aria-labelledby="pressure-trend"><h2 id="pressure-trend">Host pressure trend</h2>'
            + '<dl>' + item("Coverage", trend["status"])
            + item("Direction", trend["trend"])
            + item("Seconds between snapshots", trend["elapsed_seconds"])
            + rows + '</dl><p class="muted">Private operator-supplied readings only; no trusted host identity or recovery clearance. A missing reading does not mean recovered.</p></section>'
        )
    archive = report.get("archive_readiness")
    archive_html = ""
    if archive is not None:
        archive_html = (
            '<section aria-labelledby="archive"><h2 id="archive">External archive readiness</h2>'
            f'<dl>{item("Mount", archive["mount"])}'
            f'{item("Observed state", archive["state"])}'
            f'{item("Filesystem", val(archive["mount_type"]))}</dl>'
            '<p class="muted">No write probe or transfer was attempted. Archive writes are never authorized by this report.</p></section>'
        )
    worker = report.get("worker_safety")
    worker_html = ""
    if worker is not None:
        worker_html = (
            '<section aria-labelledby="worker"><h2 id="worker">Worker protection</h2>'
            f'<dl>{item("Workload evidence", worker["state"])}'
            f'{item("Claimed queue entries", val(worker["queued_claims"]))}'
            f'{item("Durable claims", val(worker["durable_claims"]))}'
            f'{item("Fresh generating slots", val(worker["fresh_generating_slots"]))}'
            f'{item("Event-window complete", worker["events_sample_complete"])}</dl>'
            '<p class="muted">Browser evidence is not independently verified text progress.'
            ' Never interrupt workers or infer safe-idle from this snapshot.</p></section>'
        )
    storage = report.get("storage_inventory")
    if storage is None:
        storage_html = ""
    else:
        entries = "".join(
            f'<li><strong>{esc(row["category"])}</strong>: {esc(row["size_gib"])} GiB</li>'
            for row in storage["measured"]
        ) or "<li>No directory summary completed within the time budget.</li>"
        missing = ", ".join(esc(label) for label in storage["incomplete"]) or "None"
        storage_html = (
            '<section aria-labelledby="storage"><h2 id="storage">Optional disk hotspots</h2>'
            f'<ul>{entries}</ul><p>Incomplete summaries: {missing}</p>'
            '<p class="muted">Five fixed categories only. No files were changed or deleted.</p></section>'
        )
    pressure = report.get("host_pressure")
    pressure_html = ""
    if pressure is not None:
        pressure_html = (
            '<section aria-labelledby="pressure"><h2 id="pressure">Shared-host CPU and I/O contention</h2><dl>'
            + item("Observation", pressure["state"])
            + item("One-minute load / CPU", val(pressure["load_per_cpu"]))
            + item("Logical CPU count", val(pressure["cpu_count"]))
            + item("CPU PSI some (10s)", val(pressure["cpu_some_avg10_pct"]))
            + item("Memory PSI full (10s)", val(pressure["memory_full_avg10_pct"]))
            + item("I/O PSI full (10s)", val(pressure["io_full_avg10_pct"]))
            + '</dl><p class="muted">PSI values are percentages. Demand is not CPU utilization, nor proof of which worker caused pressure. No actions or release rights.</p></section>'
        )
    checkout = report["checkout"]
    runner = report["runner"]
    resources = report["resources"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>zCloud VPS release preflight</title>
<style>
:root {{font: 16px/1.5 system-ui,-apple-system,BlinkMacSystemFont,sans-serif;color:#1f2937;background:#f6f7f9}}
*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:960px;margin:auto;padding:clamp(16px,4vw,42px)}}
header{{border-bottom:1px solid #d6dbe1;padding-bottom:22px}}h1{{font-size:clamp(25px,3vw,38px);line-height:1.15;margin:0 0 10px}}
h2{{font-size:19px;margin:0 0 14px}}p{{margin:6px 0}}.muted{{color:#52606d}}
section{{background:white;margin:20px 0;padding:22px;border:1px solid #e0e5ea;border-radius:12px}}
dl{{display:grid;grid-template-columns:minmax(125px,1fr) minmax(0,2fr);gap:8px 16px;margin:0}}
dt{{color:#52606d}}dd{{margin:0;overflow-wrap:anywhere;font-weight:600}}
ol,ul{{padding-left:22px;margin:0}}li{{margin:0 0 14px}}li:last-child{{margin-bottom:0}}
.finding p{{color:#52606d}}.block{{color:#ae2626}}.warn{{color:#8a6100}}.owner{{font-weight:650}}
.status{{font-size:20px;font-weight:750}}.summary{{display:flex;gap:16px;flex-wrap:wrap}}.summary span{{padding-right:16px}}
.disclaimer{{border-left:4px solid #45556a;padding:12px 16px;background:#eef1f5;border-radius:4px}}
@media(max-width:520px){{dl{{grid-template-columns:1fr;gap:0}}dd{{margin-bottom:12px}}section{{padding:16px}}}}
@media print{{:root{{background:white}}main{{padding:10px}}section{{break-inside:avoid}}}}
</style></head><body><main>
<header><h1>zCloud VPS deployment preflight</h1>
<p class="status">{esc(label)}</p><div class="summary"><span>{counts["block"]} blockers</span>
<span>{counts["warn"]} warnings</span><span>{esc(report["observed_at"])}</span></div>
<p class="muted">Offline, read-only observation. Production release authorization: NO.</p></header>
<section aria-labelledby="overview"><h2 id="overview">Host snapshot</h2><dl>
{item("Checked-out branch", checkout["branch"])}
{item("Local commit SHA", val(checkout["sha"]))}
{item("Expected main SHA", val(checkout["expected_main_sha"]))}
{item("Changed tracked entries", checkout["tracked_changes"])}
{item("Untracked entries", checkout["untracked_entries"])}
{item("Dedicated runner unit", runner["unit"])}
{item("Unit state", runner["unit_state"])}
{item("Unit manager", runner["unit_manager"])}
{item("Dedicated listener count", len(runner["listener_pids"]))}
{item("Dedicated worker process count", runner["worker_process_count"])}
{item("Available RAM", val(resources["available_ram_pct"]) + "%")}
{item("Swap used", str(resources["swap_used_pct"]) + "%" if resources["swap_used_pct"] is not None else "unknown")}
{item("Filesystem free", val(resources["disk_free_pct"]) + "%")}
{item("Actual free bytes", val(resources["disk_free_bytes"]))}
{item("Training free reserve bytes", resources["training_reserve_bytes"])}
{item("Remaining reserve gap bytes", val(resources["training_reserve_gap_bytes"]))}
</dl></section>
{comparison_html}
{pressure_html}
{trend_html}
{storage_html}
{archive_html}
{worker_html}
{release_html}
{handoff_html}
<section aria-labelledby="findings"><h2 id="findings">Release blockers and warnings</h2><ul>{findings}</ul></section>
<section aria-labelledby="recovery"><h2 id="recovery">Owner-gated recovery priorities</h2><ol>{actions}</ol></section>
<p class="disclaimer"><strong>Not an approval.</strong> This report cannot prove protected-main review,
exact-head CI, GitHub runner registration, active leases or production readiness. Follow the
existing #1326 runner and #580/#1089 serialized release gates. No recovery action was executed.</p>
</main></body></html>"""



MAX_PRIVATE_REPORT_BYTES = 4 * 1024 * 1024


def save_private_report(destination: Path, rendered: str) -> None:
    """Create one finished 0600 operator report without replacing prior work.

    The only mutation is the explicitly selected *new* local report path.
    Writing a private file never changes the observed VPS/runner/queue state.
    A same-directory temporary inode is fsynced and hardlinked into place
    only when its final name is still unused; no existing file is overwritten.
    """
    if not isinstance(destination, Path) or destination.name in ("", ".", ".."):
        raise ValueError("invalid private report destination")
    encoded = rendered.encode("utf-8")
    if not encoded or len(encoded) > MAX_PRIVATE_REPORT_BYTES:
        raise ValueError("private preflight report exceeds 4 MiB bound")
    fd = None
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(
            prefix=".zsentinel-private-", suffix=".partial",
            dir=destination.parent,
        )
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # Hardlink creation fails if the target already exists, including
        # a symlink. Unlike os.replace, link() does not overwrite owner data.
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass



def save_private_bundle(directory: Path, report: dict,
                        args: argparse.Namespace) -> None:
    """One operator-requested 0700 package from one read-only observation.

    All file contents are derived from the identical in-memory report,
    never independent re-probes. The directory must not exist already.
    This creates private evidence files only; no runner/service/queue writes.
    """
    if not isinstance(directory, Path) or directory.name in ("", ".", ".."):
        raise ValueError("invalid private preflight bundle directory")
    brief_args = argparse.Namespace(**{
        **vars(args), "json": False, "format": "brief",
    })
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        render_report_to_stdout(report, brief_args)
    content = {
        "observation.json": json.dumps(report, indent=2, sort_keys=True) + "\n",
        "preflight.html": render_html(report) + "\n",
        "owner-handoff.md": render_markdown(report) + "\n",
        "owner-brief.txt": buffer.getvalue(),
    }
    manifest = {
        "schema": "zsentinel-private-vps-preflight-bundle-v1",
        "observed_at": report["observed_at"],
        "release_authorized": False,
        "host_actions_executed": False,
        "origin": "one_in_memory_read_only_operator_observation",
        "source_scope_attested": False,
        "artifacts": [
            {
                "filename": name,
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                "bytes": len(value.encode("utf-8")),
            }
            for name, value in sorted(content.items())
        ],
    }
    content["manifest.json"] = json.dumps(
        manifest, sort_keys=True, indent=2) + "\n"
    created_names = []
    os.mkdir(directory, mode=0o700)
    try:
        for name, body in content.items():
            save_private_report(directory / name, body)
            created_names.append(name)
    except (OSError, ValueError, UnicodeError):
        # The 0700 newly-created folder belongs to this invocation; clean up
        # only the exact artifact filenames we successfully created.
        for name in reversed(created_names):
            try:
                (directory / name).unlink()
            except FileNotFoundError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass
        raise




BUNDLE_ARTIFACTS = frozenset({
    "observation.json", "preflight.html",
    "owner-handoff.md", "owner-brief.txt",
})


def verify_private_bundle(directory: Path) -> dict:
    """Check one saved operator bundle without a live VPS probe.

    The manifest is operator-controlled and unsigned; byte consistency is
    not independent provenance, host attestation, or release permission.
    """
    if (not isinstance(directory, Path) or
            not hasattr(os, "O_DIRECTORY") or
            not hasattr(os, "O_NOFOLLOW")):
        raise ValueError("private bundle verification unavailable")
    root_fd = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        root_stat = os.fstat(root_fd)
        if (not stat.S_ISDIR(root_stat.st_mode) or
                root_stat.st_mode & 0o077):
            raise ValueError("bundle folder must be private")
        def read_named(name, maximum):
            if name not in BUNDLE_ARTIFACTS and name != "manifest.json":
                raise ValueError("unrecognized bundle artifact")
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=root_fd)
            try:
                before = os.fstat(fd)
                if (not stat.S_ISREG(before.st_mode)
                        or before.st_mode & 0o077
                        or before.st_size < 1 or before.st_size > maximum):
                    raise ValueError("invalid private bundle artifact")
                with os.fdopen(os.dup(fd), "rb") as stream:
                    raw = stream.read(maximum + 1)
                after = os.fstat(fd)
                if (len(raw) != before.st_size or len(raw) > maximum
                        or before.st_size != after.st_size
                        or before.st_mtime_ns != after.st_mtime_ns):
                    raise ValueError("bundle source changed during verification")
                return raw
            finally:
                os.close(fd)

        def unique_object(pairs):
            value = {}
            for key, element in pairs:
                if key in value:
                    raise ValueError("duplicate manifest property")
                value[key] = element
            return value

        manifest = json.loads(
            read_named("manifest.json", 16 * 1024).decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("invalid manifest number")),
        )
        if (type(manifest) is not dict or
                manifest.get("schema") != "zsentinel-private-vps-preflight-bundle-v1"
                or manifest.get("release_authorized") is not False
                or manifest.get("host_actions_executed") is not False
                or manifest.get("source_scope_attested") is not False
                or manifest.get("origin") != "one_in_memory_read_only_operator_observation"
                or type(manifest.get("observed_at")) is not str
                or type(manifest.get("artifacts")) is not list
                or len(manifest["artifacts"]) != len(BUNDLE_ARTIFACTS)):
            raise ValueError("invalid private bundle manifest")
        found = {}
        for record in manifest["artifacts"]:
            if (type(record) is not dict or
                    set(record) != {"filename", "sha256", "bytes"}
                    or record["filename"] not in BUNDLE_ARTIFACTS
                    or record["filename"] in found
                    or type(record["sha256"]) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None
                    or type(record["bytes"]) is not int
                    or not 1 <= record["bytes"] <= MAX_PRIVATE_REPORT_BYTES):
                raise ValueError("invalid bundle file receipt")
            found[record["filename"]] = record
        if set(found) != BUNDLE_ARTIFACTS:
            raise ValueError("missing bundle evidence")
        for name in sorted(BUNDLE_ARTIFACTS):
            raw = read_named(name, MAX_PRIVATE_REPORT_BYTES)
            recorded = found[name]
            if (len(raw) != recorded["bytes"] or
                    hashlib.sha256(raw).hexdigest() != recorded["sha256"]):
                raise ValueError("private bundle integrity mismatch")
            if name == "observation.json":
                snapshot = json.loads(
                    raw.decode("utf-8"), object_pairs_hook=unique_object,
                    parse_constant=lambda _: (_ for _ in ()).throw(
                        ValueError("invalid observation number")),
                )
                if (type(snapshot) is not dict or
                        snapshot.get("mode") != "read_only"
                        or snapshot.get("release_authorized") is not False
                        or snapshot.get("observed_at") != manifest["observed_at"]):
                    raise ValueError("observation does not match manifest")
        return {
            "status": "local_bundle_bytes_match_unsigned_manifest",
            "observed_at": manifest["observed_at"],
            "artifact_count": len(BUNDLE_ARTIFACTS),
            "independent_host_attestation": False,
            "claim_authorized": False,
            "release_authorized": False,
            "actions_executed": False,
        }
    finally:
        os.close(root_fd)



def render_report_to_stdout(report: dict, args: argparse.Namespace) -> None:
    """Preserve existing stdout formatting across terminal and private output."""
    if args.json or args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.format == "html":
        print(render_html(report))
    elif args.format == "markdown":
        print(render_markdown(report))
    elif args.format == "brief":
        print("zSentinel owner handoff · read-only · NOT release authorized")
        print("Observation: " + report["observed_at"])
        print("Local source binding: " + report["source_scope"]["state"] +
              " (operator source only; no host attestation)")
        for card in report["owner_handoff"]:
            print("Problem [" + card["code"] + "]: " + card["problem"])
            print("Action [" + card["owner"] + "]: " + card["action"])
            print("Status: " + card["status"] + " · " + card["trend"] +
                  " · " + (", ".join(card["finding_codes"]) or "external gates"))
    else:
        print(f"zCloud VPS preflight: {report['result'].upper()} (read-only; NOT a release authorization)")
        c = report["checkout"]
        print(f"Checkout: {c['branch']} {c['sha'] or 'unknown'}; tracked={c['tracked_changes']} untracked={c['untracked_entries']}")
        r = report["runner"]
        print(f"Runner: {r['unit_state']} (systemd manager: {r['unit_manager']}); "
              f"dedicated listener count={len(r['listener_pids'])}; "
              f"dedicated Runner.Worker count={r['worker_process_count']}")
        disk = report["resources"]
        print(f"Free bytes: {disk['disk_free_bytes']} / training reserve: {disk['training_reserve_bytes']}; gap: {disk['training_reserve_gap_bytes']}")
        for f in report["findings"]:
            print(f"[{f['severity'].upper()}] {f['code']}: {f['detail']}")
        if "comparison" in report:
            change = report["comparison"]
            print(f"Previous snapshot: {change['snapshot_freshness']} · age={change['snapshot_age_seconds']}s · resolution credit={change['resolution_credit_allowed']}")
            print(f"Bound source SHA256: {change['baseline_sha256']} ({change['baseline_bytes']} bytes; no-follow stable inode)")
            print(f"Source scope: {change['source_scope_comparison']} · recovery credit allowed={change['resolution_credit_allowed']}")
            print(f"New findings: {', '.join(change['new_findings']) or 'none'}")
            print(f"Confirmed resolved findings: {', '.join(change['resolved_findings']) or 'none'}")
            print(f"Recovery unverified: {', '.join(change['recovery_unverified_findings']) or 'none'}")
            trend = change["host_pressure_trend"]
            print(f"CPU / PSI trend: {trend['status']} · {trend['trend']} (operator snapshot comparison, not release clearance)")
            print(f"Still present: {', '.join(change['still_present']) or 'none'}")
            for entry in change["severity_changes"]:
                print(f"Severity changed: {entry['code']} {entry['previous']} -> {entry['current']}")
        inventory = report.get("storage_inventory")
        if inventory is not None:
            print("Optional disk inventory (incomplete categories are NOT zero):")
            for row in inventory["measured"]:
                print(f"  {row['category']}: {row['size_gib']} GiB")
            if inventory["incomplete"]:
                print(f"  Incomplete: {', '.join(inventory['incomplete'])}")
        archive = report.get("archive_readiness")
        if archive is not None:
            print(f"External archive: {archive['state']} ({archive['mount_type'] or 'unknown'}); archive writes NOT authorized")
        pressure = report.get("host_pressure")
        if pressure is not None:
            print(f"Host demand: {pressure['load_per_cpu']} per CPU; CPU PSI some={pressure['cpu_some_avg10_pct']}%; memory full={pressure['memory_full_avg10_pct']}%; IO full={pressure['io_full_avg10_pct']}%; source={pressure['state']}")
        worker = report.get("worker_safety")
        if worker is not None:
            print(f"Worker protection: {worker['state']}; claimed queue={worker['queued_claims']}; fresh generating={worker['fresh_generating_slots']}; safe idle NOT verified")
        print("Release decision board (all entries are advisory):")
        for row in report["release_gates"]:
            print(f"  {row['gate']}: {row['status']} [{row['owner']}]")
        print("Owner-gated recovery priorities:")
        for index, item in enumerate(report["recovery_priorities"], 1):
            print(f"  {index}. {item['code']} [{item['owner']}]: {item['next_action']}")
        print(report["next_step"])



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/home/ubuntu/zennay-cloud"))
    parser.add_argument("--json", action="store_true", help="Print bounded machine-readable evidence")
    parser.add_argument("--disk-hotspots", action="store_true",
                        help="Optional bounded read-only du inventory of five known storage categories")
    parser.add_argument("--archive-readiness", action="store_true",
                        help="Read-only external archive mount inspection; never proves write permission")
    parser.add_argument("--host-pressure", action="store_true",
                        help="Opt-in bounded CPU load + kernel PSI pressure observation; no workload actions")
    parser.add_argument("--worker-safety", action="store_true",
                        help="Read-only portfolio queue and browser-event admission snapshot; cannot prove safe idle")
    parser.add_argument("--previous-report", type=Path, default=None,
                        help="Compare with a saved bounded JSON report; never modifies the prior file")
    parser.add_argument("--format", choices=("text", "json", "html", "markdown", "brief"), default="text",
                        help="Output as console text, JSON, offline HTML, or three-line owner handoff")
    parser.add_argument("--expected-main-sha", default=None,
                        help="Exact independently fetched 40-character main commit SHA (no network requests)")
    saved = parser.add_mutually_exclusive_group()
    saved.add_argument("--output", type=Path, default=None,
                       help="Create a NEW private 0600 report of the selected format; never overwrite")
    saved.add_argument("--bundle-dir", type=Path, default=None,
                       help="Create a NEW private 0700 snapshot folder: JSON, HTML, Markdown, brief and digest manifest")
    saved.add_argument("--verify-bundle", type=Path, default=None,
                       help="Verify a saved private bundle locally without probing VPS sources")
    args = parser.parse_args()
    if args.expected_main_sha and not re.fullmatch(r"[0-9a-f]{40}", args.expected_main_sha):
        parser.error("--expected-main-sha must be a lowercase 40-character Git SHA")
    if args.verify_bundle is not None:
        if (args.worker_safety or args.disk_hotspots or args.host_pressure
                or args.archive_readiness or args.previous_report is not None
                or args.expected_main_sha is not None or args.json
                or args.format != "text"):
            parser.error("--verify-bundle accepts no live preflight or output-format options")
        try:
            result = verify_private_bundle(args.verify_bundle)
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            print("PRIVATE_PREFLIGHT_BUNDLE_INTEGRITY_UNVERIFIED: "
                  "invalid, modified or unavailable private bundle; no VPS probe.",
                  file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    report = measure(args.root.resolve(), args.expected_main_sha, args.disk_hotspots, args.archive_readiness, args.worker_safety, args.host_pressure)
    if args.previous_report is not None:
        try:
            report["comparison"] = compare_snapshot(args.previous_report, report)
            report["owner_handoff"] = operator_handoff(report)
        except ValueError as exc:
            print(f"PREVIOUS_REPORT_INVALID: {exc}", file=sys.stderr)
            return 2
    if args.bundle_dir is not None:
        try:
            save_private_bundle(args.bundle_dir, report, args)
        except (OSError, ValueError, UnicodeError):
            # The directory and any contents are private operator paths.
            print("PRIVATE_PREFLIGHT_BUNDLE_NOT_CREATED: destination unavailable, "
                  "already exists or rendering failed; no existing bundle replaced.",
                  file=sys.stderr)
            return 2
        print("Private zSentinel VPS bundle created (0700, no replace).",
              file=sys.stderr)
    elif args.output is None:
        render_report_to_stdout(report, args)
    else:
        try:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                render_report_to_stdout(report, args)
            save_private_report(args.output, buffer.getvalue())
        except (OSError, ValueError, UnicodeError):
            # Input/output paths may identify private owner workspaces.
            print("PRIVATE_PREFLIGHT_REPORT_NOT_CREATED: destination unavailable, "
                  "existing or outside the bound; no existing report replaced.",
                  file=sys.stderr)
            return 2
        print("Private zSentinel VPS report created (0600, no replace).",
              file=sys.stderr)
    return 2 if report["result"] == "blocked" else (1 if report["result"] == "partial" else 0)


if __name__ == "__main__":
    sys.exit(main())
