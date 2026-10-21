#!/usr/bin/env python3
"""zSentinel portfolio PR-conflict board: one command, up to 3 canonical projects.

This is a read-only inspection helper. It does not grant source ownership,
release rights, leases, CI status, or permission to edit another worker's work.
"""
from __future__ import annotations

import argparse
import csv
import io
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import stat
import sys

from zsentinel_pr_scope_guard import (
    ROOT, observe_conflict_graph, registry,
)

from zsentinel_pr_scope_delta import read_private_previous, compare, render_delta
from zsentinel_pr_gate_continuity import compare_gate_episodes, render_gate_episodes

MAX_PROJECTS = 3
MAX_PRS_PER_PROJECT = 16
MAX_VISIBLE_PAIRS = 80


def collect_portfolio(projects, canonical, token=None, max_prs=10):
    if (not isinstance(projects, list)
            or not 1 <= len(projects) <= MAX_PROJECTS
            or len(set(projects)) != len(projects)
            or any(project not in canonical for project in projects)
            or type(max_prs) is not int
            or not 1 <= max_prs <= MAX_PRS_PER_PROJECT):
        raise ValueError("choose one to three distinct registered projects and 1-16 PRs")
    items = []
    for project in projects:
        repo = canonical[project]
        # A failure in one repository must NOT hide observed collisions in
        # another: fail closed for this source, continue independent projects.
        try:
            source = observe_conflict_graph(repo, token=token, limit=max_prs)
        except (ValueError, OSError):
            items.append({
                "project": project,
                "repository": repo,
                "source_status": "unavailable",
                "sample_complete": False,
                "open_pr_snapshot_stable": False,
                "sampled_open_pr_heads": [],
                "shown_collision_pairs": [],
                "linked_pr_collision_clusters": [],
                "observed_connected_pr_collision_clusters": None,
                "shown_duplicate_issue_groups": [],
                "observed_duplicate_issue_groups": None,
                "duplicate_issue_groups_truncated": False,
                "duplicate_issue_group_coverage_complete": False,
                "unpaired_open_prs_not_claim_verified": [],
                "issue_reference_source_covered": False,
                "issue_reference_snapshot_stable": False,
                "issue_reference_sample_complete": False,
                "observed_issue_to_pr_mentions_unverified": [],
                "observed_collision_pairs": None,
                "inspected_open_prs": None,
                "observed_open_prs": None,
                "source_observed_at_utc": None,
                "reason": "github_source_unavailable_or_incomplete",
            })
            continue
        pairs = source["shown_file_collision_pairs"][:MAX_VISIBLE_PAIRS]
        display_truncated = (source["collision_cards_truncated"] or
                             len(source["shown_file_collision_pairs"]) > MAX_VISIBLE_PAIRS)
        complete = source["pr_file_scope_sample_complete"] and not display_truncated
        items.append({
            "project": project,
            "repository": repo,
            "source_status": "complete_open_pr_sample_only"
                             if complete else "partial_open_pr_sample",
            "sample_complete": complete,
            "open_pr_snapshot_stable": source["open_pr_snapshot_stable"],
            "sampled_open_pr_heads": source["inspected_pr_heads"],
            "source_sample_complete": source["pr_file_scope_sample_complete"],
            "shown_collision_pairs": pairs,
            "linked_pr_collision_clusters": source["linked_pr_collision_clusters"],
            "observed_connected_pr_collision_clusters":
                source["observed_connected_pr_collision_clusters"],
            "shown_duplicate_issue_groups": source["shown_duplicate_issue_groups"],
            "observed_duplicate_issue_groups": source["observed_duplicate_issue_groups"],
            "duplicate_issue_groups_truncated": source["duplicate_issue_groups_truncated"],
            "duplicate_issue_group_coverage_complete": (
                complete and source["duplicate_issue_group_coverage_complete"]),
            "unpaired_open_prs_not_claim_verified":
                source["unpaired_open_prs_in_sample_not_claim_verified"],
            # Positive links from a *stable inspected subset* remain useful
            # even if this large repository has more PRs than our budget.
            # Never infer an absent PR or an absent issue is claimable.
            "issue_reference_source_covered":
                source["open_pr_snapshot_stable"],
            "issue_reference_snapshot_stable":
                source["open_pr_snapshot_stable"],
            "issue_reference_sample_complete":
                complete and source["issue_reference_body_coverage_complete"],
            "observed_issue_to_pr_mentions_unverified": (
                source["open_pr_issue_references_unverified"]
                if source["open_pr_snapshot_stable"] else []),
            "observed_collision_pairs": source["observed_file_collision_pairs"],
            "inspected_open_prs": source["inspected_prs"],
            "observed_open_prs": source["observed_open_prs"],
            "source_observed_at_utc": source["observed_at_utc"],
            "source_truncated": display_truncated,
            "reason": ("display_truncated_inspect_original_prs" if display_truncated
                       else "same_filename_does_not_prove_merge_conflict"),
        })
    return {
        "schema": "zsentinel-portfolio-pr-collision-board-v1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "projects": projects,
        "maximum_projects": MAX_PROJECTS,
        "maximum_prs_per_project": MAX_PRS_PER_PROJECT,
        "maximum_github_gets": len(projects) * (max_prs + 2),
        "source": "bounded_readonly_open_github_prs",
        "project_views": items,
        "source_coverage_complete": all(i["sample_complete"] for i in items),
        "any_file_overlaps_observed": any(
            bool(i["observed_collision_pairs"]) for i in items),
        "any_multiple_pr_issue_mentions_observed": any(
            bool(i["observed_duplicate_issue_groups"]) for i in items),
        "notion_claims_verified": False,
        "prless_branch_owners_verified": False,
        "worker_file_leases_verified": False,
        "file_edit_authorized": False,
        "claim_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "writes_performed": False,
    }



