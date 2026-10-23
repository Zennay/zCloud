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


MAX_PROC_MOUNTINFO_BYTES = 65536
_MOUNTINFO_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")
_MOUNTINFO_ALLOWED_ESCAPES = frozenset({"040", "011", "012", "134"})


def _decode_mountinfo_path(value: str) -> str | None:
    """Decode Linux mountinfo's documented path escapes; reject ambiguous syntax."""
    if re.search(r"\\(?![0-7]{3})", value):
        return None

    def decode(match: re.Match[str]) -> str:
        code = match.group(1)
        if code not in _MOUNTINFO_ALLOWED_ESCAPES:
            raise ValueError("unsupported_mountinfo_escape")
        return chr(int(code, 8))

    try:
        return _MOUNTINFO_OCTAL_ESCAPE.sub(decode, value)
    except ValueError:
        return None


def _mountinfo_target_state(path: str) -> str:
    """Return mounted/not_mounted/ambiguous/unknown from a bounded kernel snapshot."""
    raw = _read_bounded_proc_file("/proc/self/mountinfo", MAX_PROC_MOUNTINFO_BYTES)
    if raw is None:
        return "unknown"
    try:
        text = raw.decode("ascii")
    except UnicodeError:
        return "unknown"

    target = os.path.normpath(os.path.abspath(path))
    matches = 0
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            return "unknown"
        try:
            separator = fields.index("-")
        except ValueError:
            return "unknown"
        if separator < 6 or len(fields) < separator + 4:
            return "unknown"
        mount_point = _decode_mountinfo_path(fields[4])
        if mount_point is None or not mount_point.startswith("/"):
            return "unknown"
        if os.path.normpath(mount_point) == target:
            matches += 1
            if matches > 1:
                return "ambiguous"
    return "mounted" if matches == 1 else "not_mounted"


