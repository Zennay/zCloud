#!/usr/bin/env python3
"""Merge-first zSentinel human operator work queue from already collected evidence.

Pure transformation: does not poll providers, issue claims, dispatch, or approve
a release. Missing evidence always blocks automated authorisation.
"""
from __future__ import annotations

from collections import Counter
import re

PR = re.compile(r"^https://github\.com/Zennay/[A-Za-z0-9_.-]+/pull/[1-9][0-9]{0,8}$")
ISSUE = re.compile(r"^https://github\.com/Zennay/[A-Za-z0-9_.-]+/issues/[1-9][0-9]{0,8}$")
MAX_CARDS = 60
ORDER = {"P0": 0, "P1": 1, "P2": 2, "Unspecified": 3}
LABELS = {
    "human_infrastructure": "Bevoegde infrastructuureigenaar nodig",
    "missing_current_source": "Ververs onvolledige actuele bron",
    "existing_pr_failed_gates": "Bestaande PR: eigenaar repareert falende gates",
    "existing_pr_waits_on_gates": "Bestaande PR: wacht op exacte-HEAD CI/review",
    "existing_pr_scope_overlap": "Bestaande PR: stem bestandsoverlap af",
    "existing_pr_changed": "Bestaande PR: beoordeel nieuwe bronwijziging",
    "existing_pr_owner_review": "Bestaande PR: eigenaarschap en mergegates verifiëren",
    "owner_before_new_product_work": "Controleer eigenaar voordat productwerk start",
}
GUIDANCE = {
    "human_infrastructure": (
        "Alleen bevoegde infra-/billingbeheerder kan de P0 oplossen. Geen runner-, "
        "billing-, productie- of securityactie vanuit deze werkbak."),
    "missing_current_source": (
        "Bron te oud of incompleet; lees huidige issue/PR/claimgegevens opnieuw "
        "voordat een eigenaars- of herstelbesluit wordt genomen."),
    "existing_pr_failed_gates": (
        "Laat de huidige PR-eigenaar mislukte exacte-HEAD checks of gevraagde "
        "reviewwijzigingen oplossen. Geen gate overslaan."),
    "existing_pr_waits_on_gates": (
        "Bestaande PR heeft lopende of ontbrekende gatebewijzen. Parkeer alleen "
        "die PR en pak een onafhankelijk productissue op."),
    "existing_pr_scope_overlap": (
        "Bestaand werk raakt dezelfde bestanden. Stem de wijziging met "
        "de actuele eigenaren af; geen branch of claim overnemen."),
    "existing_pr_changed": (
        "Bronverschil op bestaande PR gezien. Lees de nieuwe implementatie, "
        "actuele eigenaar en exacte-HEAD gates vóór een mergebesluit."),
    "existing_pr_owner_review": (
        "Bekijk eerst bestaande PR, actuele claim, verplichte branchregels, "
        "onafhankelijke review en CI. Geen tweede implementatie starten."),
    "owner_before_new_product_work": (
        "Geen gekoppelde PR in deze steekproef; controleer actieve claims, "
        "PR-loze branches en volledige scope vóór nieuw productwerk."),
}