def current_merge_first_gates(board, token=None, focus_prs=None):
    """Combine the current PR-overlap board with <=4 actual exact-head gate reads.

    This is one opted-in *observation*, never merge authorization or a claim.
    The overlap and check snapshots are sequential, not atomic. A changed head
    is rejected as a cross-source match rather than called merge-ready.
    """
    from zsentinel_pr_gate_now import observe_many
    from zsentinel_pr_scope_guard import selected_source_pr

    if (type(board) is not dict
            or board.get("schema") != "zsentinel-portfolio-pr-collision-board-v1"
            or type(board.get("project_views")) is not list
            or not 1 <= len(board["project_views"]) <= MAX_PROJECTS
            or any(board.get(key) is not False for key in (
                "notion_claims_verified", "worker_file_leases_verified",
                "claim_authorized", "merge_authorized", "deploy_authorized",
                "writes_performed"))):
        raise ValueError("untrusted source for live merge-first gate inspection")

    if focus_prs is None:
        focus_prs = []
    if (type(focus_prs) is not list or len(focus_prs) > 4
            or len(set(focus_prs)) != len(focus_prs)
            or any(type(raw) is not str or
                   re.fullmatch(r"[a-z][a-z0-9_-]{0,39}#[1-9][0-9]{0,8}", raw) is None
                   for raw in focus_prs)):
        raise ValueError("choose at most four distinct canonical --gate-pr project#number refs")
    buckets = []
    total_inspected = 0
    qa_only_skipped = 0
    incomplete_projects = []
    for view in board["project_views"]:
        if (type(view) is not dict or type(view.get("project")) is not str
                or type(view.get("repository")) is not str):
            raise ValueError("untrusted project identity")
        receipts = view.get("sampled_open_pr_heads", [])
        if type(receipts) is not list or len(receipts) > MAX_PRS_PER_PROJECT:
            raise ValueError("invalid inspected PR head count")
        total_inspected += len(receipts)
        if (view.get("source_status") == "unavailable"
                or view.get("open_pr_snapshot_stable") is not True):
            incomplete_projects.append(view["project"])
            buckets.append([])
            continue
        by_num = {}
        for receipt in receipts:
            if (type(receipt) is not dict or type(receipt.get("number")) is not int
                    or type(receipt.get("product_candidate_based_on_title")) is not bool
                    or type(receipt.get("head_sha")) is not str
                    or type(receipt.get("base_sha")) is not str
                    or type(receipt.get("url")) is not str
                    or receipt["number"] in by_num
                    or receipt["url"] != "https://github.com/" +
                    view["repository"] + "/pull/" + str(receipt["number"])):
                raise ValueError("untrusted PR sample identity")
            by_num[receipt["number"]] = receipt
            if not receipt["product_candidate_based_on_title"]:
                qa_only_skipped += 1
        order = []
        # Prefer existing source collisions and duplicate issue mentions,
        # then stable existing PR order. This is attention, NOT priority/owner.
        for pair in view.get("shown_collision_pairs", []):
            for side in ("left", "right"):
                number = pair[side]["number"]
                if (number in by_num and
                        by_num[number]["product_candidate_based_on_title"] and
                        number not in order):
                    order.append(number)
        for group in view.get("shown_duplicate_issue_groups", []):
            for item in group["shown_pr_sources"]:
                number = next((n for n, pr in by_num.items()
                               if pr["url"] == item["url"]), None)
                if (number is not None and
                        by_num[number]["product_candidate_based_on_title"] and
                        number not in order):
                    order.append(number)
        for pr in receipts:
            if (pr["product_candidate_based_on_title"] and
                    pr["number"] not in order):
                order.append(pr["number"])
        buckets.append([(view["project"], view["repository"], by_num[n])
                        for n in order])
    # Fair sampling across projects: no noisy project can occupy all four
    # scarce API receipts while another stable project is present.
    selected = []
    for offset in range(MAX_PRS_PER_PROJECT):
        for bucket in buckets:
            if len(bucket) > offset:
                selected.append(bucket[offset])
                if len(selected) == 4:
                    break
        if len(selected) == 4:
            break
    fallback_count = 0
    direct_attempt_count = 0
    unavailable_targets = []
    if focus_prs:
        available = {
            project + "#" + str(pr["number"]): (project, repo, pr)
            for bucket in buckets for project, repo, pr in bucket
        }
        source_projects = {
            view["project"]: view["repository"] for view in board["project_views"]
        }
        selected = []
        for raw in focus_prs:
            project, number_text = raw.split("#", 1)
            if project not in source_projects:
                raise ValueError("explicit PR not in selected registered project")
            if raw in available:
                selected.append(available[raw])
                continue
            # An older PR can fall outside the 16 newest entries. Pin its
            # exact head/base directly instead of declaring it nonexistent.
            # This bounded four-GET direct observation does not verify conflicts
            # with PRs outside the ordinary capped collision inventory.
            repo = source_projects[project]
            direct_attempt_count += 1
            try:
                direct = selected_source_pr(repo, int(number_text), token,
                                            expanded_files=True)
                if direct["product_candidate_based_on_title"] is not True:
                    raise ValueError("QA-only or non-product selected PR")
            except (OSError, ValueError):
                # An unreachable or QA-only selected PR is UNKNOWN, not
                # authorized. Continue independently selected PRs; do not
                # let one source failure hide other actual gate blockers.
                unavailable_targets.append({
                    "project": project,
                    "url": "https://github.com/" + repo + "/pull/" + number_text,
                    "head_sha_observed": None,
                    "observed_in_recent_pr_collision_sample": False,
                    "complete_file_collision_coverage_verified": False,
                    "sampled_file_overlaps_observed": [],
                    "sampled_file_overlaps_hidden": 0,
                    "status": "direct_selected_pr_unavailable_or_not_product",
                    "human_gate_blocker": "source_changed_or_unavailable",
                    "required_rules_verified": False,
                    "live_owner_claim_verified": False,
                    "merge_authorized": False,
                })
                continue
            # The first inventory has already fetched bounded file lists.
            # Reuse them for positive, explicitly incomplete collision
            # evidence instead of issuing another full GitHub PR scan.
            observed_overlaps = []
            view = next(item for item in board["project_views"]
                        if item["project"] == project)
            if view.get("open_pr_snapshot_stable") is True:
                for recent in view.get("sampled_open_pr_heads", []):
                    shared = sorted(set(direct["paths"]).intersection(
                        recent.get("file_paths_observed", [])))
                    if recent["number"] == direct["number"] or not shared:
                        continue
                    observed_overlaps.append({
                        "other_pr_url": recent["url"],
                        "other_pr_head_when_read": recent["head_sha"],
                        "shared_file_count_observed": len(shared),
                        "shared_paths_preview": shared[:12],
                        "shared_paths_preview_truncated": len(shared) > 12,
                        "other_pr_file_list_incomplete":
                            not recent.get("file_list_complete", False),
                        "complete_repo_collision_scan_verified": False,
                    })
            observed_overlaps.sort(
                key=lambda item: (-item["shared_file_count_observed"],
                                  item["other_pr_url"]))
            selected.append((project, repo, {
                **direct,
                "direct_explicit_source_only": True,
                "sampled_file_overlaps_observed": observed_overlaps[:16],
                "sampled_file_overlaps_hidden": max(0, len(observed_overlaps) - 16),
            }))
            fallback_count += 1
    identities = [(repo, pr["number"]) for _, repo, pr in selected]
    gate_rows = observe_many(identities, token)["items"] if identities else []
    output = []
    for (project, repo, captured), gate in zip(selected, gate_rows):
        matched = (
            gate.get("status") == "observed_current_head_partial_gate_evidence"
            and gate.get("head_sha_observed") == captured["head_sha"]
            and gate.get("base_sha_observed") == captured["base_sha"])
        if matched:
            item = {
                "project": project,
                "url": captured["url"],
                "head_sha_observed": captured["head_sha"],
                "base_sha_observed": captured["base_sha"],
                "observed_in_recent_pr_collision_sample":
                    not captured.get("direct_explicit_source_only", False),
                "complete_file_collision_coverage_verified": False,
                "sampled_file_overlaps_observed":
                    captured.get("sampled_file_overlaps_observed", []),
                "sampled_file_overlaps_hidden":
                    captured.get("sampled_file_overlaps_hidden", 0),
                "focused_pr_file_pages_observed":
                    captured.get("source_file_pages_observed"),
                "focused_pr_paths_observed":
                    len(captured.get("paths", [])) if
                    captured.get("direct_explicit_source_only") else None,
                "status": "current_exact_pr_head_and_sample_match",
                "draft_at_read": gate["draft_at_read"],
                "github_mergeable_boolean_observed": gate["github_mergeable_boolean_observed"],
                "check_runs_total_observed": gate["check_runs_total_observed"],
                "check_runs_latest_attempts_observed": gate["check_runs_latest_attempts_observed"],
                "check_runs_evidence_sha256": gate["check_runs_evidence_sha256"],
                "check_runs_superseded_attempts_observed": gate["check_runs_superseded_attempts_observed"],
                "observed_actionable_check_runs": gate["observed_actionable_check_runs"],
                "observed_actionable_check_runs_hidden": gate["observed_actionable_check_runs_hidden"],
                "observed_checks_pending": gate["observed_checks_pending"],
                "observed_checks_unknown": gate["observed_checks_unknown"],
                "observed_checks_not_successful": gate["observed_checks_not_successful"],
                "observed_legacy_pending": gate["legacy_status_pending_observed"],
                "legacy_contexts_observed": gate["legacy_status_contexts_observed"],
                "legacy_status_evidence_sha256": gate["legacy_status_evidence_sha256"],
                "legacy_status_superseded_attempts_observed":
                    gate["legacy_status_superseded_attempts_observed"],
                "observed_actionable_legacy_contexts":
                    gate["observed_actionable_legacy_contexts"],
                "observed_actionable_legacy_contexts_hidden":
                    gate["observed_actionable_legacy_contexts_hidden"],
                "legacy_status_source_may_be_truncated":
                    gate["legacy_status_source_may_be_truncated"],
                "observed_legacy_failure_or_error":
                    gate["legacy_status_failure_or_error_observed"],
                "observed_independent_exact_head_approvals":
                    gate["observed_independent_exact_head_approvals"],
                "exact_head_review_evidence_sha256": gate["exact_head_review_evidence_sha256"],
                "observed_exact_head_changes_requested":
                    gate["observed_exact_head_changes_requested"],
                "human_next_gate_step": gate["human_next_gate_step"],
                "check_page_complete": gate["check_runs_page_complete"],
                "check_runs_sample_complete_and_terminal":
                    gate["check_runs_sample_complete_and_terminal"],
                "review_page_may_be_truncated": gate["review_page_may_be_truncated"],
                "required_rules_verified": False,
                "live_owner_claim_verified": False,
                "merge_authorized": False,
            }
        else:
            item = {
                "project": project, "url": captured["url"],
                "head_sha_observed": captured["head_sha"],
                "base_sha_observed": captured["base_sha"],
                "observed_in_recent_pr_collision_sample":
                    not captured.get("direct_explicit_source_only", False),
                "complete_file_collision_coverage_verified": False,
                "sampled_file_overlaps_observed":
                    captured.get("sampled_file_overlaps_observed", []),
                "sampled_file_overlaps_hidden":
                    captured.get("sampled_file_overlaps_hidden", 0),
                "status": "pr_source_unavailable_or_changed_between_observations",
                "required_rules_verified": False,
                "live_owner_claim_verified": False,
                "merge_authorized": False,
            }
        item["human_gate_blocker"] = current_gate_blocker(item)
        item["merge_authorized"] = False
        output.append(item)
    output.extend(unavailable_targets)
    if focus_prs:
        rows_by_ref = {
            row["project"] + "#" + row["url"].rsplit("/", 1)[-1]: row
            for row in output
        }
        output = [rows_by_ref[raw] for raw in focus_prs]
    result = {
        **board,
        # The recent-PR collision board cannot represent the full set after
        # an explicitly selected different PR has been found. Even if it
        # claimed a complete bounded snapshot earlier, that newer/direct
        # source proves it is not a complete view of the combined result.
        "source_coverage_complete": (
            board["source_coverage_complete"] and direct_attempt_count == 0),
        "source_coverage_limited_by_direct_prs": direct_attempt_count > 0,
        "current_live_pr_gate_observation": {
            "mode": ("explicit_product_pr_refs_with_bounded_direct_lookup"
                     if focus_prs and direct_attempt_count else
                     "explicit_stable_sampled_product_pr_refs" if focus_prs else
                     "opt_in_one_shot_at_most_four_stable_sampled_prs"),
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "items": output,
            "inspected_stable_sample_prs": total_inspected,
            "pr_gate_receipts_requested": len(selected),
            "direct_explicit_prs_outside_recent_sample": fallback_count,
            "direct_explicit_pr_lookup_failed_or_not_product": len(unavailable_targets),
            "extra_direct_pr_identity_gets": direct_attempt_count * 4,
            "observed_gate_blocker_counts": {
                category: sum(row["human_gate_blocker"] == category for row in output)
                for category in sorted({row["human_gate_blocker"] for row in output})
            },
            "explicit_selected_prs": list(focus_prs),
            "qa_only_title_prs_not_inspected": qa_only_skipped,
            "more_sampled_prs_not_gate_checked": (
                total_inspected > len(selected) - fallback_count),
            "source_unavailable_or_changed_projects": incomplete_projects,
            "max_additional_github_gets": len(selected) * 5 + direct_attempt_count * 4,
            "file_scope_and_gate_reads_are_atomic": False,
            "required_branch_protection_verified": False,
            "notion_claims_verified": False,
            "independent_review_policy_verified": False,
            "merge_authorized": False,
            "deploy_authorized": False,
            "writes_performed": False,
        },
        "maximum_github_gets": (board["maximum_github_gets"] + len(selected) * 5
                                + direct_attempt_count * 4),
        "claim_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "writes_performed": False,
    }
    return result


