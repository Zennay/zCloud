#!/usr/bin/env python3
"""Run one command under zCloud resource-governor admission and process bounds."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import project_runtime as runtime  # noqa: E402


def connect(path: Path):
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    runtime.init_tables(conn)
    conn.commit()  # admission below opens its own explicit write transaction
    return conn


def _bounded_cpu_count(value, available: int) -> int:
    requested = max(1, int(math.ceil(float(value or 1))))
    return max(1, min(max(1, int(available)), requested))


def _apply_process_limits(project_id: str) -> None:
    """Apply inherited Linux process bounds from the canonical project contract."""
    compute = dict(runtime.project_contract(project_id).get("compute") or {})

    if hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity"):
        allowed = sorted(os.sched_getaffinity(0))
        if not allowed:
            raise RuntimeError("resource_cpu_affinity_unavailable")
        # Isolate a physical schedulable CPU from non-control work when the
        # host offers more than one. zCloud/zSSH can remain schedulable there.
        # The pool lease alone does not reserve OS scheduler capacity.
        if compute.get("pool") != "protected":
            if len(allowed) < 2:
                raise RuntimeError("protected_control_cpu_headroom_unavailable")
            allowed = allowed[1:]
        count = _bounded_cpu_count(compute.get("cpu_soft_cores"), len(allowed))
        os.sched_setaffinity(0, set(allowed[:count]))

    memory_mb = int(compute.get("memory_soft_mb") or 0)
    if memory_mb > 0:
        import resource

        requested = memory_mb * 1024 * 1024
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard == resource.RLIM_INFINITY:
            new_soft = requested
        else:
            new_soft = min(requested, hard)
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, hard))


def _metadata(command, *, pid=None):
    # Only bounded command identity belongs in the lease, not arguments,
    # private file paths, environment values or unbounded free-form text.
    basename = Path(str(command[0])).name if command else ""
    basename = "".join(ch if ord(ch) >= 32 and ord(ch) != 127 else "_"
                       for ch in basename[:128])
    payload = {
        "executor": "zcloud-governed-exec",
        "command": basename,
    }
    for key, env_name in (
        ("workflow_run_id", "GITHUB_RUN_ID"),
        ("workflow_job", "GITHUB_JOB"),
        ("runner_name", "RUNNER_NAME"),
    ):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            payload[key] = value[:200]
    if pid is not None:
        payload["pid"] = int(pid)
    return payload


def _exit_code(returncode: int) -> int:
    return returncode if returncode >= 0 else 128 + abs(returncode)


def _safe_owner(owner_id: str) -> str:
    """Never create a lease with a truncated or unreleasable owner key."""
    if (not isinstance(owner_id, str) or not 1 <= len(owner_id) <= 200
            or owner_id != owner_id.strip() or not owner_id.strip()
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in owner_id)):
        raise ValueError("invalid resource owner identifier")
    return owner_id


def _admission_denied(result: dict) -> None:
    """Never print other heavy workload owners or receipt metadata."""
    print(json.dumps({
        "acquired": False,
        "reason": str(result.get("reason") or "resource_admission_unavailable")[:80],
        "project_id": str(result.get("project_id") or "")[:60],
        "pool": str(result.get("pool") or "")[:40],
    }, sort_keys=True), file=sys.stderr)


def _group_rss_bytes(group_id: int) -> int:
    """Read bounded aggregate resident memory for the inherited process group.

    Per-process RLIMIT_AS does not bound a whole forked training workload.
    Unobservable /proc data is unsafe admission evidence, not zero usage.
    """
    used = 0
    inspected = 0
    try:
        with os.scandir("/proc") as processes:
            for item in processes:
                if not item.name.isdecimal():
                    continue
                inspected += 1
                if inspected > 20000:
                    raise RuntimeError("resource_process_inventory_unbounded")
                try:
                    stat_fields = (Path("/proc") / item.name / "stat").read_text().rpartition(")")[2].split()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise RuntimeError("resource_process_inventory_unavailable") from exc
                if len(stat_fields) < 3:
                    raise RuntimeError("resource_process_stat_invalid")
                if int(stat_fields[2]) != group_id or stat_fields[0] in {"Z", "X"}:
                    continue
                try:
                    statm = (Path("/proc") / item.name / "statm").read_text().split()
                    if len(statm) < 2:
                        raise RuntimeError("resource_process_memory_unavailable")
                    used += int(statm[1]) * os.sysconf("SC_PAGE_SIZE")
                except FileNotFoundError:
                    continue
                except (OSError, ValueError) as exc:
                    raise RuntimeError("resource_process_memory_unavailable") from exc
    except OSError as exc:
        raise RuntimeError("resource_process_inventory_unavailable") from exc
    return used


def _signal_process_group(proc: subprocess.Popen | None, signum: int) -> None:
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signum)
    except ProcessLookupError:
        return


def _group_exists(proc: subprocess.Popen) -> bool:
    """Fail closed when a process group cannot be proven absent."""
    try:
        os.killpg(proc.pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _stop_process_group(proc: subprocess.Popen | None, *, grace_seconds: float = 2.0) -> bool:
    """Stop the owned session before a resource lease can be released.

    A direct child finishing is NOT proof all of its spawned workers stopped.
    The caller must retain admission if the process group cannot be proven gone.
    """
    if proc is None:
        return True
    try:
        _signal_process_group(proc, signal.SIGTERM)
        deadline = time.monotonic() + max(0.1, float(grace_seconds))
        while time.monotonic() < deadline:
            if not _group_exists(proc):
                break
            time.sleep(0.05)
        if _group_exists(proc):
            _signal_process_group(proc, signal.SIGKILL)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not _group_exists(proc):
                    break
                time.sleep(0.05)
        if proc.poll() is None:
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                return False
        return not _group_exists(proc)
    except (OSError, subprocess.SubprocessError):
        return False

def run_governed(
    db_path: Path,
    project_id: str,
    owner_id: str,
    command,
    *,
    lease_seconds: int = 300,
    renew_seconds: int = 60,
) -> int:
    if not command:
        raise ValueError("command is required")
    owner_id = _safe_owner(owner_id)

    lease_seconds = max(60, min(6 * 3600, int(lease_seconds or 300)))
    renew_seconds = max(5, min(int(renew_seconds or 60), max(5, lease_seconds // 2)))
    compute = dict(runtime.project_contract(project_id).get("compute") or {})
    memory_budget = int(compute.get("memory_soft_mb") or 0) * 1024 * 1024
    if compute.get("pool") == "heavy" and memory_budget <= 0:
        raise RuntimeError("heavy_memory_budget_unavailable")
    conn = connect(Path(db_path))
    proc = None
    old_handlers = {}
    acquired = False
    try:
        # Serialize same-owner admission with the pool capacity reservation.
        # Reusing an active owner key would otherwise renew one lease while
        # starting a second real process without consuming another slot.
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT 1 FROM resource_leases WHERE project_id=? AND owner_id=? "
            "AND lease_until>?",
            (project_id, owner_id, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
        if existing:
            conn.rollback()
            _admission_denied({"reason": "resource_owner_already_running",
                               "project_id": project_id})
            return 75
        result = runtime.acquire_resource(
            conn,
            project_id,
            owner_id,
            lease_seconds=lease_seconds,
            metadata=_metadata(command),
        )
        if not result.get("acquired") or result.get("renewed"):
            conn.rollback()
            _admission_denied(result if not result.get("renewed") else {
                "reason": "resource_owner_already_running", "project_id": project_id})
            return 75
        conn.commit()
        acquired = True

        env = os.environ.copy()
        env["ZCLOUD_RESOURCE_PROJECT"] = str(project_id)
        env["ZCLOUD_RESOURCE_OWNER"] = str(owner_id)
        proc = subprocess.Popen(
            list(command),
            env=env,
            preexec_fn=lambda: _apply_process_limits(project_id),
            start_new_session=True,
        )

        renewed = runtime.acquire_resource(
            conn,
            project_id,
            owner_id,
            lease_seconds=lease_seconds,
            metadata=_metadata(command, pid=proc.pid),
        )
        conn.commit()
        if not renewed.get("acquired"):
            _stop_process_group(proc)
            _admission_denied(renewed)
            return 75

        def forward(signum, _frame):
            if proc is not None:
                _signal_process_group(proc, signum)

        for signum in (signal.SIGTERM, signal.SIGINT):
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, forward)

        next_renewal = time.monotonic() + renew_seconds
        next_memory_check = time.monotonic()
        while True:
            returncode = proc.poll()
            if returncode is not None:
                return _exit_code(returncode)

            now = time.monotonic()
            if memory_budget > 0 and now >= next_memory_check:
                try:
                    memory_used = _group_rss_bytes(proc.pid)
                    if memory_used > memory_budget:
                        _stop_process_group(proc)
                        print('{"acquired":false,"reason":"governed_group_memory_budget_exceeded"}',
                              file=sys.stderr)
                        return 75
                except RuntimeError:
                    _stop_process_group(proc)
                    print('{"acquired":false,"reason":"governed_group_memory_evidence_unavailable"}',
                          file=sys.stderr)
                    return 75
                next_memory_check = now + 2.0
            if now >= next_renewal:
                renewed = runtime.acquire_resource(
                    conn,
                    project_id,
                    owner_id,
                    lease_seconds=lease_seconds,
                    metadata=_metadata(command, pid=proc.pid),
                )
                conn.commit()
                if not renewed.get("acquired"):
                    _stop_process_group(proc)
                    _admission_denied(renewed)
                    return 75
                next_renewal = now + renew_seconds
            time.sleep(0.5)
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        group_stopped = _stop_process_group(proc)
        release_ok = not acquired
        try:
            if acquired and not group_stopped:
                # An orphan may keep using the heavy pool. Retain the slot
                # at the maximum bounded TTL for explicit owner recovery.
                retained = runtime.acquire_resource(
                    conn, project_id, owner_id, lease_seconds=6 * 3600,
                    metadata={"executor": "zcloud-governed-exec",
                              "cleanup": "process_group_unverified"},
                )
                conn.commit()
                if not retained.get("acquired"):
                    raise RuntimeError("resource_lease_retention_failed")
            elif acquired:
                result = runtime.release_resource(conn, project_id, owner_id)
                conn.commit()
                if not result.get("released"):
                    raise RuntimeError("resource_lease_release_unconfirmed")
                release_ok = True
        except Exception as exc:
            conn.rollback()
            print(json.dumps({
                "released": False,
                "reason": "resource_lease_cleanup_unverified",
                "project_id": project_id,
                "error": type(exc).__name__,
            }, sort_keys=True), file=sys.stderr)
        finally:
            conn.close()
        if acquired and not group_stopped:
            raise RuntimeError("governed_process_group_not_stopped; resource lease not released")
        if not release_ok:
            raise RuntimeError("resource_lease_release_unverified")

def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "history.db")
    parser.add_argument("--project", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--renew-seconds", type=int, default=60)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("command is required after --")

    return run_governed(
        args.db,
        args.project,
        args.owner,
        command,
        lease_seconds=args.lease_seconds,
        renew_seconds=args.renew_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
