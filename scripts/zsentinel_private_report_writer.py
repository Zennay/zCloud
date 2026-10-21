#!/usr/bin/env python3
"""Create-only, owner-directory-pinned private report output for zSentinel CLIs.

This utility writes one caller-requested file. It never creates directories,
overwrites existing paths, follows the final destination symlink, or removes a
different inode during error cleanup.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

MAX_PRIVATE_REPORT_BYTES = 6 * 1024 * 1024


def write_private_report(destination: Path, content: str, *,
                         max_bytes: int = MAX_PRIVATE_REPORT_BYTES) -> int:
    """Write UTF-8 mode-0600 text through a pinned, private directory FD."""
    if (not isinstance(destination, Path) or not isinstance(content, str)
            or type(max_bytes) is not int
            or not 1 <= max_bytes <= MAX_PRIVATE_REPORT_BYTES):
        raise ValueError("invalid private report arguments")
    destination = Path(os.path.abspath(destination))
    if destination.name in ("", ".", ".."):
        raise ValueError("invalid private report filename")
    data = content.encode("utf-8")
    if not 1 <= len(data) <= max_bytes:
        raise ValueError("private report is empty or exceeds its byte limit")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("private directory-bound no-follow writes unsupported")

    parent = destination.parent
    before = parent.lstat()
    if (not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o022):
        raise ValueError("private report directory must be owned and not writable by others")
    directory_fd = os.open(
        parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
        getattr(os, "O_CLOEXEC", 0))
    try:
        actual = os.fstat(directory_fd)
        if (not stat.S_ISDIR(actual.st_mode)
                or (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino)
                or actual.st_uid != os.geteuid()
                or stat.S_IMODE(actual.st_mode) & 0o022):
            raise ValueError("private report directory was replaced or became unsafe")
        fd = None
        created_inode = None
        try:
            fd = os.open(
                destination.name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW |
                getattr(os, "O_CLOEXEC", 0),
                0o600, dir_fd=directory_fd)
            info = os.fstat(fd)
            created_inode = (info.st_dev, info.st_ino)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("private report target is not a private regular inode")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as sink:
                fd = None
                sink.write(data)
                sink.flush()
                os.fsync(sink.fileno())
            os.fsync(directory_fd)
        except BaseException:
            if fd is not None:
                os.close(fd)
            # Remove only the inode this invocation successfully created.
            if created_inode is not None:
                try:
                    current = os.stat(
                        destination.name, dir_fd=directory_fd,
                        follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == created_inode:
                        os.unlink(destination.name, dir_fd=directory_fd)
                except OSError:
                    pass
            raise
    finally:
        os.close(directory_fd)
    return len(data)