def summary_lines(view):
    if view["source_status"] == "unavailable":
        return ["Bron ONBESCHIKBAAR: geen oordeel over open PR's of overlap."]
    result = [
        "Open PR's bekeken: " + str(view["inspected_open_prs"]) +
        " / waargenomen " + str(view["observed_open_prs"]),
        "Waargenomen gedeelde-bestandsparen: " +
        str(view["observed_collision_pairs"]),
        "Samenhangende PR-bestandsgroepen: " +
        str(view["observed_connected_pr_collision_clusters"]),
        "Open PR-teksten met hetzelfde issue (GEEN fix- of eigenaarsbewijs): " +
        str(view["observed_duplicate_issue_groups"]),
        "Bron: " + ("volledige huidige open PR-lijst (geen leasebewijs)"
                     if view["sample_complete"] else "ONVOLLEDIG: andere PR's kunnen ontbreken"),
    ]
    for cluster in view["linked_pr_collision_clusters"]:
        result.append(
            "  GROEP " + str(cluster["component_number"]) + " · " +
            str(cluster["pr_count_observed"]) + " open PRs · " +
            str(cluster["shared_file_edge_count_observed"]) + " directe PR-paren · " +
            str(cluster["distinct_shared_file_count_observed"]) + " gedeelde bestanden")
        for pr in cluster["participating_prs"]:
            result.append("    PR #" + str(pr["number"]) + " · " +
                          str(pr["other_prs_with_shared_files_observed"]) +
                          " bestandsverbindingen | " + pr["url"])
    for pair in view["shown_collision_pairs"]:
        result.append(
            "  PR #" + str(pair["left"]["number"]) +
            " ↔ PR #" + str(pair["right"]["number"]) +
            " | " + str(pair["shared_file_count_observed"]) +
            " bestanden | " + pair["left"]["url"] +
            " | " + pair["right"]["url"])
    for group in view["shown_duplicate_issue_groups"]:
        result.append("  ISSUE #" + str(group["issue_number"]) +
                      " · vermeld door " + str(group["open_pr_mentions_observed"]) +
                      " open PR's, fix/eigenaar onbekend")
        for pr in group["shown_pr_sources"]:
            result.append("    " + pr["url"] + " @ " + pr["head_sha"][:12])
    if view["duplicate_issue_groups_truncated"]:
        result.append("  Issue-groepen afgekapt; bron alleen gedeeltelijk.")
    if view["unpaired_open_prs_not_claim_verified"]:
        result.append("Zonder gedeelde paden in steekproef (GEEN claimvrijgave):")
        for pr in view["unpaired_open_prs_not_claim_verified"]:
            result.append("  PR #" + str(pr["number"]) + " | " + pr["url"])
    if view.get("source_truncated"):
        result.append("  Details afgekapt; open bron-PR's voor verdere inspectie.")
    return result



GATE_LABELS = {
    "inspect_incomplete_current_head_gate_source": "Controleer ontbrekende huidige CI/reviewbron.",
    "inspect_current_head_failed_checks_with_owner": "Laat PR-eigenaar mislukte CI herstellen.",
    "wait_for_existing_current_head_checks": "Bestaande exacte-head CI loopt nog.",
    "owner_address_exact_head_review_changes": "PR-eigenaar moet reviewwijzigingen verwerken.",
    "verify_missing_current_head_checks_and_rules": "Controleer branchregels en ontbrekende CI.",
    "request_independent_current_head_review": "Vraag onafhankelijke review op actuele HEAD.",
    "verify_required_rules_ownership_and_release_separately":
        "Controleer verplichte branchregels, eigenaar en release apart.",
}



def current_gate_blocker(row):
    """Truthful operator blocker classification, never a merge clearance."""
    if row["status"] != "current_exact_pr_head_and_sample_match":
        return "source_changed_or_unavailable"
    if (row["check_page_complete"] is not True
            or row["review_page_may_be_truncated"] is True
            or row["legacy_status_source_may_be_truncated"] is True):
        return "incomplete_github_gate_source"
    if (row["observed_checks_not_successful"] > 0
            or row["observed_legacy_failure_or_error"] > 0):
        return "failed_checks_or_legacy_context"
    if row["github_mergeable_boolean_observed"] is False:
        return "github_merge_conflict_observed"
    if row["draft_at_read"]:
        return "draft_pr_needs_owner"
    if row["observed_exact_head_changes_requested"] > 0:
        return "review_changes_requested"
    if (row["observed_checks_pending"] > 0
            or row["observed_checks_unknown"] > 0
            or row["observed_legacy_pending"] > 0):
        return "checks_still_running"
    if (row["observed_checks_pending"] == 0
            and row["observed_checks_not_successful"] == 0
            and row["legacy_contexts_observed"] == 0
            and not row["check_runs_sample_complete_and_terminal"]):
        return "check_source_not_terminal"
    if (row["check_runs_total_observed"] == 0
            and row["legacy_contexts_observed"] == 0):
        return "no_ci_check_or_legacy_context_seen"
    if row["observed_independent_exact_head_approvals"] < 1:
        return "independent_review_not_seen"
    if row["github_mergeable_boolean_observed"] is None:
        return "github_mergeability_not_determined"
    return "branch_rules_ownership_and_release_not_verified"


BLOCKER_DESCRIPTIONS = {
    "source_changed_or_unavailable": "PR-HEAD veranderd of GitHub-bron ontbreekt.",
    "incomplete_github_gate_source": "Niet alle Check Runs, reviews of legacy-statussen zijn gelezen.",
    "failed_checks_or_legacy_context": "Er is een falende Check Run of legacy-statuscontext gezien.",
    "draft_pr_needs_owner": "De PR is nog een draft; eerst door eigenaar reviewklaar maken.",
    "github_merge_conflict_observed": "GitHub meldt dat deze PR niet automatisch kan worden gemerged; herstel conflict met eigenaar.",
    "github_mergeability_not_determined": "GitHub heeft mergeability nog niet berekend; geen groen oordeel.",
    "no_ci_check_or_legacy_context_seen": "Geen Check Runs of legacy-statuscontext waargenomen; vereiste CI onbekend.",
    "review_changes_requested": "Er zijn op de exacte HEAD wijzigingen gevraagd.",
    "checks_still_running": "Bestaande Check Runs of legacy-statuscontexten lopen nog.",
    "check_source_not_terminal": "CI-status kon niet als volledig afgerond worden bevestigd.",
    "independent_review_not_seen": "Geen onafhankelijke approval voor deze exacte HEAD gezien.",
    "branch_rules_ownership_and_release_not_verified":
        "Branch protection, eigenaarschap en release-toestemming niet bevestigd.",
}


def safe_markdown_label(value):
    """Escape provider-supplied Check Run/context text inside Markdown links."""
    return (value.replace("\\", "\\\\")
            .replace("[", "\\[").replace("]", "\\]"))


