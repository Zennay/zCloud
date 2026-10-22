#!/usr/bin/env python3
"""Optional local-host headroom evidence for one exact zSentinel runner triage.

No queue DB, shell, service manager, network or writable filesystem source.
A host pressure measurement never proves GitHub assignment or safe restart.
"""
from __future__ import annotations

import math
import os
import socket
import stat
from datetime import datetime, timezone

from zsentinel_local_runner_observer import HOST
from zsentinel_vps_resource_observer import _memory_values

SCHEMA = "zsentinel-runner-host-pressure-v1"


def observe_host_pressure():
    """Sample root statvfs and bounded /proc/meminfo on the named VPS only."""
    if (os.name != "posix" or not hasattr(os, "O_NOFOLLOW")
            or not hasattr(os, "fstatvfs")
            or socket.gethostname().split(".", 1)[0].lower() != HOST):
        raise ValueError("runner-host capacity requires the intended Linux VPS")
    flags = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0))
    fd = os.open("/", flags)
    try:
        inode = os.fstat(fd)
        if not stat.S_ISDIR(inode.st_mode):
            raise ValueError("root filesystem is not a directory")
        capacity = os.fstatvfs(fd)
    finally:
        os.close(fd)
    if (capacity.f_blocks <= 0 or capacity.f_frsize <= 0
            or not 0 <= capacity.f_bavail <= capacity.f_blocks
            or not 0 <= capacity.f_bfree <= capacity.f_blocks
            or capacity.f_bavail > capacity.f_bfree
            or any(type(n) is not int or n < 0 for n in
                   (capacity.f_files, capacity.f_ffree, capacity.f_favail))
            or (capacity.f_files > 0 and
                (capacity.f_ffree > capacity.f_files
                 or capacity.f_favail > capacity.f_ffree))):
        raise ValueError("inconsistent root filesystem statistics")
    free_bytes = capacity.f_bavail * capacity.f_frsize
    free_gib = math.floor(free_bytes / 2**30 * 100) / 100
    unavailable_pct = math.ceil(
        (1 - capacity.f_bavail / capacity.f_blocks) * 10000) / 100
    inode_pct = (math.ceil((1 - capacity.f_favail / capacity.f_files) * 10000) / 100
                 if capacity.f_files > 0 else None)
    mem_available_mib, mem_total_mib, swap_free_mib, swap_total_mib = _memory_values()
    if (not all(math.isfinite(n) and n >= 0 for n in (
            free_gib, unavailable_pct, mem_available_mib,
            mem_total_mib, swap_free_mib, swap_total_mib))
            or not 0 <= unavailable_pct <= 100
            or swap_free_mib > swap_total_mib
            or (inode_pct is not None and not 0 <= inode_pct <= 100)):
        raise ValueError("invalid root capacity counters")
    # A configured but nearly exhausted swap device is meaningful pressure;
    # zero-sized swap is a configuration state, not a depleted resource.
    swap_status = "NO_SWAP_CONFIGURED" if swap_total_mib == 0 else "OBSERVED"
    swap_free_ratio = (
        swap_free_mib / swap_total_mib if swap_total_mib > 0 else None)
    critical_swap = (
        swap_free_ratio is not None and swap_free_ratio <= 0.05)
    constrained_swap = (
        swap_free_ratio is not None and swap_free_ratio <= 0.15)
    # Keep the severity strictly about measured host headroom. It cannot
    # establish which process filled disk or stalled a runner.
    critical_inodes = inode_pct is not None and inode_pct >= 95
    constrained_inodes = inode_pct is not None and inode_pct >= 90
    pressure = ("critical_headroom_observed"
                if free_gib < 2 or unavailable_pct >= 95 or critical_inodes
                or mem_available_mib < 512 or critical_swap else
                "constrained_headroom_observed"
                if free_gib < 4 or unavailable_pct >= 90 or constrained_inodes
                or mem_available_mib < 1024 or constrained_swap else
                "no_critical_pressure_in_this_sample")
    return {
        "schema": SCHEMA,
        "source": "same_host_root_fd_and_bounded_proc_meminfo",
        "observed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pressure_observation": pressure,
        "root_available_gib": free_gib,
        "root_unavailable_percent": unavailable_pct,
        "root_inode_unavailable_percent": inode_pct,
        "mem_available_mib": math.floor(mem_available_mib),
        "mem_total_mib": math.floor(mem_total_mib),
        "swap_free_mib": math.floor(swap_free_mib),
        "swap_total_mib": math.floor(swap_total_mib),
        "swap_status": swap_status,
        "github_assignment_verified": False,
        "runner_process_cause_verified": False,
        "runner_safe_idle_verified": False,
        "service_restart_authorized": False,
        "disk_cleanup_authorized": False,
        "queue_or_claims_changed": False,
        "mutations_performed": False,
    }


