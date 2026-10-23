#!/usr/bin/env python3
"""One-shot private owner handoff from an already assembled zSentinel decision.

No provider reads or control-plane writes. Handoff items are suggestions only:
even an apparent free slot, expired lease or green CI is NOT authorization.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import unicodedata
from io import StringIO
from html import escape
from datetime import datetime, timezone
from zsentinel_selection_holds import admit as admit_selection_holds

SAFE_SHA = re.compile(r"^[a-f0-9]{40}$")
SAFE_REPOSITORY = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")
SCHEMA = "zsentinel-runtime-owner-handoff-v1"
MAX_ITEMS = 24
MAX_TEXT = 220
SAFE_PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
SAFE_ISSUE = re.compile(r"^https://github\.com/Zennay/[A-Za-z0-9_.-]+/issues/[1-9][0-9]{0,8}$")
SAFE_PR = re.compile(r"^https://github\.com/Zennay/[A-Za-z0-9_.-]+/pull/[1-9][0-9]{0,8}$")


def _short(value, limit=MAX_TEXT):
    """Bound source text while making invisible Unicode controls observable."""
    if not isinstance(value, str):
        return "ONBEKEND"
    visible = []
    for char in value:
        if char in (chr(9), chr(10), chr(13)):
            visible.append(" ")
            continue
        category = unicodedata.category(char)
        if (category in ("Cc", "Cs", "Cf", "Zl", "Zp")
                or (char.isspace() and char != " ")):
            visible.append("⟦U+%04X⟧" % ord(char))
        else:
            visible.append(char)
    return " ".join("".join(visible).split())[:limit] or "ONBEKEND"

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
        "kind": _short(kind, 80),
        "next_step": _short(action),
        "evidence": _short(source, 80),
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



def _link_issue_pr_owner_evidence(cards, pr_cards):
    """Attach already-inspected PR gate steps to related open issue cards.

    The issue's body can only suggest a relationship. Matching canonical
    PRs still does NOT prove the issue is fixed or authorize merge/release.
    No additional provider reads, active-owner inference, or queue changes.
    """
    observed = {}
    for pr in pr_cards:
        urls = pr.get("existing_pr_urls")
        if (pr.get("kind") != "open_pr_owner_review"
                or type(urls) is not list or len(urls) != 1
                or type(urls[0]) is not str or not SAFE_PR.fullmatch(urls[0])
                or urls[0] in observed
                or pr.get("merge_authorized") is not False
                or pr.get("exact_head_required_ci_verified") is not False
                or pr.get("independent_review_policy_verified") is not False):
            raise ValueError("untrusted existing PR owner source for issue routing")
        coverage = pr.get("pr_observation_coverage")
        if coverage not in (
                "bounded_exact_head_review_and_checks",
                "source_incomplete_or_changed",
                "not_queried_after_auth_or_rate_limit"):
            raise ValueError("unknown existing PR source coverage")
        sha = pr.get("pr_observed_head_sha")
        if coverage != "bounded_exact_head_review_and_checks" and sha is not None:
            raise ValueError("incomplete PR source cannot supply observed HEAD")
        if sha is not None and (type(sha) is not str or
                                not SAFE_SHA.fullmatch(sha)):
            raise ValueError("invalid linked exact PR SHA")
        observed[urls[0]] = {
            "pr_url": urls[0],
            "source_coverage": coverage,
            "observed_head_sha": sha,
            "next_step": pr["pr_gate_next_step"],
            "issue_fixed_verified": False,
            "current_owner_verified": False,
            "merge_authorized": False,
            "deploy_authorized": False,
        }
    for card in cards:
        if card.get("kind") != "open_product_issue":
            continue
        issue = card.get("issue_url")
        links = card.get("existing_pr_urls")
        if (type(issue) is not str or not SAFE_ISSUE.fullmatch(issue)
                or type(links) is not list or len(links) > 3
                or card.get("merge_authorized") is not False):
            raise ValueError("invalid open issue to PR linkage")
        origin = issue.split("/issues/", 1)[0] + "/pull/"
        if any(type(url) is not str or not SAFE_PR.fullmatch(url)
               or not url.startswith(origin) for url in links):
            raise ValueError("cross-repository issue/PR linkage")
        matches = [dict(observed[url]) for url in dict.fromkeys(links)
                   if url in observed]
        card["linked_inspected_pr_gates"] = matches
        card["uninspected_related_pr_count"] = len(set(links)) - len(matches)
        card["issue_fixed_verified"] = False
        # Keep issue priority intact; never turn a sampled PR listing into
        # a reason to take another worker's issue or to close it.


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
        "project_relation_coverage": "not_observed",
        "unresolved_project_relation_count": None,
        "claim_window_observation_counts": None,
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
    relation_coverage = observed.get("project_relation_coverage")
    unresolved_relations = observed.get("unresolved_project_relation_count")
    if mode[0] == "notion_readonly_data_source_pages":
        if (relation_coverage not in ("complete", "partial")
                or type(count) is not int or not 0 <= count <= 400
                or type(unresolved_relations) is not int
                or not 0 <= unresolved_relations <= count
                or (relation_coverage == "complete") != (unresolved_relations == 0)):
            raise ValueError("invalid Notion project-relation coverage")
    elif relation_coverage is not None or unresolved_relations is not None:
        raise ValueError("saved Notion snapshot cannot assert live project-relation coverage")
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
    window_counts = observed.get("claim_window_observation_counts")
    allowed_windows = {
        "missing_worker_or_lease_fields", "date_only_not_a_lease",
        "invalid_lease_timestamp", "invalid_lease_interval",
        "future_claim_start", "expired_claim_window",
        "time_window_present_not_owner_verified",
    }
    if mode[0] == "notion_readonly_data_source_pages":
        if (type(window_counts) is not dict
                or observed.get("claim_window_verified") is not False
                or any(category not in allowed_windows or type(n) is not int
                       or n < 1 for category, n in window_counts.items())
                or sum(window_counts.values()) != count):
            raise ValueError("untrusted Notion claim window observations")
    elif window_counts is not None:
        raise ValueError("saved connector cannot supply live lease evidence")
    project_windows = observed.get("active_project_claim_window_counts")
    if mode[0] == "notion_readonly_data_source_pages":
        if (type(project_windows) is not dict or len(project_windows) > 50
                or set(project_windows) != set(counts)):
            raise ValueError("untrusted per-project Notion lease source")
        summed_windows = {}
        for project, buckets in project_windows.items():
            if (type(buckets) is not dict or len(buckets) > 7
                    or any(k not in allowed_windows or type(n) is not int or n < 1
                           for k, n in buckets.items())
                    or sum(buckets.values()) != counts[project]):
                raise ValueError("inconsistent project Notion lease evidence")
            for key, value in buckets.items():
                summed_windows[key] = summed_windows.get(key, 0) + value
        if summed_windows != window_counts:
            raise ValueError("Notion project/portfolio lease totals disagree")
    elif project_windows is not None:
        raise ValueError("saved Notion source cannot assert live project claim windows")
    running_clock = observed.get("active_project_in_progress_claim_clock_rechecks")
    if mode[0] == "notion_readonly_data_source_pages":
        if (type(running_clock) is not dict
                or set(running_clock) != set(counts)
                or any(type(n) is not int or n < 0
                       or n > counts[pid] - verifying.get(pid, 0)
                       or n > sum(
                           value for kind, value in project_windows[pid].items()
                           if kind != "time_window_present_not_owner_verified")
                       for pid, n in running_clock.items())):
            raise ValueError("Notion claimed/running anomaly counts inconsistent")
    elif running_clock is not None:
        raise ValueError("saved Notion snapshot cannot assert current work clocks")
    windows = observed.get("status_source_windows")
    status_windows = []
    if mode[0] == "notion_readonly_data_source_pages" and windows is None:
        raise ValueError("live Notion handoff lacks per-status source windows")
    if windows is not None:
        expected = (("Claimed", 1), ("Running", 1), ("Verifying", 2))
        if (mode[0] != "notion_readonly_data_source_pages"
                or type(windows) is not list or len(windows) != len(expected)):
            raise ValueError("private Notion handoff cannot carry live source receipts")
        pages, total_rows, partial = 0, 0, False
        unresolved_relations_from_windows = 0
        for row, (status, budget) in zip(windows, expected):
            if (type(row) is not dict or row.get("status") != status
                    or row.get("page_budget") != budget
                    or type(row.get("pages_read")) is not int
                    or not 1 <= row["pages_read"] <= budget
                    or type(row.get("observed_rows")) is not int
                    or not 0 <= row["observed_rows"] <= 100 * row["pages_read"]
                    or type(row.get("unresolved_project_relations")) is not int
                    or not 0 <= row["unresolved_project_relations"] <= row["observed_rows"]
                    or row.get("coverage") not in
                    ("complete_within_source", "partial")
                    or row.get("read_only") is not True
                    or row.get("claim_scope_verified") is not False):
                raise ValueError("invalid Notion handoff status window")
            pages += row["pages_read"]
            total_rows += row["observed_rows"]
            unresolved_relations_from_windows += row["unresolved_project_relations"]
            partial |= row["coverage"] == "partial"
            status_windows.append({
                "status": status, "page_budget": budget,
                "pages_read": row["pages_read"],
                "observed_rows": row["observed_rows"],
                "unresolved_project_relations": row["unresolved_project_relations"],
                "coverage": row["coverage"], "owner_verified": False,
            })
        if (type(observed.get("total_pages_read")) is not int
                or observed["total_pages_read"] != pages or pages > 4
                or total_rows != count
                or unresolved_relations_from_windows != unresolved_relations
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
        in_progress = total - review
        clock_buckets = (
            dict(project_windows.get(pid, {})) if project_windows is not None else None)
        uncertain = (sum(value for kind, value in clock_buckets.items()
                         if kind != "time_window_present_not_owner_verified")
                     if clock_buckets is not None else None)
        running_uncertain = (
            running_clock[pid] if running_clock is not None else None)
        known.append({
            "project": pid, "observed_unfinished_min": total,
            "observed_verifying_min": review,
            "observed_in_progress_min": in_progress,
            "observed_claim_clock_recheck_min": uncertain,
            "observed_in_progress_claim_clock_recheck_min": running_uncertain,
            "claim_window_observation_counts": clock_buckets,
            "next_step": (
                "Notion-claimklokken missen of verlopen: bevoegde eigenaar controleert "
                "originele wachtrijlease, slot en PR; GEEN claimvrijgave of retry."
                if uncertain else
                "Controleer bestaande PR, queueclaim en verificatie-eigenaar; "
                "Notion-status bewijst geen huidige lease."
                if clock_buckets is not None else
                "Controleer actieve taakclaim, scope en bestaande PR; "
                "bron heeft geen actuele leaseklokken."),
            "live_owner_verified": False,
            "review_required": True, "can_claim": False,
        })
    # The canonical registry and the live source must agree on how many
    # positive rows lacked a resolvable Project relation. Without this,
    # a source could claim a different urgent mapping count from the
    # actual grouped unfinished-work inventory.
    if (mode[0] == "notion_readonly_data_source_pages"
            and registry is not None and unknown_total != unresolved_relations):
        raise ValueError("unresolved Notion project counts disagree with registry mapping")
    # Flag substantive owner attention before the large Verifying backlog:
    # rows are incomplete positive observations, not eligible work slots.
    known.sort(key=lambda x: (
        -bool(x["observed_in_progress_claim_clock_recheck_min"]),
        -(x["observed_in_progress_claim_clock_recheck_min"] or 0),
        -(x["observed_claim_clock_recheck_min"] or 0),
        -x["observed_in_progress_min"],
        -x["observed_verifying_min"], x["project"]))
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
        "project_relation_coverage": (
            relation_coverage if mode[0] == "notion_readonly_data_source_pages"
            else "not_observed"),
        "unresolved_project_relation_count": (
            unresolved_relations if mode[0] == "notion_readonly_data_source_pages"
            else None),
        "claim_window_observation_counts": (
            dict(sorted(window_counts.items())) if window_counts is not None else None),
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
    # Read-side authentication and repository-access failures deserve an
    # actionable owner handoff. A proven rate-budget hold remains an
    # informational wait (the private cockpit already displays it), never a
    # human escalation, runner outage or authorization to rerun Actions.
    refresh_source = decision.get("live_github_read")
    if refresh_source is not None:
        if type(refresh_source) is not dict:
            raise ValueError("untrusted GitHub refresh owner source")
        denial = refresh_source.get("provider_admission_denial")
        if denial not in (None, "authentication_required", "rate_limit_confirmed"):
            raise ValueError("untrusted GitHub admission owner source")
        rows = refresh_source.get("refreshed_projects", [])
        if type(rows) is not list or len(rows) > 8:
            raise ValueError("unbounded GitHub refresh owner source")
        if denial == "authentication_required":
            cards.append(_card(
                "P1", "cloud", "github_refresh_auth_owner_hold",
                "GitHub-authenticatie voor runtime jobverversing geweigerd. "
                "Bevoegde eigenaar herstelt het bron-token en herhaalt pas "
                "daarna een begrensde read-only observatie; geen CI-rerun.",
                source="github_token_auth_denial_not_runner_failure"))
        denied_repos = set()
        for row in rows:
            if type(row) is not dict or row.get("status") not in (
                    "refreshed", "degraded"):
                raise ValueError("invalid GitHub owner refresh row")
            category = row.get("admission_category")
            if category is not None and (
                    row["status"] != "degraded" or category not in (
                        "authentication_required", "rate_limit_confirmed",
                        "forbidden_or_rate_limit_unknown")):
                raise ValueError("untrusted project GitHub admission")
            if category != "forbidden_or_rate_limit_unknown":
                continue
            project = _project(row.get("project_id"))
            if registry is not None and project not in registry:
                raise ValueError("unknown repository in GitHub owner refresh")
            if project in denied_repos:
                continue
            denied_repos.add(project)
            if len(denied_repos) <= 3:
                cards.append(_card(
                    "P1", project, "github_repository_access_owner_hold",
                    "GitHub-weigering zonder bewezen tarieflimiet. Bevoegde "
                    "repo-eigenaar controleert toegangsrechten en API-budget; "
                    "deze bron bewijst geen billing- of runnerstoring.",
                    source="github_403_admission_unverified"))
    dep = decision.get("incident_dependency_holds")
    dependency_coverage = "not_supplied"
    dependency_count = None
    if dep is not None:
        examples = dep.get("examples") if type(dep) is dict else None
        raw_dependency_coverage = (dep.get("coverage", "incomplete_or_stale")
                                   if type(dep) is dict else None)
        allowed_dependency_reasons = {
            "claim_inventory_not_complete", "task_inventory_not_complete",
            "claim_observation_missing", "task_observation_missing",
            "claim_observation_stale_or_future", "task_observation_stale_or_future",
            "active_claim_scope_unknown",
        }
        dependency_reasons = (dep.get("coverage_reasons", [])
                              if type(dep) is dict else None)
        if (type(dep) is not dict
                or raw_dependency_coverage not in ("complete", "incomplete_or_stale")
                or type(dependency_reasons) is not list or len(dependency_reasons) > 7
                or any(reason not in allowed_dependency_reasons for reason in dependency_reasons)
                or (raw_dependency_coverage == "complete" and dependency_reasons)
                or type(dep.get("count")) is not int
                or not 0 <= dep["count"] <= 250
                or type(examples) is not list or len(examples) > 8
                or len(examples) > dep["count"]
                or dep.get("samples_truncated") is not (
                    dep["count"] > len(examples))
                or dep.get("exact_reconciliation_required") is not True
                or dep.get("claim_authorized") is not False):
            raise ValueError("untrusted dependency HOLD source")
        if raw_dependency_coverage == "complete":
            dependency_count = dep["count"]
            dependency_coverage = ("bounded_examples_truncated"
                                   if dep["samples_truncated"] else "bounded_examples")
        else:
            # Never carry a zero count across a missing, stale or incomplete
            # task/claim inventory as proof that no dependent work exists.
            dependency_count = None
            dependency_coverage = "incomplete_or_stale"
        for row in examples:
            if (type(row) is not dict
                    or row.get("reason") != "incident_dependency_reconciliation_required"
                    or row.get("claim_authorized") is not False
                    or row.get("priority") not in ("P0", "P1", "P2", "P3")
                    or type(row.get("id")) is not str
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", row["id"])
                    or type(row.get("unresolved_count")) is not int
                    or not 1 <= row["unresolved_count"] <= 30
                    or type(row.get("observed_blocked_count")) is not int
                    or not 0 <= row["observed_blocked_count"] <= row["unresolved_count"]):
                raise ValueError("invalid dependency HOLD owner source")
            project = _project(row.get("project_id"))
            priority = row["priority"] if row["priority"] in ("P0", "P1") else "P2"
            cards.append(_card(
                priority, project, "incident_dependency_owner_hold",
                "Routeerbare kandidaat " + row["id"] + " HOLD: " +
                str(row["unresolved_count"]) + " incidentafhankelijkheden missen " +
                "exacte job-reconciliatie. Bevoegde eigenaar herbeoordeelt " +
                "originele bron/claims; GEEN dispatch of leasevrijgave.",
                source="bounded_router_incident_dependency_hold"))
    # Router gate/capacity/destination HOLDs are product blockers, not
    # independent task suggestions. Preserve them in the central owner view
    # using the same validated decision; never reinterpret them as dispatch.
    gate = decision.get("routing_gate_holds")
    gate_coverage = "not_supplied"
    gate_count = None
    gate_truncated = False
    if gate is not None:
        if (type(gate) is not dict
                or gate.get("coverage") not in ("complete", "incomplete_or_stale")
                or gate.get("exact_gate_recheck_required") is not True
                or gate.get("claim_authorized") is not False
                or gate.get("dispatch_authorized") is not False
                or type(gate.get("count")) is not int
                or not 0 <= gate["count"] <= 250
                or type(gate.get("examples")) is not list
                or len(gate["examples"]) > 8
                or len(gate["examples"]) > gate["count"]
                or gate.get("samples_truncated") is not (
                    gate["count"] > len(gate["examples"]))
                or type(gate.get("coverage_reasons")) is not list
                or len(gate["coverage_reasons"]) > 7):
            raise ValueError("untrusted router gate HOLD source")
        allowed_coverage = {
            "claim_inventory_not_complete", "task_inventory_not_complete",
            "claim_observation_missing", "task_observation_missing",
            "claim_observation_stale_or_future", "task_observation_stale_or_future",
            "active_claim_scope_unknown",
        }
        if (any(reason not in allowed_coverage for reason in gate["coverage_reasons"])
                or (gate["coverage"] == "complete" and gate["coverage_reasons"])):
            raise ValueError("inconsistent router gate coverage")
        gate_coverage = gate["coverage"]
        gate_count = gate["count"] if gate_coverage == "complete" else None
        gate_truncated = gate["samples_truncated"]
        unique_holds = set()
        for row in gate["examples"]:
            if (type(row) is not dict
                    or type(row.get("id")) is not str
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", row["id"])
                    or row.get("priority") not in ("P0", "P1", "P2", "P3")
                    or row.get("reason") not in (
                        "required_gate_blocked",
                        "self_hosted_capacity_not_advisory_headroom",
                        "compute_destination_not_explicitly_routable")
                    or row.get("claim_authorized") is not False
                    or row.get("dispatch_authorized") is not False
                    or type(row.get("required_gates")) is not list
                    or not 1 <= len(row["required_gates"]) <= 3
                    or len(set(row["required_gates"])) != len(row["required_gates"])
                    or any(g not in ("github_hosted", "self_hosted", "none")
                           for g in row["required_gates"])
                    or type(row.get("blocked_required_gates")) is not list
                    or len(row["blocked_required_gates"]) > 2
                    or any(g not in row["required_gates"]
                           for g in row["blocked_required_gates"])
                    or row.get("capacity_state") not in (
                        None, "unavailable", "stale", "overloaded",
                        "advisory_headroom")):
                raise ValueError("invalid router gate HOLD row")
            pid = _project(row.get("project_id"))
            unique_key = (pid, row["id"])
            if unique_key in unique_holds:
                raise ValueError("duplicate router gate owner card")
            unique_holds.add(unique_key)
            reason = row["reason"]
            if reason == "required_gate_blocked":
                if not row["blocked_required_gates"] or row["capacity_state"] is not None:
                    raise ValueError("invalid blocked gate evidence")
                action = ("GitHub/runner gate geblokkeerd: " +
                          ", ".join(row["blocked_required_gates"]) +
                          ". Laat bevoegde eigenaar actuele exacte gate verifiëren.")
            elif reason == "self_hosted_capacity_not_advisory_headroom":
                if ("self_hosted" not in row["required_gates"]
                        or row["blocked_required_gates"]
                        or row["capacity_state"] == "advisory_headroom"
                        or row["capacity_state"] is None):
                    raise ValueError("invalid self-hosted gate evidence")
                action = ("Self-hosted capaciteit " + row["capacity_state"] +
                          "; hosteigenaar controleert nieuwe bron en headroom.")
            else:
                if ("self_hosted" in row["required_gates"]
                        or row["blocked_required_gates"]
                        or row["capacity_state"] is not None):
                    raise ValueError("invalid destination gate evidence")
                action = ("Uitvoeringsbestemming niet bewezen; eigenaar stelt "
                          "trusted non-VPS of expliciete VPS-route vast.")
            priority = row["priority"] if row["priority"] in ("P0", "P1") else "P2"
            card = _card(priority, pid, "routing_gate_owner_hold",
                         "Taak " + row["id"] + " HOLD: " + action +
                         " GEEN dispatch/claim.", source="bounded_router_gate_hold")
            card["source_priority"] = row["priority"]
            card["gate_hold_reason"] = reason
            card["required_gates"] = row["required_gates"][:]
            card["blocked_required_gates"] = row["blocked_required_gates"][:]
            card["capacity_state"] = row["capacity_state"]
            cards.append(card)
    # An active-owner guard is a visible handoff, never a suggestion or lease grant.
    owner_report = decision.get("routing_owner_holds")
    owner_coverage = "not_supplied"
    owner_count = None
    owner_truncated = False
    owner_reasons = []
    if owner_report is not None:
        owner_examples = owner_report.get("examples") if type(owner_report) is dict else None
        owner_coverage = owner_report.get("coverage") if type(owner_report) is dict else None
        owner_reasons = owner_report.get("coverage_reasons") if type(owner_report) is dict else None
        allowed_owner_reasons = {
            "claim_inventory_not_complete", "task_inventory_not_complete",
            "claim_observation_missing", "task_observation_missing",
            "claim_observation_stale_or_future", "task_observation_stale_or_future",
            "active_claim_scope_unknown",
        }
        if (type(owner_report) is not dict
                or owner_coverage not in ("complete", "incomplete_or_stale")
                or type(owner_reasons) is not list or len(owner_reasons) > 7
                or any(reason not in allowed_owner_reasons for reason in owner_reasons)
                or (owner_coverage == "complete" and owner_reasons)
                or type(owner_report.get("count")) is not int
                or not 0 <= owner_report["count"] <= 250
                or type(owner_examples) is not list or len(owner_examples) > 8
                or len(owner_examples) > owner_report["count"]
                or owner_report.get("samples_truncated") is not (
                    owner_report["count"] > len(owner_examples))
                or owner_report.get("claim_authorized") is not False
                or owner_report.get("dispatch_authorized") is not False):
            raise ValueError("untrusted active-owner HOLD source")
        owner_count = owner_report["count"] if owner_coverage == "complete" else None
        owner_truncated = owner_report["samples_truncated"]
        seen_owner_holds = set()
        for row in owner_examples:
            if (type(row) is not dict
                    or row.get("reason") != "active_owner_protection"
                    or row.get("claim_authorized") is not False
                    or row.get("dispatch_authorized") is not False
                    or row.get("priority") not in ("P0", "P1", "P2", "P3")
                    or type(row.get("id")) is not str
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", row["id"])):
                raise ValueError("invalid active-owner HOLD row")
            project = _project(row.get("project_id"))
            unique_key = (project, row["id"])
            if unique_key in seen_owner_holds:
                raise ValueError("duplicate active-owner HOLD row")
            seen_owner_holds.add(unique_key)
            source_priority = row["priority"]
            priority = source_priority if source_priority in ("P0", "P1") else "P2"
            card = _card(
                priority, project, "routing_owner_hold",
                "Taak " + row["id"] + " HOLD: de bron meldt een actieve eigenaar. " +
                "Verifieer de actuele eigenaar/claim en stem overdracht af; " +
                "niet overnemen, geen claim of dispatch.",
                source="bounded_router_active_owner_hold")
            card["source_priority"] = source_priority
            card["owner_hold_reason"] = "active_owner_protection"
            card["claim_authorized"] = False
            card["dispatch_authorized"] = False
            cards.append(card)
    # Tasks omitted from the fair five-slot shortlist remain owner-visible,
    # never assigned or treated as resolved by a prior proposal.
    selection_source = decision.get("routing_selection_holds")
    selection = admit_selection_holds(selection_source)
    selection_coverage = selection["coverage"] if selection_source is not None else "not_supplied"
    selection_count = selection["count"] if selection_coverage == "complete" else None
    for row in selection["examples"]:
        reason = row["reason"]
        action = (
            "Voorgestelde bestandsscope overlapt; kies eerst één bron en "
            "hercontroleer actuele claims."
            if reason == "overlapping_suggested_scope" else
            "P0-eerst project-gebalanceerde shortlist vol; hercontroleer "
            "prioriteit en bron bij de volgende ronde."
        )
        priority = row["priority"] if row["priority"] in ("P0", "P1") else "P2"
        card = _card(priority, _project(row["project_id"]),
                     "routing_selection_owner_hold",
                     "Taak " + row["id"] + " HOLD: " + action +
                     " GEEN claim/dispatch.", source="bounded_router_selection_hold")
        card["source_priority"] = row["priority"]
        card["selection_hold_reason"] = reason
        cards.append(card)
    notion_owner_status = _notion_owner_status_summary(
        decision.get("notion_claims_observation"), sources.get("notion"), registry)
    # Material owner work, not a recovery command: the bounded live Notion
    # reader can now identify *observed* incomplete/expired claim clocks per
    # project. Preserve priority for pinned incidents and existing PRs. Never
    # suggest reclaim/retry, including when every Notion field is empty.
    notion_clock_review_count = 0
    notion_clock_review_truncated = False
    notion_relation_owner_review_count = 0
    if (notion_owner_status is not None
            and notion_owner_status["coverage"] in
            ("live_readonly_bounded", "live_readonly_incomplete")
            and notion_owner_status["source_age_seconds"] is not None
            and notion_owner_status["source_age_seconds"] <= 600):
        affected = [
            item for item in notion_owner_status["projects"]
            if item["observed_claim_clock_recheck_min"] is not None
            and item["observed_claim_clock_recheck_min"] > 0
        ]
        notion_clock_review_count = len(affected)
        notion_clock_review_truncated = (
            len(affected) > 6 or notion_owner_status["items_truncated"])
        for item in affected[:6]:
            project = item["project"]
            count = item["observed_claim_clock_recheck_min"]
            # A P1 clock issue must belong to the SAME Claimed/Running
            # status row. A healthy Running row elsewhere in the project does
            # not make an old Verifying anomaly an active worker incident.
            priority = (
                "P1" if item["observed_in_progress_claim_clock_recheck_min"]
                else "P2")
            action = (
                "Notion " + project + ": " + str(count) +
                " verdachte of ontbrekende claimklokken; " +
                str(item["observed_in_progress_min"]) +
                " Claimed/Running-statusregels. Eigenaar checkt originele "
                "queuelease en PR; GEEN claim, vrijgave, retry of herstart.")
            card = _card(
                priority, project, "notion_claim_clock_owner_review",
                action, source="recent_readonly_notion_claim_fields_unverified")
            card["notion_claim_clock_recheck_min"] = count
            card["notion_work_status_in_progress_min"] = item["observed_in_progress_min"]
            card["notion_claimed_running_clock_recheck_min"] = (
                item["observed_in_progress_claim_clock_recheck_min"])
            card["notion_observed_at_utc"] = notion_owner_status["observed_at_utc"]
            card["notion_claim_owner_verified"] = False
            card["queue_lease_same_work_verified"] = False
            cards.append(card)
    # An unresolved Project relation cannot be assigned to a guessed project
    # owner. Surface one cloud registry-owner handoff, P1 only when that SAME
    # source status window contains a Claimed/Running row; Verifying-only is P2.
    # This does not suggest that the underlying worker lease is active or free.
    if (notion_owner_status is not None
            and notion_owner_status["coverage"] == "live_readonly_incomplete"
            and notion_owner_status["source_age_seconds"] is not None
            and notion_owner_status["source_age_seconds"] <= 600
            and notion_owner_status["project_relation_coverage"] == "partial"):
        relation_total = notion_owner_status["unresolved_project_relation_count"]
        in_progress_relations = sum(
            window["unresolved_project_relations"]
            for window in notion_owner_status["status_windows"]
            if window["status"] in ("Claimed", "Running"))
        if relation_total > 0:
            priority = "P1" if in_progress_relations else "P2"
            card = _card(
                priority, "cloud", "notion_project_relation_owner_review",
                "Notion: " + str(relation_total) + " actieve statusregels zonder "
                "canonieke Project-relatie, waarvan " + str(in_progress_relations) +
                " Claimed/Running. Register-/taakeigenaar koppelt handmatig "
                "de juiste bron en controleert bestaande claims; GEEN overname.",
                source="live_bounded_notion_project_relation_unresolved")
            card["notion_unresolved_project_relations"] = relation_total
            card["notion_unresolved_claimed_running_relations"] = in_progress_relations
            card["notion_observed_at_utc"] = notion_owner_status["observed_at_utc"]
            card["notion_claim_owner_verified"] = False
            card["queue_lease_same_work_verified"] = False
            cards.append(card)
            notion_relation_owner_review_count = 1
    pr_cards, pr_coverage = _observed_pr_owner_cards(pr_desk, registry)
    pr_change_source = decision.get("pr_gate_continuity")
    if pr_change_source is not None:
        from zsentinel_pr_hold_continuity import SCHEMA as PR_CHANGE_SCHEMA, html_panel
        # Local schema/range/URL validation; no second GitHub read.
        html_panel(pr_change_source)
        if (pr_desk is None or type(pr_change_source) is not dict
                or pr_change_source.get("schema") != PR_CHANGE_SCHEMA
                or pr_change_source.get("merge_verified") is not False
                or pr_change_source.get("actions_performed") is not False):
            raise ValueError("untrusted PR continuity in owner handoff")
        by_url = {card["existing_pr_urls"][0]: card for card in pr_cards}
        for observed in pr_change_source["current_prs"]:
            url = observed["pr_url"]
            if url not in by_url:
                raise ValueError("PR comparison not linked to this live owner source")
            card = by_url[url]
            old_head = card.get("pr_observed_head_sha")
            if old_head != observed.get("observed_head_sha"):
                raise ValueError("PR continuity head differs from owner receipt")
            card["pr_continuity_state"] = observed["state"]
            card["pr_continuity_gate_families"] = list(
                observed.get("observed_gate_families_changed", []))
            card["pr_continuity_transitions"] = list(
                observed.get("observed_gate_transitions", []))
            card["pr_continuity_metadata_transitions"] = list(
                observed.get("observed_metadata_transitions", []))
            card["pr_continuity_owner_step"] = observed.get(
                "observed_gate_owner_step")
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
                "database_mount_inodes_unavailable_at_least_90_percent",
                "root_filesystem_headroom_below_2_gib",
                "root_filesystem_headroom_below_5_gib",
                "root_filesystem_unavailable_at_least_90_percent",
                "root_filesystem_inodes_unavailable_at_least_90_percent",
                "database_mount_headroom_below_5_gib",
                "swap_used_percent_at_least_90",
                "available_memory_percent_at_most_10",
                "capacity_severity_high", "capacity_severity_critical",
                "root_filesystem_critical", "root_filesystem_high",
                "database_mount_critical", "database_mount_high",
                "swap_pressure_high", "memory_pressure_high",
                "load_pressure_high", "no_verified_external_archive_headroom",
            }
            reasons = capacity["reason"].split(",")
            if not reasons or len(reasons) != len(set(reasons)) or any(
                    reason not in allowed for reason in reasons):
                raise ValueError("untrusted VPS overload reason")
            observation = decision.get("vps_resource_observation")
            severity = "unknown"
            metrics = None
            if observation is not None:
                if (type(observation) is not dict
                        or observation.get("source") != "trusted_readonly_vps_snapshot"
                        or type(observation.get("observed_at")) is not str
                        or type(observation.get("vps")) is not dict):
                    raise ValueError("untrusted detailed VPS capacity handoff")
                metrics = observation["vps"]
                severity = metrics.get("capacity_severity")
                if severity not in ("critical", "high", "normal"):
                    raise ValueError("invalid VPS capacity severity in handoff")
                warnings = metrics.get("capacity_warnings")
                if (type(warnings) is not list or len(warnings) > 8
                        or any(type(code) is not str or code not in allowed
                               for code in warnings)
                        or len(set(warnings)) != len(warnings)):
                    raise ValueError("invalid VPS capacity warning handoff")
                archive = metrics.get("archive_target")
                if (type(archive) is not dict
                        or archive.get("offload_authorized") is not False
                        or archive.get("mutation_performed") is not False
                        or archive.get("source_files_inspected") is not False
                        or archive.get("state") not in (
                            "not_requested", "unavailable", "observed_external_mount",
                            "same_as_root_filesystem", "mount_point_not_verified")):
                    raise ValueError("archive capacity must remain non-authorizing")
            priority = "P0" if severity == "critical" else "P1"
            reason_order = (
                "root_filesystem_critical",
                "database_mount_critical",
                "root_filesystem_inodes_unavailable_at_least_90_percent",
                "database_mount_inodes_unavailable_at_least_90_percent",
                "root_filesystem_headroom_below_2_gib",
                "root_filesystem_headroom_below_5_gib",
                "database_mount_headroom_below_5_gib",
                "root_filesystem_unavailable_at_least_90_percent",
                "disk_unavailable_at_least_90_percent",
                "root_filesystem_high", "database_mount_high",
                "disk_headroom_low", "no_verified_external_archive_headroom",
                "swap_used_percent_at_least_90", "swap_pressure_high",
                "swap_headroom_low", "available_memory_percent_at_most_10",
                "memory_pressure_high", "memory_headroom_low",
                "load_pressure_high", "load_above_conservative_capacity",
                "capacity_severity_critical", "capacity_severity_high",
            )
            reasons.sort(key=reason_order.index)
            reason_labels = {
                "root_filesystem_critical": "systeemschijf kritiek",
                "database_mount_critical": "databasemount kritiek",
                "root_filesystem_inodes_unavailable_at_least_90_percent": "systeemschijf-inodes kritiek",
                "database_mount_inodes_unavailable_at_least_90_percent": "databasemount-inodes kritiek",
                "root_filesystem_headroom_below_2_gib": "systeemschijf <2 GiB vrij",
                "root_filesystem_headroom_below_5_gib": "systeemschijf <5 GiB vrij",
                "database_mount_headroom_below_5_gib": "databasemount <5 GiB vrij",
                "root_filesystem_unavailable_at_least_90_percent": "systeemschijf >=90% bezet",
                "disk_unavailable_at_least_90_percent": "databasemount >=90% bezet",
                "root_filesystem_high": "systeemschijf krap",
                "database_mount_high": "databasemount krap",
                "disk_headroom_low": "minder dan 2 GiB vrije databasemount",
                "no_verified_external_archive_headroom": "geen externe archief-headroom bewezen",
                "swap_used_percent_at_least_90": "swap >=90% gebruikt",
                "swap_pressure_high": "swapdruk hoog",
                "swap_headroom_low": "minder dan 256 MiB vrije swap",
                "available_memory_percent_at_most_10": "RAM beschikbaar <=10%",
                "memory_pressure_high": "RAM-druk hoog",
                "memory_headroom_low": "minder dan 1 GiB beschikbare RAM",
                "load_pressure_high": "load >=2x toegestane CPU-capaciteit",
                "load_above_conservative_capacity": "load boven conservatieve CPU-capaciteit",
                "capacity_severity_critical": "kritieke capaciteitsdruk",
                "capacity_severity_high": "hoge capaciteitsdruk",
            }
            display_reasons = [reason_labels[reason] for reason in reasons]
            details = []
            if metrics is not None:
                root_free = metrics.get("root_free_disk_gib")
                swap_pct = metrics.get("swap_used_percent")
                if type(root_free) in (int, float) and math.isfinite(root_free) and root_free >= 0:
                    details.append("root " + f"{root_free:.1f}" + " GiB vrij")
                if type(swap_pct) in (int, float) and math.isfinite(swap_pct) and 0 <= swap_pct <= 100:
                    details.append("swap " + f"{swap_pct:.1f}" + "% gebruikt")
                archive = metrics.get("archive_target", {})
                if (type(archive) is dict
                        and archive.get("state") == "observed_external_mount"
                        and type(archive.get("free_disk_gib")) in (int, float)
                        and math.isfinite(archive["free_disk_gib"])
                        and archive["free_disk_gib"] >= 0):
                    details.append("extern archief " + f"{archive['free_disk_gib']:.1f}" + " GiB vrij")
            detail_text = "; ".join(details)
            card = _card(
                priority, "cloud", "vps_capacity_owner_review",
                "VPS capaciteitsdruk (" + ", ".join(display_reasons[:2]) +
                (" + meer" if len(display_reasons) > 2 else "") +
                (("; " + detail_text) if detail_text else "") +
                "). Hosteigenaar beoordeelt vrije ruimte en actieve jobs. "
                "GEEN cleanup, service-restart of workerstop.",
                source="validated_recent_readonly_vps_capacity",
                ref="https://github.com/Zennay/zCloud/issues/1326")
            card["observed_capacity_state"] = "overloaded"
            card["capacity_severity"] = severity
            card["capacity_reasons"] = reasons
            card["cleanup_authorized"] = False
            card["offload_authorized"] = False
            card["restart_authorized"] = False
            cards.append(card)
        elif capacity_coverage == "unavailable" and capacity["reason"] in {
            "database_mount_inode_capacity_unknown",
            "legacy_vps_inode_capacity_not_observed",
        }:
            # The router denied fresh self-hosted suggestions, but an absent
            # probe must not silently disappear from the human recovery desk.
            card = _card(
                "P1", "cloud", "vps_capacity_source_incomplete",
                "Databasemount inodecapaciteit is niet bewezen. VPS-eigenaar "
                "neemt een nieuwe opt-in read-only meting op dezelfde mount. "
                "GEEN nieuwe self-hosted job, cleanup of herstart vanuit deze kaart.",
                source="bounded_vps_inode_capacity_unverified",
                ref="https://github.com/Zennay/zCloud/issues/1326")
            card["observed_capacity_state"] = "unavailable"
            card["capacity_reasons"] = [capacity["reason"]]
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
        # The same bounded read-only admission used by the offline worker
        # cockpit must apply here; otherwise a source can reach owner cards
        # with classification/followup combinations the cockpit would reject.
        from zsentinel_worker_progress_observer import worker_progress_html
        from zsentinel_worker_handoff import STEP as WORKER_OWNER_STEPS
        worker_progress_html(worker)
        worker_coverage = _short(worker.get("coverage"), 80)
        observed = worker.get("observed_at_utc")
        if type(observed) is not str or len(observed) > 45:
            raise ValueError("worker owner source lacks bounded observation time")
        try:
            worker_at = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid worker source observation time") from exc
        if worker_at.utcoffset() is None or worker_at.utcoffset().total_seconds() != 0:
            raise ValueError("worker owner source timestamp must be UTC")
        worker_age = (datetime.now(timezone.utc) - worker_at).total_seconds()
        worker_fresh = -120 <= worker_age <= 600
        if not worker_fresh:
            # A stored private worker sample may be useful historically, but
            # its old queue claim must not be a *current* P0/P1 intervention.
            worker_coverage = "stale_or_future_reobservation_required"
            card = _card(
                "P2", "cloud", "worker_source_refresh_required",
                "Workerbewijs is ouder dan tien minuten of heeft een onbetrouwbare "
                "toekomstklok. Maak eerst een nieuwe begrensde read-only "
                "worker-/queueobservatie; oude blokkades zijn niet bewezen actueel.",
                source="private_worker_sample_stale_or_future")
            card["worker_observed_at_utc"] = worker_at.isoformat(timespec="seconds")
            card["current_worker_incidents_verified"] = False
            cards.append(card)
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
            followup_code = item.get("safe_followup")
            if (type(followup_code) is not str
                    or followup_code not in WORKER_OWNER_STEPS):
                raise ValueError("worker owner action has no approved explanation")
            # Preserve the sample for audit but do not turn an outdated
            # classification into a fresh worker owner escalation.
            if not worker_fresh:
                continue
            priority = ("P0" if cls == "allocated_paused_with_claimed_queue_owner_gate"
                        else "P1")
            card = _card(
                priority, project, "worker_owner_review",
                "Worker " + str(item["worker_slot"]) + ": " +
                WORKER_OWNER_STEPS[followup_code],
                source="indexed_browser_event_and_queue_sample")
            # Only bounded, non-identifying source indicators; no queue ID,
            # claim fingerprint, worker-browser URL or execution permission.
            card["worker_slot"] = item["worker_slot"]
            card["worker_condition"] = _short(cls, 85)
            card["queue_lease_observed_not_verified"] = (
                item.get("current_queue_lease_observed") is True)
            cards.append(card)
    if worker is None and sources.get("worker_progress") in (
            "degraded_unavailable_no_recovery_proof",
            "unavailable_no_worker_recovery_proof"):
        # This is a requested-but-unavailable input, NOT a negative worker
        # inventory. Keep the other project/PR owner cards usable.
        worker_coverage = "requested_source_unavailable_owner_recheck"
        card = _card(
            "P2", "cloud", "worker_source_refresh_required",
            "De aangevraagde worker-eventbron is onbeschikbaar of incompatibel. "
            "Controleer met de broneigenaar database/schema en toegestane "
            "read-only observatie; geen nul workers, recovery of slotvrijgave.",
            source="requested_worker_observation_unavailable")
        card["current_worker_incidents_verified"] = False
        cards.append(card)
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
    if pressure is None:
        # Both live-cockpit and explicit pinned-job refresh can report a
        # deliberately absent/failed SQLite read. Never infer an empty queue.
        queue_scope = sources.get("queue")
        read_scope = decision.get("live_github_read")
        source_scope = (read_scope.get("queue_source")
                        if type(read_scope) is dict else None)
        if (queue_scope == "explicitly_unobserved_no_database"
                or source_scope == "degraded_unavailable_no_mutation"):
            queue_coverage = "requested_source_unavailable_owner_recheck"
            card = _card(
                "P2", "cloud", "queue_source_refresh_required",
                "De aangevraagde read-only queuebron is niet beschikbaar. "
                "Laat de bevoegde broneigenaar de bestaande SQLite-bron "
                "controleren; geen vrije slots, nul claims of retries afleiden.",
                source="requested_queue_observation_unavailable")
            card["current_queue_owner_claim_verified"] = False
            cards.append(card)
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
        queue_age = (datetime.now(timezone.utc) - parsed).total_seconds()
        queue_fresh = -120 <= queue_age <= 600
        if not queue_fresh:
            # Saved read-only source is historical, not a new active owner
            # lease incident. Keep the source clock but withhold current totals
            # and per-project escalation until a fresh queue observation.
            queue_coverage = "stale_or_future_reobservation_required"
            current_queue.update({
                "coverage": queue_coverage,
                "inconsistent_windows": None,
                "expired_leases": None,
                "unverifiable_leases": None,
                "group_sample_truncated": True,
            })
            stale = _card(
                "P2", "cloud", "queue_source_refresh_required",
                "Queuelease-bron is ouder dan tien minuten of heeft een "
                "onbetrouwbare toekomstklok. Lees de huidige SQLite-queue "
                "opnieuw in read-only modus vóór claim-/lease-escalatie.",
                source="private_queue_pressure_stale_or_future")
            stale["queue_observed_at_utc"] = parsed.isoformat(timespec="seconds")
            stale["current_queue_owner_claim_verified"] = False
            cards.append(stale)
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
            if not kinds or not queue_fresh:
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
    # Issue #677: blocked queue rows are a distinct work state. Expose a
    # small P0/P1 *owner* inbox directly from the router's already-validated
    # aggregate; do not infer failed retries, free workers or resume rights.
    # One card per (project, priority) prevents high-volume projects from
    # consuming all owner attention; never perform additional SQLite reads.
    blocked_summary = {
        "coverage": "not_observed", "observed_blocked_count": None,
        "priority_groups_observed": None, "priority_groups_shown": 0,
        "priority_groups_truncated": True,
        "resume_authorized": False,
    }
    blocked = pressure.get("blocked_queue") if type(pressure) is dict else None
    if blocked is not None:
        if (type(blocked) is not dict
                or any(blocked.get(flag) is not False for flag in (
                    "claim_ownership_verified", "queue_write_authorized",
                    "automatic_retry_authorized"))):
            raise ValueError("untrusted or authorizing blocked queue evidence")
        blocked_coverage = blocked.get("coverage")
        # The blocked-task inventory is a distinct bounded SQLite read
        # within the same snapshot. An invalid ACTIVE row makes the active
        # queue coverage incomplete, but must not suppress otherwise-valid
        # current blocked P0/P1 work from its independent owner inbox.
        blocked_at = blocked.get("observed_at")
        blocked_fresh = False
        blocked_moment = None
        if type(blocked_at) is str and 1 <= len(blocked_at) <= 45:
            try:
                blocked_moment = datetime.fromisoformat(
                    blocked_at.replace("Z", "+00:00"))
            except ValueError:
                blocked_moment = None
            if (blocked_moment is not None
                    and blocked_moment.utcoffset() is not None
                    and blocked_moment.utcoffset().total_seconds() == 0):
                blocked_age = (datetime.now(timezone.utc) -
                               blocked_moment).total_seconds()
                blocked_fresh = -120 <= blocked_age <= 600
        blocked_usable = (
            pressure.get("coverage") in ("current", "incomplete")
            and blocked_fresh
        )
        blocked_summary["coverage"] = (
            blocked_coverage if blocked_usable else "not_current_unverified"
        )
        groups = blocked.get("project_groups")
        samples = blocked.get("work_items")
        if (blocked_coverage not in ("not_supplied", "not_observed",
                                     "complete", "partial", "stale")
                or type(groups) is not list or len(groups) > 250
                or type(samples) is not list or len(samples) > 24):
            raise ValueError("untrusted blocked queue work sample")
        if blocked_coverage in ("complete", "partial") and blocked_usable:
            groups_by_key = {}
            for entry in groups:
                if (type(entry) is not dict
                        or entry.get("priority") not in ("P0", "P1", "P2", "P3")
                        or type(entry.get("items")) is not int
                        or not 1 <= entry["items"] <= 10**9):
                    raise ValueError("invalid blocked queue project group")
                pid = _project(entry.get("project_id"))
                key = (pid, entry["priority"])
                if key in groups_by_key:
                    raise ValueError("duplicate blocked queue project group")
                groups_by_key[key] = entry["items"]
            sample_by_key = {}
            for entry in samples:
                if (type(entry) is not dict
                        or entry.get("priority") not in ("P0", "P1", "P2", "P3")
                        or entry.get("current_owner_verified") is not False
                        or entry.get("resume_authorized") is not False
                        or entry.get("automatic_retry_authorized") is not False):
                    raise ValueError("untrusted blocked queue sample item")
                pid = _project(entry.get("project_id"))
                key = (pid, entry["priority"])
                if key not in groups_by_key:
                    # Partial aggregate cannot license a fabricated count.
                    continue
                if len(sample_by_key.get(key, [])) < 2:
                    sample_by_key.setdefault(key, []).append(entry)
            candidates = sorted(
                ((pid, pri, count) for (pid, pri), count in groups_by_key.items()
                 if pri in ("P0", "P1")),
                key=lambda item: (0 if item[1] == "P0" else 1,
                                  -item[2], item[0]),
            )
            blocked_total = blocked.get("total_items_observed")
            if type(blocked_total) is not int or not 0 <= blocked_total <= 10**9:
                raise ValueError("invalid blocked queue total in owner inbox")
            blocked_summary = {
                "coverage": "bounded_current_readonly",
                "observed_blocked_count": blocked_total,
                "priority_groups_observed": len(candidates),
                "priority_groups_shown": min(8, len(candidates)),
                "priority_groups_truncated": (
                    len(candidates) > 8 or blocked.get("sample_truncated") is True),
                "resume_authorized": False,
                "active_queue_inventory_incomplete": (
                    pressure.get("coverage") == "incomplete"),
            }
            for pid, pri, count in candidates[:8]:
                # A bounded sample may contain two entries from one group.
                # Prefer the one requiring owner attention; the first rowid
                # is NOT inherently the most relevant blocked task.
                example_candidates = sample_by_key.get((pid, pri), [])
                def owner_signal_rank(item):
                    return (
                        1 if item.get("claim_fields_present_observed") is True else 0,
                        1 if item.get("eligible_observed") is False else 0,
                        1 if item.get("updated_at_clock_anomaly_observed") is True else 0,
                        1 if item.get("canonical_github_source_link_unverified") else 0,
                        min(item.get("claim_attempts", 0), 10)
                        if type(item.get("claim_attempts")) is int else 0,
                    )
                example = (
                    [max(example_candidates, key=owner_signal_rank)]
                    if example_candidates else []
                )
                title = (_short(example[0].get("title"), 65)
                         if example else None)
                action = (str(count) + " geblokkeerde queue-items (" + pri +
                          ") in " + pid + ". Bevoegde taakeigenaar controleert "
                          "originele blokkade, actuele claim en bestaande PR; "
                          "GEEN automatische retry, vrijgave of overname.")
                if title is not None:
                    action += " Voorbeeld uit beperkte bron: " + title + "."
                # A blocked task is NOT necessarily a retry-churn defect.
                # Linking every task to policy issue #677 made unrelated
                # project blockers look as though that issue owned them.
                card = _card(
                    pri, pid, "blocked_queue_owner_review", action,
                    source="validated_readonly_blocked_queue_aggregate")
                if example:
                    target = example[0].get(
                        "canonical_github_source_link_unverified")
                    if target is not None:
                        if (type(target) is not str
                                or not (SAFE_ISSUE.fullmatch(target)
                                        or SAFE_PR.fullmatch(target))):
                            raise ValueError("untrusted blocked work source link")
                        # Operator navigation only; queue-supplied links do
                        # NOT prove an issue owner, open PR or authorization.
                        card["unverified_queue_source_url"] = target
                    # Sample-only risk hints, not a project aggregate. These
                    # attributes are intentionally from the same validated
                    # blocked work row and confer NO ownership/retry rights.
                    hints = []
                    for key, label in (
                            ("claim_fields_present_observed",
                             "Voorbeeld bevat claimmetadata: eigenaar controleren"),
                            ("updated_at_clock_anomaly_observed",
                             "Voorbeeld heeft onbetrouwbare toekomstige updateklok"),
                            ("updated_at_future_observed",
                             "Voorbeeldupdate ligt na het bronmoment")):
                        value = example[0].get(key)
                        if type(value) is not bool:
                            raise ValueError("invalid bounded blocked-work risk flag")
                        if value:
                            hints.append(label)
                    eligible = example[0].get("eligible_observed")
                    if type(eligible) is not bool:
                        raise ValueError("invalid blocked-work eligibility observation")
                    if not eligible:
                        hints.append("Voorbeeld is niet eligible: geen hervatting")
                    if example[0].get("worker_slot_source_covered") is False:
                        hints.append("Workerslot ontbreekt in bron: toewijzing ONBEKEND")
                    attempts = example[0].get("claim_attempts")
                    if type(attempts) is not int or not 0 <= attempts <= 10**9:
                        raise ValueError("invalid blocked-work claim attempt count")
                    if attempts >= 3:
                        hints.append("Voorbeeld heeft herhaalde claimpogingen, GEEN bewezen fouten")
                    card["sample_only_cautions"] = hints[:6]
                card["blocked_items_observed"] = count
                card["queue_priority_observed"] = pri
                card["queue_observed_at_utc"] = blocked_moment.isoformat(timespec="seconds")
                card["active_queue_inventory_incomplete"] = (
                    pressure.get("coverage") == "incomplete")
                card["blocked_group_source_complete"] = blocked_coverage == "complete"
                card["claim_owner_verified"] = False
                card["blocked_work_resume_authorized"] = False
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
    # Match linked open issues only against the SAME verified PR observation.
    # This is a cross-reference, not proof of closure, ownership or merge.
    _link_issue_pr_owner_evidence(cards, pr_cards)
    # Reuse the very same bounded GitHub read; no extra fetch or release action.
    cards.extend(pr_cards)
    # Priority before kind: make owner/incident work visible before new intake.
    order = {"P0": 0, "P1": 1, "P2": 2}
    kind_order = {"fresh_incident": 0, "worker_owner_review": 1,
                  "worker_source_refresh_required": 6,
                  "github_refresh_auth_owner_hold": 1,
                  "github_repository_access_owner_hold": 1,
                  "queue_current_owner_review": 2,
                  "blocked_queue_owner_review": 9,
                  "queue_source_refresh_required": 6,
                  "queue_lease_window_review": 3,
                  "incident_dependency_owner_hold": 4,
                  "routing_owner_hold": 4,
                  "routing_gate_owner_hold": 4,
                  "routing_selection_owner_hold": 8,
                  "vps_capacity_owner_review": 5,
                  "vps_capacity_source_incomplete": 6,
                  "notion_claim_clock_owner_review": 8,
                  "notion_project_relation_owner_review": 9,
                  "open_pr_owner_review": 7, "open_product_issue": 8}
    cards.sort(key=lambda c: (order[c["priority"]], kind_order[c["kind"]],
                              c.get("review_by_utc", "9999"),
                              0 if c.get("existing_pr_urls") else 1,
                              c["project"], c.get("issue_url", ""), c["next_step"]))
    # Notion owner cards also share the GLOBAL 24-item priority window with
    # real incidents/PRs. 'Shown' means visible *after* the priority sort,
    # not merely selected before the global P0/P1/PR limit.
    visible_notion = sum(
        row["kind"] == "notion_claim_clock_owner_review"
        for row in cards[:MAX_ITEMS])
    visible_relation = sum(
        row["kind"] == "notion_project_relation_owner_review"
        for row in cards[:MAX_ITEMS])
    notion_clock_meta = {
        "visible": visible_notion,
        "truncated": (notion_clock_review_truncated or
                      visible_notion < notion_clock_review_count),
    }
    # Source grouping happens before the GLOBAL 24-card owner window.
    # Count only blocked groups actually rendered inside that window;
    # other P0 incidents/merge-first work may legitimately take priority.
    if blocked_summary["coverage"] == "bounded_current_readonly":
        visible_blocked = sum(
            row["kind"] == "blocked_queue_owner_review"
            for row in cards[:MAX_ITEMS])
        blocked_summary["priority_groups_shown"] = visible_blocked
        blocked_summary["priority_groups_truncated"] = (
            blocked_summary["priority_groups_truncated"] or
            visible_blocked < blocked_summary["priority_groups_observed"])
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
        "pr_gate_continuity": pr_change_source,
        "routing_owner_summary": {
             "coverage": owner_coverage,
             "held_count": owner_count,
             "sample_truncated": owner_truncated,
             "coverage_reasons": owner_reasons,
             "claim_authorized": False,
             "dispatch_authorized": False,
         },
         "routing_gate_owner_summary": {
            "coverage": gate_coverage,
            "held_count": gate_count,
            "sample_truncated": gate_truncated,
            "claim_authorized": False,
            "dispatch_authorized": False,
            "exact_gate_recheck_required": True,
        },
        "routing_selection_owner_summary": {
            "coverage": selection_coverage,
            "held_count": selection_count,
            "sample_truncated": selection["samples_truncated"],
            "claim_authorized": False, "dispatch_authorized": False,
        },
        "incident_dependency_owner_summary": {
            "coverage": dependency_coverage,
            "held_count": dependency_count,
            "coverage_reasons": (dependency_reasons if dep is not None else []),
            "claim_authorized": False,
            "runtime_recovered_verified": False,
        },
        "notion_owner_status_review": notion_owner_status,
        "notion_project_relation_owner_review": {
            "coverage": (
                notion_owner_status["coverage"]
                if notion_owner_status is not None
                and notion_owner_status["coverage"] in
                ("live_readonly_bounded", "live_readonly_incomplete")
                and notion_owner_status["source_age_seconds"] is not None
                and notion_owner_status["source_age_seconds"] <= 600
                else "not_observed_or_stale"),
            "source_count": (
                notion_owner_status["unresolved_project_relation_count"]
                if notion_owner_status is not None
                and notion_owner_status["coverage"] in
                ("live_readonly_bounded", "live_readonly_incomplete")
                and notion_owner_status["source_age_seconds"] is not None
                and notion_owner_status["source_age_seconds"] <= 600
                else None),
            "owner_card_shown": bool(visible_relation),
            "owner_card_omitted": bool(notion_relation_owner_review_count and not visible_relation),
            "source_only_not_owner_proof": True,
            "claim_authorized": False, "retry_authorized": False,
            "dispatch_authorized": False,
        },
        "notion_claim_clock_owner_review": {
            "affected_project_groups_in_sample": notion_clock_review_count,
            "coverage": (
                notion_owner_status["coverage"]
                if notion_owner_status is not None
                and notion_owner_status["coverage"] in
                ("live_readonly_bounded", "live_readonly_incomplete")
                and notion_owner_status["source_age_seconds"] is not None
                and notion_owner_status["source_age_seconds"] <= 600
                else "not_observed_or_stale"),
            "owner_cards_shown": notion_clock_meta["visible"],
            "owner_cards_sample_truncated": notion_clock_meta["truncated"],
            "source_only_not_lease_proof": True,
            "claim_authorized": False, "retry_authorized": False,
            "dispatch_authorized": False,
        },
        "current_queue_lease_owner_summary": current_queue,
        "blocked_queue_owner_summary": blocked_summary,
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


def _pr_continuity_label(card):
    """No delta means NOT measured, not a released worker or a green PR."""
    state = card.get("pr_continuity_state")
    if state is None:
        return ""
    labels = {
        "exact_head_changed_REINSPECT_GATE":
            "Sinds privé-bron: exacte HEAD gewijzigd, controleer CI/reviews opnieuw",
        "same_head_target_base_changed_REINSPECT_GATE":
            "Sinds privé-bron: doelbranch SHA gewijzigd, exacte CI en mergevoorwaarden herlezen",
        "same_head_draft_changed_REVIEW_GATE":
            "Sinds privé-bron: draftstatus gewijzigd, huidige onafhankelijke reviewroute bekijken",
        "same_head_mergeability_hint_changed_RECHECK_RULES":
            "Sinds privé-bron: GitHub mergeable-hint gewijzigd, protected-main regels controleren",
        "same_head_owner_step_changed_REVIEW_GATE":
            "Sinds privé-bron: eigenaaractie gewijzigd, huidige releasegate bekijken",
        "same_head_observed_ci_or_review_changed_REVIEW_GATE":
            "Sinds privé-bron: CI/status/reviewsteekproef veranderd, gate opnieuw inspecteren",
        "same_observed_head_and_gate_sample_NOT_ALL_CLEAR":
            "Zelfde HEAD, eigenaaractie en begrensde CI/reviewsteekproef, geen algemeen groen bewijs",
        "newly_in_bounded_sample_NOT_NEW_PR":
            "Nu voor het eerst in beperkt bronvenster, geen nieuwe PR bewezen",
        "partial_exact_source_NO_GATE_DELTA":
            "PR-bron onvolledig, daadwerkelijke gatewijziging ONBEKEND",
        "incomparable_source_times_NO_GATE_DELTA":
            "Privé-bronmomenten niet vergelijkbaar, gatewijziging ONBEKEND",
    }
    if state not in labels or card.get("kind") != "open_pr_owner_review":
        raise ValueError("untrusted exact PR continuity label")
    families = card.get("pr_continuity_gate_families", [])
    names = {
        "check_runs": "GitHub check-runs",
        "classic_statuses": "klassieke statuscontexts",
        "same_head_reviews": "same-head reviews",
    }
    if (type(families) is not list or len(families) > 3
            or len(set(families)) != len(families)
            or any(f not in names for f in families)):
        raise ValueError("invalid observed PR source families")
    transitions = card.get("pr_continuity_transitions", [])
    if (type(transitions) is not list or len(transitions) > 14
            or any(type(row) is not str or not 1 <= len(row) <= 150
                   or any(ord(ch) < 32 for ch in row) for row in transitions)):
        raise ValueError("untrusted observed current-vs-prior gate changes")
    metadata_transitions = card.get("pr_continuity_metadata_transitions", [])
    if (type(metadata_transitions) is not list or len(metadata_transitions) > 3
            or any(type(t) is not str or not 1 <= len(t) <= 150
                   or any(ord(ch) < 32 for ch in t) for t in metadata_transitions)):
        raise ValueError("invalid owner PR metadata changes")
    next_step = card.get("pr_continuity_owner_step")
    if (next_step is not None
            and (type(next_step) is not str or not 1 <= len(next_step) <= 280
                 or any(ord(ch) < 32 for ch in next_step))):
        raise ValueError("invalid private PR gate owner action")
    return (labels[state] +
            (" · veranderd: " + ", ".join(names[f] for f in families)
             if families else "") +
            (" · " + "; ".join(metadata_transitions)
             if metadata_transitions else "") +
            (" · " + "; ".join(transitions) if transitions else "") +
            (" · eigenaaractie: " + next_step if next_step else ""))


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
    for marker in ("\\\\", chr(96), "*", "_", "{", "}", "[", "]",
                   "(", ")", "<", ">", "#", "!", "|", "~"):
        value = value.replace(marker, "\\\\" + marker)
    return value

def _blocked_queue_owner_note(report):
    """Human-readable bounded blocked-work inventory, never a retry permit."""
    row = report.get("blocked_queue_owner_summary")
    if row is None:
        return "Geblokkeerde queue niet in deze oudere export; status ONBEKEND."
    if (type(row) is not dict
            or row.get("coverage") not in (
                "not_observed", "not_supplied", "not_current_unverified",
                "complete", "partial", "stale", "bounded_current_readonly")
            or row.get("resume_authorized") is not False
            or type(row.get("priority_groups_shown")) is not int
            or not 0 <= row["priority_groups_shown"] <= 8
            or type(row.get("priority_groups_truncated")) is not bool):
        raise ValueError("untrusted blocked-work owner overview")
    if row["coverage"] != "bounded_current_readonly":
        if (row.get("observed_blocked_count") is not None
                or row.get("priority_groups_observed") is not None):
            raise ValueError("unknown blocked queue source cannot report counts")
        return ("Geblokkeerde queuebron " + _short(row["coverage"], 45) +
                "; actuele blokkades ONBEKEND, geen hervatting.")
    total, groups = row.get("observed_blocked_count"), row.get("priority_groups_observed")
    if (type(total) is not int or not 0 <= total <= 10**9
            or type(groups) is not int or not 0 <= groups <= 250
            or row["priority_groups_shown"] > min(8, groups)
            or (row["priority_groups_shown"] < groups
                and row["priority_groups_truncated"] is not True)):
        raise ValueError("inconsistent current blocked-work overview")
    if type(row.get("active_queue_inventory_incomplete", False)) is not bool:
        raise ValueError("invalid active inventory coverage in blocked-work note")
    return (str(total) + " geblokkeerde items in begrensde read-only bron; " +
            str(row["priority_groups_shown"]) + "/" + str(groups) +
            " P0/P1-projectgroepen getoond. GEEN retry, claim of hervatting." +
            (" De afzonderlijke ACTIEVE queuebron is onvolledig; actuele "
             "claims/slots blijven ONBEKEND."
             if row.get("active_queue_inventory_incomplete") is True else "") +
            (" Overige groepen ONBEKEND door beperkte bron/kaarten."
             if row["priority_groups_truncated"] else ""))


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
    relation_coverage = value.get("project_relation_coverage")
    unresolved_relations = value.get("unresolved_project_relation_count")
    if value["coverage"] in ("live_readonly_bounded", "live_readonly_incomplete"):
        if (relation_coverage not in ("complete", "partial")
                or type(unresolved_relations) is not int
                or not 0 <= unresolved_relations <= 400
                or (relation_coverage == "complete") != (unresolved_relations == 0)):
            raise ValueError("invalid Notion project-relation handoff")
    elif relation_coverage != "not_observed" or unresolved_relations is not None:
        raise ValueError("non-live Notion handoff cannot claim project-relation coverage")
    lease_categories = {
        "missing_worker_or_lease_fields", "date_only_not_a_lease",
        "invalid_lease_timestamp", "invalid_lease_interval",
        "future_claim_start", "expired_claim_window",
        "time_window_present_not_owner_verified",
    }
    lease_counts = value.get("claim_window_observation_counts")
    if lease_counts is not None and (
            value["coverage"] not in ("live_readonly_bounded", "live_readonly_incomplete")
            or type(lease_counts) is not dict or len(lease_counts) > 7
            or any(k not in lease_categories or type(n) is not int or not 1 <= n <= 400
                   for k, n in lease_counts.items())
            or sum(lease_counts.values()) > 400):
        raise ValueError("untrusted Notion lease-window handoff summary")
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
    if value["coverage"] in ("live_readonly_bounded", "live_readonly_incomplete"):
        if (len(status_windows) != 3
                or any(type(win.get("unresolved_project_relations")) is not int
                       or not 0 <= win["unresolved_project_relations"] <= win["observed_rows"]
                       for win in status_windows)
                or sum(win["unresolved_project_relations"] for win in status_windows)
                   != unresolved_relations):
            raise ValueError("Notion project relation status-window counts disagree")
    elif any("unresolved_project_relations" in win for win in status_windows):
        raise ValueError("saved Notion source cannot assert live relation windows")
    seen = set()
    for row in value["projects"]:
        if type(row) is not dict or row.get("project") in seen:
            raise ValueError("duplicate or invalid Notion review project")
        project = _project(row.get("project"))
        seen.add(project)
        total = row.get("observed_unfinished_min")
        review = row.get("observed_verifying_min")
        in_progress = row.get("observed_in_progress_min")
        review_clocks = row.get("observed_claim_clock_recheck_min")
        running_review_clocks = row.get("observed_in_progress_claim_clock_recheck_min")
        if (type(total) is not int or not 1 <= total <= 400
                or type(review) is not int or not 0 <= review <= total
                or type(in_progress) is not int or in_progress != total - review
                or (review_clocks is not None and
                    (type(review_clocks) is not int or
                     not 0 <= review_clocks <= total))
                or (running_review_clocks is not None and
                    (type(running_review_clocks) is not int
                     or not 0 <= running_review_clocks <= in_progress
                     or review_clocks is None
                     or running_review_clocks > review_clocks))):
            raise ValueError("invalid Notion owner claim-clock triage counts")
        project_counts = row.get("claim_window_observation_counts")
        if project_counts is not None and (
                type(project_counts) is not dict or len(project_counts) > 7
                or any(k not in lease_categories or type(n) is not int or not 1 <= n <= 400
                       for k, n in project_counts.items())
                or sum(project_counts.values()) != total
                or review_clocks != sum(
                    n for kind, n in project_counts.items()
                    if kind != "time_window_present_not_owner_verified")):
            raise ValueError("untrusted per-project Notion claim clock summary")
        if project_counts is None and (
                review_clocks is not None or running_review_clocks is not None):
            raise ValueError("Notion owner triage cannot invent lease counts")
        if project_counts is not None and running_review_clocks is None:
            raise ValueError("Notion live source lacks status-to-clock correlation")
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
    if source["coverage"] == "not_requested_or_unavailable":
        return ('<section class="panel" aria-label="Notion eigenaarbron onbekend">'
                '<h2>Notion eigenaarbron ONBEKEND</h2><p>De aangevraagde '
                'statusbron is niet bereikbaar of niet bruikbaar. '
                'Geen nul-claims, vrije scope of herstelbevoegdheid '
                'afleiden; actuele bron door bevoegde eigenaar herlezen.</p>'
                '</section>')
    age = source["source_age_seconds"]
    old = age is None or age > 600
    lines = []
    for row in source["projects"]:
        lines.append(
            "<li><strong>" + escape(row["project"]) + "</strong> · ≥" +
            str(row["observed_unfinished_min"]) + " onafgerond" +
            (" · " + str(sum(n for k, n in row["claim_window_observation_counts"].items()
                              if k != "time_window_present_not_owner_verified")) +
             " onduidelijke/verlopen claimklokken"
             if row.get("claim_window_observation_counts") is not None else "") +
            (" · ≥" + str(row["observed_verifying_min"]) + " Verifying"
             if row["observed_verifying_min"] else "") +
            " · " + escape(row["next_step"]) + "</li>")
    source_windows = source["status_windows"]
    claim_windows = source.get("claim_window_observation_counts")
    ambiguous = (sum(n for kind, n in claim_windows.items()
                     if kind != "time_window_present_not_owner_verified")
                 if claim_windows is not None else None)
    claim_window_html = (
        '<p><strong>Notion claimklokken:</strong> ' + str(ambiguous) +
        ' regels met ontbrekende, verlopen, toekomstige of ongeldige '
        'leasevelden; ' +
        str(claim_windows.get("time_window_present_not_owner_verified", 0)) +
        ' met tijdvenster, maar ook die zijn GEEN eigenaarbewijs.</p>'
        if ambiguous is not None else
        '<p>Leasevensters niet geleverd door deze Notion-bron; ONBEKEND.</p>'
    )
    relation_panel = ""
    if source["project_relation_coverage"] == "partial":
        in_progress_relations = sum(
            win["unresolved_project_relations"]
            for win in source_windows if win["status"] in ("Claimed", "Running"))
        relation_panel = (
            '<p class="banner">PROJECTRELATIES ONVOLLEDIG — ' +
            str(source["unresolved_project_relation_count"]) +
            ' actieve statusregels zonder canonieke Project-relatie, waaronder ' +
            str(in_progress_relations) + ' Claimed/Running. ' +
            ('P1' if in_progress_relations else 'P2') +
            ' register-/taakeigenaarcontrole nodig. Niet herclaimen of herstarten.</p>'
        )
    elif source["project_relation_coverage"] == "complete":
        relation_panel = (
            '<p class="muted">Binnen de begrensde Notion-bron geen onopgeloste '
            'projectrelaties waargenomen; andere werkitems en leases ONBEKEND.</p>'
        )
    else:
        relation_panel = (
            '<p class="muted">Geen geverifieerde live projectrelatiebron; '
            'geen conclusie over vrij werk.</p>'
        )
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
        'toestemming om werk over te nemen.</p>' + claim_window_html +
        relation_panel +
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


def _selection_owner_line(report):
    """Source truth for fair shortlist omissions, not active work claims."""
    value = report.get("routing_selection_owner_summary")
    if (type(value) is not dict or value.get("claim_authorized") is not False
            or value.get("dispatch_authorized") is not False
            or type(value.get("sample_truncated")) is not bool):
        raise ValueError("invalid shortlist owner summary")
    coverage = value.get("coverage")
    count = value.get("held_count")
    if coverage in ("not_supplied", "incomplete_or_stale") and count is None:
        return "Shortlistbron ONBEKEND; geen scopevrijgave of claim."
    if coverage == "complete" and type(count) is int and 0 <= count <= 250:
        return (str(count) + " niet-geselecteerde, verder geschikte taken " +
                "(overlap of vijf-plaatsenlimiet); eigenaar controleert " +
                "huidige leases opnieuw." +
                (" Voorbeelden afgekapt." if value["sample_truncated"] else ""))
    raise ValueError("contradictory shortlist owner evidence")


def _gate_owner_html_line(report):
    """One source-bound summary reused by the existing offline cockpit."""
    gate = report.get("routing_gate_owner_summary")
    if (type(gate) is not dict or gate.get("claim_authorized") is not False
            or gate.get("dispatch_authorized") is not False
            or gate.get("exact_gate_recheck_required") is not True
            or type(gate.get("sample_truncated")) is not bool):
        raise ValueError("untrusted offline routing gate summary")
    coverage = gate.get("coverage")
    total = gate.get("held_count")
    if coverage in ("not_supplied", "incomplete_or_stale") and total is None:
        return "Uitvoergates ONBEKEND of onvolledig; geen dispatch of vrijgave."
    if coverage == "complete" and type(total) is int and 0 <= total <= 250:
        return (str(total) + " uitvoergate-HOLDs; eigenaar bevestigt exacte "
                "GitHub-/capaciteits-/bestemmingsbron vóór enig werk." +
                (" Bronvoorbeelden afgekapt." if gate["sample_truncated"] else ""))
    raise ValueError("contradictory routing gate evidence")




def _notion_clock_owner_overview(report):
    """One safe, explicit coverage line for the project-owner work queue."""
    source = report.get("notion_claim_clock_owner_review")
    if source is None:
        # Read-only saved reports from before this optional feature remain
        # inspectable; missing source is UNKNOWN, not an empty workload.
        return "Notion eigenaaracties: bron ONBEKEND (geen nulclaim)."
    if (type(source) is not dict
            or source.get("coverage") not in (
                "not_observed_or_stale", "live_readonly_bounded",
                "live_readonly_incomplete")
            or source.get("source_only_not_lease_proof") is not True
            or any(source.get(flag) is not False for flag in
                   ("claim_authorized", "retry_authorized", "dispatch_authorized"))
            or type(source.get("affected_project_groups_in_sample")) is not int
            or not 0 <= source["affected_project_groups_in_sample"] <= 12
            or type(source.get("owner_cards_shown")) is not int
            or not 0 <= source["owner_cards_shown"] <= 6
            or source["owner_cards_shown"] >
               source["affected_project_groups_in_sample"]
            or type(source.get("owner_cards_sample_truncated")) is not bool):
        raise ValueError("invalid Notion owner workboard coverage")
    if source["coverage"] == "not_observed_or_stale":
        if source["affected_project_groups_in_sample"] or source["owner_cards_shown"]:
            raise ValueError("unknown Notion owner source claims current groups")
        return ("Notion eigenaaracties: bron ontbreekt of is verouderd; "
                "aantal en eigenaarschap ONBEKEND, niet nul.")
    notion = _notion_review_summary(report)
    if notion is None or notion["coverage"] != source["coverage"]:
        raise ValueError("Notion owner workboard has no matching source")
    count = source["affected_project_groups_in_sample"]
    if count > len(notion["projects"]):
        raise ValueError("owner workboard exceeds bounded Notion projects")
    relation_note = (
        "PROJECTRELATIES ONVOLLEDIG: " +
        str(notion["unresolved_project_relation_count"]) +
        " actieve rij(en) zonder eenduidige canonieke projectmapping; geen vrije-werkconclusie. "
        if notion["project_relation_coverage"] == "partial" else
        "Projectrelaties compleet binnen de beperkte bron, maar geen lease- of eigendomsbewijs. "
        if notion["project_relation_coverage"] == "complete" else
        "Projectrelaties niet live geverifieerd. ")
    return (relation_note +
            str(count) + " projectgroepen met verdachte Notion-claimklokken "
            "in de beperkte bron; " +
            str(source["owner_cards_shown"]) +
            " eigenaaracties werkelijk zichtbaar in de P0/PR-werkbak. " +
            ("Overige groepen/acties ONBEKEND of afgekapte weergave. "
             if source["owner_cards_sample_truncated"] else "") +
            "GEEN queueclaim of hersteltoestemming.")

def _notion_relation_owner_overview(report):
    """Return source-bound registry-owner follow-up, even if its card was crowded out."""
    meta = report.get("notion_project_relation_owner_review")
    if meta is None:
        return "Projectrelatie-opvolging: historische bron zonder afzonderlijke eigenaaractie."
    if (type(meta) is not dict
            or meta.get("coverage") not in (
                "live_readonly_incomplete", "live_readonly_bounded", "not_observed_or_stale")
            or meta.get("source_only_not_owner_proof") is not True
            or any(meta.get(k) is not False for k in (
                "claim_authorized", "retry_authorized", "dispatch_authorized"))
            or type(meta.get("owner_card_shown")) is not bool
            or type(meta.get("owner_card_omitted")) is not bool):
        raise ValueError("untrusted Notion registry-owner summary")
    cards = report.get("priority_items", [])
    if type(cards) is not list or len(cards) > MAX_ITEMS:
        raise ValueError("unbounded Notion registry-owner action list")
    visible = [x for x in cards
               if type(x) is dict and x.get("kind") ==
                  "notion_project_relation_owner_review"]
    if len(visible) > 1 or meta["owner_card_shown"] != (len(visible) == 1):
        raise ValueError("Notion registry-owner visibility disagrees with handoff")
    count = meta.get("source_count")
    if meta["coverage"] == "not_observed_or_stale":
        if count is not None or visible or meta["owner_card_omitted"]:
            raise ValueError("stale Notion mapping cannot assert current owner action")
        return "Projectrelaties: actuele eigenaarbron ONBEKEND; geen vrijgegeven taken."
    notion = _notion_review_summary(report)
    if (notion is None or notion["coverage"] != meta["coverage"]
            or type(count) is not int or not 0 <= count <= 400
            or count != notion["unresolved_project_relation_count"]
            or (meta["owner_card_omitted"] != (count > 0 and not visible))):
        raise ValueError("Notion registry-owner count/visibility source mismatch")
    if count == 0:
        return ("Geen onopgeloste projectrelaties gezien binnen het beperkte "
                "Notion-venster; overige ongeziene projectrelaties blijven ONBEKEND. "
                "Geen lease-/eigenaarsbewijs.")
    if meta["coverage"] != "live_readonly_incomplete":
        raise ValueError("positive missing relations with complete coverage")
    in_progress = sum(
        win["unresolved_project_relations"]
        for win in notion["status_windows"]
        if win["status"] in ("Claimed", "Running"))
    return (str(count) + " actieve statusregels zonder canonieke Project-relatie, "
            + str(in_progress) + " daarvan Claimed/Running: " +
            ("P1" if in_progress else "P2") + " menselijke register-/claimcontrole. " +
            ("Actiekaart NIET zichtbaar door prioriteitslimiet; bronprobleem blijft open."
             if meta["owner_card_omitted"] else
             "Actiekaart zichtbaar binnen de bestaande P0/PR-werkbak.") +
            " Geen werkovername, retry of dispatch.")


def _notion_relation_card_detail(card, report):
    """Validate the single registry-owner action against the exact live windows."""
    if card.get("kind") != "notion_project_relation_owner_review":
        return ""
    notion = _notion_review_summary(report)
    meta = report.get("notion_project_relation_owner_review")
    if (notion is None or type(meta) is not dict
            or notion["coverage"] != "live_readonly_incomplete"
            or notion["project_relation_coverage"] != "partial"
            or meta.get("coverage") != notion["coverage"]
            or meta.get("source_count") != notion["unresolved_project_relation_count"]
            or meta.get("source_only_not_owner_proof") is not True
            or any(meta.get(k) is not False for k in (
                "claim_authorized", "retry_authorized", "dispatch_authorized"))
            or meta.get("owner_card_shown") is not True
            or meta.get("owner_card_omitted") is not False
            or card.get("project") != "cloud"
            or card.get("notion_claim_owner_verified") is not False
            or card.get("queue_lease_same_work_verified") is not False
            or card.get("notion_observed_at_utc") != notion["observed_at_utc"]
            or notion["source_age_seconds"] is None
            or notion["source_age_seconds"] > 600):
        raise ValueError("untrusted Notion project-relation owner card")
    unresolved = notion["unresolved_project_relation_count"]
    in_progress = sum(win["unresolved_project_relations"]
                      for win in notion["status_windows"]
                      if win["status"] in ("Claimed", "Running"))
    if (type(unresolved) is not int or not 1 <= unresolved <= 400
            or card.get("notion_unresolved_project_relations") != unresolved
            or card.get("notion_unresolved_claimed_running_relations") != in_progress
            or card.get("priority") != ("P1" if in_progress else "P2")):
        raise ValueError("Notion registry-owner card contradicts source status")
    return ("Notion bron " + notion["observed_at_utc"] +
            " · " + str(unresolved) + " projectrelaties onbekend, " +
            str(in_progress) + " bij Claimed/Running; alleen handmatige "
            "register-/taakeigenaarcontrole, GEEN claim of herverdeling.")

def _notion_clock_card_detail(card, report):
    """Bind a suggested owner recheck to the original bounded Notion read."""
    if card.get("kind") == "notion_project_relation_owner_review":
        return _notion_relation_card_detail(card, report)
    if card.get("kind") != "notion_claim_clock_owner_review":
        return ""
    summary = report.get("notion_claim_clock_owner_review")
    notion = _notion_review_summary(report)
    if (type(summary) is not dict
            or summary.get("coverage") not in
            ("live_readonly_bounded", "live_readonly_incomplete")
            or summary.get("source_only_not_lease_proof") is not True
            or any(summary.get(key) is not False for key in
                   ("claim_authorized", "retry_authorized", "dispatch_authorized"))
            or type(summary.get("owner_cards_sample_truncated")) is not bool
            or type(summary.get("affected_project_groups_in_sample")) is not int
            or not 1 <= summary["affected_project_groups_in_sample"] <= 12
            or notion is None or notion["coverage"] != summary["coverage"]
            or card.get("notion_claim_owner_verified") is not False
            or card.get("queue_lease_same_work_verified") is not False
            or card.get("notion_observed_at_utc") != notion["observed_at_utc"]):
        raise ValueError("untrusted Notion owner action scope/authority")
    project = card.get("project")
    matches = [row for row in notion["projects"] if row["project"] == project]
    if len(matches) != 1:
        raise ValueError("Notion owner card lacks its original project")
    row = matches[0]
    concern = card.get("notion_claim_clock_recheck_min")
    working = card.get("notion_work_status_in_progress_min")
    working_issues = card.get("notion_claimed_running_clock_recheck_min")
    if (type(concern) is not int or concern < 1
            or concern != row["observed_claim_clock_recheck_min"]
            or type(working) is not int
            or working != row["observed_in_progress_min"]
            or type(working_issues) is not int
            or working_issues != row["observed_in_progress_claim_clock_recheck_min"]
            or card.get("priority") != ("P1" if working_issues else "P2")):
        raise ValueError("Notion owner card contradicts its source")
    return ("Notion bron " + notion["observed_at_utc"] +
            " · " + str(concern) + " verdachte/onbekende leaseklokken, "
            "waarvan " + str(working_issues) +
            " bij Claimed/Running-statusregels; "
            "geen matching queueclaim of vrijgave bewezen.")

def _linked_issue_pr_gates(card):
    """Strict reusable validation for all private owner handoff formats."""
    gates = card.get("linked_inspected_pr_gates", [])
    if type(gates) is not list or len(gates) > 3:
        raise ValueError("unbounded issue linked exact PR gate evidence")
    if gates and card.get("kind") != "open_product_issue":
        raise ValueError("PR linkage belongs to an observed open issue")
    from zsentinel_pr_delivery import NEXT_ACTION_NL
    origin = (card.get("issue_url", "").split("/issues/", 1)[0] + "/pull/")
    seen = set()
    for gate in gates:
        if type(gate) is not dict:
            raise ValueError("invalid related PR gate record")
        url = gate.get("pr_url")
        source = gate.get("source_coverage")
        sha = gate.get("observed_head_sha")
        if (type(url) is not str or not SAFE_PR.fullmatch(url)
                or not url.startswith(origin) or url in seen
                or source not in (
                    "bounded_exact_head_review_and_checks",
                    "source_incomplete_or_changed",
                    "not_queried_after_auth_or_rate_limit")
                or gate.get("next_step") not in NEXT_ACTION_NL
                or any(gate.get(k) is not False for k in (
                    "issue_fixed_verified", "current_owner_verified",
                    "merge_authorized", "deploy_authorized"))):
            raise ValueError("untrusted linked PR release action")
        seen.add(url)
        if ((source != "bounded_exact_head_review_and_checks" and sha is not None)
                or (sha is not None and (type(sha) is not str
                                         or not SAFE_SHA.fullmatch(sha)))):
            raise ValueError("invalid exact source commit on issue PR link")
    if card.get("kind") == "open_product_issue":
        not_inspected = card.get("uninspected_related_pr_count")
        existing = card.get("existing_pr_urls", [])
        if (type(not_inspected) is not int or not 0 <= not_inspected <= 3
                or type(existing) is not list or
                len(set(existing)) != len(gates) + not_inspected
                or card.get("issue_fixed_verified") is not False):
            raise ValueError("issue PR coverage cannot prove release")
    return gates


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
        source_url = item.get("unverified_queue_source_url")
        if source_url is not None and (
                item.get("kind") != "blocked_queue_owner_review"
                or type(source_url) is not str
                or not (SAFE_ISSUE.fullmatch(source_url)
                        or SAFE_PR.fullmatch(source_url))):
            raise ValueError("untrusted queue navigation source")
        queue_link = (
            ' <a href="' + escape(source_url, quote=True) +
            '" target="_blank" rel="noopener noreferrer">Bronlink uit queue '
            '(NIET geverifieerd)</a>' if source_url is not None else '')
        cautions = item.get("sample_only_cautions", [])
        if (type(cautions) is not list or len(cautions) > 6
                or (cautions and item.get("kind") != "blocked_queue_owner_review")
                or any(type(value) is not str or not 0 < len(value) <= 100
                       for value in cautions)):
            raise ValueError("untrusted blocked-work sample-only caution")
        caution_label = (
            ' <span class="muted">Voorbeeldsignalen (niet volledig): ' +
            escape("; ".join(cautions)) + '</span>'
            if cautions else '')
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
        gate_rows = _linked_issue_pr_gates(item)
        from zsentinel_pr_delivery import NEXT_ACTION_NL
        linked_labels = "".join(
            '<div class="muted">Gekoppelde PR <a href="' +
            escape(gate["pr_url"], quote=True) +
            '" target="_blank" rel="noopener noreferrer">exacte bron</a>' +
            ' · HEAD ' + escape(
                gate["observed_head_sha"][:12]
                if gate["observed_head_sha"] else "ONBEKEND") +
            ' · ' + escape(NEXT_ACTION_NL[gate["next_step"]]) +
            ' · issue NIET als gerepareerd of gemerged bevestigd.</div>'
            for gate in gate_rows
        )
        unchecked = item.get("uninspected_related_pr_count", 0)
        missing_label = (
            ' <span class="muted">· ' + str(unchecked) +
            ' gekoppelde PR(' + ("s" if unchecked != 1 else "") +
            ') niet exact geïnspecteerd; status ONBEKEND</span>'
            if unchecked else ""
        )
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
            continuity = _pr_continuity_label(item)
            if continuity:
                pr_label += " · " + escape(continuity)
        notion_clock_label = _notion_clock_card_detail(item, report)
        cards.append('<li><strong>' + escape(_short(item.get("priority"), 5)) +
                     ' · ' + escape(_short(item.get("project"))) + '</strong> — ' +
                     escape(_short(item.get("next_step"))) +
                     (' <span class="muted">' + escape(notion_clock_label) +
                      '</span>' if notion_clock_label else '') +
                     due_label + pr_label +
                     link + queue_link + caution_label + pr_links +
                     verified_links + linked_labels + missing_label + '</li>')
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
    blocked_queue_line = _blocked_queue_owner_note(report)
    queue_line = ("Claimleasevergelijking niet aangeleverd; conflictstatus ONBEKEND."
                  if not compared else
                  str(count) + " tegenstrijdige leasevensters in de begrensde bron; "
                  "alleen handmatige eigenaarverificatie.")
    if truncated:
        queue_line += " Bron niet volledig; niet-getoonde conflicten blijven ONBEKEND."
    dependency = report.get("incident_dependency_owner_summary", {})
    allowed_dependency_reasons = {
        "claim_inventory_not_complete", "task_inventory_not_complete",
        "claim_observation_missing", "task_observation_missing",
        "claim_observation_stale_or_future", "task_observation_stale_or_future",
        "active_claim_scope_unknown",
    }
    dependency_reasons = (dependency.get("coverage_reasons", [])
                          if type(dependency) is dict else None)
    if (type(dependency) is not dict
            or type(dependency_reasons) is not list or len(dependency_reasons) > 7
            or any(reason not in allowed_dependency_reasons for reason in dependency_reasons)
            or (dependency.get("coverage") in ("bounded_examples", "bounded_examples_truncated")
                and dependency_reasons)
            or dependency.get("coverage") not in (
                "not_supplied", "incomplete_or_stale",
                "bounded_examples", "bounded_examples_truncated")
            or dependency.get("claim_authorized") is not False
            or dependency.get("runtime_recovered_verified") is not False
            or (dependency["coverage"] in ("not_supplied", "incomplete_or_stale")
                and dependency.get("held_count") is not None)
            or (dependency["coverage"] not in ("not_supplied", "incomplete_or_stale")
                and (type(dependency.get("held_count")) is not int
                     or not 0 <= dependency["held_count"] <= 250))):
        raise ValueError("untrusted dependency owner summary")
    dependency_reason_labels = {
        "claim_inventory_not_complete": "claiminventaris niet expliciet compleet",
        "task_inventory_not_complete": "taakinventaris niet expliciet compleet",
        "claim_observation_missing": "waarneming van actieve claims ontbreekt",
        "task_observation_missing": "waarneming van de taakinventaris ontbreekt",
        "claim_observation_stale_or_future": "claimwaarneming verouderd of ongeldige toekomsttijd",
        "task_observation_stale_or_future": "taakwaarneming verouderd of ongeldige toekomsttijd",
        "active_claim_scope_unknown": "bestandsscope van een actieve claim onbekend",
    }
    dep_line = (
        "Bron niet aangeleverd; afhankelijke taken ONBEKEND."
        if dependency["coverage"] == "not_supplied" else
        "Taak-/claiminventaris onvolledig of verouderd; totaal ONBEKEND, nul is geen vrijgave."
        if dependency["coverage"] == "incomplete_or_stale" else
        str(dependency["held_count"]) +
        " routeerbare kandidaat-taken op incident-HOLD; exacte bronreconciliatie vereist." +
        (" Zichtbare voorbeelden afgekapt."
         if dependency["coverage"] == "bounded_examples_truncated" else ""))
    if dependency_reasons:
        dep_line += " Bronprobleem: " + "; ".join(
            dependency_reason_labels[reason] for reason in dependency_reasons) + "."
    return ('<section class="panel warn" aria-label="Prioritaire eigenaaroverdracht">'
            '<h2>Nu opvolgen · P0 eerst</h2><p>Actuele, begrensde '
            'bronwaarneming. GEEN vrijgave voor workerclaim, retry, merge of deploy.</p>'
            '<p class="small muted">Handoff source SHA256: ' +
            _source_report_digest(report) + '</p>'
            '<p class="small muted">Bronbereik: ' + sources + '</p>' +
            '<p class="small muted">Queue huidig: ' +
            escape(current_queue_line) + '</p>' +
            '<p class="small muted">Geblokkeerd queuewerk: ' +
            escape(blocked_queue_line) + '</p>' +
            '<p class="small muted">Queue #677: ' + escape(queue_line) + '</p>' +
            '<p class="small muted">Afhankelijke taken: ' + escape(dep_line) + '</p>' +
            '<p class="small muted">Uitvoergates: ' +
            escape(_gate_owner_html_line(report)) + '</p>' +
            '<p class="small muted">Project-gebalanceerde shortlist: ' +
            escape(_selection_owner_line(report)) + '</p>' +
            '<p class="small muted">Notion claimklokken: ' +
            escape(_notion_clock_owner_overview(report)) + '</p>' +
            '<p class="small muted">Notion projectrelaties: ' +
            escape(_notion_relation_owner_overview(report)) + '</p>' +
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
             "Bronhandoff SHA256: `" + _source_report_digest(report) + "`",
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
    lines.append("Geblokkeerd queuewerk: " + _md(_blocked_queue_owner_note(report), 350))
    lines.append("Queue #677: " + (
        str(queue_count) + " tegenstrijdige leasevensters in begrensde bron."
        if queue_known else "geen bronvergelijking; leaseconflicten ONBEKEND."))
    if queue_partial:
        lines.append("Queuebron niet volledig; verborgen leaseconflicten ONBEKEND.")
    notion = _notion_review_summary(report)
    if notion is not None and notion["coverage"] == "not_requested_or_unavailable":
        lines.extend([
            "", "## Notion · bron ONBEKEND",
            "Aangevraagde Notion-statusbron onbereikbaar of niet bruikbaar. "
            "GEEN nul actieve claims en GEEN vrijgave; eigenaar leest opnieuw."
        ])
    elif notion is not None:
        age = notion["source_age_seconds"]
        lines.extend([
            "", "## Notion · onafgerond werk (GEEN claimautorisatie)",
            "Bron: " + _md(notion["coverage"], 50) +
            (" · verouderd of onbekend" if age is None or age > 600 else
             " · beperkt recent geobserveerd"),
            "Notion-statussen bewijzen geen actuele worker, lease, bestandsscope of vrijgave.",
        ])
        clock_classes = notion.get("claim_window_observation_counts")
        if clock_classes is None:
            lines.append("Notion leaseklokken: ONBEKEND; status is geen eigenaarbewijs.")
        else:
            clock_uncertain = sum(
                value for key, value in clock_classes.items()
                if key != "time_window_present_not_owner_verified")
            lines.append(
                "Notion leaseklokken: " + str(clock_uncertain) +
                " ontbrekend/verlopen/toekomstig/inconsistent; " +
                str(clock_classes.get("time_window_present_not_owner_verified", 0)) +
                " tijdvenster aanwezig maar NIET eigenaar- of claimgeverifieerd.")
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
                         (", claimklokken onduidelijk/verlopen " +
                          str(sum(n for k, n in item["claim_window_observation_counts"].items()
                                  if k != "time_window_present_not_owner_verified"))
                          if item.get("claim_window_observation_counts") is not None else "") +
                         (", Verifying minstens " + str(item["observed_verifying_min"])
                          if item["observed_verifying_min"] else "") +
                         " · " + _md(item["next_step"], 160))
        if notion["items_truncated"]:
            lines.append("Projectvoorbeeld afgekapt; andere projecten ONBEKEND.")
        if notion["unregistered_work_status_min"]:
            lines.append("Niet aan project toe te wijzen: minstens " +
                         str(notion["unregistered_work_status_min"]) +
                         " statuswaarnemingen.")
    dep = report.get("incident_dependency_owner_summary", {})
    allowed_dependency_reasons = {
        "claim_inventory_not_complete", "task_inventory_not_complete",
        "claim_observation_missing", "task_observation_missing",
        "claim_observation_stale_or_future", "task_observation_stale_or_future",
        "active_claim_scope_unknown",
    }
    dependency_reasons = (dep.get("coverage_reasons", [])
                          if type(dep) is dict else None)
    if (type(dep) is not dict
            or type(dependency_reasons) is not list or len(dependency_reasons) > 7
            or any(reason not in allowed_dependency_reasons for reason in dependency_reasons)
            or (dep.get("coverage") in ("bounded_examples", "bounded_examples_truncated")
                and dependency_reasons)
            or dep.get("coverage") not in (
                "not_supplied", "incomplete_or_stale",
                "bounded_examples", "bounded_examples_truncated")
            or dep.get("claim_authorized") is not False
            or dep.get("runtime_recovered_verified") is not False
            or (dep.get("coverage") in ("not_supplied", "incomplete_or_stale")
                and dep.get("held_count") is not None)
            or (dep.get("coverage") not in ("not_supplied", "incomplete_or_stale")
                and (type(dep.get("held_count")) is not int
                     or not 0 <= dep["held_count"] <= 250))):
        raise ValueError("invalid dependency status in owner handoff")
    if dep["coverage"] == "not_supplied":
        lines.append("Incidentafhankelijkheden: bron ONBEKEND, nooit veronderstellen dat alle taken vrij zijn.")
    elif dep["coverage"] == "incomplete_or_stale":
        if dep.get("held_count") is not None:
            raise ValueError("incomplete dependency source must not carry a zero/all-clear count")
        lines.append("Incidentafhankelijkheden: inventaris onvolledig/verouderd; totaal ONBEKEND, nul is geen vrijgave.")
        reason_labels = {
            "claim_inventory_not_complete": "claiminventaris niet expliciet compleet",
            "task_inventory_not_complete": "taakinventaris niet expliciet compleet",
            "claim_observation_missing": "waarneming van actieve claims ontbreekt",
            "task_observation_missing": "waarneming van de taakinventaris ontbreekt",
            "claim_observation_stale_or_future": "claimwaarneming verouderd of ongeldige toekomsttijd",
            "task_observation_stale_or_future": "taakwaarneming verouderd of ongeldige toekomsttijd",
            "active_claim_scope_unknown": "bestandsscope van een actieve claim onbekend",
        }
        for reason in dependency_reasons:
            lines.append("   Bronprobleem: " + reason_labels[reason] + ".")
    else:
        count = dep.get("held_count")
        if type(count) is not int or not 0 <= count <= 250:
            raise ValueError("invalid held task count")
        lines.append("Incidentafhankelijkheden: " + str(count) +
                     " verder routeerbare kandidaat-taken op HOLD; alleen exacte job-reconciliatie kan status herstellen." +
                     (" Steekproef afgekapt."
                      if dep["coverage"] == "bounded_examples_truncated" else ""))
    gate_summary = report.get("routing_gate_owner_summary")
    if type(gate_summary) is not dict or any(
            gate_summary.get(flag) is not False
            for flag in ("claim_authorized", "dispatch_authorized")):
        raise ValueError("invalid routing gate owner summary")
    if gate_summary.get("exact_gate_recheck_required") is not True:
        raise ValueError("routing gate recheck must remain manual")
    if gate_summary.get("coverage") in ("not_supplied", "incomplete_or_stale"):
        if gate_summary.get("held_count") is not None:
            raise ValueError("unknown gate source cannot assert zero")
        lines.append("Uitvoergates: bron ONBEKEND of onvolledig; geen vrijgave.")
    elif (gate_summary.get("coverage") == "complete"
          and type(gate_summary.get("held_count")) is int
          and 0 <= gate_summary["held_count"] <= 250):
        lines.append("Uitvoergates: " + str(gate_summary["held_count"]) +
                     " kandidaat-taken op HOLD; eigenaar controleert exacte gate." +
                     (" Voorbeelden afgekapt." if
                      gate_summary.get("sample_truncated") is True else ""))
    else:
        raise ValueError("invalid routing gate coverage")
    lines.append("Project-gebalanceerde shortlist: " + _selection_owner_line(report))
    lines.append("Notion claimklokken: " + _notion_clock_owner_overview(report))
    lines.append("Notion projectrelaties: " + _notion_relation_owner_overview(report))
    lines.append("")
    lines.append("## Prioritaire opvolging")
    for n, card in enumerate(report["priority_items"], 1):
        lines.append(str(n) + ". **" + card["priority"] + " " +
                     _md(card["project"]) + "** · " + _md(card["kind"]) +
                     " · " + _md(card["next_step"]))
        cautions = card.get("sample_only_cautions", [])
        if (type(cautions) is not list or len(cautions) > 6
                or (cautions and card.get("kind") != "blocked_queue_owner_review")
                or any(type(value) is not str or not 0 < len(value) <= 100
                       for value in cautions)):
            raise ValueError("untrusted blocked-work Markdown cautions")
        if cautions:
            lines.append("   Alleen voorbeeldsignalen (niet volledig): " +
                         _md("; ".join(cautions), 500))
        clock_detail = _notion_clock_card_detail(card, report)
        if clock_detail:
            lines.append("   " + _md(clock_detail, 350))
        if card.get("issue_url"):
            lines.append("   Bestaand issue: " + card["issue_url"])
        if card.get("unverified_queue_source_url"):
            source_url = card["unverified_queue_source_url"]
            if (card.get("kind") != "blocked_queue_owner_review"
                    or type(source_url) is not str
                    or not (SAFE_ISSUE.fullmatch(source_url)
                            or SAFE_PR.fullmatch(source_url))):
                raise ValueError("untrusted Markdown blocked task source link")
            lines.append("   Queue-bronlink (NIET geverifieerd als eigenaar of "
                         "huidige PR): " + source_url)
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
        from zsentinel_pr_delivery import NEXT_ACTION_NL
        for gate in _linked_issue_pr_gates(card):
            lines.append(
                "   Gekoppelde PR-bron: " + gate["pr_url"] +
                " · HEAD " + (gate["observed_head_sha"] or "ONBEKEND") +
                " · " + _md(NEXT_ACTION_NL[gate["next_step"]], 250) +
                " · issuefix en merge NIET bewezen."
            )
        if card.get("uninspected_related_pr_count"):
            lines.append("   Nog niet exact geïnspecteerde PR-links: " +
                         str(card["uninspected_related_pr_count"]) +
                         "; status en eigenaarschap ONBEKEND.")
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
            continuity = _pr_continuity_label(card)
            if continuity:
                lines.append("   Vergeleken met privé-bron: " + _md(continuity, 180))
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



CSV_FIELDS = (
    "record_type", "handoff_schema", "source_report_sha256",
    "source_created_at_utc", "source_coverage_json",
    "observed_action_card_count", "items_truncated",
    "all_sources_complete", "historical_recheck_count",
    "historical_rechecks_truncated", "priority", "project", "kind",
    "next_step", "evidence", "issue_url", "existing_pr_urls",
    "exact_github_job_run_id", "exact_github_job_id",
    "exact_github_job_head_sha", "exact_github_job_url",
    "exact_github_commit_url", "review_by_utc", "review_overdue_at_export",
    "current_source_observed", "historical_reconciliation_required",
    "live_alert_authorized", "claim_authorized", "retry_authorized",
    "merge_authorized", "deploy_authorized", "owner_verification_required",
)


def _csv_cell(value, limit=2000):
    """Make source-derived spreadsheet cells inert without hiding Unicode controls."""
    if value is None:
        return ""
    if type(value) is bool:
        text = str(value).lower()
    elif type(value) is int:
        text = str(value)
    elif type(value) is str:
        if not value:
            return ""
        text = _short(value, limit)
    else:
        raise ValueError("unexpected value in private owner CSV")
    if text.lstrip(" ").startswith(("=", "+", "-", "@")):
        text = "'" + text
    return text



def _source_report_digest(report):
    """Stable cross-format SHA256 of the handoff object, excluding its own digest."""
    source = dict(report)
    source.pop("source_report_sha256", None)
    return hashlib.sha256(json.dumps(
        source, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()

def csv_export(report):
    """Render a separate source-bound owner CSV; no claim or release action."""
    if (not isinstance(report, dict) or report.get("schema") != SCHEMA
            or report.get("actions_performed") is not False
            or report.get("merge_authorized") is not False
            or report.get("owner_and_current_claim_verified") is not False):
        raise ValueError("invalid runtime owner CSV source")
    items = report.get("priority_items")
    coverage = report.get("coverage")
    historical = report.get("historical_owner_rechecks", [])
    if (not isinstance(items, list) or len(items) > MAX_ITEMS
            or not isinstance(coverage, dict) or len(coverage) > 8
            or type(report.get("items_truncated")) is not bool
            or type(report.get("all_sources_complete")) is not bool
            or report.get("all_sources_complete") is not False
            or type(report.get("historical_rechecks_truncated")) is not bool
            or not isinstance(historical, list) or len(historical) > 5):
        raise ValueError("unbounded or incomplete runtime owner CSV metadata")
    # Reuse the existing canonical renderer's detailed card/URL/authorization
    # validation; rendering it is local and has no provider or filesystem effects.
    handoff_html(report)
    stamp = _short(report.get("created_at_utc"), 40)
    coverage_json = json.dumps(
        coverage, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_report_sha256 = _source_report_digest(report)
    metadata = {
        "handoff_schema": SCHEMA,
        "source_report_sha256": source_report_sha256,
        "source_created_at_utc": stamp,
        "source_coverage_json": coverage_json,
        "observed_action_card_count": len(items),
        "items_truncated": report["items_truncated"],
        "all_sources_complete": report["all_sources_complete"],
        "historical_recheck_count": len(historical),
        "historical_rechecks_truncated": report["historical_rechecks_truncated"],
        "live_alert_authorized": False,
        "claim_authorized": False,
        "retry_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "owner_verification_required": True,
    }
    rows = []
    for item in items:
        job = item.get("exact_github_job")
        linked = _linked_issue_pr_gates(item)
        from zsentinel_pr_delivery import NEXT_ACTION_NL
        # Preserve the existing CSV-v1 columns: clients can keep importing
        # their saved owner handoffs. The original existing_pr_urls column
        # holds navigation while next_step carries the exact source action.
        linked_followup = "".join(
            " · gekoppelde PR " + gate["pr_url"] +
            " HEAD " + (gate["observed_head_sha"] or "ONBEKEND") +
            ": " + NEXT_ACTION_NL[gate["next_step"]]
            for gate in linked
        )
        if item.get("uninspected_related_pr_count"):
            linked_followup += (
                " · " + str(item["uninspected_related_pr_count"]) +
                " PR-links nog niet exact geïnspecteerd: ONBEKEND")
        rows.append({
            **metadata,
            "record_type": "owner_action",
            "priority": item.get("priority"),
            "project": item.get("project"),
            "kind": item.get("kind"),
            "next_step": ((item.get("next_step") or "") + linked_followup +
                          (" · " + _pr_continuity_label(item)
                           if item.get("pr_continuity_state") is not None else "")),
            "evidence": item.get("evidence"),
            "issue_url": item.get("issue_url"),
            "existing_pr_urls": " | ".join(item.get("existing_pr_urls", [])),
            "exact_github_job_run_id": job.get("run_id") if job else None,
            "exact_github_job_id": job.get("job_id") if job else None,
            "exact_github_job_head_sha": job.get("head_sha") if job else None,
            "exact_github_job_url": item.get("exact_github_job_url"),
            "exact_github_commit_url": item.get("exact_github_commit_url"),
            "review_by_utc": item.get("review_by_utc"),
            "review_overdue_at_export": item.get("review_overdue_at_export"),
            "current_source_observed": None,
            "historical_reconciliation_required": False,
        })
    for item in historical:
        job = item.get("exact_github_job")
        rows.append({
            **metadata,
            "record_type": "historical_owner_recheck",
            "priority": item["priority"],
            "project": item["project"],
            "kind": item["kind"],
            "next_step": item.get("next_step"),
            "evidence": item["evidence"],
            "exact_github_job_run_id": job.get("run_id") if job else None,
            "exact_github_job_id": job.get("job_id") if job else None,
            "exact_github_job_head_sha": job.get("head_sha") if job else None,
            "exact_github_job_url": item.get("exact_github_job_url"),
            "exact_github_commit_url": item.get("exact_github_commit_url"),
            "current_source_observed": False,
            "historical_reconciliation_required": True,
        })
    if not rows:
        rows.append({
            **metadata,
            "record_type": "metadata_only_no_action_cards",
        })
    stream = StringIO()
    writer = csv.DictWriter(
        stream, fieldnames=CSV_FIELDS, lineterminator=chr(13) + chr(10))
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_cell(row.get(key)) for key in CSV_FIELDS})
    payload = chr(0xFEFF) + stream.getvalue()
    if len(payload.encode("utf-8")) > 512 * 1024:
        raise ValueError("private runtime owner CSV exceeds 512 KiB")
    return payload

def serialize(report, suffix):
    if suffix == ".md":
        return markdown(report)
    if suffix == ".json":
        envelope = dict(report)
        envelope["source_report_sha256"] = _source_report_digest(report)
        return json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2)
    if suffix == ".csv":
        return csv_export(report)
    raise ValueError("private handoff output must be .md, .json or .csv")
