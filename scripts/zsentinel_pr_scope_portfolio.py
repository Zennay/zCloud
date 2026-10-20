#!/usr/bin/env python3
"""zSentinel portfolio PR-conflict board: one command, up to 3 canonical projects.

This is a read-only inspection helper. It does not grant source ownership,
release rights, leases, CI status, or permission to edit another worker's work.
"""
from __future__ import annotations

import argparse
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
            # This two-GET direct observation does not verify its conflicts
            # with PRs outside the ordinary capped collision inventory.
            repo = source_projects[project]
            direct = selected_source_pr(repo, int(number_text), token)
            if direct["product_candidate_based_on_title"] is not True:
                raise ValueError("QA-only or non-product selected PR")
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
                "observed_in_recent_pr_collision_sample":
                    not captured.get("direct_explicit_source_only", False),
                "complete_file_collision_coverage_verified": False,
                "sampled_file_overlaps_observed":
                    captured.get("sampled_file_overlaps_observed", []),
                "sampled_file_overlaps_hidden":
                    captured.get("sampled_file_overlaps_hidden", 0),
                "status": "current_exact_pr_head_and_sample_match",
                "draft_at_read": gate["draft_at_read"],
                "github_mergeable_boolean_observed": gate["github_mergeable_boolean_observed"],
                "check_runs_total_observed": gate["check_runs_total_observed"],
                "observed_checks_pending": gate["observed_checks_pending"],
                "observed_checks_unknown": gate["observed_checks_unknown"],
                "observed_checks_not_successful": gate["observed_checks_not_successful"],
                "observed_legacy_pending": gate["legacy_status_pending_observed"],
                "legacy_contexts_observed": gate["legacy_status_contexts_observed"],
                "legacy_status_source_may_be_truncated":
                    gate["legacy_status_source_may_be_truncated"],
                "observed_legacy_failure_or_error":
                    gate["legacy_status_failure_or_error_observed"],
                "observed_independent_exact_head_approvals":
                    gate["observed_independent_exact_head_approvals"],
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
    result = {
        **board,
        "current_live_pr_gate_observation": {
            "mode": ("explicit_product_pr_refs_with_bounded_direct_lookup"
                     if focus_prs and fallback_count else
                     "explicit_stable_sampled_product_pr_refs" if focus_prs else
                     "opt_in_one_shot_at_most_four_stable_sampled_prs"),
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "items": output,
            "inspected_stable_sample_prs": total_inspected,
            "pr_gate_receipts_requested": len(selected),
            "direct_explicit_prs_outside_recent_sample": fallback_count,
            "extra_direct_pr_identity_gets": fallback_count * 2,
            "observed_gate_blocker_counts": {
                category: sum(row["human_gate_blocker"] == category for row in output)
                for category in sorted({row["human_gate_blocker"] for row in output})
            },
            "explicit_selected_prs": list(focus_prs),
            "qa_only_title_prs_not_inspected": qa_only_skipped,
            "more_sampled_prs_not_gate_checked": (
                total_inspected > len(selected) - fallback_count),
            "source_unavailable_or_changed_projects": incomplete_projects,
            "max_additional_github_gets": len(selected) * 5 + fallback_count * 2,
            "file_scope_and_gate_reads_are_atomic": False,
            "required_branch_protection_verified": False,
            "notion_claims_verified": False,
            "independent_review_policy_verified": False,
            "merge_authorized": False,
            "deploy_authorized": False,
            "writes_performed": False,
        },
        "maximum_github_gets": (board["maximum_github_gets"] + len(selected) * 5
                                + fallback_count * 2),
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
        "Open PR-teksten met hetzelfde issue (GEEN fix- of eigenaarsbewijs): " +
        str(view["observed_duplicate_issue_groups"]),
        "Bron: " + ("volledige huidige open PR-lijst (geen leasebewijs)"
                     if view["sample_complete"] else "ONVOLLEDIG: andere PR's kunnen ontbreken"),
    ]
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
        if (not gate["check_page_complete"]
                or gate["review_page_may_be_truncated"]
                or gate["legacy_status_source_may_be_truncated"]):
            lines.append("  WAARSCHUWING: niet alle actuele check/reviewgegevens waargenomen.")
    lines.append("Afzonderlijke Notion-claims, branch protection, eigenaar/release "
                 "en verplichte checks blijven NIET vrijgegeven.")
    return lines