def owner_summary(observation):
    """Operator-facing, bounded and source-validated non-authorizing summary."""
    if not isinstance(observation, dict) or observation.get("schema") != SCHEMA:
        raise ValueError("invalid runner-host sample")
    if (observation.get("source") != "same_host_root_fd_and_bounded_proc_meminfo"
            or observation.get("pressure_observation") not in (
                "critical_headroom_observed",
                "constrained_headroom_observed",
                "no_critical_pressure_in_this_sample")
            or any(observation.get(k) is not False for k in (
                "github_assignment_verified", "runner_process_cause_verified",
                "runner_safe_idle_verified", "service_restart_authorized",
                "disk_cleanup_authorized", "queue_or_claims_changed",
                "mutations_performed"))):
        raise ValueError("untrusted runner-host authority")
    from datetime import datetime
    stamp = observation.get("observed_at_utc")
    if not isinstance(stamp, str) or not 1 <= len(stamp) <= 45:
        raise ValueError("missing bounded host observation time")
    instant = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("invalid host sample timezone")
    for key in ("root_available_gib", "root_unavailable_percent",
                "mem_available_mib", "mem_total_mib",
                "swap_free_mib", "swap_total_mib"):
        number = observation.get(key)
        if type(number) not in (int, float) or not math.isfinite(number) or number < 0:
            raise ValueError("untrusted host capacity metric")
    root_used = observation["root_unavailable_percent"]
    root_free = observation["root_available_gib"]
    memory = observation["mem_available_mib"]
    inodes = observation.get("root_inode_unavailable_percent")
    swap_total = observation["swap_total_mib"]
    swap_free = observation["swap_free_mib"]
    expected_swap_status = "NO_SWAP_CONFIGURED" if swap_total == 0 else "OBSERVED"
    if (observation.get("swap_status", expected_swap_status) != expected_swap_status
            or root_used > 100 or observation["mem_total_mib"] < memory
            or swap_total < swap_free
            or inodes is not None and
            (type(inodes) not in (int, float) or
             not math.isfinite(inodes) or not 0 <= inodes <= 100)):
        raise ValueError("inconsistent host capacity report")
    inode_label = (
        " · inodes niet beschikbaar"
        if inodes is None else " · inodes bezet " + str(inodes) + "%")
    swap_label = (
        " · swap niet geconfigureerd"
        if expected_swap_status == "NO_SWAP_CONFIGURED"
        else " · swap vrij " + str(swap_free) + "/" + str(swap_total) + " MiB")
    guidance = (
        "Hosteigenaar controleert rootdisk, inodes, geheugen/swap en bestaande jobs "
        "vóór een eventueel herstel; GEEN cleanup, jobstop of herstart vanuit deze kaart."
        if observation["pressure_observation"] == "critical_headroom_observed"
        else "Gebruik actuele GitHub runner-/jobidentiteit voor vervolgcontrole; "
             "dit is geen veilige-idle verklaring."
    )
    return (
        "Hostdruk (" + observation["pressure_observation"] + "): "
        + str(root_free) + " GiB vrij, schijf bezet " + str(root_used)
        + "%, geheugen vrij " + str(memory) + " MiB" + inode_label + swap_label
        + ". " + guidance
        + " Oorzaak, registratie en runner-herstel zijn NIET bewezen."
    )
