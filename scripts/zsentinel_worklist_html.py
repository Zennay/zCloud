#!/usr/bin/env python3
"""Private offline, interactive human handoff for zSentinel's merge-first inbox.

Render only prevalidated source-bound cards; no API calls, persistence, claims,
work scheduling, automatic approval or release permissions.
"""
from __future__ import annotations

from html import escape
import hashlib
import json
import re

from zsentinel_issue_action_board import MAX_CARDS, ORDER, PR, ISSUE

# These are historical source observations, never the current GitHub decision.
GATE_LABELS = {
    "not_observed": "Geen waarneming in deze bron",
    "observed_failed": "Check mislukt in opgeslagen bron",
    "observed_pending": "Check wacht in opgeslagen bron",
    "observed_partial_no_release_permission": "Gedeeltelijke bron; geen mergevrijgave",
    "issue_source_stale": "Issuebron verouderd; opnieuw raadplegen",
    "source_clock_incoherent": "Bronklokken inconsistent; opnieuw raadplegen",
    "inconsistent_existing_pr_gate_sources": "PR-gatebronnen tegenstrijdig",
}


def source_head_link(pr_url, head):
    """Render only an admitted historical exact commit, never merge approval."""
    if head is None:
        return "<strong>Bron HEAD ONBEKEND</strong>"
    if (type(head) is not str or re.fullmatch(r"[0-9a-f]{40}", head) is None
            or type(pr_url) is not str or PR.fullmatch(pr_url) is None):
        raise ValueError("untrusted saved PR commit identity")
    base = pr_url.split("/pull/", 1)[0]
    return ("<a target='_blank' rel='noopener noreferrer' href='" +
            escape(base + "/commit/" + head, quote=True) +
            "'>Waargenomen bron HEAD <code>" + head[:12] +
            "</code> ↗</a> · geen actuele mergevrijgave")


