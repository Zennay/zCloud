#!/usr/bin/env python3
"""Run one command under zCloud resource-governor admission and process bounds."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
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
    if _uses_live_governor_authority(path):
        # A supervised workload with a pinned shared database must never
        # create its own empty SQLite store after a missing disk/mount.
        # Doing so would silently create an independent "heavy" pool.
        if (path.is_symlink() or not path.is_file()
                or not os.access(path, os.R_OK | os.W_OK)):
            raise RuntimeError("resource_shared_database_unavailable")
    if _uses_live_governor_authority(path):
        # The existing file can vanish after preflight. SQLite mode=rw
        # refuses to create a replacement when a shared mount disappears.
        from urllib.parse import quote
        uri = "file:" + quote(str(path.resolve(strict=True)), safe="/") + "?mode=rw"
        conn = sqlite3.connect(uri, uri=True, timeout=15)
    else:
        conn = sqlite3.connect(path, timeout=15)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        if _uses_live_governor_authority(path):
            # An existing, unrelated SQLite database at the pinned path
            # must not be silently initialized as an empty resource pool.
            row = conn.execute(
                "SELECT type FROM sqlite_master WHERE name='resource_leases'"
            ).fetchone()
            fields = {
                field["name"]: field
                for field in conn.execute("PRAGMA table_info(resource_leases)")
            }
            required = {"project_id", "owner_id", "pool", "workload_class",
                        "cpu_soft_cores", "memory_soft_mb", "acquired_at",
                        "lease_until", "metadata_json"}
            if (row is None or row["type"] != "table"
                    or not required.issubset(fields)
                    or fields["project_id"]["pk"] != 1
                    or fields["owner_id"]["pk"] != 2):
                # Without the canonical composite primary key, a pre-existing
                # SQLite file can store ambiguous duplicate owner leases and
                # silently violate the one-heavy-job admission invariant.
                raise RuntimeError("resource_shared_database_schema_unavailable")
        else:
            runtime.init_tables(conn)
            conn.commit()  # isolated canary DB only
        return conn
    except Exception:
        conn.close()
        raise


def _assert_portfolio_pool_capacities(contracts: dict) -> None:
    """Fail closed on pool widening that breaks portfolio exclusivity.

    These are safety ceilings from the canonical portfolio contract, not
    adjustable per-worker local hints. In particular, two projects with
    different contract checkouts must not turn the single heavy slot into
    independent two-slot admissions against the same SQLite database.
    """
    pools = contracts["resource_pools"]
    limits = {"heavy": 1, "build": 2, "disabled": 0}
    for name, ceiling in limits.items():
        if name not in pools:
            continue
        actual = pools[name].get("slots")
        if type(actual) is not int or actual > ceiling:
            raise RuntimeError("resource_pool_capacity_unsafe")
    protected = pools.get("protected")
    if protected is not None:
        slots = protected.get("slots")
        if type(slots) is not int or slots < 2:
            raise RuntimeError("resource_control_pool_capacity_unsafe")

    required_project_pools = {
        "cloud": "protected", "zssh": "protected",
        "ftmo": "heavy", "haxlab": "heavy", "lightup": "heavy",
        "supa": "build", "raiseai": "build", "ulab": "disabled",
    }
    # A stale external project checkout must not silently classify FTMO
    # backtests, HaxLab training or LightUp labs as a build job. A
    # project with an approved future pool change needs a reviewed update
    # to this governor rather than widening active production leases.
    for project_id, expected_pool in required_project_pools.items():
        project = contracts["projects"].get(project_id)
        if project is None:
            continue  # isolated contract fixtures may cover fewer projects
        if (project.get("compute") or {}).get("pool") != expected_pool:
            raise RuntimeError("resource_project_pool_contract_unsafe")


def _local_boot_identity() -> str:
    """Identify the Linux host boot owning a heavy supervised lease.

    A PID, PGID or systemd cgroup path is local to one host/boot. A
    different VPS must never treat its own empty /proc as proof that
    an expired heavy process from the other host has stopped.
    """
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text().strip().lower()
        import uuid
        return str(uuid.UUID(value))
    except (OSError, ValueError) as exc:
        raise RuntimeError("resource_host_boot_identity_unavailable") from exc


def _service_cgroup_for_pid(pid: int) -> str | None:
    """Capture the dedicated systemd service identity of a supervised child."""
    if sys.platform != "linux":
        return None
    try:
        rows = (Path("/proc") / str(pid) / "cgroup").read_text().splitlines()
    except FileNotFoundError:
        return None  # A very short-lived child may already have exited.
    except OSError as exc:
        raise RuntimeError("resource_child_cgroup_unavailable") from exc
    groups = [line.partition("::")[2].strip()
              for line in rows if line.startswith("0::")]
    if len(groups) != 1:
        return None
    parts = [value for value in groups[0].split("/") if value]
    if (not parts or not parts[-1].endswith(".service")
            or any(part in (".", "..") for part in parts)):
        return None
    return "/" + "/".join(parts)


def _live_service_members(cgroup_path: str) -> bool:
    """Read-only bounded subtree census for an expired heavy lease's unit."""
    if not isinstance(cgroup_path, str) or not cgroup_path.startswith("/"):
        raise RuntimeError("resource_expired_cgroup_invalid")
    parts = [part for part in cgroup_path.split("/") if part]
    if (not parts or not parts[-1].endswith(".service")
            or any(part in (".", "..") for part in parts)):
        raise RuntimeError("resource_expired_cgroup_invalid")
    root = Path("/sys/fs/cgroup").resolve(strict=True)
    folder = root
    for part in parts:
        folder = folder / part
        if folder.is_symlink():
            raise RuntimeError("resource_expired_cgroup_invalid")
    if not folder.is_dir():
        return False  # Unit no longer exists, no old members can remain.
    pending = [folder]
    count = 0
    while pending:
        unit = pending.pop()
        count += 1
        if count > 1024:
            raise RuntimeError("resource_expired_cgroup_inventory_unbounded")
        for child in unit.iterdir():
            if child.is_dir():
                if child.is_symlink():
                    raise RuntimeError("resource_expired_cgroup_invalid")
                pending.append(child)
        pids = (unit / "cgroup.procs").read_text().splitlines()
        if len(pids) > 20000:
            raise RuntimeError("resource_expired_cgroup_inventory_unbounded")
        for pid in pids:
            if not pid.isdecimal():
                raise RuntimeError("resource_expired_cgroup_invalid")
            if int(pid) == os.getpid():
                continue
            try:
                fields = (Path("/proc") / pid / "stat").read_text().rpartition(")")[2].split()
            except FileNotFoundError:
                continue
            if len(fields) < 3 or fields[0] not in {"Z", "X"}:
                return True
    return False


