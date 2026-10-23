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
    if os.environ.get("ZCLOUD_RESOURCE_DB") is not None:
        # A supervised workload with a pinned shared database must never
        # create its own empty SQLite store after a missing disk/mount.
        # Doing so would silently create an independent "heavy" pool.
        if (path.is_symlink() or not path.is_file()
                or not os.access(path, os.R_OK | os.W_OK)):
            raise RuntimeError("resource_shared_database_unavailable")
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    runtime.init_tables(conn)
    conn.commit()  # admission below opens its own explicit write transaction
    return conn


def _assert_shared_resource_db(db_path: Path) -> None:
    """Prevent accidental split-brain pools when a service pins its DB path.

    Unit/service launchers should export ZCLOUD_RESOURCE_DB to the single
    shared SQLite authority. Production paths must not be silently replaced
    by local checkout or temporary per-project database files. Temporary
    canary databases without an explicitly pinned environment stay supported.
    """
    configured = os.environ.get("ZCLOUD_RESOURCE_DB")
    if configured:
        supplied = Path(db_path).expanduser().resolve(strict=False)
        canonical = Path(configured).expanduser().resolve(strict=False)
        if supplied != canonical:
            raise RuntimeError("resource_database_identity_mismatch")


def _effective_cgroup_memory_limits_mb(host_mb: int) -> tuple[int, int]:
    """Return min inherited cgroup-v2 limit and currently free headroom.

    A child can be constrained by any ancestor slice. Reading just its
    leaf memory.max misses the parent unit/slice and reading max alone
    misses memory already in use by unrelated processes in that slice.
    """
    remaining_mb = host_mb
    try:
        controls = Path("/proc/self/cgroup").read_text().splitlines()
        unified = [line.split("::", 1)[1] for line in controls if line.startswith("0::")]
        if not unified:
            return host_mb, remaining_mb  # Legacy cgroup v1.
        if len(unified) != 1:
            raise RuntimeError("resource_cgroup_identity_ambiguous")
        components = [part for part in unified[0].split("/") if part]
        if any(part in (".", "..") for part in components):
            raise RuntimeError("resource_cgroup_path_invalid")
        path = Path("/sys/fs/cgroup")
        for component in [""] + components:
            if component:
                path = path / component
            raw = (path / "memory.max").read_text().strip()
            if raw == "max":
                continue
            maximum = int(raw)
            current = int((path / "memory.current").read_text().strip())
            if maximum <= 0 or current < 0:
                raise RuntimeError("resource_cgroup_memory_invalid")
            host_mb = min(host_mb, maximum // (1024 * 1024))
            remaining_mb = min(remaining_mb, max(0, (maximum-current)//(1024*1024)))
        return host_mb, remaining_mb
    except (OSError, ValueError, IndexError) as exc:
        raise RuntimeError("resource_cgroup_memory_unavailable") from exc

def _nonprotected_memory_admission(conn, project_id: str,
                                   compute: dict) -> dict | None:
    """Protect host memory for both control-plane projects across pools.

    Heavy/build slot limits prevent within-pool collisions, but they alone
    permit one heavy plus two build jobs whose declared maxima may exceed
    host RAM. Check aggregate active lease reservations atomically with
    first admission and reject unsupported/unobservable host headroom.
    """
    if compute.get("pool") in ("protected", "disabled"):
        return None
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[2] == "kB":
                if fields[0] in ("MemTotal:", "MemAvailable:"):
                    mem[fields[0][:-1]] = int(fields[1]) // 1024
        host_mb = mem["MemTotal"]
        cgroup_limit_mb, cgroup_free_mb = _effective_cgroup_memory_limits_mb(host_mb)
        available_mb = mem["MemAvailable"]
        if host_mb <= 0 or available_mb < 0 or cgroup_limit_mb <= 0:
            raise ValueError("host memory numbers invalid")
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError("resource_host_memory_evidence_unavailable") from exc

    contracts = runtime.load_contracts()
    protected_mb = sum(
        int((p.get("compute") or {}).get("memory_soft_mb") or 0)
        for p in contracts["projects"].values()
        if (p.get("compute") or {}).get("pool") == "protected"
    )
    safety_mb = max(512, int(host_mb * 0.05))
    budget_mb = int(compute.get("memory_soft_mb") or 0)
    if budget_mb <= 0:
        return {"acquired": False, "reason": "resource_memory_budget_missing",
                "project_id": project_id}
    now = datetime.now(timezone.utc).isoformat()
    leases = conn.execute(
        "SELECT pool,memory_soft_mb FROM resource_leases WHERE lease_until>?",
        (now,),
    ).fetchall()
    other_mb = 0
    for lease in leases:
        pool_name = lease["pool"]
        reserved_mb = lease["memory_soft_mb"]
        if (pool_name not in contracts["resource_pools"]
                or type(reserved_mb) is not int or reserved_mb <= 0):
            return {"acquired": False, "reason": "resource_existing_lease_invalid",
                    "project_id": project_id}
        if pool_name != "protected":
            other_mb += reserved_mb
    # MemAvailable is an additional pressure gate, not a proxy for leases.
    # The global host reserve and the leaf's delegated memory.max are
    # independent ceilings. Deducting zCloud/zSSH's global RAM reserve from
    # a dedicated FTMO-only cgroup would double-count unrelated services and
    # prevent even an appropriately bounded heavy job from ever starting.
    if (budget_mb + other_mb > host_mb - protected_mb - safety_mb
            or available_mb < budget_mb + safety_mb
            or budget_mb > cgroup_limit_mb
            or budget_mb > cgroup_free_mb):
        return {"acquired": False, "reason": "resource_protected_memory_headroom",
                "project_id": project_id}
    return None


def _bounded_cpu_count(value, available: int) -> int:
    requested = max(1, int(math.ceil(float(value or 1))))
    return max(1, min(max(1, int(available)), requested))


def _apply_process_limits(project_id: str, parent_pid: int | None = None,
                          compute: dict | None = None) -> None:
    """Apply inherited Linux process bounds from the canonical project contract."""
    if sys.platform == "linux" and parent_pid is not None:
        # If the governor wrapper itself is SIGKILLed or crashes, Linux
        # should also kill its *direct* supervised child instead of letting
        # it outlive the renewable lease unnoticed. This does not replace
        # systemd/cgroup containment for descendants that start new sessions.
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, int(signal.SIGKILL), 0, 0, 0) != 0:
            raise RuntimeError("resource_parent_death_watch_unavailable")
        if os.getppid() != parent_pid:
            os._exit(75)

    # The admitted compute contract must be identical to the inherited
    # child limits; reloading it in the fork could select a different pool
    # or budget if configuration changes between admission and launch.
    compute = dict(compute if compute is not None else
                   (runtime.project_contract(project_id).get("compute") or {}))

    if hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity"):
        allowed = sorted(os.sched_getaffinity(0))
        if not allowed:
            raise RuntimeError("resource_cpu_affinity_unavailable")
        # Preserve one schedulable CPU on a 2-CPU host, or two on a
        # larger host, for zCloud and the separately authorized zSSH
        # control-plane. Resource leases alone do not reserve OS CPUs.
        reserve = min(2, max(0, len(allowed) - 1))
        if compute.get("pool") != "protected":
            if not reserve:
                raise RuntimeError("protected_control_cpu_headroom_unavailable")
            allowed = allowed[reserve:]
        elif project_id == "zssh" and len(allowed) > 1:
            # Keep the dedicated zSSH control path usable when zCloud is
            # busy; zSSH still requires independent manual authorization.
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


def _renew_existing_lease(conn, project_id: str, owner_id: str,
                          acquired_at: str, lease_seconds: int,
                          metadata: dict) -> dict:
    """Renew only a STILL-ACTIVE lease, never reacquire a lost heavy slot.

    A scheduled renewal may be delayed beyond TTL by a paused worker or
    SQLite contention. The normal public acquire API can admit a *new*
    lease after expiry; that would allow a running orphan and a second
    heavyweight project to overlap. Serialize verification and renewal.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        now = datetime.now(timezone.utc).isoformat()
        row = conn.execute(
            "SELECT acquired_at,lease_until FROM resource_leases "
            "WHERE project_id=? AND owner_id=?", (project_id, owner_id)
        ).fetchone()
        if (row is None or row["acquired_at"] != acquired_at
                or not str(row["lease_until"]) > now):
            conn.rollback()
            return {"acquired": False, "reason": "resource_lease_lost",
                    "project_id": project_id}
        result = runtime.acquire_resource(
            conn, project_id, owner_id, lease_seconds=lease_seconds,
            metadata=metadata,
        )
        if not result.get("acquired") or result.get("renewed") is not True:
            conn.rollback()
            return {"acquired": False, "reason": "resource_renewal_not_confirmed",
                    "project_id": project_id}
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def _retain_owned_lease(conn, project_id: str, owner_id: str,
                        acquired_at: str) -> bool:
    """Retain a stuck process lease without renewing a newer owner instance."""
    from datetime import timedelta
    conn.execute("BEGIN IMMEDIATE")
    try:
        until = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "UPDATE resource_leases SET lease_until=?,metadata_json=? "
            "WHERE project_id=? AND owner_id=? AND acquired_at=? AND lease_until>?",
            (until, '{"cleanup":"process_group_unverified","executor":"zcloud-governed-exec"}',
             project_id, owner_id, acquired_at, now),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def _release_owned_lease(conn, project_id: str, owner_id: str,
                         acquired_at: str) -> bool:
    """Only delete the lease from our own admission generation."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = conn.execute(
            "DELETE FROM resource_leases WHERE project_id=? AND owner_id=? "
            "AND acquired_at=?", (project_id, owner_id, acquired_at),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def _signal_process_group(proc: subprocess.Popen | None, signum: int) -> None:
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signum)
    except ProcessLookupError:
        return


def _group_exists(proc: subprocess.Popen) -> bool:
    """Check for live (non-zombie) members of our private process group.

    Linux keeps a process group addressable through killpg(0) while orphan
    zombies await their reaper, even though those zombies consume no work.
    Treat a missing or inaccessible /proc member as inconclusive rather than
    claiming safe release.
    """
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    found = False
    try:
        with os.scandir("/proc") as processes:
            for entry in processes:
                if not entry.name.isdecimal():
                    continue
                try:
                    source = (Path("/proc") / entry.name / "stat").read_text()
                    fields = source.rpartition(")")[2].split()
                except FileNotFoundError:
                    continue
                except OSError:
                    return True
                if len(fields) < 3:
                    return True
                try:
                    group = int(fields[2])
                except ValueError:
                    return True
                if group != proc.pid:
                    continue
                found = True
                if fields[0] not in ("Z", "X"):
                    return True
    except OSError:
        return True
    # All observed group members are already dead; a zombie cannot do work.
    # If killpg said yes but no group member was visible, stay conservative.
    return not found

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
    _assert_shared_resource_db(Path(db_path))

    lease_seconds = max(60, min(6 * 3600, int(lease_seconds or 300)))
    renew_seconds = max(5, min(int(renew_seconds or 60), max(5, lease_seconds // 2)))
    compute = dict(runtime.project_contract(project_id).get("compute") or {})
    memory_budget = int(compute.get("memory_soft_mb") or 0) * 1024 * 1024
    if compute.get("pool") == "heavy" and memory_budget <= 0:
        raise RuntimeError("heavy_memory_budget_unavailable")
    conn = connect(Path(db_path))
    proc = None
    old_handlers = {}
    termination_requested = None
    acquired = False
    acquired_at = None
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
        pressure = _nonprotected_memory_admission(conn, project_id, compute)
        if pressure is not None:
            conn.rollback()
            _admission_denied(pressure)
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
        acquired_at = str(result["lease"]["acquired_at"])

        def forward(signum, _frame):
            nonlocal termination_requested
            if termination_requested is None:
                termination_requested = signum
            if proc is not None:
                _signal_process_group(proc, signum)

        # Install handlers BEFORE child launch. A SIGTERM between Popen and
        # first renewal otherwise kills the governor but may orphan heavy
        # descendants with an active lease still in the shared pool.
        for signum in (signal.SIGTERM, signal.SIGINT):
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, forward)

        if termination_requested is not None:
            return 128 + int(termination_requested)

        env = os.environ.copy()
        env["ZCLOUD_RESOURCE_PROJECT"] = str(project_id)
        env["ZCLOUD_RESOURCE_OWNER"] = str(owner_id)
        wrapper_pid = os.getpid()
        proc = subprocess.Popen(
            list(command),
            env=env,
            preexec_fn=lambda: _apply_process_limits(project_id, wrapper_pid, compute),
            start_new_session=True,
        )
        if termination_requested is not None:
            # Stop the complete session even when the direct subprocess
            # ignores its signal. Finally verifies descendants before
            # the original lease can be released.
            _stop_process_group(proc)
            return 128 + int(termination_requested)

        renewed = _renew_existing_lease(
            conn, project_id, owner_id, acquired_at, lease_seconds,
            _metadata(command, pid=proc.pid),
        )
        if not renewed.get("acquired"):
            _stop_process_group(proc)
            _admission_denied(renewed)
            return 75

        next_renewal = time.monotonic() + renew_seconds
        next_memory_check = time.monotonic()
        while True:
            # An ignored SIGTERM must not strand the wrapper in its ordinary
            # polling/renewal loop indefinitely. Start bounded group cleanup.
            if termination_requested is not None:
                _stop_process_group(proc)
                return 128 + int(termination_requested)
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
                renewed = _renew_existing_lease(
                    conn, project_id, owner_id, acquired_at, lease_seconds,
                    _metadata(command, pid=proc.pid),
                )
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
                if not _retain_owned_lease(
                    conn, project_id, owner_id, acquired_at
                ):
                    raise RuntimeError("resource_lease_retention_failed")
            elif acquired:
                if not _release_owned_lease(conn, project_id, owner_id, acquired_at):
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

def inspect_pool(db_path: Path, project_id: str) -> dict:
    """Read-only, bounded admission visibility without creating DB or leases.

    This is an advisory instant snapshot, never evidence that concurrent
    admission will succeed or that the binary is deployed in production.
    """
    _assert_shared_resource_db(db_path)
    contracts = runtime.load_contracts()
    compute = runtime.project_contract(project_id, contracts).get("compute") or {}
    pool = str(compute.get("pool") or "")
    capacities = contracts["resource_pools"]
    target = Path(db_path)
    if target.is_symlink() or not target.is_file():
        raise RuntimeError("resource_shared_database_unavailable")
    from urllib.parse import quote
    uri = "file:" + quote(str(target.resolve(strict=True)), safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        conn.execute("PRAGMA query_only=ON")
        now = datetime.now(timezone.utc).isoformat()
        rows = conn.execute(
            "SELECT pool,COUNT(*) FROM resource_leases "
            "WHERE lease_until>? GROUP BY pool", (now,),
        ).fetchall()
        observed = {str(row[0]): int(row[1]) for row in rows}
        unexpected = sorted(set(observed) - set(capacities))
        pools = {
            name: {
                "capacity": int(cfg["slots"]),
                "admitted": int(observed.get(name, 0)),
                "remaining": max(0, int(cfg["slots"]) - int(observed.get(name, 0))),
            }
            for name, cfg in capacities.items()
        }
        return {
            "source": "readonly_shared_sqlite_snapshot",
            "project_id": project_id,
            "pool": pool,
            "observed_at": now,
            "pool_snapshot": pools,
            "unknown_pool_count": len(unexpected),
            "overcommitted": any(value["admitted"] > value["capacity"]
                                 for value in pools.values()),
            "pinned_database": bool(os.environ.get("ZCLOUD_RESOURCE_DB")),
            "admission_granted": False,
            "runtime_deployment_verified": False,
        }
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "history.db")
    parser.add_argument("--project", required=True)
    parser.add_argument("--owner", help="Unique live lease owner; not needed for --inspect-pool")
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--renew-seconds", type=int, default=60)
    parser.add_argument("--inspect-pool", action="store_true",
                        help="Read a redacted shared-pool snapshot, without launching a workload")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if args.inspect_pool:
        if command:
            parser.error("--inspect-pool does not accept a workload command")
        print(json.dumps(inspect_pool(args.db, args.project),
                         ensure_ascii=False, sort_keys=True))
        return 0
    if not args.owner:
        parser.error("--owner is required when launching a governed command")
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
