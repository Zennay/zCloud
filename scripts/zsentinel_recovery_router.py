#!/usr/bin/env python3
"""zSentinel recovery router: durable, fail-closed incident and independent-work decisions.

Processes a trusted, bounded observation bundle. It NEVER acquires claims, dispatches
jobs, retries CI, restarts runners or writes production/Notion/GitHub/SQLite state.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import fcntl
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zsentinel_diagnose import diagnose, utc_time
from zsentinel_selection_holds import operator_lines as selection_hold_lines

UTC = timezone.utc
MAX_INPUT_BYTES = 1_000_000
MAX_STATE_BYTES = 300_000
MAX_TASKS = 250
MAX_CLAIMS = 250
MAX_LEDGER = 200
MAX_RECOMMENDATIONS = 5
SLUG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_PATH = re.compile(r"^[a-zA-Z0-9_.@/+ -]{1,220}$")
BRANCH_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,160}$")
PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def timestamp(value, name):
    return utc_time(value, name)


def bounded_slug(value, name):
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        raise ValueError(f"{name}: bounded ASCII identifier required")
    return value


def utc_string(moment):
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def bounded_path(path):
    if (not isinstance(path, str) or not SAFE_PATH.fullmatch(path)
            or path.startswith("/") or path.startswith("./")
            or path.endswith("/") or "//" in path or "\\" in path
            or any(bit in ("", ".", "..") for bit in path.split("/"))):
        raise ValueError("invalid repository-relative task or claim path")
    return path


def paths_overlap(first, second):
    return first == second or first.startswith(second + "/") or second.startswith(first + "/")


def source_incident_key(project, diagnosis):
    """Do not collapse distinct run/jobs sharing the same project and HEAD."""
    github = project.get("github", {})
    kind = diagnosis.get("reason", "unknown")
    raw_run, raw_job = github.get("run_id"), github.get("job_id")
    ids = []
    for name, value in (("run_id", raw_run), ("job_id", raw_job)):
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**53 - 1:
                raise ValueError(f"github.{name}: safe positive integer required")
            ids.append(f"{name}:{value}")
    if raw_job is not None and raw_run is None:
        raise ValueError("github.job_id requires run_id")
    if kind.startswith("github_") and (raw_run is None or raw_job is None):
        # Without a run identity a cross-snapshot 'dedup' could hide a different job.
        return None
    sha = github.get("head_sha", "")
    if sha and (not isinstance(sha, str) or not SHA.fullmatch(sha)):
        raise ValueError("github.head_sha: full lowercase SHA required")
    if kind == "runtime_progress_missing":
        # A heartbeat from a different claim epoch cannot identify this incident.
        runtime = project.get("runtime", {})
        claim = runtime.get("claim_id")
        epoch = runtime.get("claim_epoch")
        if (not isinstance(claim, str) or not SLUG.fullmatch(claim)
                or type(epoch) is not int or not 1 <= epoch <= 2**53 - 1
                or runtime.get("claim_identity_verified") is not True):
            return None
        ids.extend((f"claim:{claim}", f"epoch:{epoch}"))
    raw = "|".join((project["id"], kind, sha, *ids))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous duplicate JSON key")
        result[key] = value
    return result


def read_json(path, limit, name, *, private_owner_only=False):
    """Read bounded stable JSON; optionally enforce privacy on the OPEN inode.

    Private archival sources must be checked after the no-follow open, not
    just via an earlier pathname stat that can race a replacement file.
    """
    if type(private_owner_only) is not bool:
        raise ValueError(f"{name}: invalid private-source admission")
    file_path = Path(path)
    before = file_path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError(f"{name}: bounded regular file required")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("secure no-follow file opening required")
    fd = os.open(file_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size > limit):
            raise ValueError(f"{name}: source inode changed")
        if private_owner_only and (
                opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) & 0o077
                or opened.st_nlink != 1
                or opened.st_size == 0):
            raise ValueError(f"{name}: opened inode is not owner-private")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        if (len(raw) > limit or opened.st_size != after.st_size
                or opened.st_mtime_ns != after.st_mtime_ns):
            raise ValueError(f"{name}: source changed or exceeded size limit")
    finally:
        os.close(fd)
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_fields,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              ValueError("invalid JSON constant")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name}: invalid UTF-8 JSON") from exc


def acquire_private_ledger_lock(path):
    """Single-writer, fail-fast lock over a whole private zSentinel run.

    Lock the adjacent stable inode, not the atomically replaced JSON inode.
    This is local private state only; it never locks or mutates the zCloud
    SQLite queue, GitHub, workers or a production deployment.
    """
    target = Path(path)
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("private incident ledger needs a real existing parent directory")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("no-follow private ledger lock not supported")
    parent_info = parent.stat()
    if (parent_info.st_uid != os.geteuid()
            or stat.S_IMODE(parent_info.st_mode) & 0o022):
        raise ValueError("private incident ledger directory must be owned and not group/world writable")
    lockfile = str(target) + ".lock"
    fd = os.open(lockfile, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW |
                 getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError("unsafe shared private ledger lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another zSentinel operator is currently updating this ledger") from exc
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def write_json_atomic(path, data):
    """Only optional private local state. Never touch a project-controlled queue."""
    target = Path(path)
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("state directory must already exist and not be a symlink")
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("state must be a regular non-symlink file")
    serialized = (json.dumps(data, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(serialized) > MAX_STATE_BYTES:
        raise ValueError("state ledger exceeds size limit")
    fd, temporary = tempfile.mkstemp(prefix=".zsentinel-", dir=parent)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as output:
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
        if target.is_symlink():
            raise ValueError("state destination changed to symlink")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_state(data):
    if data is None:
        return {}
    if type(data) is not dict or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("state schema_version must be 1")
    incidents = data.get("incidents", {})
    if not isinstance(incidents, dict) or len(incidents) > MAX_LEDGER:
        raise ValueError("state incidents must be a bounded object")
    for key, entry in incidents.items():
        if not re.fullmatch("[0-9a-f]{32}", key) or not isinstance(entry, dict):
            raise ValueError("invalid incident state record")
        first = timestamp(entry.get("first_seen"), "first_seen")
        last = timestamp(entry.get("last_seen"), "last_seen")
        if first > last:
            raise ValueError("incident state first_seen after last_seen")
        if entry.get("status") not in ("human_action_required", "read_only_diagnosis_required", "not_observed_needs_reconciliation", "exact_job_completed_source_reconciled", "exact_job_route_reclassified_not_runner_recovered"):
            raise ValueError("unsupported incident state")
        identity = entry.get("source_identity")
        if identity is not None:
            if (not isinstance(identity, dict)
                    or not isinstance(identity.get("project_id"), str)
                    or not SLUG.fullmatch(identity["project_id"])
                    or not isinstance(identity.get("reason"), str)
                    or not identity["reason"].startswith("github_")
                    or not isinstance(identity.get("head_sha"), str)
                    or not SHA.fullmatch(identity["head_sha"])
                    or type(identity.get("run_id")) is not int
                    or type(identity.get("job_id")) is not int
                    or not 1 <= identity["run_id"] <= 2**53-1
                    or not 1 <= identity["job_id"] <= 2**53-1):
                raise ValueError("invalid exact GitHub incident source identity")
        context = entry.get("source_context")
        if context is not None:
            if (type(context) is not dict
                    or type(context.get("project_id")) is not str
                    or not SLUG.fullmatch(context["project_id"])
                    or type(context.get("reason")) is not str
                    or not re.fullmatch(r"[a-z][a-z0-9_]{1,119}", context["reason"])
                    or context.get("severity") not in ("P0", "P1")
                    or type(context.get("human_escalation")) is not bool):
                raise ValueError("untrusted historical incident source context")
            if identity is not None and (
                    context["project_id"] != identity["project_id"]
                    or context["reason"] != identity["reason"]):
                raise ValueError("historical incident context contradicts exact job identity")
        gates = entry.get("blocked_gates", [])
        if (not isinstance(gates, list) or len(gates) > 2
                or any(gate not in ("github_hosted", "self_hosted") for gate in gates)):
            raise ValueError("unsupported incident gate ledger")
    return incidents


def admit_paths(task):
    paths = task.get("paths")
    if not isinstance(paths, list) or not 1 <= len(paths) <= 25:
        # Unscoped work is not automatically routed to avoid claiming ownership.
        return None
    return tuple(bounded_path(item) for item in paths)


def vps_capacity(bundle, now):
    """Advisory, fail-closed VPS resource admission for suggestions.

    The single source snapshot must be trusted and fresh. This does not
    restart the listener, authorize compute, or create worker capacity.
    """
    data = bundle.get("resources")
    if data is None:
        return {"state": "unavailable", "reason": "no_vps_observation"}
    if not isinstance(data, dict) or data.get("source") != "trusted_readonly_vps_snapshot":
        raise ValueError("resources must be an explicitly trusted read-only VPS snapshot")
    observed = timestamp(data.get("observed_at"), "resources.observed_at")
    if observed > now + timedelta(minutes=2) or now - observed > timedelta(minutes=3):
        return {"state": "stale", "reason": "vps_observation_outdated"}
    metrics = data.get("vps")
    if not isinstance(metrics, dict):
        raise ValueError("resources.vps must be an object")
    cores = metrics.get("cores")
    load = metrics.get("load_5m")
    memory = metrics.get("available_memory_mib")
    swap = metrics.get("available_swap_mib")
    disk = metrics.get("free_disk_gib")
    unavailable_disk_pct = metrics.get("disk_unavailable_percent")
    inode_available = metrics.get("inode_available")
    inode_total = metrics.get("inode_total")
    inode_pct = metrics.get("inode_unavailable_percent")
    inode_source = metrics.get("inode_source")
    if type(cores) is not int or not 1 <= cores <= 128:
        raise ValueError("invalid VPS vCPU capacity")
    for key, value, maximum in (
        ("load_5m", load, 5000),
        ("available_memory_mib", memory, 1048576),
        ("available_swap_mib", swap, 1048576),
        ("free_disk_gib", disk, 100000),
    ):
        if type(value) not in (int, float) or not 0 <= value <= maximum:
            raise ValueError(f"invalid VPS resource: {key}")
    # Older read-only resource receipts did not provide disk occupancy.
    # Missing percent is UNKNOWN, not zero or proof of a spacious mount.
    if unavailable_disk_pct is not None:
        if (type(unavailable_disk_pct) not in (int, float)
                or not math.isfinite(unavailable_disk_pct)
                or not 0 <= unavailable_disk_pct <= 100):
            raise ValueError("invalid VPS disk occupancy")
    # Preserve independently observed overload even when inode telemetry is
    # unknown, but never promote incomplete legacy evidence to headroom.
    inode_unknown = None
    if inode_source == "unsupported_or_unknown_not_zero":
        if any(metrics.get(k) is not None for k in
               ("inode_available", "inode_total", "inode_unavailable_percent")):
            raise ValueError("unsupported inode observation cannot carry counts")
        inode_unknown = "database_mount_inode_capacity_unknown"
    elif all(metrics.get(k) is None for k in
             ("inode_source", "inode_available", "inode_total", "inode_unavailable_percent")):
        inode_unknown = "legacy_vps_inode_capacity_not_observed"
    elif (inode_source != "same_database_mount_statvfs"
          or type(inode_available) is not int or type(inode_total) is not int
          or not 1 <= inode_total <= 10**15
          or not 0 <= inode_available <= inode_total
          or type(inode_pct) not in (int, float)
          or not math.isfinite(inode_pct) or not 0 <= inode_pct <= 100
          or abs(inode_pct - 100 * (1 - inode_available / inode_total)) > 0.015):
        raise ValueError("invalid same-database-mount inode capacity")
    overloaded = load > 1.5 * cores
    low_memory = memory < 1024
    low_swap = swap < 256
    low_disk = disk < 2
    almost_full_disk = unavailable_disk_pct is not None and unavailable_disk_pct >= 90
    inode_pressure = inode_unknown is None and (inode_available == 0 or inode_pct >= 90)
    if overloaded or low_memory or low_swap or low_disk or almost_full_disk or inode_pressure:
        reasons = []
        if overloaded:
            reasons.append("load_above_conservative_capacity")
        if low_memory:
            reasons.append("memory_headroom_low")
        if low_swap:
            reasons.append("swap_headroom_low")
        if low_disk:
            reasons.append("disk_headroom_low")
        if almost_full_disk:
            reasons.append("disk_unavailable_at_least_90_percent")
        if inode_pressure:
            reasons.append("database_mount_inodes_unavailable_at_least_90_percent")
        return {"state": "overloaded", "reason": ",".join(reasons)}
    if inode_unknown is not None:
        return {"state": "unavailable", "reason": inode_unknown}
    if unavailable_disk_pct is None:
        # A legacy snapshot cannot establish safe mount headroom; a human may
        # inspect it but the self-hosted advisory router must fail closed.
        return {"state": "unavailable",
                "reason": "old_snapshot_disk_occupancy_unknown_not_release_authorization"}
    return {"state": "advisory_headroom",
            "reason": "thresholds_met_not_release_authorization"}



def observed_prless_branch_conflicts(bundle, now):
    """Optional canonical branch evidence suppresses overlapping suggestions.

    The existing owner inventory covers only Zennay/zCloud; it never claims
    files as available, restarts a runner, or authorizes dispatch.
    """
    receipt = bundle.get("prless_branch_observation")
    if receipt is None:
        return {"supplied": False, "coverage": "not_supplied",
                "paths": (), "source_branches_for_path": {},
                "suppress_all": False,
                "reason": "optional_source_not_supplied", "observed_branches": 0}
    if (type(receipt) is not dict
            or receipt.get("schema") != "zsentinel-recent-unpr-branches-v1"
            or receipt.get("source") != "canonical_zcloud_recent_prless_ownership_v1"
            or receipt.get("repository") != "Zennay/zCloud"
            or any(receipt.get(key) is not False for key in
                   ("current_slice_safe_to_claim", "branches_deleted",
                    "worker_claims_changed", "workflows_retried"))):
        raise ValueError("invalid PR-less branch ownership observation")
    observed = timestamp(receipt.get("observed_at"), "prless_branch_observed_at")
    coverage = receipt.get("coverage")
    if coverage not in ("bounded_complete", "incomplete_owner_review"):
        raise ValueError("unknown PR-less ownership source coverage")
    rows = receipt.get("recent_candidates")
    count = receipt.get("recent_candidate_count")
    if (type(rows) is not list or len(rows) > 100
            or type(count) is not int or count != len(rows)):
        raise ValueError("unbounded PR-less ownership source")
    paths = set()
    path_sources = {}
    truncated = False
    for row in rows:
        if (type(row) is not dict or row.get("owner_verified") is not False
                or not isinstance(row.get("branch"), str)
                or BRANCH_REF.fullmatch(row["branch"]) is None
                or ".." in row["branch"].split("/")
                or row.get("claim_authorized") is not False
                or type(row.get("paths")) is not list or len(row["paths"]) > 100
                or type(row.get("path_count")) is not int
                or row["path_count"] < len(row["paths"])
                or type(row.get("paths_truncated_for_view")) is not bool
                or row["paths_truncated_for_view"] != (row["path_count"] > 100)):
            raise ValueError("invalid PR-less branch file scope")
        truncated |= row["paths_truncated_for_view"]
        for item in row["paths"]:
            path = bounded_path(item)
            paths.add(path)
            path_sources.setdefault(path, row["branch"])
    stale = observed > now + timedelta(minutes=2) or now - observed > timedelta(minutes=5)
    uncertain = coverage != "bounded_complete" or stale or truncated
    return {
        "supplied": True,
        "coverage": ("stale" if stale else
                     "incomplete" if uncertain else "bounded_complete"),
        "paths": tuple(sorted(paths)),
        "source_branches_for_path": path_sources,
        "suppress_all": uncertain,
        "reason": ("source_stale" if stale else
                   "source_incomplete_or_paths_truncated" if uncertain else
                   "recent_prless_branch_paths_observed"),
        "observed_branches": len(rows),
    }


def prless_branch_conflict(task, receipt):
    """Return a source-specific collision, not a claim or reassignment."""
    if not receipt["supplied"] or task.get("project_id") != "cloud":
        return None
    if receipt["suppress_all"]:
        return {"reason": "prless_branch_source_stale_or_incomplete"}
    task_paths = admit_paths(task)
    if task_paths is None:
        return {"reason": "task_file_scope_unavailable"}
    for task_path in task_paths:
        for branch_path in receipt["paths"]:
            if paths_overlap(task_path, branch_path):
                return {
                    "reason": "overlaps_existing_recent_unpr_branch",
                    "branch_ref": receipt["source_branches_for_path"][branch_path],
                    "conflicting_path": branch_path,
                }
    return None


def eligible_tasks(bundle, blocked, now, capacity, reconciled_dependencies=None):
    tasks = bundle.get("tasks", [])
    claims = bundle.get("active_claims", [])
    # Only exact previously reconciled incident identities can satisfy a
    # declared dependency. Absence from a bounded GitHub sample is UNKNOWN.
    reconciled_dependencies = (frozenset() if reconciled_dependencies is None
                               else frozenset(reconciled_dependencies))
    if any(type(value) is not str or not re.fullmatch(r"[0-9a-f]{32}", value)
           for value in reconciled_dependencies):
        raise ValueError("invalid reconciled task dependency identity")
    coverage_reasons = set()
    complete = (bundle.get("claim_inventory_complete") is True
                and bundle.get("task_inventory_complete") is True)
    if bundle.get("claim_inventory_complete") is not True:
        coverage_reasons.add("claim_inventory_not_complete")
    if bundle.get("task_inventory_complete") is not True:
        coverage_reasons.add("task_inventory_not_complete")
    # A historically complete claim/queue snapshot is NOT proof the same
    # files are still available for writing. Source timestamps are separate
    # from GitHub/Notion issue observation and must be no older than 5 min.
    for clock in ("claim_observed_at", "task_observed_at"):
        value = bundle.get(clock)
        reason_prefix = "claim" if clock == "claim_observed_at" else "task"
        if value is None:
            complete = False
            coverage_reasons.add(reason_prefix + "_observation_missing")
            continue
        stamp = timestamp(value, clock)
        if stamp > now + timedelta(minutes=2) or now - stamp > timedelta(minutes=5):
            complete = False
            coverage_reasons.add(reason_prefix + "_observation_stale_or_future")
    if not isinstance(tasks, list) or len(tasks) > MAX_TASKS:
        raise ValueError("tasks must be a bounded array")
    if not isinstance(claims, list) or len(claims) > MAX_CLAIMS:
        raise ValueError("active_claims must be a bounded array")
    claim_scopes = []
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError("active claim must be an object")
        pid = bounded_slug(claim.get("project_id"), "claim.project_id")
        if claim.get("active") is not True:
            raise ValueError("claim inventory must contain only active, verified claims")
        paths = admit_paths(claim)
        if paths is None:
            complete = False
            coverage_reasons.add("active_claim_scope_unknown")
        else:
            claim_scopes.append((pid, paths))
    try:
        branch_ownership = observed_prless_branch_conflicts(bundle, now)
    except (ValueError, TypeError, KeyError):
        # A malformed optional owner snapshot must not starve unrelated projects.
        # Treat only the canonical cloud project as unavailable for suggestion.
        branch_ownership = {
            "supplied": True, "coverage": "invalid",
            "paths": (), "source_branches_for_path": {},
            "suppress_all": True,
            "reason": "invalid_optional_branch_source",
            "observed_branches": 0,
        }
    recommendations = []
    seen = set()
    branch_suppressed = []
    dependency_holds = []
    gate_holds = []
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("task must be an object")
        ident = bounded_slug(task.get("id"), "task.id")
        pid = bounded_slug(task.get("project_id"), "task.project_id")
        if ident in seen:
            raise ValueError("duplicate task id")
        seen.add(ident)
        tier = task.get("priority")
        if tier not in PRIORITY:
            raise ValueError("task.priority must be P0..P3")
        if task.get("state") not in ("queued", "claimed", "running", "verifying", "blocked", "done"):
            raise ValueError("task.state unsupported")
        deps = task.get("depends_on_incidents")
        if (not isinstance(deps, list) or len(deps) > 30
                or any(type(x) is not str or not re.fullmatch(r"[0-9a-f]{32}", x)
                       for x in deps)
                or len(set(deps)) != len(deps)):
            raise ValueError("invalid, duplicate or noncanonical incident dependencies")
        scopes = admit_paths(task) if task.get("state") == "queued" else None
        if (not complete or task.get("state") != "queued" or task.get("eligible") is not True
                or task.get("human_gate") is not False or task.get("qa_only") is not False
                or task.get("active_owner") is not False or scopes is None):
            continue
        unresolved = [dep for dep in deps if dep not in reconciled_dependencies
                      or dep in blocked["ids"]]
        if unresolved:
            # A lost or unobserved prior incident is not a clearance receipt.
            # Keep bounded counts without exporting private incident IDs.
            dependency_holds.append({
                "id": ident, "project_id": pid, "priority": tier,
                "unresolved_count": len(unresolved),
                "observed_blocked_count": sum(dep in blocked["ids"] for dep in unresolved),
                "reason": "incident_dependency_reconciliation_required",
                "claim_authorized": False,
            })
            continue
        if any(pid == claimed_pid and
               any(paths_overlap(p, c) for p in scopes for c in claimed_paths)
               for claimed_pid, claimed_paths in claim_scopes):
            continue
        branch_conflict = prless_branch_conflict(task, branch_ownership)
        if branch_conflict:
            branch_suppressed.append({
                "id": ident, "project_id": pid, "priority": tier,
                **branch_conflict,
                "next_step": "Inspect the original recent branch and current owner before editing.",
            })
            continue
        # Conservative: a portfolio-level runner outage or hosted billing failure
        # makes tasks requiring those gates non-routable, even across projects.
        requirements = task.get("required_gates")
        if (not isinstance(requirements, list) or not requirements or len(requirements) > 3
                or any(x not in ("github_hosted", "self_hosted", "none") for x in requirements)):
            raise ValueError("invalid task.required_gates")
        if "none" in requirements and len(requirements) != 1:
            raise ValueError("none cannot be combined with other required_gates")
        blocked_required_gates = sorted(set(requirements) & blocked["gates"])
        if blocked_required_gates:
            gate_holds.append({
                "id": ident, "project_id": pid, "priority": tier,
                "reason": "required_gate_blocked",
                "required_gates": sorted(set(requirements)),
                "blocked_required_gates": blocked_required_gates,
                "capacity_state": None,
                "claim_authorized": False, "dispatch_authorized": False,
            })
            continue
        if "self_hosted" in requirements and capacity["state"] != "advisory_headroom":
            gate_holds.append({
                "id": ident, "project_id": pid, "priority": tier,
                "reason": "self_hosted_capacity_not_advisory_headroom",
                "required_gates": sorted(set(requirements)),
                "blocked_required_gates": [],
                "capacity_state": capacity["state"],
                "claim_authorized": False, "dispatch_authorized": False,
            })
            continue
        # Unknown compute destination is not evidence of non-VPS work. Surface
        # the otherwise-eligible task as a human HOLD instead of silently losing it.
        if task.get("requires_vps") is not False and "self_hosted" not in requirements:
            gate_holds.append({
                "id": ident, "project_id": pid, "priority": tier,
                "reason": "compute_destination_not_explicitly_routable",
                "required_gates": sorted(set(requirements)),
                "blocked_required_gates": [],
                "capacity_state": None,
                "claim_authorized": False, "dispatch_authorized": False,
            })
            continue
        recommendations.append({
            "id": ident,
            "project_id": pid,
            "priority": tier,
            "paths": list(scopes),
            "status": "suggested_only_no_claim",
            "action": "Offer to an available owner; recheck live claims immediately before any write."
        })
    # P0 first, then P1 etc.; within a priority class, give every
    # project a turn before accepting a second task from the same project.
    fair_order = []
    for tier in PRIORITY:
        groups = {}
        for candidate in sorted(recommendations, key=lambda item: (
                item["project_id"], item["id"])):
            if candidate["priority"] == tier:
                groups.setdefault(candidate["project_id"], []).append(candidate)
        while any(groups.values()):
            for project_id in sorted(groups):
                if groups[project_id]:
                    fair_order.append(groups[project_id].pop(0))
    # Advisory suggestions must not overlap. Expose invisible, otherwise
    # safe candidates as bounded private owner holds without file paths.
    disjoint = []
    selection_holds = []
    for candidate in fair_order:
        conflict = any(
            candidate["project_id"] == existing["project_id"] and
            any(paths_overlap(path, held) for path in candidate["paths"]
                for held in existing["paths"])
            for existing in disjoint
        )
        if conflict or len(disjoint) == MAX_RECOMMENDATIONS:
            selection_holds.append({
                "id": candidate["id"], "project_id": candidate["project_id"],
                "priority": candidate["priority"],
                "reason": ("overlapping_suggested_scope" if conflict
                           else "shortlist_limit_reached"),
                "claim_authorized": False, "dispatch_authorized": False,
            })
            continue
        disjoint.append(candidate)
    gate_holds.sort(key=lambda item: (
        PRIORITY[item["priority"]], item["project_id"], item["id"], item["reason"]))
    return (disjoint, complete, branch_ownership, branch_suppressed,
            dependency_holds, gate_holds, selection_holds,
            sorted(coverage_reasons))


def triage_issue_inbox(inbox, now):
    """Reuse existing GitHub issue collector receipts without inventing ownership."""
    if inbox is None:
        return {"source": "not_supplied", "coverage": "unknown", "issues": []}
    if not isinstance(inbox, dict) or inbox.get("schema") != "zsentinel-product-issue-inbox-v1":
        raise ValueError("issue inbox schema is not canonical")
    if (inbox.get("actions_authorized") is not False
            or inbox.get("active_claims_verified") is not False
            or inbox.get("queue_ownership_known") is not False):
        raise ValueError("issue inbox cannot authorize work")
    observed = timestamp(inbox.get("observed_at"), "issue_inbox.observed_at")
    if observed > now + timedelta(minutes=2):
        raise ValueError("future issue inbox observation")
    coverage = inbox.get("coverage")
    items = inbox.get("items")
    if (not isinstance(coverage, list) or len(coverage) > 50
            or not isinstance(items, list) or len(items) > 150
            or type(inbox.get("truncated")) is not bool
            or type(inbox.get("stopped_after_rate_or_auth_denial")) is not bool):
        raise ValueError("issue inbox missing bounded source coverage")
    status = "stale" if now - observed > timedelta(hours=6) else "partial"
    observed_projects = set()
    for entry in coverage:
        if not isinstance(entry, dict):
            raise ValueError("invalid issue source coverage")
        pid = bounded_slug(entry.get("project"), "coverage.project")
        if pid in observed_projects or entry.get("status") not in ("partial", "degraded", "unavailable"):
            raise ValueError("duplicate or invalid issue source coverage")
        observed_projects.add(pid)
        if entry["status"] != "partial":
            status = "degraded" if status != "stale" else "stale"
    if inbox["truncated"] or inbox["stopped_after_rate_or_auth_denial"]:
        status = "degraded" if status != "stale" else "stale"
    issue_url = re.compile(r"^https://github[.]com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[1-9][0-9]{0,8}$")
    pr_url = re.compile(r"^https://github[.]com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]{0,8}$")
    candidates = []
    seen = set()
    for entry in items:
        if not isinstance(entry, dict):
            raise ValueError("invalid issue item")
        url, pid = entry.get("url"), bounded_slug(entry.get("project"), "issue.project")
        if (not isinstance(url, str) or not issue_url.fullmatch(url)
                or pid not in observed_projects or url in seen):
            raise ValueError("noncanonical, duplicate or uncovered issue URL")
        seen.add(url)
        if (entry.get("priority") not in ("P0", "P1", "P2", "Unspecified")
                or type(entry.get("qa_only")) is not bool
                or type(entry.get("pinned_exact_issue_read", False)) is not bool
                or entry.get("active_claim") != "not_verified"):
            raise ValueError("issue inbox cannot prove an active claim")
        links = entry.get("related_open_prs")
        if not isinstance(links, list) or len(links) > 3:
            raise ValueError("malformed open PR references")
        repo_prefix = url.rsplit("/issues/", 1)[0] + "/pull/"
        linked = []
        for item in links:
            link = item.get("url") if isinstance(item, dict) else None
            if (not isinstance(link, str) or not pr_url.fullmatch(link)
                    or not link.startswith(repo_prefix)
                    or item.get("active_claim") != "not_verified"):
                raise ValueError("invalid or cross-repo PR link")
            linked.append(link)
        title = entry.get("title")
        if (not isinstance(title, str) or not 1 <= len(title) <= 180
                or any(ord(ch) < 32 for ch in title)):
            raise ValueError("invalid issue title")
        # The recovery operator prioritizes P0/P1 but must not silently drop
        # legitimate lower-priority or unranked product issues. The existing
        # issue collector marks QA-only work explicitly; it stays excluded.
        if entry["qa_only"]:
            continue
        candidates.append({
            "project_id": pid, "priority": entry["priority"], "title": title,
            "issue_url": url, "open_pr_candidates": sorted(set(linked)),
            "pinned_exact_issue_read": entry.get("pinned_exact_issue_read", False),
            "status": "inspection_only_ownership_unverified",
            "action": ("Review existing PR and its current owner before implementing."
                       if linked else "Verify active claims and file scope before proposing work."),
        })
    # Give P0 then P1 precedence, followed by explicitly P2 and finally
    # UNRANKED (never invent severity). Round-robin each tier across sampled
    # repositories so a busy project cannot hide another project's blockers
    # behind the eight-result source budget.
    issue_order = ("P0", "P1", "P2", "Unspecified")
    by_tier_project = {}
    for item in candidates:
        by_tier_project.setdefault((item["priority"], item["project_id"]), []).append(item)
    for values in by_tier_project.values():
        values.sort(key=lambda item: (
            0 if item["pinned_exact_issue_read"] else 1,
            0 if item["open_pr_candidates"] else 1, item["issue_url"]
        ))
    balanced = []
    for tier in issue_order:
        while any(by_tier_project.get((tier, pid)) for pid in observed_projects):
            for pid in sorted(observed_projects):
                remaining = by_tier_project.get((tier, pid))
                if remaining:
                    balanced.append(remaining.pop(0))
                    if len(balanced) >= 8:
                        break
            if len(balanced) >= 8:
                break
        if len(balanced) >= 8:
            break
    return {"source": "github_readonly_issue_inbox", "coverage": status,
            "observed_at": utc_string(observed), "issues": balanced,
            "truncated": inbox["truncated"],
            "stopped_after_rate_or_auth_denial": inbox["stopped_after_rate_or_auth_denial"]}


def queue_pressure_summary(source, now):
    """Read-only SQLite pressure in reports only, never a lease or retry claim."""
    if source is None:
        return {"coverage":"not_supplied", "totals":{}, "samples":[],
                "sample_is_complete":False, "project_groups":[],
                "project_groups_truncated":False, "claim_ownership_verified":False}
    required = {"schema":"zsentinel-queue-pressure-v1",
                "source":"local_sqlite_transaction_query_only",
                "database_read_only":True, "automatic_retry_authorized":False,
                "queue_write_authorized":False, "attempts_are_claims_not_failures":True,
                "claim_ownership_verified":False}
    if not isinstance(source, dict) or any(source.get(k) != v for k,v in required.items()):
        raise ValueError("untrusted or authorizing queue pressure source")
    # Preserve opaque same-database source provenance across saved decisions.
    # A different SQLite inode/host must never be interpreted as one queue.
    queue_source_identity = source.get("source_file_identity_sha256_private")
    if (queue_source_identity is not None and
            (type(queue_source_identity) is not str or
             re.fullmatch(r"[0-9a-f]{64}", queue_source_identity) is None)):
        raise ValueError("invalid opaque queue database source identity")
    observed = timestamp(source.get("observed_at_utc"), "queue_pressure.observed_at_utc")
    if observed > now + timedelta(minutes=2):
        raise ValueError("future queue pressure evidence")
    coverage = ("current" if now - observed <= timedelta(minutes=5) else "stale")
    if source.get("queue_inventory_complete") is not True and coverage == "current":
        coverage = "incomplete"
    totals, samples = source.get("totals"), source.get("high_churn_samples")
    groups = source.get("project_groups")
    if (not isinstance(totals, dict) or not isinstance(samples, list) or len(samples) > 20
            or not isinstance(groups, list) or len(groups) > 2500
            or type(source.get("sample_is_complete")) is not bool):
        raise ValueError("queue pressure report bounds invalid")
    safe_totals = {}
    for name in ("active_items","queued_items","owned_items","high_claim_attempt_items",
                 "ten_plus_claim_attempt_items","expired_owner_lease_rows",
                 "unverifiable_owner_lease_rows","inconsistent_owner_lease_window_rows",
                 "invalid_record_rows","max_claim_attempts"):
        value = totals.get(name)
        if type(value) is not int or not 0 <= value <= 10**9:
            raise ValueError("queue pressure total invalid")
        safe_totals[name] = value
    if (safe_totals["queued_items"] + safe_totals["owned_items"] != safe_totals["active_items"]
            or safe_totals["high_claim_attempt_items"] > safe_totals["active_items"]
            or safe_totals["ten_plus_claim_attempt_items"] > safe_totals["high_claim_attempt_items"]
            or safe_totals["expired_owner_lease_rows"] +
                safe_totals["unverifiable_owner_lease_rows"] +
                safe_totals["inconsistent_owner_lease_window_rows"] >
                safe_totals["owned_items"]):
        raise ValueError("queue pressure totals incoherent")
    safe_groups=[]
    group_identities=set()
    for group in groups:
        if not isinstance(group,dict):
            raise ValueError("invalid queue project group")
        project,priority = group.get("project_id"),group.get("priority")
        if (not isinstance(project,str) or not SLUG.fullmatch(project)
                or priority not in PRIORITY):
            raise ValueError("invalid queue group scope")
        key=(project,priority)
        if key in group_identities:
            raise ValueError("duplicate project/priority queue aggregate")
        group_identities.add(key)
        counts={}
        for name in ("queued","claimed","running","verifying",
                     "high_claim_attempts","ten_plus_claim_attempts",
                     "expired_owner_lease_rows","unverifiable_owner_lease_rows",
                     "inconsistent_owner_lease_window_rows","max_claim_attempts"):
            value=group.get(name)
            if type(value) is not int or not 0<=value<=10**9:
                raise ValueError("invalid queue group count")
            counts[name]=value
        active=sum(counts[state] for state in ("queued","claimed","running","verifying"))
        if (counts["high_claim_attempts"]>active
                or counts["ten_plus_claim_attempts"]>counts["high_claim_attempts"]
                or counts["expired_owner_lease_rows"] +
                 counts["unverifiable_owner_lease_rows"] +
                 counts["inconsistent_owner_lease_window_rows"] >
                 active-counts["queued"]):
            raise ValueError("incoherent queue group count")
        safe_groups.append({"project_id":project, "priority":priority, **counts})
    # Full groups precede the intentional 18-row display limit. Verify
    # their aggregated counts against the original trusted queue totals;
    # otherwise the operator could see a misleading project attribution.
    group_to_total={
        "queued_items": "queued", "high_claim_attempt_items": "high_claim_attempts",
        "ten_plus_claim_attempt_items": "ten_plus_claim_attempts",
        "expired_owner_lease_rows": "expired_owner_lease_rows",
        "unverifiable_owner_lease_rows": "unverifiable_owner_lease_rows",
        "inconsistent_owner_lease_window_rows": "inconsistent_owner_lease_window_rows",
    }
    for total_field, group_field in group_to_total.items():
        if sum(g[group_field] for g in safe_groups) != safe_totals[total_field]:
            raise ValueError("queue project aggregate disagrees with total: "+total_field)
    if (sum(g["claimed"]+g["running"]+g["verifying"] for g in safe_groups)
            != safe_totals["owned_items"]
            or sum(g["queued"]+g["claimed"]+g["running"]+g["verifying"]
                   for g in safe_groups) != safe_totals["active_items"]
            or max((g["max_claim_attempts"] for g in safe_groups),default=0)
               != safe_totals["max_claim_attempts"]):
        raise ValueError("queue inventory totals and project groups disagree")
    safe_samples=[]
    sample_identities=set()
    for row in samples:
        if not isinstance(row,dict):
            raise ValueError("queue sample not an object")
        ref,pid,title = row.get("queue_ref"),row.get("project_id"),row.get("title")
        count = row.get("claim_attempts")
        if (not isinstance(ref,str) or not re.fullmatch("[0-9a-f]{16}",ref)
                or not isinstance(pid,str) or not SLUG.fullmatch(pid)
                or not isinstance(title,str) or len(title)>180 or any(ord(c)<32 for c in title)
                or row.get("priority") not in PRIORITY
                or row.get("status") not in ("queued","claimed","running","verifying")
                or type(count) is not int or not 0<=count<=10**9
                or type(row.get("owner_lease_expired_observed")) is not bool
                or type(row.get("owner_lease_unverifiable_observed")) is not bool
                or type(row.get("owner_lease_window_inconsistent_observed")) is not bool
                or sum((row["owner_lease_expired_observed"],
                        row["owner_lease_unverifiable_observed"],
                        row["owner_lease_window_inconsistent_observed"])) > 1):
            raise ValueError("invalid queue sample")
        if ref in sample_identities:
            raise ValueError("duplicate private queue sample reference")
        sample_identities.add(ref)
        safe_samples.append({
            "queue_ref":ref, "project_id":pid, "title":title,
            "priority":row["priority"], "status":row["status"],
            "claim_attempts":count,
            "owner_lease_expired_observed":row["owner_lease_expired_observed"],
            "owner_lease_unverifiable_observed":row["owner_lease_unverifiable_observed"],
            "owner_lease_window_inconsistent_observed":
                row["owner_lease_window_inconsistent_observed"],
            "action":("Ask authorized owner to verify BOTH lease timestamps; do not reclaim."
                      if row["owner_lease_window_inconsistent_observed"] else
                      "Ask authorized owner to review expired lease; do not remove claim."
                     if row["owner_lease_expired_observed"] else
                     "Lease clock not verifiable; owner must check claim before any action."
                     if row["owner_lease_unverifiable_observed"] else
                     "Inspect claim churn; attempts are NOT failures or retry permits.")
        })
    return {"coverage":coverage,"observed_at":utc_string(observed),
            "source_file_identity_sha256_private": queue_source_identity,
            "totals":safe_totals,"samples":safe_samples if coverage=="current" else [],
            "sample_is_complete":source["sample_is_complete"],
            "project_groups":safe_groups[:18] if coverage=="current" else [],
            "project_groups_truncated":len(safe_groups)>18,
            "claim_ownership_verified":False}


def plan(bundle, previous=None, now=None):
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    if type(bundle) is not dict or type(bundle.get("schema_version")) is not int or bundle["schema_version"] != 1:
        raise ValueError("bundle schema_version must be 1")
    observations = bundle.get("evidence")
    if not isinstance(observations, dict):
        raise ValueError("evidence must be a zSentinel diagnosis input snapshot")
    report = diagnose(observations, now=now)
    by_project = {item["id"]: item for item in observations["projects"]}
    prior = load_state(previous)
    incidents = []
    unattributed = 0
    next_state = {}
    blocking_ids = set()
    blocked_gates = set()
    # GitHub's waiting/pending states must never be diagnosed as runner outages.
    # An explicit, separately verified approval hold is nevertheless a human
    # decision that deserves a distinct operator card, without auto-retrying.
    actionable = list(report["incidents"])
    for entry in observations["projects"]:
        github = entry.get("github", {})
        if (github.get("job_status") in ("waiting", "pending")
                and github.get("verified_approval_wait") is True):
            actionable.append({
                "project_id": entry["id"],
                "reason": "github_required_owner_approval",
                "next_action": "Wait for authorized GitHub owner approval; do not rerun jobs.",
            })
    for incident in actionable:
        project = by_project[incident["project_id"]]
        # waiting/pending represent approval or a scheduler wait, never a missing runner.
        job_status = project.get("github", {}).get("job_status")
        if incident["reason"] == "github_job_unassigned" and job_status != "queued":
            continue
        github = project.get("github", {})
        initial_category = github.get("verified_failure_category")
        key = source_incident_key(project, {
            **incident,
            "reason": ("github_hosted_billing_admission" if initial_category == "billing_admission"
                       else "github_required_owner_approval" if initial_category == "owner_approval"
                       else "github_job_unstarted" if initial_category == "job_unstarted"
                       else incident["reason"])
        })
        if key is None:
            # Missing GitHub run/job or verified runtime claim identity does not
            # license an incident, runner action or historical dedup.
            unattributed += 1
            continue
        prior_entry = prior.get(key, {})
        first = timestamp(prior_entry["first_seen"], "first_seen") if prior_entry else now
        if first > now + timedelta(minutes=2):
            raise ValueError("incident state timestamp is in the future")
        reason = incident["reason"]
        github = project.get("github", {})
        category = github.get("verified_failure_category")
        if category is not None and category not in ("billing_admission", "owner_approval", "ordinary_failure", "job_unstarted"):
            raise ValueError("github.verified_failure_category: unsupported")
        if category and reason != "github_workflow_failed":
            raise ValueError("failure category requires a fresh failed GitHub job")
        if category == "billing_admission":
            if github.get("no_job_steps") is not True:
                raise ValueError("billing admission requires explicit no-job-steps evidence")
            reason = "github_hosted_billing_admission"
            blocked_gates.add("github_hosted")
        elif category == "owner_approval":
            reason = "github_required_owner_approval"
        elif category == "job_unstarted":
            if (github.get("no_job_steps") is not True
                    or github.get("runner_assigned") is not False
                    or github.get("verified_receipt") != "github_exact_run_job"):
                raise ValueError("unstarted GitHub job requires exact no-steps/runner source")
            reason = "github_job_unstarted"
        severity = "P0" if reason in ("github_job_unassigned", "github_hosted_billing_admission") else "P1"
        owner = project.get("notion", {}).get("owner") or project.get("runtime", {}).get("owner")
        if not isinstance(owner, str) or not 1 <= len(owner) <= 80 or any(ord(c) < 32 for c in owner):
            owner = "owner_confirmation_required"
        human = reason in ("github_job_unassigned", "github_hosted_billing_admission",
                           "github_required_owner_approval", "github_job_unstarted")
        if reason == "github_job_unassigned":
            blocked_gates.add("self_hosted")
        if reason == "github_hosted_billing_admission":
            owner = "GitHub account billing owner"
        if reason == "github_required_owner_approval":
            owner = "authorized PR/release owner"
        if reason == "github_job_unstarted":
            owner = "GitHub workflow/account owner"
        action = incident["next_action"]
        if reason == "github_hosted_billing_admission":
            action = "Account owner reviews GitHub billing/spend admission; do not retry paid jobs or change billing."
        elif reason == "github_required_owner_approval":
            action = "Wait for authorized GitHub owner review; no runner restart or PR merge."
        elif reason == "github_job_unstarted":
            action = ("Inspect this exact GitHub job's platform/check-run annotations and billing "
                      "admission with the account owner; no blind retry or VPS restart.")
        blocking_ids.add(key)
        previous_status = prior_entry.get("status", "")
        incidents.append({
            "incident_id": key, "project_id": incident["project_id"],
            "severity": severity, "problem": reason, "owner": owner,
            "first_seen": utc_string(first), "last_seen": utc_string(now),
            "target_review_by": utc_string(first + timedelta(hours=2 if severity == "P0" else 12)),
            "action": action,
            "status": ("human_action_required" if human else "read_only_diagnosis_required"),
            "human_escalation": human,
            "last_status": previous_status if isinstance(previous_status, str) else "",
            # Exact observed job identity for a human operator, not a runner
            # recovery assertion or a credential to act on this job.
            "exact_github_job": ({
                "run_id": github["run_id"], "job_id": github["job_id"],
                "head_sha": github["head_sha"],
            } if (reason.startswith("github_")
                  and github.get("verified_receipt") == "github_exact_run_job"
                  and type(github.get("run_id")) is int
                  and type(github.get("job_id")) is int
                  and 1 <= github["run_id"] <= 2**53 - 1
                  and 1 <= github["job_id"] <= 2**53 - 1
                  and type(github.get("head_sha")) is str
                  and SHA.fullmatch(github["head_sha"])) else None),
        })
        next_state[key] = {
            "first_seen": utc_string(first), "last_seen": utc_string(now),
            "status": "human_action_required" if human else "read_only_diagnosis_required",
            "blocked_gates": (["self_hosted"] if reason == "github_job_unassigned"
                              else ["github_hosted"] if reason == "github_hosted_billing_admission"
                              else []),
            "source_context": {
                "project_id": incident["project_id"],
                "reason": reason,
                "severity": severity,
                "human_escalation": human,
            },
        }
        if (reason.startswith("github_") and github.get("run_id") is not None
                and github.get("job_id") is not None
                and isinstance(github.get("head_sha"), str)
                and SHA.fullmatch(github["head_sha"])):
            next_state[key]["source_identity"] = {
                "project_id": incident["project_id"], "reason": reason,
                "run_id": github["run_id"], "job_id": github["job_id"],
                "head_sha": github["head_sha"],
            }
    # A *specific* previously blocked GitHub job can be marked source-reconciled
    # ONLY when a later canonical live GET proves that SAME run/job/head actually
    # executed and completed successfully. This is not a service or repo recovery.
    reconciled_jobs = []
    reclassified_jobs = []
    for key, entry in prior.items():
        if key in next_state:
            continue
        if entry["status"] in ("exact_job_completed_source_reconciled",
                               "exact_job_route_reclassified_not_runner_recovered"):
            next_state[key] = entry
            continue
        identity = entry.get("source_identity")
        observed = by_project.get(identity["project_id"]) if identity else None
        gh = observed.get("github", {}) if observed else {}
        exact_live = bool(identity and
            gh.get("verified_receipt") == "github_exact_run_job"
            and gh.get("run_id") == identity["run_id"]
            and gh.get("job_id") == identity["job_id"]
            and gh.get("head_sha") == identity["head_sha"]
            and now - timestamp(gh.get("observed_at"), "route_reclassified_observed_at") <= timedelta(minutes=30))
        route_refuted = bool(exact_live
            and identity["reason"] == "github_job_unassigned"
            and (gh.get("job_status") in ("waiting", "pending")
                 or (gh.get("job_status") == "queued"
                     and gh.get("runner_route") == "hosted_or_custom"
                     and gh.get("runner_assigned") is False)))
        if route_refuted:
            # Correct the specific old false-positive runner gate, NOT the
            # listener's health, worker claim or other incidents.
            next_state[key] = {**entry, "last_seen": utc_string(now),
                               "status": "exact_job_route_reclassified_not_runner_recovered",
                               "blocked_gates": []}
            reclassified_jobs.append({
                "incident_id": key, "project_id": identity["project_id"],
                "status": "exact_job_route_refuted_no_runtime_recovery_claim",
            })
            continue
        matches = bool(identity and
            gh.get("verified_receipt") == "github_exact_run_job"
            and gh.get("run_id") == identity["run_id"]
            and gh.get("job_id") == identity["job_id"]
            and gh.get("head_sha") == identity["head_sha"]
            and gh.get("job_status") == "completed"
            and gh.get("conclusion") == "success"
            and gh.get("runner_assigned") is True
            and gh.get("no_job_steps") is False
            and now - timestamp(gh.get("observed_at"), "reconciled_github_observed_at") <= timedelta(minutes=30))
        if matches:
            next_state[key] = {**entry, "last_seen": utc_string(now),
                               "status": "exact_job_completed_source_reconciled",
                               "blocked_gates": []}
            reconciled_jobs.append({"incident_id": key, "project_id": identity["project_id"],
                                    "status": "exact_job_success_no_runtime_recovery_claim"})
        else:
            # Missing from a later partial snapshot is NOT proof of recovery.
            next_state[key] = {**entry, "status": "not_observed_needs_reconciliation"}
    if len(next_state) > MAX_LEDGER:
        # Never silently evict unresolved incidents or their dedup history.
        raise ValueError("incident ledger full; human archival/reconciliation needed")
    # Missing from a subsequent source is not an all-clear. Preserve dependencies
    # and the historical global runner/billing gate until owner reconciliation.
    for key, entry in next_state.items():
        if entry["status"] == "not_observed_needs_reconciliation":
            blocking_ids.add(key)
            blocked_gates.update(entry.get("blocked_gates", ["self_hosted", "github_hosted"]))
    from zsentinel_historical_backlog import unresolved_backlog
    historical = unresolved_backlog(next_state, now)
    blocked = {"ids": blocking_ids, "gates": blocked_gates}
    capacity = vps_capacity(bundle, now)
    exact_reconciled_dependencies = {
        key for key, entry in next_state.items()
        if entry["status"] == "exact_job_completed_source_reconciled"
    }
    (routes, routing_inventory_complete, branches, branch_suppressed,
     dependency_holds, gate_holds, selection_holds,
     routing_coverage_reasons) = eligible_tasks(
        bundle, blocked, now, capacity, exact_reconciled_dependencies)
    issue_triage = triage_issue_inbox(bundle.get("issue_inbox"), now)
    queue_triage = queue_pressure_summary(bundle.get("queue_pressure"), now)
    # Candidate feed for an authorized watch/notifier integrator, NOT a push.
    # Stable ids allow the downstream delivery system to dedupe independently.
    # Preserve strict human-only categorization and never expose raw job logs,
    # prompts, claim tokens, personal data or arbitrary browser event strings.
    watch_candidates = [
        {
            "alert_id": row["incident_id"],
            "project_id": row["project_id"],
            "severity": row["severity"],
            "problem": row["problem"],
            "action": row["action"],
            "status": row["status"],
            "review_by": row["target_review_by"],
            "delivered": False,
        }
        for row in sorted(incidents, key=lambda i: (
            0 if i["severity"] == "P0" else 1,
            i["target_review_by"], i["incident_id"],
        ))
        if row["human_escalation"]
    ][:5]
    # Previously verified human-only incidents must not disappear from the
    # owner's reconciliation backlog just because the latest bounded GitHub/
    # Notion sample omitted them. This is a DIFFERENT, historical-only feed:
    # never pass it to a live alert sender or give it dispatch authority.
    historical_owner_rechecks = []
    for row in historical["shown"]:
        if not row["human_escalation_historical"]:
            continue
        historical_owner_rechecks.append({
            "incident_id": row["incident_id"],
            "project_id": row["project_id"],
            "severity_at_last_observation": row["severity"],
            "problem_at_last_observation": row["problem"],
            "source_last_seen": row["last_seen"],
            "exact_github_job": row["exact_github_job"],
            "source_status": "historical_not_observed_needs_owner_recheck",
            "next_step": ("Authorized owner: verify the same original job/issue "
                          "and current gates before taking any action."),
            "current_source_observed": False,
            "notifier_delivery_authorized": False,
            "recovery_action_authorized": False,
            "owner_confirmation_required": True,
        })
        if len(historical_owner_rechecks) >= 5:
            break
    return {
        "schema_version": 1, "observed_at_utc": utc_string(now),
        "mutations_performed": False, "runner_restarted": False,
        "queue_claims_changed": False, "ci_retried": False,
        "deployment_authorized": False,
        # Legacy field name retained for existing consumers; this is the
        # combined safe-routing gate, not a claim-only source declaration.
        "claim_inventory_complete": routing_inventory_complete,
        "routing_inventory_complete": routing_inventory_complete,
        "routing_inventory_coverage_reasons": routing_coverage_reasons,
        "vps_resource_admission": capacity,
        "incidents": incidents,
        "unattributed_progress_or_jobs": unattributed,
        "reconciled_exact_jobs": reconciled_jobs,
        "reclassified_exact_jobs": reclassified_jobs,
        "watch_alert_candidates": watch_candidates,
        "watch_delivery_performed": False,
        "unresolved_not_observed": historical["total_unresolved"],
        "historical_unresolved": historical,
        "historical_owner_recheck_candidates": historical_owner_rechecks,
        "historical_recheck_source_truncated": (
            historical["truncated"] or
            sum(row["human_escalation_historical"]
                for row in historical["shown"]) > len(historical_owner_rechecks)
        ),
        "independent_task_suggestions": routes,
        "incident_dependency_holds": {
            # A zero count is meaningful only when the task AND claim source
            # inventories are complete and their observation clocks are fresh.
            "coverage": "complete" if routing_inventory_complete else "incomplete_or_stale",
            "coverage_reasons": routing_coverage_reasons,
            "count": len(dependency_holds),
            "examples": dependency_holds[:8],
            "samples_truncated": len(dependency_holds) > 8,
            "exact_reconciliation_required": True,
            "claim_authorized": False,
        },
        "routing_gate_holds": {
            # These are tasks that passed claim/dependency/branch admission but
            # cannot be proposed until their execution gates/destination clear.
            "coverage": "complete" if routing_inventory_complete else "incomplete_or_stale",
            "coverage_reasons": routing_coverage_reasons,
            "count": len(gate_holds),
            "examples": gate_holds[:8],
            "samples_truncated": len(gate_holds) > 8,
            "exact_gate_recheck_required": True,
            "claim_authorized": False,
            "dispatch_authorized": False,
        },
        "routing_selection_holds": {
            # Otherwise-safe tasks not shown due to five-slot limit or an
            # overlapping proposal. A suggestion never acquires a claim.
            "coverage": "complete" if routing_inventory_complete else "incomplete_or_stale",
            "coverage_reasons": routing_coverage_reasons,
            "count": len(selection_holds),
            "examples": selection_holds[:8],
            "samples_truncated": len(selection_holds) > 8,
            "claim_authorized": False,
            "dispatch_authorized": False,
        },
        "recent_branch_ownership": {
            "supplied": branches["supplied"],
            "project_id": "cloud", "coverage": branches["coverage"],
            "reason": branches["reason"],
            "observed_branches": branches["observed_branches"],
            "suppressed_task_count": len(branch_suppressed),
            "suppressed_task_examples": branch_suppressed[:8],
            "samples_truncated": len(branch_suppressed) > 8,
            "ownership_verified": False, "claim_authorized": False,
        },
        "open_product_issue_triage": issue_triage,
        "live_queue_pressure": queue_triage,
    }, {"schema_version": 1, "incidents": next_state}


def operator_text(result):
    lines = ["zSentinel recovery routing — source observations, NOT live authority"]
    ordered = sorted(result["incidents"], key=lambda row: (
        0 if row["severity"] == "P0" else 1,
        row["target_review_by"], row["incident_id"],
    ))
    for item in ordered:
        lines.extend((
            f"{item['severity']} {item['project_id']} · {item['problem']}",
            f"Action ({item['owner']}): {item['action']}",
            f"Status: {item['status']} · review {item['target_review_by']}",
        ))
        exact = item.get("exact_github_job")
        if exact is not None:
            if (type(exact) is not dict or set(exact) != {"run_id", "job_id", "head_sha"}
                    or type(exact["run_id"]) is not int
                    or type(exact["job_id"]) is not int
                    or not 1 <= exact["run_id"] <= 2**53 - 1
                    or not 1 <= exact["job_id"] <= 2**53 - 1
                    or type(exact["head_sha"]) is not str
                    or not SHA.fullmatch(exact["head_sha"])):
                raise ValueError("untrusted exact operator job identity")
            lines.append("Exact GitHub job (read only): run " +
                         str(exact["run_id"]) + " / job " +
                         str(exact["job_id"]) + " / HEAD " + exact["head_sha"])
    if not result["incidents"]:
        lines.append("No actionable fresh incidents; unknown or missing evidence does not mean healthy.")
    lines.append(f"Independent work suggestions (not claimed): {len(result['independent_task_suggestions'])}")
    holds = result.get("incident_dependency_holds")
    if holds is None:
        # Older saved decisions have no dependency coverage receipt. Absence
        # cannot be interpreted as an empty set of dependent tasks.
        holds = {
            "coverage": "incomplete_or_stale", "coverage_reasons": [],
            "count": 0, "examples": [], "samples_truncated": False,
            "exact_reconciliation_required": True, "claim_authorized": False,
        }
    if holds is not None:
        coverage = holds.get("coverage", "incomplete_or_stale") if type(holds) is dict else None
        allowed_coverage_reasons = {
            "claim_inventory_not_complete", "task_inventory_not_complete",
            "claim_observation_missing", "task_observation_missing",
            "claim_observation_stale_or_future", "task_observation_stale_or_future",
            "active_claim_scope_unknown",
        }
        coverage_reasons = (holds.get("coverage_reasons", [])
                            if type(holds) is dict else None)
        if (type(holds) is not dict or coverage not in ("complete", "incomplete_or_stale")
                or type(coverage_reasons) is not list or len(coverage_reasons) > 7
                or any(reason not in allowed_coverage_reasons for reason in coverage_reasons)
                or (coverage == "complete" and coverage_reasons)
                or type(holds.get("count")) is not int
                or not 0 <= holds["count"] <= MAX_TASKS
                or type(holds.get("examples")) is not list
                or len(holds["examples"]) > 8
                or len(holds["examples"]) > holds["count"]
                or holds.get("samples_truncated") is not (
                    holds["count"] > len(holds["examples"]))
                or holds.get("exact_reconciliation_required") is not True
                or holds.get("claim_authorized") is not False):
            raise ValueError("untrusted incident dependency hold report")
        if coverage == "complete":
            lines.append("Otherwise-eligible queued tasks held by unresolved incident dependencies: " +
                         str(holds["count"]) + " (no claim/dispatch)")
        else:
            lines.append("Incident dependencies: UNKNOWN — task/claim inventory incomplete or stale; " +
                         "the observed count is not an all-clear and no dependency is cleared.")
            reason_labels = {
                "claim_inventory_not_complete": "Claiminventaris is niet expliciet compleet.",
                "task_inventory_not_complete": "Taakinventaris is niet expliciet compleet.",
                "claim_observation_missing": "Waarneming van actieve claims ontbreekt.",
                "task_observation_missing": "Waarneming van de taakinventaris ontbreekt.",
                "claim_observation_stale_or_future": "Claimwaarneming is ouder dan vijf minuten of heeft een ongeldige toekomsttijd.",
                "task_observation_stale_or_future": "Taakwaarneming is ouder dan vijf minuten of heeft een ongeldige toekomsttijd.",
                "active_claim_scope_unknown": "Bestandsscope van minstens één actieve claim is onbekend.",
            }
            for reason in coverage_reasons:
                lines.append("- Bronprobleem: " + reason_labels[reason])
        for hold in holds["examples"]:
            lines.append("- HOLD " + hold["priority"] + " " + hold["project_id"] +
                         "/" + hold["id"] + " · " +
                         str(hold["unresolved_count"]) +
                         " dependency identities lack exact-job reconciliation; " +
                         "owner must inspect current source.")
        if holds["samples_truncated"]:
            lines.append("Further incident dependency HOLD examples omitted.")
    gate_report = result.get("routing_gate_holds")
    if gate_report is None:
        gate_report = {
            "coverage": "incomplete_or_stale", "coverage_reasons": [],
            "count": 0, "examples": [], "samples_truncated": False,
            "exact_gate_recheck_required": True,
            "claim_authorized": False, "dispatch_authorized": False,
        }
    gate_examples = gate_report.get("examples") if type(gate_report) is dict else None
    gate_coverage = gate_report.get("coverage") if type(gate_report) is dict else None
    gate_reasons = gate_report.get("coverage_reasons") if type(gate_report) is dict else None
    allowed_gate_reasons = {
        "claim_inventory_not_complete", "task_inventory_not_complete",
        "claim_observation_missing", "task_observation_missing",
        "claim_observation_stale_or_future", "task_observation_stale_or_future",
        "active_claim_scope_unknown",
    }
    if (type(gate_report) is not dict
            or gate_coverage not in ("complete", "incomplete_or_stale")
            or type(gate_reasons) is not list or len(gate_reasons) > 7
            or any(reason not in allowed_gate_reasons for reason in gate_reasons)
            or (gate_coverage == "complete" and gate_reasons)
            or type(gate_report.get("count")) is not int
            or not 0 <= gate_report["count"] <= MAX_TASKS
            or type(gate_examples) is not list or len(gate_examples) > 8
            or len(gate_examples) > gate_report["count"]
            or gate_report.get("samples_truncated") is not (
                gate_report["count"] > len(gate_examples))
            or gate_report.get("exact_gate_recheck_required") is not True
            or gate_report.get("claim_authorized") is not False
            or gate_report.get("dispatch_authorized") is not False):
        raise ValueError("untrusted routing gate-hold report")
    if gate_coverage == "complete":
        lines.append("Otherwise-eligible tasks held by execution gates/capacity: " +
                     str(gate_report["count"]) + " (no claim/dispatch)")
    else:
        lines.append("Execution gate/capacity holds: UNKNOWN — task/claim inventory incomplete or stale; zero is not an all-clear.")
    for item in gate_examples:
        if (type(item) is not dict
                or type(item.get("id")) is not str or not SLUG.fullmatch(item["id"])
                or type(item.get("project_id")) is not str or not SLUG.fullmatch(item["project_id"])
                or item.get("priority") not in PRIORITY
                or item.get("reason") not in (
                    "required_gate_blocked", "self_hosted_capacity_not_advisory_headroom",
                    "compute_destination_not_explicitly_routable")
                or item.get("claim_authorized") is not False
                or item.get("dispatch_authorized") is not False):
            raise ValueError("invalid routing gate-hold example")
        required = item.get("required_gates")
        blocked_gates_for_task = item.get("blocked_required_gates")
        cap_state = item.get("capacity_state")
        if (type(required) is not list or not 1 <= len(required) <= 3
                or any(gate not in ("github_hosted", "self_hosted", "none") for gate in required)
                or len(set(required)) != len(required)
                or ("none" in required and len(required) != 1)
                or type(blocked_gates_for_task) is not list
                or len(blocked_gates_for_task) > 2
                or len(set(blocked_gates_for_task)) != len(blocked_gates_for_task)
                or any(gate not in ("github_hosted", "self_hosted") for gate in blocked_gates_for_task)
                or not set(blocked_gates_for_task).issubset(required)):
            raise ValueError("invalid gate-hold requirements")
        reason = item["reason"]
        if (reason == "required_gate_blocked"
                and (not blocked_gates_for_task or cap_state is not None)
                or reason == "self_hosted_capacity_not_advisory_headroom"
                and ("self_hosted" not in required or blocked_gates_for_task
                     or cap_state not in ("unavailable", "stale", "overloaded"))
                or reason == "compute_destination_not_explicitly_routable"
                and ("self_hosted" in required or blocked_gates_for_task or cap_state is not None)):
            raise ValueError("contradictory gate-hold reason")
        lines.append("- HOLD " + item["priority"] + " " + item["project_id"] + "/" +
                     item["id"] + " · " + reason +
                     (" · blocked gates=" + ",".join(blocked_gates_for_task)
                      if blocked_gates_for_task else "") +
                     (" · VPS capacity=" + cap_state if cap_state else "") +
                     " · owner must recheck exact gate/source; no claim or dispatch")
    if gate_report["samples_truncated"]:
        lines.append("Further execution gate/capacity HOLD examples omitted; source count retained.")
    lines.extend(selection_hold_lines(result.get("routing_selection_holds")))
    lines.append(f"Human-only watch candidates (NOT delivered): {len(result['watch_alert_candidates'])}")
    old_owner = result.get("historical_owner_recheck_candidates", [])
    lines.append("Historical human owner rechecks (NO notification, NO current incident): " +
                 str(len(old_owner)))
    for item in old_owner:
        source = item["exact_github_job"]
        exact = ("run " + str(source["run_id"]) + " / job " +
                 str(source["job_id"]) + " / head " + source["head_sha"]
                 if source else "exact run/job not pinned")
        lines.append("- HISTORICAL " + item["severity_at_last_observation"] + " " +
                     item["project_id"] + " · " +
                     item["problem_at_last_observation"] + " · last " +
                     item["source_last_seen"] + " · " + exact +
                     " · authorized owner must recheck source; not delivered.")
    if result.get("historical_recheck_source_truncated"):
        lines.append("Historical human-recheck shortlist incomplete; no omitted row resolved.")
    historic = result.get("historical_unresolved", {})
    if historic.get("total_unresolved", 0):
        lines.append(f"Historical not-observed incidents (NOT recovered): {historic['total_unresolved']}")
        for item in historic.get("shown", []):
            job = item.get("exact_github_job")
            ref = (f"run {job['run_id']} / job {job['job_id']} / exact head {job['head_sha']}"
                   if job else "exact source unavailable")
            lines.append(
                f"Historical {item['severity']} {item['project_id']} · {item['problem']} · "
                f"last observed {item['last_seen']} · {ref} · VERIFY with authorized owner, never auto-retry."
            )
        if historic.get("truncated"):
            lines.append("Historical backlog truncated to bounded first 30; no hidden row is resolved.")
    if result.get("unattributed_progress_or_jobs"):
        lines.append("Unattributed runtime/jobs (no incident ownership): " +
                     str(result["unattributed_progress_or_jobs"]))
    if result.get("routing_inventory_complete", result["claim_inventory_complete"]) is not True:
        lines.append("Task/claim routing coverage incomplete or stale: suggestions suppressed.")
    branch = result.get("recent_branch_ownership", {})
    if branch.get("supplied"):
        lines.append("Recent zCloud branches without PR: " + branch["coverage"] +
                     " · " + str(branch["suppressed_task_count"]) +
                     " cloud tasks omitted from the independent-work suggestions.")
        for row in branch["suppressed_task_examples"]:
            lines.append("- " + row["priority"] + " " + row["project_id"] +
                         "/" + row["id"] + ": " + row["reason"] +
                         (" · branch " + row["branch_ref"] + " · " + row["conflicting_path"]
                          if "branch_ref" in row else "") +
                         "; owner must review original branch.")
        if branch["samples_truncated"]:
            lines.append("Further suppressed branches/tasks omitted from bounded text view.")
    if result.get("reconciled_exact_jobs"):
        lines.append("Exact GitHub jobs source-reconciled (no runtime proof): " +
                     str(len(result["reconciled_exact_jobs"])))
    if result.get("reclassified_exact_jobs"):
        lines.append("Old incorrect runner incidents reclassified by exact job proof (not recovered): " +
                     str(len(result["reclassified_exact_jobs"])))
    queue_report = result["live_queue_pressure"]
    lines.append("Read-only queue pressure: " + queue_report["coverage"])
    if queue_report["totals"]:
        values = queue_report["totals"]
        lines.append("Queued " + str(values["queued_items"]) + " · Owned " +
                     str(values["owned_items"]) + " · High claim attempts " +
                     str(values["high_claim_attempt_items"]) + " · Expired leases " +
                     str(values["expired_owner_lease_rows"]) + " · Lease clock unknown " +
                      str(values["unverifiable_owner_lease_rows"]))
    lines.append("VPS resource admission: " + result["vps_resource_admission"]["state"] +
                 " / " + result["vps_resource_admission"]["reason"])
    triage = result["open_product_issue_triage"]
    lines.append("Productissue-steekproef P0/P1 → P2 → zonder prioriteit: " + triage["coverage"] + " (inspection only)")
    for issue in triage["issues"]:
        lines.append(f"- {issue['priority']} {issue['project_id']}: {issue['title']} — {issue['issue_url']}")
    for item in result["independent_task_suggestions"]:
        lines.append(f"- {item['priority']} {item['project_id']}/{item['id']}: owner must recheck claims")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="zSentinel bounded incident dedup and independent work router")
    parser.add_argument("bundle", help="trusted JSON evidence + tasks + active-claim inventory")
    parser.add_argument("--state", help="optional private local incident dedup ledger (JSON)")
    parser.add_argument("--prless-branches", type=Path,
                        help="optional saved zCloud un-PR branch owner observation (read-only)")
    parser.add_argument("--format", choices=("json", "text", "html", "ics", "desk"), default="text")
    parser.add_argument("--ics-include-historical-owner-rechecks", action="store_true",
                        help="ICS only: add source-bound historical owner checks without new alarms")
    parser.add_argument("--desk-output", type=Path,
                        help="new, create-only private .html output (required only for --format desk)")
    args = parser.parse_args()
    ledger_lock = None
    try:
        if args.ics_include_historical_owner_rechecks and args.format != "ics":
            raise ValueError("--ics-include-historical-owner-rechecks requires --format ics")
        if args.format == "desk" and args.desk_output is None:
            raise ValueError("--format desk requires --desk-output")
        if args.desk_output is not None and args.format not in ("desk", "html"):
            raise ValueError("--desk-output accepts only desk or html operator formats")
        if args.desk_output is not None:
            # Do not create a private HTML report on top of a pending
            # cursor or dedup ledger: a subsequent atomic JSON write could
            # otherwise clobber the report after its exclusive creation.
            private_inputs = [args.state, args.bundle, args.prless_branches]
            if any(item is not None and args.desk_output.resolve() == Path(item).resolve()
                   for item in private_inputs):
                raise ValueError("desk-output must differ from private ledger/cursor paths")
        if args.state is not None:
            ledger_lock = acquire_private_ledger_lock(args.state)
        bundle = read_json(args.bundle, MAX_INPUT_BYTES, "bundle")
        if args.prless_branches is not None:
            if bundle.get("prless_branch_observation") is not None:
                raise ValueError("select one PR-less ownership source, not two")
            if args.state is not None and Path(args.state).resolve() == args.prless_branches.resolve():
                raise ValueError("private ledger must differ from read-only branch source")
            bundle["prless_branch_observation"] = read_json(
                args.prless_branches, MAX_INPUT_BYTES, "prless branch owner observation")
        old = read_json(args.state, MAX_STATE_BYTES, "state") if args.state and Path(args.state).exists() else None
        output, state = plan(bundle, old)
        output["private_ledger_written"] = bool(args.state)
        if args.format == "html":
            from zsentinel_recovery_view import html_view
            rendered = html_view(output)
        elif args.format == "ics":
            from zsentinel_human_agenda import human_calendar
            rendered = human_calendar(
                output, include_historical=args.ics_include_historical_owner_rechecks)
        elif args.format == "desk":
            from zsentinel_incident_desk import review_html
            rendered = review_html(output)
        else:
            rendered = (json.dumps(output, sort_keys=True, indent=2)
                        if args.format == "json" else operator_text(output))
        if args.desk_output is not None:
            from zsentinel_incident_desk import write_private_html, review_html
            private_desk = rendered if args.format == "desk" else review_html(output)
            write_private_html(args.desk_output, private_desk)
        # Never consume deduplicated incidents before rendering succeeds.
        if args.state:
            write_json_atomic(args.state, state)
        if args.format != "desk":
            print(rendered)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f"zSentinel routing refused: {exc}\n")
    finally:
        if ledger_lock is not None:
            ledger_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