def _expired_heavy_group_active(conn, *, live_authority: bool) -> bool:
    """Don't recycle an expired heavy lease while its known process group lives.

    The shared runtime deletes expired rows during acquisition. Before that
    irreversible cleanup, inspect *known* supervised heavy PID generations
    under the same BEGIN IMMEDIATE transaction. This closes the ordinary
    TTL-expiry/restarted-wrapper overlap for descendants that remain in
    the original process group. Detached sessions additionally need their
    authorized dedicated systemd service to be drained (and full live proof).
    """
    if sys.platform != "linux":
        return True
    cutoff = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT metadata_json FROM resource_leases "
        "WHERE pool='heavy' AND lease_until<=? LIMIT 257", (cutoff,)
    ).fetchall()
    if len(rows) > 256:
        return True
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
            if not isinstance(metadata, dict):
                return True
            pid = metadata.get("pid")
            scope = metadata.get("service_cgroup")
            if live_authority and (
                    metadata.get("executor") == "zcloud-governed-exec"
                    or pid is not None or scope is not None):
                # An expired heavy lease from another writer may record a
                # child PID without an executor label. Its local absence
                # is not proof of cleanup on the originating VPS/boot.
                if metadata.get("host_boot_id") != _local_boot_identity():
                    return True
            if pid is None:
                # A heavy worker can start before its first PID-bearing
                # renewal. In that window the initial lease still records
                # its dedicated service cgroup; a live member blocks reuse.
                if scope is not None:
                    if _live_service_members(scope):
                        return True
                elif metadata.get("executor") == "zcloud-governed-exec":
                    return True
                continue
            if type(pid) is not int or pid <= 1:
                return True
            if _process_group_has_live_members(pid):
                return True
            if scope is not None and _live_service_members(scope):
                return True
        except (TypeError, ValueError, KeyError, OSError, RuntimeError):
            return True
    return False


def _admitted_pool_over_limit(conn, pool_name: str) -> bool:
    """Enforce hard slot ceiling after tentative admission, before commit.

    A different checkout may change the runtime slots between our contract
    preflight and resource acquire. BEGIN IMMEDIATE makes the independent
    active-lease count authoritative for this admission transaction.
    """
    ceilings = {"heavy": 1, "build": 2, "disabled": 0}
    ceiling = ceilings.get(pool_name)
    if ceiling is None:
        return False  # Protected control-plane work stays admissible.
    now = datetime.now(timezone.utc).isoformat()
    occupied = int(conn.execute(
        "SELECT COUNT(*) FROM resource_leases "
        "WHERE pool=? AND lease_until>?", (pool_name, now),
    ).fetchone()[0])
    return occupied > ceiling


def _uses_live_governor_authority(path: Path) -> bool:
    """Separate the canonical live pool from deliberately isolated canaries.

    An operator-pinned DB is always a live authority. The repository's
    default history.db is also treated as live when no pin was provided.
    An explicit alternate unpinned SQLite path is an isolated canary and
    cannot establish cross-project production exclusion or an incident
    receipt. Service owners must pin ZCLOUD_RESOURCE_DB for production.
    """
    if os.environ.get("ZCLOUD_RESOURCE_DB") is not None:
        return True
    return (Path(path).expanduser().resolve(strict=False) ==
            (ROOT / "history.db").resolve(strict=False))


