#!/usr/bin/env python3
"""zSentinel recovery router: durable, fail-closed incident and independent-work decisions.

Processes a trusted, bounded observation bundle. It NEVER acquires claims, dispatches
jobs, retries CI, restarts runners or writes production/Notion/GitHub/SQLite state.
"""
from __future__ import annotations

import argparse
import hashlib
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
        if entry.get("status") not in ("human_action_required", "read_only_diagnosis_required", "not_observed_needs_reconciliation", "exact_job_completed_source_reconciled"):
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


def eligible_tasks(bundle, blocked, now):
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
    recommendations = []
    seen = set()
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
    return disjoint, complete


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
        if entry["priority"] not in ("P0", "P1") or entry["qa_only"]:
            continue
        candidates.append({
            "project_id": pid, "priority": entry["priority"], "title": title,
            "issue_url": url, "open_pr_candidates": sorted(set(linked)),
            "status": "inspection_only_ownership_unverified",
            "action": ("Review existing PR and its current owner before implementing."
                       if linked else "Verify active claims and file scope before proposing work."),
        })
    candidates.sort(key=lambda x: (PRIORITY[x["priority"]], x["project_id"], x["issue_url"]))
    return {"source": "github_readonly_issue_inbox", "coverage": status,
            "observed_at": utc_string(observed), "issues": candidates[:8],
            "truncated": inbox["truncated"],
            "stopped_after_rate_or_auth_denial": inbox["stopped_after_rate_or_auth_denial"]}


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
                       else incident["reason"])
        })
        if key is None:
            # Do not dedupe, retry or act on a GitHub job without a stable job/run.
            continue
        prior_entry = prior.get(key, {})
        first = timestamp(prior_entry["first_seen"], "first_seen") if prior_entry else now
        if first > now + timedelta(minutes=2):
            raise ValueError("incident state timestamp is in the future")
        reason = incident["reason"]
        github = project.get("github", {})
        category = github.get("verified_failure_category")
        if category is not None and category not in ("billing_admission", "owner_approval", "ordinary_failure"):
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
        severity = "P0" if reason in ("github_job_unassigned", "github_hosted_billing_admission") else "P1"
        owner = project.get("notion", {}).get("owner") or project.get("runtime", {}).get("owner")
        if not isinstance(owner, str) or not 1 <= len(owner) <= 80 or any(ord(c) < 32 for c in owner):
            owner = "owner_confirmation_required"
        human = reason in ("github_job_unassigned", "github_hosted_billing_admission",
                           "github_required_owner_approval")
        if reason == "github_job_unassigned":
            blocked_gates.add("self_hosted")
        if reason == "github_hosted_billing_admission":
            owner = "GitHub account billing owner"
        if reason == "github_required_owner_approval":
            owner = "authorized PR/release owner"
        action = incident["next_action"]
        if reason == "github_hosted_billing_admission":
            action = "Account owner reviews GitHub billing/spend admission; do not retry paid jobs or change billing."
        elif reason == "github_required_owner_approval":
            action = "Wait for authorized GitHub owner review; no runner restart or PR merge."
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
    for key, entry in prior.items():
        if key in next_state:
            continue
        if entry["status"] == "exact_job_completed_source_reconciled":
            next_state[key] = entry
            continue
        identity = entry.get("source_identity")
        observed = by_project.get(identity["project_id"]) if identity else None
        gh = observed.get("github", {}) if observed else {}
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
    blocked = {"ids": blocking_ids, "gates": blocked_gates}
    routes, claims_verified = eligible_tasks(bundle, blocked, now)
    issue_triage = triage_issue_inbox(bundle.get("issue_inbox"), now)
    return {
        "schema_version": 1, "observed_at_utc": utc_string(now),
        "mutations_performed": False, "runner_restarted": False,
        "queue_claims_changed": False, "ci_retried": False,
        "deployment_authorized": False,
        "claim_inventory_complete": claims_verified,
        "incidents": incidents,
        "reconciled_exact_jobs": reconciled_jobs,
        "unresolved_not_observed": sum(1 for v in next_state.values()
                                       if v["status"] == "not_observed_needs_reconciliation"),
        "independent_task_suggestions": routes,
        "open_product_issue_triage": issue_triage,
    }, {"schema_version": 1, "incidents": next_state}


def operator_text(result):
    lines = ["zSentinel recovery routing — source observations, NOT live authority"]
    for item in result["incidents"]:
        lines.extend((
            f"{item['severity']} {item['project_id']} · {item['problem']}",
            f"Action ({item['owner']}): {item['action']}",
            f"Status: {item['status']} · review {item['target_review_by']}",
        ))
    if not result["incidents"]:
        lines.append("No actionable fresh incidents; unknown or missing evidence does not mean healthy.")
    lines.append(f"Independent work suggestions (not claimed): {len(result['independent_task_suggestions'])}")
    if not result["claim_inventory_complete"]:
        lines.append("Claim coverage incomplete: routing suppressed.")
    if result.get("reconciled_exact_jobs"):
        lines.append("Exact GitHub jobs source-reconciled (no runtime proof): " +
                     str(len(result["reconciled_exact_jobs"])))
    triage = result["open_product_issue_triage"]
    lines.append("P0/P1 issue inbox: " + triage["coverage"] + " (inspection only)")
    for issue in triage["issues"]:
        lines.append(f"- {issue['priority']} {issue['project_id']}: {issue['title']} — {issue['issue_url']}")
    for item in result["independent_task_suggestions"]:
        lines.append(f"- {item['priority']} {item['project_id']}/{item['id']}: owner must recheck claims")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="zSentinel bounded incident dedup and independent work router")
    parser.add_argument("bundle", help="trusted JSON evidence + tasks + active-claim inventory")
    parser.add_argument("--state", help="optional private local incident dedup ledger (JSON)")
    parser.add_argument("--format", choices=("json", "text", "html"), default="text")
    args = parser.parse_args()
    try:
        bundle = read_json(args.bundle, MAX_INPUT_BYTES, "bundle")
        old = read_json(args.state, MAX_STATE_BYTES, "state") if args.state and Path(args.state).exists() else None
        output, state = plan(bundle, old)
        if args.state:
            write_json_atomic(args.state, state)
        output["private_ledger_written"] = bool(args.state)
        if args.format == "html":
            from zsentinel_recovery_view import html_view
            print(html_view(output))
        else:
            print(json.dumps(output, sort_keys=True, indent=2) if args.format == "json" else operator_text(output))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f"zSentinel routing refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
