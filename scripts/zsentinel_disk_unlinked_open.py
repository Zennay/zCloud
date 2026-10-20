#!/usr/bin/env python3
"""Bounded, path-free /proc metadata observation of unlinked regular files.

A removed filename can still have allocated filesystem blocks while a process
holds its file open. This collector never reads file contents, PID command
lines, fd symlink destinations, credentials, or exposes original filenames.
A positive observation is only a candidate for authorised operator review,
NEVER a signal to close a process or automatically reclaim space.
"""
from __future__ import annotations

import os
import stat
import time
from pathlib import Path

SCHEMA = "zsentinel-unlinked-open-files-v1"
MAX_PROCESSES = 2048
MAX_DESCRIPTORS = 16384
MAX_SECONDS = 2
MAX_INODES = 8192


def observe_unlinked(root: Path, *, max_processes=MAX_PROCESSES,
                     max_descriptors=MAX_DESCRIPTORS, max_seconds=MAX_SECONDS):
    from zsentinel_disk_hotspots import _root
    workspace = _root(root)
    if (os.name != "posix" or not Path("/proc").is_dir()
            or type(max_processes) is not int or not 1 <= max_processes <= MAX_PROCESSES
            or type(max_descriptors) is not int or not 1 <= max_descriptors <= MAX_DESCRIPTORS
            or type(max_seconds) is not int or not 1 <= max_seconds <= MAX_SECONDS):
        raise ValueError("bounded Linux /proc source required")
    device = workspace.stat().st_dev
    started = time.monotonic()
    pids_sampled = 0
    handles_sampled = 0
    unreadable = 0
    unlinked_handle_count = 0
    processes_with_unlinked = 0
    unique = {}
    exhausted = None

    with os.scandir("/proc") as processes:
        for process in processes:
            if time.monotonic() - started >= max_seconds:
                exhausted = "time_budget"
                break
            if pids_sampled >= max_processes:
                exhausted = "process_budget"
                break
            if not process.name.isascii() or not process.name.isdecimal():
                continue
            pids_sampled += 1
            process_positive = False
            try:
                # No readlink; only fstat-equivalent metadata on the
                # /proc/<pid>/fd/N magic link. No contents are opened.
                with os.scandir("/proc/" + process.name + "/fd") as fds:
                    for descriptor in fds:
                        if time.monotonic() - started >= max_seconds:
                            exhausted = "time_budget"
                            break
                        if handles_sampled >= max_descriptors:
                            exhausted = "descriptor_budget"
                            break
                        if not descriptor.name.isascii() or not descriptor.name.isdecimal():
                            continue
                        handles_sampled += 1
                        try:
                            info = os.stat(descriptor.path, follow_symlinks=True)
                        except (OSError, ValueError):
                            unreadable += 1
                            continue
                        if (info.st_dev != device or not stat.S_ISREG(info.st_mode)
                                or info.st_nlink != 0):
                            continue
                        key = (info.st_dev, info.st_ino)
                        if key not in unique and len(unique) >= MAX_INODES:
                            exhausted = "inode_budget"
                            break
                        allocated = max(0, int(info.st_blocks)) * 512
                        if allocated > 10**15:
                            unreadable += 1
                            continue
                        unique.setdefault(key, allocated)
                        unlinked_handle_count += 1
                        process_positive = True
                    # Never retain/process any PID outside this loop.
                if process_positive:
                    processes_with_unlinked += 1
                if exhausted:
                    break
            except (OSError, ValueError):
                unreadable += 1

    # Includes only POSITIVE references on the same filesystem. It is NOT
    # total missing capacity and never claims that deleted data is safe to
    # reclaim. A file may belong to another directory on the same mount.
    allocated_sample = sum(unique.values())
    return {
        "schema": SCHEMA,
        "source": "bounded_local_proc_fd_stat_same_mount_only",
        "coverage": "partial" if exhausted or unreadable else "bounded_sample_not_exhaustive",
        "budget_exhausted": exhausted,
        "processes_sampled": pids_sampled,
        "descriptors_sampled": handles_sampled,
        "unreadable_or_raced_entries": unreadable,
        "open_unlinked_regular_file_descriptors_observed": unlinked_handle_count,
        "distinct_unlinked_inodes_observed": len(unique),
        "processes_with_unlinked_descriptors_observed": processes_with_unlinked,
        "allocated_bytes_observed_sample_not_reclaimable": allocated_sample,
        "same_mount_does_not_prove_workspace_origin": True,
        "all_deleted_files_found": False,
        "space_reclaimable_verified": False,
        "active_writer_verified": False,
        "cleanup_authorized": False,
        "process_stop_authorized": False,
        "process_identifiers_disclosed": False,
        "file_names_disclosed": False,
        "file_contents_read": False,
        "mutations_performed": False,
    }