def _legacy_haxlab_training_active() -> bool:
    """Deny a NEW heavy lease if the observed ungoverned self-play is active.

    A running trainer from the historical service may consume the exact
    heavy resource the SQLite pool does not yet know about. Never migrate,
    signal, or claim ownership of that process here. This is a temporary
    admission veto, not evidence of FTMO/LightUp coverage or a live release.
    """
    if sys.platform != "linux":
        raise RuntimeError("resource_process_visibility_unsupported")
    count = 0
    try:
        with os.scandir("/proc") as procs:
            for item in procs:
                if not item.name.isdecimal():
                    continue
                count += 1
                if count > 20000:
                    raise RuntimeError("resource_process_inventory_unbounded")
                # The ownerless coach unit may still contain an exited
                # zombie or a newly admitted governor wrapper. Neither is
                # evidence of an *ungoverned running trainer* by itself.
                if int(item.name) == os.getpid():
                    continue
                directory = Path("/proc") / item.name
                try:
                    stat_fields = (directory / "stat").read_text().rpartition(")")[2].split()
                    if len(stat_fields) < 3:
                        raise RuntimeError("resource_process_stat_invalid")
                    if stat_fields[0] in {"Z", "X"}:
                        continue
                    name = (directory / "comm").read_text().strip()
                    cgroup = (directory / "cgroup").read_text()
                    in_legacy_coach = any(
                        line.partition("::")[2].strip().endswith(
                            "/fuckball-ai-coach.service")
                        for line in cgroup.splitlines()
                        if line.startswith("0::")
                    )
                    # Count runnable training workers, not the presence of
                    # systemd or our own governor in the same service scope.
                    if in_legacy_coach and (
                            name in {"node", "nodejs"} or name.startswith("python")):
                        return True
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise RuntimeError("resource_process_inventory_unavailable") from exc
                if name not in {"node", "nodejs"}:
                    continue
                try:
                    tokens = (directory / "cmdline").read_bytes().split(b"\x00")
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise RuntimeError("resource_process_inventory_unavailable") from exc
                if any(token in {
                        b"training/haxlab_selfplay.cjs",
                        b"training/continuous.cjs",
                    } or token.endswith((
                        b"/training/haxlab_selfplay.cjs",
                        b"/training/continuous.cjs",
                    )) for token in tokens):
                    return True
    except OSError as exc:
        raise RuntimeError("resource_process_inventory_unavailable") from exc
    return False


def _pinned_database_identity(path: Path) -> tuple[int, int] | None:
    """Observe canonical production file identity without opening a new DB.

    If an operator-pinned SQLite mount/file is replaced, the wrapper must
    stop and reconcile its original lease instead of believing two files
    under the same pathname belong to the same heavy pool.
    """
    if not _uses_live_governor_authority(path):
        return None
    try:
        status = path.stat()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("resource_shared_database_identity_invalid")
        return (status.st_dev, status.st_ino)
    except OSError as exc:
        raise RuntimeError("resource_shared_database_identity_unavailable") from exc


def _assert_database_identity(path: Path, identity: tuple[int, int] | None) -> None:
    if identity is not None and _pinned_database_identity(path) != identity:
        raise RuntimeError("resource_shared_database_identity_changed")


def _assert_shared_resource_db(db_path: Path) -> None:
    """Prevent accidental split-brain pools when a service pins its DB path.

    Unit/service launchers should export ZCLOUD_RESOURCE_DB to the single
    shared SQLite authority. Production paths must not be silently replaced
    by local checkout or temporary per-project database files. Temporary
    canary databases without an explicitly pinned environment stay supported.
    """
    configured = os.environ.get("ZCLOUD_RESOURCE_DB")
    if configured is not None:
        # An empty env override is not a valid way to disable the shared
        # production authority; this would turn a pinned run into a
        # non-shared local checkout by accident.
        if not configured.strip():
            raise RuntimeError("resource_database_identity_missing")
        supplied = Path(db_path).expanduser().resolve(strict=False)
        canonical = Path(configured).expanduser().resolve(strict=False)
        if supplied != canonical:
            raise RuntimeError("resource_database_identity_mismatch")


