#!/usr/bin/env python3
"""Read-only VPS deployment preflight. No service, Git, API or queue mutations."""
from __future__ import annotations

import argparse
from html import escape
from contextlib import closing
import json
import os
import re
import sqlite3
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone, timedelta

RUNNER_UNIT = "actions.runner.Zennay-zCloud.zcloud-vps-1.service"
TRAINING_FREE_RESERVE_BYTES = 10 * 1024 ** 3


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

    if codes & {"LOW_DISK", "TRAINING_DISK_RESERVE_BELOW", "SWAP_EXHAUSTED", "LOW_AVAILABLE_MEMORY", "MEMORY_PRESSURE"}:
        recommend("CAPACITY_FIRST", "VPS resource owner",
                  "Insufficient resource headroom can cause repeat OOM crashes or incomplete releases.",
                  "Inspect disk/cache ownership and memory pressure read-only; agree on safe cleanup or resizing before a controlled runner recovery. Never automatically delete or clear swap.")
    if codes & {"ZCLOUD_RUNNER_MISSING", "RUNNER_UNIT_NOT_ACTIVE", "RUNNER_UNIT_UNOBSERVABLE", "RUNNER_UNIT_AMBIGUOUS", "RUNNER_UNIT_TRANSITIONING"}:
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
    if "ACTIVE_WORK_REQUIRES_DRAIN" in codes or "WORKER_EVIDENCE_INCOMPLETE" in codes:
        recommend("WORKER_SAFE_IDLE_OWNER", "Serialized zCloud worker/release owner (#580/#1089)",
                  "Current queue claims or browser-reported generation may still be active, or monitoring is incomplete.",
                  "Preserve active claims and browser sessions; use existing owner-gated drain and independently confirmed idle evidence before a release. Do not kill workers.")
    if not recommendations:
        recommend("EXTERNAL_RELEASE_GATES", "zCloud release owner",
                  "A local read-only preflight cannot authorize a production release.",
                  "Verify GitHub branch protection, exact-head CI, independent review, serialized writer lock, runner assignment and live acceptance separately.")
    return recommendations


