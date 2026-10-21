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
ACTIVITY_ORDER = {
    "quiet_over_7_days_observed_claims_unknown": 0,
    "quiet_over_7_days_partial_pr_coverage": 1,
    "activity_timestamp_unknown": 2,
    "source_clock_incoherent": 3,
    "recent_linked_pr_activity_observed": 4,
    "recent_issue_activity_observed": 5,
}
LABELS = {
    "human_infrastructure": "Bevoegde infrastructuureigenaar nodig",
    "missing_current_source": "Ververs onvolledige actuele bron",
    "existing_pr_failed_gates": "Bestaande PR: eigenaar repareert falende gates",
    "existing_pr_waits_on_gates": "Bestaande PR: wacht op exacte-HEAD CI/review",
    "existing_pr_gate_unobserved": "Bestaande PR: actuele CI/reviewbron ontbreekt",
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
        "Bestaande PR heeft lopende gatebewijzen. Parkeer alleen "
        "die PR en pak een onafhankelijk productissue op."),
    "existing_pr_gate_unobserved": (
        "Voor deze bestaande PR is geen verifieerbare exacte-HEAD CI/reviewbron "
        "waargenomen. Eerst bron, eigenaar en verplichte regels controleren."),
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
        activity = issue.get("activity")
        if (type(activity) is not dict
                or activity.get("signal") not in {
                    "source_clock_incoherent", "activity_timestamp_unknown",
                    "recent_issue_activity_observed",
                    "recent_linked_pr_activity_observed",
                    "quiet_over_7_days_observed_claims_unknown",
                    "quiet_over_7_days_partial_pr_coverage"}
                or type(activity.get("label_nl")) is not str
                or not 1 <= len(activity["label_nl"]) <= 180
                or type(activity.get("note_nl")) is not str
                or not 1 <= len(activity["note_nl"]) <= 500
                or activity.get("linked_pr_activity_coverage") not in {
                    "complete_for_linked_prs_in_bounded_snapshot", "partial_or_unverified"}
                or type(activity.get("as_of_utc")) is not str
                or len(activity["as_of_utc"]) > 48
                or activity.get("window_hours") != 168
                or activity.get("active_claims_verified") is not False
                or activity.get("prless_branch_owners_verified") is not False
                or activity.get("stalled_project_proven") is not False
                or activity.get("actions_authorized") is not False):
            raise ValueError("invalid or authorizing issue activity evidence")
        for field in ("issue_inactivity_hours", "latest_linked_open_pr_inactivity_hours"):
            age = activity.get(field)
            if age is not None and (type(age) is not int or not 0 <= age <= 10000000):
                raise ValueError("invalid bounded issue activity age")
        if issue["project"] not in coverage:
            raise ValueError("issue has no matching project scope observation")
        scope = coverage[issue["project"]]
        refs = issue.get("existing_prs")
        if type(refs) is not list or len(refs) > 5:
            raise ValueError("unbounded linked existing PR inventory")
        selected = None
        link_count = 0
        linked_candidates = []
        seen_pr_urls = set()
        for p in refs:
            url = p.get("url") if type(p) is dict else None
            if (type(url) is not str or PR.fullmatch(url) is None
                    or url.split("/pull/", 1)[0] != issue["url"].split("/issues/", 1)[0]
                    or p.get("merge_authorized") is not False
                    or url in seen_pr_urls):
                raise ValueError("untrusted, repeated linked PR or authority")
            seen_pr_urls.add(url)
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
            # Every existing candidate remains visible, not just the most
            # informative one. Gate observations never confer merge rights.
            candidate_state = (
                "issue_source_stale" if issue.get("issue_evidence_stale") is True else
                "source_clock_incoherent" if report.get("source_clocks_within_10_minutes") is not True else
                "observed_failed" if failed else
                "observed_pending" if pending else
                "observed_partial_no_release_permission" if has_live_gate else
                "not_observed"
            )
            # A source HEAD is a bounded navigation receipt, NOT proof that
            # the PR still has that HEAD or may be merged. Stale/incoherent
            # issue clocks cannot endorse a linked PR's commit identity.
            observed_head = None
            if has_live_gate and (
                    issue.get("issue_evidence_stale") is not True and
                    report.get("source_clocks_within_10_minutes") is True and
                    live.get("exact_head_stable_on_second_read") is True):
                value = live.get("head_sha_observed")
                if type(value) is not str or re.fullmatch(r"[0-9a-f]{40}", value) is None:
                    raise ValueError("invalid source-bound PR HEAD")
                observed_head = value
            linked_candidates.append({
                "url": url, "gate_state": candidate_state,
                "source_head_observed": observed_head,
                "file_overlap_observed": overlap,
                "source_movement_observed": moved,
                "merge_authorized": False,
                "owner_verified": False,
            })
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
            elif not live:
                action = "existing_pr_gate_unobserved"
            elif overlap:
                action = "existing_pr_scope_overlap"
            elif moved:
                action = "existing_pr_changed"
            else:
                action = "existing_pr_owner_review"
        else:
            action = "owner_before_new_product_work"
        chosen_url = selected[1]["url"] if selected else None
        # Stable canonical order for review and local offline note binding.
        linked_candidates.sort(key=lambda item: item["url"])
        other_candidates = [
            candidate for candidate in linked_candidates
            if candidate["url"] != chosen_url
        ]
        # Main issue card and PR group must display the same source verdict.
        # Incoherent/stale clocks take precedence over sampled check results.
        gate_source = next((
            candidate["gate_state"] for candidate in linked_candidates
            if candidate["url"] == chosen_url
        ), "not_observed")
        # A complete-looking positive observation isn't current claim/review
        # authorisation. The human has to verify those in the source app.
        card = {
            "issue": issue["issue"], "issue_url": issue["url"],
            "title": issue["title"], "priority": issue["priority"],
            "activity_signal": activity["signal"],
            "activity_label_nl": activity["label_nl"],
            "activity_note_nl": activity["note_nl"],
            "issue_updated_at": activity.get("issue_updated_at"),
            "issue_inactivity_hours": activity.get("issue_inactivity_hours"),
            "latest_linked_open_pr_updated_at": activity.get("latest_linked_open_pr_updated_at"),
            "latest_linked_open_pr_inactivity_hours": activity.get("latest_linked_open_pr_inactivity_hours"),
            "linked_pr_activity_coverage": activity["linked_pr_activity_coverage"],
            "activity_window_hours": activity["window_hours"],
            "activity_as_of_utc": activity.get("as_of_utc"),
            "stalled_project_proven": False,
            "project": issue["project"], "category": action,
            "category_nl": LABELS[action], "human_next_step_nl": GUIDANCE[action],
            "existing_pr_count_in_sample": len(refs),
            "candidate_existing_pr_url": chosen_url,
            "all_existing_pr_candidates_in_sample": linked_candidates,
            "other_existing_pr_candidates_in_sample": other_candidates,
            "other_existing_prs_with_observed_failed_gates": sum(
                item["gate_state"] == "observed_failed"
                for item in other_candidates
            ),
            "other_existing_prs_with_observed_pending_gates": sum(
                item["gate_state"] == "observed_pending"
                for item in other_candidates
            ),
            "candidate_pr_observation_only": True if selected else None,
            "candidate_exact_head_gate_state": gate_source,
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
    # Preserve P0/P1 and existing-PR-first semantics. Within the same
    # priority/gate tier, quiet issues surface before recent activity so the
    # operator sees stagnation candidates without treating them as owner-free.
    cards.sort(key=lambda row: (
        ORDER[row["priority"]],
        0 if row["candidate_existing_pr_url"] else 1,
        0 if row["category"] == "existing_pr_failed_gates" else 1,
        ACTIVITY_ORDER[row["activity_signal"]],
        row["project"], row["issue_url"]))
    # One existing PR can be referenced by several different product issues.
    # Surface that single review opportunity once, while keeping the original
    # issue cards and their independent ownership and source gaps intact.
    by_pr = {}
    for card in cards:
        # Every referenced existing PR is a potential integration point.
        # Restricting the grouped view to the selected primary PR can hide
        # a second PR owned by someone else and invite duplicate work.
        for linked in card["all_existing_pr_candidates_in_sample"]:
            pr_url = linked["url"]
            group = by_pr.setdefault(pr_url, {
                "pr_url": pr_url,
                "highest_priority": card["priority"],
                "linked_issue_urls_in_source": [],
                "primary_issue_urls_in_source": [],
                "issue_categories_observed": [],
                "gate_states_observed_in_source": [],
                "source_head_observed": linked["source_head_observed"],
                "source_head_receipts_consistent": True,
                "any_file_overlap_observed": False,
                "any_historical_movement_observed": False,
                "fixes_all_linked_issues_verified": False,
                "current_owner_verified": False,
                "merge_authorized": False,
            })
            if group["source_head_observed"] != linked["source_head_observed"]:
                # A stale issue may legitimately lack a usable HEAD while a
                # sibling issue has one. Preserve both issue cards, but revoke
                # the grouped HEAD rather than making the whole P0/P1 workbench
                # fail or selecting one receipt as the winner.
                group["source_head_observed"] = None
                group["source_head_receipts_consistent"] = False
            if ORDER[card["priority"]] < ORDER[group["highest_priority"]]:
                group["highest_priority"] = card["priority"]
            group["linked_issue_urls_in_source"].append(card["issue_url"])
            if linked["gate_state"] not in group["gate_states_observed_in_source"]:
                group["gate_states_observed_in_source"].append(linked["gate_state"])
            group["any_file_overlap_observed"] |= linked["file_overlap_observed"]
            group["any_historical_movement_observed"] |= linked["source_movement_observed"]
            if pr_url == card["candidate_existing_pr_url"]:
                group["primary_issue_urls_in_source"].append(card["issue_url"])
            if card["category"] not in group["issue_categories_observed"]:
                group["issue_categories_observed"].append(card["category"])
    pr_groups = sorted(by_pr.values(), key=lambda g: (
        ORDER[g["highest_priority"]], -len(g["linked_issue_urls_in_source"]),
        -len(g["primary_issue_urls_in_source"]), g["pr_url"]))
    for group in pr_groups:
        group["linked_issue_count_observed"] = len(group["linked_issue_urls_in_source"])
        group["primary_issue_count_observed"] = len(group["primary_issue_urls_in_source"])
        observations = sorted(group["gate_states_observed_in_source"])
        group["gate_states_observed_in_source"] = observations
        group["group_gate_source_state"] = (
            "inconsistent_existing_pr_head_sources"
            if not group["source_head_receipts_consistent"] else
            observations[0] if len(observations) == 1
            else "inconsistent_existing_pr_gate_sources"
        )
        group["human_next_gate_step"] = {
            "observed_failed": "Eigenaar: herstel falende exacte-HEAD checks of reviewwijzigingen.",
            "observed_pending": "Wacht op CI; pak intussen ander onafhankelijk productwerk.",
            "not_observed": "Haal actuele exacte-HEAD check- en reviewbron op.",
            "issue_source_stale": "Issuebron verouderd: vernieuw issue en PR voordat een herstelactie gekozen wordt.",
            "source_clock_incoherent": "Bronmomenten lopen uiteen: vernieuw issue, PR en CI samen.",
            "observed_partial_no_release_permission":
                "Controleer verplichte branchregels, onafhankelijke review en eigenaarschap.",
            "inconsistent_existing_pr_gate_sources":
                "Bronissues melden verschillende gates voor dezelfde PR: controleer actuele HEAD.",
            "inconsistent_existing_pr_head_sources":
                "Bronissues bevatten ontbrekende of afwijkende HEAD-waarnemingen voor dezelfde PR; herlees actuele issue- en PR-bron. Geen HEAD kiezen.",
        }[group["group_gate_source_state"]]
        group["additional_issues_do_not_prove_fixes"] = True
    totals = Counter(item["category"] for item in cards)
    return {
        "schema": "zsentinel-merge-first-human-inbox-v1",
        "observed_issue_source_at_utc": report.get("source_issue_observed_at_utc"),
        "observed_pr_source_at_utc": report.get("source_pr_observed_at_utc"),
        "issue_count": len(cards),
        "existing_pr_first_issues": sum(bool(c["candidate_existing_pr_url"]) for c in cards),
        "human_only_infrastructure_issues": totals["human_infrastructure"],
        "pr_groups": pr_groups,
        "distinct_existing_pr_groups_observed": len(pr_groups),
        "multi_issue_existing_pr_groups_observed": sum(
            group["linked_issue_count_observed"] > 1 for group in pr_groups),
        "pr_groups_with_inconsistent_head_receipts": sum(
            group["source_head_receipts_consistent"] is not True for group in pr_groups),
        "pr_groups_with_unknown_head": sum(
            group["source_head_observed"] is None for group in pr_groups),
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
        ("PR-groepen met onbekende HEAD: " +
         str(board["pr_groups_with_unknown_head"]) +
         "; afwijkende HEAD-bronontvangsten: " +
         str(board["pr_groups_with_inconsistent_head_receipts"]) +
         ". Bij bronconflict wordt geen commit gekozen."
         if type(board.get("pr_groups_with_unknown_head")) is int
         and type(board.get("pr_groups_with_inconsistent_head_receipts")) is int
         else "PR-HEAD-groepsdekking: ONBEKEND (geen tellerbron)."),
        "GEEN huidige claims, verplichte branchregels, mergebevoegdheid of "
        "live herstel geverifieerd.",
        "",
    ]
    activity_counts = Counter(item["activity_signal"] for item in board["cards"])
    lines.append("Activiteitssignalen: stil >7d / dekking compleet=" +
                 str(activity_counts.get("quiet_over_7_days_observed_claims_unknown", 0)) +
                 "; stil >7d / dekking onvolledig=" +
                 str(activity_counts.get("quiet_over_7_days_partial_pr_coverage", 0)) +
                 "; recente issue-update=" +
                 str(activity_counts.get("recent_issue_activity_observed", 0)) +
                 "; recente gekoppelde PR-update=" +
                 str(activity_counts.get("recent_linked_pr_activity_observed", 0)) +
                 "; onbekend/incoherent=" +
                 str(activity_counts.get("activity_timestamp_unknown", 0) +
                     activity_counts.get("source_clock_incoherent", 0)) +
                 ". Stilte is geen bewijs van vrije claim of stilstaand project.")
    visible_urls = {item["issue_url"] for item in board["cards"][:max_rows]}
    if board.get("pr_groups"):
        lines.extend([
            "## Bestaande PR-bundels (observatie, geen fixbewijs)" if style == "markdown"
            else "Bestaande PR-bundels (observatie, geen fixbewijs)", "",
        ])
        for group in board["pr_groups"]:
            linked_visible = [
                url for url in group["linked_issue_urls_in_source"]
                if url in visible_urls]
            if not linked_visible:
                continue
            pr_url = group["pr_url"]
            link = ("[Open bestaande PR](" + pr_url + ")"
                    if style == "markdown" else pr_url)
            primary_visible = sum(
                url in visible_urls for url in
                group.get("primary_issue_urls_in_source", [])
            )
            lines.append("- " + group["highest_priority"] + " · " + link +
                         " · " + str(len(linked_visible)) +
                         " productissue(s) in deze bron noemen de PR; " +
                         str(primary_visible) +
                         " daarvan hebben deze als primaire reviewkandidaat (geen fixbewijs).")
            lines.append("  Waargenomen gatebron: " +
                         group["group_gate_source_state"] + " · " +
                         group["human_next_gate_step"])
            if (group.get("source_head_receipts_consistent") is not True
                    or group.get("source_head_observed") is None):
                lines.append("  PR-HEAD: ONBEKEND/INCONSISTENT; kies geen commit op basis van deze groepsbron.")
            else:
                lines.append("  PR-HEAD-waarneming: " + chr(96) +
                             group["source_head_observed"] + chr(96) +
                             " (bronmoment; geen actuele HEAD- of mergevrijgave).")
            if group["any_file_overlap_observed"]:
                lines.append("  Bestandscope-overlap in bron gezien: stem af met eigenaar.")
            if group["any_historical_movement_observed"]:
                lines.append("  Historische PR-bronbeweging gezien: exacte HEAD opnieuw beoordelen.")
            for issue_url in linked_visible:
                lines.append("  " + ("[Issue](" + issue_url + ")"
                                    if style == "markdown" else issue_url))
        lines.append("")
    for index, item in enumerate(board["cards"][:max_rows], 1):
        issue = (f"[{item['issue']}]({item['issue_url']})"
                 if style == "markdown" else item["issue_url"])
        title = inert(item["title"]) if style == "markdown" else re.sub(
            r"[\r\n\t]+", " ", item["title"])
        lines.extend([
            str(index) + ". " + item["priority"] + " · " +
            item["project"] + " · " + issue + " · " + title,
            "   " + item["category_nl"],
            "   Exacte-HEAD gatebron: " + item["candidate_exact_head_gate_state"] +
            " (nooit zelfstandige vrijgave).",
            "   " + item["human_next_step_nl"],
            "   Brondekking: PR-bestanden " +
            ("compleet in gelezen steekproef" if
             item["pr_file_scope_sample_complete"] else "ONVOLLEDIG") +
            "; PR-issueverwijzingen " +
            ("compleet in gelezen steekproef" if
             item["issue_reference_sample_complete"] else "ONVOLLEDIG") +
            "; actuele eigenaar/merge UNKNOWN.",
        ])
        lines.append("   Activiteitssignaal: " + item["activity_label_nl"] +
                     " · issue-updated_at=" + str(item["issue_updated_at"] or "ONBEKEND") +
                     " (" + str(item["issue_inactivity_hours"]) + " uur geleden)" +
                     " · laatste gekoppelde open PR=" +
                     str(item["latest_linked_open_pr_updated_at"] or "ONBEKEND") +
                     " (" + str(item["latest_linked_open_pr_inactivity_hours"]) + " uur geleden)" +
                     " · PR-dekking=" + item["linked_pr_activity_coverage"] +
                     " (geen bewijs van stilstand of vrije claim).")
        lines.append("   " + item["activity_note_nl"])
        if item["candidate_existing_pr_url"]:
            url = item["candidate_existing_pr_url"]
            lines.append("   PR eerst: " + (
                "[Open bestaande PR](" + url + ")" if style == "markdown"
                else url))
        others = item.get("other_existing_pr_candidates_in_sample", [])
        if others:
            lines.append("   Andere bestaande PRs in bron: " + str(len(others)) +
                         " (elk afzonderlijk met huidige eigenaar afstemmen).")
            for other in others[:4]:
                ref = other["url"]
                lines.append("   Alternatief: " + (
                    "[Open PR](" + ref + ")" if style == "markdown"
                    else ref) + " · exacte-HEAD bron: " + other["gate_state"] +
                    (" · bestandsoverlap" if other["file_overlap_observed"] else "") +
                    (" · historische beweging" if other["source_movement_observed"] else ""))
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