def current_gate_lines(board, markdown=False):
    observation = board.get("current_live_pr_gate_observation")
    if observation is None:
        return []
    lines = [
        "",
        "## Huidige PR-gates (maximaal vier; observatie, geen mergevrijgave)"
        if markdown else "Huidige PR-gates (maximaal vier; GEEN mergevrijgave)",
        "Selectie: " + observation["mode"] + "; expliciet gekozen: " +
        (", ".join(observation["explicit_selected_prs"]) or "automatische beperkte shortlist"),
        "Oudere expliciete PR's met afzonderlijke broncontrole: " +
        str(observation["direct_explicit_prs_outside_recent_sample"]) +
        "; extra bron-GETs voor die PR-identiteiten: " +
        str(observation["extra_direct_pr_identity_gets"]),
        "Niet-beschikbare of geweigerde gerichte PR-bronnen: " +
        str(observation["direct_explicit_pr_lookup_failed_or_not_product"]),
        "Totaal GitHub GET-budget voor huidige extra gatelezing: " +
        str(observation["max_additional_github_gets"]),
        "Gatebronnen gezien: " + str(observation["pr_gate_receipts_requested"]) +
        "; QA-only titels bewust niet geselecteerd: " +
        str(observation["qa_only_title_prs_not_inspected"]) +
        "; overige PRs in steekproef niet bekeken: " +
        str(observation["more_sampled_prs_not_gate_checked"]) +
        "; projectbron ontbreekt/verschoof: " +
        ", ".join(observation["source_unavailable_or_changed_projects"]),
    ]
    for gate in observation["items"]:
        link = ("[" + gate["url"] + "](" + gate["url"] + ")"
                if markdown else gate["url"])
        lines.append("- " + gate["project"] + " · " + link)
        if gate["observed_in_recent_pr_collision_sample"] is False:
            if gate.get("focused_pr_file_pages_observed") is not None:
                lines.append(
                    "  Directe PR-filebron: " +
                    str(gate["focused_pr_file_pages_observed"]) +
                    " pagina('s), " +
                    str(gate["focused_pr_paths_observed"]) +
                    " paden; buiten de rest van de repository onbekend.")
            lines.append(
                "  DIRECT GELEZEN OUDERE PR: buiten de recente dubbel gelezen "
                "botsingssteekproef; geen volledig collision- of claimbewijs.")
            for collision in gate["sampled_file_overlaps_observed"]:
                other = ("[" + collision["other_pr_url"] + "](" +
                         collision["other_pr_url"] + ")" if markdown
                         else collision["other_pr_url"])
                lines.append(
                    "  WAARGENOMEN gedeelde paden met " + other + ": " +
                    str(collision["shared_file_count_observed"]) + " (" +
                    ", ".join(collision["shared_paths_preview"]) + ")")
            if gate["sampled_file_overlaps_hidden"]:
                lines.append(
                    "  Extra overlaplinks buiten de beperkte voorvertoning: " +
                    str(gate["sampled_file_overlaps_hidden"]))
        lines.append("  Actuele blokkade: " + BLOCKER_DESCRIPTIONS[
            gate["human_gate_blocker"]])
        if gate["status"] != "current_exact_pr_head_and_sample_match":
            continue
        lines.append(
            "  Exact HEAD " + gate["head_sha_observed"][:12] +
            " · Check Runs lopend " + str(gate["observed_checks_pending"]) +
            " / mislukt " + str(gate["observed_checks_not_successful"]) +
            " / totaal gezien " + str(gate["check_runs_total_observed"]) +
            " / onbekend " + str(gate["observed_checks_unknown"]) +
            " · legacy pending " + str(gate["observed_legacy_pending"]) +
            " / failed " + str(gate["observed_legacy_failure_or_error"]) +
            " · draft " + str(gate["draft_at_read"]) +
            " · gevraagde reviewwijzigingen " +
            str(gate["observed_exact_head_changes_requested"]) +
            " · onafhankelijke approvals gezien " +
            str(gate["observed_independent_exact_head_approvals"]) +
            " · vervolg: " + GATE_LABELS.get(
                gate["human_next_gate_step"], "Bron onvolledig; actuele gates inspecteren."))
        lines.append("  Laatste poging per checknaam/app: " +
                     str(gate["check_runs_latest_attempts_observed"]) +
                     "; eerdere checkpogingen: " +
                     str(gate["check_runs_superseded_attempts_observed"]))
        for check in gate["observed_actionable_check_runs"]:
            link = ("[" + safe_markdown_label(check["name"]) + "](" + check["url"] + ")"
                    if markdown else check["name"] + " · " + check["url"])
            lines.append("  CHECK " + link + " · " + check["state"] +
                         " · " + str(check["conclusion"] or "geen conclusie"))
        if gate["observed_actionable_check_runs_hidden"]:
            lines.append("  Extra niet-groene checks verborgen: " +
                         str(gate["observed_actionable_check_runs_hidden"]))
        lines.append("  Eerdere legacy-statuspogingen genegeerd: " +
                     str(gate["legacy_status_superseded_attempts_observed"]))
        for status in gate["observed_actionable_legacy_contexts"]:
            link = ("[" + safe_markdown_label(status["context"]) + "](" + status["url"] + ")"
                    if markdown else status["context"] + " · " + status["url"])
            lines.append("  LEGACY " + link + " · " + status["state"])
        if gate["observed_actionable_legacy_contexts_hidden"]:
            lines.append("  Extra niet-groene legacy statussen verborgen: " +
                         str(gate["observed_actionable_legacy_contexts_hidden"]))
        if (not gate["check_page_complete"]
                or gate["review_page_may_be_truncated"]
                or gate["legacy_status_source_may_be_truncated"]):
            lines.append("  WAARSCHUWING: niet alle actuele check/reviewgegevens waargenomen.")
    lines.append("Afzonderlijke Notion-claims, branch protection, eigenaar/release "
                 "en verplichte checks blijven NIET vrijgegeven.")
    return lines


# Merge-first attention desk: source-bound, at most four selected PRs.
# This is a useful operator inbox, not a merge queue or owner-admission API.
ATTENTION_RANK = {
    "failed_checks_or_legacy_context": 0,
    "github_merge_conflict_observed": 0,
    "review_changes_requested": 1,
    "draft_pr_needs_owner": 1,
    "source_changed_or_unavailable": 1,
    "incomplete_github_gate_source": 2,
    "checks_still_running": 2,
    "check_source_not_terminal": 2,
    "no_ci_check_or_legacy_context_seen": 2,
    "independent_review_not_seen": 3,
    "github_mergeability_not_determined": 3,
    "branch_rules_ownership_and_release_not_verified": 4,
}
ATTENTION_TITLES = (
    "CI of mergeconflict vraagt actie", "PR-eigenaar moet actie ondernemen",
    "Bron/CI onvolledig of nog bezig", "Review/mergeability controleren",
    "Bescherming, eigenaarschap en release controleren",
    "Ongewijzigde HOLD · geen nieuwe controle zonder bewijs",
)


def merge_first_attention(board):
    """Bounded operator actions from the current exact-head receipt only."""
    live = board.get("current_live_pr_gate_observation")
    if live is None:
        return None
    if (type(live) is not dict or type(live.get("items")) is not list
            or len(live["items"]) > 4 or board.get("merge_authorized") is not False):
        raise ValueError("invalid read-only PR attention source")
    snapshots = {view["project"]: view for view in board["project_views"]}
    rows = []
    for gate in live["items"]:
        if (gate["project"] not in snapshots or
                gate["human_gate_blocker"] not in ATTENTION_RANK or
                gate.get("merge_authorized") is not False):
            raise ValueError("uncanonical or authorizing PR gate")
        view = snapshots[gate["project"]]
        repo = view["repository"]
        url = gate["url"]
        prefix = "https://github.com/" + repo + "/pull/"
        suffix = url[len(prefix):] if url.startswith(prefix) else ""
        if re.fullmatch(r"[1-9][0-9]{0,8}", suffix) is None:
            raise ValueError("invalid canonical PR URL")
        current = gate["status"] == "current_exact_pr_head_and_sample_match"
        blockers = []
        if current:
            for check in gate["observed_actionable_check_runs"]:
                blockers.append({
                    "name": check["name"], "state": check["state"],
                    "url": check["url"], "kind": "check_run",
                })
            for status in gate["observed_actionable_legacy_contexts"]:
                blockers.append({
                    "name": status["context"], "state": status["state"],
                    "url": status["url"], "kind": "legacy_status",
                })
        # Collision links are advisory and may cover a different sample/HEAD.
        # Never interpret absence or a shared filename as merge clearance.
        overlaps = []
        # The collision snapshot and the exact-HEAD gate can observe
        # different revisions of the same open PR. Prevent old-file
        # relationships from masquerading as current merge-first guidance.
        if current:
            gate_head = gate["head_sha_observed"]
            for pair in view.get("shown_collision_pairs", [])[:80]:
                left, right = pair["left"], pair["right"]
                if left["url"] == url and left.get("head_sha") == gate_head:
                    overlaps.append(right["url"])
                elif right["url"] == url and right.get("head_sha") == gate_head:
                    overlaps.append(left["url"])
            # Focused older PRs use a separately pinned second-read
            # exact-HEAD receipt. No sample may qualify after head drift.
            for other in gate.get("sampled_file_overlaps_observed", []):
                overlaps.append(other["other_pr_url"])
        # Only canonical same-repository PR locations are clickable. This is
        # presentation data from a bounded, possibly stale collision sample.
        candidate_links = sorted(set(overlaps))
        overlaps = [other for other in candidate_links
                    if other.startswith(prefix) and
                    re.fullmatch(r"[1-9][0-9]{0,8}",
                                 other[len(prefix):]) is not None
                    and other != url][:8]
        blocker = gate["human_gate_blocker"]
        rank = ATTENTION_RANK[blocker]
        rows.append({
            "project": gate["project"], "repository": repo, "url": url,
            "number": int(suffix),
            "head_sha": gate.get("head_sha_observed") if current else None,
            "base_sha": gate.get("base_sha_observed") if current else None,
            "blocker": blocker, "blocker_text": BLOCKER_DESCRIPTIONS[blocker],
            "rank": rank, "attention_group": ATTENTION_TITLES[rank],
            "human_next_step": (
                GATE_LABELS.get(gate["human_next_gate_step"],
                                "Bekijk GitHub-bewijs met de PR-eigenaar.")
                if current else "Herlees de actuele PR bron, claim geen merge."),
            "non_green_jobs": blockers[:24],
            "non_green_jobs_hidden": max(0, len(blockers) - 24) +
                (gate["observed_actionable_check_runs_hidden"] +
                 gate["observed_actionable_legacy_contexts_hidden"]
                 if current else 0),
            "observed_file_overlap_prs": overlaps,
            "source_current_head_verified": current,
            "historical_gate_change": None,
            "historical_next_step": None,
            "new_evidence_since_previous_sample": False,
            "merge_authorized": False,
            "owner_claim_verified": False,
            "required_branch_rules_verified": False,
        })
    # Historical data is never merge proof. It can de-emphasize a strictly
    # unchanged observed HOLD, but cannot assert uninterrupted continuity.
    episode = board.get("historical_gate_episodes")
    if episode is not None:
        if (episode.get("merge_authorized") is not False
                or episode.get("writes_performed") is not False):
            raise ValueError("non-read-only historical PR handoff")
        changed = {row["url"]: row for row in episode["items"]}
        for row in rows:
            old = changed.get(row["url"])
            if old is None:
                continue
            row["historical_gate_change"] = old["change"]
            row["historical_next_step"] = old["operator_next_step"]
            row["new_evidence_since_previous_sample"] = (
                old["change"] not in (
                    "same_exact_head_and_gate_receipts_at_two_observations",
                    "one_or_both_gate_observations_absent"))
            # Only exact bounded matching source observations qualify.
            if old["safe_to_defer_repeat_only_when_no_new_evidence"]:
                row["rank"] = 5
                row["attention_group"] = ATTENTION_TITLES[5]
                row["human_next_step"] = (
                    "Geen ongewijzigde blokkade opnieuw controleren "
                    "zonder nieuw bronbewijs; pak een andere PR of taak.")
    rows.sort(key=lambda row: (
        row["rank"], not row["new_evidence_since_previous_sample"],
        row["project"], row["number"]))
    return {
        "source": "bounded_current_exact_head_observation",
        "observed_at_utc": live["observed_at_utc"],
        "items": rows, "total": len(rows),
        "source_not_a_complete_pr_inventory": (
            live["more_sampled_prs_not_gate_checked"] or
            not board["source_coverage_complete"]),
        "merge_authorized": False, "claim_authorized": False,
        "deploy_authorized": False, "writes_performed": False,
    }


