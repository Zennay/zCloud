#!/usr/bin/env python3
"""Fail-closed, multi-project cold-storage archiver.

Only files placed in explicitly declared cold directories are eligible.
No wildcard cleanup, no system-wide age-based deletion, no live models.
A source is unlinked ONLY after byte-for-byte hash verification of its
encrypted rclone archive and a written remote manifest.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

SAFE_PROJECT = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
DENY_PARTS = {
    "active", "current", "live", "champion", "champions", "best",
    "production", "checkpoints", "latest", "models-live", ".git",
    "node_modules", "venv", ".venv", "sqlite", "db",
}
DENY_SUFFIXES = (
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3",
    ".lock", ".pid", ".sock", ".socket", ".partial", ".tmp",
)
CHUNK = 4 * 1024 * 1024

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()

def sha256_remote(rclone, destination):
    p = subprocess.Popen([rclone, "cat", destination, "--retries", "3"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    h = hashlib.sha256()
    try:
        for chunk in iter(lambda: p.stdout.read(CHUNK), b""):
            h.update(chunk)
    finally:
        p.stdout.close()
    if p.wait(timeout=120) != 0:
        raise RuntimeError("archive readback failed")
    return h.hexdigest()

def invoke(rclone, *args):
    subprocess.run([rclone, *args], check=True, timeout=7200,
                   stdout=subprocess.DEVNULL)

def safe_regular(path, owner_uid):
    try:
        s = path.lstat()
        if (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1
                or s.st_uid != owner_uid):
            return None
        return s
    except (FileNotFoundError, PermissionError):
        return None

def snapshot(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_uid)

def safe_subtree(root, sub):
    # Do not permit symlinks anywhere between root and cold directory.
    if not isinstance(sub, str) or not sub or sub.startswith("/"):
        return None
    parts = Path(sub).parts
    if any(x in {".", ".."} or x in DENY_PARTS for x in parts):
        return None
    target = root
    for part in parts:
        target = target / part
        if target.is_symlink() or not target.is_dir():
            return None
    return target

def dangerous(rel):
    return (any(p.lower() in DENY_PARTS for p in rel.parts) or
            rel.name.lower().endswith(DENY_SUFFIXES) or
            rel.name.startswith(".") or
            rel.name.lower() in {"status.json", "trainer.lock", "stop", "current.json", "live.json"})

def log(event, **kwargs):
    print(json.dumps({"event": event, "utc": datetime.now(timezone.utc).isoformat(),
                      **kwargs}, sort_keys=True), flush=True)

def archive_one(source, root, remote, project, rclone, apply, now, min_age, user_uid):
    rel = source.relative_to(root)
    if dangerous(rel):
        return 0
    if any(p.is_symlink() for p in source.parents if p != root and root in p.parents):
        return 0
    st = safe_regular(source, user_uid)
    if not st or st.st_size == 0 or now - st.st_mtime < min_age:
        return 0
    # Both mtime and ctime must be cold; this protects recently moved/written files.
    if now - st.st_ctime < min_age:
        return 0
    first = snapshot(st)
    log("candidate", project=project, source=str(source), bytes=st.st_size, apply=apply)
    if not apply:
        return 0
    checksum = sha256_file(source)
    # An immutable content-addressed destination avoids overwriting older snapshots.
    archive_path = f"{remote}/{project}/{rel.as_posix()}.{checksum}.archive"
    manifest_path = archive_path + ".manifest.json"
    if snapshot(safe_regular(source, user_uid) or st) != first:
        log("skipped_changed_before_upload", source=str(source))
        return 0
    invoke(rclone, "copyto", str(source), archive_path, "--retries", "3",
           "--transfers", "1", "--tpslimit", "3")
    if sha256_remote(rclone, archive_path) != checksum:
        raise RuntimeError(f"remote checksum mismatch for {source}")
    manifest = json.dumps({"project": project, "relative_path": rel.as_posix(),
                           "sha256": checksum, "bytes": st.st_size,
                           "original_mtime_ns": st.st_mtime_ns,
                           "archived_at": datetime.now(timezone.utc).isoformat()},
                          sort_keys=True).encode()
    # Send manifest without using temporary local files.
    subprocess.run([rclone, "rcat", manifest_path, "--retries", "3"],
                   input=manifest, check=True, timeout=120,
                   stdout=subprocess.DEVNULL)
    if snapshot(safe_regular(source, user_uid) or st) != first:
        log("preserved_changed_during_upload", project=project, source=str(source))
        return 0
    # A valid copy exists even if unlink fails; deletion never precedes verification.
    source.unlink()
    log("verified_archived_and_removed", project=project, source=str(source),
        destination=archive_path, bytes=st.st_size, sha256=checksum)
    return st.st_size

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--max-files", type=int, default=100)
    p.add_argument("--max-bytes", type=int, default=20 * 1024**3)
    p.add_argument("--state-dir", default="/var/lib/zcloud-cold-archive")
    args = p.parse_args()
    if args.max_files <= 0 or args.max_bytes <= 0:
        p.error("limits must be positive")
    config = json.loads(Path(args.config).read_text())
    remote = config["remote"].rstrip("/")
    if not remote.startswith("vpsarchive:zcloud-vps/"):
        raise SystemExit("REFUSED: non-approved encrypted archive remote")
    rclone = config.get("rclone", "/home/ubuntu/.local/bin/rclone")
    if not Path(rclone).is_file():
        raise SystemExit("REFUSED: rclone missing")
    # Apply requires an explicit operator-pinned opt-in in the deployed config,
    # not just an --apply flag from an accidental invocation.
    if args.apply and config.get("enable_pruning") is not True:
        raise SystemExit("REFUSED: enable_pruning is not true")
    state = Path(args.state_dir)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "archive.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("already_running")
            return
        count, reclaimed, seen = 0, 0, 0
        now = time.time()
        for project in config["projects"]:
            name = project["name"]
            if not SAFE_PROJECT.fullmatch(name):
                raise SystemExit("REFUSED: invalid project name")
            root = Path(project["root"])
            if not root.is_absolute() or root.is_symlink() or not root.is_dir():
                log("skipped_missing_root", project=name, root=str(root))
                continue
            if root.resolve() != root:
                raise SystemExit("REFUSED: project root contains symlinks")
            min_days = int(project.get("min_age_days", 30))
            if min_days < 7:
                raise SystemExit("REFUSED: min_age_days must be >=7")
            # Only ubuntu-owned files, and only explicitly cold-marked directories.
            uid = os.getuid()
            for sub in project.get("cold_directories", []):
                directory = safe_subtree(root, sub)
                if directory is None:
                    log("skipped_missing_cold_directory", project=name, sub=sub)
                    continue
                for source in directory.rglob("*"):
                    if count >= args.max_files or seen >= args.max_bytes:
                        break
                    try:
                        st = safe_regular(source, uid)
                        if st is None or st.st_size > (args.max_bytes - seen):
                            continue
                        if dangerous(source.relative_to(root)):
                            continue
                        if now - st.st_mtime < min_days*86400 or now - st.st_ctime < min_days*86400:
                            continue
                        seen += st.st_size
                        count += 1
                        reclaimed += archive_one(source, root, remote, name, rclone,
                                                 args.apply, now, min_days*86400, uid)
                    except (OSError, RuntimeError, subprocess.CalledProcessError,
                            subprocess.TimeoutExpired) as exc:
                        log("preserved_on_error", project=name, source=str(source),
                            error=str(exc)[:250])
                        # A failing archive must never turn into deletion.
                        continue
        log("complete", considered_files=count, candidate_bytes=seen,
            verified_reclaimed_bytes=reclaimed, apply=args.apply)

if __name__ == "__main__":
    main()
