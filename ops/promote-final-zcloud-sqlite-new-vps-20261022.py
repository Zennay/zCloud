#!/usr/bin/env python3
"""Promote the FINAL, operator-fenced zCloud SQLite snapshot on the new VPS.

Read-only by default. This does not stop the old VPS, transfer data, activate
services or assert that a human-supplied fence receipt is independently proven.
The old-host owner must stop *all* writers and create a consistent SQLite backup.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile

NEW_HOST = "VM-7eb5ee16-2c71-440d-b7fd-8095d204e930"
OLD_HOST = "vps-bb300bba"
HOME = Path("/home/ubuntu")
ROOT = HOME / "zennay-cloud"
SOURCE = HOME / "zcloud-history-final.db"
TARGET = ROOT / "history.db"
BACKUPS = HOME / ".local/state/zcloud/cutover-backups"
LOCK = ROOT / ".history-final-cutover.lock"
TABLES = ("portfolio_queue", "runner_workers", "runner_targets")
COUNT_TABLES = (*TABLES, "runner_events")


class Refused(RuntimeError):
    pass


def refuse(message: str) -> None:
    raise Refused(message)


def require_host() -> int:
    if socket.gethostname() != NEW_HOST or os.geteuid() != 0:
        refuse("run as root on the exact NEW VPS only")
    account = pwd.getpwnam("ubuntu")
    return account.pw_uid


def real_directory(path: Path, uid: int) -> None:
    try:
        info = path.lstat()
    except OSError:
        refuse(f"required directory missing: {path}")
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid:
        refuse(f"directory must be real and owned by expected user: {path}")


def real_private_file(path: Path, uid: int) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError:
        refuse(f"required file missing: {path}")
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
            or info.st_nlink != 1 or info.st_mode & 0o077):
        refuse(f"file must be a private, singly-linked regular file: {path}")
    return info


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def no_sidecars(path: Path) -> None:
    # A database copied without its pending WAL is not a final SQLite backup.
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).exists() or Path(str(path) + suffix).is_symlink():
            refuse(f"unreconciled SQLite sidecar at {path.name}{suffix}")


def read_receipt(path: Path, uid: int, digest: str) -> dict:
    real_private_file(path, uid)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        refuse("fence receipt is missing or invalid JSON")
    if not isinstance(data, dict):
        refuse("fence receipt must be an object")
    if data.get("old_host") != OLD_HOST or data.get("new_host") != NEW_HOST:
        refuse("fence receipt refers to another host")
    for field in ("old_writer_fenced", "old_scheduler_inactive",
                  "old_browser_workers_inactive", "sqlite_backup_complete",
                  "pending_wal_checkpointed"):
        if data.get(field) is not True:
            refuse(f"old-host operator has not attested: {field}")
    if data.get("db_sha256") != digest:
        refuse("final database SHA-256 does not match fence receipt")
    try:
        when = datetime.fromisoformat(str(data["fenced_at_utc"]).replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError("naive timestamp")
        age = (datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds()
    except (ValueError, TypeError, KeyError, OverflowError):
        refuse("receipt requires a timezone-aware fenced_at_utc")
    if not 0 <= age <= 4 * 3600:
        refuse("old-host fence attestation must be recent (within four hours)")
    return data


def service_stopped(unit: str, *, user: bool = False) -> None:
    argv = ["systemctl"]
    if user:
        argv.extend(["--user", "-M", "ubuntu@"])
    argv.extend(["show", unit, "--property=ActiveState", "--value"])
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        refuse(f"cannot inspect {unit}; no assumption of safe inactivity")
    if result.returncode != 0 or result.stdout.strip() not in {"inactive", "failed"}:
        refuse(f"{unit} is active/unknown or status could not be proven")


def local_writer_gate() -> None:
    service_stopped("zennay-cloud.service")
    service_stopped("zennay-cloud.service", user=True)
    service_stopped("chatgpt-firefox.service", user=True)
    # The operator must additionally inventory unmanaged processes, timers
    # and active claims. systemd inactivity is necessary but not sufficient.


def db_facts(path: Path) -> dict:
    # Read-only connection; do not checkpoint or create an empty database.
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10) as db:
            db.execute("PRAGMA query_only=ON")
            status = db.execute("PRAGMA quick_check(1)").fetchone()
            if not status or status[0] != "ok":
                refuse(f"SQLite quick_check failed for {path.name}")
            found = {name for (name,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if not set(TABLES).issubset(found):
                refuse(f"required tables missing from {path.name}")
            counts = {}
            for name in COUNT_TABLES:
                if name in found:
                    counts[name] = int(db.execute(
                        f'SELECT COUNT(*) FROM "{name}"'
                    ).fetchone()[0])
            return {"tables": len(found), "counts": counts,
                    "bytes": path.stat().st_size}
    except sqlite3.Error as exc:
        refuse(f"SQLite read refused for {path.name}: {type(exc).__name__}")


def check_no_regression(incoming: dict, existing: dict | None) -> None:
    if existing is None:
        return
    for name, count in existing["counts"].items():
        if incoming["counts"].get(name, -1) < count:
            refuse(f"incoming {name} count is less than current target; reconcile first")


def ensure_backup_directory(uid: int) -> None:
    current = HOME
    real_directory(current, uid)
    for component in (".local", "state", "zcloud", "cutover-backups"):
        current = current / component
        if not current.exists() and not current.is_symlink():
            current.mkdir(mode=0o700)
            os.chown(current, uid, pwd.getpwnam("ubuntu").pw_gid)
        real_directory(current, uid)
        if current.stat().st_mode & 0o077:
            refuse(f"backup directory must not be group/world-accessible: {current}")


def unique_file(directory: Path, prefix: str) -> Path:
    fd, filename = tempfile.mkstemp(prefix=prefix, suffix=".sqlite3", dir=directory)
    os.close(fd)
    return Path(filename)


def sqlite_copy(source: Path, dest: Path) -> None:
    # SQLite's online backup includes a consistent snapshot, unlike cp of
    # a live WAL-mode DB. dest was exclusively created in a trusted directory.
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30) as src:
        with sqlite3.connect(dest, timeout=30) as dst:
            src.backup(dst, pages=1000, sleep=0.1)
    with dest.open("rb") as handle:
        os.fsync(handle.fileno())


def write_receipt(directory: Path, payload: dict, uid: int) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    receipt_path = directory / f"promoted-{stamp}.json"
    fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchown(fd, uid, pwd.getpwnam("ubuntu").pw_gid)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        # Do not delete the database or rollback automatically.
        raise
    return receipt_path


def promote(uid: int, incoming: dict, existing: dict | None,
            digest: str, fence: dict) -> dict:
    ensure_backup_directory(uid)
    lock_fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    stage = None
    rollback = None
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Re-check every gate under the lock; this lock only serializes this
        # script, it does not replace the old-writer fence.
        local_writer_gate()
        no_sidecars(SOURCE)
        no_sidecars(TARGET)
        if file_hash(SOURCE) != digest:
            refuse("final transferred SQLite changed since preflight")
        stage = unique_file(ROOT, ".history-final-stage-")
        sqlite_copy(SOURCE, stage)
        if db_facts(stage)["counts"] != incoming["counts"]:
            refuse("staged snapshot does not match source table counts")
        if file_hash(SOURCE) != digest:
            refuse("final source changed during SQLite backup")
        if TARGET.exists():
            rollback = unique_file(BACKUPS, "before-final-cutover-")
            sqlite_copy(TARGET, rollback)
            if db_facts(rollback)["counts"] != existing["counts"]:
                refuse("current target changed while backing up; refuse promotion")
            os.chown(rollback, uid, pwd.getpwnam("ubuntu").pw_gid)
            os.chmod(rollback, 0o600)
        os.chown(stage, uid, pwd.getpwnam("ubuntu").pw_gid)
        os.chmod(stage, 0o600)
        local_writer_gate()
        no_sidecars(TARGET)
        os.replace(stage, TARGET)
        stage = None
        directory_fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        outcome = {
            "status": "promoted_not_activated", "host": NEW_HOST,
            "new_history_db": str(TARGET), "incoming_sha256": digest,
            "incoming": incoming, "previous": existing,
            "rollback_backup": str(rollback) if rollback else None,
            "old_fenced_at_utc": fence["fenced_at_utc"],
            "promoted_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        outcome["receipt_path"] = str(write_receipt(BACKUPS, outcome, uid))
        return outcome
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)
        os.close(lock_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fence-receipt", type=Path, required=True,
                        help="private old-host operator JSON receipt, transferred securely")
    parser.add_argument("--apply", action="store_true",
                        help="atomically promote only after manual writer/claim fence")
    args = parser.parse_args()
    uid = require_host()
    real_directory(HOME, uid)
    real_directory(ROOT, uid)
    if not (ROOT / "server.py").is_file() or not (ROOT / "scripts/apply_worker_fleet.py").is_file():
        refuse("canonical zCloud checkout is incomplete")
    real_private_file(SOURCE, uid)
    no_sidecars(SOURCE)
    no_sidecars(TARGET)
    if TARGET.exists():
        real_private_file(TARGET, uid)
    if TARGET.exists() and SOURCE.samefile(TARGET):
        refuse("source and target refer to the same file")
    local_writer_gate()
    digest = file_hash(SOURCE)
    fence = read_receipt(args.fence_receipt, uid, digest)
    incoming = db_facts(SOURCE)
    existing = db_facts(TARGET) if TARGET.exists() else None
    check_no_regression(incoming, existing)
    result = {
        "status": "ready_to_promote" if not args.apply else "applying",
        "host": NEW_HOST, "source_sha256": digest,
        "incoming": incoming, "previous": existing,
        "operator_fence_attested_not_independently_proven": True,
        "requires": "no unmanaged old/new writers and no live queue claims",
    }
    if args.apply:
        result = promote(uid, incoming, existing, digest, fence)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Refused, OSError, sqlite3.Error, KeyError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(2)
