#!/usr/bin/env python3
"""One-shot private owner handoff from an already assembled zSentinel decision.

No provider reads or control-plane writes. Handoff items are suggestions only:
even an apparent free slot, expired lease or green CI is NOT authorization.
"""
from __future__ import annotations

import json
import re
from html import escape
from datetime import datetime, timezone

SAFE_SHA = re.compile(r"^[a-f0-9]{40}$")
SAFE_REPOSITORY = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")
SCHEMA = "zsentinel-runtime-owner-handoff-v1"
MAX_ITEMS = 24
MAX_TEXT = 220
SAFE_PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
SAFE_ISSUE = re.compile(r"^https://github\.com/Zennay/[A-Za-z0-9_.-]+/issues/[1-9][0-9]{0,8}$")
SAFE_PR = re.compile(r"^https://github\.com/Zennay/[A-Za-z0-9_.-]+/pull/[1-9][0-9]{0,8}$")


def _short(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        return "ONBEKEND"
    return " ".join(value.split())[:limit] or "ONBEKEND"


def _project(value):
    if not isinstance(value, str) or not SAFE_PROJECT.fullmatch(value):
        raise ValueError("invalid handoff project identity")
    return value


def _card(priority, project, kind, action, *, source="observed_partial", ref=None):
    if priority not in ("P0", "P1", "P2") or not isinstance(kind, str):
        raise ValueError("invalid handoff priority or kind")
    card = {
        "priority": priority,
        "project": _project(project),
        "kind": kind,
        "next_step": _short(action),
        "evidence": source,
        "claim_authorized": False,
        "retry_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "owner_verification_required": True,
    }
    if ref is not None and isinstance(ref, str) and SAFE_ISSUE.fullmatch(ref):
        card["issue_url"] = ref
    return card


def _attach_verified_job(card, raw, registry, review_by):
    """Human navigation to one observed job; never authorizes a CI/release action."""
    if review_by is not None:
        if type(review_by) is not str or len(review_by) > 40:
            raise ValueError("invalid incident review deadline")
        try:
            deadline = datetime.fromisoformat(review_by.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid incident review deadline") from exc
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise ValueError("naive incident review deadline")
        due_utc = deadline.astimezone(timezone.utc)
        card["review_by_utc"] = due_utc.isoformat(timespec="seconds")
        card["review_overdue_at_export"] = due_utc < datetime.now(timezone.utc)
    if raw is None:
        return card
    if (type(raw) is not dict
            or set(raw) != {"run_id", "job_id", "head_sha"}
            or type(raw["run_id"]) is not int
            or type(raw["job_id"]) is not int
            or not 1 <= raw["run_id"] <= 2**53 - 1
            or not 1 <= raw["job_id"] <= 2**53 - 1
            or type(raw["head_sha"]) is not str
            or not SAFE_SHA.fullmatch(raw["head_sha"])):
        raise ValueError("invalid exact GitHub incident job")
    card["exact_github_job"] = dict(raw)
    if registry is not None:
        repo = registry.get(card["project"]) if type(registry) is dict else None
        if type(repo) is not str or not SAFE_REPOSITORY.fullmatch(repo):
            raise ValueError("no canonical repository for verified job")
        base = "https://github.com/" + repo
        card["exact_github_job_url"] = (base + "/actions/runs/" +
            str(raw["run_id"]) + "/job/" + str(raw["job_id"]))
        card["exact_github_commit_url"] = base + "/commit/" + raw["head_sha"]
    return card



def _observed_pr_owner_cards(pr_desk, registry):
    """Reuse an already-read exact-head PR desk. Never infer merge authority."""
    if pr_desk is None:
        return [], "not_requested"
    if (type(pr_desk) is not dict
            or pr_desk.get("schema") != "zsentinel-pr-delivery-desk-v1"
            or pr_desk.get("source") != "github_canonical_readonly_bounded_pr_gets"
            or any(pr_desk.get(flag) is not False for flag in
                   ("merge_authorized", "deploy_authorized",
                    "owner_verified", "writes_performed"))):
        raise ValueError("untrusted existing PR delivery source")
    rows = pr_desk.get("items")
    if type(rows) is not list or len(rows) > 3:
        raise ValueError("unbounded existing PR source")
    from zsentinel_pr_delivery import NEXT_ACTION_NL
    cards = []
    seen = set()
    remediate = {"finish_existing_draft_with_owner",
                 "resolve_exact_head_review_with_owner",
                 "fix_exact_head_ci_with_owner",
                 "inspect_unlisted_status_contexts_with_owner",
                 "resolve_hosted_billing_with_account_owner",
                 "request_independent_review",
                 "confirm_branch_protection_and_release_with_owner"}
    for row in rows:
        if (type(row) is not dict
                or row.get("source") != "github_exact_pr_readonly"
                or any(row.get(flag) is not False for flag in
                       ("action_authorized", "merge_authorized",
                        "deploy_authorized", "owner_verified"))):
            raise ValueError("untrusted PR owner action")
        project = _project(row.get("project_id"))
        repo = registry.get(project) if type(registry) is dict else None
        number = row.get("pr_number")
        if (type(repo) is not str or not SAFE_REPOSITORY.fullmatch(repo)
                or type(number) is not int or not 1 <= number <= 100000000
                or (project, number) in seen):
            raise ValueError("unknown or duplicate existing PR owner action")
        seen.add((project, number))
        url = "https://github.com/" + repo + "/pull/" + str(number)
        if row.get("repository") != repo or row.get("url") != url:
            raise ValueError("cross-repository PR source mismatch")
        action = row.get("next_step")
        if action not in NEXT_ACTION_NL:
            raise ValueError("unknown PR owner action")
        coverage = row.get("coverage")
        if coverage not in ("bounded_exact_head_review_and_checks",
                            "source_incomplete_or_changed",
                            "not_queried_after_auth_or_rate_limit"):
            raise ValueError("unverified PR source coverage")
        card = _card(
            "P1" if action in remediate else "P2", project,
            "open_pr_owner_review",
            "Bestaande PR #" + str(number) + ": " + NEXT_ACTION_NL[action],
            source="bounded_canonical_readonly_pr_observation")
        card["existing_pr_urls"] = [url]
        card["pr_observation_coverage"] = coverage
        card["pr_gate_next_step"] = action
        card["exact_head_required_ci_verified"] = False
        card["independent_review_policy_verified"] = False
        if coverage == "bounded_exact_head_review_and_checks":
            head = row.get("head_sha")
            ci, reviews = row.get("ci"), row.get("reviews")
            if (type(head) is not str or not SAFE_SHA.fullmatch(head)
                    or type(ci) is not dict or ci.get("head_sha") != head
                    or type(reviews) is not dict
                    or reviews.get("head_sha") != head):
                raise ValueError("PR owner card needs consistent exact HEAD")
            counted = {name: ci.get(name) for name in
                       ("failed", "pending", "inconclusive")}
            sampled, total = ci.get("sampled"), ci.get("total_reported")
            approvals = reviews.get("same_head_approvals_seen")
            if (type(sampled) is not int or not 0 <= sampled <= 30
                    or type(total) is not int or not sampled <= total <= 1000000
                    or type(approvals) is not int or not 0 <= approvals <= 30
                    or type(ci.get("all_checks_observed")) is not bool
                    or type(reviews.get("review_page_full")) is not bool
                    or any(type(group) is not list or len(group) > 8
                           or any(type(name) is not str or len(name) > 100
                                  for name in group)
                           for group in counted.values())):
                raise ValueError("untrusted PR exact-head gate sample")
            card["pr_observed_head_sha"] = head
            card["pr_observed_gate_counts"] = {
                "check_runs_sampled": sampled,
                "check_runs_reported": total,
                "checks_failed_seen_min": len(counted["failed"]),
                "checks_pending_seen_min": len(counted["pending"]),
                "checks_inconclusive_seen_min": len(counted["inconclusive"]),
                "same_head_independent_approvals_seen": approvals,
                "review_window_truncated": reviews["review_page_full"],
                "check_run_window_complete": ci["all_checks_observed"],
                "required_checks_and_reviews_verified": False,
            }
        cards.append(card)
    return cards, "bounded_%d_open_prs_other_prs_unknown" % len(cards)



def _notion_owner_status_summary(observed, source_mode, registry):
    """Handoff positive incomplete work statuses, never claim/lease authority.

    This deliberately does not inspect Notion rows, send notifications, choose
    a worker, or modify the primary P0-first priority action queue.
    """
    blank = {
        "coverage": "not_requested_or_unavailable",
        "observed_at_utc": None,
        "source_age_seconds": None,
        "status_windows": [],
        "projects": [],
        "unregistered_work_status_min": None,
        "items_truncated": False,
        "owner_verified": False,
        "live_lease_verified": False,
        "can_dispatch": False,
        "can_release_claim": False,
    }
    if observed is None:
        # Do not add a phantom Notion task panel to the default operator
        # handoff when this optional source was never requested. A genuinely
        # failed requested source stays explicitly UNKNOWN.
        return (blank if source_mode == "degraded_unavailable_no_claim_authority"
                else None)
    mode = {
        "claim_status_observed_no_file_ownership":
            ("notion_readonly_data_source_pages", ("current_pages_only", "incomplete")),
        "private_connector_snapshot_unverified_origin":
            ("user_supplied_notion_connector_export_unverified_origin",
             ("saved_single_page_not_live_current_claims_unknown",)),
    }.get(source_mode)
    if (mode is None or type(observed) is not dict
            or observed.get("source") != mode[0]
            or observed.get("coverage") not in mode[1]
            or any(observed.get(key) is not False for key in
                   ("claim_scope_verified", "task_inventory_verified",
                    "can_dispatch", "can_release_claim"))
            or (registry is not None and type(registry) is not dict)):
        raise ValueError("untrusted Notion handoff work-status source")
    if mode[0] == "user_supplied_notion_connector_export_unverified_origin" and (
            observed.get("origin_authenticity_verified") is not False
            or observed.get("live_claim_ownership_verified") is not False):
        raise ValueError("private snapshot cannot grant live owner status")
    stamp = observed.get("observed_at")
    if type(stamp) is not str or len(stamp) > 45:
        raise ValueError("invalid handoff Notion source timestamp")
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid handoff Notion observation date") from exc
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("Notion timestamp must have timezone")
    age = int((datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds())
    if age < -120:
        raise ValueError("Notion timestamp is in future")
    counts = observed.get("active_project_counts")
    verifying = observed.get("verifying_project_counts")
    count = observed.get("active_count")
    verifying_total = observed.get("verifying_count")
    if (type(counts) is not dict or len(counts) > 50
            or type(verifying) is not dict or len(verifying) > 50
            or type(count) is not int or not 0 <= count <= 400
            or type(verifying_total) is not int
            or not 0 <= verifying_total <= count
            or any(type(k) is not str or len(k) > 100
                   or type(v) is not int or not 0 < v <= 400
                   for k, v in counts.items())
            or any(type(k) is not str or type(v) is not int
                   or not 0 < v <= counts.get(k, -1)
                   for k, v in verifying.items())
            or sum(counts.values()) != count
            or sum(verifying.values()) != verifying_total):
        raise ValueError("inconsistent Notion owner status aggregates")
    windows = observed.get("status_source_windows")
    status_windows = []
    if windows is not None:
        expected = (("Claimed", 1), ("Running", 1), ("Verifying", 2))
        if (mode[0] != "notion_readonly_data_source_pages"
                or type(windows) is not list or len(windows) != len(expected)):
            raise ValueError("private Notion handoff cannot carry live source receipts")
        pages, total_rows, partial = 0, 0, False
        for row, (status, budget) in zip(windows, expected):
            if (type(row) is not dict or row.get("status") != status
                    or row.get("page_budget") != budget
                    or type(row.get("pages_read")) is not int
                    or not 1 <= row["pages_read"] <= budget
                    or type(row.get("observed_rows")) is not int
                    or not 0 <= row["observed_rows"] <= 100 * row["pages_read"]
                    or row.get("coverage") not in
                    ("complete_within_source", "partial")
                    or row.get("read_only") is not True
                    or row.get("claim_scope_verified") is not False):
                raise ValueError("invalid Notion handoff status window")
            pages += row["pages_read"]
            total_rows += row["observed_rows"]
            partial |= row["coverage"] == "partial"
            status_windows.append({
                "status": status, "page_budget": budget,
                "pages_read": row["pages_read"],
                "observed_rows": row["observed_rows"],
                "coverage": row["coverage"], "owner_verified": False,
            })
        if (type(observed.get("total_pages_read")) is not int
                or observed["total_pages_read"] != pages or pages > 4
                or total_rows != count
                or (observed["coverage"] == "incomplete") != partial):
            raise ValueError("inconsistent Notion handoff source totals")
    known = []
    unknown_total = 0
    for pid, total in counts.items():
        if registry is None or pid not in registry:
            unknown_total += total
            continue
        _project(pid)
        review = verifying.get(pid, 0)
        known.append({
            "project": pid, "observed_unfinished_min": total,
            "observed_verifying_min": review,
            "next_step": ("Controleer bestaande PR en verificatie-eigenaar vóór nieuw werk."
                          if review else
                          "Controleer actieve taakclaim, scope en bestaande PR."),
            "live_owner_verified": False,
            "review_required": True, "can_claim": False,
        })
    known.sort(key=lambda x: (-bool(x["observed_verifying_min"]),
                               -x["observed_verifying_min"],
                               -x["observed_unfinished_min"], x["project"]))
    return {
        **blank,
        "coverage": ("saved_snapshot_origin_unverified"
                     if source_mode == "private_connector_snapshot_unverified_origin"
                     else ("live_readonly_incomplete"
                           if observed["coverage"] == "incomplete"
                           else "live_readonly_bounded")),
        "observed_at_utc": when.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "source_age_seconds": max(0, age),
        "status_windows": status_windows,
        "projects": known[:12],
        "unregistered_work_status_min": unknown_total,
        "items_truncated": len(known) > 12,
    }

def build_handoff(decision, registry=None, queue_trend=None, pr_desk=None):
    """Prioritize exact incidents, owner/lease conflicts, and open product work.

    The handoff does not infer absence of work from a missing/partial source.
    Limited source samples are not promoted into a complete project state.
    """
    if not isinstance(decision, dict):
        raise ValueError("missing source-bound recovery decision")
    sources = decision.get("live_sources")
    incidents = decision.get("incidents")
    if not isinstance(sources, dict) or not isinstance(incidents, list) or len(incidents) > 200:
        raise ValueError("handoff requires assembled live source provenance")
    cards = []
    notion_owner_status = _notion_owner_status_summary(
        decision.get("notion_claims_observation"), sources.get("notion"), registry)
    pr_cards, pr_coverage = _observed_pr_owner_cards(pr_desk, registry)
    for item in incidents:
        if not isinstance(item, dict) or item.get("severity") not in ("P0", "P1", "P2"):
            raise ValueError("malformed fresh incident")
        cards.append(_attach_verified_job(
            _card(item["severity"], item.get("project_id"),
                  "fresh_incident", item.get("action"),
                  source="bounded_fresh_incident"),
            item.get("exact_github_job"), registry, item.get("target_review_by")))
    # The router already verified this bounded VPS read-only source and its
    # freshness. An overloaded host must become an *owner* action in the
    # unified handoff, not silently disappear behind a task backlog.
    capacity = decision.get("vps_resource_admission")
    capacity_coverage = "unavailable"
    if capacity is not None:
        if (type(capacity) is not dict
                or capacity.get("state") not in {
                    "unavailable", "stale", "overloaded", "advisory_headroom"}
                or type(capacity.get("reason")) is not str
                or not re.fullmatch(r"[a-z0-9_,]{1,180}", capacity["reason"])):
            raise ValueError("invalid source-bound VPS capacity decision")
        capacity_coverage = capacity["state"]
        if capacity_coverage == "overloaded":
            allowed = {
                "load_above_conservative_capacity", "memory_headroom_low",
                "swap_headroom_low", "disk_headroom_low",
                "disk_unavailable_at_least_90_percent",
            }
            reasons = capacity["reason"].split(",")
            if not reasons or len(reasons) != len(set(reasons)) or any(
                    reason not in allowed for reason in reasons):
                raise ValueError("untrusted VPS overload reason")
            # Rank disk and swap before load: the bounded operator label
            # has room for two reasons, but the JSON card retains all of them.
            reason_order = (
                "disk_unavailable_at_least_90_percent", "disk_headroom_low",
                "swap_headroom_low", "memory_headroom_low",
                "load_above_conservative_capacity",
            )
            reasons.sort(key=reason_order.index)
            card = _card(
                "P1", "cloud", "vps_capacity_owner_review",
                "VPS capaciteitsdruk (" + ", ".join(reasons[:2]) +
                (" + meer" if len(reasons) > 2 else "") +
                "). Hosteigenaar beoordeelt vrije ruimte en actieve jobs. "
                "GEEN cleanup, service-restart of workerstop.",
                source="validated_recent_readonly_vps_capacity",
                ref="https://github.com/Zennay/zCloud/issues/1326")
            card["observed_capacity_state"] = "overloaded"
            card["capacity_reasons"] = reasons
            cards.append(card)
    worker = decision.get("worker_progress_observation")
    worker_coverage = "not_requested"
    if worker is not None:
        if (not isinstance(worker, dict)
                or worker.get("schema") != "zsentinel-worker-progress-observation-v1"
                or worker.get("source") != "existing_sqlite_mode_ro_query_only_indexed_runner_events"
                or worker.get("actions_performed") is not False
                or worker.get("live_worker_claims_verified") is not False
                or worker.get("worker_resumed_verified") is not False
                or worker.get("runner_restart_authorized") is not False):
            raise ValueError("untrusted worker progress source")
        worker_coverage = _short(worker.get("coverage"), 80)
        followups = worker.get("owner_followup_candidates")
        if not isinstance(followups, list) or len(followups) > 24:
            raise ValueError("unbounded worker followup list")
        seen = set()
        for item in followups:
            if (not isinstance(item, dict)
                    or item.get("owner_claim_verified") is not False
                    or item.get("recovery_authorized") is not False
                    or item.get("continuation_verified") is not False
                    or type(item.get("worker_slot")) is not int
                    or not 1 <= item["worker_slot"] <= 16):
                raise ValueError("untrusted worker owner followup")
            project = _project(item.get("project_id"))
            key = (project, item["worker_slot"])
            if key in seen:
                raise ValueError("duplicate worker owner followup")
            seen.add(key)
            cls = item.get("classification")
            priority = ("P0" if cls == "allocated_paused_with_claimed_queue_owner_gate"
                        else "P1")
            cards.append(_card(priority, project, "worker_owner_review",
                               "Worker " + str(item["worker_slot"]) + ": " +
                               _short(item.get("safe_followup"), 150),
                               source="indexed_browser_event_and_queue_sample"))
    pressure = decision.get("live_queue_pressure")
    queue_coverage = "not_requested_or_unavailable"
    if pressure is not None:
        if (not isinstance(pressure, dict)
                or pressure.get("claim_ownership_verified") is not False):
            raise ValueError("untrusted queue pressure contract")
        queue_coverage = _short(pressure.get("coverage", pressure.get("source")), 80)
        # An aggregate must never invent a new owner or translate attempts to failures.
        if pressure.get("queue_write_authorized") is True:
            raise ValueError("queue write authority cannot come from a read-only handoff")
    # A first operator run has no private previous queue snapshot yet.
    # Existing router already validated a bounded *current* SQLite source:
    # surface current lease conflicts now, without inventing trend or owner.
    current_queue = {"coverage": queue_coverage, "observed_at_utc": None,
                     "inconsistent_windows": None, "expired_leases": None,
                     "unverifiable_leases": None, "group_sample_truncated": True}
    if pressure is not None and pressure.get("coverage") == "current":
        totals = pressure.get("totals")
        groups = pressure.get("project_groups")
        if (type(totals) is not dict or type(groups) is not list
                or len(groups) > 18
                or type(pressure.get("project_groups_truncated")) is not bool
                or type(pressure.get("sample_is_complete")) is not bool):
            raise ValueError("missing bounded current queue source")
        for name in ("inconsistent_owner_lease_window_rows",
                     "expired_owner_lease_rows",
                     "unverifiable_owner_lease_rows"):
            value = totals.get(name)
            if type(value) is not int or not 0 <= value <= 2500:
                raise ValueError("invalid current queue lease total")
        current_queue = {
            "coverage": "current",
            "observed_at_utc": pressure.get("observed_at"),
            "inconsistent_windows": totals["inconsistent_owner_lease_window_rows"],
            "expired_leases": totals["expired_owner_lease_rows"],
            "unverifiable_leases": totals["unverifiable_owner_lease_rows"],
            "group_sample_truncated": pressure["project_groups_truncated"],
        }
        stamp = current_queue["observed_at_utc"]
        if type(stamp) is not str or len(stamp) > 40:
            raise ValueError("current queue observation needs UTC source timestamp")
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid current queue observation timestamp") from exc
        if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError("queue owner timestamp must be UTC")
        already = set()
        for row in groups:
            if (type(row) is not dict
                    or row.get("priority") not in ("P0", "P1", "P2", "P3")):
                raise ValueError("invalid current queue group priority")
            pid = _project(row.get("project_id"))
            pri = row["priority"]
            if (pid, pri) in already:
                raise ValueError("duplicate current queue owner group")
            already.add((pid, pri))
            kinds = []
            for name, label in (
                ("inconsistent_owner_lease_window_rows", "onbetrouwbare start/vervalklok"),
                ("expired_owner_lease_rows", "verlopen lease"),
                ("unverifiable_owner_lease_rows", "niet verifieerbare lease")):
                value = row.get(name)
                if type(value) is not int or not 0 <= value <= 2500:
                    raise ValueError("invalid current queue lease group")
                if value:
                    kinds.append(str(value) + " " + label)
            if not kinds:
                continue
            card = _card(
                pri if pri in ("P0", "P1") else "P2",
                pid, "queue_current_owner_review",
                "Actuele read-only queue: " + ", ".join(kinds) +
                " (bronprioriteit " + pri + "). Laat bevoegde eigenaar "
                "originele claim/lease controleren; GEEN reclaim/retry.",
                source="validated_current_sqlite_queue_pressure",
                ref="https://github.com/Zennay/zCloud/issues/677")
            card["source_priority"] = pri
            card["queue_observed_at_utc"] = parsed.isoformat(timespec="seconds")
            card["queue_claim_failure_proven"] = False
            card["owner_lease_released_verified"] = False
            cards.append(card)
    issue = decision.get("open_product_issue_triage")
    issue_coverage = "not_requested_or_unavailable"
    if issue is not None:
        if not isinstance(issue, dict):
            raise ValueError("invalid open issue triage")
        records = issue.get("issues", [])
        if not isinstance(records, list) or len(records) > 60:
            raise ValueError("unbounded issue sample")
        if issue.get("source") == "github_readonly_issue_inbox":
            issue_coverage = "bounded_" + _short(issue.get("coverage"), 30)
        elif records:
            raise ValueError("issues cannot appear without read-only GitHub source")
        seen_urls = set()
        for row in records:
            if not isinstance(row, dict):
                raise ValueError("invalid issue row")
            priority = row.get("priority")
            url = row.get("issue_url")
            if priority not in ("P0", "P1") or not isinstance(url, str):
                continue
            if url in seen_urls or not SAFE_ISSUE.fullmatch(url):
                continue
            seen_urls.add(url)
            card = _card(priority, row.get("project_id"), "open_product_issue",
                         row.get("action") or "Controleer bestaande open PR en eigenaar",
                         source="bounded_open_issue_sample", ref=url)
            related = row.get("open_pr_candidates", [])
            if not isinstance(related, list) or len(related) > 3:
                raise ValueError("invalid related product pull request inventory")
            origin = url.rsplit("/issues/", 1)[0] + "/pull/"
            if any(not isinstance(pr, str) or not SAFE_PR.fullmatch(pr)
                   or not pr.startswith(origin) for pr in related):
                raise ValueError("untrusted cross-repo product PR link")
            card["existing_pr_urls"] = sorted(set(related))
            card["review_existing_pr_first"] = bool(related)
            cards.append(card)
    # The portfolio already compared exact-private, read-only queue snapshots.
    # Its owner handoff MUST use that already-observed comparison, never run
    # a second provider/SQLite read, and never invent a lease-release right.
    conflict_total = None  # UNKNOWN unless an exact private comparison was supplied.
    conflict_truncated = False
    if queue_trend is not None:
        from zsentinel_queue_churn import (
            public_summary as claim_summary, private_conflict_owner_rows)
        bound_summary = claim_summary(queue_trend)
        if decision.get("queue_claim_churn") != bound_summary:
            raise ValueError("owner queue trend not attached to same decision")
        conflict_total = bound_summary[
            "current_inconsistent_lease_windows_owner_review"]
        conflicts = private_conflict_owner_rows(queue_trend)
        conflict_truncated = queue_trend["lease_conflict_samples_truncated"]
        if conflict_total > len(conflicts) and not conflict_truncated:
            raise ValueError("incomplete queue conflict sample falsely marked complete")
        for item in conflicts:
            pri = item["priority"]
            # P3 is not part of the actionable P0/P1/P2 owner handoff.
            owner_priority = pri if pri in ("P0", "P1") else "P2"
            card = _card(
                owner_priority, item["project_id"], "queue_lease_window_review",
                "Onbetrouwbare claimstart/vervaltijd op " +
                item["current_status"] + " queuewerk (priority " + pri +
                ", " + str(item["current_claim_attempts"]) +
                " claimpogingen). Bevoegde eigenaar controleert originele " +
                "claim en lease; geen vrijgave of overname.",
                source="readonly_exact_private_queue_snapshot_comparison",
                ref="https://github.com/Zennay/zCloud/issues/677")
            card["source_priority"] = pri
            card["queue_claim_failure_proven"] = False
            card["owner_lease_released_verified"] = False
            cards.append(card)
    elif decision.get("queue_claim_churn") is not None:
        # A previous file missing/unreadable is not a zero-conflict result.
        if decision["queue_claim_churn"].get("coverage") != "unavailable":
            raise ValueError("owner queue handoff lacks observed trend")
        conflict_truncated = True
    # Historical backlog is a DIFFERENT source class. Never splice it into
    # fresh incidents, issue intake, live alerts or worker assignment.
    backlog = decision.get("historical_owner_recheck_candidates", [])
    old = decision.get("historical_unresolved")
    historical = []
    if (type(backlog) is not list or len(backlog) > 5):
        raise ValueError("invalid bounded historical owner recheck source")
    if backlog:
        if (type(old) is not dict
                or old.get("schema") != "zsentinel-historical-backlog-v1"
                or old.get("source_partial") is not True
                or old.get("action_authorized") is not False
                or type(old.get("shown")) is not list
                or len(old["shown"]) > 30):
            raise ValueError("historical handoff needs canonical unresolved ledger")
        historical_source = {row.get("incident_id"): row for row in old["shown"]
                             if type(row) is dict}
        if len(historical_source) != len(old["shown"]):
            raise ValueError("duplicate historical ledger incident ID")
        seen_history = set()
        for row in backlog:
            if (type(row) is not dict or type(row.get("incident_id")) is not str
                    or not re.fullmatch("[0-9a-f]{32}", row["incident_id"])
                    or row["incident_id"] in seen_history
                    or row.get("source_status") !=
                        "historical_not_observed_needs_owner_recheck"
                    or row.get("current_source_observed") is not False
                    or row.get("notifier_delivery_authorized") is not False
                    or row.get("recovery_action_authorized") is not False
                    or row.get("owner_confirmation_required") is not True
                    or row.get("severity_at_last_observation") not in ("P0", "P1")
                    or type(row.get("next_step")) is not str):
                raise ValueError("untrusted historical owner recheck")
            seen_history.add(row["incident_id"])
            prior = historical_source.get(row["incident_id"])
            if (prior is None
                    or prior.get("source_status") !=
                        "not_observed_needs_reconciliation"
                    or prior.get("current_source_observed") is not False
                    or prior.get("mutation_authorized") is not False
                    or prior.get("human_escalation_historical") is not True
                    or prior.get("project_id") != row.get("project_id")
                    or prior.get("severity") != row["severity_at_last_observation"]
                    or prior.get("problem") != row.get("problem_at_last_observation")
                    or prior.get("last_seen") != row.get("source_last_seen")
                    or prior.get("exact_github_job") != row.get("exact_github_job")):
                raise ValueError("historical owner source contradicts private ledger")
            original = row.get("exact_github_job")
            if original is not None:
                if (type(original) is not dict
                        or original.get("project_id") != row["project_id"]):
                    raise ValueError("cross-project historical GitHub job")
                fields = {key: original.get(key) for key in
                          ("run_id", "job_id", "head_sha")}
            else:
                fields = None
            card = _card(row["severity_at_last_observation"],
                         row["project_id"], "historical_owner_recheck",
                         row["next_step"], source="prior_private_ledger_not_current")
            card = _attach_verified_job(card, fields, registry, None)
            last = row["source_last_seen"]
            if type(last) is not str or len(last) > 40:
                raise ValueError("invalid historical last seen")
            try:
                parsed = datetime.fromisoformat(last.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("invalid historical last seen") from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("historical last seen needs timezone")
            card.update({"historical_source_last_seen_utc":
                         parsed.astimezone(timezone.utc).isoformat(timespec="seconds"),
                         "current_source_observed": False,
                         "historical_reconciliation_required": True,
                         "live_alert_authorized": False})
            historical.append(card)
    # Reuse the very same bounded GitHub read; no extra fetch or release action.
    cards.extend(pr_cards)
    # Priority before kind: make owner/incident work visible before new intake.
    order = {"P0": 0, "P1": 1, "P2": 2}
    kind_order = {"fresh_incident": 0, "worker_owner_review": 1,
                  "queue_current_owner_review": 2,
                  "queue_lease_window_review": 3,
                  "vps_capacity_owner_review": 4,
                  "open_pr_owner_review": 5, "open_product_issue": 6}
    cards.sort(key=lambda c: (order[c["priority"]], kind_order[c["kind"]],
                              c.get("review_by_utc", "9999"),
                              0 if c.get("existing_pr_urls") else 1,
                              c["project"], c.get("issue_url", ""), c["next_step"]))
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "schema": SCHEMA,
        "created_at_utc": stamp,
        "coverage": {"github": _short(sources.get("github_jobs"), 80)
                     if isinstance(sources.get("github_jobs"), str)
                     else "bounded_exact_job_receipts_only",
                     "queue": queue_coverage, "workers": worker_coverage,
                     "vps_capacity": capacity_coverage,
                     "issues": issue_coverage, "prs": pr_coverage,
                     "notion": _short(sources.get("notion"), 80)},
        "priority_items": cards[:MAX_ITEMS],
        "notion_owner_status_review": notion_owner_status,
        "current_queue_lease_owner_summary": current_queue,
        "queue_claim_conflict_total_observed": conflict_total,
        "queue_claim_conflicts_sample_truncated": conflict_truncated,
        "queue_claim_comparison_supplied": queue_trend is not None,
        "historical_owner_rechecks": historical,
        "historical_source_incomplete": (
            old.get("source_partial") is True if type(old) is dict else True),
        "historical_rechecks_truncated": (
            decision.get("historical_recheck_source_truncated") is True),
        "items_truncated": len(cards) > MAX_ITEMS,
        "all_sources_complete": False,
        "owner_and_current_claim_verified": False,
        "human_review_required": True,
        "actions_performed": False,
        "worker_resumed_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
    }


def _pr_gate_brief(card):
    """Display sampled exact-head gate evidence without granting permission."""
    observed = card.get("pr_observed_gate_counts")
    if observed is None:
        return ""
    if (type(observed) is not dict
            or observed.get("required_checks_and_reviews_verified") is not False
            or type(observed.get("check_run_window_complete")) is not bool
            or type(observed.get("review_window_truncated")) is not bool):
        raise ValueError("untrusted exact-head PR gate counts")
    numbers = {}
    for key in ("check_runs_sampled", "check_runs_reported",
                "checks_failed_seen_min", "checks_pending_seen_min",
                "checks_inconclusive_seen_min",
                "same_head_independent_approvals_seen"):
        value = observed.get(key)
        if type(value) is not int or not 0 <= value <= 1000000:
            raise ValueError("invalid bounded PR count")
        numbers[key] = value
    if (numbers["check_runs_sampled"] > 30
            or numbers["check_runs_reported"] < numbers["check_runs_sampled"]
            or any(numbers[key] > numbers["check_runs_sampled"] for key in (
                "checks_failed_seen_min", "checks_pending_seen_min",
                "checks_inconclusive_seen_min"))
            or numbers["same_head_independent_approvals_seen"] > 30):
        raise ValueError("inconsistent PR gate sample")
    return ("zichtbare checks " + str(numbers["check_runs_sampled"]) + "/" +
            str(numbers["check_runs_reported"]) +
            ", falend minstens " + str(numbers["checks_failed_seen_min"]) +
            ", wachtend minstens " + str(numbers["checks_pending_seen_min"]) +
            ", onbeslist minstens " + str(numbers["checks_inconclusive_seen_min"]) +
            ", onafhankelijke same-head approvals gezien " +
            str(numbers["same_head_independent_approvals_seen"]) +
            (" (reviewvenster afgekapt)" if observed["review_window_truncated"] else "") +
            "; verplichte checks/reviews NIET geverifieerd")


def _md(value, limit=MAX_TEXT):
    """Neutralize untrusted GitHub/worker text inside Markdown handoffs."""
    value = _short(value, limit)
    for marker in ("\\", chr(96), "*", "_", "{", "}", "[", "]",
                   "(", ")", "<", ">", "#", "!", "|"):
        value = value.replace(marker, "\\" + marker)
    return value



def _current_queue_owner_note(report):
    """One bounded first-run queue read, never an owner or lease-release proof."""
    row = report.get("current_queue_lease_owner_summary")
    if type(row) is not dict or type(row.get("coverage")) is not str:
        raise ValueError("missing source-bound first queue observation")
    coverage = row["coverage"]
    if coverage != "current":
        return ("Actuele leasebron " + _short(coverage, 55) +
                "; leaseconflicten, verlopen en onbekende clocks ONBEKEND.")
    if type(row.get("group_sample_truncated")) is not bool:
        raise ValueError("current queue sample bound unknown")
    nums = []
    for key, title in (
            ("inconsistent_windows", "tegenstrijdige leasevensters"),
            ("expired_leases", "verlopen leases"),
            ("unverifiable_leases", "niet-verifieerbare leaseklokken")):
        value = row.get(key)
        if type(value) is not int or not 0 <= value <= 2500:
            raise ValueError("invalid source-bound current queue owner count")
        nums.append(str(value) + " " + title)
    timestamp = row.get("observed_at_utc")
    if type(timestamp) is not str or len(timestamp) > 40:
        raise ValueError("missing current queue source moment")
    return ("Read-only queue " + _short(timestamp, 40) + ": " +
            ", ".join(nums) +
            ". Geen vrijgave of overname." +
            (" Projectenlijst afgekapt, resterende items ONBEKEND."
             if row["group_sample_truncated"] else ""))


def _historical_html(report):
    """Separate prior-ledger owner queue, deliberately NOT a fresh incident."""
    entries = report.get("historical_owner_rechecks", [])
    if type(entries) is not list or len(entries) > 5:
        raise ValueError("invalid historical owner handoff collection")
    rows = []
    for row in entries:
        if (type(row) is not dict
                or row.get("kind") != "historical_owner_recheck"
                or row.get("evidence") != "prior_private_ledger_not_current"
                or row.get("current_source_observed") is not False
                or row.get("historical_reconciliation_required") is not True
                or row.get("live_alert_authorized") is not False
                or any(row.get(k) is not False for k in (
                    "claim_authorized", "retry_authorized",
                    "merge_authorized", "deploy_authorized"))
                or row.get("priority") not in ("P0", "P1")):
            raise ValueError("untrusted historical owner view")
        last = row.get("historical_source_last_seen_utc")
        if type(last) is not str or len(last) > 35:
            raise ValueError("missing last-seen historical source")
        job = row.get("exact_github_job")
        job_links = ""
        if job is not None:
            if (type(job) is not dict
                    or set(job) != {"run_id", "job_id", "head_sha"}
                    or type(job["run_id"]) is not int
                    or type(job["job_id"]) is not int
                    or not SAFE_SHA.fullmatch(str(job["head_sha"]))):
                raise ValueError("invalid historical exact job reference")
            for key, suffix, label in (
                ("exact_github_job_url",
                 "/actions/runs/" + str(job["run_id"]) + "/job/" + str(job["job_id"]),
                 "Historische job"),
                ("exact_github_commit_url", "/commit/" + job["head_sha"],
                 "Historische commit")):
                url = row.get(key)
                if url is None:
                    continue
                if (type(url) is not str
                        or not re.fullmatch(
                            r"https://github[.]com/Zennay/[A-Za-z0-9_.-]+"
                            + re.escape(suffix), url)):
                    raise ValueError("noncanonical historical GitHub link")
                job_links += (' <a href="' + escape(url, quote=True) +
                              '" target="_blank" rel="noopener noreferrer">' +
                              label + '</a>')
        elif row.get("exact_github_job_url") or row.get("exact_github_commit_url"):
            raise ValueError("historical link without source job")
        rows.append('<li><strong>HISTORISCH · ' +
                    escape(row["priority"]) + ' · ' +
                    escape(_project(row.get("project"))) +
                    '</strong> — laatst gezien ' + escape(last) +
                    ' · momenteel NIET waargenomen. ' +
                    escape(_short(row.get("next_step"))) + job_links +
                    '</li>')
    if not rows and report.get("historical_rechecks_truncated") is not True:
        return ""
    return ('<section class="panel" aria-label="Historische eigenaarhercontrole">'
            '<h2>Historisch · opnieuw verifiëren</h2>'
            '<p>Deze blokkades zijn in een eerdere privébron gezien, '
            'niet in de huidige momentopname. Geen nieuwe melding, '
            'geen herstel- of claimbevoegdheid.</p>' +
            ('<ol>' + ''.join(rows) + '</ol>' if rows else
             '<p>Geen historische hercontroles getoond.</p>') +
            ('<p>Historische steekproef afgekapt; andere openstaande '
             'incidenten niet als hersteld beschouwen.</p>'
             if report.get("historical_rechecks_truncated") is True else '') +
            '</section>')



def _notion_review_summary(report):
    """Read safely bounded Notion owner hints from the current handoff."""
    value = report.get("notion_owner_status_review")
    if value is None:
        return None
    if (type(value) is not dict
            or value.get("coverage") not in (
                "not_requested_or_unavailable", "saved_snapshot_origin_unverified",
                "live_readonly_incomplete", "live_readonly_bounded")
            or any(value.get(key) is not False for key in (
                "owner_verified", "live_lease_verified",
                "can_dispatch", "can_release_claim"))
            or type(value.get("projects")) is not list
            or len(value["projects"]) > 12
            or type(value.get("items_truncated")) is not bool):
        raise ValueError("untrusted Notion owner handoff rendering")
    age = value.get("source_age_seconds")
    if age is not None and (type(age) is not int or not 0 <= age <= 2**31):
        raise ValueError("invalid Notion handoff observation age")
    unknown = value.get("unregistered_work_status_min")
    if unknown is not None and (type(unknown) is not int or not 0 <= unknown <= 400):
        raise ValueError("invalid unregistered Notion work count")
    status_windows = value.get("status_windows", [])
    if type(status_windows) is not list or len(status_windows) > 3:
        raise ValueError("untrusted handoff Notion query windows")
    expected = (("Claimed", 1), ("Running", 1), ("Verifying", 2))
    for idx, source in enumerate(status_windows):
        state, budget = expected[idx]
        if (type(source) is not dict or source.get("status") != state
                or source.get("page_budget") != budget
                or type(source.get("pages_read")) is not int
                or not 1 <= source["pages_read"] <= budget
                or type(source.get("observed_rows")) is not int
                or not 0 <= source["observed_rows"] <= 100 * source["pages_read"]
                or source.get("coverage") not in ("partial", "complete_within_source")
                or source.get("owner_verified") is not False):
            raise ValueError("untrusted displayed Notion source window")
    seen = set()
    for row in value["projects"]:
        if type(row) is not dict or row.get("project") in seen:
            raise ValueError("duplicate or invalid Notion review project")
        project = _project(row.get("project"))
        seen.add(project)
        total = row.get("observed_unfinished_min")
        review = row.get("observed_verifying_min")
        if (type(total) is not int or not 1 <= total <= 400
                or type(review) is not int or not 0 <= review <= total
                or row.get("live_owner_verified") is not False
                or row.get("can_claim") is not False
                or row.get("review_required") is not True
                or type(row.get("next_step")) is not str
                or len(row["next_step"]) > 160):
            raise ValueError("untrusted Notion review counters")
    return value


def _notion_review_html(report):
    source = _notion_review_summary(report)
    if source is None:
        return ""
    age = source["source_age_seconds"]
    old = age is None or age > 600
    lines = []
    for row in source["projects"]:
        lines.append(
            "<li><strong>" + escape(row["project"]) + "</strong> · ≥" +
            str(row["observed_unfinished_min"]) + " onafgerond" +
            (" · ≥" + str(row["observed_verifying_min"]) + " Verifying"
             if row["observed_verifying_min"] else "") +
            " · " + escape(row["next_step"]) + "</li>")
    source_windows = source["status_windows"]
    coverage_lines = "".join(
        "<li>" + escape(win["status"]) + ": ≥" +
        str(win["observed_rows"]) + " geobserveerd, " +
        str(win["pages_read"]) + "/" + str(win["page_budget"]) +
        " bronpagina's · " +
        ("ONVOLLEDIG" if win["coverage"] == "partial" else
         "gelezen binnen bronvenster") + "</li>"
        for win in source_windows)
    return (
        '<section class="panel" aria-label="Notion status eigenaarhercontrole">'
        '<h2>Notion · onafgerond werk, eigenaar hercontroleren</h2>'
        '<p>Bron: ' + escape(source["coverage"]) +
        (" · VEROUDERD/ONBEKEND" if old else " · recent gelezen") +
        '. Observaties zijn GEEN actuele workerlease, bestandsclaim of '
        'toestemming om werk over te nemen.</p>' +
        ('<details><summary>Brondekking per Notion-status</summary><ul>' +
         coverage_lines + '</ul></details>' if source_windows else '') +
        ('<ul>' + "".join(lines) + '</ul>' if lines else
         '<p>Geen positieve werkitems in de begrensde bron; overige '
         'projecten blijven ONBEKEND.</p>') +
        ('<p>Projectlijst afgekapt; niet getoonde projecten ONBEKEND.</p>'
         if source["items_truncated"] else "") +
        ('<p>≥' + str(source["unregistered_work_status_min"]) +
         ' statusregels hebben geen bekend projectlabel.</p>'
         if source["unregistered_work_status_min"] else "") +
        '</section>'
    )


def handoff_html(report):
    """Script-free P0-first owner strip for the existing live cockpit."""
    if (not isinstance(report, dict) or report.get("schema") != SCHEMA
            or report.get("actions_performed") is not False
            or report.get("owner_and_current_claim_verified") is not False):
        raise ValueError("untrusted operator handoff")
    items = report.get("priority_items")
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError("unbounded operator handoff")
    cards = []
    for item in items:
        if (not isinstance(item, dict) or item.get("claim_authorized") is not False
                or item.get("retry_authorized") is not False
                or item.get("merge_authorized") is not False
                or item.get("deploy_authorized") is not False):
            raise ValueError("authorizing handoff item rejected")
        url = item.get("issue_url")
        link = (' <a href="' + escape(url, quote=True) +
                '" target="_blank" rel="noopener noreferrer">Bestaand issue</a>'
                if isinstance(url, str) and SAFE_ISSUE.fullmatch(url) else '')
        prs = item.get("existing_pr_urls", [])
        if not isinstance(prs, list) or len(prs) > 3 or any(
                not isinstance(p, str) or not SAFE_PR.fullmatch(p) for p in prs):
            raise ValueError("invalid issue-linked PR handoff")
        pr_links = "".join(' <a href="' + escape(p, quote=True) +
                           '" target="_blank" rel="noopener noreferrer">Bestaande PR</a>'
                           for p in prs)
        job = item.get("exact_github_job")
        job_url = item.get("exact_github_job_url")
        commit_url = item.get("exact_github_commit_url")
        if job is not None:
            if (type(job) is not dict
                    or set(job) != {"run_id", "job_id", "head_sha"}
                    or not SAFE_SHA.fullmatch(str(job.get("head_sha", "")))):
                raise ValueError("invalid private handoff job")
            if job_url is not None and (
                    type(job_url) is not str or not job_url.startswith("https://github.com/Zennay/")
                    or not job_url.endswith("/actions/runs/" + str(job["run_id"]) +
                                           "/job/" + str(job["job_id"]))):
                raise ValueError("untrusted private handoff job link")
            if commit_url is not None and (
                    type(commit_url) is not str or not commit_url.startswith("https://github.com/Zennay/")
                    or not commit_url.endswith("/commit/" + job["head_sha"])):
                raise ValueError("untrusted private handoff commit link")
        elif job_url is not None or commit_url is not None:
            raise ValueError("unbound private handoff link")
        verified_links = ""
        for target, label in ((job_url, "Exacte job"), (commit_url, "Exacte commit")):
            if target:
                verified_links += (' <a href="' + escape(target, quote=True) +
                                   '" target="_blank" rel="noopener noreferrer">' +
                                   label + '</a>')
        due = item.get("review_by_utc")
        if due is not None and (type(due) is not str or len(due) > 35):
            raise ValueError("untrusted review deadline")
        overdue = item.get("review_overdue_at_export")
        if overdue is not None and type(overdue) is not bool:
            raise ValueError("untrusted overdue classification")
        due_label = (" · DEADLINE VERSTREKEN" if overdue else "") + (
            " · Beoordelen vóór " + escape(due) if due else "")
        pr_label = ""
        if item.get("kind") == "open_pr_owner_review":
            source = item.get("pr_observation_coverage")
            if source not in ("bounded_exact_head_review_and_checks",
                              "source_incomplete_or_changed",
                              "not_queried_after_auth_or_rate_limit"):
                raise ValueError("untrusted private PR observation coverage")
            head = item.get("pr_observed_head_sha")
            if head is not None and (type(head) is not str or not SAFE_SHA.fullmatch(head)):
                raise ValueError("untrusted private PR exact head")
            if (item.get("merge_authorized") is not False
                    or item.get("exact_head_required_ci_verified") is not False
                    or item.get("independent_review_policy_verified") is not False):
                raise ValueError("PR gate unexpectedly authorizing")
            pr_label = (" · PR-bron: " + escape(source) +
                        (" · geobserveerde HEAD " + escape(head[:12]) if head else
                         " · actuele HEAD ONBEKEND") +
                        " · vereiste CI/review niet bewezen")
            brief = _pr_gate_brief(item)
            if brief:
                pr_label += " · " + escape(brief)
        cards.append('<li><strong>' + escape(_short(item.get("priority"), 5)) +
                     ' · ' + escape(_short(item.get("project"))) + '</strong> — ' +
                     escape(_short(item.get("next_step"))) + due_label + pr_label +
                     link + pr_links + verified_links + '</li>')
    coverage = report.get("coverage", {})
    if not isinstance(coverage, dict) or len(coverage) > 8:
        raise ValueError("invalid handoff source coverage")
    sources = " · ".join(
        escape(_short(name, 20)) + ': ' + escape(_short(value, 75))
        for name, value in coverage.items())
    compared = report.get("queue_claim_comparison_supplied")
    count = report.get("queue_claim_conflict_total_observed")
    truncated = report.get("queue_claim_conflicts_sample_truncated")
    if (type(compared) is not bool or type(truncated) is not bool
            or (compared and (type(count) is not int or not 0 <= count <= 2500))
            or (not compared and count is not None)):
        raise ValueError("untrusted queue comparison handoff provenance")
    current_queue_line = _current_queue_owner_note(report)
    queue_line = ("Claimleasevergelijking niet aangeleverd; conflictstatus ONBEKEND."
                  if not compared else
                  str(count) + " tegenstrijdige leasevensters in de begrensde bron; "
                  "alleen handmatige eigenaarverificatie.")
    if truncated:
        queue_line += " Bron niet volledig; niet-getoonde conflicten blijven ONBEKEND."
    return ('<section class="panel warn" aria-label="Prioritaire eigenaaroverdracht">'
            '<h2>Nu opvolgen · P0 eerst</h2><p>Actuele, begrensde '
            'bronwaarneming. GEEN vrijgave voor workerclaim, retry, merge of deploy.</p>'
            '<p class="small muted">Bronbereik: ' + sources + '</p>' +
            '<p class="small muted">Queue huidig: ' +
            escape(current_queue_line) + '</p>' +
            '<p class="small muted">Queue #677: ' + escape(queue_line) + '</p>' +
            ('<ol>' + ''.join(cards) + '</ol>' if cards else
             '<p>Geen actie in het onderzochte venster; geen compleet herstelbewijs.</p>') +
            ('<p>Werkbak afgekapt; extra items blijven buiten dit venster.</p>'
             if report.get("items_truncated") is True else '') +
            '</section>' + _notion_review_html(report) +
            _historical_html(report))


def markdown(report):
    if (not isinstance(report, dict) or report.get("schema") != SCHEMA
            or report.get("actions_performed") is not False
            or report.get("merge_authorized") is not False):
        raise ValueError("invalid handoff report")
    lines = ["# zSentinel · actuele eigenaaroverdracht",
             "Bronmoment: " + _md(report.get("created_at_utc"), 40),
             "",
             "**Alleen brongebonden advies.** Geen claim, CI-herstart, merge, "
             "VPS-herstel of deploy uitgevoerd of toegestaan.",
             "",
             "## Bronbereik (nooit volledig)"]
    for label, value in report["coverage"].items():
        lines.append("- " + _md(label, 24) + ": " + _md(value, 100))
    lines.append("")
    queue_known = report.get("queue_claim_comparison_supplied")
    queue_count = report.get("queue_claim_conflict_total_observed")
    queue_partial = report.get("queue_claim_conflicts_sample_truncated")
    if (type(queue_known) is not bool or type(queue_partial) is not bool
            or (queue_known and (type(queue_count) is not int
                                 or not 0 <= queue_count <= 2500))
            or (not queue_known and queue_count is not None)):
        raise ValueError("unsafe queue source status in Markdown")
    lines.append("Queue nu: " + _md(_current_queue_owner_note(report), 350))
    lines.append("Queue #677: " + (
        str(queue_count) + " tegenstrijdige leasevensters in begrensde bron."
        if queue_known else "geen bronvergelijking; leaseconflicten ONBEKEND."))
    if queue_partial:
        lines.append("Queuebron niet volledig; verborgen leaseconflicten ONBEKEND.")
    notion = _notion_review_summary(report)
    if notion is not None:
        age = notion["source_age_seconds"]
        lines.extend([
            "", "## Notion · onafgerond werk (GEEN claimautorisatie)",
            "Bron: " + _md(notion["coverage"], 50) +
            (" · verouderd of onbekend" if age is None or age > 600 else
             " · beperkt recent geobserveerd"),
            "Notion-statussen bewijzen geen actuele worker, lease, bestandsscope of vrijgave.",
        ])
        for win in notion["status_windows"]:
            lines.append("- Statusbron " + _md(win["status"]) + ": minstens " +
                         str(win["observed_rows"]) + " rijen, " +
                         str(win["pages_read"]) + "/" +
                         str(win["page_budget"]) + " pagina's · " +
                         ("ONVOLLEDIG" if win["coverage"] == "partial"
                          else "binnen venster gelezen"))
        for item in notion["projects"]:
            lines.append("- " + _md(item["project"]) +
                         ": minstens " + str(item["observed_unfinished_min"]) +
                         " onafgerond" +
                         (", Verifying minstens " + str(item["observed_verifying_min"])
                          if item["observed_verifying_min"] else "") +
                         " · " + _md(item["next_step"], 160))
        if notion["items_truncated"]:
            lines.append("Projectvoorbeeld afgekapt; andere projecten ONBEKEND.")
        if notion["unregistered_work_status_min"]:
            lines.append("Niet aan project toe te wijzen: minstens " +
                         str(notion["unregistered_work_status_min"]) +
                         " statuswaarnemingen.")
    lines.append("")
    lines.append("## Prioritaire opvolging")
    for n, card in enumerate(report["priority_items"], 1):
        lines.append(str(n) + ". **" + card["priority"] + " " +
                     _md(card["project"]) + "** · " + _md(card["kind"]) +
                     " · " + _md(card["next_step"]))
        if card.get("issue_url"):
            lines.append("   Bestaand issue: " + card["issue_url"])
        if card.get("review_by_utc"):
            lines.append("   Uiterlijk beoordelen (UTC): " + _md(card["review_by_utc"], 40))
            if card.get("review_overdue_at_export") is True:
                lines.append("   DEADLINE VERSTREKEN volgens exportklok; eigenaar controleren.")
        job = card.get("exact_github_job")
        if job:
            lines.append("   Exacte bron (geen actierecht): run " +
                         str(job["run_id"]) + ", job " + str(job["job_id"]) +
                         ", commit " + job["head_sha"])
            if card.get("exact_github_job_url"):
                lines.append("   GitHub-job: " + card["exact_github_job_url"])
                lines.append("   GitHub-commit: " + card["exact_github_commit_url"])
        for pr in card.get("existing_pr_urls", []):
            lines.append("   Eerst bestaande PR beoordelen: " + pr)
        if card.get("kind") == "open_pr_owner_review":
            coverage = card.get("pr_observation_coverage")
            if coverage not in ("bounded_exact_head_review_and_checks",
                                "source_incomplete_or_changed",
                                "not_queried_after_auth_or_rate_limit"):
                raise ValueError("untrusted private PR Markdown coverage")
            head = card.get("pr_observed_head_sha")
            if head is not None and (type(head) is not str or not SAFE_SHA.fullmatch(head)):
                raise ValueError("untrusted private PR Markdown head")
            lines.append("   PR-bron: " + _md(coverage, 50) +
                         " · geobserveerde HEAD " + (head if head else "ONBEKEND") +
                         " · vereiste CI/review, eigenaar en merge NIET bevestigd.")
            brief = _pr_gate_brief(card)
            if brief:
                lines.append("   Gate-steekproef: " + _md(brief, 450))
    if not report["priority_items"]:
        lines.append("Geen actie in de beperkte steekproef; brongebrek is geen groen licht.")
    if report["items_truncated"]:
        lines.append("Meer items niet getoond; geen volledige werkvoorraad.")
    historical = report.get("historical_owner_rechecks", [])
    if type(historical) is not list or len(historical) > 5:
        raise ValueError("unbounded historical owner Markdown")
    if historical or report.get("historical_rechecks_truncated") is True:
        lines.extend(["", "## HISTORISCH · NIET ACTUEEL WAARGENOMEN",
                      "Alleen eerdere privéledgerbron. Geen nieuw incident, "
                      "geen verzending, geen automatische herstelactie."])
        for card in historical:
            if (card.get("kind") != "historical_owner_recheck"
                    or card.get("current_source_observed") is not False
                    or card.get("historical_reconciliation_required") is not True
                    or card.get("live_alert_authorized") is not False
                    or card.get("claim_authorized") is not False
                    or card.get("retry_authorized") is not False
                    or card.get("merge_authorized") is not False
                    or card.get("deploy_authorized") is not False):
                raise ValueError("unsafe historical owner Markdown")
            lines.append("- HISTORISCH " + _md(card.get("priority"), 5) +
                         " " + _md(card.get("project"), 40) +
                         " · laatst gezien " +
                         _md(card.get("historical_source_last_seen_utc"), 40))
            lines.append("  Eigenaar moet historische blokkade opnieuw verifiëren: " +
                         _md(card.get("next_step")))
            job = card.get("exact_github_job")
            if job:
                if (type(job) is not dict
                        or type(job.get("run_id")) is not int
                        or type(job.get("job_id")) is not int
                        or not SAFE_SHA.fullmatch(str(job.get("head_sha", "")))):
                    raise ValueError("untrusted historical Markdown GitHub job")
                lines.append("  Oude exacte job: run " + str(job["run_id"]) +
                             ", job " + str(job["job_id"]) +
                             ", HEAD " + job["head_sha"])
                if card.get("exact_github_job_url"):
                    lines.append("  GitHub-job (historisch): " +
                                 card["exact_github_job_url"])
                    lines.append("  GitHub-commit: " +
                                 card["exact_github_commit_url"])
        if report.get("historical_rechecks_truncated") is True:
            lines.append("Historische lijst afgekapt; geen verborgen item is opgelost.")
    lines.extend(["", "Eigenaar moet actuele claims, exacte PR-head, CI, reviews "
                  "en releasebevoegdheid apart verifiëren.", ""])
    return "\n".join(lines)


def serialize(report, suffix):
    if suffix == ".md":
        return markdown(report)
    if suffix == ".json":
        return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    raise ValueError("private handoff output must be .md or .json")
