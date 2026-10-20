#!/usr/bin/env python3
"""Read-only storage tier inventory for the VPS. Never transfers or deletes data.

Cold candidates here are GitHub runner diagnostics with no recent writes,
and completed Fuckball research reports outside the trainer's recent window.
Encrypted offloading is handled by the existing zcloud-drive-archive machinery,
which must verify remote content before any separately authorized cleanup.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

HOME = Path("/home/ubuntu")
ROOT = HOME / "zennay-cloud"
TRAINING = Path("/opt/fuckball-training")
ARCHIVE = "vpsarchive:zcloud-vps"
MIN_AGE_DAYS = 3
MAX_LIST = 100


def candidate_file(path: Path, cutoff: float) -> bool:
    try:
        st = path.lstat()
        return path.is_file() and not path.is_symlink() and st.st_nlink == 1 and st.st_mtime < cutoff
    except OSError:
        return False


def runner_diagnostics(cutoff: float):
    for runner in sorted(HOME.glob("actions-runner-*")):
        diagnostic = runner / "_diag"
        if not diagnostic.is_dir() or diagnostic.is_symlink():
            continue
        for path in diagnostic.iterdir():
            if not (path.name.startswith(("Worker_", "Runner_", "SelfUpdate_")) and path.suffix == ".log"):
                continue
            if candidate_file(path, cutoff):
                stat = path.stat()
                yield {
                    "kind": "completed-runner-diagnostic",
                    "source": str(path),
                    "relative_archive": f"cold/runner-diagnostics/{runner.name}/{path.name}",
                    "bytes": stat.st_size,
                    "modified_utc": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
                    "can_remove_automatically": False,
                }


def training_reports(cutoff: float):
    try:
        status = json.loads((TRAINING / "status.json").read_text())
        cycles = status["cycles"]
        if type(cycles) is not int or cycles < 50:
            return
    except (OSError, KeyError, TypeError, ValueError):
        return
    for path in TRAINING.glob("cycle-*-report.json"):
        try:
            cycle = int(path.name.removeprefix("cycle-").removesuffix("-report.json"))
        except ValueError:
            continue
        if cycle >= cycles - 50 or not candidate_file(path, cutoff):
            continue
        stat = path.stat()
        yield {
            "kind": "completed-training-report",
            "source": str(path),
            "relative_archive": f"cold/fuckball-training/reports/{path.name}",
            "bytes": stat.st_size,
            "modified_utc": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
            "can_remove_automatically": False,
        }


def filesystem():
    root = os.statvfs("/")
    return {"total_bytes": root.f_blocks * root.f_frsize,
            "free_bytes": root.f_bavail * root.f_frsize}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--older-than-days", type=int, default=MIN_AGE_DAYS)
    ap.add_argument("--limit", type=int, default=MAX_LIST)
    args = ap.parse_args()
    if args.older_than_days < 3 or args.limit < 1 or args.limit > 5000:
        ap.error("requires at least 3 cold days and 1..5000 displayed candidates")
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - args.older_than_days * 86400
    totals, count, examples = {}, {}, []
    for row in (*runner_diagnostics(cutoff), *training_reports(cutoff)):
        kind = row["kind"]
        totals[kind] = totals.get(kind, 0) + row["bytes"]
        count[kind] = count.get(kind, 0) + 1
        if len(examples) < args.limit:
            examples.append(row)
    result = {
        "schema": "zcloud-storage-tier-plan-v1",
        "read_only": True,
        "remote": ARCHIVE,
        "minimum_age_days": args.older_than_days,
        "filesystem": filesystem(),
        "cold_candidate_counts": count,
        "cold_candidate_bytes": totals,
        "total_candidate_bytes": sum(totals.values()),
        "examples": examples,
        "excluded": [
            "live code, GitHub runner executables, working checkouts and active logs",
            "HaxLab raw/derived training shards, manifests, databases and models",
            "Fuckball active cycle, latest checkpoints and 50 most recent reports",
            "FTMO live trading/paper state, credentials and browser profiles",
            "files with multiple hardlinks or symlinks",
        ],
        "move_policy": "Existing encrypted archive tooling must complete remote full-byte verification; never delete from inventory alone.",
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