def render_html(board, max_rows=20):
    if (type(board) is not dict
            or board.get("schema") != "zsentinel-merge-first-human-inbox-v1"
            or any(board.get(key) is not False for key in (
                "current_claims_verified", "merge_authorized", "claim_authorized",
                "automatic_dispatch", "release_authorized", "writes_performed"))
            or type(board.get("cards")) is not list
            or type(board.get("issue_count")) is not int
            or not 0 <= board["issue_count"] <= MAX_CARDS
            or len(board["cards"]) != board["issue_count"]
            or type(max_rows) is not int or not 1 <= max_rows <= MAX_CARDS):
        raise ValueError("untrusted or unbounded private operator inbox")
    cards = board["cards"]
    signals = (
        "quiet_over_7_days_observed_claims_unknown",
        "quiet_over_7_days_partial_pr_coverage",
        "recent_issue_activity_observed",
        "recent_linked_pr_activity_observed",
        "activity_timestamp_unknown",
        "source_clock_incoherent",
    )
    activity_counts = {signal: sum(row.get("activity_signal") == signal for row in cards)
                       for signal in signals}
    activity_summary = (
        "Activiteitssignalen — stil >7d, PR-dekking compleet: " +
        str(activity_counts[signals[0]]) + " · stil >7d, dekking onvolledig: " +
        str(activity_counts[signals[1]]) + " · recente issue-update: " +
        str(activity_counts[signals[2]]) + " · recente gekoppelde PR-update: " +
        str(activity_counts[signals[3]]) + " · onbekend/incoherent: " +
        str(activity_counts[signals[4]] + activity_counts[signals[5]]) +
        ". Dit bewijst geen stilstand of vrije claim."
    )
    output = []
    for pos, row in enumerate(cards[:max_rows], 1):
        if (type(row) is not dict or row.get("priority") not in ORDER
                or type(row.get("issue_url")) is not str
                or ISSUE.fullmatch(row["issue_url"]) is None
                or type(row.get("issue")) is not str
                or type(row.get("project")) is not str
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", row["project"]) is None
                or type(row.get("title")) is not str
                or not 1 <= len(row["title"]) <= 180
                or type(row.get("category_nl")) is not str
                or not 1 <= len(row["category_nl"]) <= 180
                or type(row.get("human_next_step_nl")) is not str
                or not 1 <= len(row["human_next_step_nl"]) <= 650
                or row.get("activity_signal") not in {
                    "source_clock_incoherent", "activity_timestamp_unknown",
                    "recent_issue_activity_observed",
                    "recent_linked_pr_activity_observed",
                    "quiet_over_7_days_observed_claims_unknown",
                    "quiet_over_7_days_partial_pr_coverage"}
                or type(row.get("activity_label_nl")) is not str
                or not 1 <= len(row["activity_label_nl"]) <= 180
                or type(row.get("activity_note_nl")) is not str
                or not 1 <= len(row["activity_note_nl"]) <= 500
                or row.get("stalled_project_proven") is not False
                or row.get("activity_window_hours") != 168
                or type(row.get("pr_file_scope_sample_complete")) is not bool
                or type(row.get("issue_reference_sample_complete")) is not bool
                or row.get("claim_authorized") is not False
                or row.get("release_authorized") is not False):
            raise ValueError("invalid issue row in operator inbox")
        pr = row.get("candidate_existing_pr_url")
        if pr is not None and (
                type(pr) is not str or PR.fullmatch(pr) is None
                or pr.split("/pull/", 1)[0] != row["issue_url"].split("/issues/", 1)[0]):
            raise ValueError("cross-repository candidate PR in operator inbox")
        other_prs = row.get("other_existing_pr_candidates_in_sample", [])
        if type(other_prs) is not list or len(other_prs) > 4:
            raise ValueError("unbounded alternative PR candidates")
        other_seen = set()
        alt_items = []
        for other in other_prs:
            if type(other) is not dict:
                raise ValueError("invalid alternative PR source")
            alt_url = other.get("url")
            gate_state = other.get("gate_state")
            if (type(alt_url) is not str or PR.fullmatch(alt_url) is None
                    or alt_url.split("/pull/", 1)[0] != row["issue_url"].split("/issues/", 1)[0]
                    or alt_url == pr or alt_url in other_seen
                    or gate_state not in (
                        "not_observed", "observed_failed", "observed_pending",
                        "observed_partial_no_release_permission",
                        "issue_source_stale", "source_clock_incoherent")
                    or type(other.get("file_overlap_observed")) is not bool
                    or type(other.get("source_movement_observed")) is not bool
                    or other.get("merge_authorized") is not False
                    or other.get("owner_verified") is not False
                    or (other.get("source_head_observed") is not None
                        and (type(other["source_head_observed"]) is not str
                             or re.fullmatch(r"[0-9a-f]{40}",
                                             other["source_head_observed"]) is None))):
                raise ValueError("untrusted alternative PR gate or authority")
            other_seen.add(alt_url)
            alt_items.append((alt_url, gate_state, other))
        full = row.get("all_existing_pr_candidates_in_sample")
        observed_count = row.get("existing_pr_count_in_sample")
        if (type(full) is not list or len(full) > 5
                or type(observed_count) is not int
                or observed_count != len(full)
                or observed_count != len(alt_items) + int(pr is not None)
                or any(type(item) is not dict or type(item.get("url")) is not str
                       for item in full)
                or {item["url"] for item in full}
                != (other_seen | ({pr} if pr else set()))):
            raise ValueError("issue PR candidates not consistent with saved source")
        if (row.get("other_existing_prs_with_observed_failed_gates")
                != sum(state == "observed_failed" for _, state, _ in alt_items)
                or row.get("other_existing_prs_with_observed_pending_gates")
                != sum(state == "observed_pending" for _, state, _ in alt_items)):
            raise ValueError("issue PR gate totals contradict saved source")
        def h(value):
            return escape(value, quote=True)
        alt_html = ""
        if alt_items:
            extra = "".join(
                "<li><a target='_blank' rel='noopener noreferrer' href='" +
                h(url) + "'>PR " + h(url.rsplit("/", 1)[-1]) + " ↗</a> · " +
                h(GATE_LABELS[state]) + ' (' + h(state) + ')' +
                (" · bestandsoverlap" if data["file_overlap_observed"] else "") +
                (" · bronbeweging" if data["source_movement_observed"] else "") +
                "<br>" + source_head_link(url, data.get("source_head_observed")) +
                " <button class='focus-alternative' type='button' data-focus-pr='" +
                h(url) + "'>Bekijk alle issues bij deze PR</button></li>"
                for url, state, data in alt_items
            )
            alt_html = ("<details class='other-prs'><summary>Andere bestaande PRs (" +
                        str(len(alt_items)) +
                        ") · afzonderlijke eigenaar- en CI-beoordeling</summary><ul>" +
                        extra + "</ul></details>")
        primary = next((item for item in full if item["url"] == pr), None)
        primary_head = primary.get("source_head_observed") if primary else None
        if primary_head is not None and (
                type(primary_head) is not str or
                re.fullmatch(r"[0-9a-f]{40}", primary_head) is None):
            raise ValueError("untrusted primary PR source HEAD")
        # Preserve per-issue saved PR receipts even when the grouped PR
        # panel is bounded out of this particular offline HTML window.
        candidate_receipts = []
        for candidate in full:
            source_head = candidate.get("source_head_observed")
            source_gate = candidate.get("gate_state")
            if (source_gate not in GATE_LABELS or
                    (source_head is not None and
                     (type(source_head) is not str or
                      re.fullmatch(r"[0-9a-f]{40}", source_head) is None))):
                raise ValueError("invalid source-bound issue PR receipt")
            candidate_receipts.append({
                "url": candidate["url"], "head": source_head,
                "gate": source_gate,
            })
        pr_link = (
            "<a rel='noopener noreferrer' target='_blank' href='" + h(pr) +
            "'>Bestaande PR ↗</a><br>" + source_head_link(pr, primary_head)
            if pr else
            "<span>Geen bestaande PR bevestigd in bron</span>"
        )
        alternatives_failed = sum(state == "observed_failed"
                                  for _, state, _ in alt_items)
        alternatives_pending = sum(state == "observed_pending"
                                   for _, state, _ in alt_items)
        note = ("Bestaande PRs in bron: " + str(len(alt_items) + int(bool(pr))) +
                (" · andere met mislukte checks: " + str(alternatives_failed)
                 if alternatives_failed else "") +
                (" · andere met wachtende checks: " + str(alternatives_pending)
                 if alternatives_pending else "") +
                " · PR-bestanden: " +
                ("volledig binnen steekproef" if row["pr_file_scope_sample_complete"]
                 else "ONVOLLEDIG") +
                " · Issueverwijzingen: " +
                ("volledig binnen steekproef" if row["issue_reference_sample_complete"]
                 else "ONVOLLEDIG"))
        output.append(
            "<article class='ticket' tabindex='-1' data-project='" + h(row["project"]) +
            "' data-priority='" + h(row["priority"]) +
            "' data-issue='" + h(row["issue_url"]) +
            "' data-collision='" + ("yes" if len(full) > 1 else "no") +
            "' data-pr='" + h(pr or "") +
            "' data-prs='" + h(" ".join(item[0] for item in alt_items)) +
            "' data-candidate-evidence='" + h(json.dumps(
                candidate_receipts, ensure_ascii=True, separators=(",", ":"))) +
            "' data-title='" + h(row["title"]) +
            "' data-category='" + h(row["category_nl"]) +
            "' data-activity='" + h(row["activity_signal"]) +
            "' data-next='" + h(row["human_next_step_nl"]) +
            "' data-issue-updated='" + h(str(row["issue_updated_at"] or "ONBEKEND")) +
            "' data-pr-updated='" + h(str(row["latest_linked_open_pr_updated_at"] or "ONBEKEND")) +
            "' data-scope-complete='" + ("yes" if row["pr_file_scope_sample_complete"] else "no") +
            "' data-references-complete='" + ("yes" if row["issue_reference_sample_complete"] else "no") + "'>"
            "<header><span class='priority'>" + h(row["priority"]) + "</span> "
            "<span>" + h(row["project"]) + "</span> <small>#" +
            str(pos) + "</small></header><h2><a rel='noopener noreferrer' "
            "target='_blank' href='" + h(row["issue_url"]) + "'>" +
            h(row["title"]) + "</a></h2>"
            "<p><strong>" + h(row["category_nl"]) + "</strong></p>"
            "<p>" + h(row["human_next_step_nl"]) + "</p>"
            "<p class='activity'><strong>Activiteitssignaal:</strong> " +
            h(row["activity_label_nl"]) +
            " · issue update " + h(str(row["issue_updated_at"] or "ONBEKEND")) +
            " (" + h(str(row["issue_inactivity_hours"])) + " uur geleden)" +
            " · laatste gekoppelde open PR " +
            h(str(row["latest_linked_open_pr_updated_at"] or "ONBEKEND")) +
            " (" + h(str(row["latest_linked_open_pr_inactivity_hours"])) + " uur geleden)" +
            " · dekking " + h(row["linked_pr_activity_coverage"]) +
            "<br><small>" + h(row["activity_note_nl"]) + "</small></p>"
            "<p class='links'>" + pr_link +
            (" <button class='focus-from-issue' type='button'>"
             "Bekijk alle bronissues van deze PR</button>" if pr else "") +
            "</p>" + alt_html +
            "<p class='source'>" + h(note) +
            " · opgeslagen CI/review (niet actueel): " +
                h(GATE_LABELS.get(row["candidate_exact_head_gate_state"],
                                  row["candidate_exact_head_gate_state"])) +
                " · actuele owner/merge: ONBEKEND</p>"
            "<div class='controls'><label>Lokale vervolgstatus"
            "<select class='local-state'>"
            "<option value='open'>Nog te bekijken</option>"
            "<option value='owner'>Wacht op bevoegde eigenaar</option>"
            "<option value='coordination'>Afstemming nodig</option>"
            "<option value='followup'>Opvolging genoteerd</option>"
            "</select></label><label>Privé werknotitie (alleen dit browservenster)"
            "<textarea maxlength='700' rows='2' placeholder='Handmatige afspraak, geen automatische claim'></textarea>"
            "</label></div><button type='button' class='issue-export-one'>"
            "Download privé eigenaarsdossier voor dit issue</button></article>"
        )
    # Multiple saved PR mentions for one issue are actionable OWNER TRIAGE,
    # not evidence of file collision, a shared owner, or two complete fixes.
    collisions = []
    collision_issues = set()
    for card in cards[:max_rows]:
        candidates = card["all_existing_pr_candidates_in_sample"]
        if len(candidates) < 2:
            continue
        collision_issues.add(card["issue_url"])
        peer_links = "".join(
            "<li><a target='_blank' rel='noopener noreferrer' href='" +
            escape(candidate["url"], quote=True) + "'>PR #" +
            escape(candidate["url"].rsplit("/", 1)[-1]) + "</a> · " +
            source_head_link(candidate["url"], candidate.get("source_head_observed")) +
            " · gate " + escape(GATE_LABELS[candidate["gate_state"]]) +
            (" · bestandsoverlap gezien" if candidate["file_overlap_observed"] else "") +
            (" · historische beweging gezien" if candidate["source_movement_observed"] else "") +
            " <button type='button' class='focus-alternative' data-focus-pr='" +
            escape(candidate["url"], quote=True) +
            "'>Open PR-werkbak</button></li>"
            for candidate in candidates
        )
        collisions.append(
            "<li class='collision' tabindex='-1' data-project='" + escape(card["project"], quote=True) +
            "' data-issue='" + escape(card["issue_url"], quote=True) +
            "' data-title='" + escape(card["title"], quote=True) +
            "' data-priority='" + escape(card["priority"], quote=True) +
            "' data-activity='" + escape(card["activity_signal"], quote=True) +
            "' data-prs='" + escape(" ".join(c["url"] for c in candidates), quote=True) +
            "'><strong>" + escape(card["priority"]) + "</strong> · " +
            "<a target='_blank' rel='noopener noreferrer' href='" +
            escape(card["issue_url"], quote=True) + "'>" +
            escape(card["title"]) + "</a><p>" +
            str(len(candidates)) + " PR-kandidaten in opgeslagen bron. " +
            "Controleer eigenaren en exacte HEADs los van elkaar.</p><ul>" +
            peer_links + "</ul>"
            "<div class='collision-controls'><label>Privé afstemming voor dit issue"
            "<select class='collision-state'>"
            "<option value='unreviewed'>Nog niet beoordeeld</option>"
            "<option value='owner'>Eigenaar bepalen</option>"
            "<option value='coordinate'>Afstemmen met PR-eigenaren</option>"
            "<option value='followup'>Opvolging vastgelegd (niet opgelost)</option>"
            "</select></label><label>Privé botsingsnotitie (geen GitHub-status)"
            "<textarea class='collision-note' maxlength='500' rows='2' "
            "placeholder='Eigenaar/contact of apart te controleren PR'></textarea>"
            "</label></div><button type='button' class='collision-export-one'>Download privéoverdracht voor dit issue</button></li>"
        )
    visible_issues = {card["issue_url"] for card in cards[:max_rows]}
    visible_project = {card["issue_url"]: card["project"]
                       for card in cards[:max_rows]}
    groups = board.get("pr_groups", [])
    if (type(groups) is not list or len(groups) > MAX_CARDS * 5):
        raise ValueError("unbounded existing PR work groups")
    grouped = []
    group_source = []
    seen_group_prs = set()
    for group in groups:
        pr = group.get("pr_url") if type(group) is dict else None
        linked = group.get("linked_issue_urls_in_source") if type(group) is dict else None
        if (type(pr) is not str or PR.fullmatch(pr) is None
                or group.get("highest_priority") not in ORDER
                or group.get("fixes_all_linked_issues_verified") is not False
                or group.get("merge_authorized") is not False
                or type(linked) is not list or not 1 <= len(linked) <= MAX_CARDS
                or any(type(url) is not str or ISSUE.fullmatch(url) is None
                       or url.split("/issues/", 1)[0] != pr.split("/pull/", 1)[0]
                       for url in linked)):
            raise ValueError("untrusted existing PR issue-group evidence")
        if pr in seen_group_prs:
            raise ValueError("duplicated PR group")
        seen_group_prs.add(pr)
        primary_urls = group.get("primary_issue_urls_in_source", [])
        if (type(primary_urls) is not list
                or len(primary_urls) > len(linked)
                or len(set(primary_urls)) != len(primary_urls)
                or any(url not in linked for url in primary_urls)
                or type(group.get("primary_issue_count_observed")) is not int
                or group["primary_issue_count_observed"] != len(primary_urls)):
            raise ValueError("invalid primary vs secondary PR issue mapping")
        observed_gate = group.get("group_gate_source_state")
        group_head = group.get("source_head_observed")
        if group_head is not None and (
                type(group_head) is not str or
                re.fullmatch(r"[0-9a-f]{40}", group_head) is None):
            raise ValueError("invalid PR-group source HEAD")
        permitted_gate = {
            "observed_failed", "observed_pending", "not_observed",
            "observed_partial_no_release_permission",
            "issue_source_stale", "source_clock_incoherent",
            "inconsistent_existing_pr_gate_sources",
        }
        human_gate = group.get("human_next_gate_step")
        if (observed_gate not in permitted_gate
                or type(human_gate) is not str or not 1 <= len(human_gate) <= 220
                or type(group.get("any_file_overlap_observed")) is not bool
                or type(group.get("any_historical_movement_observed")) is not bool):
            raise ValueError("untrusted existing PR group gate observation")
        shown_links = [url for url in linked if url in visible_issues]
        if not shown_links:
            continue
        primary_visible = sum(url in visible_issues for url in primary_urls)
        source_projects = {visible_project[url] for url in shown_links}
        if len(source_projects) != 1:
            raise ValueError("PR issue group spans inconsistent local projects")
        group_project = next(iter(source_projects))
        # Each grouped PR must reference the same saved exact-head identity as
        # its visible source-linked issues. A forged or stale group receipt
        # cannot turn an unrelated commit into a credible operator shortcut.
        for issue_row in cards[:max_rows]:
            if issue_row["issue_url"] not in shown_links:
                continue
            matches = [candidate for candidate in
                       issue_row["all_existing_pr_candidates_in_sample"]
                       if candidate["url"] == pr]
            if len(matches) != 1 or matches[0].get("source_head_observed") != group_head:
                raise ValueError("grouped PR source HEAD disagrees with issue evidence")
        group_source.append((
            pr, group["highest_priority"], tuple(linked), tuple(primary_urls),
            observed_gate, human_gate, group_head,
            group["any_file_overlap_observed"],
            group["any_historical_movement_observed"]))
        refs = "".join("<li><a rel='noopener noreferrer' target='_blank' href='" +
                       escape(url, quote=True) + "'>Issue " +
                       escape(url.rsplit("/", 1)[-1]) + "</a></li>"
                       for url in shown_links)
        grouped.append(
            "<li class='pr-work' tabindex='-1' data-pr='" + escape(pr, quote=True) +
            "' data-project='" + escape(group_project, quote=True) +
            "' data-collision='" + ("yes" if any(url in collision_issues for url in shown_links) else "no") +
            "' data-priority='" + escape(group["highest_priority"], quote=True) +
            "' data-issues='" + escape(" ".join(shown_links), quote=True) +
            "' data-source-issues='" + escape(" ".join(linked), quote=True) +
            "' data-head='" + escape(group_head or "", quote=True) +
            "' data-gate='" + escape(observed_gate, quote=True) +
            "' data-next='" + escape(human_gate, quote=True) +
            "' data-overlap='" + ("yes" if group["any_file_overlap_observed"] else "no") +
            "' data-movement='" + ("yes" if group["any_historical_movement_observed"] else "no") +
            "'><strong>" + escape(group["highest_priority"]) + "</strong> · "
            "<a rel='noopener noreferrer' target='_blank' href='" +
            escape(pr, quote=True) + "'>PR " +
            escape(pr.rsplit("/", 1)[-1]) + "</a> · " +
            str(len(shown_links)) +
            " gekoppelde issuevermelding(en) in deze beperkte overdracht; " +
            str(primary_visible) + " als primaire reviewkandidaat"
            "<p class='source'>" + source_head_link(pr, group_head) +
            "<br>Opgeslagen exact-HEAD-gate: " + escape(GATE_LABELS[observed_gate]) +
            " · " + escape(human_gate) +
            (" · Bestandsoverlap waargenomen" if
             group["any_file_overlap_observed"] else "") +
            (" · Historische bronbeweging" if
             group["any_historical_movement_observed"] else "") +
            " · actuele owner/merge: ONBEKEND</p>"
            "<details><summary>Bekijk bronissues (geen fixbewijs)</summary><ul>" +
            refs + "</ul></details>"
            "<button class='focus-pr' type='button'>Bekijk gekoppelde issues in werkbak</button>"
            "<div class='pr-controls'><label>Privé PR-opvolging"
            "<select class='pr-local-state'>"
            "<option value='open'>Nog te beoordelen</option>"
            "<option value='review'>Wacht op review/CI</option>"
            "<option value='owner'>Wacht op huidige eigenaar</option>"
            "<option value='coordinate'>Afstemming tussen PRs nodig</option>"
            "<option value='followup'>Opvolging vastgelegd</option>"
            "</select></label><label>Privé PR-notitie, geen GitHub-commentaar"
            "<textarea class='pr-note' maxlength='500' rows='2'"
            " placeholder='Menselijke opvolging; geen merge- of eigenaarvrijgave'></textarea>"
            "</label></div></li>"
        )
    collisions_panel = (
        "<section class='pr-panel' id='collisions'><h2>Issues met meerdere bestaande PRs</h2>"
        "<p>Deze bronissues hebben twee of meer opgeslagen PR-kandidaten. "
        "Dit bewijst geen gedeelde bestandswijziging, eigenaarschap, fix of actuele status. "
        "Controleer elke PR apart bij de bevoegde eigenaar.</p>"
        "<ol>" + "".join(collisions) + "</ol></section>"
        if collisions else
        "<section class='pr-panel' id='collisions'><h2>Issues met meerdere bestaande PRs</h2>"
        "<p>Geen meervoudige PR-vermelding binnen deze beperkte zichtbare bronsteekproef. "
        "Daarbuiten blijft overlap ONBEKEND.</p></section>"
    )
    groups_panel = (
        "<section class='pr-panel'><h2>Bestaande PR's als één werkgroep</h2>"
        "<p>Iedere bestaande PR-kandidaat staat hier, ook als een andere PR "
        "primair bij het issue werd gekozen. Gedeelde issuevermeldingen zijn "
        "géén fixbewijs, eigenaarschap of mergevrijgave.</p>"
        "<ol>" + "".join(grouped) + "</ol></section>" if grouped else "")
    if len(cards) > max_rows:
        hidden = "<p class='warning'>ONVOLLEDIGE overdracht: " + str(len(cards)-max_rows) + (
            " issues buiten deze begrensde pagina. Gebruik --worklist-limit "
            "voor een grotere, afzonderlijke export.</p>"
        )
    else:
        hidden = ""
    ts_issue = board.get("observed_issue_source_at_utc")
    ts_pr = board.get("observed_pr_source_at_utc")
    # Timestamps are inert text; historical snapshots never license work.
    issue_stamp = escape(str(ts_issue)[:60])
    pr_stamp = escape(str(ts_pr)[:60])
    script = r"""<script>
(function(){
'use strict';
const tickets=Array.from(document.querySelectorAll('article.ticket[data-issue]'));
const prs=Array.from(document.querySelectorAll('.pr-work[data-pr]'));
const search=document.getElementById('q'), priority=document.getElementById('priority');
const project=document.getElementById('project'), state=document.getElementById('state');
const activity=document.getElementById('activity');
const visible=document.getElementById('visible'), prState=document.getElementById('pr-state');
const nextOpenPr=document.getElementById('pr-review-next');
const nextOpenIssue=document.getElementById('issue-review-next');
const openIssueSummary=document.getElementById('issue-review-summary');
const openPrSummary=document.getElementById('pr-review-summary');
const projectExport=document.getElementById('project-export');
const projectNext=document.getElementById('project-review-next');
const projectReviewSummary=document.getElementById('project-review-summary');
const focusBar=document.getElementById('focus-bar'),focusTitle=document.getElementById('focus-title');
const focusClear=document.getElementById('focus-clear');
const focusNext=document.getElementById('focus-next');
const focusExport=document.getElementById('focus-export');
const collisionOnly=document.getElementById('collision-only');
const collisionRows=Array.from(document.querySelectorAll('li.collision[data-issue]'));
const collisionVisible=document.getElementById('collision-visible');
const collisionStateFilter=document.getElementById('collision-state-filter');
const collisionNext=document.getElementById('collision-next');
let collisionIndex=-1;
const collisionLabels={
 unreviewed:'Nog niet beoordeeld',
 owner:'Eigenaar bepalen',
 coordinate:'Afstemmen met PR-eigenaren',
 followup:'Opvolging vastgelegd (niet opgelost)'
};
const gateLabels={
 not_observed:'Geen waarneming in deze bron',
 observed_failed:'Check mislukt in opgeslagen bron',
 observed_pending:'Check wacht in opgeslagen bron',
 observed_partial_no_release_permission:'Gedeeltelijke bron; geen mergevrijgave',
 issue_source_stale:'Issuebron verouderd; opnieuw raadplegen',
 source_clock_incoherent:'Bronklokken inconsistent; opnieuw raadplegen',
 inconsistent_existing_pr_gate_sources:'PR-gatebronnen tegenstrijdig'
};
const collisionExport=document.getElementById('collision-export');
const collisionOpenExport=document.getElementById('collision-open-export');
let focusedPr=null,focusIndex=-1;
let lastPrReview=null,lastProjectReview=null,lastIssueReview=null;
// The privacy-preserving baseline describes ONLY this browser's local edits;
// never persist it automatically or mistake a completed download for a merge.
let lastSavedState=null;
function localSnapshot(){
 return JSON.stringify({
  focused_pr:focusedPr,
  issues:tickets.map(t=>[t.dataset.issue,t.querySelector('.local-state').value,
    t.querySelector('textarea').value]),
  prs:prs.map(p=>[p.dataset.pr,p.querySelector('.pr-local-state').value,
    p.querySelector('.pr-note').value]),
  collisions:collisionRows.map(c=>[c.dataset.issue,
    c.querySelector('.collision-state').value,c.querySelector('.collision-note').value])
 });
}
function sessionDirty(){return localSnapshot()!==lastSavedState;}
function updateSessionIndicator(){
 document.getElementById('unsaved').textContent=sessionDirty()?
  'Niet-opgeslagen lokale wijzigingen — bewaar opnieuw de privésessie (JSON).':
  'Geen niet-opgeslagen lokale wijzigingen sinds openen, import of laatste JSON-export.';
}
function linkedToFocus(t){
 return Boolean(focusedPr)&&
   [t.dataset.pr,...t.dataset.prs.trim().split(' ').filter(Boolean)].includes(focusedPr);
}
const prLabels={open:'Nog te beoordelen',review:'Wacht op review/CI',
 owner:'Wacht op huidige eigenaar',coordinate:'Afstemming tussen PRs nodig',
 followup:'Opvolging vastgelegd'};
const labels={open:'Nog te bekijken',owner:'Wacht op bevoegde eigenaar',
  coordination:'Afstemming nodig',followup:'Opvolging genoteerd'};
function openProjectQueue(){
 const entries=new Map();
 const rank={P0:0,P1:1,P2:2,Unspecified:3};
 function add(key,status,priority){
  if(status==='followup')return;
  if(!entries.has(key))entries.set(key,{name:key,count:0,priority:4});
  const item=entries.get(key);
  item.count++;
  item.priority=Math.min(item.priority,rank[priority]??4);
 }
 for(const p of prs)add(p.dataset.project,
  p.querySelector('.pr-local-state').value,p.dataset.priority);
 for(const t of tickets)add(t.dataset.project,
  t.querySelector('.local-state').value,t.dataset.priority);
 for(const c of collisionRows)add(c.dataset.project,
  c.querySelector('.collision-state').value,c.dataset.priority);
 return [...entries.values()].sort((a,b)=>a.priority-b.priority||
  a.name.localeCompare(b.name,'nl'));
}
function openIssueQueue(){
 const rank={P0:0,P1:1,P2:2,Unspecified:3};
 // Historical source priority, never an inferred live issue or worker claim.
 return tickets.filter(t=>t.querySelector('.local-state').value!=='followup')
  .sort((a,b)=>(rank[a.dataset.priority]??4)-(rank[b.dataset.priority]??4)||
    tickets.indexOf(a)-tickets.indexOf(b));
}
function refresh(){
 const q=search.value.trim().toLocaleLowerCase('nl');
 let count=0;
 for(const t of tickets){
  const text=[t.dataset.title,t.dataset.issue,t.dataset.pr,t.dataset.prs,
   t.dataset.project,t.dataset.category,t.dataset.next].join(' ').toLocaleLowerCase('nl');
  const show=(!focusedPr||linkedToFocus(t))&&
   (!collisionOnly.checked||t.dataset.collision==='yes')&&
   (!q||text.includes(q))&&(!priority.value||priority.value===t.dataset.priority)&&
   (!project.value||project.value===t.dataset.project)&&
   (!activity.value||(activity.value==='unknown'?['source_clock_incoherent','activity_timestamp_unknown'].includes(t.dataset.activity):activity.value===t.dataset.activity))&&
   (!state.value||state.value===t.querySelector('.local-state').value);
  t.hidden=!show;if(show)count++;
 }
 let prCount=0;
 for(const p of prs){
  const content=[p.dataset.pr,p.dataset.issues,p.dataset.project,
    p.dataset.gate].join(' ').toLocaleLowerCase('nl');
  const show=(!focusedPr||p.dataset.pr===focusedPr)&&
    (!collisionOnly.checked||p.dataset.collision==='yes')&&(!q||content.includes(q))&&
    (!priority.value||priority.value===p.dataset.priority)&&
    (!project.value||project.value===p.dataset.project)&&
    (!prState.value||prState.value===p.querySelector('.pr-local-state').value);
  p.hidden=!show;if(show)prCount++;
 }
 let collisionsShown=0;
 for(const c of collisionRows){
  const source=[c.dataset.issue,c.dataset.title,c.dataset.prs,c.dataset.project]
    .join(' ').toLocaleLowerCase('nl');
  const show=(!focusedPr||c.dataset.prs.split(' ').includes(focusedPr))&&
    (!q||source.includes(q))&&
    (!priority.value||priority.value===c.dataset.priority)&&
    (!project.value||project.value===c.dataset.project)&&
    (!collisionStateFilter.value||collisionStateFilter.value===c.querySelector('.collision-state').value)&&
    (!activity.value||(activity.value==='unknown'?['source_clock_incoherent','activity_timestamp_unknown'].includes(c.dataset.activity):activity.value===c.dataset.activity));
  c.hidden=!show;if(show)collisionsShown++;
 }
 const unattended=collisionRows.filter(c=>c.querySelector('.collision-state').value==='unreviewed').length;
 const completed=collisionRows.filter(c=>c.querySelector('.collision-state').value==='followup').length;
 const actionable=collisionRows.filter(c=>!c.hidden&&c.querySelector('.collision-state').value!=='followup').length;
 collisionVisible.textContent=collisionsShown+' van '+collisionRows.length+
  ' meervoudige PR-bronissues zichtbaar · '+unattended+' nog niet beoordeeld · '+
  completed+' met lokale opvolging (niet opgelost) · '+actionable+' zichtbare vervolgacties';
 collisionNext.disabled=actionable===0;
 visible.textContent=count+' van '+tickets.length+' issuekaarten en '+
   prCount+' van '+prs.length+' bestaande PR-groepen zichtbaar';
 const unreviewed=prs.filter(p=>p.querySelector('.pr-local-state').value==='open').length;
 const outstanding=prs.filter(p=>p.querySelector('.pr-local-state').value!=='followup').length;
 openPrSummary.textContent=outstanding+' PRs zonder afgeronde lokale opvolging uit '
   +prs.length+' bron-PRs; '+unreviewed+' nog te beoordelen. '
   +'Lokale opvolging bewijst geen review, merge of fixes.';
 nextOpenPr.disabled=outstanding===0;
 const issueQueue=openIssueQueue();
 openIssueSummary.textContent=issueQueue.length+' bronissues zonder genoteerde lokale opvolging uit '+
  tickets.length+' issuekaarten · '+issueQueue.filter(t=>t.querySelector('.local-state').value==='open').length+
  ' nog niet bekeken. P0 gaat voor P1; status is geen GitHub-fix of geldige claim.';
 nextOpenIssue.disabled=issueQueue.length===0;
 const reviewQueue=openProjectQueue();
 const projectsOutstanding=reviewQueue.length;
 const localOpenEntries=reviewQueue.reduce((sum,item)=>sum+item.count,0);
 projectReviewSummary.textContent=projectsOutstanding+
  ' projecten met '+localOpenEntries+' open lokale PR-, issue- of botsingsopvolgingen. '+
  'P0 eerst, daarna P1; dit is geen bewijs van voltooide fixes.';
 projectNext.disabled=projectsOutstanding===0;
 projectExport.disabled=!project.value;
 projectExport.textContent=project.value?
  'Download privéoverdracht voor project '+project.value:
  'Kies eerst een project voor privéoverdracht';
 focusBar.hidden=!focusedPr;
 updateSessionIndicator();
 if(focusedPr){
   const open=tickets.filter(t=>linkedToFocus(t)&&
     t.querySelector('.local-state').value==='open');
   focusTitle.textContent='PR '+focusedPr.split('/').pop()+
     ': '+count+' gekoppelde bronissues in dit dossier, '+open.length+
     ' nog te bekijken. Status is alleen lokaal; geen fix- of mergebewijs.';
   focusNext.disabled=open.length===0;
 }
}
function focusOnPr(url){
 const target=prs.find(p=>p.dataset.pr===url);
 if(!target)return;
 focusedPr=url;focusIndex=-1;collisionIndex=-1;
 collisionOnly.checked=false;collisionStateFilter.value='';
 search.value='';priority.value='';project.value='';activity.value='';state.value='';prState.value='';
 refresh();
 focusBar.scrollIntoView({block:'start'});
 focusClear.focus();
}
for(const p of prs)p.querySelector('.focus-pr').addEventListener('click',()=>focusOnPr(p.dataset.pr));
projectNext.addEventListener('click',()=>{
 const candidates=openProjectQueue().map(item=>item.name);
 if(!candidates.length)return;
 const last=candidates.indexOf(lastProjectReview);
 lastProjectReview=candidates[(last+1)%candidates.length];
 focusedPr=null;focusIndex=-1;collisionIndex=-1;
 collisionOnly.checked=false;collisionStateFilter.value='';
 search.value='';priority.value='';activity.value='';state.value='';prState.value='';
 project.value=lastProjectReview;
 refresh();
 project.scrollIntoView({block:'center'});
 project.focus();
});
nextOpenIssue.addEventListener('click',()=>{
 const queue=openIssueQueue();
 if(!queue.length)return;
 const last=queue.findIndex(t=>t.dataset.issue===lastIssueReview);
 const target=queue[(last+1)%queue.length];
 lastIssueReview=target.dataset.issue;
 focusedPr=null;focusIndex=-1;collisionIndex=-1;
 collisionOnly.checked=false;collisionStateFilter.value='';
 search.value='';priority.value='';project.value='';activity.value='';state.value='';prState.value='';
 refresh();
 target.scrollIntoView({block:'start'});
 target.focus();
});
nextOpenPr.addEventListener('click',()=>{
 const start=prs.findIndex(p=>p.dataset.pr===lastPrReview);
 const order=[...prs.slice(start+1),...prs.slice(0,start+1)];
 const target=order.find(p=>p.querySelector('.pr-local-state').value!=='followup');
 if(!target)return;
 lastPrReview=target.dataset.pr;
 focusOnPr(lastPrReview);
 target.scrollIntoView({block:'center'});
 target.focus();
});
for(const button of document.querySelectorAll('.focus-alternative[data-focus-pr]')){
 button.addEventListener('click',()=>focusOnPr(button.dataset.focusPr));
}
for(const t of tickets){
 const button=t.querySelector('.focus-from-issue');
 if(button)button.addEventListener('click',()=>focusOnPr(t.dataset.pr));
}
focusClear.addEventListener('click',()=>{
 focusedPr=null;focusIndex=-1;refresh();document.getElementById('q').focus();
});
focusNext.addEventListener('click',()=>{
 const open=tickets.filter(t=>linkedToFocus(t)&&
   t.querySelector('.local-state').value==='open'&&!t.hidden);
 if(!open.length)return;
 focusIndex=(focusIndex+1)%open.length;
 open[focusIndex].scrollIntoView({block:'start'});
 open[focusIndex].focus();
});
collisionNext.addEventListener('click',()=>{
 const active=collisionRows.filter(c=>!c.hidden&&c.querySelector('.collision-state').value!=='followup');
 if(!active.length)return;
 collisionIndex=(collisionIndex+1)%active.length;
 active[collisionIndex].scrollIntoView({block:'start'});
 active[collisionIndex].focus();
});
for(const c of [search,priority,project,activity,state,prState,collisionOnly,collisionStateFilter])c.addEventListener('input',refresh);
for(const t of tickets)t.querySelector('.local-state').addEventListener('change',refresh);
for(const p of prs)p.querySelector('.pr-local-state').addEventListener('change',refresh);
for(const c of collisionRows)c.querySelector('.collision-state').addEventListener('change',refresh);
document.getElementById('reset').addEventListener('click',function(){
 focusedPr=null;focusIndex=-1;collisionIndex=-1;collisionOnly.checked=false;collisionStateFilter.value='';search.value='';priority.value='';project.value='';activity.value='';state.value='';prState.value='';refresh();
});
function safeText(value){
 return String(value||'').replace(/[\r\n\t]+/g,' ').replace(/([\\*_{}\[\]()|<>!#\x60~])/g,'\\$1').slice(0,720);
}
function privateDownload(filename,source){
 const blob=new Blob([source],{type:'text/markdown;charset=utf-8'});
 const url=URL.createObjectURL(blob),a=document.createElement('a');
 a.href=url;a.download=filename;a.click();
 window.setTimeout(()=>URL.revokeObjectURL(url),3000);
}
function exportOpenIssueHandoff(){
 const queue=openIssueQueue().filter(t=>t.dataset.priority==='P0'||t.dataset.priority==='P1');
 const lines=[
  '# zSentinel · P0/P1 open issue-eigenaarsoverdracht','',
  '> Offline, historisch, uitsluitend handmatige opvolging. Bronmeldingen autoriseren geen actuele claim, code-overname, CI-retry, review, merge of deploy.','',
  '- Issuebron UTC: '+safeText(document.getElementById('issue-time').textContent),
  '- PR-bron UTC: '+safeText(document.getElementById('pr-time').textContent),
  '- Exact bron-token: '+safeText(document.body.dataset.sourceToken),
  '- Open P0/P1-issues IN DIT beperkte bronvenster: '+queue.length,
  '- P0: '+queue.filter(t=>t.dataset.priority==='P0').length,
  '- P1: '+queue.filter(t=>t.dataset.priority==='P1').length,
  '- Alleen lokaal op opvolging genoteerde issues en overige prioriteiten zijn uitgesloten; niet opgelost of gesloten verklaard.',
  '- Schermfilters, PR-focus en projectselectie beïnvloeden deze brongebonden lijst niet.',
  '- Claims en gates moeten opnieuw bij actuele bron worden gecontroleerd.',''
 ];
 if(!queue.length)lines.push(
  'Geen lokaal open P0/P1-issue in deze steekproef. Dit bewijst NIET dat het project geen open P0/P1-issues heeft.','');
 for(const [index,t] of queue.entries()){
  const issue=t.dataset.issue;
  const candidates=[t.dataset.pr,...t.dataset.prs.trim().split(' ').filter(Boolean)].filter(Boolean);
  lines.push(
   '## '+(index+1)+'. [Issue #'+issue.split('/').pop()+']('+issue+')','',
   '- Project: '+safeText(t.dataset.project)+' · '+safeText(t.dataset.priority),
   '- Titel: '+safeText(t.dataset.title),
   '- Historisch activiteitssignaal: '+safeText(t.dataset.activity),
   '- Bron issue-update: '+safeText(t.dataset.issueUpdated||'ONBEKEND'),
   '- Bron laatste gekoppelde PR-update: '+safeText(t.dataset.prUpdated||'ONBEKEND'),
   '- Bronroute: '+safeText(t.dataset.category),
   '- Aanbevolen eigenaarstap: '+safeText(t.dataset.next),
   '- Lokale opvolging: '+safeText(labels[t.querySelector('.local-state').value]),
   '- PR-bestandsdekking compleet binnen steekproef: '+(t.dataset.scopeComplete==='yes'?'JA':'NEE'),
   '- PR-verwijzingsdekking compleet binnen steekproef: '+(t.dataset.referencesComplete==='yes'?'JA':'NEE')
  );
  const note=t.querySelector('textarea').value.trim();
  if(note)lines.push('- Privé issuenotitie: '+safeText(note));
  if(!candidates.length)lines.push('- Bestaande PRs: ONBEKEND; geen kandidaat in beperkt venster.');
  for(const pr of candidates){
   const p=prs.find(group=>group.dataset.pr===pr);
   const receipt=issueReceipt(t,pr);
   const h=p&&p.dataset.head, r=receipt&&receipt.head;
   const evidenceHead=h&&r&&h!==r?'ONBEKEND (BRONCONFLICT)':(h||r||'ONBEKEND');
   const evidenceGate=p?(gateLabels[p.dataset.gate]||p.dataset.gate):
    (receipt?(gateLabels[receipt.gate]||receipt.gate):'ONBEKEND');
   lines.push('  - [Bestaande PR #'+pr.split('/').pop()+']('+pr+') · geobserveerde HEAD '+
    safeText(evidenceHead)+' · gate '+safeText(evidenceGate)+
    (p?' · lokale opvolging '+safeText(prLabels[p.querySelector('.pr-local-state').value]):''));
  }
  const c=collisionRows.find(row=>row.dataset.issue===issue);
  if(c){
   lines.push('- Meerdere PR-kandidaten: menselijke afstemming nodig (geen bewezen bestandsoverlap).',
    '- Privé afstemmingsstatus: '+safeText(collisionLabels[c.querySelector('.collision-state').value]));
   const cnote=c.querySelector('.collision-note').value.trim();
   if(cnote)lines.push('- Privé afstemmingsnotitie: '+safeText(cnote));
  }
  lines.push('');
 }
 lines.push('**Menselijke releasegrens:** actuele claims, HEAD, verplichte exacte-head CI, onafhankelijke review en branch protection verifiëren vóór ieder besluit. Geen externe wijzigingen door deze export.','');
 privateDownload('zsentinel-open-p0-p1-issue-eigenaaroverdracht.md',lines.join('\n')+'\n');
}
document.getElementById('issue-review-export').addEventListener('click',exportOpenIssueHandoff);

function onePrHandoff(){
 const p=prs.find(group=>group.dataset.pr===focusedPr);
 if(!p)return;
 const url=p.dataset.pr,head=p.dataset.head||'ONBEKEND';
 const linked=p.dataset.sourceIssues.trim().split(' ').filter(Boolean);
 const shown=p.dataset.issues.trim().split(' ').filter(Boolean);
 const linkedTickets=tickets.filter(t=>linked.includes(t.dataset.issue));
 const repoUrl=url.split('/pull/')[0];
 const lines=[
  '# zSentinel · privé opvolging voor bestaande PR '+url.split('/').pop(),'',
  '> Offline handmatige BRONOPNAME; geen actuele CI, eigenaar, review, merge, fix of deployautorisatie.','',
  '- Bestaande PR: [Open op GitHub]('+url+')',
  '- Waargenomen bron HEAD: '+safeText(head),
  '- Waargenomen broncommit: '+(head==='ONBEKEND'?'ONBEKEND':'[GitHub commit]('+repoUrl+'/commit/'+head+')'),
  '- Waargenomen gate: '+safeText(gateLabels[p.dataset.gate]||p.dataset.gate)+
    ' ('+safeText(p.dataset.gate)+')',
  '- Menselijke gates: '+safeText(p.dataset.next),
  '- Bestandsoverlap in bron: '+safeText(p.dataset.overlap),
  '- Historische bronbeweging: '+safeText(p.dataset.movement),
  '- Issuebron UTC: '+safeText(document.getElementById('issue-time').textContent),
  '- PR-bron UTC: '+safeText(document.getElementById('pr-time').textContent),
  '- Privé bron-token: '+safeText(document.body.dataset.sourceToken),
  '- Beperkte bronvermeldingen voor deze PR: '+linked.length,
  '- Momenteel in deze export zichtbare bronissues: '+shown.length,
  '- Geen bewering over ontbrekende bronissues of de huidige GitHub-status.','',
  '## Gekoppelde bronissues (géén bewezen fixes)',''
 ];
 for(const issue of linked){
  const t=linkedTickets.find(t=>t.dataset.issue===issue);
  lines.push('- [Issue '+issue.split('/').pop()+']('+issue+')');
  if(!t){lines.push('  - Niet in dit beperkte werkbakvenster; huidige toestand ONBEKEND.');continue;}
  lines.push('  - '+safeText(t.dataset.priority)+' · '+safeText(t.dataset.title));
  lines.push('  - Handmatige lokale opvolging: '+labels[t.querySelector('.local-state').value]);
  lines.push('  - Menselijke vervolgstap uit de bron: '+safeText(t.dataset.next));
  const alternatives=[t.dataset.pr,...t.dataset.prs.trim().split(' ').filter(Boolean)]
    .filter(other=>other&&other!==url);
  if(alternatives.length){
   lines.push('  - Andere PR-kandidaten bij ditzelfde issue (apart beoordelen, geen eigenaarschap):');
   for(const other of alternatives){
    const group=prs.find(row=>row.dataset.pr===other);
    const head=group&&group.dataset.head;
    lines.push('    - [Andere PR '+other.split('/').pop()+']('+other+
      ')' +(head?' · waargenomen HEAD '+safeText(head):' · HEAD ONBEKEND'));
   }
  }
  const note=t.querySelector('textarea').value.trim();
  if(note)lines.push('  - Privé issuenotitie: '+safeText(note));
  const conflict=collisionRows.find(c=>c.dataset.issue===issue);
  if(conflict){
   lines.push('  - Meervoudige PR-vermelding: handmatige afstemmingsstatus '+
     collisionLabels[conflict.querySelector('.collision-state').value]+
     ' (geen bewezen bestandsbotsing of oplossing)');
   const conflictNote=conflict.querySelector('.collision-note').value.trim();
   if(conflictNote)lines.push('  - Privé PR-afstemmingsnotitie: '+safeText(conflictNote));
  }
 }
 lines.push('','## Privé PR-eigenaarsopvolging','',
   '- Handmatige PR-status: '+prLabels[p.querySelector('.pr-local-state').value]);
 const note=p.querySelector('.pr-note').value.trim();
 if(note)lines.push('- Privé PR-notitie: '+safeText(note));
 lines.push('','**Volgende geautoriseerde stap:** controleer opnieuw de actuele PR-HEAD, alle verplichte check runs, onafhankelijke review, branch protection en actieve claims; dit dossier doet geen GitHub-, Notion-, worker- of productiewijzigingen.','');
 privateDownload('zsentinel-pr-'+url.split('/').pop()+'-privé-overdracht.md',
   lines.join('\n')+'\n');
}
function issueReceipt(t,url){
 // Receipts come from Python's validated, bounded historical issue rows.
 const saved=JSON.parse(t.dataset.candidateEvidence||'[]');
 return saved.find(entry=>entry.url===url)||null;
}
function oneIssueHandoff(t){
 const issue=t.dataset.issue;
 if(!tickets.includes(t))return;
 const candidates=[t.dataset.pr,...t.dataset.prs.trim().split(' ').filter(Boolean)]
  .filter(Boolean);
 const c=collisionRows.find(row=>row.dataset.issue===issue);
 const lines=[
  '# zSentinel · privé eigenaarsdossier voor issue '+issue.split('/').pop(),'',
  '> Eén issue uit de begrensde offline bronopname. Geen actuele claim, PR-eigenaar, CI, review, merge, fix of deployvrijgave.','',
  '- Bronissue: [GitHub issue]('+issue+')',
  '- Project: '+safeText(t.dataset.project),
  '- Prioriteit uit bron: '+safeText(t.dataset.priority),
  '- Titel: '+safeText(t.dataset.title),
  '- Issuebron UTC: '+safeText(document.getElementById('issue-time').textContent),
  '- PR-bron UTC: '+safeText(document.getElementById('pr-time').textContent),
  '- Issue laatst waargenomen update: '+safeText(t.dataset.issueUpdated||'ONBEKEND'),
  '- Gekoppelde PR laatst waargenomen update: '+safeText(t.dataset.prUpdated||'ONBEKEND'),
  '- Historisch activiteitssignaal: '+safeText(t.dataset.activity),
  '- PR-bestandsdekking: '+(t.dataset.scopeComplete==='yes'?'binnen bronsteekproef compleet':'ONVOLLEDIG'),
  '- Issue-PR-verwijzingsdekking: '+(t.dataset.referencesComplete==='yes'?'binnen bronsteekproef compleet':'ONVOLLEDIG'),
  '- Exact privé brontoken: '+safeText(document.body.dataset.sourceToken),'',
  '## Menselijke issueopvolging','',
  '- Geobserveerde bronroute: '+safeText(t.dataset.category),
  '- Aanbevolen stap: '+safeText(t.dataset.next),
  '- Handmatige issuestatus: '+safeText(labels[t.querySelector('.local-state').value]),
  '- PR-kandidaten binnen deze bron: '+candidates.length
 ];
 const note=t.querySelector('textarea').value.trim();
 if(note)lines.push('- Privé issuenotitie: '+safeText(note));
 if(c){
  lines.push('- Meer dan één PR verwijst naar dit issue: GEZIEN, geen bewezen bestandsbotsing of dubbele fix.',
   '- Lokale afstemming: '+safeText(collisionLabels[c.querySelector('.collision-state').value]));
  const cnote=c.querySelector('.collision-note').value.trim();
  if(cnote)lines.push('- Privé PR-afstemmingsnotitie: '+safeText(cnote));
 }
 lines.push('','## Bestaande PR-kandidaten uit opgeslagen bron','');
 if(!candidates.length)lines.push(
  'Geen gekoppelde PR in deze beperkte steekproef waargenomen. Dat bewijst NIET dat er geen bestaande PR bestaat.','');
 for(const [i,url] of candidates.entries()){
  const p=prs.find(group=>group.dataset.pr===url);
  const receipt=issueReceipt(t,url);
  const groupHead=p&&p.dataset.head;
  const receiptHead=receipt&&receipt.head;
  const sourceMismatch=Boolean(groupHead&&receiptHead&&groupHead!==receiptHead);
  const head=sourceMismatch?'ONBEKEND (BRONCONFLICT)':(groupHead||receiptHead||'ONBEKEND');
  const gate=p?(gateLabels[p.dataset.gate]||p.dataset.gate):
   (receipt?(gateLabels[receipt.gate]||receipt.gate):'ONBEKEND');
  lines.push('### '+(i+1)+'. [Bestaande PR #'+url.split('/').pop()+']('+url+')','',
   '- Historisch geobserveerde HEAD: '+safeText(head),
   '- Historische gatebron: '+safeText(gate)+(sourceMismatch?' · TEGENSTRIJDIGE HEADs; herlees bron':''),
   '- PR-groep in werkbak: '+(p?'JA':'NEE, geen actuele bronconclusie'));
  if(/^[0-9a-f]{40}$/.test(head))lines.push(
   '- Exact broncommit: [GitHub commit]('+url.split('/pull/')[0]+'/commit/'+head+')');
  if(p){
   lines.push(
    '- Gatevervolgstap uit bron: '+safeText(p.dataset.next),
    '- Waargenomen PR-bestandsoverlap: '+safeText(p.dataset.overlap),
    '- Historische PR-beweging: '+safeText(p.dataset.movement),
    '- Privé PR-status: '+safeText(prLabels[p.querySelector('.pr-local-state').value])
   );
   const pnote=p.querySelector('.pr-note').value.trim();
   if(pnote)lines.push('- Privé PR-notitie: '+safeText(pnote));
   const linked=p.dataset.sourceIssues.trim().split(' ').filter(Boolean);
   const others=linked.filter(other=>other!==issue);
   if(others.length){
    lines.push('- Andere bronissuevermeldingen bij deze PR (geen bewezen fixes):');
    for(const other of others)lines.push('  - [Issue #'+other.split('/').pop()+']('+other+')'+
      (tickets.some(card=>card.dataset.issue===other)?'':' · buiten huidig beperkt venster'));
   }
  }
  lines.push('');
 }
 lines.push('**Volgende geautoriseerde stap:** herlees actuele open issues, actieve claims, exacte PR-HEADs, verplichte CI, onafhankelijke review en branch protection. Deze privé-overdracht heeft nergens extern iets gewijzigd.','');
 privateDownload('zsentinel-issue-'+issue.split('/').pop()+'-prive-eigenaarsdossier.md',lines.join('\n')+'\n');
}
for(const t of tickets)t.querySelector('.issue-export-one').addEventListener('click',
 ()=>oneIssueHandoff(t));
focusExport.addEventListener('click',onePrHandoff);
function exportOutstandingPrHandoff(){
 const outstanding=prs.filter(p=>p.querySelector('.pr-local-state').value!=='followup');
 const lines=[
  '# zSentinel · openstaande bestaande PRs voor menselijke opvolging','',
  '> Privé offline bronopname: lokale opvolgnotities, geen actuele PR- of eigenaarvrijgave.','',
  '- Oorspronkelijke PR-bron UTC: '+safeText(document.getElementById('pr-time').textContent),
  '- Oorspronkelijke issuebron UTC: '+safeText(document.getElementById('issue-time').textContent),
  '- Exact opgeslagen brontoken: '+safeText(document.body.dataset.sourceToken),
  '- Bestaande PRs in bronvenster: '+prs.length,
  '- Open menselijke opvolging: '+outstanding.length,
  '- Met lokale opvolging genoteerd (NIET gemerged): '+(prs.length-outstanding.length),
  '- Selectie hangt af van lokaal opgeslagen opvolgstatussen; filters/PR-focus wijzigen deze inventaris niet.',
  '- Externe GitHub/Notion claims, verplichte exacte-head CI, reviews en branchbescherming: NIET opnieuw vastgesteld.',''
 ];
 for(const [index,p] of outstanding.entries()){
  const pr=p.dataset.pr;
  const head=p.dataset.head||'ONBEKEND';
  const all=p.dataset.sourceIssues.split(' ').filter(Boolean);
  const shown=p.dataset.issues.split(' ').filter(Boolean);
  const status=p.querySelector('.pr-local-state').value;
  lines.push(
   '## '+(index+1)+'. [Bestaande PR #'+pr.split('/').pop()+']('+pr+')','',
   '- Project: '+safeText(p.dataset.project),
   '- Prioriteit bron: '+safeText(p.dataset.priority),
   '- Waargenomen HEAD: '+safeText(head),
   '- Historische gate: '+safeText(gateLabels[p.dataset.gate]||p.dataset.gate)+
      ' ('+safeText(p.dataset.gate)+')',
   '- Bronbestandsoverlap: '+safeText(p.dataset.overlap),
   '- Historische bronbeweging: '+safeText(p.dataset.movement),
   '- Menselijke gatevervolgstap: '+safeText(p.dataset.next),
   '- Privé PR-status: '+safeText(prLabels[status]||status),
   '- Gekoppelde bronissues: '+all.length+
     '; zichtbaar in deze werkbak: '+shown.length
  );
  if(head!=='ONBEKEND')lines.push('- GitHub broncommit: [Bekijk commit]('+
      pr.split('/pull/')[0]+'/commit/'+head+')');
  const note=p.querySelector('.pr-note').value.trim();
  if(note)lines.push('- Privé PR-opvolgnotitie: '+safeText(note));
  for(const issue of all){
   lines.push('  - [Bronissue #'+issue.split('/').pop()+']('+issue+')');
   const t=tickets.find(card=>card.dataset.issue===issue);
   if(!t){
    lines.push('    - Buiten de beperkte issuepagina; huidige status ONBEKEND.');
    continue;
   }
   lines.push('    - '+safeText(t.dataset.priority)+' · '+safeText(t.dataset.title));
   const alternatives=[t.dataset.pr,...t.dataset.prs.split(' ').filter(Boolean)]
     .filter(url=>url&&url!==pr);
   if(alternatives.length)lines.push('    - Andere PR-kandidaten (geen fix-/eigenaarsbewijs): '+
    alternatives.map(url=>'[PR '+url.split('/').pop()+']('+url+')').join(', '));
   const local=t.querySelector('.local-state').value;
   lines.push('    - Lokale issueopvolging: '+safeText(labels[local]||local));
  }
  lines.push('');
 }
 lines.push('**Volgende stap:** eigenaar bepaalt per PR of deze vrij is; lees '
  +'de actuele PR-HEAD, complete verplichte CI, onafhankelijke review en '
  +'branch protection op GitHub voor een eventuele bevoegde merge. '
  +'Dit document schrijft niets naar GitHub, Notion of productie.','');
 privateDownload('zsentinel-prs-open-prive-eigenaaroverdracht.md',lines.join('\n')+'\n');
}
document.getElementById('pr-review-export').addEventListener('click',exportOutstandingPrHandoff);

function exportProjectHandoff(){
 const selected=project.value;
 if(!selected||!Array.from(project.options).some(o=>o.value===selected&&selected))return;
 const sourceTickets=tickets.filter(t=>t.dataset.project===selected);
 const sourcePrs=prs.filter(p=>p.dataset.project===selected);
 const sourceCollisions=collisionRows.filter(c=>c.dataset.project===selected);
 if(!sourceTickets.length)return;
 const parts=[
  '# zSentinel · privé eigenaarsoverdracht '+safeText(selected),'',
  '> Eén project uit de beperkte opgeslagen bronopname; geen actuele CI, eigenaars-, fix-, claim-, merge- of deployvrijgave.','',
  '- Bron issue UTC: '+safeText(document.getElementById('issue-time').textContent),
  '- Bron PR UTC: '+safeText(document.getElementById('pr-time').textContent),
  '- Exact bron-token: '+safeText(document.body.dataset.sourceToken),
  '- Project: '+safeText(selected),
  '- Issuekaarten binnen opgeslagen venster: '+sourceTickets.length,
  '- Bestaande PR-groepen binnen opgeslagen venster: '+sourcePrs.length,
  '- Issues met meerdere bron-PRs in deze subset: '+sourceCollisions.length,
  '- Bronpagina en filters zijn begrensd; onbekende issues/PRs en actieve Notion-claims blijven ONBEKEND.',
  '- Huidige UI-zoekfilters en PR-focus beïnvloeden deze volledige projectsubset niet.','',
  '## Bestaande product-PRs binnen bronvenster',''
 ];
 if(!sourcePrs.length)parts.push('Geen bestaande PR-groep in deze opgeslagen subset gezien. Dit is geen bewijs dat er geen PR bestaat.','');
 for(const [index,p] of sourcePrs.entries()){
  const pr=p.dataset.pr,head=p.dataset.head||'ONBEKEND';
  const shown=p.dataset.issues.trim().split(' ').filter(Boolean);
  const all=p.dataset.sourceIssues.trim().split(' ').filter(Boolean);
  const local=p.querySelector('.pr-local-state').value;
  parts.push(
   '### '+(index+1)+'. [Bestaande PR #'+pr.split('/').pop()+']('+pr+')','',
   '- Bron HEAD: '+safeText(head),
   '- Historische gate: '+safeText(gateLabels[p.dataset.gate]||p.dataset.gate)+
     ' ('+safeText(p.dataset.gate)+')',
   '- Prioriteit: '+safeText(p.dataset.priority),
   '- Menselijke gatevervolgstap: '+safeText(p.dataset.next),
   '- Bestands-/historische overlapindicatie: '+
     safeText(p.dataset.overlap)+' / '+safeText(p.dataset.movement),
   '- Privé opvolgstatus: '+safeText(prLabels[local]||local),
   '- Gekoppelde issuevermeldingen bron: '+all.length+
     '; binnen dit venster: '+shown.length
  );
  if(head!=='ONBEKEND')parts.push(
   '- Historische broncommit: [GitHub commit]('+pr.split('/pull/')[0]+'/commit/'+head+')');
  const note=p.querySelector('.pr-note').value.trim();
  if(note)parts.push('- Privé PR-notitie: '+safeText(note));
  for(const issue of shown)parts.push('  - [Bronissue #'+issue.split('/').pop()+']('+issue+')');
  if(all.length>shown.length)parts.push(
   '- ONVOLLEDIG: '+(all.length-shown.length)+
   ' aanvullende issuevermelding(en) buiten de huidige beperkte issuepagina.');
  parts.push('');
 }
 parts.push('## Geobserveerde projectissues', '');
 for(const [index,t] of sourceTickets.entries()){
  const url=t.dataset.issue;
  const state=t.querySelector('.local-state').value;
  const candidates=[t.dataset.pr,...t.dataset.prs.split(' ').filter(Boolean)]
    .filter(Boolean);
  parts.push(
   '### '+(index+1)+'. [Issue #'+url.split('/').pop()+']('+url+')','',
   '- Prioriteit: '+safeText(t.dataset.priority),
   '- Titel: '+safeText(t.dataset.title),
   '- Bronroute: '+safeText(t.dataset.category),
   '- Aanbevolen vervolgstap: '+safeText(t.dataset.next),
   '- Privé issuestatus: '+safeText(labels[state]||state),
   '- PR-kandidaten in bron: '+candidates.length
  );
  for(const pr of candidates){
   const p=prs.find(x=>x.dataset.pr===pr);
   parts.push('  - [Bestaande PR #'+pr.split('/').pop()+']('+pr+') · bron HEAD '+
    safeText(p&&p.dataset.head||'ONBEKEND'));
  }
  const note=t.querySelector('textarea').value.trim();
  if(note)parts.push('- Privé issuenotitie: '+safeText(note));
  const collision=sourceCollisions.find(c=>c.dataset.issue===url);
  if(collision){
   const status=collision.querySelector('.collision-state').value;
   parts.push('- Meerdere PRs: '+safeText(collisionLabels[status]||status)+
    ' (geen aangetoonde bestandsbotsing)');
   const note=collision.querySelector('.collision-note').value.trim();
   if(note)parts.push('- Privé botsingsnotitie: '+safeText(note));
  }
  parts.push('');
 }
 parts.push('## Vereiste handmatige gates','',
  'Controleer voor ieder bestaand PR opnieuw de huidige GitHub-HEAD, volledige verplichte CI, onafhankelijke review, branchbescherming en actuele Notion/workerclaims. Dit bestand is alleen een lokaal privé-overzicht; het heeft niets op GitHub, Notion of de VPS gewijzigd.','');
 privateDownload('zsentinel-project-'+selected+'-prive-overdracht.md',parts.join('\n')+'\n');
}
projectExport.addEventListener('click',exportProjectHandoff);


function exportCollisionHandoff(onlyUnresolved,focusIssue=null){
 if(focusIssue!==null&&!collisionRows.some(c=>c.dataset.issue===focusIssue))return;
 const selectedCollisions=collisionRows.filter(c=>(!focusIssue||c.dataset.issue===focusIssue)&&
  (!onlyUnresolved||c.querySelector('.collision-state').value!=='followup'));
 const lines=[
  '# zSentinel · '+(focusIssue?'privé PR-afstemming voor één issue':
   onlyUnresolved?'openstaande privé PR-afstemming':'privé afstemming meervoudige PR-bronissues'),'',
  '> '+(focusIssue?'Eén bronissue uit de':'Volledige')+
   ' beperkte opgeslagen botsingsinventaris; geen bewezen file conflict, fixes, eigenaren, CI of mergebevoegdheid.','',
  '- Issuebron UTC: '+safeText(document.getElementById('issue-time').textContent),
  '- PR-bron UTC: '+safeText(document.getElementById('pr-time').textContent),
  '- Exact privé bron-token: '+safeText(document.body.dataset.sourceToken),
  '- Totale meervoudige bronissuevermeldingen: '+collisionRows.length,
  '- In dit bestand: '+selectedCollisions.length,
  '- Geselecteerd bronissue: '+(focusIssue||'geen, volledige of openstaande beperkte bronlijst'),
  '- Handmatig opvolging vastgelegd (niet opgelost), overgeslagen bij open werk: '+
    (onlyUnresolved?collisionRows.filter(c=>c.querySelector('.collision-state').value==='followup').length:0),
  '- Huidige UI-filters of geselecteerde PR veranderen de opgeslagen bronlijst niet.',
  '- Open-werkexport sluit alleen lokaal gemarkeerde opvolging uit; bewijst geen oplossing.',''
 ];
 for(const [i,c] of selectedCollisions.entries()){
  const issue=c.dataset.issue, t=tickets.find(row=>row.dataset.issue===issue);
  const sourcePrs=c.dataset.prs.split(' ').filter(Boolean);
  lines.push('## '+(i+1)+'. [Issue #'+issue.split('/').pop()+']('+issue+')','',
   'Prioriteit uit bron: '+safeText(c.dataset.priority),
   'Project uit bron: '+safeText(c.dataset.project),
   'Titel: '+safeText(c.dataset.title),
   'PR-kandidaten uit hetzelfde beperkte issuevenster: '+sourcePrs.length,
   'Een gedeeld issue bewijst geen overlapping van bestanden of twee complete fixes.','');
  lines.push('Lokale botsingsopvolging: '+
   collisionLabels[c.querySelector('.collision-state').value]);
  const collisionNote=c.querySelector('.collision-note').value.trim();
  if(collisionNote)lines.push('Privé botsingsnotitie: '+safeText(collisionNote));
  if(t){
   lines.push('Handmatige lokale issuestatus: '+labels[t.querySelector('.local-state').value]);
   const note=t.querySelector('textarea').value.trim();
   if(note)lines.push('Privé issuenotitie: '+safeText(note));
  }
  for(const pr of sourcePrs){
   const group=prs.find(p=>p.dataset.pr===pr);
   const head=group&&group.dataset.head;
   lines.push('- [Bestaande PR #'+pr.split('/').pop()+']('+pr+')',
    '  - Geobserveerde bron HEAD: '+safeText(head||'ONBEKEND'),
    '  - Gate: '+safeText(group?(gateLabels[group.dataset.gate]||group.dataset.gate):'ONBEKEND'));
   if(group){
    lines.push('  - Menselijke gatevervolgstap: '+safeText(group.dataset.next),
      '  - PR-bestandsoverlap in de bron: '+safeText(group.dataset.overlap),
      '  - Handmatige lokale PR-status: '+prLabels[group.querySelector('.pr-local-state').value]);
    const prNote=group.querySelector('.pr-note').value.trim();
    if(prNote)lines.push('  - Privé PR-notitie: '+safeText(prNote));
   }
  }
  lines.push('');
 }
 lines.push('Herlees op GitHub/Notion voor werkovername of merge: huidige claims en PR HEAD, verplichte CI, onafhankelijke reviews, branch protection en exacte scope.','');
 privateDownload(focusIssue?'zsentinel-pr-botsing-issue-'+focusIssue.split('/').pop()+'-prive.md':
  onlyUnresolved?'zsentinel-pr-botsingen-open-afstemming.md':
  'zsentinel-pr-botsingen-prive-overdracht.md',lines.join('\n')+'\n');
}
collisionExport.addEventListener('click',()=>exportCollisionHandoff(false));
collisionOpenExport.addEventListener('click',()=>exportCollisionHandoff(true));
for(const c of collisionRows)c.querySelector('.collision-export-one').addEventListener('click',
 ()=>exportCollisionHandoff(false,c.dataset.issue));
document.getElementById('export').addEventListener('click',function(){
 const parts=['# zSentinel · private handmatige opvolging','',
  'Issuebron UTC: '+safeText(document.getElementById('issue-time').textContent),
  'PR-bron UTC: '+safeText(document.getElementById('pr-time').textContent),
  'Exact privé bron-token: '+safeText(document.body.dataset.sourceToken),
  'Geen claim-, merge-, CI-, review- of deployautorisatie.',''];
 for(const [index,t] of tickets.entries()){
  const issue=t.dataset.issue;
  parts.push(String(index+1)+'. ['+safeText(t.dataset.title)+']('+issue+') · '+
   safeText(t.dataset.project)+' · '+safeText(t.dataset.priority));
  parts.push('   Bestaande PR: '+(t.dataset.pr?'[Open PR]('+t.dataset.pr+')':'niet gezien in steekproef'));
  const additional=t.dataset.prs.trim()?t.dataset.prs.trim().split(' '):[];
  if(additional.length){
   parts.push('   Andere bestaande PRs (geen eigenaars- of mergebewijs):');
   for(const alt of additional.slice(0,4))parts.push('   - [Open PR]('+alt+')');
  }
  parts.push('   Bronroute: '+safeText(t.dataset.category));
  parts.push('   Aanbevolen stap: '+safeText(t.dataset.next));
  parts.push('   Handmatige status: '+labels[t.querySelector('.local-state').value]);
  const note=t.querySelector('textarea').value.trim();
  if(note)parts.push('   Lokale operatornotitie: '+safeText(note));
  parts.push('');
 }
 if(prs.length)parts.push('## Bestaande PRs: één handmatige opvolging per PR','',
  'Groepskoppelingen zijn bronverwijzingen, geen eigenaars-, review- of fixbewijs.','');
 for(const p of prs){
  const url=p.dataset.pr;
  parts.push('- [Bestaande PR]('+url+') · '+safeText(p.dataset.priority)+
   ' · '+safeText(p.dataset.project));
  const issues=p.dataset.issues.trim()?p.dataset.issues.trim().split(' '):[];
  for(const issue of issues)parts.push('  - [Bronissue]('+issue+')');
  parts.push('  - Waargenomen bron HEAD: '+safeText(p.dataset.head||'ONBEKEND'));
  if(p.dataset.head)parts.push('  - Broncommit: [GitHub commit]('+
    url.split('/pull/')[0]+'/commit/'+p.dataset.head+')');
  parts.push('  - Waargenomen gatebron: '+safeText(gateLabels[p.dataset.gate]||p.dataset.gate)+
   ' (geen autorisatie)');
  parts.push('  - Handmatige PR-status: '+prLabels[p.querySelector('.pr-local-state').value]);
  const note=p.querySelector('.pr-note').value.trim();
  if(note)parts.push('  - Privé PR-opvolging: '+safeText(note));
  parts.push('');
 }
 if(collisionRows.length)parts.push('## Issues met meerdere bestaande PRs', '',
  'Dit is een beperkte bronsteekproef, geen vastgestelde bestandsbotsing of eigenaarsclaim.','');
 for(const c of collisionRows){
  const issue=c.dataset.issue;
  parts.push('- [Bronissue '+issue.split('/').pop()+']('+issue+') · '+
    safeText(c.dataset.priority)+' · '+safeText(c.dataset.title));
  parts.push('  - Bestaande PRs in bron: '+c.dataset.prs.split(' ')
    .filter(Boolean).map(url=>'[PR '+url.split('/').pop()+']('+url+')').join(', '));
  parts.push('  - Lokale botsingsopvolging: '+
    collisionLabels[c.querySelector('.collision-state').value]);
  const note=c.querySelector('.collision-note').value.trim();
  if(note)parts.push('  - Privé botsingsnotitie: '+safeText(note));
 }
 parts.push('Statussen zijn uitsluitend handmatig, lokaal en niet op GitHub/Notion vastgelegd.');
 const blob=new Blob([parts.join('\n')+'\n'],{type:'text/markdown;charset=utf-8'});
 const url=URL.createObjectURL(blob);const a=document.createElement('a');
 a.href=url;a.download='zsentinel-prive-opvolging.md';a.click();
 window.setTimeout(function(){URL.revokeObjectURL(url);},3000);
});
document.getElementById('session-save').addEventListener('click',function(){
 const rows=tickets.map(function(t){return {
  issue:t.dataset.issue,
  status:t.querySelector('.local-state').value,
  note:t.querySelector('textarea').value.slice(0,700)
 };});
 const prRows=prs.map(function(p){return {
  pr:p.dataset.pr,
  status:p.querySelector('.pr-local-state').value,
  note:p.querySelector('.pr-note').value.slice(0,500)
 };});
 const collisionEntries=collisionRows.map(c=>({
  issue:c.dataset.issue,
  status:c.querySelector('.collision-state').value,
  note:c.querySelector('.collision-note').value.slice(0,500)
 }));
 const data=JSON.stringify({
  schema:'zsentinel-local-handoff-v2',
  source_token:document.body.dataset.sourceToken,
  saved_at:new Date().toISOString(),
  focused_pr:focusedPr,
  entries:rows, pr_entries:prRows, collision_entries:collisionEntries,
  merge_authorized:false,claim_authorized:false,remote_writes_performed:false
 },null,2);
 const blob=new Blob([data+'\n'],{type:'application/json;charset=utf-8'});
 const url=URL.createObjectURL(blob);const a=document.createElement('a');
 a.href=url;a.download='zsentinel-prive-sessie.json';a.click();
 window.setTimeout(function(){URL.revokeObjectURL(url);},3000);
 lastSavedState=localSnapshot();
 updateSessionIndicator();
 document.getElementById('session-status').textContent=
   'JSON-export gestart; bewaar dit lokale bestand veilig. Extern is niets opgeslagen.';
});
document.getElementById('session-import').addEventListener('change',async function(event){
 const input=event.currentTarget, file=input.files&&input.files[0];
 if(!file)return;
 try{
  if(file.size>524288||file.size<10)throw new Error('Privébestand is ongeldig of te groot');
  const restored=JSON.parse(await file.text());
  const oldSession=restored&&restored.schema==='zsentinel-local-handoff-v1'&&
    restored.source_token===document.body.dataset.legacySourceToken;
  const currentSession=restored&&restored.schema==='zsentinel-local-handoff-v2'&&
    restored.source_token===document.body.dataset.sourceToken;
  if(!restored||(!oldSession&&!currentSession)||
     restored.merge_authorized!==false||restored.claim_authorized!==false||
     restored.remote_writes_performed!==false||
     !Array.isArray(restored.entries)||restored.entries.length!==tickets.length||
     (currentSession&&(!Array.isArray(restored.pr_entries)||
       restored.pr_entries.length!==prs.length))||
     (oldSession&&Object.prototype.hasOwnProperty.call(restored,'pr_entries'))||
     (oldSession&&Object.prototype.hasOwnProperty.call(restored,'collision_entries'))||
     (currentSession&&Object.prototype.hasOwnProperty.call(restored,'collision_entries')&&
      (!Array.isArray(restored.collision_entries)||
       restored.collision_entries.length!==collisionRows.length))||
     (currentSession&&Object.prototype.hasOwnProperty.call(restored,'focused_pr')&&
      restored.focused_pr!==null&&!prs.some(p=>p.dataset.pr===restored.focused_pr)))
    throw new Error('Deze sessie hoort niet bij dezelfde bronopname');
  const allowed=new Set(Object.keys(labels));
  for(let i=0;i<tickets.length;i++){
    const item=restored.entries[i];
    if(!item||item.issue!==tickets[i].dataset.issue||!allowed.has(item.status)||
       typeof item.note!=='string'||item.note.length>700)
      throw new Error('Sessie bevat geen betrouwbare handmatige opvolging');
  }
  if(currentSession){
   const prAllowed=new Set(Object.keys(prLabels));
   for(let i=0;i<prs.length;i++){
     const item=restored.pr_entries[i];
     if(!item||item.pr!==prs[i].dataset.pr||!prAllowed.has(item.status)||
        typeof item.note!=='string'||item.note.length>500)
       throw new Error('Sessie bevat ongeldige PR-opvolging');
   }
   if(Object.prototype.hasOwnProperty.call(restored,'collision_entries')){
    const collisionAllowed=new Set(Object.keys(collisionLabels));
    for(let i=0;i<collisionRows.length;i++){
     const item=restored.collision_entries[i];
     if(!item||item.issue!==collisionRows[i].dataset.issue||
        !collisionAllowed.has(item.status)||
        typeof item.note!=='string'||item.note.length>500)
       throw new Error('Sessie bevat ongeldige botsingsopvolging');
    }
   }
  }
  for(let i=0;i<tickets.length;i++){
    tickets[i].querySelector('.local-state').value=restored.entries[i].status;
    tickets[i].querySelector('textarea').value=restored.entries[i].note;
  }
  if(currentSession){
   for(let i=0;i<prs.length;i++){
     prs[i].querySelector('.pr-local-state').value=restored.pr_entries[i].status;
     prs[i].querySelector('.pr-note').value=restored.pr_entries[i].note;
   }
   if(Object.prototype.hasOwnProperty.call(restored,'collision_entries')){
    for(let i=0;i<collisionRows.length;i++){
     collisionRows[i].querySelector('.collision-state').value=
       restored.collision_entries[i].status;
     collisionRows[i].querySelector('.collision-note').value=
       restored.collision_entries[i].note;
    }
   }
   if(Object.prototype.hasOwnProperty.call(restored,'focused_pr')){
    focusedPr=restored.focused_pr;
    focusIndex=-1;
    search.value='';priority.value='';project.value='';activity.value='';state.value='';prState.value='';
   }
  }
  lastSavedState=localSnapshot();
  refresh();
  document.getElementById('session-status').textContent=oldSession?
   'Oude issuenotities exact op bron hersteld; PR-groepsnotities blijven ongewijzigd.':
   'Privésessie inclusief PR-opvolging hersteld, alleen lokaal in dit browservenster.';
 }catch(error){
  document.getElementById('session-status').textContent=
   'Import geweigerd: '+(error instanceof Error?error.message:'onbekende bron');
 }finally{input.value='';}
});
for(const item of [...tickets,...prs,...collisionRows]){
 item.addEventListener('input',updateSessionIndicator);
 item.addEventListener('change',updateSessionIndicator);
}
window.addEventListener('beforeunload',function(e){
 if(sessionDirty()){
  e.preventDefault();e.returnValue='';
 }
});
lastSavedState=localSnapshot();
refresh();
})();</script>"""
    # Exact saved source identity; local notes cannot silently attach
    # to different observations or reordered PR/issue sets.
    source_identity = {
        "issue_time": str(ts_issue), "pr_time": str(ts_pr),
        "visible_pr_groups": group_source,
        "visible_rows": [
            (row["issue_url"], row["candidate_existing_pr_url"],
             row.get("candidate_exact_head_gate_state"),
             tuple((candidate["url"], candidate["gate_state"],
                    candidate.get("source_head_observed"),
                    candidate["file_overlap_observed"],
                    candidate["source_movement_observed"])
                   for candidate in row.get("all_existing_pr_candidates_in_sample", [])))
            for row in cards[:max_rows]
        ],
    }
    source_token = hashlib.sha256(json.dumps(
        source_identity, ensure_ascii=True, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    # Keep old issue-only private session notes importable exclusively when
    # their exact original issue/PR/gate source token still matches.
    legacy_identity = {
        "issue_time": str(ts_issue), "pr_time": str(ts_pr),
        "visible_rows": [
            (row["issue_url"], row["candidate_existing_pr_url"],
             row.get("candidate_exact_head_gate_state"),
             tuple((candidate["url"], candidate["gate_state"],
                    candidate["file_overlap_observed"],
                    candidate["source_movement_observed"])
                   for candidate in row.get("other_existing_pr_candidates_in_sample", [])))
            for row in cards[:max_rows]
        ],
    }
    legacy_token = hashlib.sha256(json.dumps(
        legacy_identity, ensure_ascii=True, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    project_names = sorted({r["project"] for r in cards[:max_rows]})
    p_opts = "".join("<option value='" + escape(name, quote=True) + "'>" +
                     escape(name) + "</option>" for name in project_names)
    return (
        "<!doctype html><html lang='nl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='referrer' content='no-referrer'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
        "script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; "
        "img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'\">"
        "<title>zSentinel · merge-eerst werkbak</title>"
        "<style>body{font:16px/1.55 system-ui;background:#111927;color:#eff5fc;"
        "max-width:1120px;margin:auto;padding:18px}h1{font-size:1.6rem}"
        "h2{font-size:1.15rem}a{color:#a6d7ff}a:focus-visible,button:focus-visible,"
        "input:focus-visible,textarea:focus-visible,select:focus-visible{outline:3px solid #f0c976}"
        ".filters{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:18px 0}"
        "label{display:grid;gap:4px}input,select,textarea,button{font:inherit;"
        "background:#233248;color:white;border:1px solid #8595ac;border-radius:6px;"
        "padding:10px}button{cursor:pointer}.ticket{border:1px solid #526a83;"
        "background:#1b293c;border-radius:10px;padding:16px;margin:14px 0}"
        ".ticket[hidden]{display:none}.ticket:focus{outline:3px solid #f0c976;scroll-margin:90px}"
        ".ticket header{display:flex;gap:12px;"
        "align-items:center}.priority{font-weight:bold;background:#3a4d66;padding:2px 8px}"
        ".pr-panel{border:1px solid #70879f;border-radius:10px;padding:16px;"
        "margin:18px 0;background:#172338}.pr-panel ol{padding-left:24px}"
        ".pr-work{padding:12px;border-bottom:1px solid #526a83}"
        ".pr-work:focus{outline:3px solid #f0c976;scroll-margin:90px}"
        ".pr-work[hidden],.focus-bar[hidden],.collision[hidden]{display:none}"
        ".collision{border-bottom:1px solid #526a83;padding:14px 4px}"
        ".collision:focus{outline:3px solid #f0c976;scroll-margin:90px}"
        ".collision ul{padding-left:26px}.collision li{margin:9px 0}"
        ".collision-controls{display:grid;grid-template-columns:minmax(170px,260px) 1fr;"
        "gap:12px;padding:12px 0}.collision-note{width:100%;box-sizing:border-box}"
        "@media(max-width:640px){.collision-controls{grid-template-columns:1fr}}"
        ".focus-alternative{padding:5px 9px;margin:4px 0}"
        ".pr-controls{display:grid;grid-template-columns:minmax(170px,260px) 1fr;"
        "gap:14px;margin:12px 0}.pr-note{width:100%;box-sizing:border-box}"
        ".controls{display:grid;grid-template-columns:minmax(170px,260px) 1fr;"
        "gap:16px}.controls textarea{width:100%;box-sizing:border-box;resize:vertical}"
        ".source,.warning{color:#ebce9b}.links a{margin-right:10px}"
        ".focus-bar{position:sticky;top:0;background:#293950;border:2px solid #ebce9b;"
        "padding:12px;border-radius:9px;z-index:2;display:flex;align-items:center;"
        "gap:12px;flex-wrap:wrap}"
        "@media(max-width:640px){.controls,.pr-controls{grid-template-columns:1fr}}"
        "</style></head><body data-source-token='" + source_token +
        "' data-legacy-source-token='" + legacy_token +
        "'><h1>zSentinel · merge-eerst werkbak</h1>"
        "<p>Geobserveerde P0/P1-productissues, bestaande PRs en menselijke vervolgstappen. "
        "Geen automatische claim, issue-afhandeling, CI, review, merge, runnerherstel of deploy. "
        "Notities en statussen blijven lokaal; bewaar een privé-sessiebestand om later precies dezelfde bron te hervatten.</p>"
        "<p>Bron issue UTC <span id='issue-time'>" + issue_stamp +
        "</span> · PR UTC <span id='pr-time'>" + pr_stamp + "</span></p>"
        "<p class='activity-summary'>" + escape(activity_summary) + "</p>"
        "<div class='filters'><label>Zoeken<input id='q' type='search'></label>"
        "<label>Prioriteit<select id='priority'><option value=''>Alles</option>"
        "<option>P0</option><option>P1</option><option>P2</option>"
        "<option>Unspecified</option></select></label>"
        "<label>Project<select id='project'><option value=''>Alles</option>" +
        p_opts + "</select></label>"
        "<label>Issue-activiteit<select id='activity'><option value=''>Alle signalen</option>"
        "<option value='quiet_over_7_days_observed_claims_unknown'>Geen update &gt;7d · dekking compleet</option>"
        "<option value='quiet_over_7_days_partial_pr_coverage'>Geen update &gt;7d · dekking onvolledig</option>"
        "<option value='recent_issue_activity_observed'>Issue recent bijgewerkt</option>"
        "<option value='recent_linked_pr_activity_observed'>Gekoppelde PR recent bijgewerkt</option>"
        "<option value='unknown'>Activiteit onbekend</option></select></label>"
        "<label>Handmatige status<select id='state'><option value=''>Alles</option>"
        "<option value='open'>Nog te bekijken</option>"
        "<option value='owner'>Wacht op eigenaar</option>"
        "<option value='coordination'>Afstemming nodig</option>"
        "<option value='followup'>Opvolging genoteerd</option></select></label>"
        "<label><input type='checkbox' id='collision-only'>Alleen issues/PRs met "
        "meerdere PR-kandidaten</label>"
        "<label>PR-opvolging<select id='pr-state'>"
        "<option value=''>Alle PR-statussen</option>"
        "<option value='open'>Nog te beoordelen</option>"
        "<option value='review'>Wacht op review/CI</option>"
        "<option value='owner'>Wacht op huidige eigenaar</option>"
        "<option value='coordinate'>Afstemming tussen PRs nodig</option>"
        "<option value='followup'>Opvolging vastgelegd</option></select></label>"
        "<label>Botsingsopvolging<select id='collision-state-filter'>"
        "<option value=''>Alle botsingsstatussen</option>"
        "<option value='unreviewed'>Nog niet beoordeeld</option>"
        "<option value='owner'>Eigenaar bepalen</option>"
        "<option value='coordinate'>Afstemmen met PR-eigenaren</option>"
        "<option value='followup'>Opvolging vastgelegd (niet opgelost)</option></select></label>"
        "<button id='reset' type='button'>Wis filters</button>"
        "<button id='collision-export' type='button'>Download PR-botsingsdossier (Markdown)</button>"
        "<button id='collision-open-export' type='button'>Download open afstemming (Markdown)</button>"
        "<button id='export' type='button'>Download privéoverdracht (Markdown)</button>"
        "<button id='session-save' type='button'>Bewaar lokale sessie (JSON)</button>"
        "<label>Heropen lokale sessie<input id='session-import' type='file' "
        "accept='.json,application/json'></label></div>"
        "<p id='visible' aria-live='polite'></p>"
        "<p id='issue-review-summary' aria-live='polite'></p>"
        "<button id='issue-review-next' type='button'>Volgende open bronissue (P0 eerst)</button>"
        "<button id='issue-review-export' type='button'>Download open P0/P1 issue-overdracht</button>"
        "<p id='pr-review-summary' aria-live='polite'></p>"
        "<button id='pr-review-next' type='button'>Volgende PR zonder lokale opvolging</button>"
        "<button id='pr-review-export' type='button'>Download open PR-eigenaaroverdracht</button>"
        "<button id='project-export' type='button' disabled>Kies eerst een project voor privéoverdracht</button>"
        "<p id='project-review-summary' aria-live='polite'></p>"
        "<button id='project-review-next' type='button'>Volgende project met open lokale opvolging</button>"
        "<p id='collision-visible' aria-live='polite'></p>"
        "<button id='collision-next' type='button'>Volgende open botsingsopvolging</button>"
        "<p id='session-status' aria-live='polite'></p>"
        "<p id='unsaved' role='status' aria-live='polite'></p>"
        "<div id='focus-bar' class='focus-bar' role='status' hidden>"
        "<strong id='focus-title'></strong>"
        "<button id='focus-next' type='button'>Volgende nog te bekijken issue</button>"
        "<button id='focus-export' type='button'>Download overdracht voor deze PR</button>"
        "<button id='focus-clear' type='button'>Terug naar alle bronissues</button></div>"
        "<p>Filters en PR-focus veranderen de privé-export niet: deze bevat alle bronkaarten.</p>" + hidden +
        "<main>" + collisions_panel + groups_panel + "".join(output) + "</main>" + script + "</body></html>"
    )
