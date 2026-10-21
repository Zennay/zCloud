#!/usr/bin/env python3
"""Offline, source-bound zSentinel operator workbench for real product issue delivery.

Only displays previously saved, validated board evidence and human notes.
Cannot grant ownership, trigger CI, merge, deploy or modify worker/queue state.
"""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
import os
import stat
from pathlib import Path
import re
import sys

from zsentinel_product_delivery_board import (
    ISSUE, PROJECT, URL, SCHEMAS, MAX_INPUT, unique, write_create_only, when,
)

MAX_CARDS = 60
MAX_REFERENCES = 3
MAX_GAPS = 15
MAX_NOTE = 1000
PRIORITIES = {"P0", "P1", "P2", "Unspecified"}
DECISIONS = ("investigate", "integrate-existing-pr", "coordinate-owner",
             "park-blocker", "ready-for-new-product-work")
HEX = re.compile(r"^[a-f0-9]{64}$")


def read_source_and_sha(path: Path):
    """Validate one stable, bounded regular snapshot and hash the SAME bytes."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("No-follow file opening is unavailable")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_INPUT:
        raise ValueError("Expected a bounded regular source snapshot")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    try:
        first = os.fstat(fd)
        if not stat.S_ISREG(first.st_mode) or (
                first.st_dev, first.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Board source identity drift")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(MAX_INPUT + 1)
        last = os.fstat(fd)
        if (len(raw) != first.st_size or len(raw) > MAX_INPUT or
                (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns) !=
                (last.st_dev, last.st_ino, last.st_size, last.st_mtime_ns)):
            raise ValueError("Board source changed during read")
    finally:
        os.close(fd)
    parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    if type(parsed) is not dict or parsed.get("schema") != SCHEMAS["board"]:
        raise ValueError("Unsupported source board schema")
    return parsed, hashlib.sha256(raw).hexdigest()


def validated_view(source: dict) -> dict:
    if source.get("authorizes_claim") is not False or (
        source.get("authorizes_merge") is not False or
        source.get("authorizes_deploy") is not False or
        source.get("automatic_dispatch_enabled") is not False
    ):
        raise ValueError("Source board unexpectedly authorizes privileged work")
    board_moment = when(source.get("observed_at_utc"))
    # Preserve the independent observation clock of every recovered source.
    # A board generated later is not proof that older retry evidence is live.
    source_clocks = source.get("source_times_utc")
    source_fresh = source.get("fresh")
    if (type(source_clocks) is not dict or type(source_fresh) is not dict
            or "inbox" not in source_clocks
            or set(source_clocks) - {"inbox", "retry", "branches", "claims", "prs"}
            or set(source_fresh) - {"inbox", "retry", "branches", "claims", "prs"}):
        raise ValueError("Missing bounded independent source clocks")
    admitted_clocks = {}
    for name, stamp in source_clocks.items():
        parsed = when(stamp)
        if parsed > board_moment:
            raise ValueError("A source cannot be newer than its captured board")
        if source_fresh.get(name) is not True and source_fresh.get(name) is not False:
            raise ValueError("Source freshness must be explicit")
        admitted_clocks[name] = stamp
    if set(source_fresh) != set(admitted_clocks):
        raise ValueError("Freshness flags and original source clocks disagree")
    cards = source.get("cards")
    if type(cards) is not list or len(cards) > MAX_CARDS:
        raise ValueError("Source board has unbounded cards")
    chosen = []
    seen = set()
    for card in cards:
        if type(card) is not dict or any(
            card.get(field) is not False for field in
            ("claim_authorized", "merge_authorized", "deploy_authorized")
        ):
            raise ValueError("Board card contains unauthorized action")
        issue, project, priority = (card.get("issue"), card.get("project"),
                                    card.get("priority"))
        if (type(issue) is not str or not ISSUE.fullmatch(issue) or issue in seen
                or type(project) is not str or not PROJECT.fullmatch(project)
                or priority not in PRIORITIES):
            raise ValueError("Noncanonical or duplicate product issue")
        seen.add(issue)
        refs = card.get("open_pr_candidates")
        gaps = card.get("evidence_gaps")
        if (type(refs) is not list or len(refs) > MAX_REFERENCES or
            len(set(refs)) != len(refs) or any(
                type(ref) is not str or not URL.fullmatch(ref) or
                re.fullmatch(
                    r"https://github[.]com/" +
                    re.escape(issue.rsplit("#", 1)[0]) +
                    r"/pull/[1-9][0-9]{0,8}", ref
                ) is None for ref in refs
            )):
            raise ValueError("Untrusted, cross-repository or duplicate PR references")
        if (type(gaps) is not list or len(gaps) > MAX_GAPS or
            any(type(g) is not str or len(g) > 300 for g in gaps)):
            raise ValueError("Untrusted or unbounded evidence gaps")
        title = card.get("title")
        if type(title) is not str or not title.strip() or len(title) > 180:
            raise ValueError("Invalid bounded original issue title")
        next_step = card.get("recommended_next_step")
        if type(next_step) is not str or len(next_step) > 650:
            raise ValueError("Noncanonical recommended next step")
        # Issue identities are constrained to owner/repository#number and
        # rebuilt into GitHub links rather than trusting a stored URL.
        repo, number = issue.rsplit("#", 1)
        chosen.append({
            "issue": issue, "project": project, "priority": priority,
            "title": title,
            "url": "https://github.com/" + repo + "/issues/" + number,
            "open_pr_candidates": refs, "evidence_gaps": gaps,
            "recommended_next_step": next_step,
            "issue_coverage": card.get("project_issue_coverage", "unknown"),
            "claim_context": ("reported_observation_only" if
                              card.get("observed_claim_ids") else "not_verified"),
        })
    coverage = source.get("coverage")
    if type(coverage) is not dict or type(coverage.get("projects")) is not dict or (
        len(coverage["projects"]) > 50
    ) or any(type(p) is not str or not PROJECT.fullmatch(p) or
             status not in ("partial", "degraded", "unavailable")
             for p, status in coverage["projects"].items()) or any(
        type(coverage.get(flag)) is not bool for flag in
        ("inbox_truncated", "cards_truncated", "github_denial")
    ):
        raise ValueError("Source board has incomplete coverage labels")
    safe_coverage = {
        "projects": coverage["projects"],
        "inbox_truncated": coverage["inbox_truncated"],
        "cards_truncated": coverage["cards_truncated"],
        "github_denial": coverage["github_denial"],
    }
    rows = source.get("project_operations")
    if type(rows) is not list or len(rows) > 60:
        raise ValueError("Source board contains invalid project operations")
    project_operations = []
    projects = set()
    valid_coverages = ("partial", "degraded", "unavailable")
    for row in rows:
        if (type(row) is not dict or type(row.get("project")) is not str
                or not PROJECT.fullmatch(row["project"])
                or row["project"] in projects
                or row.get("action_authorized") is not False
                or row.get("is_complete_inventory") is not False
                or row.get("issue_coverage") not in valid_coverages):
            raise ValueError("Ambiguous or authoritative project operation")
        pid = row["project"]
        projects.add(pid)
        action = row.get("recommended_next_step")
        if type(action) is not str or not action.strip() or len(action) > 650:
            raise ValueError("Invalid project next action")
        counts = row.get("issue_counts_in_sample")
        if (type(counts) is not dict
                or set(counts) != PRIORITIES
                or any(type(n) is not int or n < 0 or n > 100000000
                       for n in counts.values())):
            raise ValueError("Invalid project issue sample")
        queue = row.get("queue_observation")
        if queue is not None and (source_fresh.get("retry") is not True
                                  or "retry" not in admitted_clocks):
            raise ValueError("Project queue observation has no fresh source provenance")
        if queue is not None:
            if (type(queue) is not dict
                    or not {"active_items", "high_claim_attempts",
                            "expired_active_leases"}.issubset(queue)
                    or any(type(queue[k]) is not int or not 0 <= queue[k] <= 100000000
                           for k in ("active_items", "high_claim_attempts",
                                     "expired_active_leases"))
                    or queue["high_claim_attempts"] > queue["active_items"]
                    or queue["expired_active_leases"] > queue["active_items"]):
                raise ValueError("Invalid bounded queue observation")
        claims = row.get("observed_claim_ids")
        if claims is not None and (type(claims) is not list or len(claims) > 150):
            raise ValueError("Invalid claim observation")
        project_operations.append({
            "project": pid,
            "issue_coverage": row["issue_coverage"],
            "issue_counts_in_sample": {k: counts[k] for k in
                                       ("P0", "P1", "P2", "Unspecified")},
            "queue_observation": (
                {k: queue[k] for k in ("active_items", "high_claim_attempts",
                                       "expired_active_leases")}
                if queue is not None else None),
            "queue_source_observed_at_utc": (
                admitted_clocks["retry"] if queue is not None else None),
            "claim_context": ("reported_observation_only" if claims
                              else "not_verified"),
            "recommended_next_step": action,
        })
    if any(card["project"] not in safe_coverage["projects"] or
           card["issue_coverage"] != safe_coverage["projects"][card["project"]]
           for card in chosen):
        raise ValueError("Issue card no longer matches the admitted project coverage")
    return {"observed_at_utc": source["observed_at_utc"],
            "cards": chosen,
            "coverage": safe_coverage,
            "project_operations": project_operations,
            "project_source_observed_at_utc": admitted_clocks,
            "projects": sorted(projects | {r["project"] for r in chosen}),
            "global_gate": "live claims, exact-head CI, review and release authority unverified"}




def issue_pr_connections_html(view: dict, report: dict | None) -> str:
    """Embed separate, verified saved PR evidence in the existing human desk.

    Board issue cards and offline PR ZIPs are independently admitted inputs.
    The presence or absence of an observed PR link cannot release a claim.
    """
    if report is None:
        return ""
    e = lambda v: escape(str(v), quote=True)
    group_map = {row["issue_url"]: row for row in report["issues"]}
    visited = set()
    cards = []
    for card in view["cards"]:
        match = group_map.get(card["url"])
        if match is None:
            continue
        visited.add(card["url"])
        prs = []
        for pr in match["linked_existing_prs"][:12]:
            prs.append(
                '<li><a href="' + e(pr["pr_url"]) +
                '" target="_blank" rel="noopener noreferrer">Existing PR #' +
                str(pr["pr_number"]) + '</a> · saved ' +
                e(pr["source_priority"]) + ' · observed gate ' +
                e(pr["observed_primary_gate"]) +
                ' · last issue-link source #' +
                str(pr["issue_ref_last_seen_snapshot"]) +
                (' · issue-link NOT seen in final sample (UNKNOWN)'
                 if pr["issue_ref_absent_in_last_snapshot"] else '') +
                '</li>')
        omitted = len(match["linked_existing_prs"]) - len(prs)
        cards.append(
            '<article class="brief"><details><summary>' +
            e(card["priority"]) + ' ' + e(card["issue"]) + ' · ' +
            str(len(match["linked_existing_prs"])) +
            ' source-PR links</summary><p><a target="_blank" ' +
            'rel="noopener noreferrer" href="' + e(card["url"]) + '">' +
            e(card["title"]) +
            '</a> · Old saved PR references only; live ownership/CI/review '
            'remain UNKNOWN.</p><ol>' +
            ''.join(prs) + '</ol>' +
            ('<p>' + str(omitted) +
             ' other saved PR references not displayed. NOT resolved.</p>'
             if omitted else '') + '</details></article>')
    other_issues = [
        row for row in report["issues"]
        if row["issue_url"] not in visited
    ]
    unlinked_prs = report["unlinked_prs"]
    unlinked_links = [
        '<li><a href="' + e(pr["url"]) +
        '" target="_blank" rel="noopener noreferrer">' +
        e(pr["priority"]) + ' · ' + e(pr["url"]) +
        '</a> · issue relationship UNKNOWN</li>'
        for pr in unlinked_prs[:30]
    ]
    unmatched = [
        '<li><a href="' + e(row["issue_url"]) +
        '" target="_blank" rel="noopener noreferrer">Issue #' +
        str(row["issue_number"]) + '</a> · ' +
        str(len(row["linked_existing_prs"])) +
        ' source-seen existing PR(s)</li>'
        for row in other_issues[:30]
    ]
    return (
        '<section id="sourcePrConnections" class="portfolio" '
        'aria-label="Source-linked existing PRs for current product issues">'
        '<h2>Existing PRs · source-connected issue desk</h2>'
        '<p><strong>Separate historical ZIP evidence, NOT a live overlay.</strong> '
        'Links are matched by exact canonical issue URL. Board and PR ZIP '
        'sources were validated independently, so a missing link never proves '
        'that a project has no PR or an issue is free. Active owner, current '
        'HEAD, required CI, independent review and main merge remain UNKNOWN.</p>'
        '<p>PR sources: ' + str(len(report["source_snapshots"])) +
        ' · matched board issues: ' + str(len(cards)) +
        ' · other source-linked issues: ' + str(len(other_issues)) +
        ' · PRs without saved issue links: ' +
        str(report["pr_without_source_issue_count"]) + ' (not free).</p>'
        '<details><summary>Browse ' + str(len(cards)) +
        ' matching saved-PR issue groups</summary>' +
        ''.join(cards) + '</details>' +
        '<details><summary>Other source issues and unlinked PRs (incomplete '
        'coverage)</summary><ol>' + ''.join(unmatched) + '</ol>' +
        ('<p>' + str(len(other_issues) - len(unmatched)) +
         ' other historical issues omitted; NOT resolved.</p>'
         if len(other_issues) > len(unmatched) else '') +
        '<p>' + str(report["pr_without_source_issue_count"]) +
        ' saved PR(s) have no observed issue link; inspect originals before '
        'opening a new PR.</p><ol>' + ''.join(unlinked_links) + '</ol>' +
        ('<p>' + str(len(unlinked_prs) - len(unlinked_links)) +
         ' additional unlinked saved PR references not displayed (UNKNOWN).</p>'
         if len(unlinked_prs) > len(unlinked_links) else '') +
        '</details></section>'
    )

def project_operations_html(view: dict) -> str:
    """Show source-only queue pressure and recovery routes even with zero issues.

    Deliberately never embed opaque claim identifiers or runtime task titles.
    """
    sections = []
    def rank(row):
        queue = row["queue_observation"] or {}
        counts = row["issue_counts_in_sample"]
        # A sampled expired lease is a human reconciliation lead, not a
        # released lease. Preserve P0 priority inside that bounded category.
        return (0 if queue.get("expired_active_leases", 0) else
                1 if counts["P0"] else
                2 if queue.get("high_claim_attempts", 0) else
                3 if counts["P1"] else 4, row["project"])
    for row in sorted(view["project_operations"], key=rank):
        counts = row["issue_counts_in_sample"]
        queue = row["queue_observation"]
        queue_text = (
            "Queue source absent (UNKNOWN)"
            if queue is None else
            "Queue sampled: {} entries, {} high claim cycles, {} expired leases"
            .format(queue["active_items"], queue["high_claim_attempts"],
                    queue["expired_active_leases"])
        )
        if queue is not None and queue["expired_active_leases"]:
            badge = "Review suspected expired lease with current owner"
        elif counts["P0"]:
            badge = "Review observed P0 product blocker"
        elif queue is not None and queue["high_claim_attempts"]:
            badge = "Investigate repeated claim cycles (not failures)"
        elif counts["P1"]:
            badge = "Review observed P1 product work"
        else:
            badge = "Verify current project source and claims"
        sections.append(
            '<li data-recovery-project="' + escape(row["project"], quote=True) +
            '"><strong>' + escape(row["project"]) + '</strong> · ' +
            '<strong>' + escape(badge) + '</strong>' +
            '<br>P0 ' + str(counts["P0"]) + ' / P1 ' + str(counts["P1"]) +
            ' (observed sample) · GitHub coverage: ' +
            escape(row["issue_coverage"]) +
            '<br>' + escape(queue_text) +
            ' · original queue source UTC: ' +
            escape(row["queue_source_observed_at_utc"] or "unavailable") +
            ' · Current owner: UNKNOWN (' +
            escape(row["claim_context"]) + ')' +
            '<br>Suggested human investigation: ' +
            escape(row["recommended_next_step"]) +
            '<label>Local human follow-up <select data-recovery-choice="' +
            escape(row["project"], quote=True) + '">' +
            '<option value="investigate">Investigate (not a live owner)</option>' +
            '<option value="coordinate-owner">Coordinate current owner</option>' +
            '<option value="park-blocker">Park external blocker</option>' +
            '</select></label>' +
            '<label>Private operator note (local only; max 500 chars)' +
            '<textarea data-recovery-note="' +
            escape(row["project"], quote=True) +
            '" maxlength="500" rows="2" placeholder="Cause, owner input and next recheck"></textarea></label></li>'
        )
    return (
        '<section class="portfolio" id="projectRecoveryDesk">'
        '<h2>Project recovery · issue-independent source observations</h2>'
        '<p>The saved issue sample may contain zero issues for a project '
        'while a bounded queue observation still shows claim pressure. '
        'These are historical human investigation leads, never worker '
        'availability, confirmed failures or permission to retry.</p>'
        '<details><summary>Inspect <span id="recoveryVisibleCount">' +
        str(len(sections)) + '</span> project-level recovery leads (offline)' +
        '</summary>'
        '<ol>' + ''.join(sections) + '</ol></details>'
        '<p>All source windows are incomplete; no claim, CI, merge or '
        'production state was verified live.</p></section>'
    )


def html_document(view: dict, board_sha: str, saved_pr_context=None) -> str:
    records = json.dumps(view, ensure_ascii=True, separators=(",", ":"))
    # The JSON is embedded into an inline script. Escape HTML syntax even if
    # the JSON was formed from malicious public issue titles or operator notes.
    records = records.replace("<", "\\u003c").replace(">", "\\u003e")
    choices = "".join(
        '<option value="' + escape(p, quote=True) + '">' + escape(p) + '</option>'
        for p in view["projects"]
    )
    source_pr_section = issue_pr_connections_html(view, saved_pr_context)
    project_recovery_section = project_operations_html(view)
    return """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'; img-src 'none'">