def _mountinfo_target_backing(path: str) -> str:
    """Privacy-safe mount backing hint from a bounded kernel source.

    A block device node is only a candidate, never physical-drive separation
    or permission to use that device. FUSE/network/virtual mounts are
    ineligible for local bulk-training storage even with large free space.
    """
    raw = _read_bounded_proc_file("/proc/self/mountinfo", MAX_PROC_MOUNTINFO_BYTES)
    if raw is None:
        return "unknown"
    try:
        value = raw.decode("ascii")
    except UnicodeError:
        return "unknown"
    target = os.path.normpath(os.path.abspath(path))
    matches: list[tuple[str, str]] = []
    for line in value.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            return "unknown"
        if separator < 6 or len(fields) < separator + 4:
            return "unknown"
        mount_point = _decode_mountinfo_path(fields[4])
        if mount_point is None or not mount_point.startswith("/"):
            return "unknown"
        if os.path.normpath(mount_point) == target:
            matches.append((fields[separator + 1].lower(), fields[separator + 2]))
            if len(matches) > 1:
                return "unknown"
    if len(matches) != 1:
        return "unknown"
    filesystem, source = matches[0]
    if (
        filesystem.startswith("fuse")
        or filesystem in {
            "nfs", "nfs4", "cifs", "smb3", "9p", "ceph", "overlay",
            "tmpfs", "ramfs", "autofs", "proc", "sysfs", "devtmpfs",
        }
    ):
        return "virtual_or_network"
    if filesystem not in {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs"}:
        return "unknown"
    if not re.fullmatch(r"/dev/[A-Za-z0-9_.\-/]+", source):
        return "unknown"
    if "/../" in source or source.endswith("/..") or "/./" in source:
        return "unknown"
    try:
        device = os.stat(source, follow_symlinks=True)
    except (OSError, ValueError):
        return "unknown"
    return "local_block_candidate" if stat.S_ISBLK(device.st_mode) else "unknown"


BULK_ARCHIVE_MIN_FREE_BYTES = 30 * 1024 ** 3


def _archive_bulk_storage_gate(archive: dict[str, object]) -> dict[str, object]:
    """Advisory-only 30 GiB gate; never claim a physical external training disk.

    The configured archive mount is not automatically the application's actual
    training/output path. Kernel mount identity, device writability for the
    current application user, and physical-drive independence are separate.
    """
    mount = archive.get("mount_detected")
    separate = archive.get("separate_filesystem")
    readonly = archive.get("read_only")
    available = archive.get("available_bytes")
    if mount is False:
        state, next_code = "mount_missing", "INSPECT_ARCHIVE_MOUNT_WITH_OWNER"
    elif mount is not True:
        state, next_code = "mount_unknown", "REFRESH_ARCHIVE_MOUNT_EVIDENCE"
    elif separate is False:
        state, next_code = "root_filesystem_shared", "ATTACH_SEPARATE_STORAGE_WITH_OWNER"
    elif separate is not True:
        state, next_code = "filesystem_identity_unknown", "REFRESH_ARCHIVE_DEVICE_EVIDENCE"
    elif readonly is True:
        state, next_code = "read_only_mount", "REQUEST_WRITABLE_ARCHIVE_WITH_OWNER"
    elif readonly is not False:
        state, next_code = "mount_writability_unknown", "REFRESH_ARCHIVE_WRITABILITY_EVIDENCE"
    elif archive.get("filesystem_backing") == "virtual_or_network":
        state, next_code = "virtual_or_network_mount", "REQUEST_LOCAL_BLOCK_DEVICE_WITH_OWNER"
    elif archive.get("filesystem_backing") != "local_block_candidate":
        state, next_code = "block_backing_unknown", "VERIFY_ARCHIVE_BLOCK_DEVICE_WITH_OWNER"
    elif type(available) is not int or available < 0:
        state, next_code = "free_space_unknown", "REFRESH_ARCHIVE_CAPACITY_EVIDENCE"
    elif available < BULK_ARCHIVE_MIN_FREE_BYTES:
        state, next_code = "below_30_gib", "REQUEST_LARGER_ARCHIVE_WITH_OWNER"
    else:
        # Even separate + writable + 30 GiB does not prove a second physical
        # drive, that the training path is mounted there, or app-user access.
        state, next_code = (
            "capacity_candidate_not_training_authorization",
            "VERIFY_PHYSICAL_DRIVE_AND_TRAINING_PATH_WITH_OWNER",
        )
    messages = {
        "mount_missing": "The configured archive is not mounted; request owner inspection before using it for large files.",
        "mount_unknown": "The archive mount source is not reliable; re-observe it without moving data.",
        "root_filesystem_shared": "The archive shares the root filesystem; it cannot isolate large files from the system disk.",
        "filesystem_identity_unknown": "Filesystem independence is unverified; do not infer external storage.",
        "read_only_mount": "The separate archive mount is read-only and cannot receive large files.",
        "mount_writability_unknown": "Archive mount writability is unverified; do not plan an offload.",
        "virtual_or_network_mount": "The archive is FUSE, network or virtual storage; it is not a local block-device bulk-training target.",
        "block_backing_unknown": "The archive has no verified local block-device backing; obtain owner inspection.",
        "free_space_unknown": "Archive free-space evidence is missing or invalid; do not infer capacity.",
        "below_30_gib": "The archive has less than 30 GiB free; request an owner-approved larger destination.",
        "capacity_candidate_not_training_authorization": (
            "The archive has at least 30 GiB, a separate non-read-only filesystem and a "
            "local block-device source hint, but its independent physical drive, training "
            "output path and application-user write access "
            "are not verified. Do not start training or move data from this hint."
        ),
    }
    return {
        "state": state,
        "operator_summary": messages[state],
        "minimum_free_bytes": BULK_ARCHIVE_MIN_FREE_BYTES,
        "minimum_free_gib": 30,
        "available_bytes_observed": (
            available if type(available) is int and available >= 0 else None
        ),
        "physical_drive_separation_verified": False,
        "training_output_path_verified": False,
        "training_user_write_permission_verified": False,
        "training_running_verified": False,
        "training_start_authorized": False,
        "archive_offload_authorized": False,
        "next_step_code": next_code,
    }


TRAINING_OUTPUT_PATH = "/opt/fuckball-training"


def _training_path_observation() -> dict[str, object]:
    """Report the actual fixed training output directory without writing to it.

    This observation must never replace the training launcher's own external
    block-device admission or claim that training has been started.
    """
    receipt: dict[str, object] = {
        "state": "unknown",
        "minimum_free_bytes": BULK_ARCHIVE_MIN_FREE_BYTES,
        "available_bytes": None,
        "separate_from_root_filesystem": None,
        "direct_mount_state": "unknown",
        "local_block_backing": "unknown",
        "mount_read_only": None,
        "physical_external_disk_verified": False,
        "dataset_checkpoint_temp_paths_verified": False,
        "application_write_permissions_verified": False,
        "training_running_verified": False,
        "training_start_authorized": False,
        "mutation_performed": False,
    }
    try:
        before = os.stat(TRAINING_OUTPUT_PATH, follow_symlinks=False)
        root = os.stat("/", follow_symlinks=True)
    except FileNotFoundError:
        receipt["state"] = "path_missing"
    except OSError:
        receipt["state"] = "path_unavailable"
    else:
        if not stat.S_ISDIR(before.st_mode):
            receipt["state"] = "path_not_directory_or_symlink"
        elif before.st_dev == root.st_dev:
            receipt["state"] = "training_output_on_root"
            receipt["separate_from_root_filesystem"] = False
        else:
            receipt["separate_from_root_filesystem"] = True
            try:
                mount_state = _mountinfo_target_state(TRAINING_OUTPUT_PATH)
                receipt["direct_mount_state"] = mount_state
                backing_before = _mountinfo_target_backing(TRAINING_OUTPUT_PATH)
                receipt["local_block_backing"] = backing_before
                vfs = os.statvfs(TRAINING_OUTPUT_PATH)
                flag = getattr(os, "ST_RDONLY", None)
                flags_before = getattr(vfs, "f_flag", None)
                if (type(flag) is not int or flag <= 0
                        or type(flags_before) is not int or flags_before < 0):
                    raise ValueError("training_mount_flags_unavailable")
                readonly_before = bool(flags_before & flag)
                block_size = int(vfs.f_frsize or vfs.f_bsize)
                free_blocks = int(vfs.f_bavail)
                blocks = int(vfs.f_blocks)
                if (block_size <= 0 or blocks <= 0
                        or free_blocks < 0 or free_blocks > blocks):
                    raise ValueError("training_capacity_invalid")
                available = block_size * free_blocks
                vfs_after = os.statvfs(TRAINING_OUTPUT_PATH)
                flags_after = getattr(vfs_after, "f_flag", None)
                after = os.stat(TRAINING_OUTPUT_PATH, follow_symlinks=False)
                if (not stat.S_ISDIR(after.st_mode)
                        or (before.st_dev, before.st_ino)
                        != (after.st_dev, after.st_ino)
                        or mount_state != _mountinfo_target_state(TRAINING_OUTPUT_PATH)
                        or backing_before != _mountinfo_target_backing(TRAINING_OUTPUT_PATH)
                        or type(flags_after) is not int or flags_after < 0
                        or bool(flags_after & flag) != readonly_before):
                    raise ValueError("training_path_source_changed")
                receipt["available_bytes"] = available
                receipt["mount_read_only"] = readonly_before
                if mount_state != "mounted":
                    receipt["state"] = "direct_mount_unverified"
                elif backing_before == "virtual_or_network":
                    receipt["state"] = "virtual_or_network_training_storage"
                elif backing_before != "local_block_candidate":
                    receipt["state"] = "training_block_device_unverified"
                elif readonly_before:
                    receipt["state"] = "training_mount_read_only"
                elif available < BULK_ARCHIVE_MIN_FREE_BYTES:
                    receipt["state"] = "training_space_below_30_gib"
                else:
                    receipt["state"] = "training_capacity_candidate_owner_proof_required"
            except (OSError, ValueError, TypeError, OverflowError, AttributeError):
                receipt["state"] = "training_source_unavailable_or_changed"
                receipt["available_bytes"] = None
                receipt["local_block_backing"] = "unknown"
                receipt["mount_read_only"] = None

    actions = {
        "path_missing": "VERIFY_TRAINING_PATH_EXISTS",
        "path_unavailable": "REQUEST_TRAINING_PATH_ACCESS",
        "path_not_directory_or_symlink": "RESOLVE_TRAINING_PATH_TYPE",
        "training_output_on_root": "HOLD_TRAINING_SYSTEM_DISK",
        "direct_mount_unverified": "VERIFY_TRAINING_MOUNT_MEMBERSHIP",
        "virtual_or_network_training_storage": "HOLD_TRAINING_FUSE_OR_NETWORK",
        "training_block_device_unverified": "VERIFY_TRAINING_BLOCK_DEVICE",
        "training_mount_read_only": "HOLD_TRAINING_READ_ONLY_MOUNT",
        "training_space_below_30_gib": "HOLD_TRAINING_INSUFFICIENT_SPACE",
        "training_capacity_candidate_owner_proof_required": "VERIFY_TRAINING_EXTERNAL_DRIVE_AND_PATHS",
        "training_source_unavailable_or_changed": "REFRESH_TRAINING_STORAGE_EVIDENCE",
    }
    summaries = {
        "path_missing": "Training output directory is missing; preserve the launcher gate.",
        "path_unavailable": "Training output source is inaccessible; do not infer mounted storage.",
        "path_not_directory_or_symlink": "Training output path is not a stable direct directory.",
        "training_output_on_root": "Training output is on the system filesystem. Do not start or resume bulk training.",
        "direct_mount_unverified": "Training directory is on another filesystem, but a stable direct block mount is unverified.",
        "virtual_or_network_training_storage": "Training output is FUSE, virtual or network-backed, not a local block device.",
        "training_block_device_unverified": "Training mount has no verified local block source.",
        "training_mount_read_only": "Training output mount is read-only.",
        "training_space_below_30_gib": "Training output mount has less than 30 GiB available.",
        "training_capacity_candidate_owner_proof_required": (
            "A local block-backed mount with at least 30 GiB is a candidate only; "
            "a distinct physical disk, all artifact/temp paths and app-user access "
            "remain unverified. Do not start training from this receipt."
        ),
        "training_source_unavailable_or_changed": "Training mount identity, flags or capacity changed or are unavailable.",
    }
    receipt["next_step_code"] = actions.get(receipt["state"], "REFRESH_TRAINING_STORAGE_EVIDENCE")
    receipt["operator_summary"] = summaries.get(
        receipt["state"], "Training storage evidence is unknown."
    )
    return receipt


def _host_capacity_observation() -> dict[str, object]:
    """Bounded resource pressure hints; never infer cause or authorize cleanup."""
    result: dict[str, object] = {
        "state": "partial",
        "coverage": "partial",
        "severity": "unknown",
        "observed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "load_average": {"1m": None, "5m": None, "15m": None, "cpu_count": None,
                         "max_load_per_cpu": None},
        "root_filesystem": {
            "total_bytes": None, "available_bytes": None,
            "available_percent": None, "used_percent": None,
            "inodes_total": None, "inodes_free": None, "inodes_available": None,
            "inodes_available_percent": None, "inodes_used_percent": None,
        },
        "memory": {"total_bytes": None, "available_bytes": None,
                   "available_percent": None},
        "swap": {"configured": None, "total_bytes": None, "free_bytes": None,
                 "used_percent": None},
        "archive_mount": {"configured": True, "mount_detected": None,
                          "mount_detection_basis": "unknown", "separate_filesystem": None,
                          "read_only": None, "filesystem_backing": "unknown",
                          "total_bytes": None, "available_bytes": None,
                          "available_percent": None},
        "bulk_storage_advisory": _archive_bulk_storage_gate({}),
        "training_output_observation": _training_path_observation(),
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
        total_inodes, free_inodes, available_inodes = (
            int(vfs.f_files), int(vfs.f_ffree), int(vfs.f_favail)
        )
        if (block_size <= 0 or total_blocks <= 0 or free_blocks > total_blocks
                or available_blocks > free_blocks):
            raise ValueError("invalid_root_filesystem_capacity")
        if (min(total_inodes, free_inodes, available_inodes) < 0
                or (total_inodes == 0 and
                    (free_inodes != 0 or available_inodes != 0))
                or (total_inodes > 0 and
                    (free_inodes > total_inodes or available_inodes > free_inodes))):
            raise ValueError("invalid_root_inode_capacity")
        total = block_size * total_blocks
        free = block_size * free_blocks
        available = block_size * available_blocks
        used = max(0, total - free)
        denominator = used + available
        root_capacity = {
            "total_bytes": total,
            "available_bytes": available,
            "available_percent": round(100 * available / total, 1),
            "used_percent": round(100 * used / denominator, 1) if denominator else 100.0,
            "inodes_total": total_inodes if total_inodes > 0 else None,
            "inodes_free": free_inodes if total_inodes > 0 else None,
            "inodes_available": available_inodes if total_inodes > 0 else None,
            "inodes_available_percent": (
                round(100 * available_inodes / total_inodes, 1)
                if total_inodes > 0 else None
            ),
            "inodes_used_percent": (
                round(100 * (total_inodes - free_inodes) / total_inodes, 1)
                if total_inodes > 0 else None
            ),
        }
        result["root_filesystem"] = root_capacity
        if total_inodes == 0:
            complete = False
            evidence_warnings.append("root_inode_capacity_unavailable")
    except (OSError, ValueError, TypeError, OverflowError, AttributeError, ZeroDivisionError):
        complete = False
        evidence_warnings.append("root_filesystem_capacity_observation_unavailable")

    archive = result["archive_mount"]
    mount_state = _mountinfo_target_state(ARCHIVE_MOUNT_PATH)
    archive["mount_detection_basis"] = {
        "mounted": "proc_self_mountinfo_exact_target",
        "not_mounted": "proc_self_mountinfo_no_exact_target",
        "ambiguous": "proc_self_mountinfo_ambiguous_target",
    }.get(mount_state, "unknown")

    if mount_state == "not_mounted":
        archive["mount_detected"] = False
        complete = False
        evidence_warnings.append("archive_mount_not_detected")
    elif mount_state == "ambiguous":
        archive["mount_detected"] = None
        complete = False
        evidence_warnings.append("archive_mount_ambiguous_multiple_entries")
    elif mount_state != "mounted":
        archive["mount_detected"] = None
        complete = False
        evidence_warnings.append("archive_mount_observation_unavailable")
    else:
        archive["mount_detected"] = True
        try:
            archive["filesystem_backing"] = _mountinfo_target_backing(ARCHIVE_MOUNT_PATH)
            archive_stat_before = os.stat(ARCHIVE_MOUNT_PATH, follow_symlinks=False)
            if not stat.S_ISDIR(archive_stat_before.st_mode):
                raise ValueError("archive_mount_path_not_directory")
            root_device = os.stat("/", follow_symlinks=True).st_dev
            archive_device = archive_stat_before.st_dev
            separate_filesystem = root_device != archive_device
            archive["separate_filesystem"] = separate_filesystem
            if not separate_filesystem:
                # A bind mount on root cannot provide independent offload space.
                evidence_warnings.append("archive_mount_same_filesystem_as_root")
            vfs = os.statvfs(ARCHIVE_MOUNT_PATH)
            read_only_flag = getattr(os, "ST_RDONLY", None)
            flags_before = getattr(vfs, "f_flag", None)
            if (type(read_only_flag) is int and read_only_flag > 0
                    and type(flags_before) is int and flags_before >= 0):
                read_only = bool(flags_before & read_only_flag)
                archive["read_only"] = read_only
                if read_only:
                    evidence_warnings.append("archive_mount_read_only_no_offload")
            else:
                read_only = None
                archive["read_only"] = None
                complete = False
                evidence_warnings.append("archive_mount_read_only_state_unknown")
            block_size = int(vfs.f_frsize or vfs.f_bsize)
            archive_free_blocks, archive_available_blocks = (
                int(vfs.f_bfree), int(vfs.f_bavail)
            )
            total_blocks = int(vfs.f_blocks)
            if (block_size <= 0 or total_blocks <= 0
                    or min(archive_free_blocks, archive_available_blocks) < 0
                    or archive_free_blocks > total_blocks
                    or archive_available_blocks > archive_free_blocks):
                raise ValueError("invalid_archive_filesystem_capacity")
            total, available = block_size * total_blocks, block_size * archive_available_blocks

            # Recheck both mount membership and path identity around the path-based
            # statvfs call; a changed target must not be credited as archive capacity.
            vfs_after = os.statvfs(ARCHIVE_MOUNT_PATH)
            flags_after = getattr(vfs_after, "f_flag", None)
            archive_stat_after = os.stat(ARCHIVE_MOUNT_PATH, follow_symlinks=False)
            if (
                not stat.S_ISDIR(archive_stat_after.st_mode)
                or (archive_stat_before.st_dev, archive_stat_before.st_ino)
                != (archive_stat_after.st_dev, archive_stat_after.st_ino)
                or _mountinfo_target_state(ARCHIVE_MOUNT_PATH) != "mounted"
            ):
                raise ValueError("archive_mount_changed_during_observation")
            if read_only is not None and (
                type(read_only_flag) is not int
                or type(flags_after) is not int
                or flags_after < 0
                or bool(flags_after & read_only_flag) != read_only
            ):
                raise ValueError("archive_mount_read_only_state_changed")
            if _mountinfo_target_backing(ARCHIVE_MOUNT_PATH) != archive["filesystem_backing"]:
                raise ValueError("archive_mount_backing_changed_during_observation")

            archive.update({
                "total_bytes": total,
                "available_bytes": available,
                "available_percent": round(100 * available / total, 1),
            })
        except (OSError, ValueError, TypeError, OverflowError, AttributeError, ZeroDivisionError) as exc:
            complete = False
            path_identity_lost = (
                str(exc) in ("archive_mount_changed_during_observation", "archive_mount_path_not_directory")
                or isinstance(exc, (FileNotFoundError, NotADirectoryError))
            )
            read_only_state_lost = str(exc) == "archive_mount_read_only_state_changed"
            if path_identity_lost:
                archive["mount_detected"] = None
                archive["mount_detection_basis"] = "unknown"
                archive["separate_filesystem"] = None
                archive["read_only"] = None
            if read_only_state_lost:
                archive["read_only"] = None
            backing_lost = str(exc) == "archive_mount_backing_changed_during_observation"
            if path_identity_lost or backing_lost:
                archive["filesystem_backing"] = "unknown"
            if path_identity_lost:
                evidence_warnings.append("archive_mount_changed_during_observation")
            elif backing_lost:
                evidence_warnings.append("archive_mount_backing_changed_during_observation")
            elif read_only_state_lost:
                evidence_warnings.append("archive_mount_read_only_state_changed")
            else:
                evidence_warnings.append("archive_mount_observation_unavailable")

    root = result["root_filesystem"]
    memory = result["memory"]
    swap = result["swap"]
    load = result["load_average"]
    archive = result["archive_mount"]
    if type(root.get("used_percent")) in (int, float) and root["used_percent"] >= 95:
        warnings.append("root_filesystem_critical")
    inode_available_percent = root.get("inodes_available_percent")
    inode_used_percent = root.get("inodes_used_percent")
    if (type(inode_available_percent) in (int, float) and inode_available_percent <= 1
            or type(inode_used_percent) in (int, float) and inode_used_percent >= 99):
        warnings.append("root_inodes_critical")
    elif (type(inode_available_percent) in (int, float) and inode_available_percent <= 5
          or type(inode_used_percent) in (int, float) and inode_used_percent >= 95):
        warnings.append("root_inode_pressure_high")
    if type(swap.get("used_percent")) in (int, float) and swap["used_percent"] >= 90:
        warnings.append("swap_pressure_high")
    if type(memory.get("available_percent")) in (int, float) and memory["available_percent"] <= 10:
        warnings.append("memory_pressure_high")
    if type(load.get("max_load_per_cpu")) in (int, float) and load["max_load_per_cpu"] >= 2:
        warnings.append("load_pressure_high")
    archive_available = archive.get("available_bytes")
    archive_has_capacity = (
        archive.get("mount_detected") is True
        and archive.get("separate_filesystem") is True
        and archive.get("read_only") is False
        and type(archive_available) is int and archive_available > 0
    )
    if (archive.get("mount_detected") is True
            and archive.get("separate_filesystem") is True
            and type(archive_available) is int and archive_available == 0):
        # Only a measured zero is "no space"; missing counters are unavailable.
        evidence_warnings.append("archive_mount_no_available_space")

    root_storage_critical = (
        "root_filesystem_critical" in warnings or "root_inodes_critical" in warnings
    )
    if root_storage_critical:
        if archive_has_capacity:
            summary = (
                "Root storage is critically constrained by filesystem bytes or inodes, and the configured archive "
                "mount reports capacity on a separate filesystem. Verify file provenance, encryption/retention "
                "policy and active handles before owner-approved offload; no deletion, move or restart is authorized."
            )
        elif archive.get("read_only") is True:
            summary = (
                "Root storage is critically constrained by filesystem bytes or inodes, but the configured archive "
                "mount is read-only and cannot be used as an offload target. Preserve active work and obtain an "
                "authorized writable-storage inventory; no deletion, move or restart is authorized by this snapshot."
            )
        elif (archive.get("mount_detected") is True
              and archive.get("separate_filesystem") is False):
            summary = (
                "Root storage is critically constrained by filesystem bytes or inodes, but the configured archive "
                "path shares the root filesystem; "
                "it is not a separate offload target. Preserve active work and obtain an authorized storage "
                "inventory; no deletion, move or restart is authorized by this snapshot."
            )
        elif archive.get("mount_detected") is True and archive.get("read_only") is None:
            summary = (
                "Root storage is critically constrained by filesystem bytes or inodes, but the configured archive "
                "mount's read-only state is unknown. Preserve active work and do not offload until the owner "
                "confirms a writable, separate storage target."
            )
        elif archive.get("mount_detected") is True:
            summary = (
                "Root storage is critically constrained by filesystem bytes or inodes, but the configured archive "
                "mount has no verified separate "
                "available capacity. Preserve active work and obtain an authorized storage inventory; no deletion, "
                "move or restart is authorized by this snapshot."
            )
        else:
            summary = (
                "Root storage is critically constrained by filesystem bytes or inodes. Preserve active work and "
                "obtain an authorized storage inventory; "
                "do not delete files, move data or restart services from this snapshot."
            )
    elif warnings:
        summary = (
            "Resource pressure threshold(s) observed. This is not proof of runner/job failure; "
            "reconcile service identity, GitHub registration and job assignment separately."
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
    if evidence_warnings:
        summary += (
            " Capacity evidence caveat(s): " + ", ".join(evidence_warnings)
            + ". Do not offload or infer safe storage until verified by the authorized owner."
        )
    severity = (
        "critical" if root_storage_critical else
        "high" if warnings else
        "normal" if complete else "unknown"
    )
    # Capacity alone is never training admission. Compute one stable advisory
    # after all mount, capacity, identity and read-only observations settle.
    result["bulk_storage_advisory"] = _archive_bulk_storage_gate(archive)
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
    bulk = capacity.get("bulk_storage_advisory")
    if isinstance(bulk, dict):
        state_to_code = {
            "mount_missing": "ARCHIVE_BULK_MOUNT_MISSING",
            "mount_unknown": "ARCHIVE_BULK_MOUNT_EVIDENCE_UNKNOWN",
            "root_filesystem_shared": "ARCHIVE_BULK_NOT_SEPARATE_FROM_ROOT",
            "filesystem_identity_unknown": "ARCHIVE_BULK_FILESYSTEM_EVIDENCE_UNKNOWN",
            "read_only_mount": "ARCHIVE_BULK_READ_ONLY",
            "mount_writability_unknown": "ARCHIVE_BULK_WRITE_STATUS_UNKNOWN",
            "virtual_or_network_mount": "ARCHIVE_BULK_REJECT_VIRTUAL_OR_NETWORK",
            "block_backing_unknown": "ARCHIVE_BULK_BLOCK_DEVICE_NOT_VERIFIED",
            "free_space_unknown": "ARCHIVE_BULK_CAPACITY_UNKNOWN",
            "below_30_gib": "ARCHIVE_BULK_BELOW_30_GIB",
            "capacity_candidate_not_training_authorization": (
                "ARCHIVE_BULK_CAPACITY_CANDIDATE_REQUIRES_EXTERNAL_PROOF"
            ),
        }
        codes.append(state_to_code.get(
            bulk.get("state"), "ARCHIVE_BULK_GATE_UNKNOWN"
        ))
    else:
        codes.append("ARCHIVE_BULK_GATE_UNKNOWN")
    # The actual output directory is a separate observation from the archive:
    # a healthy archive does not mean active training writes avoid root.
    training = capacity.get("training_output_observation")
    training_actions = {
        "VERIFY_TRAINING_PATH_EXISTS",
        "REQUEST_TRAINING_PATH_ACCESS",
        "RESOLVE_TRAINING_PATH_TYPE",
        "HOLD_TRAINING_SYSTEM_DISK",
        "VERIFY_TRAINING_MOUNT_MEMBERSHIP",
        "HOLD_TRAINING_FUSE_OR_NETWORK",
        "VERIFY_TRAINING_BLOCK_DEVICE",
        "HOLD_TRAINING_READ_ONLY_MOUNT",
        "HOLD_TRAINING_INSUFFICIENT_SPACE",
        "VERIFY_TRAINING_EXTERNAL_DRIVE_AND_PATHS",
        "REFRESH_TRAINING_STORAGE_EVIDENCE",
    }
    if (isinstance(training, dict)
            and training.get("next_step_code") in training_actions):
        codes.append(str(training["next_step_code"]))
    else:
        codes.append("REFRESH_TRAINING_STORAGE_EVIDENCE")
    evidence_warnings = capacity.get("evidence_warnings", [])
    if isinstance(evidence_warnings, list):
        if any(reason in evidence_warnings for reason in (
            "archive_mount_not_detected",
            "archive_mount_observation_unavailable",
            "archive_mount_ambiguous_multiple_entries",
            "archive_mount_changed_during_observation",
            "archive_mount_backing_changed_during_observation",
        )):
            codes.append("ARCHIVE_MOUNT_UNAVAILABLE_NO_OFFLOAD")
        if "archive_mount_read_only_no_offload" in evidence_warnings:
            codes.append("ARCHIVE_MOUNT_READ_ONLY_NO_OFFLOAD")
        if any(reason in evidence_warnings for reason in (
                "archive_mount_read_only_state_unknown",
                "archive_mount_read_only_state_changed")):
            codes.append("ARCHIVE_MOUNT_READ_ONLY_STATE_UNCERTAIN_NO_OFFLOAD")
        if "archive_mount_same_filesystem_as_root" in evidence_warnings:
            codes.append("ARCHIVE_MOUNT_NOT_SEPARATE_FROM_ROOT_NO_OFFLOAD")
        if "archive_mount_no_available_space" in evidence_warnings:
            codes.append("ARCHIVE_MOUNT_NO_CAPACITY_NO_OFFLOAD")
    # Keep the actionable cause visible in the structured handoff; the
    # generic severity alone cannot distinguish inode, swap, memory or load.
    pressure_codes = {
        "root_filesystem_critical": "ROOT_FILESYSTEM_CRITICAL",
        "root_inode_pressure_high": "ROOT_INODE_PRESSURE_HIGH",
        "root_inodes_critical": "ROOT_INODES_CRITICAL",
        "swap_pressure_high": "SWAP_PRESSURE_HIGH",
        "memory_pressure_high": "MEMORY_PRESSURE_HIGH",
        "load_pressure_high": "LOAD_PRESSURE_HIGH",
    }
    observed_pressure_warnings = capacity.get("warnings", [])
    if isinstance(observed_pressure_warnings, list):
        for warning in observed_pressure_warnings:
            if warning in pressure_codes:
                codes.append(pressure_codes[warning])
    severity = capacity.get("severity")
    if severity == "critical":
        if "root_filesystem_critical" in capacity.get("warnings", []):
            codes.append("HOLD_DISK_WRITES_REQUEST_OWNER_INVENTORY")
        if "root_inodes_critical" in capacity.get("warnings", []):
            codes.append("HOLD_STORAGE_WRITES_REQUEST_OWNER_INVENTORY")
        if not any(code in capacity.get("warnings", []) for code in
                   ("root_filesystem_critical", "root_inodes_critical")):
            codes.append("HOLD_STORAGE_WRITES_REQUEST_OWNER_INVENTORY")
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