def _effective_cgroup_memory_limits_mb(
    host_mb: int, *, dedicated_heavy_leaf: bool = False
) -> tuple[int, int, int | None]:
    """Return inherited cgroup cap and usable admission headroom.

    A bounded, dedicated heavy service already contains its own wrapper.
    Its leaf memory.current therefore consumes part of the same total
    memory.max budget, not *additional* memory on top of the requested
    workload limit. Check the leaf ceiling as total capacity, while
    retaining free-headroom checks on all ancestor slices that can
    contain unrelated workloads. For shared build/test scopes, charge
    leaf memory.current as before.
    """
    remaining_mb = host_mb
    dedicated_leaf_mb = None
    try:
        controls = Path("/proc/self/cgroup").read_text().splitlines()
        unified = [line.split("::", 1)[1] for line in controls if line.startswith("0::")]
        if not unified:
            return host_mb, remaining_mb, None  # Legacy cgroup v1.
        if len(unified) != 1:
            raise RuntimeError("resource_cgroup_identity_ambiguous")
        components = [part for part in unified[0].split("/") if part]
        if any(part in (".", "..") for part in components):
            raise RuntimeError("resource_cgroup_path_invalid")
        path = Path("/sys/fs/cgroup")
        for index, component in enumerate([""] + components):
            if component:
                path = path / component
            # cgroup-v2 mount root has controllers but is not itself a
            # limited child cgroup; Linux can omit memory.max there.
            # Every non-root ancestor and leaf remains mandatory.
            control = path / "memory.max"
            if index == 0 and not control.exists():
                continue
            raw = control.read_text().strip()
            if raw == "max":
                continue
            maximum = int(raw)
            current = int((path / "memory.current").read_text().strip())
            if maximum <= 0 or current < 0:
                raise RuntimeError("resource_cgroup_memory_invalid")
            ceiling_mb = maximum // (1024 * 1024)
            host_mb = min(host_mb, ceiling_mb)
            if dedicated_heavy_leaf and index == len(components):
                dedicated_leaf_mb = ceiling_mb
                # The leaf's memory.max already caps the whole service,
                # including its current wrapper, forked descendants and
                # future child allocations. Subtracting that same current
                # usage from the full requested total double-counts it.
                remaining_mb = min(remaining_mb, ceiling_mb)
            else:
                remaining_mb = min(
                    remaining_mb, max(0, (maximum-current)//(1024*1024))
                )
        return host_mb, remaining_mb, dedicated_leaf_mb
    except (OSError, ValueError, IndexError) as exc:
        raise RuntimeError("resource_cgroup_memory_unavailable") from exc

def _nonprotected_memory_admission(conn, project_id: str, owner_id: str,
                                   compute: dict, *,
                                   dedicated_heavy_leaf: bool = False) -> dict | None:
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
        cgroup_limit_mb, cgroup_free_mb, dedicated_leaf_mb = _effective_cgroup_memory_limits_mb(
            host_mb, dedicated_heavy_leaf=dedicated_heavy_leaf
        )
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
        "SELECT project_id,owner_id,pool,memory_soft_mb "
        "FROM resource_leases WHERE lease_until>?",
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
        if pool_name != "protected" and not (
                lease["project_id"] == project_id and lease["owner_id"] == owner_id
        ):
            other_mb += reserved_mb
    # MemAvailable is an additional pressure gate, not a proxy for leases.
    # The global host reserve and the leaf's delegated memory.max are
    # independent ceilings. Deducting zCloud/zSSH's global RAM reserve from
    # a dedicated FTMO-only cgroup would double-count unrelated services and
    # prevent even an appropriately bounded heavy job from ever starting.
    # A deliberately stricter heavy service memory.max is safe: account
    # its full project budget for portfolio admission, but require only
    # the smaller *enforced* ceiling against its own cgroup ancestry.
    # The host/protected-pool checks still reserve the full contract.
    cgroup_need_mb = (
        min(budget_mb, dedicated_leaf_mb)
        if dedicated_heavy_leaf and dedicated_leaf_mb is not None
        else budget_mb
    )
    if (budget_mb + other_mb > host_mb - protected_mb - safety_mb
            or available_mb < budget_mb + safety_mb
            or (not dedicated_heavy_leaf and budget_mb > cgroup_limit_mb)
            or cgroup_need_mb > cgroup_free_mb):
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


def _metadata(command, *, pid=None, service_cgroup=None,
              host_boot_id=None):
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
        if service_cgroup is None:
            service_cgroup = _service_cgroup_for_pid(int(pid))
    if service_cgroup is not None:
        payload["service_cgroup"] = service_cgroup[:256]
    if host_boot_id is not None:
        payload["host_boot_id"] = host_boot_id
    return payload


def _exit_code(returncode: int) -> int:
    return returncode if returncode >= 0 else 128 + abs(returncode)


def _matches_admitted_contract(lease: dict, compute: dict) -> bool:
    """Do not launch against a pool/budget different from what was reserved."""
    try:
        return (
            str(lease["pool"]) == str(compute["pool"])
            and str(lease["workload_class"]) == str(compute["class"])
            and int(lease["memory_soft_mb"]) == int(compute["memory_soft_mb"])
            and float(lease["cpu_soft_cores"]) == float(compute["cpu_soft_cores"])
        )
    except (TypeError, ValueError, KeyError):
        return False


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


def _owned_heavy_generation_active(conn, project_id: str, owner_id: str,
                                   acquired_at: str) -> bool:
    """Observe the original active generation without changing any lease.

    Other authorized paths can delete or replace a lease while its child
    still executes. Do not wait until the next scheduled lease renewal
    (potentially 60 seconds) before supervising the old process tree.
    A missing/expired/replaced generation is unsafe, never a free slot.
    """
    observed = conn.execute(
        "SELECT acquired_at,lease_until FROM resource_leases "
        "WHERE project_id=? AND owner_id=?",
        (project_id, owner_id),
    ).fetchone()
    return bool(
        observed is not None
        and observed["acquired_at"] == acquired_at
        and str(observed["lease_until"]) > datetime.now(timezone.utc).isoformat()
    )


def _renew_existing_lease(conn, project_id: str, owner_id: str,
                          acquired_at: str, lease_seconds: int,
                          metadata: dict, admitted_compute: dict,
                          live_authority: bool = False) -> dict:
    """Renew only a STILL-ACTIVE lease, never reacquire a lost heavy slot.

    A scheduled renewal may be delayed beyond TTL by a paused worker or
    SQLite contention. The normal public acquire API can admit a *new*
    lease after expiry; that would allow a running orphan and a second
    heavyweight project to overlap. Serialize verification and renewal.
    """
    if admitted_compute.get("pool") == "heavy" and live_authority:
        # Dynamic service changes cannot quietly widen CPU/memory limits
        # after the initial heavy admission.
        _require_heavy_unit_bounds(admitted_compute)
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
        current_contracts = runtime.load_contracts()
        _assert_portfolio_pool_capacities(current_contracts)
        current_compute = runtime.project_contract(
            project_id, current_contracts
        ).get("compute") or {}
        if any(str(current_compute.get(key)) != str(admitted_compute.get(key))
               for key in ("pool", "class", "cpu_soft_cores", "memory_soft_mb")):
            conn.rollback()
            return {"acquired": False, "reason": "resource_contract_changed",
                    "project_id": project_id}
        result = runtime.acquire_resource(
            conn, project_id, owner_id, lease_seconds=lease_seconds,
            metadata=metadata,
        )
        if (not result.get("acquired") or result.get("renewed") is not True
                or not _matches_admitted_contract(result.get("lease") or {},
                                                  admitted_compute)):
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
    """Extend only the still-active original generation for orphan recovery.

    Keep its child process identity in metadata. Overwriting the whole
    metadata with just a cleanup flag would remove the PID that later
    admission needs to detect surviving process groups after lease expiry.
    """
    from datetime import timedelta
    conn.execute("BEGIN IMMEDIATE")
    try:
        now = datetime.now(timezone.utc).isoformat()
        row = conn.execute(
            "SELECT metadata_json FROM resource_leases "
            "WHERE project_id=? AND owner_id=? AND acquired_at=? "
            "AND lease_until>?",
            (project_id, owner_id, acquired_at, now),
        ).fetchone()
        if row is None:
            conn.rollback()
            return False
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            conn.rollback()
            return False
        if not isinstance(metadata, dict):
            conn.rollback()
            return False
        # Retain only minimal bounded, non-secret incident evidence.
        incident = {
            "executor": "zcloud-governed-exec",
            "cleanup": "process_group_unverified",
        }
        if type(metadata.get("pid")) is int and metadata["pid"] > 1:
            incident["pid"] = metadata["pid"]
        if isinstance(metadata.get("service_cgroup"), str):
            incident["service_cgroup"] = metadata["service_cgroup"][:256]
        if isinstance(metadata.get("host_boot_id"), str):
            incident["host_boot_id"] = metadata["host_boot_id"][:36]
        encoded = json.dumps(incident, separators=(",", ":"), sort_keys=True)
        until = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        cursor = conn.execute(
            "UPDATE resource_leases SET lease_until=?,metadata_json=? "
            "WHERE project_id=? AND owner_id=? AND acquired_at=? AND lease_until>?",
            (until, encoded, project_id, owner_id, acquired_at, now),
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


def _require_heavy_unit_bounds(compute: dict) -> None:
    """Require a dedicated heavy service's hard cgroup CPU/RAM ceilings.

    Individual RLIMIT_AS and process affinity cannot bound a forked research
    tree's total resident memory or CPU usage. A pinned live heavy unit must
    supply hard kernel limits before admitting any child process.
    """
    try:
        root = Path("/sys/fs/cgroup").resolve(strict=True)
        rows = Path("/proc/self/cgroup").read_text().splitlines()
        owned = [row.partition("::")[2].strip()
                 for row in rows if row.startswith("0::")]
        if len(owned) != 1:
            raise RuntimeError("resource_heavy_cgroup_identity_unavailable")
        parts = [part for part in owned[0].split("/") if part]
        if (not parts or not parts[-1].endswith(".service")
                or any(part in (".", "..") for part in parts)):
            raise RuntimeError("resource_heavy_dedicated_service_required")
        unit = root.joinpath(*parts)
        if not unit.is_dir() or unit.is_symlink():
            raise RuntimeError("resource_heavy_dedicated_service_required")
        cpu = (unit / "cpu.max").read_text().strip().split()
        memory = (unit / "memory.max").read_text().strip()
        if len(cpu) != 2 or cpu[0] == "max" or memory == "max":
            raise RuntimeError("resource_heavy_cgroup_bounds_missing")
        quota, period, memory_bytes = int(cpu[0]), int(cpu[1]), int(memory)
        cpu_budget = float(compute.get("cpu_soft_cores") or 0)
        memory_budget = int(compute.get("memory_soft_mb") or 0) * 1024 * 1024
        if (quota <= 0 or period <= 0 or memory_bytes <= 0
                or cpu_budget <= 0 or memory_budget <= 0
                or quota > cpu_budget * period
                or memory_bytes > memory_budget):
            raise RuntimeError("resource_heavy_cgroup_bounds_unsafe")
    except (OSError, ValueError, OverflowError) as exc:
        raise RuntimeError("resource_heavy_cgroup_bounds_unavailable") from exc


def _owned_service_cgroup_drained() -> bool:
    """Verify the wrapper's private systemd service contains no live workers.

    A governed child can escape its original process group using setsid().
    Releasing a production heavy lease on PGID cleanup alone could then
    admit a second project while the escaped descendant is still running.
    The unit cgroup is read only; never kill or alter an unrelated process.
    An ambiguous/shared/non-systemd cgroup is NOT evidence of cleanup.
    """
    if sys.platform != "linux":
        return False
    try:
        root = Path("/sys/fs/cgroup").resolve(strict=True)
        rows = (Path("/proc/self/cgroup").read_text()).splitlines()
        unified = [line.partition("::")[2].strip()
                   for line in rows if line.startswith("0::")]
        if len(unified) != 1:
            return False
        parts = [value for value in unified[0].split("/") if value]
        if not parts or not parts[-1].endswith(".service"):
            return False  # No dedicated systemd unit: cannot prove cleanup.
        if any(value in (".", "..") for value in parts):
            return False
        parent = root
        for part in parts:
            parent = parent / part
            if parent.is_symlink():
                return False
        if not parent.is_dir() or parent.resolve(strict=True) == root:
            return False

        # cgroup.procs lists only direct members. Descendants can be placed
        # in child cgroups, so examine the whole finite subtree.
        pending = [parent]
        scanned = 0
        while pending:
            path = pending.pop()
            scanned += 1
            if scanned > 1024:
                return False
            for child in path.iterdir():
                if child.is_dir():
                    if child.is_symlink():
                        return False
                    pending.append(child)
            pids = (path / "cgroup.procs").read_text().splitlines()
            if len(pids) > 20000:
                return False
            for pid in pids:
                if not pid.isdecimal():
                    return False
                if int(pid) == os.getpid():
                    continue
                try:
                    fields = (Path("/proc") / pid / "stat").read_text().rpartition(")")[2].split()
                except FileNotFoundError:
                    continue
                if len(fields) < 3 or fields[0] not in {"Z", "X"}:
                    return False
        return True
    except (OSError, ValueError):
        return False


def _process_group_has_live_members(group_id: int) -> bool:
    """Check for live (non-zombie) members of one Linux process group.

    Linux keeps a process group addressable through killpg(0) while orphan
    zombies await their reaper, even though those zombies consume no work.
    Treat a missing or inaccessible /proc member as inconclusive rather than
    claiming safe release.
    """
    try:
        os.killpg(group_id, 0)
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
                if group != group_id:
                    continue
                found = True
                if fields[0] not in ("Z", "X"):
                    return True
    except OSError:
        return True
    # All observed group members are already dead; a zombie cannot do work.
    # If killpg said yes but no group member was visible, stay conservative.
    return not found

def _group_exists(proc: subprocess.Popen) -> bool:
    return _process_group_has_live_members(proc.pid)

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
    wait_seconds: int = 0,
) -> int:
    if not command:
        raise ValueError("command is required")
    owner_id = _safe_owner(owner_id)
    _assert_shared_resource_db(Path(db_path))

    lease_seconds = max(60, min(6 * 3600, int(lease_seconds or 300)))
    renew_seconds = max(5, min(int(renew_seconds or 60), max(5, lease_seconds // 2)))
    wait_seconds = max(0, min(3600, int(wait_seconds or 0)))
    contracts = runtime.load_contracts()
    _assert_portfolio_pool_capacities(contracts)
    compute = dict(runtime.project_contract(project_id, contracts).get("compute") or {})
    memory_budget = int(compute.get("memory_soft_mb") or 0) * 1024 * 1024
    if compute.get("pool") == "heavy" and memory_budget <= 0:
        raise RuntimeError("heavy_memory_budget_unavailable")
    db_identity = _pinned_database_identity(Path(db_path))
    live_authority = _uses_live_governor_authority(Path(db_path))
    heavy_scope = None
    heavy_boot_id = None
    # On a pinned live heavy service, no old orphan or detached training
    # child may share the unit before the next lease is even requested.
    # This closes the window after a crashed wrapper's lease has expired:
    # a new invocation cannot run beside the surviving unleased worker.
    if compute.get("pool") == "heavy" and live_authority:
        _require_heavy_unit_bounds(compute)
        if not _owned_service_cgroup_drained():
            raise RuntimeError("resource_dedicated_heavy_unit_not_idle")
        heavy_scope = _service_cgroup_for_pid(os.getpid())
        if heavy_scope is None:
            raise RuntimeError("resource_heavy_service_identity_unavailable")
        heavy_boot_id = _local_boot_identity()
    conn = connect(Path(db_path))
    try:
        _assert_database_identity(Path(db_path), db_identity)
    except Exception:
        conn.close()
        raise
    proc = None
    old_handlers = {}
    termination_requested = None
    acquired = False
    acquired_at = None
    try:
        # Serialize same-owner admission with the pool capacity reservation.
        # Reusing an active owner key would otherwise renew one lease while
        # starting a second real process without consuming another slot.
        # Install handlers before any capacity wait, not after admission.
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

        deadline = time.monotonic() + wait_seconds
        while True:
            _assert_database_identity(Path(db_path), db_identity)
            _assert_portfolio_pool_capacities(runtime.load_contracts())
            if termination_requested is not None:
                return 128 + int(termination_requested)
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
            # The shared runtime prunes *all* expired pools on acquire.
            # A protected zCloud/zSSH admission must not erase the only
            # surviving recovery evidence for an orphaned heavy child.
            # Reconcile under this same BEGIN IMMEDIATE transaction before
            # allowing the runtime's ordinary cleanup to run.
            orphaned_heavy = (
                _expired_heavy_group_active(conn, live_authority=live_authority)
                if live_authority or compute.get("pool") == "heavy" else False
            )
            if orphaned_heavy:
                if compute.get("pool") == "heavy":
                    conn.rollback()
                    _admission_denied({
                        "reason": "resource_expired_heavy_process_still_active",
                        "project_id": project_id,
                    })
                    return 75
                # Conservatively retain the bounded expired heavy records,
                # preserving the original PID/cgroup/host generation for an
                # authorized owner to resolve. Protecting this slot must not
                # block a different protected control-plane pool.
                now = datetime.now(timezone.utc)
                cutoff = now.isoformat()
                amount = conn.execute(
                    "SELECT COUNT(*) FROM resource_leases "
                    "WHERE pool='heavy' AND lease_until<=?", (cutoff,)
                ).fetchone()[0]
                if amount > 256:
                    conn.rollback()
                    _admission_denied({
                        "reason": "resource_expired_heavy_recovery_unbounded",
                        "project_id": project_id,
                    })
                    return 75
                conn.execute(
                    "UPDATE resource_leases SET lease_until=? "
                    "WHERE pool='heavy' AND lease_until<=?",
                    ((now + timedelta(hours=6)).isoformat(), cutoff),
                )
            # Check pool occupancy before host-memory pressure: if another
            # heavy job owns the only slot, bounded waiters must sleep
            # *without* re-scanning all host/cgroup memory each second.
            # A successful reservation remains uncommitted until the
            # independent host-memory guard approves the new workload.
            result = runtime.acquire_resource(
                conn,
                project_id,
                owner_id,
                lease_seconds=lease_seconds,
                metadata=_metadata(command, service_cgroup=heavy_scope,
                                   host_boot_id=heavy_boot_id),
            )
            if (not result.get("acquired") or result.get("renewed")
                    or not _matches_admitted_contract(result.get("lease") or {}, compute)):
                conn.rollback()
                # Re-attempt only a busy pool; safety or identity denials
                # must never turn into an indefinite retry.
                if result.get("reason") == "resource_pool_busy" and wait_seconds:
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        time.sleep(min(1.0, remaining))
                        continue
                _admission_denied({
                    "reason": ("resource_owner_already_running"
                               if result.get("renewed") else
                               "resource_contract_admission_mismatch"
                               if result.get("acquired") else
                               result.get("reason") or "resource_admission_unavailable"),
                    "project_id": project_id,
                })
                return 75
            if _admitted_pool_over_limit(conn, str(compute.get("pool") or "")):
                conn.rollback()
                _admission_denied({
                    "reason": "resource_pool_capacity_unsafe",
                    "project_id": project_id,
                })
                return 75
            if (compute.get("pool") == "heavy"
                    and _uses_live_governor_authority(Path(db_path))
                    and _legacy_haxlab_training_active()):
                conn.rollback()
                remaining = deadline - time.monotonic()
                if wait_seconds and remaining > 0:
                    # The legacy self-play service is not yet lease-aware.
                    # Wait cooperatively instead of permitting a second
                    # heavy job or restarting the existing trainer.
                    time.sleep(min(1.0, remaining))
                    continue
                _admission_denied({
                    "reason": "resource_unleased_training_active",
                    "project_id": project_id,
                })
                return 75
            pressure = _nonprotected_memory_admission(
                conn, project_id, owner_id, compute,
                dedicated_heavy_leaf=(live_authority and compute.get("pool") == "heavy"),
            )
            if pressure is not None:
                conn.rollback()
                _admission_denied(pressure)
                return 75
            conn.commit()
            acquired = True
            acquired_at = str(result["lease"]["acquired_at"])
            break


        if termination_requested is not None:
            return 128 + int(termination_requested)
        if (compute.get("pool") == "heavy"
                and _uses_live_governor_authority(Path(db_path))
                and _legacy_haxlab_training_active()):
            # A legacy trainer may have started after our transactional
            # /proc veto but before the supervised child was launched.
            # Fail without starting competing research; finally releases
            # the lease we just acquired. Never stop the legacy trainer.
            _admission_denied({
                "reason": "resource_unleased_training_active",
                "project_id": project_id,
            })
            return 75

        if (compute.get("pool") == "heavy" and live_authority
                and not _owned_heavy_generation_active(
                    conn, project_id, owner_id, acquired_at
                )):
            # A separate writer may have revoked this exact heavy lease
            # between transaction commit and the Popen boundary. Never
            # start a research tree on a slot it no longer owns.
            _admission_denied({
                "reason": "resource_heavy_lease_generation_lost",
                "project_id": project_id,
            })
            return 75

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

        _assert_database_identity(Path(db_path), db_identity)
        renewed = _renew_existing_lease(
            conn, project_id, owner_id, acquired_at, lease_seconds,
            _metadata(command, pid=proc.pid, service_cgroup=heavy_scope,
                      host_boot_id=heavy_boot_id),
            compute, live_authority,
        )
        if not renewed.get("acquired"):
            _stop_process_group(proc)
            _admission_denied(renewed)
            return 75

        next_renewal = time.monotonic() + renew_seconds
        next_memory_check = time.monotonic()
        next_generation_check = time.monotonic()
        while True:
            # A swapped/detached shared SQLite file can allow an independent
            # concurrent heavy admission under the same path. Stop the old
            # workload at the first detected identity loss.
            _assert_database_identity(Path(db_path), db_identity)
            # An ignored SIGTERM must not strand the wrapper in its ordinary
            # polling/renewal loop indefinitely. Start bounded group cleanup.
            if termination_requested is not None:
                _stop_process_group(proc)
                return 128 + int(termination_requested)
            returncode = proc.poll()
            if returncode is not None:
                return _exit_code(returncode)

            now = time.monotonic()
            if (compute.get("pool") == "heavy" and live_authority
                    and now >= next_generation_check):
                # Never let an externally deleted/replaced canonical lease
                # leave the old research tree working until its next renewal.
                # This is a read-only owner-generation check; cleanup is
                # handled by the existing fenced finally path below.
                if not _owned_heavy_generation_active(
                    conn, project_id, owner_id, acquired_at
                ):
                    _stop_process_group(proc)
                    _admission_denied({
                        "reason": "resource_heavy_lease_generation_lost",
                        "project_id": project_id,
                    })
                    return 75
                next_generation_check = now + 1.0
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
                    _metadata(command, pid=proc.pid, service_cgroup=heavy_scope,
                      host_boot_id=heavy_boot_id),
                    compute, live_authority,
                )
                if not renewed.get("acquired"):
                    _stop_process_group(proc)
                    _admission_denied(renewed)
                    return 75
                next_renewal = now + renew_seconds
            time.sleep(0.5)
    finally:
        # Keep our signal handlers installed until every descendant is
        # stopped and the lease is durably reconciled. Restoring SIG_DFL
        # before cleanup lets a second SIGTERM kill this wrapper halfway
        # through group termination or SQLite release.
        group_stopped = _stop_process_group(proc)
        # A pinned production service must prove that its *entire*
        # dedicated systemd unit is drained, not just the original PGID.
        # Detached setsid descendants still occupy the same unit cgroup.
        if (group_stopped and proc is not None
                and compute.get("pool") == "heavy"
                and live_authority):
            # Protected zCloud/zSSH work is allowed to use shared control-
            # plane units; demanding an exclusive service cgroup there would
            # needlessly exhaust protected slots. The hard detached-worker
            # policy applies only to pinned multi-process heavy workloads.
            group_stopped = _owned_service_cgroup_drained()
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
                    # A later writer may have removed/replaced this exact
                    # generation after we detected lease loss. Never delete
                    # its successor just to make our cleanup look green.
                    # But a failed DELETE while we STILL own the generation
                    # remains a real release failure, never a false success.
                    _assert_database_identity(Path(db_path), db_identity)
                    remaining = conn.execute(
                        "SELECT acquired_at FROM resource_leases "
                        "WHERE project_id=? AND owner_id=?",
                        (project_id, owner_id),
                    ).fetchone()
                    if (remaining is not None
                            and remaining["acquired_at"] == acquired_at):
                        raise RuntimeError("resource_lease_release_unconfirmed")
                    print(json.dumps({
                        "released": False,
                        "reason": "resource_lease_generation_lost",
                        "project_id": project_id,
                    }, sort_keys=True), file=sys.stderr)
                # This means no lease of OUR generation remains.
                # A successor's lease was neither released nor modified.
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
            try:
                conn.close()
            finally:
                for signum, handler in old_handlers.items():
                    signal.signal(signum, handler)
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
    _assert_portfolio_pool_capacities(contracts)
    compute = runtime.project_contract(project_id, contracts).get("compute") or {}
    pool = str(compute.get("pool") or "")
    capacities = contracts["resource_pools"]
    target = Path(db_path)
    if target.is_symlink() or not target.is_file():
        raise RuntimeError("resource_shared_database_unavailable")
    from urllib.parse import quote
    uri = "file:" + quote(str(target.resolve(strict=True)), safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    # Expired-lease reconciliation uses named fields (metadata_json).
    # Without Row, a tuple lookup throws TypeError and the inspector
    # falsely reports every expired heavy record as unrecoverable.
    conn.row_factory = sqlite3.Row
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
        # Slots visible in SQLite are not actually available if the
        # known pre-migration HaxLab coach is running outside lease control.
        # Keep raw lease availability distinct from conservative admission.
        live_authority = _uses_live_governor_authority(target)
        legacy_active = (_legacy_haxlab_training_active()
                         if live_authority else None)
        # An expired lease can represent a still-running heavy process.
        # Inspect the same recovery evidence as admission *without*
        # calling runtime.resource_status (which prunes expired rows).
        # An advisory snapshot must not advertise capacity which admission
        # will conservatively deny. Isolated canaries have no shared
        # production authority to reconcile.
        expired_recovery = (_expired_heavy_group_active(
            conn, live_authority=True
        ) if live_authority and "heavy" in pools else None)
        if "heavy" in pools:
            heavy = pools["heavy"]
            heavy["legacy_unleased_training_detected"] = legacy_active
            heavy["legacy_detection_scope"] = (
                "canonical_live_authority" if live_authority
                else "not_checked_isolated_canary"
            )
            heavy["expired_heavy_recovery_unresolved"] = expired_recovery
            heavy["effective_remaining"] = (
                0 if legacy_active or expired_recovery else heavy["remaining"]
            )
        return {
            "source": ("readonly_canonical_pool_snapshot" if live_authority
                       else "readonly_isolated_canary_snapshot"),
            "project_id": project_id,
            "pool": pool,
            "observed_at": now,
            "pool_snapshot": pools,
            "unknown_pool_count": len(unexpected),
            "overcommitted": any(value["admitted"] > value["capacity"]
                                 for value in pools.values()),
            "pinned_database": bool(os.environ.get("ZCLOUD_RESOURCE_DB")),
            "canonical_live_authority": live_authority,
            "other_unleased_heavy_jobs_verified_absent": False,
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
    parser.add_argument("--wait-seconds", type=int, default=0,
                        help="Bounded wait for a busy resource pool; default fail-fast")
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
        wait_seconds=args.wait_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