<title>zSentinel · Product Delivery Workbench</title>
<style>
:root{color-scheme:dark}body{font:15px/1.55 system-ui,sans-serif;background:#0e1725;color:#ecf3ff;margin:auto;max-width:1160px;padding:23px 18px}
h1{font-size:clamp(28px,5vw,46px);letter-spacing:-.03em;line-height:1.15}
h2{font-size:20px}p{color:#c4d0e1}a{color:#9fe6ff;text-underline-offset:3px}
label{display:block;margin:12px 0 6px}input,select,textarea,button{font:inherit}
input[type=search],select,textarea{background:#1d2c41;border:1px solid #6a7e9d;border-radius:9px;padding:10px;color:white}
input[type=search]{width:100%;max-width:420px}textarea{width:100%;box-sizing:border-box;resize:vertical}
button{background:#b8e1ff;color:#082036;padding:10px 16px;border:0;border-radius:8px;cursor:pointer;font-weight:650}
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,a:focus-visible{outline:3px solid #f9db80}
.notice{border-left:4px solid #f4d68c;background:#292d34;padding:12px 18px;border-radius:8px;margin:19px 0}
.filters{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;padding:14px 0}
.filters label{margin:0}.filters select{display:block;min-width:170px}
.portfolio{border:1px solid #445e7d;background:#142238;border-radius:12px;padding:12px 18px;margin:14px 0 20px}
.portfolio dl{display:flex;flex-wrap:wrap;gap:15px;margin:6px 0}.portfolio dt{font-size:12px;color:#b9cce6}.portfolio dd{font-size:19px;font-weight:750;margin:0}
.portfolio ul{columns:2;list-style:none;padding:0;margin:10px 0}.portfolio li{break-inside:avoid;margin:5px 0}
.portfolio small{color:#d5dfed}.brief{border:1px solid #53738e;border-radius:12px;padding:15px;margin:12px 0}
.navtriage{display:flex;flex-wrap:wrap;gap:9px;align-items:center;padding:12px 14px;border:1px solid #53738e;border-radius:12px;background:#142438;margin:10px 0}
.navtriage button{background:#243c56;color:#ecf5ff;border:1px solid #7897b6;padding:7px 12px}
.navtriage button[disabled]{opacity:.5;cursor:not-allowed}
.navtriage small{flex-basis:100%}
.triage{border:1px solid #5c718c;background:#17283a;border-radius:12px;padding:15px;margin:14px 0}
.triage ol{padding-left:23px;margin:8px 0;max-height:360px;overflow:auto}.triage li{padding:5px 0;overflow-wrap:anywhere}
.triage button{background:#243c56;color:#ecf5ff;border:1px solid #7897b6;padding:6px 9px;margin-right:8px}
.triage small{display:inline-block;margin:2px}
.escalations{border:1px solid #677d9a;border-radius:12px;background:#15283a;padding:15px;margin:14px 0}
.escalations ol{padding-left:23px;max-height:360px;overflow:auto}
.escalations li{padding:7px 0;overflow-wrap:anywhere}
.escalations button{padding:5px 9px;margin-right:10px;background:#233c55;color:white;border:1px solid #779db8}
.escalations small{display:block;color:#d6e3f2}
.batch{border:1px solid #5a8197;border-radius:12px;background:#132437;padding:15px;margin:14px 0}
.batch ol{list-style:none;padding:0;margin:8px 0;max-height:300px;overflow:auto}
.batch li{padding:6px 0;border-bottom:1px solid #34485f}
.batch li label{display:flex;align-items:flex-start;gap:8px;margin:0}
.batch .toolbar{margin:10px 0}.batch pre{max-height:240px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}
.batch button[disabled]{opacity:.55;cursor:not-allowed}
.prdesk{border:1px solid #5b86a0;border-radius:12px;padding:16px;background:#12283b;margin:16px 0}
.prdesk ol{padding-left:24px;max-height:440px;overflow:auto}.prdesk li{margin:10px 0;border-bottom:1px solid #405c73;padding:6px 0 10px;overflow-wrap:anywhere}
.prdesk small{display:block}.prdesk pre{max-height:320px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}
.prdesk button[disabled]{opacity:.55;cursor:not-allowed}
.comparison{border:1px solid #486f87;border-radius:12px;padding:15px;margin:16px 0;background:#132237}
.comparison ol{padding-left:25px;max-height:320px;overflow:auto}.comparison li{margin:7px 0;overflow-wrap:anywhere}
.comparison strong{color:#d7f0ff}
.brief pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:330px;overflow:auto;background:#091627;padding:12px}
.stack{display:grid;grid-template-columns:minmax(210px,40%) 1fr;gap:16px}
.list{list-style:none;padding:0;margin:0;max-height:68vh;overflow:auto}
.list li{margin:0 0 8px}.list button{display:block;width:100%;text-align:left;background:#1b2b40;border:1px solid #445e7d;color:#f3f7ff}
.list button[aria-current=true]{border-color:#b8e1ff;background:#28445c}
.issue-id{display:block;font-weight:750}.meta{font-size:12px;color:#a2b7d1}
article{padding:18px;border:1px solid #465e7b;border-radius:14px;background:#1a293d;min-height:300px}
details{margin-top:14px}.code{font:12px ui-monospace,monospace;word-break:break-all}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}
small{color:#c0cede}.action{font-weight:700}
@media(max-width:690px){.stack{grid-template-columns:1fr}.list{max-height:230px}.portfolio ul{columns:1}}
@media print{body{background:white;color:black}.filters,.toolbar,.list{display:none}article{color:black;background:white}.stack{display:block}.notice{color:black;background:white}}
</style>
<header><small>ZCLOUD / zSENTINEL / HUMAN-OPERATED</small>
<h1>Product Delivery Workbench</h1>
<p>Navigate actual P0/P1 product issues, inspect existing PR links and record a human handoff.
Source is a bounded saved zSentinel board, not a live ownership or production feed.</p>
<p class="notice"><strong>No automatic claim, merge, retry, deploy or privileged runner action.</strong>
An apparently free issue is never proof that its ownership is clear. Check live
Notion/queue claims and the exact-head GitHub gates separately before acting.</p>
<p id="info" aria-live="polite"></p>
<p id="unsavedStatus" role="status" aria-live="polite">No locally edited handoffs to export.</p></header>
""" + source_pr_section + project_recovery_section + """
<div class="filters">
<label>Search product issues<input id="search" type="search" placeholder="Issue, project or next step"></label>
<label>Priority<select id="priority"><option value="">All priorities</option>
<option>P0</option><option>P1</option><option>P2</option><option>Unspecified</option></select></label>
<label>Project<select id="project"><option value="">All projects</option>""" + choices + """</select></label>
<label>Human disposition<select id="disposition">
<option value="">All decisions</option>
<option value="investigate">Investigate / not yet reviewed</option>
<option value="integrate-existing-pr">Inspect existing PR</option>
<option value="coordinate-owner">Coordinate owner</option>
<option value="park-blocker">Parked blocker</option>
<option value="ready-for-new-product-work">Candidate only · verify live claims</option>
</select></label>
<label>Follow-up status<select id="followupStatus">
<option value="">All manual states</option>
<option value="overdue">Past manual review date</option>
<option value="incomplete">Missing contact or date</option>
<option value="today">Due today UTC</option>
<option value="scheduled">Scheduled later</option>
<option value="local-complete">Locally closed follow-up (NOT issue resolved)</option>
</select></label>
<label>Change since earlier board<select id="change" disabled>
<option value="">All captured issues</option>
<option value="new-or-changed">New or changed evidence</option>
<option value="new-observation">Newly observed</option>
<option value="changed-evidence">Changed observed evidence</option>
<option value="still-observed">Still observed (not resolved)</option>
</select></label>
</div>
<nav class="navtriage" aria-label="Browse observed issue cards without taking ownership">
<button id="navPrev" type="button">← Previous issue</button>
<button id="navNext" type="button">Next issue →</button>
<button id="navUrgent" type="button">Next open P0/P1 follow-up →</button>
<span id="navPosition" role="status" aria-live="polite">No issue selected</span>
<small>Keyboard: J / K for next / previous when not typing. Only the
currently filtered, saved source is navigated; these are not assignments,
claim permissions or external actions. Human notes remain local.</small>
</nav>
<section class="portfolio" aria-label="Snapshot portfolio triage">
<h2>Observed product workload</h2>
<p id="portfolioFreshness">Source freshness is not live-worker availability.</p>
<dl id="portfolioStats"></dl>
<ul id="projectCounts"></ul>
<small>Counts describe only this saved, bounded source snapshot. No checked claim, available worker or release-ready status is inferred.</small>
</section>
<section class="comparison" aria-label="Compare with a previous offline board">
<h2>Compare two saved observations</h2>
<p>Load an earlier private JSON delivery-board snapshot to see new, still-observed
and no-longer-visible issues. A disappearance is <strong>not evidence of a fix</strong>:
source coverage, permissions or filters may have changed.</p>
<label>Earlier source snapshot <input id="baseline" type="file" accept=".json,application/json"></label>
<button id="clearBaseline" type="button" disabled>Clear comparison</button>
<p id="comparisonMeta" aria-live="polite">Only this captured board is loaded; no baseline or live comparison.</p>
<p id="comparisonCoverage" aria-live="polite">Coverage: current bounded source only.</p>
<dl id="comparisonStats" class="portfolio"></dl><ol id="comparisonRows"></ol>
<div class="brief" aria-label="Reviewed snapshot difference export">
<h3>Portable two-snapshot change ledger</h3>
<p>Export all bounded changes between the two exact saved boards, including
items no longer visible. Disappearance is NOT proof of issue resolution.
No private human notes or live claim states are included.</p>
<button type="button" id="previewDeltaCsv" disabled>Preview full change CSV</button>
<div id="deltaCsvPanel" hidden><pre id="deltaCsvContent"></pre>
<button type="button" id="downloadDeltaCsv" disabled>Download reviewed two-snapshot CSV</button></div>
</div>
<div class="brief">
<h3>Reviewed human follow-up carry-forward</h3>
<p>After loading the earlier source board, select its exact private handoff JSON.
Only matching canonical human-blocker issues can be carried to the current board.
Existing current annotations are never overwritten. Preview first; explicitly
confirm the transfer. Transferred labels/dates are stale human notes, NOT current ownership.</p>
<label>Earlier private human handoff (.json)
<input id="carryFile" type="file" accept=".json,application/json"></label>
<p id="carrySummary" aria-live="polite">Load an earlier board first; nothing imported.</p>
<ol id="carryRows"></ol>
<button type="button" id="carryApply" disabled>Confirm carry-forward of reviewed human follow-ups</button>
</div>
</section>
<section class="escalations" aria-label="Human follow-up schedule">
<h2>Human follow-up · private, manual only</h2>
<p>Track human-only blockers with a local contact label and UTC review date. Overdue means
the operator-selected date passed, not that an owner accepted a task or a notification was delivered.</p>
<p id="followupSummary" aria-live="polite"></p><ol id="followupRows"></ol>
</section>
<section class="batch" aria-label="Bulk human follow-up decisions">
<h2>Bulk human follow-up review · offline only</h2>
<p>Choose existing human follow-ups. Preview changes before applying to private annotations.
Selection does not claim, close or reassign any original GitHub issue.</p>
<div class="toolbar">
<button id="batchSelectOpen" type="button">Select currently open human follow-ups</button>
<button id="batchClear" type="button">Clear selection</button></div>
<p id="batchCount" aria-live="polite">0 selected.</p><ol id="batchRows"></ol>
<label>Local change only<select id="batchAction">
<option value="local-complete">Mark human follow-ups locally complete</option>
<option value="open">Reopen human follow-ups</option>
<option value="review-date">Change UTC human review date (open cases only)</option>
</select></label>
<label>Review date for date changes only <input type="date" id="batchDate" min="2020-01-01" max="2099-12-31"></label>
<button id="batchPreview" type="button">Preview exact private changes</button>
<div id="batchPreviewPanel" hidden><pre id="batchPreviewText"></pre>
<button id="batchApply" type="button" disabled>Confirm these local follow-up changes</button></div>
<button id="batchUndo" type="button" disabled>Undo last confirmed local bulk action</button>
<p><small>Undo is refused if any affected private record changed after bulk apply.</small></p>
<p><small>Human labels, notes, real claims, CI/review gates and issue states remain unchanged.
Export private JSON separately to preserve these local changes.</small></p>
</section>
<section class="triage" aria-label="Human-only priority and blocker queue">
<h2>Next-action queue · observed evidence only</h2>
<p>Prioritizes the currently filtered P0/P1 issues and the work you explicitly parked.
No live owner/claim status is fetched or inferred. Recheck GitHub, Notion and release gates before touching code.</p>
<p id="triageCount" aria-live="polite"></p><ol id="triageQueue"></ol>
</section>
<section class="prdesk" aria-label="Merge-first observed PR desk">
<h2>Merge first · grouped existing PR references</h2>
<p>One original PR link per current filtered issue group, not one new branch per issue.
This is a saved partial snapshot: linked PRs may have changed, closed or merged.
Open the original PR, inspect the current head, live claims, required checks,
independent reviews and branch/release authority before touching code or merging.</p>
<div class="brief" aria-label="Saved GitHub PR gate observation">
<h3>Import GitHub PR-gate snapshot · optional</h3>
<p>Attach saved JSON from the zSentinel current-head PR gate desk or integrated issue cockpit.
Imported receipts are NOT live GitHub evidence, required branch rules, owner consent or merge clearance.</p>
<label>Saved PR gate JSON (maximum 1 MiB)
<input type="file" id="prGateImport" accept=".json,application/json"></label>
<button id="prGateClear" type="button" disabled>Clear imported gate snapshot</button>
<details><summary>Combine original reviewed manifest and saved PR batches here (offline)</summary>
<p>Choose the exact previously exported review-only JSON manifest plus up to 75
saved four-PR gate JSON files. The browser checks the original review SHA,
board, PR order, batch boundaries and timestamps before replacing the
current local observation. Missing batches remain UNKNOWN. No internet calls.</p>
<label>Original saved review manifest (maximum 1 MiB)
<input type="file" id="prGateReviewFile" accept=".json,application/json"></label>
<label>Saved current-head PR gate batches (up to 75, 8 MiB combined)
<input type="file" id="prGateBatchFiles" multiple accept=".json,application/json"></label>
<button id="prGateBatchApply" type="button">Combine saved batches in this browser</button>
<button id="prGateBatchPreview" type="button" disabled>Preview portable, private combined JSON</button>
<div id="prGateBatchPreviewPanel" hidden>
<pre id="prGateBatchPreviewText"></pre>
<button id="prGateBatchDownload" type="button" disabled>Download exactly this reviewed combined JSON</button>
</div>
</details>
<p id="prGateStatus" aria-live="polite">No imported source. All current PR gates unknown.</p>
<details><summary>Compare an older saved PR-gate snapshot with this imported source (offline)</summary>
<p>Observations only, NOT live CI or merge authority. The older file must
belong to this exact saved board and have an earlier UTC observation time.</p>
<label>Older saved PR-gate JSON (maximum 1 MiB)
<input type="file" id="prGateOlderFile" accept=".json,application/json"></label>
<p id="prGateDeltaSummary" aria-live="polite">Load a current PR-gate snapshot first.</p>
<ol id="prGateDeltaRows"></ol>
<button type="button" id="prGateDeltaPreview" disabled>Preview historical PR gate changes</button>
<div id="prGateDeltaPanel" hidden>
<pre id="prGateDeltaText"></pre>
<button id="prGateDeltaDownload" type="button" disabled>Download reviewed historical changes (.md)</button>
</div>
</details>
<label>Filter saved PR references by observed gate state
<select id="prGateFocus">
<option value="">All referenced PRs</option>
<option value="failed">Observed failing checks</option>
<option value="pending">Observed pending checks</option>
<option value="review">Review required / changes requested</option>
<option value="unverified">No usable current-head source</option>
<option value="observed-no-authority">Observed clear checks, NOT merge authority</option>
</select></label>
</div>
<p id="prDeskSummary" aria-live="polite"></p>
<h3>PR links with no saved gate observation · missing source batches</h3>
<p>Only original review-manifest PR references. These links do not prove a PR
is currently open or free of active ownership. Re-collect the indicated
batch using the original reviewed manifest before interpreting current CI.</p>
<ol id="missingGateRows"></ol>
<ol id="prDeskRows"></ol>
<h3>Productissues without captured PR references · human triage first</h3>
<p>Missing from this saved PR window does not mean no PR exists in live GitHub.
Verify current claims and recent branches before opening new implementation work.</p>
<ol id="unlinkedIssueRows"></ol>
<button type="button" id="previewPrDesk">Preview filtered merge-first handoff</button>
<div id="prDeskPanel" hidden><pre id="prDeskContent"></pre>
<button type="button" id="downloadPrDesk" disabled>Download exactly this reviewed PR handoff (.md)</button></div>
<h3>Portable review-only source manifest</h3>
<p>Structured JSON for human-reviewed handoff to the next operator. Includes only
captured canonical public issue/PR references and source-coverage flags.
No personal notes, no credentials, no permission to claim, merge or deploy.</p>
<button type="button" id="previewPrManifest">Preview exact review manifest (.json)</button>
<div id="prManifestPanel" hidden><pre id="prManifestContent"></pre>
<button type="button" id="downloadPrManifest" disabled>Download exactly this review-only JSON</button></div>
</section>
<div class="stack">
<nav aria-label="Observed product issues"><ol class="list" id="issues"></ol></nav>
<article id="detail" aria-label="Selected product issue">
<h2>Choose a product issue</h2><p>No issue has been assigned to a worker by this page.</p>
</article>
</div>
<div class="toolbar">
<button id="export" type="button">Download private human handoff</button>
<label>Reopen own handoff<input id="restore" type="file" accept=".json,application/json"></label>
<button id="print" type="button">Print selected issue</button>
</div>
<section class="brief" aria-label="Private project handoff brief">
<h2>Source-bound, human-only project briefing</h2>
<p>Review all visible cards, owner gaps and your human dispositions before exporting
a private Markdown briefing. It includes any private notes you entered: check the
recipient and download destination. It cannot claim, merge, release or contact a project.</p>
<button type="button" id="previewBrief">Preview current filtered briefing</button>
<div id="briefPanel" hidden>
<pre id="briefContent"></pre>
<button type="button" id="downloadBrief" disabled>Download exactly this private briefing (.md)</button>
</div>
</section>
<section class="brief" aria-label="Offline CSV triage export">
<h2>Offline spreadsheet triage · reviewed CSV</h2>
<p>Export exactly the currently filtered saved issues, including observed PR links,
evidence gaps and local human dispositions. Every row says claim, merge and deploy
remain unauthorized. This is a captured snapshot, not a live issue tracker.</p>
<label><input type="checkbox" id="csvIncludePrivate">
Explicitly include private operator contact labels and notes (off by default)</label>
<p><small>Spreadsheet and cloud-sync applications may copy this CSV to another account.
Cells are quoted and formula-prefixed values are escaped. Verify your destination.</small></p>
<button type="button" id="previewCsv">Preview filtered offline CSV</button>
<div id="csvPanel" hidden><pre id="csvContent"></pre>
<button type="button" id="downloadCsv" disabled>Download exactly this reviewed CSV</button></div>
</section>
<section class="brief" aria-label="Manual calendar handoff">
<h2>Optional private calendar follow-up export</h2>
<p>Only human-marked issues with a contact label and UTC review date qualify.
Preview first, then optionally download a local .ics file for manual calendar import.
All-day dates contain no alarm. Importing is your decision; external calendar defaults may add notifications.
No calendar, watch or external service is contacted here.</p>
<label><input type="checkbox" id="calendarIncludeContact">
Deliberately include private human contact labels in downloaded calendar events (unchecked by default).</label>
<p><small>Calendars can sync to other devices/accounts. Leave unchecked unless a contact label is needed and the import destination is trusted.</small></p>
<button type="button" id="previewCalendar">Preview reviewed calendar events</button>
<div id="calendarPanel" hidden><pre id="calendarContent"></pre>
<button type="button" id="downloadCalendar" disabled>Download exactly these all-day events (.ics)</button></div>
</section>
<p id="status" role="status" aria-live="polite"></p>
<footer><small>Offline. No localStorage, analytics, network requests, GitHub writes, or quiet persistence.
Downloaded notes belong to the operator and are not authorization to act on queues.</small></footer>
<script>
const board = """ + records + """;
const boardSha = """ + json.dumps(board_sha) + """;
const baselineSchema = 'zsentinel-product-delivery-triage-v1';
const issuePattern = /^[A-Za-z0-9_.-]+\\/[A-Za-z0-9_.-]+#[1-9][0-9]{0,8}$/;
let baseline=null,carryPreview=null,prGateImport=null,prGatePrevious=null;
let gateDeltaPreview=null;
const issueList=document.getElementById('issues'),detail=document.getElementById('detail');
const status=document.getElementById('status');
const entries=new Map(board.cards.map(c=>[c.issue,c]));
const permitted=new Set(['investigate','integrate-existing-pr','coordinate-owner','park-blocker','ready-for-new-product-work']);
const manualGateDecisions=new Set(['coordinate-owner','park-blocker']);
const utcToday=()=>new Date().toISOString().slice(0,10);
function contactLabel(value){
 return Array.from(String(value||'')).filter(ch=>{
  const point=ch.codePointAt(0);
  return point>=32&&point!==127&&ch!=='<'&&ch!=='>';
 }).join('').trim().slice(0,80);
}
function followupStateMatches(info,kind){
 if(!kind)return true;
 if(!manualGateDecisions.has(info?.decision))return false;
 if(kind==='local-complete')return info.followup_status==='local-complete';
 if(info.followup_status==='local-complete')return false;
 const complete=Boolean(info?.owner?.trim()&&info?.due);
 if(kind==='incomplete')return !complete;
 if(!complete)return false;
 const now=utcToday();
 return kind==='overdue'?info.due<now:
  kind==='today'?info.due===now:
  kind==='scheduled'?info.due>now:false;
}
function validFollowupDate(value){
 if(value==='')return true;
 return typeof value==='string' && /^20[2-9][0-9]-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])$/.test(value) &&
  !Number.isNaN(Date.parse(value+'T00:00:00Z')) &&
  new Date(value+'T00:00:00Z').toISOString().slice(0,10)===value;
}
function validLocalChangeTime(value){
 if(value==='')return true;
 if(typeof value!=='string'||value.length!==24||
    !/^20[2-9][0-9]-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]\\.[0-9]{3}Z$/.test(value))
   return false;
 const date=Date.parse(value);
 return Number.isFinite(date)&&new Date(date).toISOString()===value;
}
function dueState(info){
 if(info?.followup_status==='local-complete')return 'local follow-up closed (issue status UNVERIFIED)';
 if(!info?.owner?.trim()||!info?.due)return 'needs human contact/date';
 return info.due<utcToday()?'overdue (manual review required)':info.due===utcToday()?
  'due today UTC':'scheduled by operator';
}
let selected=null;const handoffs=new Map();
const recoveryHandoffs=new Map();
const recoveryDecisions=new Set(['investigate','coordinate-owner','park-blocker']);
const batchSelection=new Set();let batchPreview=null,batchUndo=null;
function clearBatchUndo(){
 batchUndo=null;document.getElementById('batchUndo').disabled=true;
}
function invalidateBatch(){
 batchPreview=null;
 document.getElementById('batchPreviewPanel').hidden=true;
 document.getElementById('batchPreviewText').textContent='';
 document.getElementById('batchApply').disabled=true;
}
let unsavedLocal=false;
function setUnsaved(value){
 unsavedLocal=Boolean(value);
 document.getElementById('unsavedStatus').textContent=unsavedLocal?
  'Unsaved private follow-up edits. Use Download private human handoff (.json) before closing this tab.':
  'Private follow-up annotations match the last locally downloaded or restored handoff (if any).';
}
window.addEventListener('beforeunload',event=>{
 if(!unsavedLocal)return;
 event.preventDefault();event.returnValue='';
});
let viewedBrief=null,viewedCsv=null,viewedDeltaCsv=null,viewedPrDesk=null,viewedPrManifest=null;
let viewedCalendar=null,viewedCalendarStamp=null;
function invalidateBrief(){
 viewedPrManifest=null;
 document.getElementById('prManifestPanel').hidden=true;
 document.getElementById('prManifestContent').textContent='';
 document.getElementById('downloadPrManifest').disabled=true;
 viewedPrDesk=null;
 document.getElementById('prDeskPanel').hidden=true;
 document.getElementById('prDeskContent').textContent='';
 document.getElementById('downloadPrDesk').disabled=true;
 viewedDeltaCsv=null;
 document.getElementById('deltaCsvPanel').hidden=true;
 document.getElementById('deltaCsvContent').textContent='';
 document.getElementById('downloadDeltaCsv').disabled=true;
 viewedCsv=null;
 document.getElementById('csvPanel').hidden=true;
 document.getElementById('csvContent').textContent='';
 document.getElementById('downloadCsv').disabled=true;
 viewedCalendar=null;viewedCalendarStamp=null;
 document.getElementById('calendarPanel').hidden=true;
 document.getElementById('calendarContent').textContent='';
 document.getElementById('downloadCalendar').disabled=true;
 viewedBrief=null;
 document.getElementById('briefPanel').hidden=true;
 document.getElementById('briefContent').textContent='';
 document.getElementById('downloadBrief').disabled=true;
}
function elem(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
function link(text,url){const a=elem('a',text);a.href=url;a.rel='noopener noreferrer';a.target='_blank';return a}
function saveSelection(){
 if(!selected)return;
 const decision=document.getElementById('decision'),note=document.getElementById('note');
 const owner=document.getElementById('followupOwner'),due=document.getElementById('followupDue');
 if(decision&&note&&owner&&due){
  const before=handoffs.get(selected);
  const after={
   decision:decision.value,note:note.value.slice(0,1000),
   owner:contactLabel(owner.value),due:validFollowupDate(due.value)?due.value:'',
   followup_status:manualGateDecisions.has(decision.value)?
    (before?.followup_status||'open'):'open',
   followup_changed_at_utc:before?.followup_changed_at_utc||'',
   carried_from:before?.carried_from||''
  };
  const blank=!after.note&&!after.owner&&!after.due&&
   after.decision==='investigate'&&after.followup_status==='open'&&!after.carried_from;
  if(!before&&blank)return;
  if(JSON.stringify(before)!==JSON.stringify(after)){
   handoffs.set(selected,after);setUnsaved(true);
  }
 }
}
function filtered(){
 const q=document.getElementById('search').value.trim().toLowerCase();
 const p=document.getElementById('priority').value;
 const project=document.getElementById('project').value;
 const decision=document.getElementById('disposition').value;
 const change=document.getElementById('change').value;
 const followupStatus=document.getElementById('followupStatus').value;
 const kinds=change&&baseline?new Map(changesSinceBaseline().map(x=>[x.issue,x.kind])):null;
 return board.cards.filter(c=>(!p||p===c.priority)&&(!project||project===c.project)&&
   (!decision||decision===(handoffs.get(c.issue)?.decision||'investigate'))&&
   followupStateMatches(handoffs.get(c.issue),followupStatus)&&
   (!change||(kinds&&(change==='new-or-changed'?
     ['new-observation','changed-evidence'].includes(kinds.get(c.issue)):
     kinds.get(c.issue)===change)))&&
   (!q||(c.issue+' '+c.title+' '+c.project+' '+c.recommended_next_step).toLowerCase().includes(q)));
}


const knownPrLinks=new Set(board.cards.flatMap(c=>c.open_pr_candidates));
function gateTime(value){
 if(typeof value!=='string'||value.length<20||value.length>40||
 !/^20[2-9][0-9]-[01][0-9]-[0-3][0-9]T/.test(value)||
 !/(Z|\\+00:00)$/.test(value))throw Error('Invalid UTC observation time');
 const time=Date.parse(value);
 if(!Number.isFinite(time)||time>Date.now()+300000)throw Error('Future or invalid gate time');
 return time;
}
function parseGateRow(row){
 if(!row||typeof row!=='object'||Array.isArray(row)||
    typeof row.url!=='string'||!/^https:\\/\\/github\\.com\\/Zennay\\/[A-Za-z0-9_.-]+\\/pull\\/[1-9][0-9]{0,8}$/.test(row.url)||
    row.merge_authorized!==false||row.deployment_authorized!==false||
    row.read_only!==true)throw Error('Not a read-only canonical PR receipt');
 if(row.status==='github_source_unavailable_or_head_changed')
  return {url:row.url,available:false};
 if(row.status!=='observed_current_head_partial_gate_evidence'||
    typeof row.head_sha_observed!=='string'||!/^[a-f0-9]{40}$/.test(row.head_sha_observed)||
    row.exact_head_stable_on_second_read!==true||
    row.required_branch_checks_and_rules_verified!==false||
    row.required_review_policy_verified!==false||
    row.mergable_by_automation!==false)
  throw Error('Untrusted current-head observation');
 const fields=['observed_checks_pending','observed_checks_not_successful',
   'observed_independent_exact_head_approvals','observed_exact_head_changes_requested',
   'legacy_status_pending_observed','legacy_status_failure_or_error_observed',
   'observed_checks_completed','legacy_status_contexts_observed'];
 if(fields.some(k=>!Number.isInteger(row[k])||row[k]<0||row[k]>100)||
    typeof row.legacy_status_source_may_be_truncated!=='boolean'||
    typeof row.check_runs_page_complete!=='boolean'||
    typeof row.review_page_may_be_truncated!=='boolean'||
    typeof row.human_next_gate_step!=='string'||
    !/^[a-z_]{5,80}$/.test(row.human_next_gate_step))
  throw Error('Invalid bounded check/review counts');
 return {url:row.url,available:true,head:row.head_sha_observed,
   checks_pending:row.observed_checks_pending,
   checks_failed:row.observed_checks_not_successful,
   legacy_pending:row.legacy_status_pending_observed,
   legacy_failed:row.legacy_status_failure_or_error_observed,
   approvals:row.observed_independent_exact_head_approvals,
   changes_requested:row.observed_exact_head_changes_requested,
   observed_checks:row.observed_checks_completed,
   legacy_contexts:row.legacy_status_contexts_observed,
   incomplete:row.legacy_status_source_may_be_truncated||
     !row.check_runs_page_complete||row.review_page_may_be_truncated,
   next:row.human_next_gate_step};
}

// Combine an original reviewed PR manifest and independent historical gate batches
// entirely in this offline browser. Source membership is verified against the
// original captured issue board; no batch, credential or private note is sent out.
async function combineSavedGateFiles(manifestFile,batchFiles){
 if(!globalThis.crypto?.subtle||!manifestFile||!batchFiles.length||
    batchFiles.length>75)throw Error('Missing bounded local files or SHA-256');
 const files=[manifestFile,...batchFiles],totalBytes=files.reduce((v,f)=>v+f.size,0);
 if(files.some(f=>f.size<2||f.size>1048576)||totalBytes>8388608)
  throw Error('Offline source exceeds strict file bounds');
 const readJSON=async f=>{
  const raw=await f.arrayBuffer();
  return {raw,data:JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw))};
 };
 const {raw,data:review}=await readJSON(manifestFile);
 const reviewSha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',raw)),
  b=>b.toString(16).padStart(2,'0')).join('');
 if(!review||Array.isArray(review)||review.schema!=='zsentinel-offline-merge-first-review-v1'||
    review.evidence_scope!=='bounded_saved_snapshot_not_live'||
    review.source_board_sha256!==boardSha||
    ['claim_authorized','merge_authorized','deploy_authorized','dispatch_authorized']
      .some(k=>review[k]!==false)||
    !Array.isArray(review.pr_references)||review.pr_references.length<1||
    review.pr_references.length>300)
  throw Error('Source is not the original bounded, non-authorizing review manifest');
 const weights={P0:0,P1:1,P2:2,Unspecified:3},seenRefs=new Set();
 const selected=review.pr_references.map(ref=>{
  if(!ref||Array.isArray(ref)||typeof ref.url!=='string'||
     !RegExp('^https://github[.]com/Zennay/[A-Za-z0-9_.-]+/pull/[1-9][0-9]{0,8}$').test(ref.url)||
     !knownPrLinks.has(ref.url)||seenRefs.has(ref.url)||
     !Object.hasOwn(weights,ref.highest_linked_priority)||
     ref.merge_authorized!==false||
     !Array.isArray(ref.observed_issue_refs)||!ref.observed_issue_refs.length||
     ref.observed_issue_refs.length>60)
   throw Error('Foreign, duplicated or malformed reviewed PR');
  seenRefs.add(ref.url);
  const path=ref.url.split('/'),repository=path.slice(3,5).join('/');
  const seenIssues=new Set();
  for(const issue of ref.observed_issue_refs){
   const saved=entries.get(issue?.issue);
   if(!saved||saved.url!==issue.url||seenIssues.has(issue.issue)||
      !saved.open_pr_candidates.includes(ref.url)||
      issue.issue.split('#')[0]!==repository)
    throw Error('Original review links a PR to an unrelated issue');
   seenIssues.add(issue.issue);
  }
  return {url:ref.url,rank:weights[ref.highest_linked_priority],
   repo:path[4],number:Number(path[6]),linked_issues:ref.observed_issue_refs};
 }).sort((a,b)=>a.rank-b.rank||(a.repo<b.repo?-1:a.repo>b.repo?1:0)||a.number-b.number);
 const total=Math.ceil(selected.length/4),observed=new Map(),savedRows=new Map();
 let earliest=Infinity,latest=-Infinity;
 for(const file of batchFiles){
  const {data:receipt}=await readJSON(file);
  const idx=receipt?.source_review_batch_index;
  if(!receipt||Array.isArray(receipt)||
     receipt.schema!=='zsentinel-current-pr-gate-observation-v1'||
     receipt.merge_authorized!==false||receipt.deployment_authorized!==false||
     receipt.writes_performed!==false||
     receipt.required_branch_protection_verified!==false||
     receipt.current_review_and_check_samples_only!==true||
     receipt.source_review_manifest_sha256!==reviewSha||
     receipt.source_review_board_sha256!==boardSha||
     receipt.source_review_pr_count!==selected.length||
     receipt.source_review_batches_total!==total||
     receipt.review_only_top_four_observed!==(selected.length>4)||
     !Number.isInteger(idx)||idx<1||idx>total||observed.has(idx))
   throw Error('Gate batch belongs to another review or duplicates an observed batch');
  const expected=selected.slice((idx-1)*4,idx*4);
  if(receipt.source_review_batch_first_pr!==(idx-1)*4+1||
     receipt.source_review_batch_count!==expected.length||
     !Array.isArray(receipt.items)||receipt.items.length!==expected.length)
   throw Error('Gate batch has missing or reordered original source rows');
  const when=gateTime(receipt.observed_at_utc);
  earliest=Math.min(earliest,when);latest=Math.max(latest,when);
  if(latest-earliest>600000)throw Error('Saved batches exceed common 10-minute window');
  const normalized=new Map();
  for(let i=0;i<expected.length;i++){
   const row=receipt.items[i];
   if(row?.url!==expected[i].url||
      row.required_branch_checks_and_rules_verified!==false)
    throw Error('Saved PR order or branch gate scope mismatches original review');
   normalized.set(row.url,parseGateRow(row));
   // Only canonical check/approval observations belong in a portable export.
   // Unknown input metadata must never become an exported credential field.
   const fields=['url','status','head_sha_observed',
    'exact_head_stable_on_second_read','observed_checks_pending',
    'observed_checks_not_successful','observed_independent_exact_head_approvals',
    'observed_exact_head_changes_requested','legacy_status_pending_observed',
    'legacy_status_failure_or_error_observed','observed_checks_completed',
    'legacy_status_contexts_observed','legacy_status_source_may_be_truncated',
    'check_runs_page_complete','review_page_may_be_truncated',
    'human_next_gate_step','required_branch_checks_and_rules_verified',
    'required_review_policy_verified','mergable_by_automation',
    'merge_authorized','deployment_authorized','read_only'];
   savedRows.set(row.url,Object.fromEntries(fields.filter(k=>
    Object.hasOwn(row,k)).map(k=>[k,row[k]])));
  }
  observed.set(idx,normalized);
 }
 const missing=[],missingPrs=[],items=new Map();
 for(let batch=1;batch<=total;batch++){
  if(!observed.has(batch)){
   missing.push(batch);
   missingPrs.push({batch_index:batch,
    unobserved_pr_urls:selected.slice((batch-1)*4,batch*4).map(x=>x.url)});
  }else for(const [url,item] of observed.get(batch))items.set(url,item);
 }
 // Produce the SAME browser-import schema as the offline Python bridge, so
 // one deliberately downloaded file can be reopened without all raw batches.
 const issueMap=new Map();
 for(const ref of selected){
  if(!savedRows.has(ref.url))continue;
  for(const source of ref.linked_issues){
   let card=issueMap.get(source.issue);
   if(!card){
    if(issueMap.size>=60)throw Error('More than sixty source issue cards');
    card={issue:source.issue,url:source.url,existing_prs:[]};
    issueMap.set(source.issue,card);
   }
   if(card.url!==source.url||card.existing_prs.length>=5)
    throw Error('Issue PR source associations exceed original board bounds');
   card.existing_prs.push({url:ref.url,
    live_exact_head_review_checks_observation:savedRows.get(ref.url)});
  }
 }
 const timestamp=earliest,time=new Date(earliest).toISOString();
 const bridgeCoverage={manifest_sha256:reviewSha,batches:observed.size,
  expected_batches:total,missing_indexes:missing,
  candidate_count:selected.length,expected_observed_prs:items.size,
  missing_prs:missingPrs,complete:missing.length===0,
  earliest_observed_at_utc:time,latest_observed_at_utc:new Date(latest).toISOString(),
  complete_receipts_not_required_checks:missing.length===0};
 const portable={
  schema:'zsentinel-integrated-issue-pr-scope-v1',
  generated_at_utc:time,
  earliest_observed_at_utc:time,
  latest_observed_at_utc:new Date(latest).toISOString(),
  issues:[...issueMap.values()],
  original_review_manifest_sha256:reviewSha,
  original_board_sha256:boardSha,
  observed_batch_count:observed.size,
  expected_batch_count:total,
  review_candidate_pr_count:selected.length,
  missing_batch_count:missing.length,
  missing_batch_indexes:missing,
  missing_batch_prs:missingPrs,
  all_batches_observed_not_all_checks_required_verified:missing.length===0,
  saved_receipts_not_required_checks_verified:true,
  claim_authorized:false,merge_authorized:false,deploy_authorized:false,
  may_take_ownership:false,writes_performed:false
 };
 const portableText=JSON.stringify(portable,null,2)+'\\n';
 if(new TextEncoder().encode(portableText).length>1048576)
  throw Error('Combined private browser export exceeds one MiB');
 // Pass the portable file back through the existing single-file importer.
 // This makes portable output match the same source and ownership constraints.
 const reimport=acceptGateSource(portable);
 if(reimport.items.size!==items.size)
  throw Error('Portable snapshot lost original saved PR observations');
 const portableHash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',
  new TextEncoder().encode(portableText))),
  b=>b.toString(16).padStart(2,'0')).join('');
 return {...reimport,sha256:portableHash,portableText};

}
function acceptGateSource(data){
 if(!data||typeof data!=='object'||Array.isArray(data)||
    data.merge_authorized!==false||data.writes_performed!==false)
  throw Error('Source attempts privileged action');
 let rows,time,reviewContext=null,bridgeCoverage=null;
 if(data.schema==='zsentinel-current-pr-gate-observation-v1'){
  if(data.source_review_manifest_sha256!==undefined){
   const hash=data.source_review_manifest_sha256,origin=data.source_review_board_sha256;
   if(typeof hash!=='string'||!/^[a-f0-9]{64}$/.test(hash)||
      origin!==boardSha||!Number.isInteger(data.source_review_pr_count)||
      data.source_review_pr_count<1||data.source_review_pr_count>300||
      typeof data.review_only_top_four_observed!=='boolean'||
      data.review_only_top_four_observed!==(data.source_review_pr_count>4))
    throw Error('Imported gate proof belongs to another saved board');
   const total=Math.ceil(data.source_review_pr_count/4);
   const hasBatch=data.source_review_batch_index!==undefined;
   if(hasBatch){
    const idx=data.source_review_batch_index,
      first=data.source_review_batch_first_pr,
      count=data.source_review_batch_count;
    if(!Number.isInteger(idx)||idx<1||idx>total||
       data.source_review_batches_total!==total||
       first!==(idx-1)*4+1||
       count!==Math.min(4,data.source_review_pr_count-first+1))
     throw Error('Invalid source receipt batch boundaries');
   }else if(data.source_review_batches_total!==undefined||
     data.source_review_batch_first_pr!==undefined||
     data.source_review_batch_count!==undefined)
    throw Error('Partial source batch provenance');
   reviewContext={manifest_sha256:hash,original_board_sha256:origin,
     candidate_count:data.source_review_pr_count,
     truncated:data.review_only_top_four_observed,
     batch_index:hasBatch?data.source_review_batch_index:1,
     batches_total:total};
  }
  else if(data.source_review_board_sha256!==undefined||
    data.review_only_top_four_observed!==undefined||
    data.source_review_pr_count!==undefined)
   throw Error('Incomplete imported review provenance');
  if(data.deployment_authorized!==false||
     data.required_branch_protection_verified!==false||
     data.current_review_and_check_samples_only!==true||
     !Array.isArray(data.items)||data.items.length>4)throw Error('Invalid PR gate source');
  rows=data.items;time=data.observed_at_utc;
 }else if(data.schema==='zsentinel-integrated-issue-pr-scope-v1'){
  if(data.deploy_authorized!==false||data.may_take_ownership!==false||
     !Array.isArray(data.issues)||data.issues.length>60)
   throw Error('Invalid issue cockpit source');
  rows=[];time=data.generated_at_utc;
  // An all-batch bridge is an offline historical source, not merge authority.
  // Verify provenance separately; legacy single-shot imports stay supported.
  if(data.original_review_manifest_sha256!==undefined){
   const hash=data.original_review_manifest_sha256;
   const versioned=data.expected_batch_count!==undefined;
   if(typeof hash!=='string'||!/^[a-f0-9]{64}$/.test(hash)||
      data.original_board_sha256!==boardSha||
      !Number.isInteger(data.observed_batch_count)||
      data.observed_batch_count<1||data.observed_batch_count>75||
      data.earliest_observed_at_utc!==time||
      typeof data.latest_observed_at_utc!=='string')
    throw Error('Invalid source-bound saved-batch identity');
   let expected=data.observed_batch_count,missing=[];
   if(versioned){
    expected=data.expected_batch_count;missing=data.missing_batch_indexes;
    const candidateCount=data.review_candidate_pr_count;
    if(!Number.isInteger(expected)||expected<1||expected>75||
       !Number.isInteger(candidateCount)||candidateCount<1||candidateCount>300||
       expected!==Math.ceil(candidateCount/4)||
       !Number.isInteger(data.missing_batch_count)||
       data.missing_batch_count<0||
       !Array.isArray(missing)||missing.length!==data.missing_batch_count||
       data.observed_batch_count+missing.length!==expected||
       missing.some((n,i)=>!Number.isInteger(n)||n<1||n>expected||
        (i>0&&n<=missing[i-1]))||
       data.saved_receipts_not_required_checks_verified!==true||
       data.all_batches_observed_not_all_checks_required_verified!==(missing.length===0))
     throw Error('Missing or contradictory saved batch coverage');
    const missingRows=data.missing_batch_prs,seenUnobserved=new Set();
    if(!Array.isArray(missingRows)||missingRows.length!==missing.length)
     throw Error('Missing unobserved PR source breakdown');
    for(let j=0;j<missingRows.length;j++){
     const group=missingRows[j],index=missing[j];
     const count=Math.min(4,candidateCount-(index-1)*4);
     if(!group||group.batch_index!==index||
        !Array.isArray(group.unobserved_pr_urls)||
        group.unobserved_pr_urls.length!==count)
      throw Error('Missing batch PR identities or boundaries');
     for(const url of group.unobserved_pr_urls){
      if(typeof url!=='string'||!knownPrLinks.has(url)||seenUnobserved.has(url))
       throw Error('Unknown, repeated or foreign missing-batch PR');
      seenUnobserved.add(url);
     }
    }
   }else if(data.missing_batch_count!==0||
      data.all_batches_observed_not_all_checks_required_verified!==true)
    throw Error('Incomplete legacy saved-batch provenance');
   const earliest=gateTime(time),latest=gateTime(data.latest_observed_at_utc);
   if(latest<earliest||latest-earliest>600000)
    throw Error('Unbounded batch observation range');
   const expectedObservedPrs=versioned?
    Array.from({length:expected},(_,i)=>i+1)
     .filter(i=>!missing.includes(i))
     .reduce((sum,i)=>sum+Math.min(4,data.review_candidate_pr_count-(i-1)*4),0):
    null;
   bridgeCoverage={manifest_sha256:hash,batches:data.observed_batch_count,
    expected_batches:expected,missing_indexes:missing,
    candidate_count:versioned?data.review_candidate_pr_count:null,
    expected_observed_prs:expectedObservedPrs,
    missing_prs:versioned?data.missing_batch_prs:[],
    complete:missing.length===0,
    earliest_observed_at_utc:time,latest_observed_at_utc:data.latest_observed_at_utc,
    complete_receipts_not_required_checks:missing.length===0};
  }else if(data.original_board_sha256!==undefined||
   data.observed_batch_count!==undefined||data.missing_batch_count!==undefined||
   data.earliest_observed_at_utc!==undefined||
   data.latest_observed_at_utc!==undefined)
   throw Error('Incomplete all-batch metadata');
  const seenBridgeIssues=new Set();
  for(const issue of data.issues){
   if(!issue||!Array.isArray(issue.existing_prs)||issue.existing_prs.length>5)
    throw Error('Unbounded issue PR references');
   if(bridgeCoverage){
    const saved=entries.get(issue.issue);
    if(!saved||saved.url!==issue.url||seenBridgeIssues.has(issue.issue)||
       issue.existing_prs.length===0)
     throw Error('Bridge issue is foreign, duplicated or absent from current board');
    seenBridgeIssues.add(issue.issue);
   }
   const seenIssuePrs=new Set();
   for(const pr of issue.existing_prs){
    if(bridgeCoverage){
     const saved=entries.get(issue.issue);
     if(!saved.open_pr_candidates.includes(pr.url)||seenIssuePrs.has(pr.url))
      throw Error('Bridge PR association differs from captured original issue');
     seenIssuePrs.add(pr.url);
    }
    const gate=pr.live_exact_head_review_checks_observation;
    if(gate!==undefined&&gate!==null){
     if(gate.url!==pr.url)throw Error('Mismatch PR observation URL');
     rows.push(gate);
    }else if(bridgeCoverage){
     throw Error('Complete source contains issue PR without a gate receipt');
    }
   }
  }
  if(rows.length>300)throw Error('Unbounded gate receipts');
 }else throw Error('Unsupported PR-gate evidence schema');
 const timestamp=gateTime(time),items=new Map();
 for(const row of rows){
  const gate=parseGateRow(row);
  if(!knownPrLinks.has(gate.url)){
   if(bridgeCoverage)throw Error('Bridge contains PR not in this board');
   continue;
  }
  const previous=items.get(gate.url);
  if(previous&&JSON.stringify(previous)!==JSON.stringify(gate))
   throw Error('Conflicting PR observations');
  items.set(gate.url,gate);
 }
 if(!items.size)throw Error('No PR receipt matches this captured source board');
 if(bridgeCoverage?.expected_observed_prs!==null&&bridgeCoverage&&
    bridgeCoverage.expected_observed_prs!==undefined&&
    items.size!==bridgeCoverage.expected_observed_prs)
  throw Error('Saved batch count does not match distinct observed PR receipts');
 if(bridgeCoverage?.missing_prs?.some(group=>
   group.unobserved_pr_urls.some(url=>items.has(url))))
  throw Error('Missing batch PR already present as an observed gate receipt');
 return {time,timestamp,items,kind:data.schema,reviewContext,bridgeCoverage};
}
function gateState(url){
 const row=prGateImport?.items.get(url);
 if(!row||!row.available)return 'unverified';
 if(row.checks_failed||row.legacy_failed)return 'failed';
 if(row.checks_pending||row.legacy_pending)return 'pending';
 if(row.incomplete)return 'unverified';
 if(row.changes_requested||!row.approvals)return 'review';
 if(!row.observed_checks&&!row.legacy_contexts)return 'unverified';
 return 'observed-no-authority';
}

// Compare two trusted *saved* observations, never live checks or claims.
// Replacing the current imported source clears this historical comparison.
function sourceGateState(row){
 if(!row)return 'not-observed';
 if(!row.available)return 'unavailable';
 if(row.checks_failed||row.legacy_failed)return 'failed';
 if(row.checks_pending||row.legacy_pending)return 'pending';
 if(row.incomplete)return 'incomplete';
 if(row.changes_requested||!row.approvals)return 'review-needed';
 if(!row.observed_checks&&!row.legacy_contexts)return 'no-checks-observed';
 return 'sampled-clear-not-authorized';
}
function clearGateDelta(){
 prGatePrevious=null;gateDeltaPreview=null;
 document.getElementById('prGateDeltaRows').replaceChildren();
 document.getElementById('prGateDeltaSummary').textContent=
  'No historical comparison imported; current CI, owner and review UNKNOWN.';
 document.getElementById('prGateDeltaPreview').disabled=true;
 document.getElementById('prGateDeltaPanel').hidden=true;
 document.getElementById('prGateDeltaText').textContent='';
 document.getElementById('prGateDeltaDownload').disabled=true;
}
function currentGateDelta(){
 const newer=prGateImport,older=prGatePrevious;
 if(!older||!newer||older.timestamp>=newer.timestamp)
  throw Error('Current and earlier saved observations are not time ordered');
 const urls=new Set([...older.items.keys(),...newer.items.keys()]);
 if(urls.size>300)throw Error('Combined comparison exceeds source bounds');
 const changes=[];let unchanged=0,headChanged=0;
 for(const url of [...urls].sort()){
  const a=older.items.get(url),b=newer.items.get(url);
  const from=sourceGateState(a),to=sourceGateState(b);
  const distinctHeads=Boolean(a?.head&&b?.head&&a.head!==b.head);
  const sameHead=Boolean(a?.head&&b?.head&&a.head===b.head);
  // Count movement is valid only for the same sampled head and status.
  const counts=sameHead?['checks_pending','checks_failed','legacy_pending',
   'legacy_failed','approvals','changes_requested'].filter(k=>a[k]!==b[k]):[];
  if(from===to&&!distinctHeads&&!counts.length){unchanged++;continue;}
  if(distinctHeads)headChanged++;
  changes.push({url,from,to,head_change:distinctHeads,
   old_head:a?.head||null,new_head:b?.head||null,
   count_changes:counts.map(k=>({field:k,old:a[k],new:b[k]})),
   meaning:distinctHeads?'DIFFERENT HEAD: old check counts not comparable':
    !sameHead?'PARTIAL/ABSENT source: do not infer improvement':
    'SAME HEAD: saved sample movement only, not required-check approval'});
 }
 return {older,newer,changes,unchanged,headChanged,total:urls.size};
}
function gateDeltaMarkdown(){
 const delta=currentGateDelta();
 return ['# zSentinel · historical saved PR gate differences (human review only)',
  'Original source board: '+boardSha,
  'Older saved UTC: '+delta.older.time,
  'Newer saved UTC: '+delta.newer.time,
  'Older JSON SHA256: '+delta.older.sha256,
  'Newer JSON SHA256: '+delta.newer.sha256,
  'Original PRs union: '+delta.total+'; sample changes: '+delta.changes.length+
   '; new HEAD observed: '+delta.headChanged+'; unchanged samples: '+delta.unchanged,
  'IMPORTANT: both snapshots are HISTORICAL; required CI, independent reviewer,',
  'active claim, current HEAD, protected branch and release permission UNKNOWN.',
  'This document cannot authorize claim, merge, deploy, retry or dispatch.',
  '',...delta.changes.flatMap(c=>[
   '## '+c.url,'Older saved: '+c.from+' · newer saved: '+c.to,
   c.meaning,
   ...(c.head_change?['Different sampled HEAD: '+c.old_head+' -> '+c.new_head]:[]),
   ...c.count_changes.map(x=>x.field+': '+x.old+' -> '+x.new),''
  ])].join('\\n')+'\\n';
}
function gateOverview(groups){
 const counts={failed:0,pending:0,review:0,unverified:0,
  'observed-no-authority':0};
 for(const group of groups)counts[gateState(group.url)]++;
 const age=prGateImport?Math.max(0,Math.floor((Date.now()-prGateImport.timestamp)/60000)):null;
 const coverage=prGateImport?.bridgeCoverage?
   ' · '+(prGateImport.bridgeCoverage.complete?'COMPLETE':'PARTIAL')+
   ' saved batch coverage '+prGateImport.bridgeCoverage.batches+'/'+
   prGateImport.bridgeCoverage.expected_batches+
   (prGateImport.bridgeCoverage.missing_indexes.length?
    ' · UNKNOWN batches '+prGateImport.bridgeCoverage.missing_indexes.slice(0,12).join(', ')+
     (prGateImport.bridgeCoverage.missing_indexes.length>12?' +more':''):'')+
   ' · source '+prGateImport.bridgeCoverage.manifest_sha256.slice(0,12):
  prGateImport?.reviewContext?
   ' · only saved batch '+prGateImport.reviewContext.batch_index+'/'+
    prGateImport.reviewContext.batches_total+' observed':
   ' · no manifest-complete saved source proven';
 return 'Saved observations: '+counts.failed+' failed · '+counts.pending+
  ' waiting · '+counts.review+' review needed · '+counts.unverified+
  ' unknown/incomplete · '+counts['observed-no-authority']+
  ' sampled clear (NOT merge approval)'+
  (age===null?' · no imported source':' · saved source '+age+' min old'+
   (age>15?' STALE':'')+coverage)+
  ' · current required CI, claims, branch protection and owner review UNKNOWN';
}
const gateNextHuman={
 inspect_incomplete_current_head_gate_source:
  'De opgeslagen check-/reviewbron is onvolledig. Bekijk de actuele GitHub-bron opnieuw.',
 inspect_current_head_failed_checks_with_owner:
  'Er zijn mislukte checks. Laat de bevoegde PR-eigenaar de fout herstellen.',
 wait_for_existing_current_head_checks:
  'De verplichte checks lopen nog. Niet mergen of opnieuw starten.',
 owner_address_exact_head_review_changes:
  'Er staan wijzigingen gevraagd op deze PR. Stem af met de bevoegde eigenaar.',
 verify_missing_current_head_checks_and_rules:
  'Er zijn geen actuele checks gezien. Controleer de branchregels en workflows.',
 request_independent_current_head_review:
  'Vraag een onafhankelijke review aan voor de huidige HEAD.',
 verify_required_rules_ownership_and_release_separately:
  'Bevestig afzonderlijk branchregels, eigenaarschap en releasebevoegdheid.'
};
function humanGateStep(code){
 return Object.hasOwn(gateNextHuman,code)?gateNextHuman[code]:
  'Bekijk GitHub en de huidige eigenaar; dit is geen vrijgave om te mergen.';
}
function prGateLabel(url){
 if(!prGateImport)return 'No imported PR gate receipt; current checks and approvals UNKNOWN.';
 const row=prGateImport.items.get(url);
 if(!row)return 'No imported receipt for this PR; current checks and approvals UNKNOWN.';
 const age=Math.max(0,Math.floor((Date.now()-prGateImport.timestamp)/60000));
 const label='IMPORTED '+(age>15?'STALE ':'')+'snapshot '+age+' min ago · ';
 if(!row.available)return label+'GitHub gate source unavailable or HEAD changed; no merge clearance.';
 return label+'observed HEAD '+row.head.slice(0,12)+
  ' · checks pending '+row.checks_pending+' / failed '+row.checks_failed+
  ' · legacy pending '+row.legacy_pending+' / failed '+row.legacy_failed+
  ' · observed independent approvals '+row.approvals+
  (row.incomplete?' · SOURCE INCOMPLETE':'')+
  (!row.observed_checks&&!row.legacy_contexts?' · NO CHECK CONTEXT SEEN':'')+
  ' · VOLGENDE ACTIE: '+humanGateStep(row.next)+
  ' · HUIDIGE EIGENAAR, BRANCHREGELS EN MERGEBEVOEGDHEID NIET VASTGESTELD.';
}
function prDeskGroups(){
 const index=new Map(),weights={P0:0,P1:1,P2:2,Unspecified:3};
 for(const card of filtered()){
  for(const url of card.open_pr_candidates){
   let group=index.get(url);
   if(!group){
    group={url,project:card.project,priority:card.priority,issues:[],gaps:new Set()};
    index.set(url,group);
   }
   if(weights[card.priority]<weights[group.priority])group.priority=card.priority;
   group.issues.push(card);
   for(const gap of card.evidence_gaps)group.gaps.add(gap);
  }
 }
 const desired=document.getElementById('prGateFocus').value;
 const focus={failed:0,pending:1,review:2,unverified:3,'observed-no-authority':4};
 return [...index.values()].filter(group=>!desired||gateState(group.url)===desired)
  .sort((a,b)=>weights[a.priority]-weights[b.priority] ||
   focus[gateState(a.url)]-focus[gateState(b.url)] ||
   b.issues.length-a.issues.length ||
   a.project.localeCompare(b.project)||a.url.localeCompare(b.url));
}
function missingGateCommand(index){
 // Only integer indexes validated against the reviewed source. This is a
 // copyable read-only operator command, never executed by this browser.
 if(!Number.isInteger(index)||index<1||index>75)return '';
 return 'python3 scripts/zsentinel_pr_gate_now.py --from-review ./zsentinel-offline-merge-first-review.json --batch '+
  index+' --output ./zsentinel-saved-gate-batch-'+index+'.json';
}
function refreshPrDesk(){
 const list=document.getElementById('prDeskRows');
 list.replaceChildren();
 const current=filtered(),groups=prDeskGroups();
 const unlinked=current.filter(c=>c.open_pr_candidates.length===0);
 const missingRows=document.getElementById('missingGateRows');
 missingRows.replaceChildren();
 const missing=prGateImport?.bridgeCoverage?.missing_prs||[];
 for(const sourceBatch of missing){
  const row=elem('li','Saved batch '+sourceBatch.batch_index+
   ' · ALL gates UNKNOWN · original review manifest '+prGateImport.bridgeCoverage.manifest_sha256.slice(0,12));
  const links=elem('ul');
  for(const url of sourceBatch.unobserved_pr_urls){
   const item=elem('li');
   item.append(link(url+' ↗',url),elem('small','Original link only · current HEAD, checks and owner UNVERIFIED'));
   links.append(item);
  }
  row.append(links,elem('pre',missingGateCommand(sourceBatch.batch_index)));
  missingRows.append(row);
 }
 if(!missing.length)
  missingRows.append(elem('li','No explicitly identified missing saved gate batches in current imported source.'));
 const without=document.getElementById('unlinkedIssueRows');
 without.replaceChildren();
 for(const card of unlinked){
  const item=elem('li');
  item.append(link(card.priority+' '+card.issue+' ↗',card.url),
   elem('small',card.title+' · '+card.project+
     ' · human decision: '+(handoffs.get(card.issue)?.decision||'investigate')));
  without.append(item);
 }
 if(!unlinked.length)
  without.append(elem('li','Every filtered issue had a captured PR link; live state still unknown.'));
 const withMultiple=groups.filter(g=>g.issues.length>1).length;
 const gateFilter=document.getElementById('prGateFocus').value;
 document.getElementById('prDeskSummary').textContent=
  groups.length+' unique saved PR links · '+withMultiple+
  ' linked from multiple issues · '+unlinked.length+
  ' issue(s) without referenced PRs in '+current.length+
  ' filtered captured issue cards. Gate filter '+(gateFilter||'all')+
  '. Snapshot '+board.observed_at_utc+'. '+gateOverview(groups)+
  '. GEEN actuele claim, onafhankelijke review of mergebevoegdheid aangetoond.';
 if(!groups.length){
  list.append(elem('li','No referenced PRs in this bounded filtered snapshot; not proof that none exist.'));
  return;
 }
 for(const group of groups){
  const item=elem('li');
  item.append(link(group.url+' ↗',group.url),
   elem('small',group.priority+' · '+group.project+' · '+group.issues.length+
     ' captured issue link(s) · NOT CURRENT GATE EVIDENCE'));
  const inner=elem('ul');
  for(const card of group.issues){
   const li=elem('li');
   li.append(link(card.priority+' '+card.issue+' ↗',card.url),
    elem('small',card.title+' · human decision: '+
      (handoffs.get(card.issue)?.decision||'investigate')));
   const open=elem('button','View issue in local workbench');open.type='button';
   open.onclick=()=>openObservedIssue(card.issue);
   li.append(open);inner.append(li);
  }
  item.append(inner,elem('small',prGateLabel(group.url)),
   elem('small','Source gaps: '+(group.gaps.size?
   [...group.gaps].slice(0,3).map(g=>line(g,180)).join(' · '):
   'Live claim, exact-head CI, review, branch and release authority UNVERIFIED')));
  list.append(item);
 }
}

function currentPrManifest(){
 const groups=prDeskGroups();
 const cards=filtered();
 const unlinked=cards.filter(c=>c.open_pr_candidates.length===0);
 if(!groups.length&&!unlinked.length)return null;
 const receipt={
  schema:'zsentinel-offline-merge-first-review-v1',
  source_board_sha256:boardSha,
  source_observed_at_utc:board.observed_at_utc,
  captured_issue_count:cards.length,
  saved_gate_focus:document.getElementById('prGateFocus').value||'all',
  evidence_scope:'bounded_saved_snapshot_not_live',
  project_coverage:board.coverage,
  // Portable triage is descriptive, never an execution or review receipt.
  saved_gate_triage:{
   observed_at_utc:prGateImport?.time||null,
   source_schema:prGateImport?.kind||null,
   imported_source_sha256:prGateImport?.sha256||null,
   review_manifest_sha256:prGateImport?.bridgeCoverage?.manifest_sha256||null,
   complete_saved_batch_set:prGateImport?.bridgeCoverage?.complete===true,
   observed_saved_batches:prGateImport?.bridgeCoverage?.batches??null,
   expected_saved_batches:prGateImport?.bridgeCoverage?.expected_batches??null,
   captured_review_pr_candidates:prGateImport?.bridgeCoverage?.candidate_count??null,
   distinct_imported_saved_prs:prGateImport?.items.size??0,
   missing_saved_batch_indexes:prGateImport?.bridgeCoverage?.missing_indexes||[],
   missing_saved_batch_pr_links:prGateImport?.bridgeCoverage?.missing_prs||[],
   observed_categories:{
    failed:groups.filter(g=>gateState(g.url)==='failed').length,
    waiting:groups.filter(g=>gateState(g.url)==='pending').length,
    review_needed:groups.filter(g=>gateState(g.url)==='review').length,
    unknown_or_incomplete:groups.filter(g=>gateState(g.url)==='unverified').length,
    sampled_clear_not_approved:groups.filter(g=>
      gateState(g.url)==='observed-no-authority').length
   },
   current_head_and_required_branch_checks_verified:false,
   live_claim_or_review_authorized:false,
   merge_authorized:false
  },
  required_current_claim_review_ci_branch_release_check:true,
  current_pr_open_state_verified:false,
  current_pr_head_verified:false,
  independent_review_verified:false,
  claim_authorized:false,merge_authorized:false,deploy_authorized:false,
  dispatch_authorized:false,
  pr_references:groups.map(g=>({
   url:g.url,project:g.project,highest_linked_priority:g.priority,
   observed_issue_refs:g.issues.map(c=>({
    issue:c.issue,url:c.url,title:c.title,priority:c.priority,
    missing_evidence:c.evidence_gaps
   })),
   source_missing_evidence:[...g.gaps],
   optional_imported_observation:prGateImport?.items.get(g.url)?
    {source_kind:prGateImport.kind,source_observed_at_utc:prGateImport.time,
     imported_source_sha256:prGateImport.sha256,
     original_review_manifest:prGateImport.reviewContext,
     complete_saved_bridge:prGateImport.bridgeCoverage,
     stale_at_export:Date.now()-prGateImport.timestamp>900000,
     receipt:prGateImport.items.get(g.url),
     current_pr_head_verified:false,mandatory_branch_rules_verified:false,
     merge_authorized:false}:null,
   active_claim_verified:false,
   exact_head_required_ci_verified:false,
   independent_review_verified:false,
   merge_authorized:false
  })),
  issues_without_recorded_pr:unlinked.map(c=>({
   issue:c.issue,url:c.url,title:c.title,project:c.project,
   priority:c.priority,missing_evidence:c.evidence_gaps,
   active_claim_verified:false,new_work_authorized:false
  }))
 };
 return JSON.stringify(receipt,null,2)+'\\n';
}
function currentPrDesk(){
 const groups=prDeskGroups();
 const unlinked=filtered().filter(c=>c.open_pr_candidates.length===0);
 if(!groups.length&&!unlinked.length)return null;
 const lines=[
  '# zSentinel · MERGE FIRST · review-only PR handoff',
  '',
  'Board SHA256: '+boardSha,
  'Captured UTC: '+board.observed_at_utc,
  'Filtered current issue cards: '+filtered().length,
  'Unique referenced PR links: '+groups.length,
  'Gate focus: '+(document.getElementById('prGateFocus').value||'all'),
  'Filtered issues with no recorded PR link: '+unlinked.length,
  'Evidence window: bounded and partial; original links are not verified open/current.',
  'Saved gate status: '+line(gateOverview(groups),750),
  ...(prGateImport?.bridgeCoverage?.missing_prs||[]).flatMap(batch=>[
    'Missing saved batch '+batch.batch_index+' · NO GATE RECEIPT · verify separately:',
    ...batch.unobserved_pr_urls.map(url=>'  - '+url+' · currently unverified'),
    '  Optional manual read-only collection: '+missingGateCommand(batch.batch_index)
  ]),
  'All claim/merge/deploy permissions: FALSE; required CI/branch protection/review UNKNOWN.',
  'Read current GitHub PR and active Notion/worker claims separately before action.',
  ''
 ];
 for(const group of groups){
  lines.push('## '+line(group.priority,20)+' · '+group.url,
   'Project: '+line(group.project,50),
   'Referenced original issues: '+group.issues.length+
    (group.issues.length>1?' (coordinate references; code duplication NOT proved)':''),
   ...group.issues.map(c=>
    '- '+c.url+' · '+line(c.title,180)+' · '+c.priority+
    ' · human disposition '+(handoffs.get(c.issue)?.decision||'investigate')),
   'Review source candidates: '+(prGateImport?.reviewContext?
    prGateImport.reviewContext.candidate_count+
    ' · imported batch '+prGateImport.reviewContext.batch_index+'/'+
    prGateImport.reviewContext.batches_total+
    ' · other batches not included':
    'not a review-manifest-based observation'),
   'Imported source SHA256: '+(prGateImport?.items.has(group.url)?
    prGateImport.sha256:'not provided or not applicable'),
   'Imported PR gate observation (non-authorizing): '+line(prGateLabel(group.url),650),
   'Observed gaps: '+(group.gaps.size?
    [...group.gaps].map(g=>line(g,300)).join('; '):
    'No complete current gate receipt; verify required CI, owner, review and release.'),
   'Next: inspect this existing PR before creating new source work. Do not auto-merge.',
   '');
 }
 if(unlinked.length){
  lines.push('## Productissues without captured PR references (NOT verified unowned)','',
   ...unlinked.map(c=>
    '- '+c.url+' · '+line(c.priority,20)+' · '+line(c.title,180)+
    ' · human disposition '+(handoffs.get(c.issue)?.decision||'investigate')),
   'Before implementation: check active owner claims, recent branches and live open PR inventory.',
   '');
 }
 lines.push('Source-only grouping. No GitHub mutations, review approvals or releases performed.','');
 return lines.join('\\n');
}
function refreshPortfolio(){
 const cards=board.cards;
 const stats=document.getElementById('portfolioStats');
 const projects=document.getElementById('projectCounts');
 stats.replaceChildren();projects.replaceChildren();
 const counts=[
  ['P0 observed',cards.filter(c=>c.priority==='P0').length],
  ['P1 observed',cards.filter(c=>c.priority==='P1').length],
  ['Existing PR references',cards.filter(c=>c.open_pr_candidates.length>0).length],
  ['Evidence gaps',cards.filter(c=>c.evidence_gaps.length>0).length]
 ];
 counts.forEach(([label,count])=>{
  stats.append(elem('dt',label),elem('dd',String(count)));
 });
 board.projects.forEach(project=>{
  const rows=cards.filter(c=>c.project===project);
  if(!rows.length)return;
  const item=elem('li');
  const p0=rows.filter(c=>c.priority==='P0').length;
  const p1=rows.filter(c=>c.priority==='P1').length;
  item.append(elem('strong',project+': '),elem('span',
   rows.length+' observed · '+p0+' P0 · '+p1+' P1'));
  projects.append(item);
 });
 const age=Date.now()-Date.parse(board.observed_at_utc);
 const stale=!Number.isFinite(age)||age<0||age>6*3600*1000;
 document.getElementById('portfolioFreshness').textContent=
  (stale?'Stale or unverifiable saved observation; refresh source externally.':
   'Saved observation within six hours (not a live status).')+
   ' Recorded '+board.observed_at_utc+'.';
}
function refreshFollowups(){
 const list=document.getElementById('followupRows');
 list.replaceChildren();
 const rows=board.cards.filter(c=>manualGateDecisions.has(handoffs.get(c.issue)?.decision))
  .map(c=>({card:c,info:handoffs.get(c.issue)}))
  .sort((a,b)=>Number(a.info?.followup_status==='local-complete')-
    Number(b.info?.followup_status==='local-complete')||
   Number(!a.info?.due)-Number(!b.info?.due)||
   (a.info?.due||'').localeCompare(b.info?.due||'')||
   ({P0:0,P1:1,P2:2,Unspecified:3}[a.card.priority]-
    {P0:0,P1:1,P2:2,Unspecified:3}[b.card.priority])||
   a.card.issue.localeCompare(b.card.issue)).slice(0,60);
 const active=rows.filter(x=>x.info?.followup_status!=='local-complete');
 const overdue=active.filter(x=>x.info?.owner&&x.info?.due&&x.info.due<utcToday()).length;
 const incomplete=active.filter(x=>!x.info?.owner?.trim()||!x.info?.due).length;
 document.getElementById('followupSummary').textContent=
  active.length+' open human follow-ups · '+
  (rows.length-active.length)+' locally closed · '+
  overdue+' past review date · '+incomplete+' missing contact/date · '+
  '0 external notifications, 0 authorized claims.';
 if(!rows.length){list.append(elem('li','No manual follow-ups saved in this session. Live blocker status unknown.'));return;}
 rows.forEach(({card,info})=>{
  const li=elem('li'),btn=elem('button',card.priority+' '+card.issue);
  btn.type='button';
  btn.onclick=()=>{saveSelection();invalidateBrief();selected=card.issue;
    drawList();drawDetail();detail.scrollIntoView({block:'nearest'});};
  li.append(btn,elem('strong',card.title),elem('small',
   'Human label: '+(info.owner||'not entered')+' · UTC review date: '+
   (info.due||'not entered')+' · '+dueState(info)+' · '+info.decision+
   (info.carried_from?' · Imported from older source; owner must reverify':'')));
  list.append(li);
 });
}
function refreshBatch(){
 const cases=board.cards.filter(c=>manualGateDecisions.has(handoffs.get(c.issue)?.decision))
  .sort((a,b)=>({P0:0,P1:1,P2:2,Unspecified:3}[a.priority]-
    {P0:0,P1:1,P2:2,Unspecified:3}[b.priority])||a.issue.localeCompare(b.issue));
 const eligible=new Set(cases.map(c=>c.issue));
 for(const id of batchSelection)if(!eligible.has(id))batchSelection.delete(id);
 const list=document.getElementById('batchRows');list.replaceChildren();
 for(const card of cases){
  const li=elem('li'),label=elem('label'),checkbox=elem('input');
  checkbox.type='checkbox';checkbox.checked=batchSelection.has(card.issue);
  checkbox.setAttribute('aria-label','Include human follow-up '+card.issue);
  checkbox.addEventListener('change',()=>{
   checkbox.checked?batchSelection.add(card.issue):batchSelection.delete(card.issue);
   invalidateBatch();refreshBatch();
  });
  const info=handoffs.get(card.issue);
  label.append(checkbox,elem('span',card.priority+' '+card.issue+' · '+card.title+
   ' · '+dueState(info)));
  li.append(label,link('Original GitHub issue ↗',card.url));list.append(li);
 }
 if(!cases.length)list.append(elem('li','No human-only follow-ups have been marked on this bounded board.'));
 document.getElementById('batchCount').textContent=batchSelection.size+
  ' selected of '+cases.length+' saved human-only cases · no external action authorized.';
}
function batchPlan(){
 const action=document.getElementById('batchAction').value;
 const date=document.getElementById('batchDate').value;
 if(!['open','local-complete','review-date'].includes(action)||
    (action==='review-date'&&(!date||!validFollowupDate(date))))
   return null;
 const selected=[...batchSelection].sort();
 if(!selected.length||selected.length>60)return null;
 const rows=[];
 for(const issue of selected){
  const card=entries.get(issue),info=handoffs.get(issue);
  if(!card||!manualGateDecisions.has(info?.decision))return null;
  const current=info.followup_status||'open';
  let to=current,nextDate=info.due||'';
  if(action==='review-date'){
   if(current==='local-complete')continue;
   nextDate=date;
  }else to=action;
  if(to===current&&nextDate===(info.due||''))continue;
  rows.push({issue,project:card.project,priority:card.priority,title:card.title,
    original:card.url,firstGate:card.evidence_gaps[0]||
     'Current owner/claim/CI/release permissions must be independently reverified',
    before:JSON.stringify(info),from:current,to,oldDate:info.due||'',newDate:nextDate});
 }
 if(!rows.length)return null;
 const source={board:boardSha,observed_at:board.observed_at_utc,
  selected,action,date,rows};
 const proof=JSON.stringify(source);
 const preview=['Human-only private bulk follow-up review',
   'Captured source '+boardSha+' · '+board.observed_at_utc,
   'No GitHub issue closure, ownership assignment, CI success or release.',
   'Changes: '+rows.length+' of '+selected.length+' selected cards',
   ...rows.flatMap(r=>[r.priority+' '+r.issue+' · '+line(r.title,180),
    'Original issue · '+r.original,
    'First observed missing gate · '+line(r.firstGate,250),
    'Local state '+r.from+' → '+r.to+' · UTC date '+(r.oldDate||'none')+
      ' → '+(r.newDate||'none'),'']),
   'Confirm only after reviewing each original issue and ALL required gates.'].join('\\n');
 return {proof,rows,preview};
}
function refreshTriage(){
 const ranks={P0:0,P1:1,P2:2,Unspecified:3};
 const observations=baseline?new Map(changesSinceBaseline().map(c=>[c.issue,c.kind])):null;
 const weight=kind=>({'new-observation':0,'changed-evidence':1,'still-observed':2})[kind]??3;
 const cards=filtered().slice().sort((a,b)=>
  ranks[a.priority]-ranks[b.priority] ||
  Number(dueState(handoffs.get(b.issue)).startsWith('overdue'))-
    Number(dueState(handoffs.get(a.issue)).startsWith('overdue')) ||
  Number(handoffs.get(a.issue)?.decision==='park-blocker')-Number(handoffs.get(b.issue)?.decision==='park-blocker') ||
  (observations?weight(observations.get(a.issue))-weight(observations.get(b.issue)):0) ||
  a.project.localeCompare(b.project) || a.issue.localeCompare(b.issue));
 const urgent=cards.filter(c=>c.priority==='P0'||c.priority==='P1'||handoffs.get(c.issue)?.decision==='park-blocker');
 const list=document.getElementById('triageQueue');list.replaceChildren();
 const parked=urgent.filter(c=>handoffs.get(c.issue)?.decision==='park-blocker').length;
 const locallyClosed=urgent.filter(c=>handoffs.get(c.issue)?.followup_status==='local-complete').length;
 document.getElementById('triageCount').textContent=urgent.length+
  ' observed priority/parked entries · '+parked+' human-parked · '+
  locallyClosed+' locally closed follow-ups (issue remains in inbox) · 0 authorized actions';
 if(!urgent.length){list.append(elem('li','No P0/P1 or parked entry in this saved filtered snapshot; live backlog unknown.'));return;}
 urgent.forEach(c=>{
  const li=elem('li'),b=elem('button',c.priority+' '+c.issue);b.type='button';
  b.setAttribute('aria-label','Inspect observed issue '+c.issue);
  b.onclick=()=>{saveSelection();invalidateBrief();selected=c.issue;drawList();drawDetail();detail.scrollIntoView({block:'nearest'});};
  const human=handoffs.get(c.issue)?.decision||'investigate';
  const gap=c.evidence_gaps.length?c.evidence_gaps[0]:
   'live owner, exact-head CI and release admission unverified';
  const changed=observations?.get(c.issue);
  li.append(b,elem('strong',' '+c.title),
   elem('small',' · '+c.project+' · '+(changed?changed+' · ':'')+human+
   (manualGateDecisions.has(human)?' · '+dueState(handoffs.get(c.issue)):'')+
   ' · '+line(gap,160)));list.append(li);
 });
}
function sourceInstant(value){
 if(typeof value!=='string'||value.length>45||!/(Z|[+-][0-9]{2}:[0-9]{2})$/.test(value))return null;
 const t=Date.parse(value);
 return Number.isFinite(t)?t:null;
}
function acceptBaseline(data){
 if(!data||Array.isArray(data)||data.schema!==baselineSchema||
    data.authorizes_claim!==false||data.authorizes_merge!==false||
    data.authorizes_deploy!==false||data.automatic_dispatch_enabled!==false||
    !Array.isArray(data.cards)||data.cards.length>60)throw Error('unsafe baseline');
 const cov=data.coverage;
 if(!cov||Array.isArray(cov)||!cov.projects||Array.isArray(cov.projects)||
    typeof cov.projects!=='object'||Object.keys(cov.projects).length>50||
    ['inbox_truncated','cards_truncated','github_denial'].some(f=>typeof cov[f]!=='boolean')||
    Object.entries(cov.projects).some(([p,status])=>
      !/^[a-z][a-z0-9_-]{0,63}$/.test(p)||
      !['partial','degraded','unavailable'].includes(status)))
      throw Error('untrusted earlier source coverage');
 const earlier=sourceInstant(data.observed_at_utc);
 const current=sourceInstant(board.observed_at_utc);
 if(earlier===null||current===null||earlier>=current||earlier>Date.now()+300000)
   throw Error('baseline must predate current observation');
 const items=new Map();
 for(const c of data.cards){
  if(!c||Array.isArray(c)||typeof c.issue!=='string'||!issuePattern.test(c.issue)||
     items.has(c.issue)||typeof c.project!=='string'||
     !/^[a-z][a-z0-9_-]{0,63}$/.test(c.project)||
     !['P0','P1','P2','Unspecified'].includes(c.priority)||
     c.claim_authorized!==false||c.merge_authorized!==false||c.deploy_authorized!==false||
     !Array.isArray(c.open_pr_candidates)||c.open_pr_candidates.length>3||
     !Array.isArray(c.evidence_gaps)||c.evidence_gaps.length>15)
       throw Error('invalid earlier issue card');
  if(c.open_pr_candidates.some(p=>typeof p!=='string'||!/^https:\\/\\/github\.com\\/[A-Za-z0-9_.-]+\\/[A-Za-z0-9_.-]+\\/pull\\/[1-9][0-9]{0,8}$/.test(p))||
     c.evidence_gaps.some(g=>typeof g!=='string'||g.length>300))
       throw Error('invalid earlier evidence');
  if(typeof c.title!=='string'||!c.title.trim()||c.title.length>180||
     typeof c.recommended_next_step!=='string'||c.recommended_next_step.length>650||
     c.open_pr_candidates.some(p=>!p.startsWith('https://github.com/'+c.issue.split('#')[0]+'/pull/')))
       throw Error('invalid earlier issue detail or cross-repository PR');
  items.set(c.issue,{issue:c.issue,project:c.project,priority:c.priority,
   title:c.title,next:c.recommended_next_step,
   prs:new Set(c.open_pr_candidates),gaps:new Set(c.evidence_gaps)});
 }
 return {observed_at_utc:data.observed_at_utc,items,
  coverage:{projects:cov.projects,inbox_truncated:cov.inbox_truncated,
    cards_truncated:cov.cards_truncated,github_denial:cov.github_denial}};
}
function changesSinceBaseline(){
 if(!baseline)return [];
 const now=new Map(board.cards.map(c=>[c.issue,c]));
 const result=[];
 for(const c of board.cards){
  const old=baseline.items.get(c.issue);
  if(!old){result.push({kind:'new-observation',issue:c.issue,priority:c.priority});continue;}
  const changedFields=[];
  if(old.priority!==c.priority)changedFields.push('priority '+old.priority+' → '+c.priority);
  if(old.title!==c.title)changedFields.push('issue title');
  if(old.next!==c.recommended_next_step)changedFields.push('recommended investigation');
  if(old.prs.size!==new Set(c.open_pr_candidates).size||
     c.open_pr_candidates.some(p=>!old.prs.has(p)))changedFields.push('referenced PRs');
  if(old.gaps.size!==new Set(c.evidence_gaps).size||
     c.evidence_gaps.some(g=>!old.gaps.has(g)))changedFields.push('missing evidence');
  result.push({kind:changedFields.length?'changed-evidence':'still-observed',
    issue:c.issue,priority:c.priority,changedFields});
 }
 for(const old of baseline.items.values())if(!now.has(old.issue))
   result.push({kind:'not-observed',issue:old.issue,priority:old.priority});
 return result;
}
function refreshComparison(){
 const meta=document.getElementById('comparisonMeta');
 const covLabel=document.getElementById('comparisonCoverage');
 const stats=document.getElementById('comparisonStats'),list=document.getElementById('comparisonRows');
 stats.replaceChildren();list.replaceChildren();
 document.getElementById('clearBaseline').disabled=!baseline;
 document.getElementById('previewDeltaCsv').disabled=!baseline;
 const changeSelect=document.getElementById('change');
 changeSelect.disabled=!baseline;
 if(!baseline)changeSelect.value='';
 if(!baseline){
  meta.textContent='Only this captured board is loaded; no baseline or live comparison.';
  covLabel.textContent='Current source: bounded; original GitHub issue coverage is never a complete portfolio inventory.';
  return;
 }
 const age=Date.parse(board.observed_at_utc)-Date.parse(baseline.observed_at_utc);
 meta.textContent='Earlier '+baseline.observed_at_utc+' → current '+board.observed_at_utc+
  ' · '+(age/3600000).toFixed(1)+' hours between captured observations.'+
  ' Original earlier source SHA256: '+baseline.sha+
  '. No issue resolution or deploy inferred.';
 const flag=c=>(c.inbox_truncated||c.cards_truncated||c.github_denial)?
  'truncated/denied snapshot': 'bounded partial snapshot';
 const degraded=c=>Object.entries(c.projects).filter(([,v])=>v!=='partial')
  .map(([p,v])=>p+':'+v).slice(0,20).join(', ')||'none flagged (still partial)';
 covLabel.textContent='Earlier: '+flag(baseline.coverage)+
  '; unavailable/degraded projects: '+degraded(baseline.coverage)+
  '. Current: '+flag(board.coverage)+
  '; unavailable/degraded projects: '+degraded(board.coverage)+
  '. Neither board establishes completeness, resolution or current ownership.';
 const events=changesSinceBaseline();
 const names=[['new-observation','New in current snapshot'],
  ['changed-evidence','Changed priority/PR/gap observation'],
  ['still-observed','Still observed'],['not-observed','No longer in current snapshot (NOT resolved)']];
 for(const [kind,title] of names){
  const subset=events.filter(e=>e.kind===kind);
  const dt=elem('dt',title),dd=elem('dd',String(subset.length));stats.append(dt,dd);
  subset.forEach(e=>{
   const li=elem('li');
   li.append(elem('strong',title+' · '),elem('span',e.priority+' '+e.issue));
   if(e.changedFields?.length)
     li.append(elem('small',' · Changed: '+e.changedFields.join(', ')));
   if(e.kind!=='not-observed'){
    const b=elem('button','Inspect current issue');b.type='button';
    b.onclick=()=>{saveSelection();invalidateBrief();selected=e.issue;drawList();drawDetail();detail.scrollIntoView({block:'nearest'});};
    li.append(' ',b);
   }
   list.append(li);
  });
 }
}
function emptyManual(entry){
 return !entry||(entry.decision==='investigate'&&!entry.note&&!entry.owner&&
  !entry.due&&!entry.carried_from&&entry.followup_status!=='local-complete'&&
  !entry.followup_changed_at_utc);
}
function clearCarryPreview(message){
 carryPreview=null;
 document.getElementById('carryRows').replaceChildren();
 document.getElementById('carryApply').disabled=true;
 document.getElementById('carrySummary').textContent=
  message||'No reviewed carry-forward is pending. Nothing imported.';
}
function previewCarry(data){
 if(!baseline)throw Error('Earlier source board required');
 if(!data||Array.isArray(data)||![1,2,3,4].includes(data.schema_version)||
    data.kind!=='zsentinel-private-human-delivery-handoff'||
    data.source_board_sha256!==baseline.sha||
    data.source_observed_at_utc!==baseline.observed_at_utc||
    data.authorizes_claim!==false||data.authorizes_merge!==false||
    data.authorizes_deploy!==false||!Array.isArray(data.entries)||
    data.entries.length>60||
    (data.schema_version===4&&(!Array.isArray(data.project_recovery)||
      data.project_recovery.length>60)))
  throw Error('Earlier private handoff does not match exact earlier source');
 const seen=new Set(),proposed=[],missing=[];
 const sourceDiff=new Map(changesSinceBaseline().map(c=>[c.issue,c]));
 for(const record of data.entries){
  if(!record||Array.isArray(record)||typeof record.issue!=='string'||
     seen.has(record.issue)||!baseline.items.has(record.issue)||
     !permitted.has(record.decision)||typeof record.note!=='string'||
     record.note.length>1000||new TextEncoder().encode(record.note).length>4000||
     [...record.note].some(ch=>ch.codePointAt(0)<32&&ch!=='\\n'&&ch!=='\\t')||
     (data.schema_version>=2&&(
      typeof record.owner!=='string'||record.owner.length>80||
      [...record.owner].some(ch=>ch.codePointAt(0)<32||ch==='<'||ch==='>')||
      !validFollowupDate(record.due)||
      (data.schema_version>=3&&(
       !['open','local-complete'].includes(record.followup_status)||
       (record.followup_status==='local-complete'&&!manualGateDecisions.has(record.decision))||
       (record.followup_changed_at_utc!==undefined&&
        !validLocalChangeTime(record.followup_changed_at_utc))))||
      (record.carried_from!==undefined&&
       (typeof record.carried_from!=='string'||
        (record.carried_from!==''&&!/^[a-f0-9]{64}$/.test(record.carried_from)))))))
    throw Error('Untrusted prior handoff item');
  seen.add(record.issue);
  if(!manualGateDecisions.has(record.decision))continue;
  const now=entries.get(record.issue);
  if(!now||now.project!==baseline.items.get(record.issue).project){
    missing.push(record.issue);continue;
  }
  proposed.push({issue:record.issue,
   sourceDelta:sourceDiff.get(record.issue),
   value:{decision:record.decision,note:record.note,
    owner:data.schema_version>=2?record.owner:'',
    due:data.schema_version>=2?record.due:'',
    followup_status:data.schema_version>=3?record.followup_status:'open',
    followup_changed_at_utc:data.schema_version>=3?(record.followup_changed_at_utc||''):'',
    carried_from:baseline.sha}});
 }
 const eligible=proposed.filter(x=>emptyManual(handoffs.get(x.issue)));
 const changed=eligible.filter(x=>x.sourceDelta?.kind==='changed-evidence').length;
 carryPreview={sha:baseline.sha,eligible};
 const list=document.getElementById('carryRows');list.replaceChildren();
 const preserved=proposed.length-eligible.length;
 document.getElementById('carrySummary').textContent=
  eligible.length+' manual annotations ready for explicit import · '+
  preserved+' already edited on current board (will remain unchanged) · '+
  missing.length+' no longer matched by current project/issue identity · '+
  changed+' have changed source evidence and require special attention. No live claim is assumed.';
 for(const row of eligible){
  const c=entries.get(row.issue),li=elem('li');
  li.append(elem('strong',row.issue+' · '),elem('span',c.title),
   elem('small',' · '+row.value.decision+' · Human contact: '+
    (row.value.owner||'unknown')+' · UTC review date: '+(row.value.due||'unknown')+
    (row.value.followup_status==='local-complete'?
     ' · LOCALLY CLOSED: issue status unknown':' · open local follow-up')+
    (row.value.followup_changed_at_utc?
     ' · local toggle '+row.value.followup_changed_at_utc+' (device time only)':'')+
    ' · '+(row.sourceDelta?.changedFields?.length?
       'SOURCE CHANGED: '+row.sourceDelta.changedFields.join(', ')+'. ':'')+
    'Current owner/CI must be reverified'));
  list.append(li);
 }
 if(!eligible.length)list.append(elem('li','No safe, unedited matching human follow-ups to carry.'));
 document.getElementById('carryApply').disabled=!eligible.length;
}
function line(value,limit=500){
 return String(value??'').replace(/[\u0000-\u001f\u007f]/g,' ').replace(/[<>\x60]/g,'').slice(0,limit).trim();
}
function currentBrief(){
 const rows=filtered().slice().sort((a,b)=>{
  const ranks={'P0':0,'P1':1,'P2':2,'Unspecified':3};
  return ranks[a.priority]-ranks[b.priority] ||
    a.project.localeCompare(b.project) || a.issue.localeCompare(b.issue);
 });
 const selectedRecoveryProject=document.getElementById('project').value;
 const recoveryRows=board.project_operations.filter(row=>
   !selectedRecoveryProject||row.project===selectedRecoveryProject);
 if(!rows.length && !recoveryRows.length)return null;
 const lines=[
  '# zSentinel · PRIVATE human product handoff',
  '',
  'Source SHA256: '+boardSha,
  'Observed at UTC: '+board.observed_at_utc,
  'Issues selected: '+rows.length+' of '+board.cards.length,
  'Comparison baseline: '+(baseline?baseline.sha+' observed '+baseline.observed_at_utc:
   'not loaded; no historical comparison'),
  'Status: observational, claims and releases NOT AUTHORIZED.',
  'MANDATORY: verify active owner/claim, exact-head required CI, security, review and release consent before action.',
  ''
 ];
 let group=null;
 rows.forEach(card=>{
  if(card.project!==group){group=card.project;lines.push('## Project '+line(group,80),'');}
  const human=handoffs.get(card.issue);
  lines.push('### '+line(card.issue,110)+' · '+line(card.priority,20),
    'Original: '+card.url,
    'Original issue title: '+line(card.title,180),
    'Human disposition: '+line(human?.decision||'investigate',40)+' (not an owner claim)',
    'Manual follow-up contact: '+line(human?.owner||'not entered',80)+' (label only, not a GitHub or Notion assignee)',
    'Manual UTC review date: '+(human?.due||'not entered')+' · '+
      (manualGateDecisions.has(human?.decision)?dueState(human):'not a human escalation'),
    'Manual follow-up lifecycle: '+(human?.followup_status==='local-complete'?
      'CLOSED LOCALLY — original issue still UNVERIFIED':'open / no completion proof'),
    'Last local toggle UTC: '+(human?.followup_changed_at_utc||'not recorded')+
      ' (device clock only, not an external receipt)',
    'Human handoff provenance: '+(human?.carried_from?
      'manually carried from older board SHA256 '+human.carried_from+'; REVERIFY owner and gate':
      'entered on current board or not provided'),
    'Claim context: '+line(card.claim_context,100),
    'Observed PRs: '+(card.open_pr_candidates.length?
      card.open_pr_candidates.join(' | '):'none in bounded snapshot'),
    'Missing evidence: '+(card.evidence_gaps.length?
      card.evidence_gaps.map(g=>line(g,300)).join('; '):'live owner, exact CI and release gates remain unverified'),
    'Recommended investigation: '+line(card.recommended_next_step,650));
  if(human?.note)lines.push('Private human note: '+line(human.note,1000));
  lines.push('');
 });
 // Portfolio-wide queue pressure is independent of the issue-inbox sample.
 // Include every admitted project lead, even projects with zero issue cards.
 // This is historical operator evidence, never current claim/worker authority.
 if(recoveryRows.length){
  lines.push('## Project-level recovery leads · '+(selectedRecoveryProject?
    'SELECTED saved project '+selectedRecoveryProject:'FULL saved portfolio snapshot'),
    'Project filter applies to project leads. Search, priority and disposition filters apply only to issue cards.',
    'Queue values are sampled claim cycles, not failures.',
    'Project count: '+recoveryRows.length,
    '');
  recoveryRows.forEach(row=>{
   const totals=row.issue_counts_in_sample;
   const pressure=row.queue_observation;
   lines.push('### Project '+line(row.project,80),
     'GitHub issue source coverage: '+line(row.issue_coverage,30)+' (bounded/incomplete)',
     'P0/P1/P2/unspecified sampled issues: '+totals.P0+'/'+totals.P1+'/'+
       totals.P2+'/'+totals.Unspecified,
     'Queue source: '+(pressure?
       pressure.active_items+' sampled rows; '+pressure.high_claim_attempts+
       ' high claim cycles; '+pressure.expired_active_leases+' expired leases':
       'unavailable; current worker state UNKNOWN'),
     'Original queue source observed UTC: '+
       (row.queue_source_observed_at_utc||'unavailable')+
       ' (not current worker or lease status)',
     'Observed claim context: '+line(row.claim_context,50)+
       ' (not a present owner lease)',
     'Human investigation: '+line(row.recommended_next_step,650),
     'Local operator decision: '+line(recoveryHandoffs.get(row.project)?.decision||'investigate',40)+
       ' (human note only, never a claim)',
     'Private operator note: '+line(recoveryHandoffs.get(row.project)?.note||'not entered',500),
     'Required: independently verify current claims, exact-head CI, review and release permission.',
     '');
  });
 }
 if(baseline){
  const diff=changesSinceBaseline();
  lines.push('## Historical source comparison (not resolution evidence)','',
    'Current/previous issue visibility may differ due to coverage, permissions or bounded collectors.',
    'Earlier coverage: '+(baseline.coverage.inbox_truncated||baseline.coverage.cards_truncated||baseline.coverage.github_denial?
      'truncated, bounded or denied':'bounded partial')+
      '; current coverage: '+(board.coverage.inbox_truncated||board.coverage.cards_truncated||board.coverage.github_denial?
       'truncated, bounded or denied':'bounded partial')+'.',
    ...['new-observation','changed-evidence','still-observed','not-observed'].map(kind=>
     kind+': '+diff.filter(item=>item.kind===kind).map(item=>
      line(item.issue,110)+(item.changedFields?.length?
       ' ['+item.changedFields.map(x=>line(x,100)).join('; ')+']':'' )).join(', ')),
    '');
 }
 lines.push('No live reassignment, dispatch, queue mutation or production readiness is asserted.','');
 return lines.join('\\n');
}
function calendarEscape(value){
 const slash=String.fromCharCode(92);
 return Array.from(String(value)).map(ch=>
   ch===slash?slash+slash:
   ch===','||ch===';'?slash+ch:
   ch==='\\n'||ch==='\\r'?slash+'n':ch).join('');
}
function foldCalendarLine(value){
 const encoder=new TextEncoder();
 const lines=[];let current='',size=0;
 for(const ch of value){
   const n=encoder.encode(ch).length;
   if(size+n>75){lines.push(current);current=' ';size=1;}
   current+=ch;size+=n;
 }
 lines.push(current);return lines;
}
function calendarSource(stamp){
 const includePrivateContacts=document.getElementById('calendarIncludeContact').checked;
 const rows=board.cards.filter(c=>{
  const info=handoffs.get(c.issue);
  return manualGateDecisions.has(info?.decision)&&
   info.followup_status!=='local-complete'&&
   Boolean(info?.owner?.trim()&&info?.due&&validFollowupDate(info.due));
 }).sort((a,b)=>{
  const da=handoffs.get(a.issue).due,db=handoffs.get(b.issue).due;
  return da.localeCompare(db)||a.issue.localeCompare(b.issue);
 }).slice(0,60);
 if(!rows.length)return null;
 const lines=['BEGIN:VCALENDAR','VERSION:2.0',
  'PRODID:-//zSentinel//Private Manual Followup//EN',
  'CALSCALE:GREGORIAN','METHOD:PUBLISH'];
 for(const card of rows){
  const info=handoffs.get(card.issue);
  const uid='zsentinel-'+boardSha.slice(0,24)+'-'+
    card.issue.replace(/[^A-Za-z0-9]/g,'-')+'@local.invalid';
  const desc='Human review only, not an assigned task or CI/release approval. '+
    'Original source observed '+board.observed_at_utc+'. Issue: '+card.url+
    (includePrivateContacts?'. Manually entered private contact label: '+info.owner:
     '. Private contact label intentionally omitted')+
    (info.carried_from?'. Imported from earlier source SHA256 '+info.carried_from+
     ' (requires renewed owner review)':'')+
    '. Check original live ownership and gates.';
  lines.push('BEGIN:VEVENT','UID:'+uid,
   'DTSTAMP:'+stamp,'DTSTART;VALUE=DATE:'+info.due.replace(/-/g,''),
   'SUMMARY:'+calendarEscape('Review '+card.issue+' · '+card.title),
   'DESCRIPTION:'+calendarEscape(desc),'URL:'+card.url,'END:VEVENT');
 }
 lines.push('END:VCALENDAR');
 return lines.flatMap(foldCalendarLine).join('\\r\\n')+'\\r\\n';
}
function drawList(){
 const currentProject=document.getElementById('project').value;
 let recoveryCount=0;
 document.querySelectorAll('[data-recovery-project]').forEach(node=>{
  const show=!currentProject||node.getAttribute('data-recovery-project')===currentProject;
  node.hidden=!show;
  if(show)recoveryCount++;
 });
 const recoveryCounter=document.getElementById('recoveryVisibleCount');
 if(recoveryCounter)recoveryCounter.textContent=String(recoveryCount);
 const view=filtered();issueList.replaceChildren();
 view.forEach(c=>{
   const li=elem('li'),b=elem('button');b.type='button';
   b.setAttribute('aria-current',c.issue===selected?'true':'false');
   b.append(elem('span',c.issue,'issue-id'),elem('span',c.title),
     elem('span',c.priority+' · '+c.project,'meta'));
   b.onclick=()=>openObservedIssue(c.issue);li.append(b);issueList.append(li);
 });
 document.getElementById('info').textContent=board.cards.length+' bounded issue cards · '+view.length+
   ' shown · observed '+board.observed_at_utc+' · no active-claim authorization';
 if(selected&&!view.some(c=>c.issue===selected)){saveSelection();selected=null;drawDetail()}
 const at=view.findIndex(c=>c.issue===selected);
 document.getElementById('navPosition').textContent=at>=0?
   'Observed card '+(at+1)+' of '+view.length+' · '+selected:
   view.length+' observed cards · select or navigate';
 document.getElementById('navPrev').disabled=!view.length;
 document.getElementById('navNext').disabled=!view.length;
 document.getElementById('navUrgent').disabled=!view.some(c=>
   (c.priority==='P0'||c.priority==='P1')&&
   handoffs.get(c.issue)?.followup_status!=='local-complete');
 refreshTriage();refreshFollowups();refreshBatch();refreshPrDesk()
}
function drawDetail(){
 detail.replaceChildren();
 if(!selected||!entries.has(selected)){
   detail.append(elem('h2','Select an observed product issue'),elem('p','No action has been authorized.'));
   return;
 }
 const card=entries.get(selected);
 detail.append(elem('small',card.priority+' · '+card.project),
   elem('h2',card.title),elem('p',card.issue,'code'),
   link('Open original GitHub issue ↗',card.url),
   elem('p','Owner status: '+card.claim_context+' · source coverage: '+card.issue_coverage,'meta'),
   elem('h3','Proposed next investigation'),elem('p',card.recommended_next_step));
 const prs=elem('div');prs.append(elem('h3','Existing PR references (not ownership proof)'));
 if(!card.open_pr_candidates.length)prs.append(elem('p','No candidate PR in this bounded snapshot.'));
 card.open_pr_candidates.forEach(u=>prs.append(elem('p')).lastChild.append(link(u,u)));
 detail.append(prs);
 const gaps=elem('div');gaps.append(elem('h3','Missing evidence and gates'));
 const ul=elem('ul');
 card.evidence_gaps.forEach(x=>ul.append(elem('li',x)));
 if(!card.evidence_gaps.length)ul.append(elem('li','Live claim, required review and release rights still need independent verification.'));
 gaps.append(ul);detail.append(gaps);
 const summary=elem('section');
 summary.setAttribute('aria-label','Three-line source-bound blocker handoff');
 summary.append(elem('h3','Three-line blocker handoff · human-only'),
  elem('p','Review before sharing. This is not permission to execute.'));
 const immediateGate=card.evidence_gaps.length?
  card.evidence_gaps[0]:'Owner/claim, required CI, independent review and release gates unverified.';
 const compact=[
  'Problem · '+line(card.priority+' '+card.issue+' — '+card.title,280),
  'Next investigation (not an action) · '+line(card.recommended_next_step,320),
  'Gate / status · '+line(immediateGate,260)+' · observed '+line(board.observed_at_utc,45)+
   ' · NOT AUTHORIZED'
 ].join('\\n');
 const snippet=elem('textarea');snippet.readOnly=true;snippet.rows=5;
 snippet.value=compact;snippet.setAttribute('aria-label','Read-only three-line blocker summary');
 const copy=elem('button','Select three lines for manual copy');copy.type='button';
 copy.onclick=()=>{snippet.focus();snippet.select();
  status.textContent='Three source-bound lines selected for manual copy; nothing was submitted.';};
 summary.append(snippet,copy);
 detail.append(summary);
 const label=elem('label','Operator disposition (non-authorizing)');label.htmlFor='decision';
 const sel=elem('select');sel.id='decision';
 [['investigate','Investigate product issue'],['integrate-existing-pr','Inspect/integrate existing PR'],
  ['coordinate-owner','Coordinate current owner'],['park-blocker','Park external blocker'],
  ['ready-for-new-product-work','Candidate for new product implementation (verify live claims)']]
 .forEach(([v,t])=>{const o=elem('option',t);o.value=v;sel.append(o)});
 const ownerLabel=elem('label','Manual follow-up contact label (not an assignee, max 80 characters)');
 ownerLabel.htmlFor='followupOwner';
 const owner=elem('input');owner.type='text';owner.id='followupOwner';owner.maxLength=80;
 owner.setAttribute('autocomplete','off');
 const dueLabel=elem('label','Human-only next review date (UTC, no automated reminder)');
 dueLabel.htmlFor='followupDue';
 const due=elem('input');due.type='date';due.id='followupDue';due.min='2020-01-01';due.max='2099-12-31';
 const nlabel=elem('label','Private operator note (max 1000 characters)');nlabel.htmlFor='note';
 const note=elem('textarea');note.id='note';note.maxLength=1000;note.rows=5;
 const saved=handoffs.get(selected);
 sel.value=saved?.decision||'investigate';note.value=saved?.note||'';
 owner.value=saved?.owner||'';due.value=saved?.due||'';
 if(saved?.carried_from)detail.append(elem('p',
   'Imported from older source '+saved.carried_from.slice(0,12)+
   '… · reassess owner, date, CI and original issue before action.','notice'));
 detail.append(label,sel,ownerLabel,owner,dueLabel,due,nlabel,note);
 const progress=elem('section');
 progress.setAttribute('aria-label','Manual human follow-up completion');
 progress.append(elem('h3','Local human follow-up status'),
   elem('p','Closing only hides this manual follow-up from future calendar exports. The GitHub issue, owner, CI and release status remain UNVERIFIED.'));
 const state=elem('p','Current: '+(saved?.followup_status==='local-complete'?
   'Locally closed (not issue resolved)':'Open / manual review required')+
   (saved?.followup_changed_at_utc?
    ' · last local toggle '+saved.followup_changed_at_utc+' (device clock, NOT external receipt)':''));
 const switcher=elem('button',saved?.followup_status==='local-complete'?
   'Reopen local human follow-up':'Mark human follow-up locally complete');
 switcher.type='button';switcher.disabled=!manualGateDecisions.has(sel.value);
 switcher.onclick=()=>{
   saveSelection();
   const row=handoffs.get(selected);
   if(!manualGateDecisions.has(row?.decision))return;
   row.followup_status=row.followup_status==='local-complete'?'open':'local-complete';
   row.followup_changed_at_utc=new Date().toISOString();
   setUnsaved(true);invalidateBrief();drawList();drawDetail();
   status.textContent=row.followup_status==='local-complete'?
     'Only the private follow-up was locally closed; GitHub issue and gate statuses remain unknown.':
     'Private human follow-up reopened; review date and contact label preserved.';
 };
 progress.append(state,switcher);detail.append(progress);
 const update=()=>{saveSelection();invalidateBrief();refreshTriage();refreshFollowups();
   switcher.disabled=!manualGateDecisions.has(sel.value);};
 sel.addEventListener('change',()=>{update();drawList()});
 note.addEventListener('input',update);
 owner.addEventListener('input',update);
 due.addEventListener('change',update);
}
function openObservedIssue(issue){
 if(!entries.has(issue)||!filtered().some(card=>card.issue===issue)){
   status.textContent='Issue is outside current saved-source filters; no current live issue assumed.';
   return;
 }
 saveSelection();selected=issue;invalidateBrief();drawList();drawDetail();
 detail.scrollIntoView({block:'start',behavior:'smooth'});
 status.textContent='Opened '+issue+' from saved PR associations. '+
  'Human notes remain local; live owner and required CI are unverified.';
}
function navigateObserved(direction,urgentOnly=false){
 saveSelection();
 const visible=filtered();
 const items=urgentOnly?visible.filter(card=>
   (card.priority==='P0'||card.priority==='P1')&&
   handoffs.get(card.issue)?.followup_status!=='local-complete'):visible;
 if(!items.length){
   status.textContent=urgentOnly?
    'No open P0/P1 follow-ups in the current bounded filters; live project state remains unknown.':
    'No cards in the current saved-source filters.';
   return;
 }
 const at=items.findIndex(card=>card.issue===selected);
 const next=at<0?(direction<0?items.length-1:0):
   (at+direction+items.length)%items.length;
 selected=items[next].issue;
 invalidateBrief();drawList();drawDetail();
 status.textContent=(urgentOnly?'Open urgent human follow-up: ':'Observed issue: ')+
   selected+'. Current ownership, required CI and release gates remain unverified.';
}
document.getElementById('navPrev').onclick=()=>navigateObserved(-1);
document.getElementById('navNext').onclick=()=>navigateObserved(1);
document.getElementById('navUrgent').onclick=()=>navigateObserved(1,true);
document.addEventListener('keydown',event=>{
 if(event.defaultPrevented||event.altKey||event.ctrlKey||event.metaKey||
    event.shiftKey||event.repeat)return;
 const active=document.activeElement;
 if(active&&(active.isContentEditable||
    ['INPUT','TEXTAREA','SELECT'].includes(active.tagName)))return;
 if(event.key==='j'||event.key==='k'){
   event.preventDefault();navigateObserved(event.key==='j'?1:-1);
 }
});
function syncRecoveryControls(){
 document.querySelectorAll('[data-recovery-choice]').forEach(select=>{
  select.value=recoveryHandoffs.get(select.dataset.recoveryChoice)?.decision||'investigate';
 });
 document.querySelectorAll('[data-recovery-note]').forEach(area=>{
  area.value=recoveryHandoffs.get(area.dataset.recoveryNote)?.note||'';
 });
}
document.getElementById('projectRecoveryDesk').addEventListener('input',event=>{
 const target=event.target;
 const project=target?.dataset?.recoveryChoice||target?.dataset?.recoveryNote;
 if(!project||!board.project_operations.some(item=>item.project===project))return;
 const old=recoveryHandoffs.get(project)||{decision:'investigate',note:''};
 const next={...old};
 if(target.dataset.recoveryChoice){
  if(!recoveryDecisions.has(target.value))return;
  next.decision=target.value;
 }else{
  next.note=target.value.slice(0,500);
 }
 if(next.decision==='investigate'&&!next.note)recoveryHandoffs.delete(project);
 else recoveryHandoffs.set(project,next);
 setUnsaved(true);invalidateBrief();
 status.textContent='Local project follow-up changed; export private handoff to preserve it.';
});
['search','priority','project','disposition','change','followupStatus'].forEach(id=>document.getElementById(id).addEventListener(
 'input',()=>{saveSelection();invalidateBrief();drawList();drawDetail()}));
document.getElementById('export').onclick=()=>{
 saveSelection();
 const rows=[...handoffs].filter(([id,v])=>entries.has(id)&&permitted.has(v.decision))
 .map(([issue,value])=>({issue,decision:value.decision,note:value.note.slice(0,1000),
   owner:value.owner||'',due:value.due||'',carried_from:value.carried_from||'',
   followup_status:value.followup_status||'open',
   followup_changed_at_utc:value.followup_changed_at_utc||''}));
 const projects=[...recoveryHandoffs].filter(([project,v])=>
   board.project_operations.some(row=>row.project===project)&&recoveryDecisions.has(v.decision))
  .map(([project,v])=>({project,decision:v.decision,note:v.note.slice(0,500)}));
 const payload={schema_version:4,kind:'zsentinel-private-human-delivery-handoff',
   source_board_sha256:boardSha,source_observed_at_utc:board.observed_at_utc,
   authorizes_claim:false,authorizes_merge:false,authorizes_deploy:false,
   entries:rows,project_recovery:projects};
 const uri=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)+'\\n'],{type:'application/json'}));
 const a=elem('a');a.href=uri;a.download='zsentinel-human-handoff.json';a.click();
 setTimeout(()=>URL.revokeObjectURL(uri),1000);
 setUnsaved(false);
 status.textContent='Private source-bound handoff downloaded. Nothing was submitted.';
};
document.getElementById('baseline').addEventListener('change',async event=>{
 const file=event.currentTarget.files?.[0];if(!file)return;
 try{
  if(file.size<4||file.size>2097152)throw Error('file bounds');
  if(!globalThis.crypto?.subtle)throw Error('SHA-256 unavailable');
  const buf=await file.arrayBuffer();
  const txt=new TextDecoder('utf-8',{fatal:true}).decode(buf);
  const value=JSON.parse(txt);
  const checked=acceptBaseline(value);
  checked.sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buf)),
    byte=>byte.toString(16).padStart(2,'0')).join('');
  if(checked.sha===boardSha)throw Error('baseline and current board are identical');
  baseline=checked;clearCarryPreview('Earlier source loaded. Select its matching private handoff to preview carry-forward.');
  invalidateBrief();refreshComparison();drawList();drawDetail();
  status.textContent='Earlier bounded source loaded locally. Missing issues are not proven resolved.';
 }catch{
  status.textContent='Previous source rejected: invalid, unsafe, future, or not earlier than this board.';
 }finally{event.currentTarget.value='';}
});
document.getElementById('clearBaseline').onclick=()=>{
 baseline=null;clearCarryPreview('Earlier source cleared; no pending carry-forward.');
  invalidateBrief();refreshComparison();drawList();drawDetail();
 status.textContent='Earlier source comparison cleared.';
};
document.getElementById('carryFile').addEventListener('change',async event=>{
 const file=event.currentTarget.files?.[0];if(!file)return;
 clearCarryPreview('Reviewing private prior handoff.');
 try{
  if(!baseline||file.size<4||file.size>65536)throw Error('source or file bounds');
  const data=JSON.parse(await file.text());
  previewCarry(data);
  status.textContent='Inspect prior human-only annotations, then explicitly confirm if appropriate.';
 }catch{
  clearCarryPreview('Earlier handoff rejected: incorrect source, malformed values or unauthorized provenance.');
  status.textContent='Carry-forward refused without exact earlier source and validated human-only handoff.';
 }finally{event.currentTarget.value='';}
});
document.getElementById('carryApply').onclick=()=>{
 if(!carryPreview||!baseline||carryPreview.sha!==baseline.sha){
  clearCarryPreview('Earlier board changed; load matching prior handoff again.');return;
 }
 if(carryPreview.eligible.some(x=>!emptyManual(handoffs.get(x.issue)))){
  clearCarryPreview('Current annotations changed; preview again. Existing work preserved.');return;
 }
 const count=carryPreview.eligible.length;
 for(const row of carryPreview.eligible)handoffs.set(row.issue,row.value);
 clearBatchUndo();setUnsaved(true);
 clearCarryPreview('Transferred '+count+' reviewed, human-only annotations. All live owners/gates must be reverified.');
 invalidateBrief();drawList();drawDetail();
 status.textContent='Manually reviewed notes carried forward; no Notion claim or external action performed.';
};
document.getElementById('restore').addEventListener('change',async event=>{
 const file=event.currentTarget.files?.[0];if(!file)return;
 if(unsavedLocal&&!window.confirm(
   'Restoring this handoff replaces unsaved private annotations in this tab. Download a private JSON handoff first unless you want to discard those edits. Continue?')){
  event.currentTarget.value='';
  status.textContent='Import cancelled. Your current local annotations were preserved.';
  return;
 }
 try{
   if(file.size<4||file.size>65536)throw Error('size');
   const data=JSON.parse(await file.text());
   if(!data||Array.isArray(data)||![1,2,3,4].includes(data.schema_version)||
      data.kind!=='zsentinel-private-human-delivery-handoff'||
      data.source_board_sha256!==boardSha||
      data.source_observed_at_utc!==board.observed_at_utc||
      data.authorizes_claim!==false||data.authorizes_merge!==false||
      data.authorizes_deploy!==false||!Array.isArray(data.entries)||
      data.entries.length>60)throw Error('invalid identity');
   const next=new Map();
   for(const v of data.entries){
     if(!v||Array.isArray(v)||typeof v.issue!=='string'||!entries.has(v.issue)||
         next.has(v.issue)||!permitted.has(v.decision)||
         typeof v.note!=='string'||v.note.length>1000||
         new TextEncoder().encode(v.note).length>4000||
         [...v.note].some(ch=>ch.codePointAt(0)<32&&ch!=='\\n'&&ch!=='\\t')||
         (data.schema_version>=2&&(
          typeof v.owner!=='string'||v.owner.length>80||
          [...v.owner].some(ch=>ch.codePointAt(0)<32||ch==='<'||ch==='>')||
          !validFollowupDate(v.due)||
          (data.schema_version>=3&&(
           !['open','local-complete'].includes(v.followup_status)||
           (v.followup_status==='local-complete'&&!manualGateDecisions.has(v.decision))||
           (v.followup_changed_at_utc!==undefined&&
            !validLocalChangeTime(v.followup_changed_at_utc))))||
          (v.carried_from!==undefined&&
           (typeof v.carried_from!=='string'||
            (v.carried_from!==''&&!/^[a-f0-9]{64}$/.test(v.carried_from)))))))
         throw Error('malformed note/follow-up provenance');
     next.set(v.issue,{decision:v.decision,note:v.note,
       owner:data.schema_version>=2?v.owner:'',
       due:data.schema_version>=2?v.due:'',
       carried_from:data.schema_version>=2?(v.carried_from||''):'',
       followup_status:data.schema_version>=3?v.followup_status:'open',
       followup_changed_at_utc:data.schema_version>=3?(v.followup_changed_at_utc||''):''});
   }
   const recoveredProjects=new Map();
   if(data.schema_version===4){
    if(!Array.isArray(data.project_recovery)||data.project_recovery.length>60)
     throw Error('missing or oversized private project follow-up');
    for(const row of data.project_recovery){
     if(!row||Array.isArray(row)||typeof row.project!=='string'||
        !board.project_operations.some(item=>item.project===row.project)||
        recoveredProjects.has(row.project)||!recoveryDecisions.has(row.decision)||
        typeof row.note!=='string'||row.note.length>500||
        new TextEncoder().encode(row.note).length>2000||
        [...row.note].some(ch=>ch.codePointAt(0)<32&&ch!=='\\n'&&ch!=='\\t'))
      throw Error('Malformed or cross-project recovery note');
     recoveredProjects.set(row.project,{decision:row.decision,note:row.note});
    }
   }else if(data.project_recovery!==undefined){
    throw Error('Legacy handoff cannot insert project annotations');
   }
   handoffs.clear();next.forEach((v,k)=>handoffs.set(k,v));
   recoveryHandoffs.clear();recoveredProjects.forEach((v,k)=>recoveryHandoffs.set(k,v));
   syncRecoveryControls();
   clearBatchUndo();setUnsaved(false);invalidateBrief();drawList();drawDetail();
   status.textContent='Own issue and project follow-ups restored from exact-board private handoff; no live claim changed.';
 }catch{
   status.textContent='Handoff not restored: invalid, foreign or from another board snapshot.';
 }finally{event.currentTarget.value=''}
});



function clearPortableGatePreview(){
 document.getElementById('prGateBatchPreview').disabled=true;
 document.getElementById('prGateBatchPreviewPanel').hidden=true;
 document.getElementById('prGateBatchPreviewText').textContent='';
 document.getElementById('prGateBatchDownload').disabled=true;
}
document.getElementById('prGateBatchPreview').onclick=()=>{
 if(!prGateImport?.portableText)return;
 document.getElementById('prGateBatchPreviewText').textContent=prGateImport.portableText;
 document.getElementById('prGateBatchPreviewPanel').hidden=false;
 document.getElementById('prGateBatchDownload').disabled=false;
 status.textContent='Review this exact offline combined JSON before downloading; no external action.';
};
document.getElementById('prGateBatchDownload').onclick=()=>{
 const data=prGateImport?.portableText;
 if(!data||document.getElementById('prGateBatchPreviewPanel').hidden||
    document.getElementById('prGateBatchPreviewText').textContent!==data){
  clearPortableGatePreview();status.textContent='Source changed; preview before download.';
  return;
 }
 const url=URL.createObjectURL(new Blob([data],{type:'application/json;charset=utf-8'}));
 const linkEl=elem('a');linkEl.href=url;
 linkEl.download='zsentinel-offline-source-bound-pr-gates.json';linkEl.click();
 setTimeout(()=>URL.revokeObjectURL(url),1000);
 status.textContent='Private offline combined source downloaded; no GitHub, claim or merge action.';
};
document.getElementById('prGateOlderFile').addEventListener('change',async event=>{
 const file=event.currentTarget.files?.[0];
 try{
  if(!prGateImport||!file||file.size<2||file.size>1048576||
     !globalThis.crypto?.subtle)throw Error('Older source missing or out of bounds');
  const bytes=await file.arrayBuffer();
  const source=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
  const accepted=acceptGateSource(source);
  accepted.sha256=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),
   b=>b.toString(16).padStart(2,'0')).join('');
  if(accepted.timestamp>=prGateImport.timestamp)
   throw Error('Earlier evidence must predate currently imported source');
  prGatePrevious=accepted;
  const delta=currentGateDelta(),list=document.getElementById('prGateDeltaRows');
  list.replaceChildren();
  for(const c of delta.changes){
   const li=elem('li');
   li.append(link(c.url+' ↗',c.url),elem('small',
    c.from+' -> '+c.to+' · '+c.meaning));
   list.append(li);
  }
  if(!delta.changes.length)list.append(elem('li',
   'No sampled changes found; NOT proof of current required CI or review.'));
  document.getElementById('prGateDeltaSummary').textContent=
   'Historical saved observation comparison '+delta.older.time+' -> '+delta.newer.time+
   ': '+delta.changes.length+' changed samples, '+delta.unchanged+
   ' unchanged, '+delta.headChanged+' observed HEAD changes. NOT live evidence.';
  gateDeltaPreview=null;
  document.getElementById('prGateDeltaPanel').hidden=true;
  document.getElementById('prGateDeltaText').textContent='';
  document.getElementById('prGateDeltaDownload').disabled=true;
  document.getElementById('prGateDeltaPreview').disabled=false;
  status.textContent='Two local saved observations compared; no GitHub call or merge authority.';
 }catch{
  status.textContent='Older gate source refused: use a compatible, earlier saved snapshot. Current gate overlay unchanged.';
 }finally{event.currentTarget.value='';}
});
document.getElementById('prGateDeltaPreview').onclick=()=>{
 if(!prGatePrevious||!prGateImport)return;
 try{
  gateDeltaPreview=gateDeltaMarkdown();
  document.getElementById('prGateDeltaPanel').hidden=false;
  document.getElementById('prGateDeltaText').textContent=gateDeltaPreview;
  document.getElementById('prGateDeltaDownload').disabled=false;
  status.textContent='Review the historical local PR change report; no external action.';
 }catch{clearGateDelta();status.textContent='Saved source changed. Import earlier evidence again.';}
};
document.getElementById('prGateDeltaDownload').onclick=()=>{
 if(!gateDeltaPreview)return;
 try{
  if(gateDeltaMarkdown()!==gateDeltaPreview)
   throw Error('Source moved since preview');
  const uri=URL.createObjectURL(new Blob([gateDeltaPreview],
   {type:'text/markdown;charset=utf-8'}));
  const a=elem('a');a.href=uri;a.download='zsentinel-historical-pr-gate-delta.md';a.click();
  setTimeout(()=>URL.revokeObjectURL(uri),1000);
  status.textContent='Historical comparison saved privately. Required merge/release gates remain UNKNOWN.';
 }catch{clearGateDelta();status.textContent='Historical source changed; preview again before download.';}
};
document.getElementById('prGateBatchApply').onclick=async()=>{
 const original=document.getElementById('prGateReviewFile');
 const selected=document.getElementById('prGateBatchFiles');
 try{
  const combined=await combineSavedGateFiles(original.files?.[0],
   [...(selected.files||[])]);
  // All files were admitted and cross-matched before replacing any state.
  prGateImport=combined;clearGateDelta();invalidateBrief();refreshPrDesk();clearPortableGatePreview();
  document.getElementById('prGateBatchPreview').disabled=false;
  document.getElementById('prGateClear').disabled=false;
  const coverage=combined.bridgeCoverage;
  document.getElementById('prGateStatus').textContent=
   'OFFLINE '+(coverage.complete?'COMPLETE':'PARTIAL')+
   ' saved batch import · '+coverage.batches+'/'+coverage.expected_batches+
   ' batches · '+combined.items.size+'/'+coverage.candidate_count+
   ' original PRs observed · UNKNOWN batches '+
   (coverage.missing_indexes.join(', ')||'none')+
   ' · oldest observation '+combined.time+
   ' · exact original review SHA '+coverage.manifest_sha256.slice(0,12)+
   ' · current required CI, independent review and owner permission UNKNOWN.';
  status.textContent='Local batch review imported; no GitHub requests or actions performed.';
 }catch{
  status.textContent='Offline batch combination refused; current imported gate evidence preserved.';
 }finally{original.value='';selected.value='';}
};
document.getElementById('prGateImport').addEventListener('change',async event=>{
 const file=event.currentTarget.files?.[0];
 try{
  if(!file||file.size<2||file.size>1048576)throw Error('Invalid gate file size');
  const bytes=await file.arrayBuffer();
  const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
  const accepted=acceptGateSource(value);
  accepted.sha256=Array.from(
   new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),
   byte=>byte.toString(16).padStart(2,'0')).join('');
  prGateImport=accepted;
  clearGateDelta();clearPortableGatePreview();invalidateBrief();refreshPrDesk();
  document.getElementById('prGateClear').disabled=false;
  document.getElementById('prGateStatus').textContent=
   accepted.items.size+' linked PR receipts imported · '+accepted.time+
   (accepted.bridgeCoverage?' · '+
    (accepted.bridgeCoverage.complete?'COMPLETE':'PARTIAL')+
    ' SAVED BATCH SET '+accepted.bridgeCoverage.batches+'/'+
    accepted.bridgeCoverage.expected_batches+
    (accepted.bridgeCoverage.missing_indexes.length?' · UNKNOWN BATCHES '+
     accepted.bridgeCoverage.missing_indexes.slice(0,16).join(', '):'')+
    ' · manifest '+accepted.bridgeCoverage.manifest_sha256.slice(0,12)+
    ' · REQUIRED CI/OWNER/REVIEW STILL UNKNOWN':'')+
   (accepted.reviewContext?' · '+accepted.reviewContext.candidate_count+
    ' candidates in reviewed board; batch '+
    accepted.reviewContext.batch_index+'/'+accepted.reviewContext.batches_total+
    ' examined; other batches NOT inspected by this JSON':'')+
   ' · JSON SHA256 '+accepted.sha256.slice(0,12)+
   (Date.now()-accepted.timestamp>900000?
    ' · STALE saved source':' · recent observation, NOT live ownership')+
   '. Never merge without current mandatory checks, approvals and authority.';
  status.textContent='Read-only PR evidence imported locally; no GitHub action performed.';
 }catch{
  status.textContent='Gate import refused; existing overlay (if any) kept unchanged.';
 }finally{event.currentTarget.value='';}
});
document.getElementById('prGateFocus').onchange=()=>{
 invalidateBrief();refreshPrDesk();
};
document.getElementById('prGateClear').onclick=()=>{
 prGateImport=null;clearGateDelta();clearPortableGatePreview();invalidateBrief();refreshPrDesk();
 document.getElementById('prGateClear').disabled=true;
 document.getElementById('prGateStatus').textContent=
  'Imported gate source cleared. Current PR gates unknown.';
};
document.getElementById('previewPrManifest').onclick=()=>{
 saveSelection();
 viewedPrManifest=currentPrManifest();
 document.getElementById('prManifestPanel').hidden=!viewedPrManifest;
 document.getElementById('prManifestContent').textContent=viewedPrManifest||'';
 document.getElementById('downloadPrManifest').disabled=!viewedPrManifest;
 status.textContent=viewedPrManifest?
  'Review the complete source-bound JSON; it authorizes no action.':
  'No issues in this captured filter window.';
};
document.getElementById('downloadPrManifest').onclick=()=>{
 if(!viewedPrManifest)return;
 saveSelection();
 if(currentPrManifest()!==viewedPrManifest){
  invalidateBrief();
  status.textContent='Review manifest changed; preview again before exporting.';
  return;
 }
 const uri=URL.createObjectURL(new Blob([viewedPrManifest],{type:'application/json;charset=utf-8'}));
 const a=elem('a');a.href=uri;a.download='zsentinel-offline-merge-first-review.json';a.click();
 setTimeout(()=>URL.revokeObjectURL(uri),1000);
 status.textContent='Source manifest downloaded manually. No external claim, merge or dispatch.';
 invalidateBrief();
};
document.getElementById('previewPrDesk').onclick=()=>{
 saveSelection();
 viewedPrDesk=currentPrDesk();
 document.getElementById('prDeskPanel').hidden=!viewedPrDesk;
 document.getElementById('prDeskContent').textContent=viewedPrDesk||'';
 document.getElementById('downloadPrDesk').disabled=!viewedPrDesk;
 status.textContent=viewedPrDesk?
  'Review all captured PR references and issue links; none is verified mergeable.':
  'No captured PR references under these filters.';
};
document.getElementById('downloadPrDesk').onclick=()=>{
 if(!viewedPrDesk)return;
 saveSelection();
 if(currentPrDesk()!==viewedPrDesk){
  invalidateBrief();
  status.textContent='PR handoff changed. Preview it again before download.';
  return;
 }
 const uri=URL.createObjectURL(new Blob([viewedPrDesk],{type:'text/markdown;charset=utf-8'}));
 const a=elem('a');
 a.href=uri;a.download='zsentinel-merge-first-private-review.md';a.click();
 setTimeout(()=>URL.revokeObjectURL(uri),1000);
 status.textContent='Reviewed snapshot PR handoff downloaded; no GitHub claim or merge made.';
 invalidateBrief();
};
document.getElementById('previewBrief').onclick=()=>{
 saveSelection();
 viewedBrief=currentBrief();
 const panel=document.getElementById('briefPanel');
 panel.hidden=!viewedBrief;
 document.getElementById('briefContent').textContent=viewedBrief||'';
 document.getElementById('downloadBrief').disabled=!viewedBrief;
 status.textContent=viewedBrief?
  'Read the complete private briefing and choose your own destination. No action was sent.':
  'No currently visible issue cards; no briefing produced.';
};
document.getElementById('downloadBrief').onclick=()=>{
 if(!viewedBrief)return;
 saveSelection();
 const refreshed=currentBrief();
 if(refreshed!==viewedBrief){
  invalidateBrief();
  status.textContent='Briefing changed. Preview again before downloading.';
  return;
 }
 const project=document.getElementById('project').value.replace(/[^a-z0-9-]/gi,'-').slice(0,50);
 const uri=URL.createObjectURL(new Blob([viewedBrief],{type:'text/markdown;charset=utf-8'}));
 const a=elem('a');a.href=uri;
 a.download='zsentinel-private-'+(project||'portfolio')+'-handoff.md';a.click();
 setTimeout(()=>URL.revokeObjectURL(uri),1000);
 status.textContent='Private Markdown saved locally. It does not authorize any worker action.';
 invalidateBrief();
};
// A two-observation transport, including vanished-but-not-proven-resolved issues.
function deltaCsvSource(){
 if(!baseline||!baseline.sha)return null;
 const cols=['previous_board_sha256','current_board_sha256',
  'previous_observed_at_utc','current_observed_at_utc','change_kind',
  'issue','original_issue_url','priority_previous','priority_current',
  'project_previous','project_current','changed_source_fields',
  'previous_project_coverage','current_project_coverage',
  'previous_snapshot_partial_or_denied','current_snapshot_partial_or_denied',
  'not_observed_proves_resolved','claim_authorized','merge_authorized','deploy_authorized'];
 const oldPartial=baseline.coverage.inbox_truncated||
  baseline.coverage.cards_truncated||baseline.coverage.github_denial;
 const newPartial=board.coverage.inbox_truncated||
  board.coverage.cards_truncated||board.coverage.github_denial;
 const rows=changesSinceBaseline().map(change=>{
  const before=baseline.items.get(change.issue),after=entries.get(change.issue);
  const projectBefore=before?.project||'',projectAfter=after?.project||'';
  const parts=change.issue.split('#');
  return [baseline.sha,boardSha,baseline.observed_at_utc,board.observed_at_utc,
   change.kind,change.issue,'https://github.com/'+parts[0]+'/issues/'+parts[1],
   before?.priority||'',after?.priority||'',projectBefore,projectAfter,
   change.changedFields?.join(' | ')||'',
   baseline.coverage.projects[projectBefore]||'unavailable',
   board.coverage.projects[projectAfter]||'unavailable',
   String(oldPartial),String(newPartial),'false','false','false','false'];
 });
 if(!rows.length)return null;
 return [cols,...rows].map(row=>row.map(csvCell).join(',')).join('\\r\\n')+'\\r\\n';
}
document.getElementById('previewDeltaCsv').onclick=()=>{
 viewedDeltaCsv=deltaCsvSource();
 document.getElementById('deltaCsvPanel').hidden=!viewedDeltaCsv;
 document.getElementById('deltaCsvContent').textContent=viewedDeltaCsv||'';
 document.getElementById('downloadDeltaCsv').disabled=!viewedDeltaCsv;
 status.textContent=viewedDeltaCsv?
  'Review both exact-source SHA values and every change row. Missing is not resolved.':
  'No validated two-snapshot differences available for export.';
};
document.getElementById('downloadDeltaCsv').onclick=()=>{
 if(!viewedDeltaCsv)return;
 if(deltaCsvSource()!==viewedDeltaCsv){
  invalidateBrief();status.textContent='The observed comparison changed. Preview again.';return;
 }
 const uri=URL.createObjectURL(new Blob(['\\ufeff',viewedDeltaCsv],
  {type:'text/csv;charset=utf-8'}));
 const a=elem('a');a.href=uri;a.download='zsentinel-two-snapshot-changes.csv';a.click();
 setTimeout(()=>URL.revokeObjectURL(uri),1000);
 invalidateBrief();
 status.textContent='Private observed-change ledger saved; it proves no issue was fixed or released.';
};
// A reviewed, local-only export. It must never be mistaken for fresh claims.
function csvCell(value){
 let cell=String(value??'').replace(/[\\x00-\\x1f\\x7f]/g,' ').trim();
 // Quoting alone cannot prevent spreadsheet formula evaluation.
 if(/^[=+@-]/.test(cell))cell="'"+cell;
 return '"'+cell.replace(/"/g,'""')+'"';
}
function csvSource(){
 const privateFields=document.getElementById('csvIncludePrivate').checked;
 const columns=['source_board_sha256','source_observed_at_utc','priority','project',
  'issue','original_issue_url','issue_title','observed_pr_candidates',
  'observed_evidence_gaps','recommended_next_step','human_disposition',
  'private_followup_status','human_review_date_utc','snapshot_project_coverage',
  'snapshot_inbox_truncated','snapshot_cards_truncated','snapshot_github_denial',
  'claim_authorized','merge_authorized','deploy_authorized',
  ...(privateFields?['private_contact_label','private_operator_note']:[])];
 const rows=filtered().map(c=>{
  const v=handoffs.get(c.issue);
  return [boardSha,board.observed_at_utc,c.priority,c.project,c.issue,c.url,
   c.title,c.open_pr_candidates.join(' | '),c.evidence_gaps.join(' | '),
   c.recommended_next_step,v?.decision||'investigate',
   manualGateDecisions.has(v?.decision)?v?.followup_status||'open':'not-configured',
   manualGateDecisions.has(v?.decision)?v?.due||'':'',
   board.coverage.projects[c.project]||'unavailable',
   String(board.coverage.inbox_truncated),
   String(board.coverage.cards_truncated),String(board.coverage.github_denial),
   'false','false','false',...(privateFields?[v?.owner||'',v?.note||'']:[])];
 });
 if(!rows.length)return null;
 return [columns,...rows].map(r=>r.map(csvCell).join(',')).join('\\r\\n')+'\\r\\n';
}
document.getElementById('csvIncludePrivate').addEventListener('change',()=>{
 invalidateBrief();
 status.textContent='CSV privacy selection changed. Preview again before downloading.';
});
document.getElementById('previewCsv').onclick=()=>{
 saveSelection();
 viewedCsv=csvSource();
 document.getElementById('csvPanel').hidden=!viewedCsv;
 document.getElementById('csvContent').textContent=viewedCsv||'';
 document.getElementById('downloadCsv').disabled=!viewedCsv;
 status.textContent=viewedCsv?
  'Review CSV columns, source and rows. No spreadsheet service was contacted.':
  'No issues match the filters; no CSV generated.';
};
document.getElementById('downloadCsv').onclick=()=>{
 if(!viewedCsv)return;
 saveSelection();
 if(csvSource()!==viewedCsv){
  invalidateBrief();
  status.textContent='CSV rows or private annotations changed. Preview again.';
  return;
 }
 const project=document.getElementById('project').value.replace(/[^a-z0-9-]/gi,'-').slice(0,50);
 const uri=URL.createObjectURL(new Blob(['\\ufeff',viewedCsv],{type:'text/csv;charset=utf-8'}));
 const a=elem('a');a.href=uri;
 a.download='zsentinel-observed-'+(project||'portfolio')+'.csv';a.click();
 setTimeout(()=>URL.revokeObjectURL(uri),1000);
 invalidateBrief();
 status.textContent='Reviewed CSV saved locally. Original issue, CI and release states remain unverified.';
};
document.getElementById('calendarIncludeContact').addEventListener('change',()=>{
 invalidateBrief();
 status.textContent='Calendar privacy choice changed. Review the exact calendar again before downloading.';
});
document.getElementById('previewCalendar').onclick=()=>{
 saveSelection();
 viewedCalendarStamp=new Date().toISOString().replace(/[-:]/g,'').replace(/\\.\\d{3}/,'');
 viewedCalendar=calendarSource(viewedCalendarStamp);
 const panel=document.getElementById('calendarPanel');
 panel.hidden=!viewedCalendar;
 document.getElementById('calendarContent').textContent=viewedCalendar||'';
 document.getElementById('downloadCalendar').disabled=!viewedCalendar;
 status.textContent=viewedCalendar?
  'Review the private calendar events before choosing manual import. No calendar was contacted.':
  'No complete human-only follow-up dates to export from this saved board.';
};
document.getElementById('downloadCalendar').onclick=()=>{
 if(!viewedCalendar)return;
 saveSelection();
 if(calendarSource(viewedCalendarStamp)!==viewedCalendar){
  invalidateBrief();status.textContent='Calendar content changed; preview again before exporting.';return;
 }
 const uri=URL.createObjectURL(new Blob([viewedCalendar],{type:'text/calendar;charset=utf-8'}));
 const a=elem('a');a.href=uri;a.download='zsentinel-private-human-followups.ics';a.click();
 setTimeout(()=>URL.revokeObjectURL(uri),1000);
 invalidateBrief();
 status.textContent='Private all-day calendar file saved. Import manually and review calendar alert defaults.';
};
document.getElementById('batchSelectOpen').onclick=()=>{
 saveSelection();batchSelection.clear();
 board.cards.filter(c=>manualGateDecisions.has(handoffs.get(c.issue)?.decision)&&
  handoffs.get(c.issue)?.followup_status!=='local-complete')
  .slice(0,60).forEach(c=>batchSelection.add(c.issue));
 invalidateBatch();refreshBatch();
};
document.getElementById('batchClear').onclick=()=>{
 batchSelection.clear();invalidateBatch();refreshBatch();
};
['batchAction','batchDate'].forEach(id=>document.getElementById(id).addEventListener(
 'input',invalidateBatch));
document.getElementById('batchPreview').onclick=()=>{
 saveSelection();
 const plan=batchPlan();
 batchPreview=plan?.proof||null;
 document.getElementById('batchPreviewPanel').hidden=!plan;
 document.getElementById('batchPreviewText').textContent=plan?.preview||'';
 document.getElementById('batchApply').disabled=!plan;
 status.textContent=plan?
  'Review the complete private bulk changes; no external action performed.':
  'No valid changed follow-ups in this bounded selection.';
};
document.getElementById('batchApply').onclick=()=>{
 saveSelection();
 const plan=batchPlan();
 if(!plan||plan.proof!==batchPreview){
  invalidateBatch();
  status.textContent='Selection or private notes changed. Preview the proposed changes again.';
  return;
 }
 const stamp=new Date().toISOString();
 for(const row of plan.rows){
  const info=handoffs.get(row.issue);
  info.followup_status=row.to;
  info.due=row.newDate;
  if(row.from!==row.to)info.followup_changed_at_utc=stamp;
 }
 const updated=plan.rows.length;
 batchUndo={board:boardSha,rows:plan.rows.map(row=>({
  issue:row.issue,before:row.before,after:JSON.stringify(handoffs.get(row.issue))}))};
 document.getElementById('batchUndo').disabled=false;
 invalidateBatch();invalidateBrief();setUnsaved(true);
 batchSelection.clear();drawList();drawDetail();
 status.textContent=updated+' private human follow-ups updated locally. Source issue/owner/CI/release statuses remain unknown. Export private JSON to preserve them.';
};
document.getElementById('batchUndo').onclick=()=>{
 saveSelection();
 if(!batchUndo||batchUndo.board!==boardSha||
    !batchUndo.rows.length||batchUndo.rows.some(row=>
      !entries.has(row.issue)||
      JSON.stringify(handoffs.get(row.issue))!==row.after)){
  clearBatchUndo();
  status.textContent='Undo refused: an affected private record changed. No records were overwritten.';
  return;
 }
 const n=batchUndo.rows.length;
 for(const row of batchUndo.rows)handoffs.set(row.issue,JSON.parse(row.before));
 clearBatchUndo();invalidateBatch();invalidateBrief();setUnsaved(true);
 drawList();drawDetail();
 status.textContent='Restored '+n+' prior private human annotations. External issue/claim/CI/release states never changed. Export JSON to preserve them.';
};
document.getElementById('print').onclick=()=>window.print();
refreshPortfolio();refreshComparison();drawList();drawDetail();
</script></html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True,
                        help="Existing private zSentinel JSON delivery-board snapshot")
    parser.add_argument("--pr-dossier", action="append", type=Path,
                        help="Optional saved source-bound PR ZIP, repeat 1-12 for "
                             "existing-PR references beside current board issues")
    parser.add_argument("--output", type=Path, required=True,
                        help="New private HTML workspace file; never overwrites")
    args = parser.parse_args(argv)
    try:
        if args.board.resolve() == args.output.resolve():
            raise ValueError("Output must not overwrite board evidence")
        if args.pr_dossier:
            input_paths = [args.board] + args.pr_dossier
            if len({str(x.resolve()) for x in input_paths + [args.output]}) != (
                    len(input_paths) + 1):
                raise ValueError("Board, source ZIPs and output must be distinct")
        source, board_sha = read_source_and_sha(args.board)
        view = validated_view(source)
        saved_pr_context = None
        if args.pr_dossier:
            from zsentinel_saved_dossier_timeline import build_timeline
            from zsentinel_saved_issue_timeline import issue_worklist
            timeline = build_timeline(args.pr_dossier, minimum_sources=1)
            saved_pr_context = issue_worklist(timeline)
        write_create_only(args.output, html_document(
            view, board_sha, saved_pr_context=saved_pr_context))
        print(args.output)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("zSentinel workbench refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
