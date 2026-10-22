#!/usr/bin/env python3
"""Preflight path separation for a single zSentinel operator export run.

This admission never opens, reads, creates or deletes a file. The dedicated
create-only writers still pin their parent inode and reject symlink/race
changes at the actual filesystem operation. Call before provider reads, local
ledger locks, window-cursor writes and any first sidecar export.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping


def admit_private_paths(*, destinations: Mapping[str, Path | None],
                        sources: Mapping[str, Path | None]) -> None:
    """Reject aliased writes and read/write collisions across every sidecar.

    Existing --state and --window-state destinations can be updated by their
    respective owner-controlled state writer, but neither may share a path
    with another write target or an explicitly selected input. No attempt is
    made to infer ownership or authorize changes from a pathname.
    """
    if not isinstance(destinations, dict) or not isinstance(sources, dict):
        raise ValueError("private path admission needs explicit named mappings")
    occupied = {}
    occupied_inodes = {}
    for role, path in destinations.items():
        if path is None:
            continue
        if not isinstance(role, str) or not role or not isinstance(path, Path):
            raise ValueError("invalid private output destination")
        canonical = path.resolve(strict=False)
        if canonical in occupied:
            raise ValueError(
                "private outputs overlap: " + occupied[canonical] + " and " + role)
        occupied[canonical] = role
        # Different names may still be hardlinks to the same existing file.
        # This is especially important for reusable --state/--window-state:
        # a ledger must never be a writable alias for another source.
        try:
            info = path.stat()
        except FileNotFoundError:
            info = None
        if info is not None:
            inode = (info.st_dev, info.st_ino)
            if inode in occupied_inodes:
                raise ValueError(
                    "private output inode overlaps: " +
                    occupied_inodes[inode] + " and " + role)
            occupied_inodes[inode] = role
        # Operator reports are create-only. Refuse an existing path (including
        # a dangling symlink) before sending any requests or creating sibling
        # artifacts. Stateful cursors/ledgers deliberately may already exist.
        if role not in ("state", "window_state"):
            try:
                path.lstat()
            except FileNotFoundError:
                pass
            else:
                raise ValueError("private create-only output already exists: " + role)
            # Fail before potentially expensive GitHub/Notion reads when a
            # destination directory would be refused by the pinned private
            # writer anyway. Actual writes still recheck and pin its inode.
            parent = path.parent
            try:
                parent_info = parent.lstat()
            except OSError as exc:
                raise ValueError(
                    "private output directory unavailable: " + role) from exc
            if (not stat.S_ISDIR(parent_info.st_mode)
                    or parent_info.st_uid != os.geteuid()
                    or stat.S_IMODE(parent_info.st_mode) & 0o022):
                raise ValueError(
                    "private output directory is not owner-controlled: " + role)

    for role, path in sources.items():
        if path is None:
            continue
        if not isinstance(role, str) or not role or not isinstance(path, Path):
            raise ValueError("invalid private source path")
        if path.resolve(strict=False) in occupied:
            raise ValueError(
                "private destination overlaps input source: " + role)
        try:
            source_info = path.stat()
        except FileNotFoundError:
            source_info = None
        if (source_info is not None and
                (source_info.st_dev, source_info.st_ino) in occupied_inodes):
            raise ValueError(
                "private writable inode overlaps input source: " + role)