def measure(root: Path, expected_main_sha: str | None = None, disk_hotspots: bool = False, archive_readiness: bool = False, worker_safety: bool = False) -> dict:
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
    systemd = service_observation()
    unit = str(systemd["state"])
    if not pids:
        mark("ZCLOUD_RUNNER_MISSING", "block", "Dedicated zCloud Runner.Listener not observed.")
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

    if memory.get("SwapTotal", 0):
        swap_pct = round(100 * (1 - memory.get("SwapFree", 0) / memory["SwapTotal"]), 1)
        if swap_pct >= 95:
            mark("SWAP_EXHAUSTED", "block", "Swap utilisation >=95%.")
    else:
        swap_pct = None

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
    blocked = any(f["severity"] == "block" for f in findings)
    recovery = actionable_recovery(findings)
    report = {
        "schema": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only", "release_authorized": False,
        "result": "blocked" if blocked else ("partial" if findings else "preflight_clear"),
        "checkout": {"branch": branch, "sha": sha, "expected_main_sha": expected_main_sha, "tracked_changes": tracked_count,
                     "untracked_entries": untracked_count},
        "runner": {"unit": RUNNER_UNIT, "unit_state": unit,
                   "unit_manager": systemd["manager"],
                   "loaded_managers": systemd["loaded_managers"],
                   "listener_pids": pids},
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
        "next_step": "Obtain existing owner/release approval; never use this report as deploy authorization.",
    }
    report["release_gates"] = release_gate_matrix(report)
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
    if not runner["listener_pids"] or runner["unit_state"] in {"failed", "inactive", "ambiguous", "activating", "deactivating"}:
        runner_status = "blocked"
    elif runner["unit_state"] == "unavailable":
        runner_status = "unit_unverified"
    else:
        runner_status = "process_observed_only"
    add("runner_identity", runner_status, "zCloud Actions runner owner (#1326)",
        "Listener existence does not prove current GitHub job assignment.")
    if {"LOW_DISK", "TRAINING_DISK_RESERVE_BELOW", "SWAP_EXHAUSTED", "LOW_AVAILABLE_MEMORY"} & findings:
        capacity_status = "blocked"
    elif None in (resources["available_ram_pct"], resources["disk_free_pct"]):
        capacity_status = "unknown"
    else:
        capacity_status = "locally_observed_only"
    add("host_capacity", capacity_status, "VPS resource owner",
        "Capacity measurement does not reserve resources for a future release.")
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
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 131072:
            raise ValueError("invalid snapshot")
        prior = json.loads(path.read_text(encoding="utf-8"))
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
    return {
        "new_findings": sorted(set(new) - set(old)),
        "resolved_findings": sorted(set(old) - set(new)),
        "still_present": changed,
        "severity_changes": [
            {"code": code, "previous": old[code], "current": new[code]}
            for code in changed if old[code] != new[code]
        ],
        "unchanged": old == new,
        "source": "local_operator_supplied_snapshot",
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
    comparison = report.get("comparison")
    if comparison is None:
        comparison_html = ""
    else:
        def shown(values: list[str]) -> str:
            return ", ".join(esc(x) for x in values) or "None"
        comparison_html = (
            '<section aria-labelledby="diff"><h2 id="diff">Changes since previous report</h2>'
            f'<dl>{item("New findings", shown(comparison["new_findings"]))}'
            f'{item("Resolved findings", shown(comparison["resolved_findings"]))}'
            f'{item("Still present", shown(comparison["still_present"]))}'
            f'{item("Severity changed", shown([x["code"] + ": " + x["previous"] + " → " + x["current"] for x in comparison["severity_changes"]]))}</dl>'
            '<p class="muted">Previous report was locally supplied and not independent runtime attestation.</p></section>'
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
{item("Available RAM", val(resources["available_ram_pct"]) + "%")}
{item("Swap used", val(resources["swap_used_pct"]) + "%")}
{item("Filesystem free", val(resources["disk_free_pct"]) + "%")}
{item("Actual free bytes", val(resources["disk_free_bytes"]))}
{item("Training free reserve bytes", resources["training_reserve_bytes"])}
{item("Remaining reserve gap bytes", val(resources["training_reserve_gap_bytes"]))}
</dl></section>
{comparison_html}
{storage_html}
{archive_html}
{worker_html}
{release_html}
<section aria-labelledby="findings"><h2 id="findings">Release blockers and warnings</h2><ul>{findings}</ul></section>
<section aria-labelledby="recovery"><h2 id="recovery">Owner-gated recovery priorities</h2><ol>{actions}</ol></section>
<p class="disclaimer"><strong>Not an approval.</strong> This report cannot prove protected-main review,
exact-head CI, GitHub runner registration, active leases or production readiness. Follow the
existing #1326 runner and #580/#1089 serialized release gates. No recovery action was executed.</p>
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/home/ubuntu/zennay-cloud"))
    parser.add_argument("--json", action="store_true", help="Print bounded machine-readable evidence")
    parser.add_argument("--disk-hotspots", action="store_true",
                        help="Optional bounded read-only du inventory of five known storage categories")
    parser.add_argument("--archive-readiness", action="store_true",
                        help="Read-only external archive mount inspection; never proves write permission")
    parser.add_argument("--worker-safety", action="store_true",
                        help="Read-only portfolio queue and browser-event admission snapshot; cannot prove safe idle")
    parser.add_argument("--previous-report", type=Path, default=None,
                        help="Compare with a saved bounded JSON report; never modifies the prior file")
    parser.add_argument("--format", choices=("text", "json", "html"), default="text",
                        help="Output as console text, machine-readable JSON, or self-contained offline HTML")
    parser.add_argument("--expected-main-sha", default=None,
                        help="Exact independently fetched 40-character main commit SHA (no network requests)")
    args = parser.parse_args()
    if args.expected_main_sha and not re.fullmatch(r"[0-9a-f]{40}", args.expected_main_sha):
        parser.error("--expected-main-sha must be a lowercase 40-character Git SHA")
    report = measure(args.root.resolve(), args.expected_main_sha, args.disk_hotspots, args.archive_readiness, args.worker_safety)
    if args.previous_report is not None:
        try:
            report["comparison"] = compare_snapshot(args.previous_report, report)
        except ValueError as exc:
            print(f"PREVIOUS_REPORT_INVALID: {exc}", file=sys.stderr)
            return 2
    if args.json or args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.format == "html":
        print(render_html(report))
    else:
        print(f"zCloud VPS preflight: {report['result'].upper()} (read-only; NOT a release authorization)")
        c = report["checkout"]
        print(f"Checkout: {c['branch']} {c['sha'] or 'unknown'}; tracked={c['tracked_changes']} untracked={c['untracked_entries']}")
        r = report["runner"]
        print(f"Runner: {r['unit_state']} (systemd manager: {r['unit_manager']}); dedicated listener PIDs: {r['listener_pids']}")
        disk = report["resources"]
        print(f"Free bytes: {disk['disk_free_bytes']} / training reserve: {disk['training_reserve_bytes']}; gap: {disk['training_reserve_gap_bytes']}")
        for f in report["findings"]:
            print(f"[{f['severity'].upper()}] {f['code']}: {f['detail']}")
        if "comparison" in report:
            change = report["comparison"]
            print(f"New findings: {', '.join(change['new_findings']) or 'none'}")
            print(f"Resolved findings: {', '.join(change['resolved_findings']) or 'none'}")
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
    return 2 if report["result"] == "blocked" else (1 if report["result"] == "partial" else 0)


if __name__ == "__main__":
    sys.exit(main())
