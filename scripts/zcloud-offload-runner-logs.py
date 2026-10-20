#!/usr/bin/env python3
"""Move *cold* GitHub runner diagnostics to encrypted remote; verify full readback before prune.

Active/recent logs and active runner workspaces are never touched. An archived
.log can be restored using rclone copyto + tar -I zstd -xf.
"""
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time

HOME = Path("/home/ubuntu")
ROOT = HOME / "zennay-cloud" / ".drive-archive"
STAGING = ROOT / "staging"
RCLONE = "/home/ubuntu/.local/bin/rclone"
DEST = "vpsarchive:zcloud-vps/automatic/runner-logs"
STATE = HOME / ".local/state/zcloud-drive"
AGE_SECONDS = 24 * 3600
OWN_UID = os.getuid()

def call(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def cloud_digest(remote):
    p = subprocess.Popen([RCLONE, "cat", remote, "--retries", "3"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    h = hashlib.sha256()
    while True:
        chunk = p.stdout.read(4 * 1024 * 1024)
        if not chunk:
            break
        h.update(chunk)
    err = p.stderr.read()
    if p.wait() != 0:
        raise RuntimeError("Remote readback failed: " + err.decode(errors="replace")[-500:])
    return h.hexdigest()

def unchanged(path, snapshot):
    try:
        s = path.lstat()
        return (stat.S_ISREG(s.st_mode) and not path.is_symlink() and
                (s.st_ino, s.st_size, s.st_mtime_ns, s.st_uid) == snapshot and
                s.st_uid == OWN_UID)
    except OSError:
        return False

def one_runner(directory):
    now = time.time()
    saved = []
    for path in directory.iterdir():
        try:
            st = path.lstat()
            if (path.suffix != ".log" or not stat.S_ISREG(st.st_mode)
                    or path.is_symlink() or st.st_uid != OWN_UID
                    or st.st_mtime > now - AGE_SECONDS):
                continue
            saved.append((path, (st.st_ino, st.st_size, st.st_mtime_ns, st.st_uid)))
        except OSError:
            continue
    if not saved:
        return 0
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = directory.parent.name
    prefix = name + "-" + stamp + "-" + str(os.getpid())
    listing = STAGING / (prefix + ".files")
    bundle = STAGING / (prefix + ".tar.zst")
    remote = DEST + "/" + name + "/" + bundle.name
    before = sum(s[1] for _, s in saved)
    try:
        with listing.open("wb") as out:
            for path, _ in saved:
                out.write(os.fsencode(path.name) + b"\0")
        with bundle.open("wb") as out:
            tar = subprocess.Popen(["tar", "-C", str(directory), "-cf", "-",
                                    "--null", "-T", str(listing)], stdout=subprocess.PIPE)
            zstd = subprocess.Popen(["zstd", "-q", "-T1", "-3", "-c"],
                                    stdin=tar.stdout, stdout=out)
            tar.stdout.close()
            if zstd.wait() or tar.wait():
                raise RuntimeError("Could not snapshot stable runner logs")
        expected = digest(bundle)
        call(RCLONE, "copyto", str(bundle), remote, "--retries", "3",
             "--transfers", "1", "--tpslimit", "3")
        if cloud_digest(remote) != expected:
            raise RuntimeError("Encrypted cloud readback hash mismatch: refusing to prune")
        removed = 0
        for path, snap in saved:
            if unchanged(path, snap):
                path.unlink()
                removed += snap[1]
        STATE.mkdir(parents=True, exist_ok=True)
        with (STATE / "runner-offloads.jsonl").open("a") as out:
            out.write(json.dumps({"utc": stamp, "source": str(directory),
                "remote": remote, "sha256": expected, "source_bytes": before,
                "freed_bytes": removed, "archive_bytes": bundle.stat().st_size,
                "files": len(saved)}) + "\n")
        print(json.dumps({"runner": name, "archived_bytes": before,
                          "freed_bytes": removed, "remote": remote}), flush=True)
        return removed
    finally:
        listing.unlink(missing_ok=True)
        bundle.unlink(missing_ok=True)

def main():
    STAGING.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "runner-offload.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Runner offload already running", flush=True)
            return
        sources = []
        for base in (HOME, Path("/opt")):
            for path in base.glob("actions-runner*/_diag"):
                if path.is_dir() and not path.is_symlink() and os.access(path, os.W_OK):
                    sources.append(path)
            for path in base.glob("*runner*/_diag"):
                if path.is_dir() and not path.is_symlink() and os.access(path, os.W_OK):
                    sources.append(path)
        freed = 0
        for path in sorted(set(sources)):
            try:
                freed += one_runner(path)
            except Exception as e:
                print("PRESERVED " + str(path) + ": " + str(e), file=sys.stderr, flush=True)
        print("TOTAL_VERIFIED_FREED_BYTES=" + str(freed), flush=True)

if __name__ == "__main__":
    main()
