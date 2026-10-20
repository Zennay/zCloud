#!/usr/bin/env python3
"""Private, explicit, create-only zSentinel operator artifacts.

All file operations are anchored to one owner-controlled directory inode.
No overwrite, queue/claim/release mutation, hidden network or background work.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

MAX_OUTPUT = 6 * 1024 * 1024
EXTENSIONS = frozenset((".html", ".json", ".md", ".ics", ".txt"))


def write_private_output(destination: Path, content: str, fmt: str) -> int:
    if (not isinstance(destination, Path)
            or not isinstance(content, str)
            or not isinstance(fmt, str)
            or ("." + fmt) not in EXTENSIONS):
        raise ValueError("invalid explicit private output")
    if (destination.suffix != "." + fmt
            or not destination.name or destination.name in (".", "..")):
        raise ValueError("output filename extension does not match private format")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("private directory-bound no-follow writes unsupported")
    data = (content + ("" if content.endswith("\n") else "\n")).encode("utf-8")
    if not 1 <= len(data) <= MAX_OUTPUT:
        raise ValueError("private output is empty or exceeds 6 MiB")

    parent = destination.parent
    # Validate the exact directory inode subsequently used by openat().
    # A path changing from a directory to a symlink between checks cannot
    # redirect the actual create-only write to a different parent.
    before = parent.lstat()
    if (not stat.S_ISDIR(before.st_mode)
            or before.st_uid != os.geteuid()
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
        flags = (os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW |
                 getattr(os, "O_CLOEXEC", 0))
        fd = None
        file_inode = None
        try:
            fd = os.open(destination.name, flags, 0o600, dir_fd=directory_fd)
            file_info = os.fstat(fd)
            file_inode = (file_info.st_dev, file_info.st_ino)
            if not stat.S_ISREG(file_info.st_mode) or file_info.st_nlink != 1:
                raise ValueError("not a private regular output inode")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as output:
                fd = None
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.fsync(directory_fd)
        except BaseException:
            if fd is not None:
                os.close(fd)
            # Only clean up our own just-created inode. Never unlink another
            # owner's replacement or a symlink, including on a failed fsync.
            if file_inode is not None:
                try:
                    current = os.stat(
                        destination.name, dir_fd=directory_fd,
                        follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == file_inode:
                        os.unlink(destination.name, dir_fd=directory_fd)
                except OSError:
                    pass
            raise
    finally:
        os.close(directory_fd)
    return len(data)