def attention_lines(board, markdown=False):
    desk = board.get("merge_first_attention")
    if desk is None:
        return []
    lines = ["", ("## Merge-first actielijst (alleen lezen)" if markdown
                   else "Merge-first actielijst (alleen lezen)"),
             "Gerangschikt op waargenomen blokkade, niet op bevoegdheid/eigenaarschap."]
    if desk["source_not_a_complete_pr_inventory"]:
        lines.append("ONVOLLEDIGE bron: andere open PRs en botsingen kunnen ontbreken.")
    if not desk["items"]:
        lines.append("Geen actuele betrouwbare PR-gate geselecteerd.")
    for row in desk["items"]:
        link = ("[PR #" + str(row["number"]) + "](" + row["url"] + ")"
                if markdown else row["url"])
        lines.append("- " + row["attention_group"] + " · " + row["project"] +
                     " · " + link + " · " + row["blocker_text"])
        lines.append("  Actie: " + row["human_next_step"])
        if row["new_evidence_since_previous_sample"]:
            lines.append("  VERANDERD sinds vorige opname: " +
                         str(row["historical_gate_change"]) +
                         " (geen bewijs van volledige CI/merge)")
        if row["head_sha"]:
            lines.append("  HEAD " + row["head_sha"][:12] + " · doelbasis " +
                         row["base_sha"][:12])
        for job in row["non_green_jobs"]:
            detail = ("[" + safe_markdown_label(job["name"]) + "](" +
                      job["url"] + ")" if markdown else
                      job["name"] + " · " + job["url"])
            lines.append("  " + job["kind"] + ": " + detail + " · " + job["state"])
        if row["non_green_jobs_hidden"]:
            lines.append("  Extra niet-groene jobs verborgen: " +
                         str(row["non_green_jobs_hidden"]))
        for related in row["observed_file_overlap_prs"]:
            lines.append("  Gedeelde bestandsnamen met: " +
                         ("[andere PR](" + related + ")" if markdown else related) +
                         " (geen bevestigd Git-conflict)")
    lines.append("Geen automatische workerclaim, CI-dispatch, review, merge of deploy.")
    return lines


def attention_html(board):
    desk = board.get("merge_first_attention")
    if desk is None:
        return ""
    grouped = {}
    for row in desk["items"]:
        grouped.setdefault(row["rank"], []).append(row)
    sections = []
    for rank, rows in sorted(grouped.items()):
        cards = []
        for row in rows:
            url = escape(row["url"], quote=True)
            tasks = "".join(
                "<li>" + escape(job["kind"]) + ": <a href='" +
                escape(job["url"], quote=True) +
                "' rel='noopener noreferrer'>" + escape(job["name"]) +
                "</a> · " + escape(job["state"]) + "</li>"
                for job in row["non_green_jobs"])
            related = "".join(
                "<li><a href='" + escape(other, quote=True) +
                "' rel='noopener noreferrer'>" + escape(other) +
                "</a> · alleen overeenkomstige bestandspaden</li>"
                for other in row["observed_file_overlap_prs"])
            meta = ("<p>HEAD <code>" + escape(row["head_sha"][:12]) +
                    "</code> · doelbasis <code>" + escape(row["base_sha"][:12]) +
                    "</code></p>" if row["head_sha"] else
                    "<p>HEAD/bron niet veilig bevestigd.</p>")
            movement_note = (
                "<p><strong>Gewijzigd sinds vorige opname:</strong> " +
                escape(row["historical_gate_change"]) +
                " · opnieuw bron/HEAD bekijken; geen mergevrijgave.</p>"
                if row["new_evidence_since_previous_sample"] else "")
            cards.append(
                "<li><article><strong>" + escape(row["project"]) +
                " · <a href='" + url + "' rel='noopener noreferrer'>PR #" +
                str(row["number"]) + "</a></strong><p>" +
                escape(row["blocker_text"]) + "</p><p><b>Volgende stap:</b> " +
                escape(row["human_next_step"]) + "</p>" +
                movement_note + meta +
                ("<details><summary>Open problematische CI- en statuslinks (" +
                 str(len(row["non_green_jobs"])) + ")</summary><ul>" + tasks +
                 "</ul><p>Extra bronnen verborgen: " +
                 str(row["non_green_jobs_hidden"]) + "</p></details>"
                 if tasks or row["non_green_jobs_hidden"] else "") +
                ("<details><summary>Bestandscope raakt andere PRs (" +
                 str(len(row["observed_file_overlap_prs"])) +
                 ")</summary><ul>" + related +
                 "</ul><p>Bestandsnamen bewijzen geen mergeconflict.</p></details>"
                 if related else "") + "</article></li>")
        sections.append("<h3>" + escape(ATTENTION_TITLES[rank]) + "</h3><ol>" +
                        "".join(cards) + "</ol>")
    return (
        "<section id='merge-first-attention'><h2>Merge-first actielijst</h2>"
        "<p>Maximaal vier exact-HEAD PR-waarnemingen, op blocker geordend. "
        "Alleen lezen; geen eigenaarschap, checkvereisten of mergeautorisatie.</p>"
        + ("<p><strong>ONVOLLEDIG:</strong> de overige open PRs kunnen ontbreken.</p>"
           if desk["source_not_a_complete_pr_inventory"] else "") +
        ("".join(sections) if sections else
         "<p>Geen geselecteerde bestaande product-PR met actuele gatebon.</p>") +
        "<p>Geen CI-rerun, claim, review, merge of deploy uitgevoerd.</p></section>"
    )


def as_text(board):
    output = [
        "zSentinel | Portfolio PR-conflictbalie (alleen lezen)",
        "Projecten: " + ", ".join(board["projects"]),
    ]
    output.extend(attention_lines(board))
    for view in board["project_views"]:
        output.append("")
        output.append("[" + view["project"] + "] " + view["repository"])
        output.extend(summary_lines(view))
    output.extend(current_gate_lines(board))
    if board.get("historical_gate_episodes"):
        output.extend(["", render_gate_episodes(board["historical_gate_episodes"])])
    if board.get("historical_pr_delta"):
        output.extend(["", render_delta(board["historical_pr_delta"])])
    output.extend([
        "",
        "Gedeelde bestandsnamen ≠ bewezen Git-mergeconflict of huidige eigenaar.",
        "Notion-claims, PR-loze branches en workerleases zijn NIET geverifieerd.",
        "Geen automatische claim, code-edit, merge, runneringreep of deploy.",
    ])
    return "\n".join(output)


def as_markdown(board):
    lines = [
        "# zSentinel · portfolio PR-bestandsoverlap",
        "",
        "Bron: read-only, bounded GitHub open-PR steekproef, " +
        board["observed_at_utc"] + ".",
        "",
        "**Geen autorisatie:** Notion/workerclaims, PR-loze branches, " +
        "mergeconflicten en releasegates niet geverifieerd.",
    ]
    lines.extend(attention_lines(board, markdown=True))
    for view in board["project_views"]:
        lines.extend(["", "## " + view["project"], ""])
        if view["source_status"] == "unavailable":
            lines.append("Bron onbekend/niet beschikbaar. Geen vrijgave.")
            continue
        lines.extend([
            "Gelezen open PR's: " + str(view["inspected_open_prs"]) +
            " / " + str(view["observed_open_prs"]) +
            ". Compleet: " + ("ja (alleen open-PR-sample)" if view["sample_complete"]
                              else "nee") + ".",
            "",
        ])
        lines.extend(["### Samenhangende PR-bestandsgroepen", "",
                      "Verbonden door identieke bestanden; geen claim- of mergevrijgave."])
        for cluster in view["linked_pr_collision_clusters"]:
            lines.append("- Groep " + str(cluster["component_number"]) + ": " +
                         str(cluster["pr_count_observed"]) + " PRs, " +
                         str(cluster["shared_file_edge_count_observed"]) + " directe paren, " +
                         str(cluster["distinct_shared_file_count_observed"]) + " gedeelde bestanden.")
            for pr in cluster["participating_prs"]:
                lines.append("  - [PR #" + str(pr["number"]) + "](" + pr["url"] +
                             ") · " + str(pr["other_prs_with_shared_files_observed"]) +
                             " bestandsverbindingen · HEAD " + pr["head_sha"][:12])
        if not view["shown_collision_pairs"]:
            lines.append("Geen overlap in de gelezen steekproef; geen claimvrijgave.")
        for pair in view["shown_collision_pairs"]:
            left, right = pair["left"], pair["right"]
            lines.append("- [#" + str(left["number"]) + "](" + left["url"] +
                         ") ↔ [#" + str(right["number"]) + "](" + right["url"] +
                         "): " + str(pair["shared_file_count_observed"]) +
                         " gedeelde bestanden")
        lines.extend(["", "### Meerdere open PR's noemen hetzelfde issue (geen fix-/claimbewijs)", ""])
        for group in view["shown_duplicate_issue_groups"]:
            refs = ", ".join("[PR](" + pr["url"] + ")" for pr in group["shown_pr_sources"])
            lines.append("- [Issue #" + str(group["issue_number"]) + "](" +
                         group["issue_url"] + ") · " + refs)
        if view["duplicate_issue_groups_truncated"]:
            lines.append("- **ONVOLLEDIG:** er zijn meer issue-groepen dan getoond.")
        if view["unpaired_open_prs_not_claim_verified"]:
            lines.extend(["", "Zonder gedeelde paden binnen de steekproef (GEEN claimvrijgave):"])
            for pr in view["unpaired_open_prs_not_claim_verified"]:
                lines.append("- [#" + str(pr["number"]) + "](" + pr["url"] + ")")
        if view.get("source_truncated"):
            lines.append("Bronkaarten afgekapt; inspecteer overige PR's.")
    lines.extend(current_gate_lines(board, markdown=True))
    if board.get("historical_gate_episodes"):
        lines.extend(["", render_gate_episodes(board["historical_gate_episodes"], "markdown")])
    if board.get("historical_pr_delta"):
        lines.extend(["", render_delta(board["historical_pr_delta"], "markdown")])
    return "\n".join(lines) + "\n"


