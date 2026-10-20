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


def read_json(path, limit, name):
    """Read a bounded stable regular inode; reject symlinks and JSON ambiguity."""
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
    overloaded = load > 1.5 * cores
    low_memory = memory < 1024
    low_swap = swap < 256
    low_disk = disk < 2
    almost_full_disk = unavailable_disk_pct is not None and unavailable_disk_pct >= 90
    if overloaded or low_memory or low_swap or low_disk or almost_full_disk:
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
        return {"state": "overloaded", "reason": ",".join(reasons)}
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
                "paths": (), "suppress_all": False,
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
    truncated = False
    for row in rows:
        if (type(row) is not dict or row.get("owner_verified") is not False
                or row.get("claim_authorized") is not False
                or type(row.get("paths")) is not list or len(row["paths"]) > 100
                or type(row.get("path_count")) is not int
                or row["path_count"] < len(row["paths"])
                or type(row.get("paths_truncated_for_view")) is not bool
                or row["paths_truncated_for_view"] != (row["path_count"] > 100)):
            raise ValueError("invalid PR-less branch file scope")
        truncated |= row["paths_truncated_for_view"]
        for item in row["paths"]:
            paths.add(bounded_path(item))
    stale = observed > now + timedelta(minutes=2) or now - observed > timedelta(minutes=5)
    uncertain = coverage != "bounded_complete" or stale or truncated
    return {
        "supplied": True,
        "coverage": ("stale" if stale else
                     "incomplete" if uncertain else "bounded_complete"),
        "paths": tuple(sorted(paths)),
        "suppress_all": uncertain,
        "reason": ("source_stale" if stale else
                   "source_incomplete_or_paths_truncated" if uncertain else
                   "recent_prless_branch_paths_observed"),
        "observed_branches": len(rows),
    }


def prless_branch_conflict(task, receipt):
    if not receipt["supplied"] or task.get("project_id") != "cloud":
        return False
    if receipt["suppress_all"]:
        return True
    task_paths = admit_paths(task)
    return task_paths is None or any(
        paths_overlap(task_path, branch_path)
        for task_path in task_paths for branch_path in receipt["paths"]
    )


