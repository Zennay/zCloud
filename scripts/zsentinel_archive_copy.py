#!/usr/bin/env python3
"""Explicitly owner-approved single-file archive COPY, not migration/cleanup.

No implicit transfer from scanner hints. No source delete, move, truncate,
database, runner or claim mutation. Human must independently verify ownership,
licensing, writers, backup and transfer authority. This tool cannot do so.
All paths and SHA-256 values remain in an explicit private 0600 receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from zsentinel_disk_hotspots import _root, _open_directory_beneath, _sensitive
from zsentinel_archive_readiness import COLD_SUFFIXES
from zsentinel_private_output import write_private_output

SCHEMA = "zsentinel-owner-approved-archive-copy-v1"
APPROVAL = "I_APPROVE_ARCHIVE_COPY_NO_SOURCE_DELETE"
CHUNK = 1024 * 1024
MAX_SOURCE_BYTES = 4 * 1024**3
MAX_SECONDS = 1800
MIN_AGE_SECONDS = 7 * 86400
HEX = frozenset("0123456789abcdef")


def _segments(name):
    if (type(name) is not str or not 1 <= len(name) <= 300
            or name.startswith("/") or chr(0) in name):
        raise ValueError("choose one explicit relative file")
    segments = Path(name).parts
    if (not segments or len(segments) > 8
            or any(x in (".", "..", "") or len(x) > 100 or _sensitive(x)
                   or x.startswith(".") for x in segments)
            or not segments[-1].lower().endswith(COLD_SUFFIXES)):
        raise ValueError("unsafe, private, unsupported or unbounded source file")
    return segments


def _safe_leaf(name):
    if (type(name) is not str or not 1 <= len(name) <= 130
            or name in (".", "..") or name.startswith(".")
            or "/" in name or chr(0) in name or _sensitive(name)
            or not name.lower().endswith(COLD_SUFFIXES)):
        raise ValueError("explicit non-sensitive archive filename required")
    return name


def _source_open(source_root, relative):
    root = _root(source_root)
    pieces = _segments(relative)
    mount = root.stat().st_dev
    parent_fd = _open_directory_beneath(
        root, root.joinpath(*pieces[:-1]), mount)
    try:
        fd = os.open(pieces[-1],
                     os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW |
                     getattr(os, "O_CLOEXEC", 0),
                     dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_dev != mount
            or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or not 65536 <= info.st_size <= MAX_SOURCE_BYTES
            or time.time() - info.st_mtime < MIN_AGE_SECONDS):
        os.close(fd)
        raise ValueError("source is not an owner-controlled, old, bounded regular file")
    return root, fd, info


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _read_copy(src_fd, *, output_fd=None):
    digest = hashlib.sha256()
    transferred = 0
    started = time.monotonic()
    while True:
        if time.monotonic() - started >= MAX_SECONDS:
            raise ValueError("bounded transfer time exhausted")
        block = os.read(src_fd, CHUNK)
        if not block:
            break
        transferred += len(block)
        if transferred > MAX_SOURCE_BYTES:
            raise ValueError("source exceeded bounded size")
        digest.update(block)
        if output_fd is not None:
            view = memoryview(block)
            while view:
                wrote = os.write(output_fd, view)
                if wrote <= 0:
                    raise OSError("archive write incomplete")
                view = view[wrote:]
    return transferred, digest.hexdigest()


def _target_directory(archive_root, source_root):
    target = _root(archive_root)
    if (target == source_root or source_root in target.parents
            or target in source_root.parents
            or target.stat().st_dev == source_root.stat().st_dev):
        raise ValueError("destination must be an independent mounted filesystem")
    fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        meta = os.fstat(fd)
        fs = os.fstatvfs(fd)
        if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.geteuid()
                or meta.st_mode & 0o077
                or meta.st_dev != target.stat().st_dev
                or fs.f_frsize <= 0 or fs.f_bavail < 0
                or fs.f_bavail > fs.f_blocks):
            raise ValueError("private owner-controlled archive directory required")
        return fd, fs.f_bavail * fs.f_frsize
    except BaseException:
        os.close(fd)
        raise


def transfer(source_root: Path, relative: str, archive_root: Path,
             archive_filename: str, *, expected_sha256: str, confirmation: str):
    if confirmation != APPROVAL:
        raise ValueError("explicit owner copy approval missing")
    if (type(expected_sha256) is not str or len(expected_sha256) != 64
            or any(c not in HEX for c in expected_sha256)):
        raise ValueError("previously measured source SHA-256 required")
    leaf = _safe_leaf(archive_filename)
    source, src_fd, before = _source_open(source_root, relative)
    dest_dir_fd = None
    dest_fd = None
    created = False
    created_identity = None
    try:
        dest_dir_fd, available = _target_directory(archive_root, source)
        if available < before.st_size + 128 * 1024**2:
            raise ValueError("insufficient observed archive headroom")
        dest_fd = os.open(
            leaf, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW |
            getattr(os, "O_CLOEXEC", 0),
            0o600, dir_fd=dest_dir_fd)
        created = True
        created_info = os.fstat(dest_fd)
        created_identity = (created_info.st_dev, created_info.st_ino)
        length, digest = _read_copy(src_fd, output_fd=dest_fd)
        os.fsync(dest_fd)
        written = os.fstat(dest_fd)
        if (length != before.st_size or digest != expected_sha256
                or _identity(os.fstat(src_fd)) != _identity(before)
                or not stat.S_ISREG(written.st_mode)
                or written.st_size != length or written.st_mode & 0o077):
            raise ValueError("source drift, digest mismatch, or incomplete copy")
        os.close(dest_fd)
        dest_fd = None
        # Read back destination bytes instead of trusting successful writes.
        verify_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW,
                            dir_fd=dest_dir_fd)
        try:
            if _identity(os.fstat(verify_fd)) != _identity(written):
                raise ValueError("archive file identity changed")
            read_length, read_digest = _read_copy(verify_fd)
            if (read_length != length or read_digest != digest
                    or _identity(os.fstat(verify_fd)) != _identity(written)
                    or _identity(os.fstat(src_fd)) != _identity(before)):
                raise ValueError("archive read-back or source identity drift")
        finally:
            os.close(verify_fd)
        created = False  # Preserved validated copy; never delete source.
        return {
            "schema": SCHEMA,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_workspace": str(source),
            "source_relative_file": str(Path(*_segments(relative))),
            "archive_directory": str(_root(archive_root)),
            "archive_filename": leaf,
            "source_size_bytes": length,
            "source_sha256_verified": digest,
            "archive_sha256_readback_verified": True,
            "source_identity_stable_after_copy": True,
            "archive_copy_completed": True,
            "archive_copy_owner_confirmation_supplied": True,
            "source_deleted_or_changed_by_tool": False,
            "source_disk_space_reclaimed": False,
            "recovery_or_deploy_authorized": False,
            "queue_or_claim_modified": False,
            "next_step": "Owner independently validates availability, retention, encryption and restore before any separate source cleanup.",
        }
    except BaseException:
        # Only clean up THIS execution's exclusively created archive file.
        # NEVER alter original source or any pre-existing target.
        if dest_fd is not None:
            os.close(dest_fd)
        if created and dest_dir_fd is not None:
            # Never unlink an unrelated file after a concurrent target swap.
            try:
                remaining = os.stat(leaf, dir_fd=dest_dir_fd,
                                    follow_symlinks=False)
                if ((remaining.st_dev, remaining.st_ino) ==
                        created_identity and stat.S_ISREG(remaining.st_mode)):
                    os.unlink(leaf, dir_fd=dest_dir_fd)
            except OSError:
                pass
        raise
    finally:
        os.close(src_fd)
        if dest_dir_fd is not None:
            os.close(dest_dir_fd)


def inspect_digest(source_root: Path, relative: str):
    root, fd, before = _source_open(source_root, relative)
    try:
        length, digest = _read_copy(fd)
        if length != before.st_size or _identity(os.fstat(fd)) != _identity(before):
            raise ValueError("source changed during SHA-256 observation")
        return {
            "schema": "zsentinel-archive-copy-plan-v1",
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_workspace": str(root),
            "source_relative_file": str(Path(*_segments(relative))),
            "size_bytes": length,
            "source_sha256": digest,
            "copy_authorized": False,
            "file_active_writers_verified": False,
            "backup_verified": False,
            "target_write_verified": False,
            "source_deleted": False,
        }
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(
        description="Owner-reviewed one-file archive copy; no source deletion, no automatic migration")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--file", required=True)
    parser.add_argument("--mode", choices=("plan", "copy"), default="plan")
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--archive-filename")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--owner-confirm")
    parser.add_argument("--output", required=True, type=Path,
                        help="new private .json receipt in an existing controlled folder")
    args = parser.parse_args()
    try:
        if args.output.suffix != ".json":
            raise ValueError("private JSON receipt required")
        if args.mode == "plan":
            if (args.owner_confirm is not None or args.expected_sha256 is not None
                    or args.archive_filename is not None or
                    args.archive_root is not None):
                raise ValueError("plan must not supply copy authority")
            result = inspect_digest(args.root, args.file)
        else:
            if args.archive_root is None or args.archive_filename is None:
                raise ValueError("explicit destination required")
            result = transfer(
                args.root, args.file, args.archive_root, args.archive_filename,
                expected_sha256=args.expected_sha256, confirmation=args.owner_confirm)
        try:
            write_private_output(
                args.output, json.dumps(result, ensure_ascii=True, indent=2,
                                        sort_keys=True), "json")
        except (OSError, ValueError):
            if args.mode == "copy":
                parser.exit(3, "Archive copy may have completed but its private "
                            "receipt could not be saved. Do NOT retry without "
                            "verifying destination and source independently. "
                            "Original source remains untouched.\n")
            raise
        print("Private archive receipt created; source not deleted or changed.")
        return 0
    except (ValueError, OSError, TypeError, KeyError, OverflowError):
        parser.exit(2, "Archive operation refused or incomplete; no source cleanup.\n")


if __name__ == "__main__":
    raise SystemExit(main())
