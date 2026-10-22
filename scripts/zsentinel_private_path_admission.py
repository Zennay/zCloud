#!/usr/bin/env python3
"""Preflight path separation for a single zSentinel operator export run.

This admission never opens, reads, creates or deletes a file. The dedicated
create-only writers still pin their parent inode and reject symlink/race
changes at the actual filesystem operation. Call before provider reads, local
ledger locks, window-cursor writes and any first sidecar export.
"""
from __future__ import annotations

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

    for role, path in sources.items():
        if path is None:
            continue
        if not isinstance(role, str) or not role or not isinstance(path, Path):
            raise ValueError("invalid private source path")
        if path.resolve(strict=False) in occupied:
            raise ValueError(
                "private destination overlaps input source: " + role)
