#!/usr/bin/env python3
"""zSentinel: one-shot P0/P1 product issue + live PR file overlap cockpit.

Integrates the already-merged canonical product delivery workbench source with
the bounded GitHub PR collision observer. No writes, claims, retries or merges.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone, timedelta
from html import escape
import json
import os
from pathlib import Path
import re
import sys
import stat
import unicodedata

from zsentinel_operator_workbench import read_source_and_sha, validated_view
from zsentinel_product_issue_inbox import (
    canonical_projects, collect as collect_live_issues,
    bounded_get as github_issue_get, priority as canonical_issue_priority,
    observed_time as canonical_issue_clock,
)
from zsentinel_product_delivery_board import prepare as prepare_delivery_board
from zsentinel_pr_scope_guard import ROOT, registry
from zsentinel_pr_scope_portfolio import collect_portfolio
from zsentinel_pr_scope_delta import read_private_previous, compare, render_delta
from zsentinel_issue_pr_history import attach as attach_history
from zsentinel_issue_action_board import (
    build as build_human_work_inbox, render_compact_inbox,
)
from zsentinel_worklist_html import render_html as render_private_worklist_html
from zsentinel_issue_activity import classify as classify_issue_activity

MAX_PROJECTS = 3
MAX_ISSUES = 60
MAX_PRS_PER_PROJECT = 16
MAX_LINKED_PRS_PER_ISSUE = 5
P = {"P0": 0, "P1": 1, "P2": 2, "Unspecified": 3}
PR = re.compile(r"^https://github\.com/Zennay/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]{0,8})$")
OWNER_ONLY = frozenset({
    "https://github.com/Zennay/zCloud/issues/1326",
    "https://github.com/Zennay/zCloud/issues/1523",
})
NEXT_ACTIONS_NL = {
    "authorized_infrastructure_owner_only":
        "Bevoegde infrastructuur-/billingbeheerder nodig; geen serviceherstart of betaalde capaciteit.",
    "coordinate_existing_pr_file_overlap_and_owner":
        "Bekijk de bestaande overlappende PR's en stem af met huidige eigenaar vóór wijzigingen.",
    "inspect_existing_pr_and_owner_gates_first":
        "Herbruik bestaande PR; controleer actuele claim, exacte-head CI, review en releasebevoegdheid.",
    "coordinate_multiple_existing_pr_issue_mentions":
        "Meerdere open PR-teksten noemen dit issue. Bekijk eerst bestaande implementaties en stem met hun eigenaren af.",
    "verify_live_owner_then_product_implementation":
        "Controleer eerst huidige Notion-claims, open branches en bestandscope vóór productwerk.",
}


GATE_STEPS_NL = {
    "inspect_incomplete_current_head_gate_source":
        "Onvolledige check-, legacy-status- of reviewbron; eerst volledig bronbewijs ophalen.",
    "inspect_current_head_failed_checks_with_owner":
        "Huidige HEAD heeft mislukte checks/statussen; herstel via bevoegde PR-eigenaar.",
    "wait_for_existing_current_head_checks":
        "Bestaande HEAD-checks lopen nog; niet opnieuw dispatchen of mergen.",
    "owner_address_exact_head_review_changes":
        "Wijzigingsverzoek op exacte HEAD; laat bevoegde PR-eigenaar afhandelen.",
    "verify_missing_current_head_checks_and_rules":
        "Geen check-ontvangsten gezien; vereiste workflow- en branchregels apart opvragen.",
    "request_independent_current_head_review":
        "Vraag onafhankelijke review op de actuele HEAD; geen zelfgoedkeuring.",
    "verify_required_rules_ownership_and_release_separately":
        "Waarnemingen aanwezig; controleer vereiste branchregels, eigenaar en release apart.",
}


def direct_issue_inbox(canonical, selected_projects, focus_issue, token):
    """Fetch one explicitly named still-open canonical GitHub product issue.

    Not a broad search and not evidence that a missing issue is fixed.
    Reuses the already merged issue classifier and Product Delivery Board.
    """
    from zsentinel_product_delivery_board import ISSUE as ISSUE_ID
    if (type(focus_issue) is not str or ISSUE_ID.fullmatch(focus_issue) is None
            or type(selected_projects) is not list
            or len(selected_projects) != 1
            or selected_projects[0] not in canonical):
        raise ValueError("direct issue requires one registered --project and canonical repo#number")
    repository, raw_number = focus_issue.rsplit("#", 1)
    pid = selected_projects[0]
    if canonical[pid] != repository:
        raise ValueError("focused issue does not match the selected canonical repository")
    number = int(raw_number)
    issue = github_issue_get(
        "https://api.github.com/repos/" + repository +
        "/issues/" + str(number), token)
    if (type(issue) is not dict or issue.get("number") != number
            or "pull_request" in issue or issue.get("state") != "open"):
        raise ValueError("issue unavailable, not open, or not a product issue; no recovery conclusion")
    title = issue.get("title")
    assignees = issue.get("assignees")
    if (type(title) is not str or not 1 <= len(title) <= 1000
            or type(assignees) is not list):
        raise ValueError("canonical issue metadata incomplete")
    severity, qa_only = canonical_issue_priority(issue)
    if qa_only:
        raise ValueError("explicit issue is QA-only; product-delivery policy excludes it")
    created = canonical_issue_clock(issue.get("created_at"))
    updated = canonical_issue_clock(issue.get("updated_at"))
    now = datetime.now(timezone.utc)
    if not created <= updated <= now:
        raise ValueError("issue source dates are inconsistent")
    record = {
        "id": repository + "#" + str(number), "project": pid,
        "repository": repository, "number": number,
        "title": title[:180], "priority": severity,
        "qa_only": False, "has_assignee": len(assignees) > 0,
        "active_claim": "not_verified", "related_open_prs": [],
        "created_at": created.isoformat(timespec="seconds"),
        "updated_at": updated.isoformat(timespec="seconds"),
        "url": "https://github.com/" + repository + "/issues/" + str(number),
        "state": "open",
    }
    return {
        "schema": "zsentinel-product-issue-inbox-v1",
        "observed_at": now.isoformat(timespec="seconds"),
        "projects": [pid],
        "coverage": [{
            "project": pid, "repository": repository,
            "oldest_window": "not_queried_exact_issue_only",
            "recent_window": "exact_selected_open_issue_only",
            "open_pr_window": "not_queried_direct_issue_source",
            "status": "partial",
            "active_claims_verified": False,
        }],
        "request_count": 1,
        "items": [record], "suppressed_qa_only": 0,
        "stopped_after_rate_or_auth_denial": False,
        "truncated": True,
        "queue_ownership_known": False,
        "active_claims_verified": False,
        "actions_authorized": False,
        "fully_synchronized": False,
    }


def _safe_projects(source, canonical, explicit=None):
    cards = source["cards"]
    scope = {row["project"] for row in cards}
    if explicit:
        if (len(explicit) > MAX_PROJECTS or len(set(explicit)) != len(explicit)
                or any(p not in canonical or p not in source["projects"] for p in explicit)):
            raise ValueError("select at most three existing canonical projects in the source board")
        return explicit
    # Issue-first fair portfolio selection; never assume absence of sampled
    # issues means that another project has no owner or no live work.
    ranked = {}
    for card in cards:
        p = card["project"]
        if p not in canonical:
            continue
        score = P[card["priority"]]
        current = ranked.setdefault(p, [3, 0])
        current[0] = min(current[0], score)
        current[1] += 1
    return [pid for pid, _ in sorted(ranked.items(),
                                    key=lambda item: (item[1][0], -item[1][1], item[0]))[:MAX_PROJECTS]]


def _saved_pr_gates(raw):
    """Bounded saved exact-head observations; never convert success to approval."""
    rows = raw.get("pr_delivery_observations", [])
    if type(rows) is not list or len(rows) > 5:
        raise ValueError("unexpected source PR gate observation count")
    result = {}
    for row in rows:
        if type(row) is not dict:
            raise ValueError("invalid saved PR gate observation")
        link, head = row.get("url"), row.get("head_sha")
        approvals = row.get("observed_approvals")
        check = row.get("observed_check_success")
        blockers = row.get("blocking_unknowns")
        if (type(link) is not str or PR.fullmatch(link) is None
                or link in result or type(head) is not str
                or re.fullmatch(r"[a-f0-9]{40}", head) is None
                or type(approvals) is not int or not 0 <= approvals <= 100
                or type(check) is not bool
                or type(blockers) is not list or len(blockers) > 20
                or any(type(v) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,70}", v)
                       for v in blockers)
                or row.get("merge_authorized") is not False):
            raise ValueError("saved source PR gate receipt cannot authorize merge")
        result[link] = {
            "observed_exact_head_sha": head,
            "saved_native_checks_success": check,
            "saved_independent_approval_count_unverified": approvals,
            "saved_remaining_unknowns": blockers,
            "current_head_and_required_rules_not_verified": True,
            "merge_authorized": False,
        }
    return result


def compose(source, source_sha, canonical, selected, collision, saved_gates=None,
            source_mode="saved_local_snapshot", focus_issue=None, raw_board=None):
    if (not isinstance(source_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", source_sha)
            or type(collision) is not dict
            or collision.get("schema") != "zsentinel-portfolio-pr-collision-board-v1"
            or collision.get("projects") != selected
            or any(collision.get(field) is not False for field in (
                "notion_claims_verified", "prless_branch_owners_verified",
                "worker_file_leases_verified", "file_edit_authorized",
                "claim_authorized", "merge_authorized", "deploy_authorized",
                "writes_performed"))
            or type(collision.get("project_views")) is not list
            or len(collision["project_views"]) != len(selected)):
        raise ValueError("scope source unexpectedly authorizes work or mismatches issue projects")
    if source_mode not in (
            "saved_local_snapshot", "live_readonly_github_issues",
            "live_exact_selected_github_issue"):
        raise ValueError("unknown issue source mode")
    if (type(raw_board) is not dict
            or raw_board.get("schema") != "zsentinel-product-delivery-triage-v1"
            or raw_board.get("authorizes_claim") is not False
            or raw_board.get("authorizes_merge") is not False
            or raw_board.get("authorizes_deploy") is not False
            or raw_board.get("automatic_dispatch_enabled") is not False
            or raw_board.get("observed_at_utc") != source["observed_at_utc"]
            or type(raw_board.get("cards")) is not list
            or len(raw_board["cards"]) > MAX_ISSUES):
        raise ValueError("noncanonical or unauthorized raw product board")
    raw_by_issue = {}
    validated_by_issue = {card["issue"]: card for card in source["cards"]}
    for raw in raw_board["cards"]:
        if type(raw) is not dict:
            raise ValueError("invalid raw product issue record")
        issue = raw.get("issue")
        # validated_view has already accepted every original source card;
        # --focus-issue narrows only the displayed set, not the raw source.
        if issue not in validated_by_issue:
            if focus_issue is None:
                raise ValueError("raw issue absent from canonical validated source")
            continue
        if issue in raw_by_issue:
            raise ValueError("duplicate raw issue record")
        canonical_row = validated_by_issue[issue]
        if (raw.get("project") != canonical_row["project"]
                or raw.get("priority") != canonical_row["priority"]
                or raw.get("open_pr_candidates") != canonical_row["open_pr_candidates"]
                or raw.get("recommended_next_step") != canonical_row["recommended_next_step"]
                or raw.get("evidence_gaps") != canonical_row["evidence_gaps"]
                or raw.get("claim_authorized") is not False
                or raw.get("merge_authorized") is not False
                or raw.get("deploy_authorized") is not False):
            raise ValueError("raw board diverged from canonical validated issue")
        title = raw.get("title")
        if type(title) is not str or not 1 <= len(title) <= 180:
            raise ValueError("raw source issue title missing or unbounded")
        raw_copy = dict(raw)
        for field in ("created_at", "updated_at"):
            if raw.get(field) is not None:
                stamp = canonical_issue_clock(raw[field])
                raw_copy[field] = stamp.isoformat(timespec="seconds")
        if raw_copy.get("created_at") and raw_copy.get("updated_at"):
            if canonical_issue_clock(raw_copy["created_at"]) > canonical_issue_clock(raw_copy["updated_at"]):
                raise ValueError("issue activity clocks are inconsistent")
        raw_by_issue[issue] = raw_copy
    saved_gates = saved_gates or {}
    if type(saved_gates) is not dict or len(saved_gates) > 5:
        raise ValueError("invalid saved PR gate map")
    exact = {}
    for item in collision["project_views"]:
        pid = item.get("project")
        repo = item.get("repository")
        if (pid not in selected or pid in exact or repo != canonical[pid]
                or item.get("source_status") not in (
                    "unavailable", "partial_open_pr_sample", "complete_open_pr_sample_only")
                or type(item.get("sample_complete")) is not bool):
            raise ValueError("untrusted project collision source")
        related = {}
        for pair in item.get("shown_collision_pairs", []):
            if type(pair) is not dict:
                raise ValueError("invalid PR collision record")
            a, b = pair.get("left"), pair.get("right")
            if type(a) is not dict or type(b) is not dict:
                raise ValueError("invalid PR collision identity")
            for own, other in ((a, b), (b, a)):
                link, ref = own.get("url"), other.get("url")
                if (type(link) is not str or type(ref) is not str
                        or not link.startswith("https://github.com/" + repo + "/pull/")
                        or not ref.startswith("https://github.com/" + repo + "/pull/")
                        or type(own.get("number")) is not int
                        or link != "https://github.com/" + repo + "/pull/" + str(own["number"])
                        or type(other.get("number")) is not int
                        or ref != "https://github.com/" + repo + "/pull/" + str(other["number"])):
                    raise ValueError("cross-repository collision data")
                related.setdefault(link, set()).add(ref)
        mentions = {}
        mentions_stable = (
            item.get("issue_reference_snapshot_stable") is True
            and item.get("source_status") != "unavailable")
        if item.get("issue_reference_source_covered") is True and mentions_stable:
            recorded = item.get("observed_issue_to_pr_mentions_unverified", [])
            # The producer admits at most 48 PRs × 32 issue references.
            # Reject anything beyond that actual hard source maximum, not
            # a smaller arbitrary limit that breaks busy projects.
            if type(recorded) is not list or len(recorded) > 48 * 32:
                raise ValueError("unbounded cross-PR issue reference evidence")
            for ref in recorded:
                if type(ref) is not dict:
                    raise ValueError("invalid observed issue PR reference")
                number, link = ref.get("issue_number"), ref.get("pr_url")
                if (type(number) is not int or not 1 <= number <= 99999999
                        or type(link) is not str
                        or PR.fullmatch(link) is None
                        or not link.startswith("https://github.com/" + repo + "/pull/")
                        or type(ref.get("pr_head_sha")) is not str
                        or not re.fullmatch(r"[a-f0-9]{40}", ref["pr_head_sha"])
                        or ref.get("relation") != "unverified_text_mention_only"
                        or ref.get("owner_verified") is not False
                        or ref.get("fix_verified") is not False):
                    raise ValueError("ambiguous issue reference or source PR identity")
                mentions.setdefault(number, set()).add(link)
        heads = item.get("sampled_open_pr_heads", [])
        # The portfolio producer exports the exact bounded open-PR sample
        # under sampled_open_pr_heads. Validate it before using updated_at;
        # absence of a candidate from an incomplete sample remains UNKNOWN.
        if type(heads) is not list or len(heads) > MAX_PRS_PER_PROJECT:
            raise ValueError("malformed sampled open PR metadata")
        observed_pr_heads = []
        seen_head_urls = set()
        for head in heads:
            if type(head) is not dict:
                raise ValueError("invalid sampled open PR receipt")
            number, url = head.get("number"), head.get("url")
            if (type(number) is not int or not 1 <= number <= 99999999
                    or type(url) is not str
                    or url != "https://github.com/" + repo + "/pull/" + str(number)
                    or url in seen_head_urls
                    or type(head.get("head_sha")) is not str
                    or re.fullmatch(r"[a-f0-9]{40}", head["head_sha"]) is None
                    or type(head.get("base_sha")) is not str
                    or re.fullmatch(r"[a-f0-9]{40}", head["base_sha"]) is None):
                raise ValueError("invalid sampled open PR identity")
            updated = head.get("updated_at")
            normalized_update = (canonical_issue_clock(updated).isoformat(timespec="seconds")
                                 if updated is not None else None)
            seen_head_urls.add(url)
            observed_pr_heads.append({**head, "updated_at": normalized_update})
        exact[pid] = {"source": item["source_status"],
                      "complete": item["sample_complete"],
                      "observed_pr_heads": observed_pr_heads,
                      "collisions": related,
                      "mentioned_issues": mentions,
                      "mention_snapshot_stable": mentions_stable,
                      "mention_sample_complete":
                          item.get("issue_reference_sample_complete") is True,
                      "observed_open_prs": item.get("observed_open_prs"),
                      "observed_pairs": item.get("observed_collision_pairs"),
                      "known_disjoint": {
                          p.get("url") for p in item.get(
                              "unpaired_open_prs_not_claim_verified", [])
                          if type(p) is dict and type(p.get("url")) is str
                      } if item["sample_complete"] else set()}
    if len(exact) != len(selected):
        raise ValueError("missing project collision coverage")
    try:
        source_at = datetime.fromisoformat(source["observed_at_utc"].replace("Z", "+00:00"))
        overlay_at = datetime.fromisoformat(collision["observed_at_utc"].replace("Z", "+00:00"))
        if source_at.tzinfo is None or overlay_at.tzinfo is None:
            raise ValueError("source clock has no UTC offset")
        delta = abs((overlay_at - source_at).total_seconds())
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid source time") from None
    coherent = delta <= 600
    issue_rows = []
    for card in source["cards"][:MAX_ISSUES]:
        if focus_issue is not None and card["issue"] != focus_issue:
            continue
        pid = card["project"]
        if pid not in exact:
            continue
        state = exact[pid]
        repo = canonical[pid]
        if not card["url"].startswith("https://github.com/" + repo + "/issues/"):
            raise ValueError("source issue not from canonical project repository")
        original = list(card["open_pr_candidates"])
        additional = (sorted(state["mentioned_issues"].get(
            int(card["issue"].rsplit("#", 1)[-1]), set()) - set(original))
            if coherent and state["mention_snapshot_stable"] else [])
        issue_number = int(card["issue"].rsplit("#", 1)[-1])
        current_mentions = (
            sorted(state["mentioned_issues"].get(issue_number, set()))
            if coherent and state["mention_snapshot_stable"] else [])
        duplicate_current_mentions = len(current_mentions) > 1
        references = (original + additional)[:MAX_LINKED_PRS_PER_ISSUE]
        refs = []
        for link in references:
            m = PR.fullmatch(link)
            if m is None or link.rsplit("/pull/", 1)[0] != "https://github.com/" + repo:
                raise ValueError("untrusted cross-project PR candidate")
            rivals = sorted(state["collisions"].get(link, set()))
            if rivals:
                status = "shared_file_paths_observed"
            elif coherent and state["complete"] and link in state["known_disjoint"]:
                status = "no_shared_paths_in_complete_open_pr_snapshot_only"
            else:
                status = "scope_unknown_or_uninspected"
            gates = saved_gates.get(link)
            if gates is not None and (not coherent or
                    gates.get("merge_authorized") is not False):
                gates = None
            refs.append({"url": link, "file_scope_state": status,
                         "issue_link_source": (
                             "existing_product_issue_board_reference"
                             if link in original else
                             "observed_current_open_pr_text_mention_partial_source"),
                         "mention_inventory_complete":
                             state["mention_sample_complete"] if link not in original
                             else False,
                         "overlap_with_open_prs": rivals[:16],
                         "saved_exact_head_gates": gates,
                         "current_branch_required_checks_verified": False,
                         "independent_review_current_head_verified": False,
                         "collision_is_merge_failure": False,
                         "claim_authorized": False,
                         "merge_authorized": False})
        if card["url"] in OWNER_ONLY:
            route = "authorized_infrastructure_owner_only"
        elif refs and any(p["file_scope_state"] == "shared_file_paths_observed" for p in refs):
            route = "coordinate_existing_pr_file_overlap_and_owner"
        elif duplicate_current_mentions:
            route = "coordinate_multiple_existing_pr_issue_mentions"
        elif refs:
            route = "inspect_existing_pr_and_owner_gates_first"
        else:
            route = "verify_live_owner_then_product_implementation"
        activity = classify_issue_activity(
            raw_by_issue[card["issue"]].get("updated_at"),
            [ref["url"] for ref in refs],
            state["observed_pr_heads"],
            max(source_at, overlay_at).isoformat(timespec="seconds"),
            coherent, state["complete"], state["mention_sample_complete"],
        )
        issue_rows.append({
            "activity": activity,
            "priority": card["priority"], "project": pid,
            "issue": card["issue"], "url": card["url"],
            "title": raw_by_issue[card["issue"]]["title"],
            "existing_prs": refs, "next_action": route,
            "multiple_open_pr_issue_text_mentions_observed": duplicate_current_mentions,
            "open_pr_issue_text_mentions_in_sample": len(current_mentions),
            "duplicate_mentions_sample_complete": state["mention_sample_complete"],
            "duplicate_source_links": current_mentions[:MAX_LINKED_PRS_PER_ISSUE],
            "next_action_nl": NEXT_ACTIONS_NL[route],
            "source_recommended_next_step": card["recommended_next_step"],
            "source_evidence_gaps": card["evidence_gaps"],
            "project_issue_coverage": card["issue_coverage"],
            "additional_current_pr_mentions_truncated":
                len(original) + len(additional) > MAX_LINKED_PRS_PER_ISSUE,
            "issue_evidence_stale": not coherent,
            "observed_claim_context": card["claim_context"],
            "owner_verified": False, "claim_authorized": False,
            "merge_authorized": False, "deploy_authorized": False,
        })
    issue_rows.sort(key=lambda x: (P[x["priority"]], x["project"], x["issue"]))
    # Integrate the already-merged canonical product board's saved operational
    # signals (queue retries, expiries, and positive claim observations).
    # This does NOT read production SQLite or determine owner availability.
    source_operations = {}
    for item in raw_board.get("project_operations", []):
        if type(item) is not dict or item.get("action_authorized") is not False:
            raise ValueError("unexpected authority in saved operator context")
        pid = item.get("project")
        if type(pid) is not str or pid in source_operations:
            raise ValueError("duplicate project operational evidence")
        source_operations[pid] = item
    project_signals = []
    for pid in selected:
        row = source_operations.get(pid)
        if row is None:
            project_signals.append({
                "project": pid, "source_status": "not_provided",
                "observed_claim_ids": None, "expired_leases_observed": None,
                "high_claim_cycles_observed": None, "human_next_step": None,
                "active_owner_verified": False, "recovery_authorized": False,
            })
            continue
        ids = row.get("observed_claim_ids")
        if ids is not None and (
                type(ids) is not list or len(ids) > 150
                or any(type(i) is not int or i < 0 for i in ids)):
            raise ValueError("untrusted observed claim identifiers")
        queue = row.get("queue_observation")
        if queue is not None and type(queue) is not dict:
            raise ValueError("untrusted saved queue aggregation")
        queue = queue or {}
        high = queue.get("high_claim_attempts")
        expired = queue.get("expired_active_leases")
        if any(v is not None and (
                type(v) is not int or v < 0 or v > 100000000)
               for v in (high, expired)):
            raise ValueError("unbounded saved queue incident count")
        action = row.get("recommended_next_step")
        if type(action) is not str or len(action) > 650:
            raise ValueError("invalid saved next owner step")
        project_signals.append({
            "project": pid,
            "source_status": "saved_operator_observation_only",
            "observed_claim_ids": ids,
            "expired_leases_observed": expired,
            "high_claim_cycles_observed": high,
            "human_next_step": action,
            "active_owner_verified": False, "recovery_authorized": False,
        })
    branch_raw = raw_board.get("coverage", {}).get("branches")
    branch_context = None
    if branch_raw is not None:
        if (type(branch_raw) is not dict
                or type(branch_raw.get("coverage_complete")) is not bool
                or type(branch_raw.get("recent_overlap_candidates")) is not int
                or not 0 <= branch_raw["recent_overlap_candidates"] <= 12
                or branch_raw.get("repository") != "Zennay/zCloud"):
            raise ValueError("invalid saved un-PR'd branch evidence")
        branch_context = {
            "repository": "Zennay/zCloud",
            "historical_branch_source_fresh":
                raw_board.get("fresh", {}).get("branches") is True,
            "historical_branch_observed_at_utc":
                raw_board.get("source_times_utc", {}).get("branches"),
            "recent_overlap_candidates_observed":
                branch_raw["recent_overlap_candidates"],
            "sample_complete": branch_raw["coverage_complete"],
            "active_ownership_verified": False,
            "source": "optional_saved_branch_owner_audit_not_live",
        }
    return {
        "schema": "zsentinel-integrated-issue-pr-scope-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "product_issue_source_sha256": source_sha,
        "issue_source_mode": source_mode,
        "focused_issue": focus_issue,
        "focus_found_in_bounded_issue_source": True if focus_issue else None,
        "source_issue_observed_at_utc": source["observed_at_utc"],
        "saved_claim_source_observed_at_utc":
            raw_board.get("source_times_utc", {}).get("claims"),
        "saved_queue_source_observed_at_utc":
            raw_board.get("source_times_utc", {}).get("retry"),
        "source_pr_observed_at_utc": collision["observed_at_utc"],
        "source_clocks_within_10_minutes": coherent,
        "chosen_projects": selected,
        "saved_operational_context": project_signals,
        "saved_unpr_branch_context": branch_context,
        "github_scope_by_project": [{
            "project": pid, "source_status": exact[pid]["source"],
            "pr_sample_complete": exact[pid]["complete"],
            "observed_open_prs": exact[pid]["observed_open_prs"],
            "observed_overlap_pairs": exact[pid]["observed_pairs"],
            "issue_mention_window_stable":
                exact[pid]["mention_snapshot_stable"],
            "issue_mention_inventory_complete":
                exact[pid]["mention_sample_complete"],
        } for pid in selected],
        "issues": issue_rows,
        "claims_verified": False,
        "live_runner_recovered": False,
        "automatic_dispatch": False,
        "may_take_ownership": False,
        "merge_authorized": False, "deploy_authorized": False,
        "writes_performed": False,
    }


def operator_inline(value, markdown=False):
    """Keep public issue titles and saved human notes on ONE inert output line."""
    if type(value) is not str:
        raise ValueError("invalid operator text source")
    single = "".join(
        " " if unicodedata.category(char).startswith("C") else char
        for char in value)
    if markdown:
        # This is an operator Markdown handoff, not a source of privileged
        # commands or HTML. Canonical GitHub URLs are assembled separately.
        single = (single.replace("\\", "\\\\").replace("<", "&lt;")
                  .replace(">", "&gt;").replace("|", "\\|")
                  .replace("[", "\\[").replace("]", "\\]")
                  .replace("`", "\\`"))
    return single


def output_text(report, markdown=False):
    intro = ("# zSentinel · Gecombineerde product- en PR-bestandsscope"
             if markdown else "zSentinel | productissues + PR-bestandsscope")
    lines = [intro, "Issue-bron: " + report["issue_source_mode"],
             "Actuele PR-check/review-opvraag: " +
             report["current_head_gate_observations"]["mode"] +
             " · zonder mergebevoegdheid.",
             "Issue-bronmoment: " + report["source_issue_observed_at_utc"],
             "PR-bestandsmoment: " + report["source_pr_observed_at_utc"],
             "Issue-bron SHA256: " + report["product_issue_source_sha256"],
             "Bronmomenten ≤10 min uiteen: " +
             ("ja" if report["source_clocks_within_10_minutes"] else "NEE — oude bron is geen live claim")]
    if report.get("saved_gate_batch_reconciliation"):
        b = report["saved_gate_batch_reconciliation"]
        lines.append("Opgeslagen gatebatches: " + str(b["observed_prs"]) + "/" +
                     str(b["total_manifest_prs"]) + " PRs; ontbrekende batches: " +
                     str(b["missing_batches"]) + "; bron >15 min: " +
                     str(b["stale_over_15_minutes_prs"]) +
                     "; batchmomenten onderling binnen 10 minuten: " +
                     str(b["cross_batch_observation_within_10_minutes"]) +
                     "; HEADs niet live opnieuw geverifieerd; geen mergetoestemming.")
        for action in b["manual_review_queue_preview"]:
            link = ("[" + action["url"] + "](" + action["url"] + ")"
                    if markdown else action["url"])
            lines.append("  PR-werkbak " + str(action["manifest_position"]) +
                         ": " + link + " · [" +
                         action["historic_gate_category"] + "] " +
                         action["human_next_gate"])
    for pid in report["github_scope_by_project"]:
        lines.append(pid["project"] + ": PR-bron " + pid["source_status"] +
                     ", waargenomen gedeelde padenparen " +
                     str(pid["observed_overlap_pairs"]))
    for item in report["saved_operational_context"]:
        lines.append("Operatiebron " + item["project"] + ": " +
                     item["source_status"] +
                     ", aflopende leases in opgeslagen bron: " +
                     str(item["expired_leases_observed"]) +
                     ", hoge claimcycli: " +
                     str(item["high_claim_cycles_observed"]) +
                     ", actuele eigenaar: ONBEKEND")
        if item["human_next_step"]:
            lines.append("  Bronadvies: " +
                         operator_inline(item["human_next_step"], markdown))
    if report["saved_unpr_branch_context"] is not None:
        branches = report["saved_unpr_branch_context"]
        lines.append("PR-loze branches (opgeslagen, geen live eigenaren): " +
                     str(branches["recent_overlap_candidates_observed"]) +
                     " overlapkandidaten; compleet: " +
                     str(branches["sample_complete"]) +
                     "; bron vers volgens originele board: " +
                     str(branches["historical_branch_source_fresh"]))
    if report.get("human_work_inbox"):
        inbox = report["human_work_inbox"]
        lines.extend([
            "", "MERGE-EERST · P0/P1 MENSELIJKE WERKBAK",
            "Bestaande PRs voor " + str(inbox["existing_pr_first_issues"]) +
            " issue(s) gevonden. Geen enkele kaart verleent claim, merge of dispatch.",
        ])
        for item in inbox["cards"]:
            issue_ref = ("[" + item["issue"] + "](" + item["issue_url"] + ")"
                         if markdown else item["issue_url"])
            lines.append(
                "  " + item["priority"] + " | " + item["project"] +
                " | " + issue_ref + " | " + item["category_nl"])
            if item["candidate_existing_pr_url"]:
                url = item["candidate_existing_pr_url"]
                lines.append("    PR eerst: " +
                             ("[" + url + "](" + url + ")" if markdown else url))
            lines.append("    Volgende stap: " + item["human_next_step_nl"])
    for row in report["issues"]:
        if report.get("historical_pr_delta") is not None:
            lines.append("  PR-historie: " + str(row["historical_linked_prs_with_changes"]) +
                         " gelinkte PR(s) met positieve bronwijziging; " +
                         ("volledige" if row["historical_pr_inventory_comparable"] else
                          "ONVOLLEDIGE") + " bron, geen fixbewijs.")
        ref = ("[" + row["issue"] + "](" + row["url"] + ")" if markdown
               else row["url"])
        lines.append(row["priority"] + " | " + row["project"] + " | " +
                     ref + " | " + operator_inline(row["title"], markdown) +
                     " | " + row["next_action_nl"])
        activity = row["activity"]
        lines.append("  Activiteit: " + activity["label_nl"] +
                     " · issue update " + str(activity["issue_updated_at"] or "ONBEKEND") +
                     " (" + str(activity["issue_inactivity_hours"]) + " uur geleden)" +
                     " · laatste gekoppelde open PR " +
                     str(activity["latest_linked_open_pr_updated_at"] or "ONBEKEND") +
                     " (" + str(activity["latest_linked_open_pr_inactivity_hours"]) + " uur geleden)" +
                     " · dekking " + activity["linked_pr_activity_coverage"])
        lines.append("  " + activity["note_nl"])
        if row["multiple_open_pr_issue_text_mentions_observed"]:
            lines.append("  BESTAANDE PR's eerst: " +
                         str(row["open_pr_issue_text_mentions_in_sample"]) +
                         " huidige PR-teksten noemen dit issue; fix/eigenaar ONBEKEND, bron volledig: " +
                         str(row["duplicate_mentions_sample_complete"]))
        lines.append("  Advies uit bestaande bron: " +
                     operator_inline(row["source_recommended_next_step"], markdown))
        for gap in row["source_evidence_gaps"]:
            lines.append("  Onbekend: " + operator_inline(gap, markdown))
        for pr in row["existing_prs"]:
            link = ("[" + pr["url"] + "](" + pr["url"] + ")" if markdown else pr["url"])
            lines.append("  " + link + " · " + pr["file_scope_state"] +
                         " · " + pr["issue_link_source"])
            for event in pr.get("historical_source_events", []):
                lines.append("    HISTORISCH: " + event["text_nl"] +
                             (" · " + event["related_pr_url"] if event["related_pr_url"] else "") +
                             " · geen eigenaar of merge geverifieerd")
            live_gate = pr.get("live_exact_head_review_checks_observation")
            if live_gate is not None:
                if live_gate.get("status") == "observed_current_head_partial_gate_evidence":
                    lines.append("    HUIDIGE PR-HEAD " +
                                 live_gate["head_sha_observed"][:12] +
                                 " · checks lopend: " +
                                 str(live_gate["observed_checks_pending"]) +
                                 " · checks niet geslaagd: " +
                                 str(live_gate["observed_checks_not_successful"]) +
                                 " · exact-head onafhankelijke approvals gezien: " +
                                 str(live_gate["observed_independent_exact_head_approvals"]) +
                                 " · legacy statussen: " +
                                 str(live_gate.get("legacy_status_contexts_observed", 0)) +
                                 " contexten (" +
                                 str(live_gate.get("legacy_status_pending_observed", 0)) +
                                 " lopend, " +
                                 str(live_gate.get("legacy_status_failure_or_error_observed", 0)) +
                                 " mislukt) · bron onvolledig: " +
                                 str(live_gate.get("legacy_status_source_may_be_truncated", True)) +
                                 " · vereiste branch/reviewregels ONBEKEND")
                    lines.append("    Actie: " + GATE_STEPS_NL.get(
                        live_gate.get("human_next_gate_step"),
                        "Volledige actuele bron of bevoegde eigenaar ontbreekt."))
                else:
                    lines.append("    Actuele PR-bron onbekend of HEAD veranderd; geen mergebesluit.")
            bundle_gate = pr.get("saved_reconciled_gate_observation")
            if bundle_gate is not None:
                lines.append("    OPGENOMEN SAVED gate-batch: [" +
                             bundle_gate["historic_gate_category"] + "] " +
                             bundle_gate["next_human_gate"] +
                             " · bron oud: " +
                             str(bundle_gate["saved_source_stale_over_15_minutes"]) +
                             " · HEAD niet live bevestigd; merge NEE")
            gates = pr["saved_exact_head_gates"]
            if gates is not None:
                lines.append("    Historische exacte HEAD " + gates["observed_exact_head_sha"] +
                             " · checks: " + str(gates["saved_native_checks_success"]) +
                             " · observed approvals: " +
                             str(gates["saved_independent_approval_count_unverified"]) +
                             " (geen actuele merge-autorisatie)")
            for other in pr["overlap_with_open_prs"]:
                lines.append("    Gedeeld bestandspad met " + other)
    if report.get("historical_pr_delta"):
        lines.extend(["", render_delta(report["historical_pr_delta"],
                                       "markdown" if markdown else "text")])
    lines.extend(["", "Gedeeld pad ≠ mergeconflict. Geen claims, PR-less ownership, CI of review geautoriseerd.",
                  "P0 infrastructuur/billing uitsluitend bevoegd eigenaar. Geen writes, retries, merge of deploy."])
    return "\n".join(lines) + "\n"


def output_html(report):
    delta_panel = render_delta(report.get("historical_pr_delta"), "html")
    work_panel = ""
    if report.get("human_work_inbox"):
        inbox = report["human_work_inbox"]
        signals = (
            "quiet_over_7_days_observed_claims_unknown",
            "quiet_over_7_days_partial_pr_coverage",
            "recent_issue_activity_observed",
            "recent_linked_pr_activity_observed",
            "activity_timestamp_unknown",
            "source_clock_incoherent",
        )
        activity_counts = {
            signal: sum(item.get("activity_signal") == signal for item in inbox["cards"])
            for signal in signals
        }
        activity_summary = (
            "Activiteit: stil >7d/compleet " + str(activity_counts[signals[0]]) +
            " · stil >7d/onvolledig " + str(activity_counts[signals[1]]) +
            " · recent issue " + str(activity_counts[signals[2]]) +
            " · recent PR " + str(activity_counts[signals[3]]) +
            " · onbekend/incoherent " +
            str(activity_counts[signals[4]] + activity_counts[signals[5]]) +
            ". Geen bewijs van stilstand of vrije claim."
        )
        work_rows = []
        for item in inbox["cards"]:
            issue_url = escape(item["issue_url"], quote=True)
            issue_title = escape(item["title"])
            anchor = ("issue-card-" + item["project"] + "-" +
                      item["issue"].rsplit("#", 1)[-1])
            source = ("<a href='" + escape(item["candidate_existing_pr_url"], quote=True) +
                      "' rel='noopener noreferrer'>Open bestaande PR</a>"
                      if item["candidate_existing_pr_url"] else
                      "Geen PR in beperkte bron; eigenaar onbekend")
            work_rows.append(
                "<tr><td><strong>" + escape(item["priority"]) + "</strong> " +
                escape(item["project"]) + "</td><td><a href='" +
                issue_url + "' rel='noopener noreferrer'>" + issue_title +
                "</a> · <a href='#" + escape(anchor, quote=True) +
                "' data-open-issue='" + escape(anchor, quote=True) +
                "'>Bekijk kaart</a></td><td>" + source +
                "</td><td><strong>" +
                escape(item["category_nl"]) + "</strong><br>" +
                escape(item["human_next_step_nl"]) +
                "<br><small>Activiteitssignaal: " + escape(item["activity_label_nl"]) +
                " · issue-update " + escape(str(item["issue_updated_at"] or "ONBEKEND")) +
                " (" + escape(str(item["issue_inactivity_hours"])) + " uur geleden)" +
                " · laatste gekoppelde open PR " +
                escape(str(item["latest_linked_open_pr_updated_at"] or "ONBEKEND")) +
                " (" + escape(str(item["latest_linked_open_pr_inactivity_hours"])) + " uur geleden)" +
                " · PR-activiteitsdekking " + escape(item["linked_pr_activity_coverage"]) +
                " · geen bewijs van stilstand of vrije claim</small>" +
                "<br><small>PR-bestandsscope: " +
                ("beperkte bron compleet" if item["pr_file_scope_sample_complete"]
                 else "ONVOLLEDIG") + " · PR-issueverwijzingen: " +
                ("beperkte bron compleet" if item["issue_reference_sample_complete"]
                 else "ONVOLLEDIG") +
                " · actuele PR-gatebron: " +
                escape(item["candidate_exact_head_gate_state"]) +
                " · claims niet geverifieerd</small></td></tr>")
        pr_group_items = []
        for group in inbox["pr_groups"]:
            linked = "".join(
                "<li><a href='" + escape(url, quote=True) +
                "' rel='noopener noreferrer'>Issue #" +
                escape(url.rsplit("/", 1)[-1]) + "</a></li>"
                for url in group["linked_issue_urls_in_source"])
            pr_group_items.append(
                "<li><strong>" + escape(group["highest_priority"]) +
                "</strong> · <a href='" + escape(group["pr_url"], quote=True) +
                "' rel='noopener noreferrer'>Bestaande PR #" +
                escape(group["pr_url"].rsplit("/", 1)[-1]) + "</a> · " +
                str(group["linked_issue_count_observed"]) +
                " issuelinks; GEEN fix- of mergebewijs<details>" +
                "<summary>Bekijk betrokken issues</summary><ul>" +
                linked + "</ul></details></li>")
        group_panel = (
            "<details><summary>Gedeelde PR-opvolging: " +
            str(inbox["distinct_existing_pr_groups_observed"]) +
            " verschillende bestaande PRs (" +
            str(inbox["multi_issue_existing_pr_groups_observed"]) +
            " met meerdere issuelinks)</summary><p>Een bestaande PR wordt één "
            "broncluster, niet meerdere onafhankelijke fixes.</p><ol>" +
            "".join(pr_group_items) + "</ol></details>"
            if pr_group_items else "")
        work_panel = (
            "<section id='merge-first-inbox' aria-label='P0/P1 merge-eerst werkbak'>"
            "<h2>Merge-eerst · menselijke P0/P1 werkbak</h2><p>" +
            str(inbox["issue_count"]) + " issues geprioriteerd; bij " +
            str(inbox["existing_pr_first_issues"]) +
            " is een bestaande PR-link in de bron gezien. "
            "Bronsteekproeven zijn niet volledig en huidige claims/branchregels "
            "zijn NIET geverifieerd. Geen automatisch werk, merge of deploy.</p>"
            "<p>" + escape(activity_summary) + "</p>"
            "<div class='scroll'><table><thead><tr><th>Urgentie</th>"
            "<th>Open issue</th><th>PR eerst</th><th>Menselijke actie</th>"
            "</tr></thead><tbody>" + "".join(work_rows) +
            "</tbody></table></div>" + group_panel + "</section>"
        )
    signals = []
    for item in report["saved_operational_context"]:
        signals.append(
            "<tr><td>" + escape(item["project"]) + "</td><td>" +
            escape(item["source_status"]) + "</td><td>" +
            escape(str(item["high_claim_cycles_observed"])) + "</td><td>" +
            escape(str(item["expired_leases_observed"])) + "</td><td>" +
            escape(item["human_next_step"] or "Niet bekend") +
            "</td></tr>")
    branch_context = report["saved_unpr_branch_context"]
    branch_note = (
        "<p>zCloud PR-loze-branchobservatie: " +
        str(branch_context["recent_overlap_candidates_observed"]) +
        " mogelijke overlappen (opgeslagen bron, compleet: " +
        str(branch_context["sample_complete"]) +
        "; volgens originele board vers: " +
        str(branch_context["historical_branch_source_fresh"]) +
        "; geen actuele eigenaarsbevestiging).</p>"
        if branch_context else
        "<p>PR-loze-branchwaarneming ontbreekt; geen eigenaarsconclusie.</p>")
    signals_html = (
        "<section aria-label='Opgeslagen operationele bron'>"
        "<h2>Operationele context uit bestaande Product Delivery Board</h2>"
        "<p>Dit zijn alleen eerder opgeslagen waarnemingen, geen live "
        "SQLite-, lease-, runner- of productieoverzicht.</p>"
        "<div class='scroll'><table><thead><tr><th>Project</th><th>Bron</th>"
        "<th>Claimcycli</th><th>Verlopen leases</th><th>Bronadvies</th>"
        "</tr></thead><tbody>" + "".join(signals) +
        "</tbody></table></div>" + branch_note + "</section>"
    )
    bundle_summary = ""
    if report.get("saved_gate_batch_reconciliation"):
        b = report["saved_gate_batch_reconciliation"]
        action_items = []
        for action in b["manual_review_queue_preview"]:
            url = escape(action["url"], quote=True)
            action_items.append(
                "<li><a rel='noopener noreferrer' href='" + url + "'>" +
                str(action["manifest_position"]) + ". " + url +
                "</a> · " + escape(action["historic_gate_category"]) +
                " · " + escape(action["human_next_gate"]) +
                " · uitsluitend historische triage, geen mergebevoegdheid.</li>")
        action_list = ("<details><summary>Menselijke merge-first werkbak (" +
                       str(len(action_items)) + " zichtbaar)</summary><ol>" +
                       "".join(action_items) + "</ol><p>" +
                       str(b["manual_review_queue_hidden_after_24"]) +
                       " overige kandidaat-PR's niet in deze compacte lijst.</p></details>")
        bundle_summary = (
            "<section><h2>Opgeslagen PR-gatebatches</h2><p>Brongebonden " +
            str(b["observed_prs"]) + "/" + str(b["total_manifest_prs"]) +
            " PRs · ontbrekende batches: " +
            escape(str(b["missing_batches"])) + " · verouderd: " +
            str(b["stale_over_15_minutes_prs"]) +
            " · batchmomenten coherent: " +
            escape(str(b["cross_batch_observation_within_10_minutes"])) +
            ". Alle HEADs en verplichte branchregels moeten live opnieuw worden "
            "gecontroleerd vóór merge. Geen claim- of mergevrijgave.</p>" +
            action_list + "</section>"
        )
    cards = []
    for row in report["issues"]:
        prs = []
        for pr in row["existing_prs"]:
            history_links = "".join(
                "<li>" + escape(event["text_nl"]) +
                (" · <a rel='noopener noreferrer' href='" +
                 escape(event["related_pr_url"], quote=True) + "'>" +
                 escape(event["related_pr_url"]) + "</a>" if
                 event["related_pr_url"] else "") + "</li>"
                for event in pr.get("historical_source_events", []))
            rivals = "".join(
                "<li><a rel='noopener noreferrer' href='" + escape(other, quote=True) +
                "'>" + escape(other) + "</a></li>"
                for other in pr["overlap_with_open_prs"])
            live_gate = pr.get("live_exact_head_review_checks_observation")
            live_note = (
                "<p><strong>ACTUELE PR-HEAD " +
                escape(live_gate["head_sha_observed"][:12]) +
                "</strong> · checks lopend: " +
                str(live_gate["observed_checks_pending"]) +
                " · checks niet geslaagd: " +
                str(live_gate["observed_checks_not_successful"]) +
                " · onafhankelijke HEAD-approvals gezien: " +
                str(live_gate["observed_independent_exact_head_approvals"]) +
                " · legacy statuscontexten: " +
                str(live_gate.get("legacy_status_contexts_observed", 0)) +
                " (" + str(live_gate.get("legacy_status_pending_observed", 0)) +
                " lopend, " +
                str(live_gate.get("legacy_status_failure_or_error_observed", 0)) +
                " niet geslaagd)" +
                (" · statusbron mogelijk afgekapt" if
                 live_gate.get("legacy_status_source_may_be_truncated", True) else "") +
                ". Vereiste branch/reviewregels ONBEKEND; geen mergevrijgave.</p>" +
                "<p><strong>Menselijke gate-stap:</strong> " +
                escape(GATE_STEPS_NL.get(
                    live_gate.get("human_next_gate_step"),
                    "Volledige actuele bron of bevoegde eigenaar ontbreekt.")) +
                "</p>"
                if live_gate is not None and
                live_gate.get("status") == "observed_current_head_partial_gate_evidence"
                else "<p>Actuele PR-bron onbereikbaar of HEAD gewijzigd; geen mergebesluit.</p>"
                if live_gate is not None else "")
            bundle_gate = pr.get("saved_reconciled_gate_observation")
            batch_note = (
                "<p>Gecombineerde opgeslagen gatebatch: <strong>" +
                escape(bundle_gate["historic_gate_category"]) + "</strong>" +
                " · " + escape(bundle_gate["next_human_gate"]) +
                " · verouderd: " +
                escape(str(bundle_gate["saved_source_stale_over_15_minutes"])) +
                ". Deze HEAD is NIET live bevestigd; geen mergevrijgave.</p>"
                if bundle_gate is not None else "")
            gates = pr["saved_exact_head_gates"]
            gate_note = ("<p>Opgeslagen HEAD " +
                         escape(gates["observed_exact_head_sha"][:12]) +
                         " · native checks: " +
                         ("geslaagd (historische waarneming)" if gates["saved_native_checks_success"]
                          else "niet volledig geslaagd (historische waarneming)") +
                         " · waargenomen approvals: " +
                         str(gates["saved_independent_approval_count_unverified"]) +
                         ". Verplichte huidige-head rules/review niet geverifieerd.</p>"
                         if gates is not None else
                         "<p>Geen verse, exact opgeslagen check/review-waarneming.</p>")
            prs.append(
                "<li><a rel='noopener noreferrer' href='" +
                escape(pr["url"], quote=True) + "'>" + escape(pr["url"]) +
                "</a> · " + escape(pr["file_scope_state"]) +
                " · " + escape(pr["issue_link_source"]) +
                gate_note + batch_note + live_note +
                ("<details><summary>Historische PR-verandering</summary><ul>" +
                 history_links + "</ul><p>Geen fix of release geverifieerd.</p></details>"
                 if history_links else "") +
                ("<ul>" + rivals + "</ul>" if rivals else "") + "</li>")
        card_anchor = ("issue-card-" + row["project"] + "-" +
                       row["issue"].rsplit("#", 1)[-1])
        cards.append(
            "<article id='" + escape(card_anchor, quote=True) +
            "' data-priority='" + escape(row["priority"], quote=True) +
            "' data-project='" + escape(row["project"], quote=True) +
            "' data-history='" + ("changed" if row.get("historical_linked_pr_activity_observed")
                                   else "not-observed") +
            "' data-activity='" + escape(row["activity"]["signal"], quote=True) +
            "'><small>" + escape(row["priority"]) + " · " +
            escape(row["project"]) + "</small><h2><a rel='noopener noreferrer' href='" +
            escape(row["url"], quote=True) + "'>" + escape(row["title"]) +
            "</a></h2><p class='meta'>" + escape(row["issue"]) +
            " · issuebron: " + escape(row["project_issue_coverage"]) +
            "</p><p><strong>Activiteitssignaal:</strong> " +
            escape(row["activity"]["label_nl"]) +
            " · issue update " + escape(str(row["activity"]["issue_updated_at"] or "ONBEKEND")) +
            " (" + escape(str(row["activity"]["issue_inactivity_hours"])) + " uur geleden)" +
            " · laatste gekoppelde open PR " +
            escape(str(row["activity"]["latest_linked_open_pr_updated_at"] or "ONBEKEND")) +
            " (" + escape(str(row["activity"]["latest_linked_open_pr_inactivity_hours"])) + " uur geleden)" +
            " · dekking " + escape(row["activity"]["linked_pr_activity_coverage"]) +
            "<br><small>" + escape(row["activity"]["note_nl"]) + "</small></p>" +
            ("<p><strong>PR-beweging:</strong> " +
             str(row["historical_linked_prs_with_changes"]) +
             " bestaande PR(s) gewijzigd in opgeslagen bron; " +
             ("bron volledig." if row["historical_pr_inventory_comparable"] else
              "bron ONVOLLEDIG.") + " Geen herstel- of mergegarantie.</p>"
             if report.get("historical_pr_delta") is not None else "") +
            "<p><strong>Volgende stap:</strong> " +
            escape(row["next_action_nl"]) +
            "</p>" +
            ("<p><strong>Bestaande PR's eerst:</strong> " +
             str(row["open_pr_issue_text_mentions_in_sample"]) +
             " open PR-teksten verwijzen naar dit issue. Geen geverifieerde fix of eigenaar.</p>"
             if row["multiple_open_pr_issue_text_mentions_observed"] else "") +
            "<p>Bronadvies: " + escape(row["source_recommended_next_step"]) +
            "</p><details><summary>Openstaande bronhiaten (" +
            str(len(row["source_evidence_gaps"])) + ")</summary><ul>" +
            "".join("<li>" + escape(gap) + "</li>"
                    for gap in row["source_evidence_gaps"]) + "</ul></details>" +
            ("<ul>" + "".join(prs) + "</ul>" if prs else
             "<p>Geen bestaande PR-link in het bronissue; eigenaarschap blijft onbekend.</p>") +
            "</article>")
    filter_bar = (
        "<div class='filters' aria-label='Productissuefilters'>"
        "<label>Zoek issues of bestaande PR's"
        "<input id='filter-search' type='search' placeholder='Issue, project, PR-nummer'></label>"
        "<label>Prioriteit<select id='filter-priority'>"
        "<option value=''>Alle prioriteiten</option>"
        "<option>P0</option><option>P1</option><option>P2</option>"
        "<option>Unspecified</option></select></label>"
        "<label>Project<select id='filter-project'><option value=''>Alle projecten</option>" +
        "".join("<option value='" + escape(p, quote=True) + "'>" +
                escape(p) + "</option>" for p in report["chosen_projects"]) +
        "</select></label>" +
        "<label>Issue-activiteit<select id='filter-activity'>"
        "<option value=''>Alle signalen</option>"
        "<option value='quiet_over_7_days_observed_claims_unknown'>Geen update &gt;7d · dekking compleet</option>"
        "<option value='quiet_over_7_days_partial_pr_coverage'>Geen update &gt;7d · dekking onvolledig</option>"
        "<option value='recent_issue_activity_observed'>Issue recent bijgewerkt</option>"
        "<option value='recent_linked_pr_activity_observed'>Gekoppelde PR recent bijgewerkt</option>"
        "<option value='unknown'>Activiteit onbekend</option></select></label>" +
        ("<label>PR-historie<select id='filter-history'>" +
         "<option value=''>Alle productissues</option>" +
         "<option value='changed'>Met waargenomen PR-wijziging</option>" +
         "</select></label>" if report.get("historical_pr_delta") else "") +
        "<output id='filter-count' aria-live='polite'></output></div>"
    )
    # A purely local filter over already rendered text; no fetch, storage, or
    # executable content derived from issue titles, PR bodies or repo strings.
    offline_script = """<script>(function(){
'use strict';
const search=document.getElementById('filter-search');
const priority=document.getElementById('filter-priority');
const project=document.getElementById('filter-project');
const activity=document.getElementById('filter-activity');
const history=document.getElementById('filter-history');
const count=document.getElementById('filter-count');
const cards=Array.from(document.querySelectorAll('article[data-project]'));
function update(){
  const term=search.value.trim().toLocaleLowerCase('nl');
  let shown=0;
  for(const card of cards){
    const ok=(!term || card.textContent.toLocaleLowerCase('nl').includes(term)) &&
      (!priority.value || card.dataset.priority===priority.value) &&
      (!project.value || card.dataset.project===project.value) &&
      (!activity.value || (activity.value==='unknown' ? ['source_clock_incoherent','activity_timestamp_unknown'].includes(card.dataset.activity) : card.dataset.activity===activity.value)) &&
      (!history || !history.value || card.dataset.history===history.value);
    card.hidden=!ok;
    if(ok) shown++;
  }
  count.textContent=shown+' van '+cards.length+' productissues zichtbaar';
}
for(const el of [search,priority,project,activity,history])if(el)el.addEventListener('input',update);
for(const link of document.querySelectorAll('a[data-open-issue]')){
  link.addEventListener('click',function(){
    const card=document.getElementById(link.dataset.openIssue);
    if(!card)return;
    search.value=''; priority.value=''; project.value=''; activity.value='';
    if(history)history.value='';
    update();
    card.scrollIntoView({block:'start'});
    const heading=card.querySelector('h2 a');
    if(heading)heading.focus({preventScroll:true});
  });
}
update();
})();</script>"""
    return ("<!doctype html><html lang='nl'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='referrer' content='no-referrer'>"
            "<meta http-equiv='Content-Security-Policy' "
            "content=\"default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; "
            "base-uri 'none'; form-action 'none'; object-src 'none'\">"
            "<title>zSentinel · Product + PR-scope</title>"
            "<style>body{font:16px/1.5 system-ui;background:#101a29;color:#f2f7fd;"
            "max-width:1060px;margin:auto;padding:24px}h1{font-size:28px}"
            "a{color:#b1dbff}article{margin:14px 0;padding:16px;"
            "border:1px solid #556981;border-radius:12px;background:#1c293b}"
            "small{font-weight:bold;color:#dfc794}ul{overflow-wrap:anywhere}"
            "article[hidden]{display:none!important}.meta{color:#bdd0e2;font-size:13px}"
            "details{margin:9px 0}summary{cursor:pointer}"
            ".filters{display:flex;align-items:flex-end;flex-wrap:wrap;gap:12px;"
            "margin:24px 0}.filters label{display:grid;gap:4px}"
            ".filters input,.filters select{background:#203449;border:1px solid #79899e;"
            "padding:11px;color:white;border-radius:7px;font:inherit}"
            ".filters output{font-size:14px;color:#dce7fa}"
            "a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #e4cf84}"
            ".scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}"
            "td,th{padding:10px;border-bottom:1px solid #53647a;text-align:left}"
            "aside{background:#303744;padding:18px;border-left:4px solid #e7c576}"
            "@media(min-width:770px){main{display:grid;grid-template-columns:1fr 1fr;gap:14px}}"
            "</style><h1>zSentinel · Productissues en PR-bestandscope</h1>"
            "<aside>Geen geverifieerd eigenaarschap, live claims, volledige branch-"
            "inventaris, CI, review of merge-/deploybevoegdheid. Een gedeelde "
            "bestandsnaam bewijst geen Git-mergeconflict. Alleen read-only "
            "GitHub-data of een lokaal opgeslagen bronbestand.</aside>"
            "<p>Issuebron: " + escape(report["issue_source_mode"]) +
            " · Projecten: " + escape(", ".join(report["chosen_projects"])) +
            ". Bronmomenten samenhangend: " +
            ("ja" if report["source_clocks_within_10_minutes"] else
             "NEE — controleer eerst nieuwe issue-evidence") +
            "</p><p>Issuebron " + escape(report["source_issue_observed_at_utc"]) +
            " · PR-bestandsbron " + escape(report["source_pr_observed_at_utc"]) +
            "</p>" +
            "<p>Actuele PR-review/checkbron: " +
            escape(report["current_head_gate_observations"]["mode"]) +
            " · verplichte rules en eigenaarrechten blijven onbekend.</p>" +
            signals_html + work_panel + delta_panel + bundle_summary + filter_bar + "<main>" + "".join(cards) + "</main>" +
            offline_script + "<footer><small>Issue SHA256 " +
            escape(report["product_issue_source_sha256"]) + "</small></footer></html>")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source_choice = parser.add_mutually_exclusive_group(required=True)
    source_choice.add_argument("--issue-board", type=Path,
                               help="Bestaande privé JSON-export van de Product Delivery Board")
    source_choice.add_argument("--live-issues", action="store_true",
                               help="Lees open GitHub productissues nu; alleen met --project (maximaal drie)")
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--project", action="append")
    parser.add_argument("--max-prs", type=int, default=10)
    parser.add_argument("--compare-previous", type=Path,
                        help="Vergelijk met privé 0600 JSON uit de PR-portfoliobalie")
    parser.add_argument("--inspect-pr-gates", action="store_true",
                        help="Optioneel: current-head checks/reviews van maximaal vier PRs")
    parser.add_argument("--from-review", type=Path,
                        help="Bestaand reviewmanifest voor opgeslagen PR-gate-batches")
    parser.add_argument("--saved-gate-receipt", action="append", type=Path,
                        help="Opgeslagen private gatebatch-bon, herhaal tot 32 keer")
    parser.add_argument("--focus-issue",
                        help="Toon slechts één issue die echt in de begrensde bron voorkomt, bv. Zennay/zCloud#1326")
    parser.add_argument("--output", type=Path,
                        help="Create a NEW 0600 private operator report, never overwrite")
    parser.add_argument("--worklist-limit", type=int, default=20,
                        help="Rows in compact worklist output, maximum 60")
    parser.add_argument("--format", choices=("html", "json", "text", "markdown",
                                             "worklist-markdown", "worklist-text",
                                             "worklist-json", "worklist-html"),
                        default="html")
    args = parser.parse_args(argv)
    try:
        canonical = registry(args.registry)
        if args.inspect_pr_gates and not args.live_issues:
            raise ValueError("--inspect-pr-gates requires --live-issues")
        if bool(args.from_review) != bool(args.saved_gate_receipt):
            raise ValueError("--from-review and --saved-gate-receipt must be supplied together")
        if args.inspect_pr_gates and args.saved_gate_receipt:
            raise ValueError("choose live four-PR inspection OR historical saved batches")
        saved_bundle = None
        if args.from_review:
            # Local only, strict manifest identity and bounded receipt validation.
            from zsentinel_pr_gate_batches import reconcile
            saved_bundle = reconcile(args.from_review, args.saved_gate_receipt)
        if args.live_issues:
            if not args.project or len(args.project) > MAX_PROJECTS:
                raise ValueError("--live-issues needs 1–3 explicit canonical --project values")
            # Reuse the already-merged canonical issue collector and triage
            # policy, rather than creating a second issue/PR search.
            live_registry = canonical_projects(args.registry)
            if canonical != live_registry:
                raise ValueError("canonical registry disagreement")
            if args.max_prs > 12:
                raise ValueError("live issue + PR mode caps --max-prs at 12 per project")
            if args.focus_issue:
                inbox = direct_issue_inbox(
                    canonical, args.project, args.focus_issue,
                    os.environ.get("GITHUB_TOKEN"))
            else:
                inbox = collect_live_issues(canonical, args.project,
                                            os.environ.get("GITHUB_TOKEN"), max_items=60)
            source = prepare_delivery_board(inbox, limit=60)
            # Preserve current issue activity clocks in this private, source-
            # hashed handoff; the canonical triage board does not own them.
            clocks_by_issue = {
                row.get("id"): row for row in inbox.get("items", [])
                if type(row) is dict and type(row.get("id")) is str
            }
            for card in source.get("cards", []):
                if type(card) is not dict:
                    continue
                original = clocks_by_issue.get(card.get("issue"))
                if original is not None:
                    for field in ("created_at", "updated_at"):
                        if type(original.get(field)) is str:
                            card[field] = original[field]
            raw = json.dumps(source, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
            sha = hashlib.sha256(raw).hexdigest()
        else:
            source, sha = read_source_and_sha(args.issue_board)
        view = validated_view(source)
        if args.focus_issue:
            from zsentinel_product_delivery_board import ISSUE as ISSUE_ID
            if not ISSUE_ID.fullmatch(args.focus_issue):
                raise ValueError("invalid repository#issue focus identity")
            matches = [card for card in view["cards"]
                       if card["issue"] == args.focus_issue]
            if len(matches) != 1:
                raise ValueError(
                    "focused issue absent from bounded source; NOT evidence it is resolved")
            view = {**view, "cards": matches}
        projects = _safe_projects(view, canonical, args.project)
        if args.focus_issue and matches[0]["project"] not in projects:
            raise ValueError("focused issue project is not included in the selected repositories")
        if not projects:
            raise ValueError("no canonical product-issue projects in the selected issue source")
        pr_scope = collect_portfolio(projects, canonical, os.environ.get("GITHUB_TOKEN"), args.max_prs)
        historical = (compare(read_private_previous(args.compare_previous), pr_scope)
                      if args.compare_previous is not None else None)
        report = compose(view, sha, canonical, projects, pr_scope,
                         saved_gates=_saved_pr_gates(source),
                         source_mode=("live_exact_selected_github_issue"
                                      if args.live_issues and args.focus_issue
                                      else "live_readonly_github_issues" if args.live_issues
                                      else "saved_local_snapshot"),
                         focus_issue=args.focus_issue, raw_board=source)
        attach_history(report, historical)
        if saved_bundle is not None:
            saved_entries = {
                row["url"]: row for batch in saved_bundle["batches"]
                for row in batch["prs"]
            }
            for issue in report["issues"]:
                for candidate in issue["existing_prs"]:
                    # This is intentionally historical, not a current-head gate.
                    candidate["saved_reconciled_gate_observation"] = saved_entries.get(
                        candidate["url"])
            report["saved_gate_batch_reconciliation"] = {
                "source_manifest_sha256": saved_bundle["source_review_manifest_sha256"],
                "source_board_sha256": saved_bundle["source_review_board_sha256"],
                "observed_prs": saved_bundle["observed_prs"],
                "total_manifest_prs": saved_bundle["total_manifest_prs"],
                "missing_batches": saved_bundle["missing_batches"],
                "stale_over_15_minutes_prs": saved_bundle["stale_over_15_minutes_prs"],
                "cross_batch_observation_spread_seconds":
                    saved_bundle["cross_batch_observation_spread_seconds"],
                "cross_batch_observation_within_10_minutes":
                    saved_bundle["cross_batch_observation_within_10_minutes"],
                "manual_review_queue_preview": saved_bundle["manual_review_queue_preview"],
                "manual_review_queue_hidden_after_24":
                    saved_bundle["manual_review_queue_hidden_after_24"],
                "saved_bundle_complete_and_recent":
                    saved_bundle["all_observations_present_and_recent"],
                "unavailable_or_drifted_prs": saved_bundle["unavailable_or_drifted_prs"],
                "saved_historic_gate_category_counts":
                    saved_bundle["saved_historic_gate_category_counts"],
                "current_heads_revalidated": False,
                "merge_authorized": False,
                "claim_authorized": False,
                "deploy_authorized": False,
                "writes_performed": False,
            }
        else:
            report["saved_gate_batch_reconciliation"] = None
        if args.inspect_pr_gates:
            from zsentinel_pr_gate_now import observe_many as live_pr_gate_observe
            selected_prs = []
            known = set()
            for issue in report["issues"]:
                for candidate in issue["existing_prs"]:
                    link = candidate["url"]
                    m = PR.fullmatch(link)
                    if m is None:
                        raise ValueError("untrusted live gate candidate PR URL")
                    identity = ("Zennay/" + m.group(1), int(m.group(2)))
                    if identity[0] != canonical[issue["project"]]:
                        raise ValueError("cross-project current head source")
                    if identity not in known and len(selected_prs) < 4:
                        known.add(identity)
                        selected_prs.append(identity)
            live_gates = (
                live_pr_gate_observe(selected_prs, os.environ.get("GITHUB_TOKEN"))
                if selected_prs else {
                    "schema": "zsentinel-current-pr-gate-observation-v1",
                    "items": [], "observed_prs": 0,
                    "merge_authorized": False, "writes_performed": False,
                    "required_branch_protection_verified": False})
            if (live_gates.get("schema") != "zsentinel-current-pr-gate-observation-v1"
                    or live_gates.get("merge_authorized") is not False
                    or live_gates.get("writes_performed") is not False
                    or live_gates.get("required_branch_protection_verified") is not False):
                raise ValueError("unauthorized live PR gate source")
            by_url = {entry["url"]: entry for entry in live_gates["items"]}
            for issue in report["issues"]:
                for candidate in issue["existing_prs"]:
                    candidate["live_exact_head_review_checks_observation"] = by_url.get(candidate["url"])
            report["current_head_gate_observations"] = {
                "mode": "read_only_live_bounded_up_to_four_existing_prs",
                "observed_prs": live_gates["observed_prs"],
                "more_existing_prs_uninspected": (
                    len({p["url"] for row in report["issues"] for p in row["existing_prs"]})
                    > len(selected_prs)),
                "required_branch_protection_verified": False,
                "independent_review_policy_verified": False,
                "merge_authorized": False,
                "writes_performed": False}
        else:
            report["current_head_gate_observations"] = {
                "mode": "not_requested", "observed_prs": 0,
                "more_existing_prs_uninspected": True,
                "required_branch_protection_verified": False,
                "independent_review_policy_verified": False,
                "merge_authorized": False, "writes_performed": False}
        report["human_work_inbox"] = build_human_work_inbox(report)
        if not 1 <= args.worklist_limit <= MAX_ISSUES:
            raise ValueError("worklist-limit must be between 1 and 60")
        if args.format == "json":
            rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        elif args.format == "worklist-json":
            inbox = report["human_work_inbox"]
            trimmed = {**inbox, "cards": inbox["cards"][:args.worklist_limit],
                       "cards_not_displayed": max(
                           0, inbox["issue_count"] - args.worklist_limit),
                       "source_coverage_complete": False}
            rendered = json.dumps(trimmed, ensure_ascii=False, indent=2) + "\n"
        elif args.format == "html":
            rendered = output_html(report) + "\n"
        elif args.format == "worklist-html":
            rendered = render_private_worklist_html(report["human_work_inbox"],
                                                    args.worklist_limit) + "\n"
        elif args.format in ("worklist-markdown", "worklist-text"):
            rendered = render_compact_inbox(
                report["human_work_inbox"],
                "markdown" if args.format == "worklist-markdown" else "text",
                args.worklist_limit)
        else:
            rendered = output_text(report, markdown=args.format == "markdown")
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            # The combined P0 inbox may contain private operator state.
            # Create it with restrictive mode and refuse to overwrite any
            # other worker's prior receipts (including symbolic links).
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(args.output, flags, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as sink:
                    if not stat.S_ISREG(os.fstat(sink.fileno()).st_mode):
                        raise ValueError("private issue report target is not a regular file")
                    sink.write(rendered)
                    sink.flush()
                    os.fsync(sink.fileno())
            except Exception:
                args.output.unlink(missing_ok=True)
                raise
            print("Private zSentinel issue and PR report created (0600).",
                  file=sys.stderr)
        # A saved bundle is never current permission. Mark stale/incoherent
        # saved evidence incomplete without concealing independent issue cards.
        if (report["saved_gate_batch_reconciliation"] is not None
                and not report["saved_gate_batch_reconciliation"]["saved_bundle_complete_and_recent"]):
            return 3
        return 2 if any(p["existing_prs"] and any(
            ref["file_scope_state"] == "shared_file_paths_observed"
            for ref in p["existing_prs"]) for p in report["issues"]) else (
            3 if not pr_scope["source_coverage_complete"] else 0)
    except OSError:
        # Private input/report paths may occur in an OSError message.
        print("zSentinel: private source or GitHub request unavailable", file=sys.stderr)
        return 3
    except ValueError as exc:
        print("zSentinel: source-integration refused — " + str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
