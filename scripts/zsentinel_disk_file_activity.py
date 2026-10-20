#!/usr/bin/env python3
"""Bounded read-only /proc open-file observation for private disk pressure triage.

This is deliberately *positive evidence only*: seeing an open descriptor
means an observed reference, while seeing none NEVER means safe to delete.
No file content, process command line, PID, /proc environ or credentials
are collected or returned.
"""
from __future__ import annotations

import os
import stat
import time
from pathlib import Path

SCHEMA = "zsentinel-disk-open-references-v1"
MAX_CANDIDATES = 15
MAX_PROCESSES = 2048
MAX_HANDLES = 16384
MAX_SECONDS = 2


def _candidate(root: Path, relative: str, device: int):
    """Re-resolve each ancestor using no-follow directory FDs."""
    if not isinstance(relative, str) or not 1 <= len(relative) <= 1024:
        return None
    parts = relative.split("/")
    if (len(parts) > 6 or any(p in ("", ".", "..") or "\\" in p
                              for p in parts)):
        return None
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(root, flags)
    try:
        if os.fstat(fd).st_dev != device:
            return None
        for part in parts[:-1]:
            new_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = new_fd
            if os.fstat(fd).st_dev != device:
                return None
        item = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        if item.st_dev != device or not stat.S_ISREG(item.st_mode):
            return None
        return (item.st_dev, item.st_ino)
    except OSError:
        return None
    finally:
        os.close(fd)


def observe_open_references(root: Path, file_rows: list, *,
                            max_processes=MAX_PROCESSES, max_handles=MAX_HANDLES,
                            max_seconds=MAX_SECONDS):
    """Match selected file inodes against bounded /proc/*/fd metadata only."""
    if (os.name != "posix" or not Path("/proc").is_dir()
            or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")):
        raise ValueError("Linux /proc and nofollow descriptors required")
    if (type(max_processes) is not int or not 1 <= max_processes <= MAX_PROCESSES
            or type(max_handles) is not int or not 1 <= max_handles <= MAX_HANDLES
            or type(max_seconds) is not int or not 1 <= max_seconds <= MAX_SECONDS
            or not isinstance(file_rows, list) or len(file_rows) > MAX_CANDIDATES
            or not isinstance(root, Path) or not root.is_absolute()
            or root.is_symlink() or not root.is_dir()):
        raise ValueError("invalid bounded open-file observation")
    root = root.resolve(strict=True)
    device = root.stat().st_dev
    identities = {}
    for index, row in enumerate(file_rows):
        if type(row) is not dict:
            raise ValueError("invalid candidate record")
        identity = _candidate(root, row.get("relative_path"), device)
        # A rotated/replaced file must never inherit an old sampled inode's
        # open-reference classification. Both values came from the private
        # original metadata scan, not from a user supplied pathname.
        expected_dev, expected_inode = row.get("scan_device"), row.get("scan_inode")
        if (type(expected_dev) is not int or type(expected_inode) is not int
                or expected_dev < 0 or expected_inode < 0):
            raise ValueError("missing original candidate inode identity")
        if identity == (expected_dev, expected_inode):
            identities.setdefault(identity, []).append(index)
    matches = [0] * len(file_rows)
    observed_pids = [set() for _ in file_rows]
    started = time.monotonic()
    processes = handles = unreadable = 0
    exhausted = False
    # Zero matches cannot prove absence: future writers, other hosts,
    # permissions, races and the bounded sample remain unknown.
    if identities:
        with os.scandir("/proc") as entries:
            for process in entries:
                if time.monotonic() - started >= max_seconds or processes >= max_processes:
                    exhausted = True
                    break
                if not process.name.isascii() or not process.name.isdecimal():
                    continue
                processes += 1
                try:
                    with os.scandir("/proc/" + process.name + "/fd") as fds:
                        for descriptor in fds:
                            if handles >= max_handles or time.monotonic() - started >= max_seconds:
                                exhausted = True
                                break
                            if not descriptor.name.isascii() or not descriptor.name.isdecimal():
                                continue
                            handles += 1
                            try:
                                info = os.stat(descriptor.path)
                            except OSError:
                                unreadable += 1
                                continue
                            indices = identities.get((info.st_dev, info.st_ino))
                            if indices:
                                for index in indices:
                                    matches[index] += 1
                                    observed_pids[index].add(process.name)
                        if exhausted:
                            break
                except OSError:
                    unreadable += 1
                if exhausted:
                    break
    return {
        "schema": SCHEMA,
        "source": "bounded_local_proc_fd_metadata_only",
        "candidate_count": len(file_rows),
        "reidentified_candidates": sum(len(v) for v in identities.values()),
        "files_with_observed_open_references": sum(bool(x) for x in matches),
        "processes_sampled": processes,
        "descriptors_sampled": handles,
        "unreadable_or_raced_entries": unreadable,
        "budget_exhausted": exhausted,
        "coverage": "partial" if exhausted or unreadable else "sampled_not_exhaustive",
        "per_candidate": [
            {"index": i, "open_descriptors_observed": matches[i],
             "distinct_processes_observed": len(observed_pids[i]),
             "file_reidentified": any(i in indices for indices in identities.values()),
             "safe_to_delete": False, "writer_status": "unknown"}
            for i in range(len(file_rows))
        ],
        "safe_to_delete": False,
        "no_open_handles_proves_idle": False,
        "active_writer_verified": False,
        "files_deleted": False,
        "processes_stopped": False,
        "process_identifiers_disclosed": False,
    }


def private_markdown(report):
    if (type(report) is not dict or report.get("schema") != SCHEMA
            or report.get("safe_to_delete") is not False):
        raise ValueError("untrusted open reference observation")
    return ("Open bestandsverwijzingen (alleen lokaal /proc, metadatasteekproef): " +
            str(report["files_with_observed_open_references"]) + "/" +
            str(report["candidate_count"]) + " kandidaten met een aangetroffen open verwijzing. " +
            "Processen onderzocht: " + str(report["processes_sampled"]) +
            "; descriptors: " + str(report["descriptors_sampled"]) +
            "; bron: " + report["coverage"] +
            ". Een positieve verwijzing bewijst geen schrijfactie; geen gevonden verwijzing "
            "bewijst NOOIT dat een bestand ongebruikt of veilig verwijderbaar is.")