def eligible_tasks(bundle, blocked, now, capacity):
    tasks = bundle.get("tasks", [])
    claims = bundle.get("active_claims", [])
    complete = (bundle.get("claim_inventory_complete") is True
                and bundle.get("task_inventory_complete") is True)
    # A historically complete claim/queue snapshot is NOT proof the same
    # files are still available for writing. Source timestamps are separate
    # from GitHub/Notion issue observation and must be no older than 5 min.
    for clock in ("claim_observed_at", "task_observed_at"):
        value = bundle.get(clock)
        if value is None:
            complete = False
            continue
        stamp = timestamp(value, clock)
        if stamp > now + timedelta(minutes=2) or now - stamp > timedelta(minutes=5):
            complete = False
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
        else:
            claim_scopes.append((pid, paths))
    try:
        branch_ownership = observed_prless_branch_conflicts(bundle, now)
    except (ValueError, TypeError, KeyError):
        # A malformed optional owner snapshot must not starve unrelated projects.
        # Treat only the canonical cloud project as unavailable for suggestion.
        branch_ownership = {
            "supplied": True, "coverage": "invalid",
            "paths": (), "suppress_all": True,
            "reason": "invalid_optional_branch_source",
            "observed_branches": 0,
        }
    recommendations = []
    seen = set()
    branch_suppressed = []
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
        if not isinstance(deps, list) or len(deps) > 30 or any(not isinstance(x, str) for x in deps):
            raise ValueError("invalid incident dependencies")
        scopes = admit_paths(task) if task.get("state") == "queued" else None
        if (not complete or task.get("state") != "queued" or task.get("eligible") is not True
                or task.get("human_gate") is not False or task.get("qa_only") is not False
                or task.get("active_owner") is not False or scopes is None
                or any(dep in blocked["ids"] for dep in deps)):
            continue
        if any(pid == claimed_pid and
               any(paths_overlap(p, c) for p in scopes for c in claimed_paths)
               for claimed_pid, claimed_paths in claim_scopes):
            continue
        if prless_branch_conflict(task, branch_ownership):
            branch_suppressed.append({
                "id": ident, "project_id": pid, "priority": tier,
                "reason": ("prless_branch_source_stale_or_incomplete"
                           if branch_ownership["suppress_all"]
                           else "overlaps_existing_recent_unpr_branch"),
                "next_step": "Inspect existing branch and its current owner before any new work.",
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
        if set(requirements) & blocked["gates"]:
            continue
        if "self_hosted" in requirements and capacity["state"] != "advisory_headroom":
            continue
        # Unknown compute destination is not evidence of non-VPS work.
        if task.get("requires_vps") is not False and "self_hosted" not in requirements:
            continue
        recommendations.append({
            "id": ident,
            "project_id": pid,
            "priority": tier,
            "paths": list(scopes),
            "status": "suggested_only_no_claim",
            "action": "Offer to an available owner; recheck live claims immediately before any write."
        })
    recommendations.sort(key=lambda item: (PRIORITY[item["priority"]], item["project_id"], item["id"]))
    # Do not offer two mutually overlapping next tasks just because neither
    # collides with a CURRENT active claim. Higher-priority work owns the
    # proposal slot until an authorized controller chooses and rechecks it.
    disjoint = []
    for candidate in recommendations:
        if any(candidate["project_id"] == existing["project_id"] and
               any(paths_overlap(path, held) for path in candidate["paths"]
                   for held in existing["paths"])
               for existing in disjoint):
            continue
        disjoint.append(candidate)
        if len(disjoint) == MAX_RECOMMENDATIONS:
            break
    return disjoint, complete, branch_ownership, branch_suppressed


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
                 "unverifiable_owner_lease_rows","invalid_record_rows","max_claim_attempts"):
        value = totals.get(name)
        if type(value) is not int or not 0 <= value <= 10**9:
            raise ValueError("queue pressure total invalid")
        safe_totals[name] = value
    if (safe_totals["queued_items"] + safe_totals["owned_items"] != safe_totals["active_items"]
            or safe_totals["high_claim_attempt_items"] > safe_totals["active_items"]
            or safe_totals["ten_plus_claim_attempt_items"] > safe_totals["high_claim_attempt_items"]
            or safe_totals["expired_owner_lease_rows"] +
                safe_totals["unverifiable_owner_lease_rows"] > safe_totals["owned_items"]):
        raise ValueError("queue pressure totals incoherent")
    safe_groups=[]
    for group in groups:
        if not isinstance(group,dict):
            raise ValueError("invalid queue project group")
        project,priority = group.get("project_id"),group.get("priority")
        if (not isinstance(project,str) or not SLUG.fullmatch(project)
                or priority not in PRIORITY):
            raise ValueError("invalid queue group scope")
        counts={}
        for name in ("queued","claimed","running","verifying",
                     "high_claim_attempts","ten_plus_claim_attempts",
                     "expired_owner_lease_rows","unverifiable_owner_lease_rows","max_claim_attempts"):
            value=group.get(name)
            if type(value) is not int or not 0<=value<=10**9:
                raise ValueError("invalid queue group count")
            counts[name]=value
        active=sum(counts[state] for state in ("queued","claimed","running","verifying"))
        if (counts["high_claim_attempts"]>active
                or counts["ten_plus_claim_attempts"]>counts["high_claim_attempts"]
                or counts["expired_owner_lease_rows"] +
                 counts["unverifiable_owner_lease_rows"]>active-counts["queued"]):
            raise ValueError("incoherent queue group count")
        safe_groups.append({"project_id":project, "priority":priority, **counts})
    safe_samples=[]
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
                or (row["owner_lease_expired_observed"] and
                    row["owner_lease_unverifiable_observed"])):
            raise ValueError("invalid queue sample")
        safe_samples.append({
            "queue_ref":ref, "project_id":pid, "title":title,
            "priority":row["priority"], "status":row["status"],
            "claim_attempts":count,
            "owner_lease_expired_observed":row["owner_lease_expired_observed"],
            "owner_lease_unverifiable_observed":row["owner_lease_unverifiable_observed"],
            "action":("Ask authorized owner to review expired lease; do not remove claim."
                     if row["owner_lease_expired_observed"] else
                     "Lease clock not verifiable; owner must check claim before any action."
                     if row["owner_lease_unverifiable_observed"] else
                     "Inspect claim churn; attempts are NOT failures or retry permits.")
        })
    return {"coverage":coverage,"observed_at":utc_string(observed),
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
    routes, claims_verified, branches, branch_suppressed = eligible_tasks(
        bundle, blocked, now, capacity)
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
    return {
        "schema_version": 1, "observed_at_utc": utc_string(now),
        "mutations_performed": False, "runner_restarted": False,
        "queue_claims_changed": False, "ci_retried": False,
        "deployment_authorized": False,
        "claim_inventory_complete": claims_verified,
        "vps_resource_admission": capacity,
        "incidents": incidents,
        "unattributed_progress_or_jobs": unattributed,
        "reconciled_exact_jobs": reconciled_jobs,
        "reclassified_exact_jobs": reclassified_jobs,
        "watch_alert_candidates": watch_candidates,
        "watch_delivery_performed": False,
        "unresolved_not_observed": historical["total_unresolved"],
        "historical_unresolved": historical,
        "independent_task_suggestions": routes,
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
    if not result["incidents"]:
        lines.append("No actionable fresh incidents; unknown or missing evidence does not mean healthy.")
    lines.append(f"Independent work suggestions (not claimed): {len(result['independent_task_suggestions'])}")
    lines.append(f"Human-only watch candidates (NOT delivered): {len(result['watch_alert_candidates'])}")
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
    if not result["claim_inventory_complete"]:
        lines.append("Claim coverage incomplete: routing suppressed.")
    branch = result.get("recent_branch_ownership", {})
    if branch.get("supplied"):
        lines.append("Recent zCloud branches without PR: " + branch["coverage"] +
                     " · " + str(branch["suppressed_task_count"]) +
                     " cloud tasks omitted from the independent-work suggestions.")
        for row in branch["suppressed_task_examples"]:
            lines.append("- " + row["priority"] + " " + row["project_id"] +
                         "/" + row["id"] + ": " + row["reason"] +
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
    parser.add_argument("--desk-output", type=Path,
                        help="new, create-only private .html output (required only for --format desk)")
    args = parser.parse_args()
    ledger_lock = None
    try:
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
            rendered = human_calendar(output)
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
