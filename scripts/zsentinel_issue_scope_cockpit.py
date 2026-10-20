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

from zsentinel_operator_workbench import read_source_and_sha, validated_view
from zsentinel_product_issue_inbox import canonical_projects, collect as collect_live_issues
from zsentinel_product_delivery_board import prepare as prepare_delivery_board
from zsentinel_pr_scope_guard import ROOT, registry
from zsentinel_pr_scope_portfolio import collect_portfolio

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
    "verify_live_owner_then_product_implementation":
        "Controleer eerst huidige Notion-claims, open branches en bestandscope vóór productwerk.",
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


def compose(source, source_sha, canonical, selected, collision, saved_gates=None):
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
            if type(recorded) is not list or len(recorded) > 512:
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
        heads = item.get("inspected_pr_heads", [])
        # Current portfolio producer does not export these head receipts;
        # never infer a PR is conflict-free just because it has no pair.
        if heads and type(heads) is not list:
            raise ValueError("malformed inspected PR metadata")
        exact[pid] = {"source": item["source_status"],
                      "complete": item["sample_complete"],
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
    raw_titles = {}
    for row in source["cards"][:MAX_ISSUES]:
        name = row.get("title")
        if type(name) is str and 0 < len(name) <= 180:
            raw_titles[row["issue"]] = name
    issue_rows = []
    for card in source["cards"][:MAX_ISSUES]:
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
        elif refs:
            route = "inspect_existing_pr_and_owner_gates_first"
        else:
            route = "verify_live_owner_then_product_implementation"
        issue_rows.append({
            "priority": card["priority"], "project": pid,
            "issue": card["issue"], "url": card["url"],
            "title": raw_titles.get(card["issue"], card["issue"]),
            "existing_prs": refs, "next_action": route,
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
    for item in source.get("project_operations", []):
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
    branch_raw = source.get("coverage", {}).get("branches")
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
        "source_issue_observed_at_utc": source["observed_at_utc"],
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


def output_text(report, markdown=False):
    intro = ("# zSentinel · Gecombineerde product- en PR-bestandsscope"
             if markdown else "zSentinel | productissues + PR-bestandsscope")
    lines = [intro, "Issue-bron SHA256: " + report["product_issue_source_sha256"],
             "Bronmomenten ≤10 min uiteen: " +
             ("ja" if report["source_clocks_within_10_minutes"] else "NEE — oude bron is geen live claim")]
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
            lines.append("  Bronadvies: " + item["human_next_step"])
    if report["saved_unpr_branch_context"] is not None:
        branches = report["saved_unpr_branch_context"]
        lines.append("PR-loze branches (opgeslagen, geen live eigenaren): " +
                     str(branches["recent_overlap_candidates_observed"]) +
                     " overlapkandidaten; compleet: " +
                     str(branches["sample_complete"]))
    for row in report["issues"]:
        ref = ("[" + row["issue"] + "](" + row["url"] + ")" if markdown
               else row["url"])
        lines.append(row["priority"] + " | " + row["project"] + " | " +
                     ref + " | " + row["title"] + " | " + row["next_action_nl"])
        lines.append("  Advies uit bestaande bron: " + row["source_recommended_next_step"])
        for gap in row["source_evidence_gaps"]:
            lines.append("  Onbekend: " + gap)
        for pr in row["existing_prs"]:
            link = ("[" + pr["url"] + "](" + pr["url"] + ")" if markdown else pr["url"])
            lines.append("  " + link + " · " + pr["file_scope_state"] +
                         " · " + pr["issue_link_source"])
            gates = pr["saved_exact_head_gates"]
            if gates is not None:
                lines.append("    Historische exacte HEAD " + gates["observed_exact_head_sha"] +
                             " · checks: " + str(gates["saved_native_checks_success"]) +
                             " · observed approvals: " +
                             str(gates["saved_independent_approval_count_unverified"]) +
                             " (geen actuele merge-autorisatie)")
            for other in pr["overlap_with_open_prs"]:
                lines.append("    Gedeeld bestandspad met " + other)
    lines.extend(["", "Gedeeld pad ≠ mergeconflict. Geen claims, PR-less ownership, CI of review geautoriseerd.",
                  "P0 infrastructuur/billing uitsluitend bevoegd eigenaar. Geen writes, retries, merge of deploy."])
    return "\n".join(lines) + "\n"


def output_html(report):
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
    cards = []
    for row in report["issues"]:
        prs = []
        for pr in row["existing_prs"]:
            rivals = "".join(
                "<li><a rel='noopener noreferrer' href='" + escape(other, quote=True) +
                "'>" + escape(other) + "</a></li>"
                for other in pr["overlap_with_open_prs"])
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
                gate_note +
                ("<ul>" + rivals + "</ul>" if rivals else "") + "</li>")
        cards.append(
            "<article data-priority='" + escape(row["priority"], quote=True) +
            "' data-project='" + escape(row["project"], quote=True) +
            "'><small>" + escape(row["priority"]) + " · " +
            escape(row["project"]) + "</small><h2><a rel='noopener noreferrer' href='" +
            escape(row["url"], quote=True) + "'>" + escape(row["title"]) +
            "</a></h2><p class='meta'>" + escape(row["issue"]) +
            " · issuebron: " + escape(row["project_issue_coverage"]) +
            "</p><p><strong>Volgende stap:</strong> " +
            escape(row["next_action_nl"]) +
            "</p><p>Bronadvies: " + escape(row["source_recommended_next_step"]) +
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
        "</select></label><output id='filter-count' aria-live='polite'></output></div>"
    )
    # A purely local filter over already rendered text; no fetch, storage, or
    # executable content derived from issue titles, PR bodies or repo strings.
    offline_script = """<script>(function(){
'use strict';
const search=document.getElementById('filter-search');
const priority=document.getElementById('filter-priority');
const project=document.getElementById('filter-project');
const count=document.getElementById('filter-count');
const cards=Array.from(document.querySelectorAll('article[data-project]'));
function update(){
  const term=search.value.trim().toLocaleLowerCase('nl');
  let shown=0;
  for(const card of cards){
    const ok=(!term || card.textContent.toLocaleLowerCase('nl').includes(term)) &&
      (!priority.value || card.dataset.priority===priority.value) &&
      (!project.value || card.dataset.project===project.value);
    card.hidden=!ok;
    if(ok) shown++;
  }
  count.textContent=shown+' van '+cards.length+' productissues zichtbaar';
}
for(const el of [search,priority,project])el.addEventListener('input',update);
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
            "GitHub-data en een lokaal gekozen issue-bronbestand.</aside>"
            "<p>Projecten: " + escape(", ".join(report["chosen_projects"])) +
            ". Bronmomenten samenhangend: " +
            ("ja" if report["source_clocks_within_10_minutes"] else
             "NEE — controleer eerst nieuwe issue-evidence") + "</p>" +
            signals_html + filter_bar + "<main>" + "".join(cards) + "</main>" +
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
    parser.add_argument("--format", choices=("html", "json", "text", "markdown"),
                        default="html")
    args = parser.parse_args(argv)
    try:
        canonical = registry(args.registry)
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
            inbox = collect_live_issues(canonical, args.project,
                                        os.environ.get("GITHUB_TOKEN"), max_items=60)
            source = prepare_delivery_board(inbox, limit=60)
            raw = json.dumps(source, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
            sha = hashlib.sha256(raw).hexdigest()
        else:
            source, sha = read_source_and_sha(args.issue_board)
        view = validated_view(source)
        projects = _safe_projects(view, canonical, args.project)
        if not projects:
            raise ValueError("no canonical product-issue projects in the selected issue source")
        pr_scope = collect_portfolio(projects, canonical, os.environ.get("GITHUB_TOKEN"), args.max_prs)
        report = compose(view, sha, canonical, projects, pr_scope,
                         saved_gates=_saved_pr_gates(source))
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.format == "html":
            print(output_html(report))
        else:
            print(output_text(report, markdown=args.format == "markdown"))
        return 2 if any(p["existing_prs"] and any(
            ref["file_scope_state"] == "shared_file_paths_observed"
            for ref in p["existing_prs"]) for p in report["issues"]) else (
            3 if not pr_scope["source_coverage_complete"] else 0)
    except (OSError, ValueError) as exc:
        print("zSentinel: source-integration unavailable — " + str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
