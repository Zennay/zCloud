#!/usr/bin/env python3
"""Explicit, create-only private output for offline zSentinel operator reports.

Never touches runtime queues, Notion, GitHub, workers, deployments or ledgers.
Refuses overwrites; no hidden temp artifacts or automatic background writes.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

MAX_OUTPUT = 6 * 1024 * 1024
EXTENSIONS = frozenset((".html", ".json", ".md"))


def write_private_output(destination: Path, content: str, fmt: str) -> int:
    if (not isinstance(destination, Path)
            or not isinstance(content, str)
            or fmt not in ("html", "json", "md")):
        raise ValueError("invalid explicit private output")
    if destination.suffix != "." + fmt or not destination.name or destination.name in (".", ".."):
        raise ValueError("output filename must end in the selected .html, .json or .md")
    if destination.is_symlink() or destination.exists():
        raise ValueError("output file already exists; never overwrite")
    parent = destination.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("private destination directory must already exist and not be a symlink")
    metadata = parent.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("private destination must be a directory")
    data = (content + ("" if content.endswith("\n") else "\n")).encode("utf-8")
    if not 1 <= len(data) <= MAX_OUTPUT:
        raise ValueError("private output is empty or exceeds 6 MiB")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    file_descriptor = None
    opened = False
    try:
        file_descriptor = os.open(destination, flags, 0o600)
        opened = True
        with os.fdopen(file_descriptor, "wb") as target:
            file_descriptor = None
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
    except OSError:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if opened:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return len(data)