def as_text(board):
    output = [
        "zSentinel | Portfolio PR-conflictbalie (alleen lezen)",
        "Projecten: " + ", ".join(board["projects"]),
    ]
    for view in board["project_views"]:
        output.append("")
        output.append("[" + view["project"] + "] " + view["repository"])
        output.extend(summary_lines(view))
    output.extend(current_gate_lines(board))
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
        cards.append(
            "<section><h2>" + escape(view["project"]) + "</h2>"
            "<p><strong>" + escape(state) +
            "</strong> · " + escape(view["repository"]) + "</p>"
            "<table><thead><tr><th>Open PR's</th><th>Gedeelde bestanden</th>"
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
                    "<p><strong>Gericht gelezen oudere PR</strong>: buiten "
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
                " · geen mergevrijgave</small>" + source_note + "</li>")
        gate_panel = (
            "<section><h2>Actuele PR-gates · maximaal vier</h2>"
            "<p>Bronselectie: " + escape(live["mode"]) +
            ". Expliciet gekozen: " +
            escape(", ".join(live["explicit_selected_prs"]) or
                   "automatisch uit bestaande stable product-PRs") + "</p>"
            "<p>Afzonderlijke read-only waarneming, niet atomair met de "
            "bestandscope. Bronclaims, release en verplichte regels "
            "zijn niet geautoriseerd.</p><ol>" +
            ("".join(pr_cards) or "<li>Geen betrouwbare PR-bron geselecteerd.</li>") +
            "</ol><p>" +
            ("Overige PR-gates NIET waargenomen. " if
             live["more_sampled_prs_not_gate_checked"] else "") +
            "Oudere gerichte PR's rechtstreeks gelezen: " +
            str(live["direct_explicit_prs_outside_recent_sample"]) +
            ". QA-only titels niet voor product-gatecontrole gekozen: " +
            str(live["qa_only_title_prs_not_inspected"]) +
            ". Maximaal " + str(live["max_additional_github_gets"]) +
            " extra GitHub GETs in deze optionele opname.</p></section>"
        )
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
        gate_panel + "".join(cards) + "</html>"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument("--max-prs", type=int, default=10)
    parser.add_argument("--output", type=Path,
                        help="Create NEW private offline receipt (0600); default stdout")
    parser.add_argument("--gate-pr", action="append", default=[],
                        help="Optional explicit registered-project#PR target (up to four), "
                             "must occur in stable sampled product PRs")
    parser.add_argument("--inspect-current-pr-gates", action="store_true",
                        help="Optioneel tot vier exacte-head GitHub CI/reviewbonnen "
                             "in bestaande PR-steekproef (max 20 extra GETs)")
    parser.add_argument("--format", choices=("text", "json", "html", "markdown"),
                        default="text")
    args = parser.parse_args(argv)
    try:
        canonical = registry(args.registry)
        board = collect_portfolio(args.project, canonical,
                                  os.environ.get("GITHUB_TOKEN"), args.max_prs)
        if args.gate_pr and not args.inspect_current_pr_gates:
            raise ValueError("--gate-pr requires --inspect-current-pr-gates")
        if args.inspect_current_pr_gates:
            board = current_merge_first_gates(board, os.environ.get("GITHUB_TOKEN"),
                                             focus_prs=args.gate_pr)
        if args.format == "json":
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
            print("Created new private read-only portfolio dashboard (0600); no remote writes.",
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