def _validate(report):
    if (type(report) is not dict or report.get("schema") != SCHEMA
            or report.get("source") != "bounded_local_proc_fd_stat_same_mount_only"
            or report.get("coverage") not in ("partial", "bounded_sample_not_exhaustive")
            or any(report.get(key) is not False for key in (
                "all_deleted_files_found", "space_reclaimable_verified",
                "active_writer_verified", "cleanup_authorized", "process_stop_authorized",
                "process_identifiers_disclosed", "file_names_disclosed",
                "file_contents_read", "mutations_performed"))
            or report.get("same_mount_does_not_prove_workspace_origin") is not True):
        raise ValueError("untrusted deleted-open-file evidence")
    for key in ("open_unlinked_regular_file_descriptors_observed",
                "distinct_unlinked_inodes_observed", "processes_sampled",
                "descriptors_sampled", "unreadable_or_raced_entries",
                "processes_with_unlinked_descriptors_observed",
                "allocated_bytes_observed_sample_not_reclaimable"):
        if type(report.get(key)) is not int or not 0 <= report[key] <= 10**15:
            raise ValueError("untrusted deleted-open-file counter")
    if (report["distinct_unlinked_inodes_observed"] > MAX_INODES
            or report["processes_sampled"] > MAX_PROCESSES
            or report["descriptors_sampled"] > MAX_DESCRIPTORS
            or report["processes_with_unlinked_descriptors_observed"] >
               report["processes_sampled"]):
        raise ValueError("unbounded deleted-open-file evidence")
    return report


def private_markdown(report):
    r = _validate(report)
    mib = round(r["allocated_bytes_observed_sample_not_reclaimable"] / 1048576, 1)
    return (
        "Verwijderde maar nog geopende gewone bestanden (lokale /proc-steekproef, "
        "zelfde bestandssysteem): " +
        str(r["distinct_unlinked_inodes_observed"]) + " unieke bestanden, " +
        str(r["open_unlinked_regular_file_descriptors_observed"]) +
        " open verwijzingen, " + str(mib) + " MiB waargenomen toegewezen blokken. "
        "Dekking: " + r["coverage"] + "; niet leesbare of verdwijnende items: " +
        str(r["unreadable_or_raced_entries"]) +
        ". Same mount betekent NIET dat deze bestanden bij de geselecteerde "
        "projectmap horen. De blokken zijn GEEN bewijs van vrij te maken ruimte; "
        "actieve schrijvers, procesbevoegdheid en verwijder-/stopveiligheid ONBEKEND. "
        "Geen proces stoppen, fd sluiten, bestand opruimen of service herstarten."
    )


def public_summary(report):
    r = _validate(report)
    return {
        "schema": SCHEMA,
        "coverage": r["coverage"],
        "unlinked_files_observed": r["distinct_unlinked_inodes_observed"],
        "open_descriptors_observed": r["open_unlinked_regular_file_descriptors_observed"],
        "observed_allocated_mib_not_reclaimable": round(
            r["allocated_bytes_observed_sample_not_reclaimable"] / 1048576, 1),
        "same_mount_only": True,
        "space_reclaimable_verified": False,
        "cleanup_authorized": False,
        "process_stop_authorized": False,
        "paths_or_pids_included": False,
    }