def as_html(board):
    cards = []
    for view in board["project_views"]:
        rows = []
        if view["source_status"] != "unavailable":
            for pair in view["shown_collision_pairs"]:
                left, right = pair["left"], pair["right"]
                a, b = escape(left["url"], quote=True), escape(right["url"], quote=True)
                files = "<br>".join(
                    "<code>" + escape(p) + "</code>" for p in pair["shared_files"])
                rows.append(
                    "<tr><td><a href='" + a +
                    "' rel='noopener noreferrer'>#" + str(left["number"]) +
                    "</a> ↔ <a href='" + b +
                    "' rel='noopener noreferrer'>#" + str(right["number"]) +
                    "</a></td><td>" + str(pair["shared_file_count_observed"]) +
                    "</td><td>" + files + "</td></tr>")
        if not rows:
            if view["source_status"] == "unavailable":
                rows = ["<tr><td colspan='3'>BRON ONBESCHIKBAAR: er is geen "
                        "open-PR-inventaris gelezen en geen uitspraak over overlap mogelijk.</td></tr>"]
            else:
                rows = ["<tr><td colspan='3'>Geen overlap zichtbaar in de "
                        "gelezen steekproef; afwezigheid is geen vrijgave.</td></tr>"]
        state = ("Bron onbeschikbaar" if view["source_status"] == "unavailable"
                 else "bronoverlap > uitvoerlimiet; open PR's zelf voor overige botsingen"
                 if view.get("source_truncated")
                 else "volledige huidige open-PR-steekproef" if view["sample_complete"]
                 else "onvolledige open-PR-steekproef")
        unpaired = view["unpaired_open_prs_not_claim_verified"]
        duplicates = []
        for group in view["shown_duplicate_issue_groups"]:
            urls = "".join("<li><a rel='noopener noreferrer' href='" +
                           escape(item["url"], quote=True) + "'>PR @" +
                           escape(item["head_sha"][:12]) + "</a></li>"
                           for item in group["shown_pr_sources"])
            duplicates.append("<li><a rel='noopener noreferrer' href='" +
                              escape(group["issue_url"], quote=True) +
                              "'>Issue #" + str(group["issue_number"]) +
                              "</a> · " + str(group["open_pr_mentions_observed"]) +
                              " PR-tekstverwijzingen (geen fix-bewijs)<ul>" +
                              urls + "</ul></li>")
        links = "".join(
            "<li><a rel='noopener noreferrer' href='" +
            escape(pr["url"], quote=True) + "'>PR #" +
            str(pr["number"]) + "</a></li>" for pr in unpaired)
        clusters = []
        for cluster in view["linked_pr_collision_clusters"]:
            members = "".join(
                "<li><a rel='noopener noreferrer' href='" +
                escape(pr["url"], quote=True) + "'>PR #" + str(pr["number"]) +
                "</a> · HEAD " + escape(pr["head_sha"][:12]) + " · " +
                str(pr["other_prs_with_shared_files_observed"]) +
                " directe bestandsverbindingen</li>"
                for pr in cluster["participating_prs"])
            clusters.append(
                "<li><strong>Groep " + str(cluster["component_number"]) +
                "</strong>: " + str(cluster["pr_count_observed"]) +
                " PRs, " + str(cluster["distinct_shared_file_count_observed"]) +
                " verschillende gedeelde bestanden<ul>" + members + "</ul></li>")
        cards.append(
            "<section><h2>" + escape(view["project"]) + "</h2>"
            "<p><strong>" + escape(state) +
            "</strong> · " + escape(view["repository"]) + "</p>"
            "<h3>Samenhangende PR-bestandsgroepen</h3>"
            "<p>Alleen geobserveerde bestanden, geen vrij eigenaarschap "
            "of mergevolgorde.</p><ol>" +
            ("".join(clusters) or "<li>Geen groepen geobserveerd.</li>") +
            "</ol><table><thead><tr><th>Open PR's</th><th>Gedeelde bestanden</th>"
            "<th>Bestandsnamen</th></tr></thead><tbody>" +
            "".join(rows) + "</tbody></table>"
            "<h3>Meerdere open PR's noemen hetzelfde issue</h3>"
            "<p>De links zijn tekstverwijzingen, geen bewezen oplossings-/eigenaarsrelatie.</p><ul>" +
            ("".join(duplicates) or "<li>Geen dubbele issue-vermeldingen in gelezen steekproef.</li>") +
            "</ul>"
            "<h3>Geen gedeelde paden in de steekproef</h3><p>Niet automatisch "
            "claimbaar; controleer nog steeds leases en PR-loze branches.</p><ul>" +
            (links or "<li>Geen andere PR's zichtbaar.</li>") +
            "</ul></section>")
    gate_panel = ""
    if board.get("current_live_pr_gate_observation") is not None:
        live = board["current_live_pr_gate_observation"]
        pr_cards = []
        for gate in live["items"]:
            url = escape(gate["url"], quote=True)
            if gate["status"] != "current_exact_pr_head_and_sample_match":
                detail = "GitHub-bron of exacte PR-HEAD veranderd; geen gates afleiden."
            else:
                detail = (
                    "HEAD " + escape(gate["head_sha_observed"][:12]) +
                    " · Check Runs lopend " + str(gate["observed_checks_pending"]) +
                    " / mislukt " + str(gate["observed_checks_not_successful"]) +
                    " / totaal gezien " + str(gate["check_runs_total_observed"]) +
                    " / onbekend " + str(gate["observed_checks_unknown"]) +
                    " · legacy pending " + str(gate["observed_legacy_pending"]) +
                    " / failed " + str(gate["observed_legacy_failure_or_error"]) +
                    " · draft " + str(gate["draft_at_read"]) +
                    " · reviewwijzigingen " +
                    str(gate["observed_exact_head_changes_requested"]) +
                    " · onafhankelijke approvals gezien " +
                    str(gate["observed_independent_exact_head_approvals"]) +
                    " · " + escape(GATE_LABELS.get(
                        gate["human_next_gate_step"], "Bron onvolledig.")))
            detail = (
                "BLOKKADE: " + escape(BLOCKER_DESCRIPTIONS[
                    gate["human_gate_blocker"]]) + " · " + detail)
            check_note = ""
            if gate["status"] == "current_exact_pr_head_and_sample_match":
                check_items = []
                for check in gate["observed_actionable_check_runs"]:
                    check_items.append("<li><a href=\'" + escape(check["url"], quote=True) +
                                       "\' rel=\'noopener noreferrer\'>" +
                                       escape(check["name"]) + "</a> · " +
                                       escape(check["state"]) + " · " +
                                       escape(check["conclusion"] or "geen conclusie") + "</li>")
                for status in gate["observed_actionable_legacy_contexts"]:
                    check_items.append("<li>Legacy status: <a href=\'" +
                                       escape(status["url"], quote=True) +
                                       "\' rel=\'noopener noreferrer\'>" +
                                       escape(status["context"]) + "</a> · " +
                                       escape(status["state"]) + "</li>")
                check_note = (
                    "<details><summary>Actuele checkjobs: " +
                    str(gate["check_runs_latest_attempts_observed"]) +
                    " contexten, " + str(gate["check_runs_superseded_attempts_observed"]) +
                    " eerdere pogingen</summary><ul>" +
                    ("".join(check_items) or "<li>Geen niet-groene jobs waargenomen.</li>") +
                    "</ul><p>Niet-getoonde jobs: " +
                    str(gate["observed_actionable_check_runs_hidden"]) +
                    " · vorige legacy-statuspogingen genegeerd: " +
                    str(gate["legacy_status_superseded_attempts_observed"]) +
                    " · verborgen legacy-blokkades: " +
                    str(gate["observed_actionable_legacy_contexts_hidden"]) +
                    " · branchregels blijven onbekend.</p></details>")
            source_note = ""
            if gate["observed_in_recent_pr_collision_sample"] is False:
                collision_links = []
                for other in gate["sampled_file_overlaps_observed"]:
                    other_url = escape(other["other_pr_url"], quote=True)
                    collision_links.append(
                        "<li><a href='" + other_url +
                        "' rel='noopener noreferrer'>" + other_url +
                        "</a>: " + str(other["shared_file_count_observed"]) +
                        " gedeelde paden (steekproef), o.a. " +
                        escape(", ".join(other["shared_paths_preview"])) + "</li>")
                source_note = (
                    "<p><strong>Gericht gelezen oudere PR</strong>: " +
                    (str(gate["focused_pr_file_pages_observed"]) +
                     " pagina('s), " + str(gate["focused_pr_paths_observed"]) +
                     " waargenomen bestandspaden. " if
                     gate.get("focused_pr_file_pages_observed") is not None
                     else "Bron onbereikbaar of geen product-PR. ") +
                    "Buiten "
                    "de recente collisionsteekproef, onvolledige dekking. "
                    "Geen botsing gezien is geen vrijgave.</p><ul>" +
                    "".join(collision_links) + "</ul>" +
                    ("<p>Extra links niet getoond: " +
                     str(gate["sampled_file_overlaps_hidden"]) + "</p>"
                     if gate["sampled_file_overlaps_hidden"] else ""))
            pr_cards.append(
                "<li><a href='" + url + "' rel='noopener noreferrer'>" +
                escape(gate["project"]) + " · " + url +
                "</a><br><small>" + detail +
                " · geen mergevrijgave</small>" + check_note + source_note + "</li>")
        gate_panel = (
            "<section><h2>Actuele PR-gates · maximaal vier</h2>"
            "<p>Bronselectie: " + escape(live["mode"]) +
            ". Expliciet gekozen: " +
            escape(", ".join(live["explicit_selected_prs"]) or
                   "automatisch uit bestaande stable product-PRs") + "</p>"
            "<p>Gate-bronmoment UTC: " +
            escape(live["observed_at_utc"]) +
            " · out-of-window bron onvolledig: " +
            escape(str(board["source_coverage_limited_by_direct_prs"])) +
            "</p>"
            "<p>Afzonderlijke read-only waarneming, niet atomair met de "
            "bestandscope. Bronclaims, release en verplichte regels "
            "zijn niet geautoriseerd.</p><ol>" +
            ("".join(pr_cards) or "<li>Geen betrouwbare PR-bron geselecteerd.</li>") +
            "</ol><p>" +
            ("Overige PR-gates NIET waargenomen. " if
             live["more_sampled_prs_not_gate_checked"] else "") +
            "Oudere gerichte PR's rechtstreeks gelezen: " +
            str(live["direct_explicit_prs_outside_recent_sample"]) +
            " · bron onbekend of niet-product: " +
            str(live["direct_explicit_pr_lookup_failed_or_not_product"]) +
            ". QA-only titels niet voor product-gatecontrole gekozen: " +
            str(live["qa_only_title_prs_not_inspected"]) +
            ". Maximaal " + str(live["max_additional_github_gets"]) +
            " extra GitHub GETs in deze optionele opname.</p></section>"
        )
    delta_panel = render_delta(board.get("historical_pr_delta"), "html")
    gate_episode_panel = render_gate_episodes(board.get("historical_gate_episodes"), "html")
    return (
        "<!doctype html><html lang='nl'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>zSentinel · Portfolio PR-conflicten</title>"
        "<style>body{background:#111820;color:#e8eef8;font:16px system-ui;"
        "max-width:1050px;margin:auto;padding:20px}section{margin-top:32px}"
        "table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid "
        "#44566d;padding:10px;text-align:left;vertical-align:top;"
        "overflow-wrap:anywhere}a{color:#b7d7ff}"
        "aside{border-left:4px solid #9aa9b5;background:#263446;padding:1rem}"
        "code{overflow-wrap:anywhere}</style>"
        "<h1>zSentinel · Portfolio PR-conflicten</h1>"
        "<p>Projecten: " + str(len(board["project_views"])) +
        " · bronmoment: " + escape(board["observed_at_utc"]) + "</p>"
        "<aside>Alleen gedeelde bestandspaden uit een begrensde GitHub-bron. "
        "Dit bewijst geen mergeconflicten, claims of vrije werkscope. "
        "Geen automatische actie, merge of deploy.</aside>" +
        attention_html(board) + gate_panel + gate_episode_panel + delta_panel +
        "".join(cards) + "</html>"
    )