def build(report):
    """Priority-first, bounded read-only P0/P1 dispatch suggestions for humans.

    The optional gate data is partial even when GitHub reported a successful
    check; it does not imply branch rules, owner authority or merge permission.
    """
    if (type(report) is not dict
            or report.get("schema") != "zsentinel-integrated-issue-pr-scope-v1"
            or any(report.get(flag) is not False for flag in
                   ("claims_verified", "merge_authorized", "deploy_authorized",
                    "writes_performed", "automatic_dispatch", "may_take_ownership"))
            or type(report.get("issues")) is not list
            or len(report["issues"]) > MAX_CARDS):
        raise ValueError("untrusted source for merge-first operator work queue")
    coverage = {}
    for view in report.get("github_scope_by_project", []):
        if (type(view) is not dict or type(view.get("project")) is not str
                or view["project"] in coverage
                or type(view.get("pr_sample_complete")) is not bool
                or type(view.get("issue_mention_inventory_complete")) is not bool):
            raise ValueError("missing per-project collision and issue source coverage")
        coverage[view["project"]] = view
    cards = []
    seen = set()
    for issue in report["issues"]:
        if (type(issue) is not dict or issue.get("priority") not in ORDER
                or type(issue.get("url")) is not str
                or ISSUE.fullmatch(issue["url"]) is None
                or type(issue.get("issue")) is not str
                or type(issue.get("project")) is not str
                or type(issue.get("title")) is not str
                or not 1 <= len(issue["title"]) <= 180
                or issue["url"] in seen or issue.get("merge_authorized") is not False
                or issue.get("claim_authorized") is not False):
            raise ValueError("untrusted or duplicate source issue")
        seen.add(issue["url"])
        if issue["project"] not in coverage:
            raise ValueError("issue has no matching project scope observation")
        scope = coverage[issue["project"]]
        refs = issue.get("existing_prs")
        if type(refs) is not list or len(refs) > 5:
            raise ValueError("unbounded linked existing PR inventory")
        selected = None
        link_count = 0
        for p in refs:
            url = p.get("url") if type(p) is dict else None
            if (type(url) is not str or PR.fullmatch(url) is None
                    or url.split("/pull/", 1)[0] != issue["url"].split("/issues/", 1)[0]
                    or p.get("merge_authorized") is not False):
                raise ValueError("untrusted linked PR or authority")
            link_count += 1
            live = p.get("live_exact_head_review_checks_observation")
            has_live_gate = type(live) is dict and (
                live.get("status") == "observed_current_head_partial_gate_evidence")
            failed = bool(has_live_gate and (
                live.get("observed_checks_not_successful", 0) > 0 or
                live.get("legacy_status_failure_or_error_observed", 0) > 0 or
                live.get("observed_exact_head_changes_requested", 0) > 0))
            pending = bool(has_live_gate and (
                live.get("observed_checks_pending", 0) > 0 or
                live.get("legacy_status_pending_observed", 0) > 0))
            overlap = p.get("file_scope_state") == "shared_file_paths_observed"
            moved = p.get("historical_source_activity_observed") is True
            # Select the most informative existing candidate; never use the
            # score as an indication of merge readiness or an owner lease.
            rank = (int(has_live_gate), int(failed), int(overlap),
                    int(moved), int(pending), -link_count)
            if selected is None or rank > selected[0]:
                selected = (rank, p, has_live_gate, failed, pending, overlap, moved)
        if issue["url"] in (
                "https://github.com/Zennay/zCloud/issues/1326",
                "https://github.com/Zennay/zCloud/issues/1523"):
            action = "human_infrastructure"
        elif issue.get("issue_evidence_stale") is True or not report.get(
                "source_clocks_within_10_minutes", False):
            action = "missing_current_source"
        elif selected is not None:
            _, chosen, live, failed, pending, overlap, moved = selected
            if failed:
                action = "existing_pr_failed_gates"
            elif pending:
                action = "existing_pr_waits_on_gates"
            elif overlap:
                action = "existing_pr_scope_overlap"
            elif moved:
                action = "existing_pr_changed"
            else:
                action = "existing_pr_owner_review"
        else:
            action = "owner_before_new_product_work"
        chosen_url = selected[1]["url"] if selected else None
        # A complete-looking positive observation isn't current claim/review
        # authorisation. The human has to verify those in the source app.
        card = {
            "issue": issue["issue"], "issue_url": issue["url"],
            "title": issue["title"], "priority": issue["priority"],
            "project": issue["project"], "category": action,
            "category_nl": LABELS[action], "human_next_step_nl": GUIDANCE[action],
            "existing_pr_count_in_sample": len(refs),
            "candidate_existing_pr_url": chosen_url,
            "candidate_pr_observation_only": True if selected else None,
            "historic_pr_movement_in_sample": issue.get(
                "historical_linked_pr_activity_observed") is True,
            "pr_file_scope_sample_complete": scope["pr_sample_complete"],
            "issue_reference_sample_complete": scope["issue_mention_inventory_complete"],
            "issue_reference_duplicates_sample_complete": (
                issue.get("duplicate_mentions_sample_complete") is True),
            "source_coherent": report.get("source_clocks_within_10_minutes") is True,
            "live_owner_verified": False, "required_rules_verified": False,
            "exact_head_merge_authorized": False, "claim_authorized": False,
            "dispatch_authorized": False, "release_authorized": False,
        }
        cards.append(card)
    cards.sort(key=lambda row: (
        ORDER[row["priority"]],
        0 if row["candidate_existing_pr_url"] else 1,
        0 if row["category"] == "existing_pr_failed_gates" else 1,
        row["project"], row["issue_url"]))
    totals = Counter(item["category"] for item in cards)
    return {
        "schema": "zsentinel-merge-first-human-inbox-v1",
        "observed_issue_source_at_utc": report.get("source_issue_observed_at_utc"),
        "observed_pr_source_at_utc": report.get("source_pr_observed_at_utc"),
        "issue_count": len(cards),
        "existing_pr_first_issues": sum(bool(c["candidate_existing_pr_url"]) for c in cards),
        "human_only_infrastructure_issues": totals["human_infrastructure"],
        "observed_action_counts": dict(sorted(totals.items())),
        "cards": cards,
        "current_claims_verified": False,
        "source_complete_for_authorized_dispatch": False,
        "merge_authorized": False, "claim_authorized": False,
        "automatic_dispatch": False, "release_authorized": False,
        "writes_performed": False,
    }


