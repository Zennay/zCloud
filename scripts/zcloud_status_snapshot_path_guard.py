"""Offline fail-closed status snapshot path preflight.

This module does not read snapshot contents or authorize runtime recovery.
"""
from __future__ import annotations

import os
from pathlib import Path


class UnsafeStatusPath(ValueError):
    """Status snapshot path cannot be trusted."""


def validate_status_snapshot_path(candidate: str | os.PathLike[str], trusted_root: str | os.PathLike[str]) -> Path:
    """Return a regular snapshot path only when every component is non-symlink.

    The caller must supply an absolute, preconfigured trusted root. No file is
    opened, no service is restarted, and no status is considered healthy here.
    """
    root = Path(trusted_root)
    path = Path(candidate)
    if not root.is_absolute() or not path.is_absolute() or ".." in root.parts or ".." in path.parts:
        raise UnsafeStatusPath("absolute normalized paths required")
    if not path.is_relative_to(root) or path == root:
        raise UnsafeStatusPath("snapshot outside trusted root")
    for item in (root, *root.parents):
        if item.is_symlink():
            raise UnsafeStatusPath("symlink in trusted root ancestry")
    for item in path.relative_to(root).parts:
        root = root / item
        if root.is_symlink():
            raise UnsafeStatusPath("symlink in snapshot ancestry")
    if not path.is_file():
        raise UnsafeStatusPath("snapshot must be a regular file")
    return path
