#!/usr/bin/env python3
"""Recheck a prior owner-approved archive copy receipt without any writes to data.

Read-only bytes from an already copied file may show present integrity, but
not remote backup durability, legal permission, successful restore, or proof
that the original source is safe to delete.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from zsentinel_archive_copy import (
    SCHEMA as COPY_SCHEMA, MAX_SOURCE_BYTES, _read_copy, _identity,
    _safe_leaf, _target_directory, _admit_private_receipt,
)
from zsentinel_disk_hotspots import _root
from zsentinel_private_output import write_private_output

SCHEMA = "zsentinel-external-archive-recheck-v1"
MAX_RECEIPT_BYTES = 12 * 1024


def _strict_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate key in private copy receipt")
        obj[key] = value
    return obj


def read_copy_receipt(path: Path):
    if (not isinstance(path, Path) or not path.is_absolute()
            or path.suffix != ".json" or path.is_symlink()):
        raise ValueError("explicit private receipt required")
    directory = path.parent
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("private receipt directory unavailable")
    parent_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        folder = os.fstat(parent_fd)
        if folder.st_uid != os.geteuid() or folder.st_mode & 0o077:
            raise ValueError("receipt directory must be controlled by same owner")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW |
                     os.O_NONBLOCK, dir_fd=parent_fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or not 100 <= info.st_size <= MAX_RECEIPT_BYTES):
                raise ValueError("unsafe or oversized private receipt")
            raw = os.read(fd, MAX_RECEIPT_BYTES + 1)
            if len(raw) != info.st_size or _identity(os.fstat(fd)) != _identity(info):
                raise ValueError("copy receipt changed during read")
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)
    receipt = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs)
    if (type(receipt) is not dict or receipt.get("schema") != COPY_SCHEMA
            or receipt.get("archive_copy_completed") is not True
            or receipt.get("archive_sha256_readback_verified") is not True
            or receipt.get("source_deleted_or_changed_by_tool") is not False
            or receipt.get("source_disk_space_reclaimed") is not False):
        raise ValueError("not a completed zSentinel owner-only archive copy receipt")
    return receipt


def observe_archived_copy(receipt: dict):
    if (type(receipt) is not dict or receipt.get("schema") != COPY_SCHEMA
            or receipt.get("archive_copy_completed") is not True
            or receipt.get("source_deleted_or_changed_by_tool") is not False):
        raise ValueError("invalid prior approved copy source")
    from zsentinel_archive_copy import HEX
    checksum = receipt.get("source_sha256_verified")
    size = receipt.get("source_size_bytes")
    if (type(checksum) is not str or len(checksum) != 64
            or any(c not in HEX for c in checksum)
            or type(size) is not int or not 65536 <= size <= MAX_SOURCE_BYTES):
        raise ValueError("invalid prior exact source digest or byte length")
    archive = _root(Path(receipt["archive_directory"]))
    source = _root(Path(receipt["source_workspace"]))
    leaf = _safe_leaf(receipt["archive_filename"])
    fd_dir, _ = _target_directory(archive, source)
    try:
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=fd_dir)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode)
                    or before.st_size != size or before.st_uid != os.geteuid()
                    or before.st_nlink != 1 or before.st_mode & 0o077):
                raise ValueError("archived copy changed identity or size")
            read_len, digest = _read_copy(fd)
            if (read_len != size or digest != checksum
                    or _identity(os.fstat(fd)) != _identity(before)):
                raise ValueError("archived copy content drift or readback mismatch")
        finally:
            os.close(fd)
    finally:
        os.close(fd_dir)
    return {
        "schema": SCHEMA,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "archive_directory": str(archive),
        "archive_filename": leaf,
        "receipt_source_sha256": checksum,
        "archive_bytes_read": read_len,
        "archive_content_matches_prior_receipt_at_observation": True,
        "remote_storage_long_term_durability_verified": False,
        "independent_restore_success_verified": False,
        "database_writer_absence_verified": False,
        "source_disk_space_reclaimed": False,
        "source_cleanup_authorized": False,
        "archive_file_or_source_mutated": False,
        "next_step": (
            "Separate authorized owner restore, access and retention proof "
            "required before any independent source cleanup decision."
        ),
    }


def main():
    p = argparse.ArgumentParser(description="Recheck prior owner-approved external archive bytes")
    p.add_argument("--copy-receipt", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path,
                   help="new private create-only 0600 JSON integrity observation")
    args = p.parse_args()
    try:
        _admit_private_receipt(args.output)
        if args.output.resolve() == args.copy_receipt.resolve():
            raise ValueError("cannot overwrite input receipt")
        previous = read_copy_receipt(args.copy_receipt)
        report = observe_archived_copy(previous)
        write_private_output(
            args.output, json.dumps(report, ensure_ascii=True, sort_keys=True,
                                    indent=2), "json")
        print("Private archive readback receipt created; original data untouched.")
        return 0
    except (OSError, ValueError, TypeError, UnicodeError, KeyError):
        p.exit(2, "Archive readback unavailable; no source deletion authorized.\n")


if __name__ == "__main__":
    raise SystemExit(main())
