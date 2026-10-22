#!/usr/bin/env python3
"""Read-only, fail-closed receipt for an offline final zCloud SQLite handoff.

This tool NEVER copies, checkpoints, repairs, truncates or overwrites SQLite.
It compares a consistent, already-transferred final source snapshot against
the inactive destination history.db before the new-VPS scheduler is enabled.
The old-host writer fence and transfer are separate operator responsibilities.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import sqlite3
import stat
import sys
from urllib.parse import quote

HOST = "VM-7eb5ee16-2c71-440d-b7fd-8095d204e930"
ROOT = Path("/home/ubuntu")
TARGET = ROOT / "zennay-cloud/history.db"
REQUIRED = frozenset({
    "portfolio_queue", "runner_targets", "runner_workers",
    "task_claims", "runtime_settings", "ai_global_slots",
    "runner_commands", "runner_events",
})


class Refused(Exception):
    pass


def sidecars(path: Path) -> list[str]:
    return [suffix for suffix in ("-wal", "-shm", "-journal")
            if Path(str(path) + suffix).exists()
            or Path(str(path) + suffix).is_symlink()]


def identity(path: Path, uid: int) -> os.stat_result:
    try:
        absolute = path.absolute()
        canonical = absolute.resolve(strict=True)
        if canonical != absolute or not canonical.is_relative_to(ROOT):
            raise Refused("database path must be direct, beneath /home/ubuntu")
        info = path.lstat()
    except (OSError, RuntimeError) as exc:
        raise Refused(f"database path unavailable ({type(exc).__name__})") from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid:
        raise Refused("database must be a regular ubuntu-owned file, not a link")
    if info.st_mode & 0o022 or info.st_nlink != 1:
        raise Refused("database is writable by another group/user or hardlinked")
    if sidecars(path):
        raise Refused("database has SQLite WAL/SHM/journal sidecars; take an offline snapshot first")
    return info


def stable(before: os.stat_result, after: os.stat_result) -> bool:
    return all(getattr(before, field) == getattr(after, field)
               for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"))


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def inspect(path: Path, uid: int) -> dict:
    before = identity(path, uid)
    # immutable=1 is safe ONLY because sidecars are rejected before and after
    # inspection; SQLite will not create a -shm or mutate the database.
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro&immutable=1"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
            conn.execute("PRAGMA query_only=ON")
            result = conn.execute("PRAGMA quick_check").fetchall()
            if result != [("ok",)]:
                raise Refused("SQLite quick_check failed")
            found = {name for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if not REQUIRED.issubset(found):
                raise Refused("required queue/worker/claim/command/event tables missing")
            counts = {name: conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                      for name in sorted(REQUIRED)}
            unresolved = conn.execute(
                "SELECT count(*) FROM portfolio_queue "
                "WHERE status IN ('claimed','running','verifying')"
            ).fetchone()[0]
            project_leases = conn.execute(
                "SELECT count(*) FROM task_claims "
                "WHERE lease_until IS NULL OR julianday(lease_until) IS NULL "
                "OR julianday(lease_until) >= julianday('now')"
            ).fetchone()[0]
            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            schema = hashlib.sha256(json.dumps(
                list(conn.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                )), separators=(",", ":"), ensure_ascii=True,
            ).encode("ascii")).hexdigest()
        fingerprint = digest(path)
    except sqlite3.Error as exc:
        raise Refused(f"SQLite read/verification failure ({type(exc).__name__})") from None
    after = identity(path, uid)
    if not stable(before, after):
        raise Refused("database changed during verification: writer fence not established")
    return {
        "bytes": before.st_size,
        "mtime_ns": before.st_mtime_ns,
        "sha256": fingerprint,
        "schema_sha256": schema,
        "user_version": user_version,
        "table_counts": counts,
        "unresolved_queue_rows": unresolved,
        "live_or_unknown_project_leases": project_leases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Consistent final old-VPS backup, already transferred to new host")
    parser.add_argument("--target", type=Path, default=TARGET)
    args = parser.parse_args()
    if socket.gethostname() != HOST:
        raise Refused("wrong host: verification is bound to the NEW VPS")
    uid = pwd.getpwnam("ubuntu").pw_uid
    if uid == 0 or os.getuid() != uid or os.geteuid() != uid:
        raise Refused("run unprivileged as ubuntu, never as root")
    if args.target.absolute() != TARGET:
        raise Refused("target must be the canonical zCloud history.db")
    if args.source.absolute() == args.target.absolute():
        raise Refused("source and destination may not be the same path")
    source = inspect(args.source, uid)
    target = inspect(args.target, uid)
    if source["sha256"] != target["sha256"] or source["bytes"] != target["bytes"]:
        raise Refused("source and target differ: no final handoff receipt")
    if source["schema_sha256"] != target["schema_sha256"] or (
        source["table_counts"] != target["table_counts"]
    ):
        raise Refused("schema/table counts differ despite file fingerprint agreement")
    if source["unresolved_queue_rows"] or source["live_or_unknown_project_leases"]:
        raise Refused("final source has outstanding queue/project claims; reconcile before activation")
    print(json.dumps({
        "status": "MATCHING_OFFLINE_SQLITE",
        "host": HOST,
        "database_bytes": source["bytes"],
        "sha256": source["sha256"],
        "schema_sha256": source["schema_sha256"],
        "user_version": source["user_version"],
        "table_counts": source["table_counts"],
        "unresolved_queue_rows": 0,
        "live_project_leases": 0,
        "old_host_writer_fence_independently_proven": False,
        "new_host_services_started_by_this_command": False,
        "note": "A file receipt does not prove old writers stopped or 10 browser workers are live.",
    }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Refused, OSError, KeyError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(1)