MAX_PORTFOLIO_CSV_RECORDS = 2500
MAX_PORTFOLIO_CSV_BYTES = 2 * 1024 * 1024


def as_private_csv(board):
    """Sorted existing-PR evidence across registered projects, never release authority."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, dialect="excel", lineterminator="\r\n")
    columns = (
        "record_type", "project", "repository", "source_status",
        "source_complete_for_sample", "pr_number", "pr_url", "pr_head_sha",
        "pr_base_sha", "previous_pr_base_sha",
        "related_pr_url", "related_pr_head_sha", "cluster_number",
        "shared_filename", "shared_files_observed",
        "issue_number_hint", "current_exact_head_gate_status",
        "check_name", "check_state", "check_conclusion", "check_url",
        "prior_gate_blocker", "current_gate_blocker", "gate_episode_change",
        "gate_evidence_category", "operator_next_step",
        "attention_rank", "attention_group",
        "historical_gate_change",
        "owner_claim_verified", "merge_authorized", "operator_notice",
    )
    writer.writerow(columns)
    written = 0

    def safe(value):
        raw = "".join(
            "\\u%04x" % ord(char) if ord(char) < 32 or ord(char) == 127
            else char for char in str(value))
        if raw.lstrip(" \u200b\u200c\u200d\u2060\ufeff").startswith(("=", "+", "-", "@")):
            return "'" + raw
        return raw

    def emit(kind, project, repo, source_status, **values):
        nonlocal written
        if not set(values).issubset(columns):
            raise ValueError("unexpected private portfolio CSV column")
        written += 1
        if written > MAX_PORTFOLIO_CSV_RECORDS:
            raise ValueError("private portfolio CSV row limit")
        fixed = {
            "record_type": kind, "project": project, "repository": repo,
            "source_status": source_status, "owner_claim_verified": "false",
            "merge_authorized": "false", **values,
        }
        writer.writerow([safe(fixed.get(column, "")) for column in columns])
        if output.tell() > MAX_PORTFOLIO_CSV_BYTES:
            raise ValueError("private portfolio CSV output limit")

    for view in board["project_views"]:
        project = view["project"]
        repo = view["repository"]
        source_status = view["source_status"]
        summary = {
            "source_complete_for_sample": "true" if view["sample_complete"] else "false",
        }
        emit("project_summary", project, repo, source_status, **summary,
             operator_notice="Partial non-atomic PR observations; claims and release rules UNKNOWN.")
        if source_status == "unavailable":
            continue
        for group in view["linked_pr_collision_clusters"]:
            for pr in group["participating_prs"]:
                emit("shared_file_cluster_pr", project, repo, source_status,
                     **summary, cluster_number=group["component_number"],
                     pr_number=pr["number"], pr_url=pr["url"],
                     pr_head_sha=pr["head_sha"],
                     shared_files_observed=group["distinct_shared_file_count_observed"],
                     operator_notice="Observed shared paths, NOT source ownership or merge approval.")
            for file_row in group["shared_file_pr_matrix"]:
                for touching in file_row["touching_prs"]:
                    emit("shared_file_exact_pr", project, repo, source_status,
                         **summary, cluster_number=group["component_number"],
                         shared_filename=file_row["path"],
                         pr_number=touching["number"], pr_url=touching["url"],
                         pr_head_sha=touching["head_sha"],
                         operator_notice="Exact shared filename on this PR HEAD; "
                                         "ownership and merge status NOT verified.")
            if group["shared_files_sample_truncated"]:
                emit("source_warning", project, repo, source_status, **summary,
                     cluster_number=group["component_number"],
                     operator_notice="More filenames beyond this cluster sample.")
        for pair in view["shown_collision_pairs"]:
            emit("shared_file_pr_pair", project, repo, source_status,
                 **summary, pr_number=pair["left"]["number"],
                 pr_url=pair["left"]["url"],
                 pr_head_sha=pair["left"]["head_sha"],
                 related_pr_url=pair["right"]["url"],
                 related_pr_head_sha=pair["right"]["head_sha"],
                 shared_files_observed=pair["shared_file_count_observed"],
                 operator_notice="Observed common filenames, Git merge conflict NOT verified.")
        for issue in view["shown_duplicate_issue_groups"]:
            for pr in issue["shown_pr_sources"]:
                emit("unverified_issue_number", project, repo, source_status,
                     **summary, issue_number_hint=issue["issue_number"],
                     pr_url=pr["url"], pr_head_sha=pr["head_sha"],
                     operator_notice="Number in PR text only; issue identity and fix UNKNOWN.")
        for pr in view["unpaired_open_prs_not_claim_verified"]:
            emit("unpaired_pr_in_sample", project, repo, source_status, **summary,
                 pr_number=pr["number"], pr_url=pr["url"],
                 pr_head_sha=pr["head_sha"],
                 operator_notice="Unpaired in sample only; NOT an unclaimed PR.")
        if view["source_truncated"] or view["duplicate_issue_groups_truncated"]:
            emit("source_warning", project, repo, source_status, **summary,
                 operator_notice="Other PR pairs/issues omitted in the bounded display.")
    live = board.get("current_live_pr_gate_observation")
    if live is not None:
        for item in live["items"]:
            url = item["url"]
            project = next((v["project"] for v in board["project_views"]
                            if url.startswith("https://github.com/" +
                                             v["repository"] + "/pull/")), None)
            if project is None:
                raise ValueError("unexpected outside-repo current gate")
            view = next(v for v in board["project_views"] if v["project"] == project)
            emit("current_gate_observation", project, view["repository"],
                 view["source_status"], pr_url=url,
                 pr_head_sha=item.get("head_sha_observed") or "",
                 pr_base_sha=item.get("base_sha_observed") or "",
                 current_exact_head_gate_status=item["status"],
                 operator_notice="Observed gate only; branch protection/ownership still unknown.")
            if item["status"] != "current_exact_pr_head_and_sample_match":
                continue
            for check in item["observed_actionable_check_runs"]:
                emit("current_check_job", project, view["repository"],
                     view["source_status"], pr_url=url,
                     pr_head_sha=item["head_sha_observed"],
                     pr_base_sha=item["base_sha_observed"],
                     current_exact_head_gate_status=item["status"],
                     check_name=check["name"], check_state=check["state"],
                     check_conclusion=check["conclusion"] or "",
                     check_url=check["url"],
                     operator_notice="Latest sampled Check Run attempt; not required-check or merge proof.")
            for status in item["observed_actionable_legacy_contexts"]:
                emit("current_legacy_status", project, view["repository"],
                     view["source_status"], pr_url=url,
                     pr_head_sha=item["head_sha_observed"],
                     pr_base_sha=item["base_sha_observed"],
                     current_exact_head_gate_status=item["status"],
                     check_name=status["context"], check_state=status["state"],
                     check_url=status["url"],
                     operator_notice="Latest sampled legacy status per context; no merge authorization.")
            if (item["observed_actionable_check_runs_hidden"] or
                    item["observed_actionable_legacy_contexts_hidden"]):
                emit("source_warning", project, view["repository"],
                     view["source_status"], pr_url=url,
                     pr_head_sha=item["head_sha_observed"],
                     operator_notice="Some current non-green check jobs/statuses hidden by per-PR display limit.")
    episodes = board.get("historical_gate_episodes")
    if episodes is not None:
        for row in episodes["items"]:
            project = row["project"]
            repo = next((view["repository"] for view in board["project_views"]
                         if view["project"] == project), None)
            if repo is None:
                raise ValueError("historical gate outside canonical project")
            emit("gate_episode_history", project, repo, "historical_gate_comparison",
                 pr_url=row["url"], pr_head_sha=row["current_head"] or "",
                 pr_base_sha=row["current_base"] or "",
                 previous_pr_base_sha=row["previous_base"] or "",
                 prior_gate_blocker=row["previous_blocker"] or "",
                 current_gate_blocker=row["current_blocker"] or "",
                 gate_episode_change=row["change"],
                 operator_next_step=row["operator_next_step"],
                 operator_notice="Two sampled observations only; no automatic recheck, merge or owner authority.")
            for category in row["observed_changed_gate_evidence_categories"]:
                emit("gate_episode_evidence_change", project, repo,
                     "historical_gate_comparison",
                     pr_url=row["url"], pr_head_sha=row["current_head"] or "",
                     pr_base_sha=row["current_base"] or "",
                     gate_evidence_category=category,
                     gate_episode_change=row["change"],
                     operator_next_step=row["operator_next_step"],
                     operator_notice="Changed check, legacy or individual review evidence; release gates unchanged.")
            for detail in row["observed_changed_non_green_checks"]:
                emit("gate_episode_changed_check", project, repo,
                     "historical_gate_comparison",
                     pr_url=row["url"], pr_head_sha=row["current_head"] or "",
                     current_exact_head_gate_status="two_snapshot_observation_only",
                     check_name=detail["label"], check_url=detail["url"],
                     gate_episode_change=detail["kind"],
                     operator_notice="Observed change in bounded non-green check samples, not a fix or release proof.")
            if row["observed_changed_non_green_checks_hidden"]:
                emit("source_warning", project, repo,
                     "historical_gate_comparison", pr_url=row["url"],
                     operator_notice="Additional gate episode check changes hidden by preview limit.")
        if episodes["comparison_status"] != "two_bounded_observations":
            for view in board["project_views"]:
                emit("source_warning", view["project"], view["repository"],
                     "historical_gate_comparison",
                     operator_notice="Previous or current gate receipts absent; no stable gate conclusion.")
    desk = board.get("merge_first_attention")
    if desk is not None:
        if (desk.get("merge_authorized") is not False or
                desk.get("claim_authorized") is not False or
                desk.get("writes_performed") is not False):
            raise ValueError("non-read-only PR attention source")
        for row in desk["items"]:
            emit("merge_first_action", row["project"], row["repository"],
                 "current_exact_head_gate_observation",
                 pr_url=row["url"], pr_number=row["number"],
                 pr_head_sha=row["head_sha"] or "",
                 pr_base_sha=row["base_sha"] or "",
                 current_gate_blocker=row["blocker"],
                 attention_rank=row["rank"],
                 attention_group=row["attention_group"],
                 historical_gate_change=row["historical_gate_change"] or "",
                 operator_next_step=row["human_next_step"],
                 operator_notice=(
                     "Human owner action only; NOT exact required checks, "
                     "owner lease, branch-protection or merge authorization."))
            for job in row["non_green_jobs"]:
                emit("merge_first_blocking_job", row["project"], row["repository"],
                     "current_exact_head_gate_observation",
                     pr_url=row["url"], pr_number=row["number"],
                     pr_head_sha=row["head_sha"] or "",
                     pr_base_sha=row["base_sha"] or "",
                     attention_rank=row["rank"],
                     operator_next_step=row["human_next_step"],
                     check_name=job["name"], check_state=job["state"],
                     check_url=job["url"],
                     operator_notice=(
                         "Observed non-green job in capped current sample; "
                         "GitHub required-rule membership NOT verified."))
            for related in row["observed_file_overlap_prs"]:
                emit("merge_first_related_pr", row["project"], row["repository"],
                     "current_exact_head_gate_observation",
                     pr_url=row["url"], pr_number=row["number"],
                     related_pr_url=related, attention_rank=row["rank"],
                     operator_notice=(
                         "Observed filename overlap; NOT confirmed Git conflict "
                         "or ownership/merge order."))
            if row["non_green_jobs_hidden"]:
                emit("source_warning", row["project"], row["repository"],
                     "current_exact_head_gate_observation",
                     pr_url=row["url"],
                     operator_notice=(
                         "More non-green check contexts than action desk preview; "
                         "inspect original GitHub source."))
        if desk["source_not_a_complete_pr_inventory"]:
            for view in board["project_views"]:
                emit("source_warning", view["project"], view["repository"],
                     "current_exact_head_gate_observation",
                     operator_notice=(
                         "Merge-first action desk covers <=4 selected PRs, "
                         "not the full repository backlog."))
    # The caller checks the true UTF-8 output length and creates a 0600
    # output exclusively. Never put sensitive cross-project CSV into stdout.
    return "\ufeff" + output.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument("--max-prs", type=int, default=10)
    parser.add_argument("--output", type=Path,
                        help="Create NEW private offline receipt (0600); default stdout")
    parser.add_argument("--compare-previous", type=Path,
                        help="Private 0600 JSON snapshot from an earlier run, same projects")
    parser.add_argument("--gate-pr", action="append", default=[],
                        help="Explicit project#PR target (max four); older PRs use up to "
                             "three extra bounded file pages per target")
    parser.add_argument("--inspect-current-pr-gates", action="store_true",
                        help="Optioneel tot vier exacte-head GitHub CI/reviewbonnen "
                             "in bestaande PR-steekproef (max 20 extra GETs)")
    parser.add_argument("--format", choices=("text", "json", "html", "markdown", "csv", "workorders", "workorders-json", "workorders-html", "workorders-csv"),
                        default="text")
    args = parser.parse_args(argv)
    try:
        canonical = registry(args.registry)
        board = collect_portfolio(args.project, canonical,
                                  os.environ.get("GITHUB_TOKEN"), args.max_prs)
        if args.gate_pr and not args.inspect_current_pr_gates:
            raise ValueError("--gate-pr requires --inspect-current-pr-gates")
        if args.format in ("workorders", "workorders-json", "workorders-html", "workorders-csv"):
            if not args.inspect_current_pr_gates:
                raise ValueError("private workorders require --inspect-current-pr-gates")
            if args.output is None:
                raise ValueError("private workorders require exclusive --output")
        if args.inspect_current_pr_gates:
            board = current_merge_first_gates(board, os.environ.get("GITHUB_TOKEN"),
                                             focus_prs=args.gate_pr)
        if args.compare_previous is not None:
            previous = read_private_previous(args.compare_previous)
            board["historical_pr_delta"] = compare(previous, board)
            if args.inspect_current_pr_gates:
                board["historical_gate_episodes"] = compare_gate_episodes(previous, board)
        if args.inspect_current_pr_gates:
            board["merge_first_attention"] = merge_first_attention(board)
        if args.format in ("workorders", "workorders-json", "workorders-html", "workorders-csv"):
            from zsentinel_pr_workorders import (
                collect_workorders, render_markdown, render_html, render_csv)
            workorder_bundle = collect_workorders(board)
            if args.format == "workorders":
                rendered = render_markdown(workorder_bundle)
            elif args.format == "workorders-html":
                rendered = render_html(workorder_bundle)
            elif args.format == "workorders-csv":
                rendered = render_csv(workorder_bundle)
            else:
                rendered = json.dumps(workorder_bundle, ensure_ascii=False, indent=2) + "\n"
                if len(rendered.encode("utf-8")) > 512 * 1024:
                    raise ValueError("private workorder JSON exceeds 512 KiB")
        elif args.format == "csv":
            if args.output is None:
                raise ValueError("private portfolio CSV requires --output")
            rendered = as_private_csv(board)
            if len(rendered.encode("utf-8")) > MAX_PORTFOLIO_CSV_BYTES:
                raise ValueError("private portfolio CSV output exceeds byte limit")
        elif args.format == "json":
            rendered = json.dumps(board, indent=2, ensure_ascii=False) + "\n"
        elif args.format == "html":
            rendered = as_html(board)
        elif args.format == "markdown":
            rendered = as_markdown(board)
        else:
            rendered = as_text(board) + "\n"
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            # Exclusive local creation. Do not overwrite another worker's
            # reports and never echo private path or GitHub source in logs.
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(args.output, flags, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as sink:
                    if not stat.S_ISREG(os.fstat(sink.fileno()).st_mode):
                        raise ValueError("private report output is not a regular file")
                    sink.write(rendered)
                    sink.flush()
                    os.fsync(sink.fileno())
            except Exception:
                args.output.unlink(missing_ok=True)
                raise
            print("Created private 0600 read-only zSentinel operator artifact; no remote writes.",
                  file=sys.stderr)
        if (args.inspect_current_pr_gates and
                any(g["status"] != "current_exact_pr_head_and_sample_match"
                    for g in board["current_live_pr_gate_observation"]["items"])):
            return 3
        return (2 if board["any_file_overlaps_observed"] else
                3 if not board["source_coverage_complete"] else 0)
    except (ValueError, OSError):
        print("zSentinel: portfolio PR source unavailable or invalid", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
