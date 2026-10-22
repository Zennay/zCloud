#!/usr/bin/env python3
"""Bounded GitHub issue-comment to VPS runner operations.

This is NOT a generic shell interpreter.  Extend reviewed recipes on main for
new operations.  Public issue comments and Actions logs must contain no secrets.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile

REPOSITORY = "Zennay/zCloud"
CONTROL_ISSUE = 1278
OWNER = "Zennay"
CHATGPT_APP_ID = 1144995
HOST = "vps-bb300bba"
USER = "ubuntu"
COMMAND = re.compile(
    r"/zssh (status|workers|project cloud|service (?:zennay-cloud|zssh|zssh-public)|run [a-z][a-z0-9-]{1,40})\Z"
)


MAX_PROC_ENTRIES = 8192
MAX_PROC_STAT_BYTES = 4096
MAX_PROC_ANCESTORS = 64


class RequestRejected(ValueError):
    pass


def parse_request(event: dict, actor: str) -> str:
    if event.get("action") != "created":
        raise RequestRejected("wrong_action")
    if (event.get("repository") or {}).get("full_name") != REPOSITORY:
        raise RequestRejected("wrong_repository")
    issue = event.get("issue") or {}
    if issue.get("number") != CONTROL_ISSUE or issue.get("pull_request"):
        raise RequestRejected("wrong_issue")
    comment = event.get("comment") or {}
    if actor != OWNER or (comment.get("user") or {}).get("login") != OWNER:
        raise RequestRejected("unauthorized_actor")
    if comment.get("author_association") != "OWNER":
        raise RequestRejected("unauthorized_author_association")
    # Manual owner comments are allowed. For connector-mediated requests,
    # require the exact observed GitHub App identity; never trust text claims.
    app = comment.get("performed_via_github_app")
    if app is not None and app.get("id") != CHATGPT_APP_ID:
        raise RequestRejected("untrusted_app")
    body = comment.get("body")
    if not isinstance(body, str) or len(body) > 120 or not COMMAND.fullmatch(body):
        raise RequestRejected("unsupported_operation")
    return body.removeprefix("/zssh ")


def ensure_runner() -> None:
    if socket.gethostname().split(".", 1)[0] != HOST:
        raise RuntimeError("wrong_runner_host")
    if os.environ.get("USER") != USER or os.geteuid() == 0:
        raise RuntimeError("wrong_runner_user")
    name = os.environ.get("RUNNER_NAME", "")
    if not name or "haxlab" in name.lower():
        raise RuntimeError("wrong_runner_identity")


def run_checked(argv: list[str], *, cwd: str | None = None) -> tuple[int, str]:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=20,
        check=False,
        env={k: v for k, v in os.environ.items() if k not in
             {"GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "AWS_SECRET_ACCESS_KEY"}},
    )
    # Only reviewed read-only commands may expose stdout; bounded and no stderr.
    return completed.returncode, completed.stdout[:800].strip()


def _process_stat(pid: int) -> tuple[dict[str, object] | None, str | None]:
    """Read bounded /proc identity fields only; never read cmdline or environ."""
    try:
        with (Path("/proc") / str(pid) / "stat").open("rb") as stream:
            raw = stream.read(MAX_PROC_STAT_BYTES + 1)
    except FileNotFoundError:
        return None, "vanished"
    except PermissionError:
        return None, "unreadable"
    except OSError:
        return None, "unavailable"
    if len(raw) > MAX_PROC_STAT_BYTES:
        return None, "oversized"
    try:
        value = raw.decode("ascii")
    except UnicodeError:
        return None, "malformed"
    left, right = value.find("("), value.rfind(")")
    if left < 0 or right <= left:
        return None, "malformed"
    comm = value[left + 1:right]
    fields = value[right + 2:].split()
    if len(fields) < 20:
        return None, "malformed"
    try:
        parent_pid, start_ticks = int(fields[1]), int(fields[19])
    except (ValueError, IndexError):
        return None, "malformed"
    if parent_pid < 0 or start_ticks < 0:
        return None, "malformed"
    return {"comm": comm, "parent_pid": parent_pid, "start_ticks": start_ticks}, None


MAX_PROC_CGROUP_BYTES = 4096
EXPECTED_ZCLOUD_RUNNER_UNIT = "actions.runner.Zennay-zCloud.zcloud-vps-1.service"


def _service_cgroup_membership(pid: int) -> str:
    """Return an enum only; never expose a raw /proc cgroup path."""
    fd = None
    try:
        path = Path("/proc") / str(pid) / "cgroup"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags)
        raw = os.read(fd, MAX_PROC_CGROUP_BYTES + 1)
    except OSError:
        return "UNKNOWN"
    finally:
        if fd is not None:
            os.close(fd)
    if not raw or len(raw) > MAX_PROC_CGROUP_BYTES:
        return "UNKNOWN"
    try:
        value = raw.decode("ascii")
    except UnicodeError:
        return "UNKNOWN"
    paths = []
    for line in value.splitlines():
        fields = line.split(":", 2)
        if (len(fields) != 3 or not fields[0].isdigit()
                or not fields[2].startswith("/")):
            continue
        paths.append(fields[2])
    if not paths:
        return "UNKNOWN"
    if any(EXPECTED_ZCLOUD_RUNNER_UNIT in path.strip("/").split("/") for path in paths):
        return "EXPECTED_UNIT"
    return "OTHER_UNIT"


def _empty_service_cgroup_observation(coverage: str) -> dict[str, object]:
    return {
        "expected_unit": EXPECTED_ZCLOUD_RUNNER_UNIT,
        # /proc stat comm is only a process-name hint, not executable identity.
        "process_name_match_basis": "proc_stat_comm_only",
        "membership_coverage": coverage,
        "observation_summary": "unknown_source_unavailable" if coverage == "unavailable" else "not_observed",
        "listener_summary": "unknown_source_unavailable" if coverage == "unavailable" else "not_observed",
        "worker_summary": "unknown_source_unavailable" if coverage == "unavailable" else "not_observed",
        "operator_summary": (
            "Runner process/cgroup source unavailable; do not infer service health or restart authority."
            if coverage == "unavailable" else "Runner service-cgroup observation not yet completed."
        ),
        "process_scan_partial": coverage != "unavailable",
        "listener_processes_observed": 0,
        "listener_in_expected_unit": 0,
        "listener_outside_expected_unit": 0,
        "listener_membership_unknown": 0,
        "worker_processes_observed": 0,
        "worker_in_expected_unit": 0,
        "worker_outside_expected_unit": 0,
        "worker_membership_unknown": 0,
        "runner_registration_verified": False,
        "job_assignment_verified": False,
        "safe_to_take_over": False,
        "release_authorized": False,
        "mutation_performed": False,
    }


def _record_service_cgroup_membership(observation: dict[str, object], role: str, pid: int) -> None:
    membership = _service_cgroup_membership(pid)
    if membership == "EXPECTED_UNIT":
        key = f"{role}_in_expected_unit"
    elif membership == "OTHER_UNIT":
        key = f"{role}_outside_expected_unit"
    else:
        key = f"{role}_membership_unknown"
    observation[key] = int(observation[key]) + 1


def _occupancy_band(count: int, partial: bool = False) -> str:
    if count <= 0:
        return "none_observed_coverage_partial" if partial else "none_observed"
    if partial:
        return "one_or_more_observed" if count == 1 else "multiple_observed"
    return "one_observed" if count == 1 else "multiple_observed"



MAX_PROC_CAPACITY_BYTES = 16384
ARCHIVE_MOUNT_PATH = "/home/ubuntu/VPS-Archief"


def _read_bounded_proc_file(path: str, limit: int) -> bytes | None:
    """Read one fixed procfs source with no-follow flags and a hard byte cap."""
    fd = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags)
        raw = os.read(fd, limit + 1)
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)
    if not raw or len(raw) > limit:
        return None
    return raw


def _host_capacity_observation() -> dict[str, object]:
    """Bounded resource pressure hints; never infer cause or authorize cleanup."""
    result: dict[str, object] = {
        "state": "partial",
        "coverage": "partial",
        "severity": "unknown",
        "observed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "load_average": {"1m": None, "5m": None, "15m": None, "cpu_count": None,
                         "max_load_per_cpu": None},
        "root_filesystem": {"total_bytes": None, "available_bytes": None,
                            "available_percent": None, "used_percent": None},
        "memory": {"total_bytes": None, "available_bytes": None,
                   "available_percent": None},
        "swap": {"configured": None, "total_bytes": None, "free_bytes": None,
                 "used_percent": None},
        "archive_mount": {"configured": True, "mount_detected": None,
                          "total_bytes": None, "available_bytes": None,
                          "available_percent": None},
        "warnings": [],
        "evidence_warnings": [],
        "operator_summary": "Capacity evidence is partial; do not infer runner health or authorize cleanup.",
        "service_health_verified": False,
        "runner_registration_verified": False,
        "job_assignment_verified": False,
        "causal_attribution_verified": False,
        "cleanup_authorized": False,
        "offload_authorized": False,
        "restart_authorized": False,
        "mutation_performed": False,
    }
    complete = True
    warnings: list[str] = []
    evidence_warnings: list[str] = []

    raw = _read_bounded_proc_file("/proc/loadavg", 256)
    if raw is not None:
        try:
            values = [float(value) for value in raw.decode("ascii").split()[:3]]
            if (len(values) != 3 or
                    any(not math.isfinite(value) or value < 0 for value in values)):
                raise ValueError("invalid_load_average")
            cpu_count = os.cpu_count()
            if type(cpu_count) is not int or not 1 <= cpu_count <= 1024:
                cpu_count = None
            result["load_average"] = {
                "1m": round(values[0], 2), "5m": round(values[1], 2),
                "15m": round(values[2], 2), "cpu_count": cpu_count,
                "max_load_per_cpu": (
                    round(max(values) / cpu_count, 2) if cpu_count else None
                ),
            }
        except (ValueError, TypeError, UnicodeError, OverflowError):
            complete = False
    else:
        complete = False

    raw = _read_bounded_proc_file("/proc/meminfo", MAX_PROC_CAPACITY_BYTES)
    if raw is not None:
        try:
            required = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
            values: dict[str, int] = {}
            for line in raw.decode("ascii").splitlines():
                fields = line.split()
                if not fields or fields[0].rstrip(":") not in required:
                    continue
                key = fields[0].rstrip(":")
                if key in values or len(fields) != 3 or fields[2] != "kB":
                    raise ValueError("invalid_meminfo_field")
                if not fields[1].isdigit():
                    raise ValueError("invalid_meminfo_value")
                value = int(fields[1])
                if value > (1 << 50):
                    raise ValueError("oversized_meminfo_value")
                values[key] = value
            if not required.issubset(values):
                raise ValueError("incomplete_meminfo")
            total = values["MemTotal"] * 1024
            available = values["MemAvailable"] * 1024
            swap_total = values["SwapTotal"] * 1024
            swap_free = values["SwapFree"] * 1024
            if (total <= 0 or available > total or swap_free > swap_total):
                raise ValueError("inconsistent_meminfo")
            result["memory"] = {
                "total_bytes": total, "available_bytes": available,
                "available_percent": round(100 * available / total, 1),
            }
            result["swap"] = {
                "configured": swap_total > 0, "total_bytes": swap_total,
                "free_bytes": swap_free,
                "used_percent": (
                    round(100 * (swap_total - swap_free) / swap_total, 1)
                    if swap_total else None
                ),
            }
        except (ValueError, TypeError, UnicodeError, OverflowError, ZeroDivisionError):
            complete = False
    else:
        complete = False

    try:
        vfs = os.statvfs("/")
        block_size = int(vfs.f_frsize or vfs.f_bsize)
        total_blocks, free_blocks, available_blocks = (
            int(vfs.f_blocks), int(vfs.f_bfree), int(vfs.f_bavail)
        )
        if (block_size <= 0 or total_blocks <= 0 or free_blocks > total_blocks
                or available_blocks > total_blocks):
            raise ValueError("invalid_root_filesystem_capacity")
        total = block_size * total_blocks
        free = block_size * free_blocks
        available = block_size * available_blocks
        used = max(0, total - free)
        denominator = used + available
        result["root_filesystem"] = {
            "total_bytes": total,
            "available_bytes": available,
            "available_percent": round(100 * available / total, 1),
            "used_percent": round(100 * used / denominator, 1) if denominator else 100.0,
        }
    except (OSError, ValueError, TypeError, OverflowError, AttributeError, ZeroDivisionError):
        complete = False

    try:
        mount_info = os.lstat(ARCHIVE_MOUNT_PATH)
        if not stat.S_ISDIR(mount_info.st_mode):
            raise ValueError("archive_mount_path_not_directory")
        mounted = os.path.ismount(ARCHIVE_MOUNT_PATH)
        archive = result["archive_mount"]
        archive["mount_detected"] = mounted
        if not mounted:
            # The configured archive path may merely be a directory on root.
            # Do not report complete capacity evidence or imply offload is safe.
            complete = False
            evidence_warnings.append("archive_mount_not_detected")
        else:
            vfs = os.statvfs(ARCHIVE_MOUNT_PATH)
            block_size = int(vfs.f_frsize or vfs.f_bsize)
            total_blocks, available_blocks = int(vfs.f_blocks), int(vfs.f_bavail)
            if block_size <= 0 or total_blocks <= 0 or available_blocks > total_blocks:
                raise ValueError("invalid_archive_filesystem_capacity")
            total, available = block_size * total_blocks, block_size * available_blocks
            archive.update({
                "total_bytes": total,
                "available_bytes": available,
                "available_percent": round(100 * available / total, 1),
            })
    except (OSError, ValueError, TypeError, OverflowError, AttributeError, ZeroDivisionError):
        complete = False
        evidence_warnings.append("archive_mount_observation_unavailable")

    root = result["root_filesystem"]
    memory = result["memory"]
    swap = result["swap"]
    load = result["load_average"]
    archive = result["archive_mount"]
    if type(root.get("used_percent")) in (int, float) and root["used_percent"] >= 95:
        warnings.append("root_filesystem_critical")
    if type(swap.get("used_percent")) in (int, float) and swap["used_percent"] >= 90:
        warnings.append("swap_pressure_high")
    if type(memory.get("available_percent")) in (int, float) and memory["available_percent"] <= 10:
        warnings.append("memory_pressure_high")
    if type(load.get("max_load_per_cpu")) in (int, float) and load["max_load_per_cpu"] >= 2:
        warnings.append("load_pressure_high")
    archive_available = archive.get("available_bytes")
    archive_has_capacity = type(archive_available) is int and archive_available > 0
    if archive.get("mount_detected") is True and not archive_has_capacity:
        # This is an offload-capacity limitation, not itself host pressure.
        evidence_warnings.append("archive_mount_no_available_space")

    if "root_filesystem_critical" in warnings:
        if archive.get("mount_detected") is True and archive_has_capacity:
            summary = (
                "Root filesystem critically full and the configured archive mount reports available capacity. "
                "Verify file provenance, encryption/retention policy and active handles before owner-approved "
                "offload; no deletion, move or restart is authorized by this snapshot."
            )
        elif archive.get("mount_detected") is True:
            summary = (
                "Root filesystem critically full, but the configured archive mount reports no available capacity. "
                "Preserve active work and obtain an authorized storage inventory; no deletion, move or restart "
                "is authorized by this snapshot."
            )
        else:
            summary = (
                "Root filesystem critically full. Preserve active work and obtain an authorized storage inventory; "
                "do not delete files, move data or restart services from this snapshot."
            )
    elif warnings:
        summary = (
            "Resource pressure threshold(s) observed. This is not proof of runner/job failure; "
            "reconcile service identity, GitHub registration and job assignment separately."
        )
    elif evidence_warnings:
        summary = (
            "Host pressure thresholds are not exceeded in this snapshot, but archive-mount "
            "evidence/capacity is unavailable. Do not offload data or infer safe storage until "
            "the configured mount and its capacity are verified by the authorized owner."
        )
    elif complete:
        summary = (
            "No configured capacity threshold was exceeded in this snapshot. "
            "Runner health and job assignment remain unverified."
        )
    else:
        summary = (
            "Capacity evidence is partial or unavailable. Preserve active work and do not infer service health."
        )
    severity = (
        "critical" if "root_filesystem_critical" in warnings else
        "high" if warnings else
        "normal" if complete else "unknown"
    )
    result.update({
        "state": "observed" if complete else "partial",
        "coverage": "complete" if complete else "partial",
        "severity": severity,
        "warnings": warnings,
        "evidence_warnings": evidence_warnings,
        "operator_summary": summary,
    })
    return result



def _worker_age_bands(ages: list[int | None]) -> dict[str, int]:
    return {
        "under_5m": sum(age is not None and age < 300 for age in ages),
        "5m_to_30m": sum(age is not None and 300 <= age < 1800 for age in ages),
        "over_30m": sum(age is not None and age >= 1800 for age in ages),
        "unknown": sum(age is None for age in ages),
    }


def _worker_operator_action_codes(state: str, service_cgroup: dict,
                                  capacity: dict,
                                  self_worker_excluded: bool) -> list[str]:
    """Stable advisory codes for downstream operators; never grant authority."""
    codes = [
        "PRESERVE_ACTIVE_WORK",
        "TAKEOVER_NOT_AUTHORIZED",
        "RESTART_NOT_AUTHORIZED",
    ]
    coverage = service_cgroup.get("membership_coverage")
    summary = service_cgroup.get("observation_summary")
    listener_summary = service_cgroup.get("listener_summary")
    worker_summary = service_cgroup.get("worker_summary")
    if coverage != "complete_for_comm_matched_processes":
        codes.append("RUNNER_MEMBERSHIP_UNKNOWN")
    # Positive outside-unit counters remain actionable even when another
    # process's cgroup file or the overall /proc scan is incomplete.
    listener_mismatch = (
        type(service_cgroup.get("listener_outside_expected_unit")) is int
        and service_cgroup["listener_outside_expected_unit"] > 0
    )
    worker_mismatch = (
        type(service_cgroup.get("worker_outside_expected_unit")) is int
        and service_cgroup["worker_outside_expected_unit"] > 0
    )
    # Evaluate both roles independently: the combined summary prioritizes the
    # listener, and partial coverage must not hide a positive observed mismatch.
    if listener_mismatch or worker_mismatch:
        codes.append("OWNER_RECONCILIATION_REQUIRED")
        if listener_mismatch:
            codes.append("LISTENER_SERVICE_MEMBERSHIP_MISMATCH")
        if worker_mismatch:
            codes.append("WORKER_SERVICE_MEMBERSHIP_MISMATCH")
    observed_process_matches = (
        int(service_cgroup.get("listener_processes_observed", 0) or 0)
        + int(service_cgroup.get("worker_processes_observed", 0) or 0)
    )
    if (
        observed_process_matches == 0
        and coverage in ("no_runner_processes_observed", "partial_process_scan")
    ):
        codes.append("NO_PROCESS_MATCH_IS_NOT_REGISTRATION_PROOF")
    if (
        listener_summary == "all_in_expected_unit_comm_only"
        or worker_summary == "all_in_expected_unit_comm_only"
    ):
        codes.append("REGISTRATION_AND_JOB_ASSIGNMENT_UNVERIFIED")
    if not self_worker_excluded:
        # Keep identity uncertainty visible even when a partial scan masks state.
        codes.append("CURRENT_WORKER_NOT_IDENTIFIED")
    elif state == "no_other_worker_observed_not_clearance":
        codes.append("ZERO_WORKERS_IS_NOT_CLAIM_CLEARANCE")
    if capacity.get("coverage") != "complete":
        codes.append("CAPACITY_EVIDENCE_PARTIAL")
    evidence_warnings = capacity.get("evidence_warnings", [])
    if isinstance(evidence_warnings, list):
        if ("archive_mount_not_detected" in evidence_warnings
                or "archive_mount_observation_unavailable" in evidence_warnings):
            codes.append("ARCHIVE_MOUNT_UNAVAILABLE_NO_OFFLOAD")
        if "archive_mount_no_available_space" in evidence_warnings:
            codes.append("ARCHIVE_MOUNT_NO_CAPACITY_NO_OFFLOAD")
    severity = capacity.get("severity")
    if severity == "critical":
        codes.append("HOLD_DISK_WRITES_REQUEST_OWNER_INVENTORY")
    elif severity == "high":
        codes.append("RECONCILE_RESOURCE_PRESSURE_BEFORE_NEW_WORK")
    elif severity != "normal":
        codes.append("CAPACITY_EVIDENCE_UNKNOWN")
    else:
        codes.append("CAPACITY_THRESHOLDS_NOT_EXCEEDED_NOT_HEALTH_PROOF")
    return codes


def runner_worker_snapshot() -> dict:
    """Privacy-preserving local occupancy hint, not a task/claim inventory."""
    try:
        with Path("/proc/uptime").open("rb") as stream:
            uptime_raw = stream.read(128)
        uptime_seconds = float(uptime_raw.decode("ascii").split()[0])
        ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
        if (not math.isfinite(uptime_seconds) or uptime_seconds < 0
                or not 1 <= ticks_per_second <= 1_000_000):
            raise ValueError("invalid_proc_clock")
    except (OSError, UnicodeError, ValueError, IndexError, TypeError):
        service_cgroup = _empty_service_cgroup_observation("unavailable")
        capacity = _host_capacity_observation()
        state = "unavailable"
        return {
            "schema": "zssh.runner-workers.v1",
            "state": state, "scan_coverage": "unavailable",
            "observed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "worker_process_band_observed": "unknown",
            "other_worker_process_band": "unknown",
            "listener_process_band_observed": "unknown",
            "self_worker_excluded": False,
            "self_worker_identity_basis": "unknown",
            "worker_age_bands_observed": None,
            "claim_ownership_verified": False,
            "project_attribution_verified": False,
            "safe_to_take_over": False,
            "release_authorized": False,
            "mutation_performed": False,
            "runner_service_cgroup": service_cgroup,
            "host_capacity_observation": capacity,
            "operator_action_codes": _worker_operator_action_codes(
                state, service_cgroup, capacity, self_worker_excluded=False
            ),
        }

    service_cgroup = _empty_service_cgroup_observation("not_observed")
    # Bind self-worker identity to both PID and kernel start ticks. A PID alone
    # can be reused between ancestry discovery and the bounded /proc scan.
    current_worker_pids: dict[int, int] = {}
    pid = os.getpid()
    for _ in range(MAX_PROC_ANCESTORS):
        info, _error = _process_stat(pid)
        if info is None:
            break
        if info["comm"] == "Runner.Worker":
            current_worker_pids[pid] = int(info["start_ticks"])
        parent_pid = info["parent_pid"]
        if parent_pid <= 1 or parent_pid == pid:
            break
        pid = parent_pid
    self_worker_excluded = bool(current_worker_pids)

    current_worker_seen: set[int] = set()
    worker_ages: list[int | None] = []
    listener_count = 0
    scanned = 0
    partial = False
    try:
        with os.scandir("/proc") as entries:
            for entry in entries:
                if not entry.name.isdecimal():
                    continue
                scanned += 1
                if scanned > MAX_PROC_ENTRIES:
                    partial = True
                    break
                process_id = int(entry.name)
                info, error = _process_stat(process_id)
                if info is None:
                    if error not in (None, "vanished"):
                        partial = True
                    continue
                if info["comm"] == "Runner.Listener":
                    listener_count += 1
                    service_cgroup["listener_processes_observed"] += 1
                    _record_service_cgroup_membership(service_cgroup, "listener", process_id)
                    continue
                if info["comm"] != "Runner.Worker":
                    continue
                service_cgroup["worker_processes_observed"] += 1
                _record_service_cgroup_membership(service_cgroup, "worker", process_id)
                if (process_id in current_worker_pids
                        and current_worker_pids[process_id] == int(info["start_ticks"])):
                    current_worker_seen.add(process_id)
                    continue
                age = uptime_seconds - info["start_ticks"] / ticks_per_second
                worker_ages.append(
                    int(age) if math.isfinite(age) and age >= 0 else None
                )
    except OSError:
        partial = True

    service_cgroup["process_scan_partial"] = partial
    observed_runner_processes = (
        int(service_cgroup["listener_processes_observed"])
        + int(service_cgroup["worker_processes_observed"])
    )
    unknown_cgroup_memberships = (
        int(service_cgroup["listener_membership_unknown"])
        + int(service_cgroup["worker_membership_unknown"])
    )
    if partial:
        service_cgroup["membership_coverage"] = "partial_process_scan"
    elif observed_runner_processes == 0:
        service_cgroup["membership_coverage"] = "no_runner_processes_observed"
    elif unknown_cgroup_memberships:
        service_cgroup["membership_coverage"] = "partial_cgroup_metadata"
    else:
        service_cgroup["membership_coverage"] = "complete_for_comm_matched_processes"

    def role_summary(role: str) -> str:
        if service_cgroup["membership_coverage"] != "complete_for_comm_matched_processes":
            return "unknown_" + str(service_cgroup["membership_coverage"])
        observed = int(service_cgroup[f"{role}_processes_observed"])
        if observed == 0:
            return "no_process_name_match_observed"
        expected = int(service_cgroup[f"{role}_in_expected_unit"])
        outside = int(service_cgroup[f"{role}_outside_expected_unit"])
        if expected and outside:
            return "mixed_unit_membership_comm_only"
        if outside == observed:
            return "all_outside_expected_unit_comm_only"
        if expected == observed:
            return "all_in_expected_unit_comm_only"
        return "unknown_membership"

    service_cgroup["listener_summary"] = role_summary("listener")
    service_cgroup["worker_summary"] = role_summary("worker")
    if service_cgroup["membership_coverage"] == "complete_for_comm_matched_processes":
        if service_cgroup["listener_summary"] == "all_outside_expected_unit_comm_only":
            service_cgroup["observation_summary"] = "listener_outside_expected_unit_comm_only"
        elif service_cgroup["listener_summary"] == "mixed_unit_membership_comm_only":
            service_cgroup["observation_summary"] = "mixed_listener_unit_membership_comm_only"
        elif service_cgroup["worker_summary"] == "all_outside_expected_unit_comm_only":
            service_cgroup["observation_summary"] = "worker_outside_expected_unit_comm_only"
        elif service_cgroup["worker_summary"] == "mixed_unit_membership_comm_only":
            service_cgroup["observation_summary"] = "mixed_worker_unit_membership_comm_only"
        elif service_cgroup["listener_summary"] == "all_in_expected_unit_comm_only":
            service_cgroup["observation_summary"] = "listener_in_expected_unit_comm_only"
        elif service_cgroup["worker_summary"] == "all_in_expected_unit_comm_only":
            service_cgroup["observation_summary"] = "worker_in_expected_unit_comm_only"
        else:
            service_cgroup["observation_summary"] = "no_runner_processes_observed"
    else:
        service_cgroup["observation_summary"] = "unknown_" + str(service_cgroup["membership_coverage"])

    if service_cgroup["membership_coverage"] != "complete_for_comm_matched_processes":
        observed_outside_roles = [
            role for role in ("listener", "worker")
            if type(service_cgroup.get(f"{role}_outside_expected_unit")) is int
            and service_cgroup[f"{role}_outside_expected_unit"] > 0
        ]
        if observed_outside_roles:
            observed_roles = " and ".join(
                "Runner.Listener" if role == "listener" else "Runner.Worker"
                for role in observed_outside_roles
            )
            service_cgroup["operator_summary"] = (
                "Partial runner service-cgroup coverage still observed " + observed_roles
                + " process-name match(es) outside the expected unit. Treat this only as "
                "an owner-reconciliation lead; preserve active work and do not restart or "
                "take over from this receipt."
            )
        elif service_cgroup["membership_coverage"] == "no_runner_processes_observed":
            service_cgroup["operator_summary"] = (
                "No Runner.Listener/Worker process-name match was observed in the bounded scan; "
                "this does not prove GitHub registration or job assignment."
            )
        elif (
            service_cgroup["membership_coverage"] == "partial_process_scan"
            and int(service_cgroup.get("listener_processes_observed", 0) or 0)
            + int(service_cgroup.get("worker_processes_observed", 0) or 0) == 0
        ):
            service_cgroup["operator_summary"] = (
                "The partial runner process scan observed no Listener/Worker process-name match; "
                "the scan is incomplete and this does not prove absence, registration or job assignment. "
                "Preserve active work and request owner reconciliation."
            )
        else:
            service_cgroup["operator_summary"] = (
                "Runner service-cgroup membership is unknown or partial; preserve active work "
                "and do not restart or take over from this receipt."
            )
    elif service_cgroup["observation_summary"] == "listener_outside_expected_unit_comm_only":
        service_cgroup["operator_summary"] = (
            "A Runner.Listener process-name match was observed outside the expected service cgroup; "
            "reconcile process/service identity with the authorized owner. No restart is authorized."
        )
    elif service_cgroup["observation_summary"] == "mixed_listener_unit_membership_comm_only":
        service_cgroup["operator_summary"] = (
            "Runner.Listener process-name matches have mixed service-cgroup membership; "
            "preserve all processes and request owner reconciliation. No restart is authorized."
        )
    elif service_cgroup["observation_summary"] == "worker_outside_expected_unit_comm_only":
        service_cgroup["operator_summary"] = (
            "A Runner.Worker process-name match is outside the expected service cgroup; "
            "preserve the active job and request owner reconciliation. No restart is authorized."
        )
    elif service_cgroup["observation_summary"] == "mixed_worker_unit_membership_comm_only":
        service_cgroup["operator_summary"] = (
            "Runner.Worker process-name matches have mixed service-cgroup membership; "
            "preserve active jobs and request owner reconciliation. No restart is authorized."
        )
    elif service_cgroup["observation_summary"] == "listener_in_expected_unit_comm_only":
        service_cgroup["operator_summary"] = (
            "A Runner.Listener process-name match is inside the expected service cgroup; "
            "GitHub registration and job assignment remain unverified."
        )
    elif service_cgroup["observation_summary"] == "worker_in_expected_unit_comm_only":
        service_cgroup["operator_summary"] = (
            "A Runner.Worker process-name match is inside the expected service cgroup; "
            "this does not prove job ownership or that takeover is safe."
        )
    else:
        service_cgroup["operator_summary"] = (
            "No decisive listener/worker service-cgroup clue was observed; "
            "runner registration and job assignment remain unverified."
        )

    worker_band = _occupancy_band(len(worker_ages), partial)
    observed_band = _occupancy_band(
        len(worker_ages) + len(current_worker_seen), partial
    ) if self_worker_excluded else _occupancy_band(len(worker_ages), partial)
    state = (
        "partial_observation" if partial else
        "no_other_worker_observed_not_clearance" if self_worker_excluded and not worker_ages else
        "one_other_worker_observed" if self_worker_excluded and len(worker_ages) == 1 else
        "multiple_other_workers_observed" if self_worker_excluded else
        "current_worker_not_identified"
    )
    capacity = _host_capacity_observation()
    return {
        "schema": "zssh.runner-workers.v1",
        "state": state,
        "observed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan_coverage": "partial" if partial else "bounded_proc_snapshot_complete",
        "self_worker_excluded": self_worker_excluded,
        "self_worker_identity_basis": (
            "pid_and_start_ticks" if self_worker_excluded else "unknown"
        ),
        "worker_process_band_observed": observed_band,
        "other_worker_process_band": worker_band if self_worker_excluded else "unknown_self_not_excluded",
        "listener_process_band_observed": _occupancy_band(listener_count, partial),
        "worker_age_bands_observed": _worker_age_bands(worker_ages),
        "runner_service_cgroup": service_cgroup,
        "host_capacity_observation": capacity,
        "operator_action_codes": _worker_operator_action_codes(
            state, service_cgroup, capacity, self_worker_excluded=self_worker_excluded
        ),
        "claim_ownership_verified": False,
        "project_attribution_verified": False,
        "safe_to_take_over": False,
        "release_authorized": False,
        "mutation_performed": False,
    }


def execute(request: str) -> dict:
    ensure_runner()
    if request == "status":
        probes = [
            ("uptime", ["/usr/bin/uptime", "-p"]),
            ("disk", ["/usr/bin/df", "-h", "/"]),
            ("memory", ["/usr/bin/free", "-h"]),
        ]
        checks = {}
        for label, argv in probes:
            code, output = run_checked(argv)
            if code:
                raise RuntimeError("status_probe_failed:" + label)
            checks[label] = output
        return {"ok": True, "operation": "status", "host": HOST, "checks": checks}
    if request == "workers":
        return {"ok": True, "operation": "workers", "host": HOST,
                **runner_worker_snapshot()}
    if request == "project cloud":
        code, sha = run_checked(
            ["/usr/bin/git", "-C", "/home/ubuntu/zennay-cloud", "rev-parse", "--short", "HEAD"]
        )
        if code or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
            raise RuntimeError("cloud_git_revision_unavailable")
        return {"ok": True, "operation": "project cloud", "revision": sha}
    if request.startswith("service "):
        service = request.split(" ", 1)[1] + ".service"
        code, active = run_checked(["/usr/bin/systemctl", "is-active", "--", service])
        if code not in (0, 3):
            raise RuntimeError("service_inspection_failed")
        if active not in ("active", "inactive", "failed", "activating", "deactivating", "unknown"):
            raise RuntimeError("unexpected_service_state")
        return {"ok": True, "operation": request, "state": active}
    if request == "run write-probe":
        # Material, reversible proof of writing on the VPS without modifying services.
        temp_root = os.environ.get("RUNNER_TEMP")
        if not temp_root or not Path(temp_root).is_dir():
            raise RuntimeError("runner_temp_unavailable")
        with tempfile.TemporaryDirectory(prefix="zssh-chatgpt-", dir=temp_root) as directory:
            probe = Path(directory) / "proof.txt"
            probe.write_text("runner-local-write-proof\n", encoding="utf-8")
            if probe.read_text(encoding="utf-8") != "runner-local-write-proof\n":
                raise RuntimeError("write_probe_mismatch")
        return {"ok": True, "operation": request, "effect": "temporary_file_write_verified_and_cleaned"}
    if request == "run zcloud-guard-test":
        code, _ = run_checked(
            [sys.executable, "-m", "unittest", "-q", "tests.test_vps_runner_guard"],
            cwd=os.environ.get("GITHUB_WORKSPACE") or None,
        )
        if code:
            raise RuntimeError("zcloud_guard_test_failed")
        return {"ok": True, "operation": request, "effect": "unit_test_passed"}
    if request.startswith("run "):
        # Only execute a checked-in recipe from the exact main revision.
        # Issue comments never supply code, shell flags, or file paths.
        name = request.removeprefix("run ")
        root = Path(__file__).resolve().parents[1]
        recipes_dir = root / "ops" / "zssh-runner-recipes"
        recipe = recipes_dir / (name + ".py")
        if recipe.is_symlink() or not recipe.is_file():
            raise RequestRejected("reviewed_recipe_not_found")
        if recipe.resolve().parent != recipes_dir.resolve():
            raise RequestRejected("reviewed_recipe_outside_root")
        code, _ = run_checked([sys.executable, str(recipe)], cwd=str(root))
        if code:
            raise RuntimeError("reviewed_recipe_failed")
        return {"ok": True, "operation": request, "effect": "reviewed_recipe_passed"}
    raise RequestRejected("unsupported_operation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["validate", "execute"])
    parser.add_argument("--event", type=Path, default=Path(os.environ.get("GITHUB_EVENT_PATH", "")))
    args = parser.parse_args()
    if not args.event.is_file():
        print("zssh_bridge_rejected: missing_event", file=sys.stderr)
        return 2
    try:
        event = json.loads(args.event.read_text(encoding="utf-8"))
        request = parse_request(event, os.environ.get("GITHUB_ACTOR", ""))
        if args.mode == "validate":
            print("ZSSH_BRIDGE_AUTHORIZED " + request)
            return 0
        result = execute(request)
        # The only operational output is a JSON receipt: no environment, secrets,
        # stderr, arbitrary shell stdout, private files, or dynamic paths.
        print("ZSSH_BRIDGE_RECEIPT=" + json.dumps(result, sort_keys=True))
        return 0
    except (RequestRejected, RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # Error messages are machine-owned constants, not raw command stderr.
        print("zssh_bridge_rejected: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
