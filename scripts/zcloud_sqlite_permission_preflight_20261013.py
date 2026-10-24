#!/usr/bin/env python3
"""Non-mutating preflight for a production SQLite database used by deploy safe-idle.

Reports only filesystem permission metadata; never connects to SQLite, acquires
a write lock, chmods/chowns, runs sudo, or touches a live worker.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import stat
from pathlib import Path

DEFAULT_DB = Path("/home/ubuntu/zennay-cloud/history.db")
SIDECARS = ("-wal", "-shm", "-journal")


def inspect_path(path: Path, *, need_directory: bool = False) -> dict:
    record = {"exists": False}
    try:
        st = path.lstat()
    except OSError as exc:
        record["error"] = errno.errorcode.get(exc.errno, type(exc).__name__)
        return record
    record.update({
        "exists": True,
        "mode": oct(stat.S_IMODE(st.st_mode)),
        "owner_uid": st.st_uid,
        "owner_gid": st.st_gid,
        "kind": ("symlink" if stat.S_ISLNK(st.st_mode) else
                 "directory" if stat.S_ISDIR(st.st_mode) else
                 "regular" if stat.S_ISREG(st.st_mode) else "other"),
    })
    if record["kind"] != ("directory" if need_directory else "regular"):
        record["error"] = "noncanonical_file_kind"
        return record

    flags = os.O_RDONLY if need_directory else os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if need_directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        record["open_error"] = errno.errorcode.get(exc.errno, type(exc).__name__)
    else:
        os.close(fd)
        record["open_read" if need_directory else "open_rw"] = True
    return record


def snapshot(db: Path) -> dict:
    db = Path(os.path.abspath(db))
    parent = db.parent
    filesystem = {"readonly": None}
    try:
        vfs = os.statvfs(parent)
        filesystem["readonly"] = bool(vfs.f_flag & getattr(os, "ST_RDONLY", 1))
    except OSError as exc:
        filesystem["error"] = errno.errorcode.get(exc.errno, type(exc).__name__)

    files = {"database": inspect_path(db), "directory": inspect_path(parent, need_directory=True)}
    for suffix in SIDECARS:
        files[suffix[1:]] = inspect_path(Path(str(db) + suffix))

    failures = []
    if filesystem["readonly"] is not False:
        failures.append("filesystem_unknown_or_readonly")
    for name in ("database", "directory"):
        entry = files[name]
        if not entry.get("open_read" if name == "directory" else "open_rw"):
            failures.append(f"{name}_unwritable_or_missing")
    for name in ("wal", "shm", "journal"):
        entry = files[name]
        if entry.get("exists") and not entry.get("open_rw"):
            failures.append(f"{name}_unwritable_or_unsafe")
    if not os.access(parent, os.W_OK | os.X_OK, effective_ids=True):
        failures.append("directory_cannot_create_sqlite_sidecars")

    return {
        "schema": "zcloud-sqlite-permission-preflight-v1",
        "ok": not failures,
        "process_uid": os.geteuid(),
        "process_gid": os.getegid(),
        "filesystem": filesystem,
        "files": files,
        "failures": failures,
        "mutations_performed": False,
        "sqlite_connection_opened": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    result = snapshot(args.db)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