def render_compact_inbox(board, style="markdown", max_rows=20):
    """Human handoff using canonical links, not independent task assignment."""
    if (board.get("schema") != "zsentinel-merge-first-human-inbox-v1"
            or board.get("merge_authorized") is not False
            or board.get("automatic_dispatch") is not False
            or type(max_rows) is not int or not 1 <= max_rows <= MAX_CARDS
            or style not in ("markdown", "text")):
        raise ValueError("invalid bounded human inbox handoff")
    def inert(value):
        clean = re.sub(r"[\r\n\t]+", " ", value)
        return re.sub(r"([\\*_\[\]()|<>\x60~!#])", r"\\\1", clean)[:240]
    lines = [
        "# zSentinel · bestaande PR's eerst" if style == "markdown"
        else "zSentinel | bestaande PR's eerst",
        "",
        "Bron issue UTC: " + str(board["observed_issue_source_at_utc"]),
        "Bron PR UTC: " + str(board["observed_pr_source_at_utc"]),
        "Waargenomen issues: " + str(board["issue_count"]) +
        "; met een bestaande PR in de beperkte bron: " +
        str(board["existing_pr_first_issues"]),
        "GEEN huidige claims, verplichte branchregels, mergebevoegdheid of "
        "live herstel geverifieerd.",
        "",
    ]
    for index, item in enumerate(board["cards"][:max_rows], 1):
        issue = (f"[{item['issue']}]({item['issue_url']})"
                 if style == "markdown" else item["issue_url"])
        title = inert(item["title"]) if style == "markdown" else re.sub(
            r"[\r\n\t]+", " ", item["title"])
        lines.extend([
            str(index) + ". " + item["priority"] + " · " +
            item["project"] + " · " + issue + " · " + title,
            "   " + item["category_nl"],
            "   " + item["human_next_step_nl"],
            "   Brondekking: PR-bestanden " +
            ("compleet in gelezen steekproef" if
             item["pr_file_scope_sample_complete"] else "ONVOLLEDIG") +
            "; PR-issueverwijzingen " +
            ("compleet in gelezen steekproef" if
             item["issue_reference_sample_complete"] else "ONVOLLEDIG") +
            "; actuele eigenaar/merge UNKNOWN.",
        ])
        if item["candidate_existing_pr_url"]:
            url = item["candidate_existing_pr_url"]
            lines.append("   PR eerst: " + (
                "[Open bestaande PR](" + url + ")" if style == "markdown"
                else url))
    if len(board["cards"]) > max_rows:
        lines.append("")
        lines.append("ONVOLLEDIG overzicht: " +
                     str(len(board["cards"]) - max_rows) +
                     " overige issues niet getoond. Pas --worklist-limit aan.")
    lines.extend([
        "", "Deze overdracht is een menselijke review-werkbak, "
        "geen claim-/merge-/dispatch- of deployautorisatie.",
    ])
    return "\n".join(lines) + "\n"
