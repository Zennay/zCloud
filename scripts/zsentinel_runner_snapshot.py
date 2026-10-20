#!/usr/bin/env python3
"""One-shot private runner state receipts and conservative T0/T1 comparison.

A changed systemd service is not proof of a repaired GitHub runner or CI job.
No queue, unit, GitHub or worker action is performed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import stat
from zsentinel_background_priority import RUNNER_UNIT
from zsentinel_runner_report import collect

SCHEMA = "zsentinel.runner-observation.v1"
MAX_BYTES = 128 * 1024
MAX_AGE_HOURS = 72


def _boot_fingerprint():
    """Private per-boot evidence, never a host-identity or restart authority."""
    try:
        raw = Path("/proc/sys/kernel/random/boot_id").read_text().strip().lower()
    except (OSError, UnicodeError):
        return None
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", raw):
        return None
    # Never place the literal Linux boot UUID in operator receipts.
    return hashlib.sha256(raw.encode("ascii")).hexdigest()[:24]


def _iso(dt):
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parsed(ts):
    if not isinstance(ts, str) or not ts.endswith("Z"):
        raise ValueError("Untrusted observation timestamp")
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.utcoffset().total_seconds() != 0:
        raise ValueError("Observation not UTC")
    return dt


def _unit_states(bundle):
    obs = bundle.get("units")
    if not isinstance(obs, dict):
        raise ValueError("No valid runner observations")
    rows = obs.get("runner_units")
    if not isinstance(rows, list) or len(rows) > 20:
        raise ValueError("Out-of-bounds runner observations")
    states = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Bad runner item")
        name = row.get("unit")
        state = row.get("state")
        detail = row.get("detail")
        if (not isinstance(name, str) or len(name) > 160 or
                not RUNNER_UNIT.fullmatch(name) or
                state not in {"active", "inactive", "failed", "activating", "deactivating", "unknown"} or
                not isinstance(detail, str) or len(detail) > 30 or name in states):
            raise ValueError("Malformed/duplicate service observation")
        states[name] = {"state": state, "detail": detail,
                        "reason": row.get("reason")}
    return states


def packet(report_data):
    """Construct a bounded evidence packet, never carrying raw process arguments."""
    bundle = {
        "schema": SCHEMA,
        "observation_utc": _iso(datetime.now(timezone.utc)),
        "host": os.uname().nodename,
        "boot_fingerprint": _boot_fingerprint(),
        "units": report_data["units"],
        "capacity": report_data["host"],
        "coach_state": {
            "observed": report_data["coach"].get("observed") is True,
            "active": report_data["coach"].get("active"),
            "quota": report_data["coach"].get("quota"),
            "weight": report_data["coach"].get("weight"),
            "recommendation_matches": report_data["coach"].get("recommendation_matches"),
        },
        "cpu_recommendation": {
            "quota_percent": report_data["quota"], "weight": report_data["weight"],
            "reasons": report_data["reasons"],
        },
        "authority": {
            "claim_authorized": False, "service_restart_authorized": False,
            "merge_authorized": False, "deploy_authorized": False,
        },
        "limitations": [
            "systemd service state is not GitHub job success",
            "missing from inventory is not resolved",
            "source coverage may be partial",
        ],
    }
    _unit_states(bundle)
    if len(json.dumps(bundle, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Observation exceeds allowed private receipt size")
    return bundle


def load_private(path):
    """Read only an owner-held, mode-0600, regular JSON file without symlinks."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_mode & 0o077 or info.st_size > MAX_BYTES):
            raise ValueError("Previous receipt is not an owner-private regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            blob = stream.read(MAX_BYTES + 1)
        if len(blob) > MAX_BYTES:
            raise ValueError("Receipt limit exceeded")
        obj = json.loads(blob.decode("utf-8"))
    finally:
        os.close(fd)
    if not isinstance(obj, dict) or obj.get("schema") != SCHEMA:
        raise ValueError("Wrong receipt schema")
    return obj


def capacity_changes(previous, current):
    """Report observed capacity differences without asserting resource recovery."""
    a, b = previous.get("capacity"), current.get("capacity")
    if not isinstance(a, dict) or not isinstance(b, dict):
        return {"source_complete": False, "root_comparable": False,
                "metrics": [], "explanation": "Capacity source unavailable"}
    mount_ok = (
        isinstance(a.get("root_mount_device"), str) and
        a.get("root_mount_device") == b.get("root_mount_device") and
        type(a.get("root_volume_bytes")) is int and
        a["root_volume_bytes"] > 0 and
        a["root_volume_bytes"] == b.get("root_volume_bytes")
    )
    labels = (
        ("host_load", False), ("memory_available_gib", False),
        ("swap_used_percent", False), ("disk_unavailable_percent", True),
    )
    metrics = []
    for key, is_disk in labels:
        old, new = a.get(key), b.get(key)
        numeric = (
            type(old) in (int, float) and type(new) in (int, float) and
            0 <= old < 1e12 and 0 <= new < 1e12
        )
        comparable = numeric and (not is_disk or mount_ok)
        metrics.append({
            "metric": key,
            "previous": old if numeric else None,
            "current": new if numeric else None,
            "comparable": comparable,
            "difference": round(new - old, 2) if comparable else None,
        })
    return {
        "source_complete": a.get("source_complete") is True
                           and b.get("source_complete") is True,
        "root_comparable": mount_ok,
        "metrics": metrics,
        "explanation": (
            "Observed host capacity difference only; no proof of recovered "
            "files, runner performance or successful model training."
        ),
    }


def changes(previous, current):
    """Report only observed service state transitions; never infer global recovery."""
    if previous.get("schema") != SCHEMA or current.get("schema") != SCHEMA:
        raise ValueError("Incompatible evidence schema")
    host_a, host_b = previous.get("host"), current.get("host")
    if not isinstance(host_a, str) or not host_a or host_a != host_b:
        raise ValueError("Incomparable host")
    t0, t1 = _parsed(previous.get("observation_utc")), _parsed(current.get("observation_utc"))
    elapsed = (t1 - t0).total_seconds()
    if not (0 < elapsed <= MAX_AGE_HOURS * 3600):
        raise ValueError("Out-of-order or stale receipt")
    boot_a, boot_b = previous.get("boot_fingerprint"), current.get("boot_fingerprint")
    valid_boot = lambda value: isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{24}", value))
    boot_pair_observed = valid_boot(boot_a) and valid_boot(boot_b)
    same_boot = boot_pair_observed and boot_a == boot_b
    changed_boot = boot_pair_observed and boot_a != boot_b
    a, b = _unit_states(previous), _unit_states(current)
    coverage_a = previous["units"].get("runner_units_observed") is True and previous["units"].get("runner_units_truncated") is False
    coverage_b = current["units"].get("runner_units_observed") is True and current["units"].get("runner_units_truncated") is False
    result = []
    for name in sorted(set(a) | set(b)):
        before, after = a.get(name), b.get(name)
        if before is None:
            tag = "newly_observed"
        elif after is None:
            tag = "no_longer_observed"
        elif before != after:
            tag = "service_state_changed"
        else:
            continue
        # Stable per-transition dedup receipt. A hash is not an authorization
        # token and does not prove a successful GitHub runner recovery.
        material = json.dumps(
            [host_a, boot_a if valid_boot(boot_a) else None,
             boot_b if valid_boot(boot_b) else None, name, tag, before, after],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        result.append({
            "incident_key": hashlib.sha256(material).hexdigest()[:24],
            "unit": name, "change": tag,
            "previous": before, "current": after,
            "github_job_recovered": None,
            "production_restored": None,
            "action_authorized": False,
            "same_boot_verified": same_boot,
        })
    return {
        "previous_utc": previous["observation_utc"],
        "current_utc": current["observation_utc"],
        "same_host": True,
        "same_boot_verified": same_boot,
        "host_reboot_observed": changed_boot if boot_pair_observed else None,
        "complete_service_coverage": coverage_a and coverage_b,
        "elapsed_seconds": int(elapsed),
        "capacity_comparison": capacity_changes(previous, current),
        "transitions": result,
        "interpretation": (
            "Only bounded systemd observations. A boot change or unknown boot "
            "identity prevents treating transitions as same-boot continuity; "
            "even failed→active does not prove GitHub assignment, a completed job "
            "or a merged PR."
        ),
    }


def save_private_json(path, payload):
    """Create once, 0600; never overwrite an old receipt or follow final symlinks."""
    target = Path(path)
    if target.suffix != ".json" or not target.name or target.name in {".", ".."}:
        raise ValueError("A new .json file is required")
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("Private receipt too large")
    directory = os.open(str(target.parent), os.O_RDONLY | os.O_DIRECTORY |
                        getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            os.unlink(target.name, dir_fd=directory)
            raise
    finally:
        os.close(directory)


def main():
    parser = argparse.ArgumentParser(description="Owner-private runner service observation receipt")
    parser.add_argument("--output", required=True, help="New 0600 .json receipt")
    parser.add_argument("--previous", help="Previous owner-private snapshot on the same host (max 72 hours)")
    args = parser.parse_args()
    try:
        current = packet(collect())
        if args.previous:
            earlier = load_private(args.previous)
            current["comparison"] = changes(earlier, current)
        save_private_json(args.output, current)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        parser.exit(1, "Geen privé runnerbewijs gemaakt; controleer bron, volgorde en doelrechten.\n")
    print("Privé runnerbewijs opgeslagen (0600); geen service- of wachtrijactie uitgevoerd.")


if __name__ == "__main__":
    main()
