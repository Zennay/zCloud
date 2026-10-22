#!/usr/bin/env python3
"""Read-only, fail-closed cutover evidence assessment for the *new* zCloud VPS.

This tool NEVER starts/stops a service, writes SQLite, registers runners, or shuts
down the old VPS. A positive result means evidence is ready for human review,
NOT authorization to decommission the rollback host.

Receipts are operator-supplied assertions. The training mount and hostname are
independently observed on the machine where this tool runs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import socket
import sys
from urllib.parse import urlsplit

NEW_HOST = "VM-7eb5ee16-2c71-440d-b7fd-8095d204e930"
REQUIRED_RUNNERS = 14
REQUIRED_WORKERS = 10
REQUIRED_PUBLIC_ROUTES = ("zcloud_dashboard", "zssh_plugin")
REQUIRED_PROJECTS = (
    "FTMO", "Supa", "LightUp", "TradeEngine", "Tuddy",
    "zSentinel", "Fuckball-game", "RaiseAI", "zGuard",
)
FILESYSTEMS = {"ext4", "xfs", "btrfs"}


def fresh(value: object, minutes: int, now: datetime) -> bool:
    if not isinstance(value, str):
        return False
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        return False
    delta = now - stamp.astimezone(timezone.utc)
    return -timedelta(minutes=2) <= delta <= timedelta(minutes=minutes)


def mount_records() -> list[dict[str, str]]:
    records = []
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        before, sep, after = line.partition(" - ")
        if not sep:
            continue
        cols, extra = before.split(), after.split()
        if len(cols) < 5 or len(extra) < 2:
            continue
        # mountinfo path fields use octal escape sequences for spaces and slashes
        path = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), cols[4])
        records.append({"path": path, "device": cols[2], "fs": extra[0], "source": extra[1]})
    return records


def physical_disk(device: str) -> str | None:
    """Return parent sysfs disk of a mounted partition; missing proof is a denial."""
    if not re.fullmatch(r"\d+:\d+", device):
        return None
    candidate = Path("/sys/dev/block") / device
    try:
        block = candidate.resolve(strict=True)
        if not block.is_dir():
            return None
        disk = block.parent if (block / "partition").is_file() else block
        return str(disk.resolve(strict=True))
    except (OSError, RuntimeError):
        return None


def inspect_training_mount(path: str) -> tuple[bool, str]:
    if not path.startswith("/") or os.path.normpath(path) == "/":
        return False, "training mount must be an explicit non-root absolute path"
    intended = Path(path)
    if intended.is_symlink() or os.path.realpath(path) != os.path.normpath(path):
        return False, "training path is a symlink or noncanonical"
    records = mount_records()
    by_path = {record["path"]: record for record in records}
    root = by_path.get("/")
    external = by_path.get(path)
    if not root or not external or not os.path.ismount(path):
        return False, "separate mounted training filesystem not found"
    if external["fs"] not in FILESYSTEMS:
        return False, "training filesystem must be local ext4/xfs/btrfs, not FUSE/network/tmpfs"
    if not external["source"].startswith("/dev/"):
        return False, "training mount has no proven local block-device source"
    root_disk = physical_disk(root["device"])
    training_disk = physical_disk(external["device"])
    if root_disk is None or training_disk is None:
        return False, "physical disk ancestry could not be established"
    if root_disk == training_disk:
        return False, "training storage is on the system disk"
    try:
        stat = os.stat(path)
        if f"{os.major(stat.st_dev)}:{os.minor(stat.st_dev)}" != external["device"]:
            return False, "training mount device changed during inspection"
    except OSError:
        return False, "training mount inaccessible"
    return True, "distinct local block disk with permitted filesystem observed"


def validate_receipts(receipts: dict, now: datetime, findings: list[str]) -> None:
    def require(condition: bool, reason: str) -> None:
        if not condition:
            findings.append(reason)

    require(receipts.get("target_hostname") == NEW_HOST, "receipts: wrong target hostname")
    require(fresh(receipts.get("collected_at"), 60, now), "receipts: snapshot missing/stale")
    db = receipts.get("database")
    db = db if isinstance(db, dict) else {}
    require(
        db.get("old_writers_fenced") is True
        and db.get("new_writer_singleton") is True
        and db.get("restored_on_new") is True
        and db.get("wal_consistent") is True,
        "database: final writer-fence, handoff, WAL or singleton proof missing",
    )
    sha = db.get("final_backup_sha256")
    require(
        isinstance(sha, str) and re.fullmatch(r"[a-fA-F0-9]{64}", sha) is not None,
        "database: final consistent backup digest missing",
    )
    require(
        type(db.get("source_tables")) is int
        and db.get("source_tables") > 0
        and db.get("source_tables") == db.get("target_tables"),
        "database: source/target schema table proof missing or mismatched",
    )
    require(fresh(db.get("snapshot_at"), 1440, now), "database: handoff receipt stale")

    scheduler = receipts.get("scheduler")
    scheduler = scheduler if isinstance(scheduler, dict) else {}
    require(
        scheduler.get("old_disabled") is True and scheduler.get("new_active") is True,
        "scheduler: old/new exclusive ownership not proven",
    )
    require(fresh(scheduler.get("last_completed_dispatch_at"), 60, now),
            "scheduler: no recent successful dispatch receipt")

    runners = receipts.get("runners")
    runners = runners if isinstance(runners, list) else []
    runner_ids, runner_names, job_ids = set(), set(), set()
    runner_ok = len(runners) == REQUIRED_RUNNERS
    for item in runners:
        if not isinstance(item, dict):
            runner_ok = False
            continue
        rid, name, jid = item.get("runner_id"), item.get("name"), item.get("job_id")
        if (
            type(rid) is not int or rid <= 0
            or not isinstance(name, str) or not name.strip()
            or type(jid) is not int or jid <= 0
            or item.get("job_conclusion") != "success"
            or item.get("host") != NEW_HOST
            or not fresh(item.get("job_completed_at"), 1440, now)
            or not isinstance(item.get("repository"), str)
            or "/" not in item["repository"]
        ):
            runner_ok = False
        runner_ids.add(str(rid))
        runner_names.add(str(name))
        job_ids.add(str(jid))
    require(runner_ok and len(runner_ids) == REQUIRED_RUNNERS
            and len(runner_names) == REQUIRED_RUNNERS
            and len(job_ids) == REQUIRED_RUNNERS,
            "runners: 14 unique new identities with successful actual jobs not proven")

    workers = receipts.get("workers")
    workers = workers if isinstance(workers, list) else []
    slots, identities = set(), set()
    worker_ok = len(workers) == REQUIRED_WORKERS
    for item in workers:
        if not isinstance(item, dict):
            worker_ok = False
            continue
        slot, wid = item.get("slot"), item.get("worker_id")
        if (type(slot) is not int or slot not in range(1, REQUIRED_WORKERS + 1)
            or not isinstance(wid, str) or not wid.strip()
            or item.get("host") != NEW_HOST
            or not fresh(item.get("heartbeat_at"), 5, now)
            or not fresh(item.get("last_event_at"), 60, now)):
            worker_ok = False
        slots.add(str(slot))
        identities.add(str(wid))
    require(worker_ok and len(slots) == REQUIRED_WORKERS
            and len(identities) == REQUIRED_WORKERS,
            "workers: 10 unique slots with fresh new-host heartbeats/events not proven")

    routes = receipts.get("public_routes")
    routes = routes if isinstance(routes, list) else []
    passing_routes = set()
    for item in routes:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
        except ValueError:
            continue
        route_name = item.get("name")
        if (isinstance(route_name, str)
            and parsed.scheme == "https" and hostname
            and item.get("tls_verified") is True
            and item.get("authentication_verified") is True
            and item.get("new_vps_origin_verified") is True
            and fresh(item.get("checked_at"), 60, now)):
            passing_routes.add(route_name)
    require(all(route in passing_routes for route in REQUIRED_PUBLIC_ROUTES),
            "public: fresh authenticated HTTPS zCloud and zSSH receipts missing")

    services = receipts.get("project_services")
    services = services if isinstance(services, list) else []
    services_by_name = {}
    for item in services:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            services_by_name[item["name"]] = item
    for name in REQUIRED_PROJECTS:
        service = services_by_name.get(name, {})
        require(
            service.get("state") in ("healthy", "intentionally_offline")
            and fresh(service.get("checked_at"), 1440, now)
            and isinstance(service.get("evidence"), str)
            and len(service["evidence"].strip()) >= 8,
            f"service: {name} not freshly accounted for",
        )

    rollback = receipts.get("rollback")
    rollback = rollback if isinstance(rollback, dict) else {}
    require(
        rollback.get("old_host_preserved") is True
        and rollback.get("restore_procedure_confirmed") is True
        and rollback.get("backup_off_host") is True,
        "rollback: preserved old host/restore/off-host backup not proven",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", required=True, type=Path,
                        help="operator-collected JSON receipt manifest; not independently trusted")
    parser.add_argument("--training-mount", required=True,
                        help="exact required local block-storage mountpoint")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    findings: list[str] = []
    mounted = False
    hostname = socket.gethostname()
    if hostname != NEW_HOST:
        findings.append("host: running on wrong VPS")
    try:
        mounted, reason = inspect_training_mount(args.training_mount)
        if not mounted:
            findings.append("storage: " + reason)
    except (OSError, ValueError) as exc:
        findings.append("storage: local mount inspection unavailable (" + type(exc).__name__ + ")")
    try:
        if args.receipts.stat().st_size > 262144:
            raise ValueError("manifest exceeds 256 KiB")
        data = json.loads(args.receipts.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("manifest must be a JSON object")
        validate_receipts(data, now, findings)
    except (OSError, ValueError, TypeError) as exc:
        findings.append("receipts: unreadable/invalid (" + type(exc).__name__ + ")")

    # This is intentionally not a shutdown token. It never mutates the host.
    report = {
        "status": "HOLD" if findings else "READY_FOR_HUMAN_REVIEW",
        "meaning": "No authorization to shut down or delete old VPS",
        "observed_host": hostname,
        "observed_training_storage": "distinct_block_disk" if mounted else "unverified",
        "receipt_trust": "operator_attested_not_independently_verified",
        "checked_at": now.isoformat(),
        "blockers": findings,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 2 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
