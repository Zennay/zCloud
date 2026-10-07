#!/usr/bin/env python3
"""Run one command under zCloud resource-governor admission and process bounds."""
from __future__ import annotations

import argparse
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
    return conn


def _bounded_cpu_count(value, available: int) -> int:
    requested = max(1, int(math.ceil(float(value or 1))))
    return max(1, min(max(1, int(available)), requested))


def _apply_process_limits(project_id: str) -> None:
    """Apply inherited Linux process bounds from the canonical project contract."""
    compute = dict(runtime.project_contract(project_id).get("compute") or {})

    if hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity"):
        allowed = sorted(os.sched_getaffinity(0))
        if allowed:
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
    payload = {
        "executor": "zcloud-governed-exec",
        "command": Path(command[0]).name if command else "",
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


def _signal_process_group(proc: subprocess.Popen | None, signum: int) -> None:
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signum)
    except ProcessLookupError:
        return


def _stop_process_group(proc: subprocess.Popen | None, *, grace_seconds: float = 2.0) -> None:
    """Boundedly terminate the governed process tree before releasing its lease."""
    if proc is None:
        return
    _signal_process_group(proc, signal.SIGTERM)
    deadline = time.monotonic() + max(0.1, float(grace_seconds))
    while time.monotonic() < deadline:
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        _signal_process_group(proc, signal.SIGKILL)
    if proc.poll() is None:
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _signal_process_group(proc, signal.SIGKILL)
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass


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

    lease_seconds = max(60, min(6 * 3600, int(lease_seconds or 300)))
    renew_seconds = max(5, min(int(renew_seconds or 60), max(5, lease_seconds // 2)))
    conn = connect(Path(db_path))
    proc = None
    old_handlers = {}
    acquired = False
    try:
        result = runtime.acquire_resource(
            conn,
            project_id,
            owner_id,
            lease_seconds=lease_seconds,
            metadata=_metadata(command),
        )
        conn.commit()
        if not result.get("acquired"):
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 75
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
            print(json.dumps(renewed, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 75

        def forward(signum, _frame):
            if proc is not None:
                _signal_process_group(proc, signum)

        for signum in (signal.SIGTERM, signal.SIGINT):
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, forward)

        next_renewal = time.monotonic() + renew_seconds
        while True:
            returncode = proc.poll()
            if returncode is not None:
                return _exit_code(returncode)

            now = time.monotonic()
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
                    print(json.dumps(renewed, ensure_ascii=False, sort_keys=True), file=sys.stderr)
                    return 75
                next_renewal = now + renew_seconds
            time.sleep(0.5)
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        _stop_process_group(proc)
        if acquired:
            try:
                runtime.release_resource(conn, project_id, owner_id)
                conn.commit()
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "released": False,
                            "reason": "resource_release_failed",
                            "project_id": project_id,
                            "owner_id": owner_id,
                            "error": type(exc).__name__,
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
        conn.close()


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
