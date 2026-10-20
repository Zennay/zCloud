#!/usr/bin/env python3
"""Conservative, date-scoped cold zCloud directory offload to encrypted Google Drive."""
import argparse
import datetime as dt
import fcntl
import json
import os
import re
import stat
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path("/home/ubuntu")
ROOT = HOME / "zennay-cloud"
STATE = HOME / ".local/state/zcloud-drive"
BIN = HOME / ".local/bin/rclone"
DEST = "vpsarchive:zCloud-cold"
# Shared zCloud checkouts contain dated private/research directories from OTHER projects.
# A date alone is not permission to upload or prune their contents.
ARCHIVE_NAME = re.compile(r"^zcloud-(?:cold|archive|backup)-(20\d{6})(?:[-_][A-Za-z0-9_-]{1,80})?$")
EXCLUDE = {"repos", "worktrees", "scripts", "tests", "public", "deploy", ".git", ".github",
           "node_modules", ".venv", "backups", "dist", "build", "__pycache__"}
MIN_UPLOAD_DAYS = 2
MIN_PRUNE_DAYS = 7
MAX_BATCH_BYTES = 4 * 1024 ** 3
MAX_CANDIDATES = 8
MAX_SCAN_NODES = 100000

def command(*args, capture=False):
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if p.returncode:
        # Upstream stderr may contain tokens or private provider details.
        raise RuntimeError("Archive command failed (exit code %d)" % p.returncode)
    return p.stdout if capture else ""

def examine(path, cutoff):
    if path.is_symlink() or not path.is_dir() or path.parent != ROOT or path.name in EXCLUDE:
        return None
    match = ARCHIVE_NAME.fullmatch(path.name)
    if not match:
        return None
    try:
        if dt.datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=dt.timezone.utc).timestamp() >= cutoff:
            return None
    except ValueError:
        return None
    size = 0
    count = 0
    scanned = 0
    newest = 0
    try:
        device = path.lstat().st_dev
        for current, dirs, files in os.walk(path, followlinks=False):
            st = os.lstat(current)
            if not stat.S_ISDIR(st.st_mode) or st.st_dev != device:
                return None
            newest = max(newest, st.st_mtime)
            scanned += 1
            for name in dirs + files:
                child = Path(current) / name
                x = child.lstat()
                scanned += 1
                if (stat.S_ISLNK(x.st_mode) or x.st_dev != device or
                        (not stat.S_ISDIR(x.st_mode) and
                         (not stat.S_ISREG(x.st_mode) or x.st_nlink != 1))):
                    return None
                newest = max(newest, x.st_mtime)
                if stat.S_ISREG(x.st_mode):
                    size += x.st_size
                    count += 1
                    if size > MAX_BATCH_BYTES:
                        return None
                if scanned > MAX_SCAN_NODES:
                    return None
    except (OSError, PermissionError):
        return None
    if newest >= cutoff or count == 0:
        return None
    return {"name": path.name, "path": path, "bytes": size, "files": count, "latest_mtime": newest}

def candidates(age, limit=MAX_CANDIDATES):
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=age)).timestamp()
    results = []
    for path in ROOT.iterdir():
        v = examine(path, cutoff)
        if v:
            results.append(v)
    results.sort(key=lambda x: (x["latest_mtime"], x["name"]))
    return results[:limit]

def connected():
    try:
        command(str(BIN), "lsd", "gdrive:", "--max-depth", "1", capture=True)
        return True
    except Exception:
        return False

def active_process(path):
    # A working-directory reference is a definite block; checks cannot prove all files closed.
    prefix = str(path) + "/"
    proc = Path("/proc")
    for item in proc.iterdir():
        if not item.name.isdecimal():
            continue
        try:
            cwd = os.readlink(item / "cwd")
            if cwd == str(path) or cwd.startswith(prefix):
                return True
            exe = os.readlink(item / "exe")
            if exe == str(path) or exe.startswith(prefix):
                return True
        except (OSError, PermissionError):
            pass
    return False

def transfer(item, prune=False):
    src = item["path"]
    dst = DEST + "/" + item["name"]
    common = ["--transfers", "1", "--checkers", "2", "--tpslimit", "2",
              "--bwlimit", "4M", "--retries", "2", "--low-level-retries", "3"]
    command(str(BIN), "copy", str(src), dst, *common)
    # Compare actual remote bytes, not only size or a copy exit status.
    command(str(BIN), "check", str(src), dst, "--one-way", "--download", *common)
    if prune:
        if not (HOME / ".config/zcloud-drive/allow-prune").is_file():
            raise RuntimeError("Pruning requires the offline-key-backup approval file")
        old = examine(src, (dt.datetime.now(dt.timezone.utc) -
                            dt.timedelta(days=MIN_PRUNE_DAYS)).timestamp())
        if not old or old["bytes"] != item["bytes"] or old["files"] != item["files"] or active_process(src):
            raise RuntimeError("Source changed, insufficiently cold, or referenced by active process")
        # Never follow symlinks; do not remove any directory that is now active.
        shutil.rmtree(src)
        print("PRUNED VERIFIED COLD DIRECTORY:", src.name, flush=True)
    else:
        print("ARCHIVED AND VERIFIED, ORIGINAL PRESERVED:", src.name, flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["plan", "run", "cleanup"])
    args = parser.parse_args()
    # Planning does not create state, acquire a lock or contact OAuth.
    if args.mode == "plan":
        items = candidates(MIN_UPLOAD_DAYS)
        print(json.dumps({"mode": "read_only", "cold_days": MIN_UPLOAD_DAYS,
            "candidate_count": len(items), "scope": "zcloud-{cold,archive,backup}-YYYYMMDD[-suffix]",
            "max_candidates": MAX_CANDIDATES, "max_batch_bytes": MAX_BATCH_BYTES,
            "transfer_performed": False, "cleanup_authorized": False,
            "items": [{"name": x["name"], "bytes": x["bytes"], "files": x["files"]}
                      for x in items]}, indent=2))
        return
    STATE.mkdir(parents=True, exist_ok=True)
    lock = open(STATE / "archive.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another archive job is running; skip safely")
        return
    days = MIN_PRUNE_DAYS if args.mode == "cleanup" else MIN_UPLOAD_DAYS
    items = candidates(days)
    # Mutation modes alone connect to external storage.
    if not connected():
        print("NOT_CONNECTED: authorize Google Drive for the gdrive remote first", file=sys.stderr)
        return
    if args.mode == "cleanup" and not (HOME / ".config/zcloud-drive/allow-prune").exists():
        print("PRUNE_DISABLED: back up recovery keys off VPS and explicitly approve", file=sys.stderr)
        return
    total = 0
    for item in items:
        if total + item["bytes"] > MAX_BATCH_BYTES:
            continue
        try:
            transfer(item, prune=args.mode == "cleanup")
            total += item["bytes"]
        except Exception as e:
            print("FAILED, ORIGINAL PRESERVED:", item["name"], str(e), file=sys.stderr)
            sys.exit(5)
    print("COMPLETED:", args.mode, "bytes_considered:", total, flush=True)

if __name__ == "__main__":
    main()
