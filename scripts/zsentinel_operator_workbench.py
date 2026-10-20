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
    when(source.get("observed_at_utc"))
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
    projects = []
    for row in rows:
        if type(row) is not dict or type(row.get("project")) is not str or (
            not PROJECT.fullmatch(row["project"])) or row.get("action_authorized") is not False:
            raise ValueError("Source project operation is not read-only")
        projects.append(row["project"])
    return {"observed_at_utc": source["observed_at_utc"],
            "cards": chosen,
            "coverage": safe_coverage,
            "projects": sorted(set(projects) | {r["project"] for r in chosen}),
            "global_gate": "live claims, exact-head CI, review and release authority unverified"}


def html_document(view: dict, board_sha: str) -> str:
    records = json.dumps(view, ensure_ascii=True, separators=(",", ":"))
    # The JSON is embedded into an inline script. Escape HTML syntax even if
    # the JSON was formed from malicious public issue titles or operator notes.
    records = records.replace("<", "\\u003c").replace(">", "\\u003e")
    choices = "".join(
        '<option value="' + escape(p, quote=True) + '">' + escape(p) + '</option>'
        for p in view["projects"]
    )
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
<p id="prGateStatus" aria-live="polite">No imported source. All current PR gates unknown.</p>
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
<p id="prDeskSummary" aria-live="polite"></p><ol id="prDeskRows"></ol>
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
let baseline=null,carryPreview=null,prGateImport=null;
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
   if(typeof hash!=='string'||!/^[a-f0-9]{64}$/.test(hash)||
      data.original_board_sha256!==boardSha||
      !Number.isInteger(data.observed_batch_count)||
      data.observed_batch_count<1||data.observed_batch_count>75||
      data.missing_batch_count!==0||
      data.all_batches_observed_not_all_checks_required_verified!==true||
      data.earliest_observed_at_utc!==time||
      typeof data.latest_observed_at_utc!=='string')
    throw Error('Invalid complete-batch source provenance');
   const earliest=gateTime(time),latest=gateTime(data.latest_observed_at_utc);
   if(latest<earliest||latest-earliest>600000)
    throw Error('Unbounded batch observation range');
   bridgeCoverage={manifest_sha256:hash,batches:data.observed_batch_count,
    earliest_observed_at_utc:time,latest_observed_at_utc:data.latest_observed_at_utc,
    complete_receipts_not_required_checks:true};
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
function gateOverview(groups){
 const counts={failed:0,pending:0,review:0,unverified:0,
  'observed-no-authority':0};
 for(const group of groups)counts[gateState(group.url)]++;
 const age=prGateImport?Math.max(0,Math.floor((Date.now()-prGateImport.timestamp)/60000)):null;
 const coverage=prGateImport?.bridgeCoverage?
   ' · complete SAVED batch set '+prGateImport.bridgeCoverage.batches+
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
function refreshPrDesk(){
 const list=document.getElementById('prDeskRows');
 list.replaceChildren();
 const current=filtered(),groups=prDeskGroups();
 const unlinked=current.filter(c=>c.open_pr_candidates.length===0);
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
   inner.append(li);
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
   complete_saved_batch_set:!!prGateImport?.bridgeCoverage,
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
 if(!data||Array.isArray(data)||![1,2,3].includes(data.schema_version)||
    data.kind!=='zsentinel-private-human-delivery-handoff'||
    data.source_board_sha256!==baseline.sha||
    data.source_observed_at_utc!==baseline.observed_at_utc||
    data.authorizes_claim!==false||data.authorizes_merge!==false||
    data.authorizes_deploy!==false||!Array.isArray(data.entries)||
    data.entries.length>60)throw Error('Earlier private handoff does not match exact earlier source');
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
      (data.schema_version===3&&(
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
    followup_status:data.schema_version===3?record.followup_status:'open',
    followup_changed_at_utc:data.schema_version===3?(record.followup_changed_at_utc||''):'',
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
 if(!rows.length)return null;
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
 const view=filtered();issueList.replaceChildren();
 view.forEach(c=>{
   const li=elem('li'),b=elem('button');b.type='button';
   b.setAttribute('aria-current',c.issue===selected?'true':'false');
   b.append(elem('span',c.issue,'issue-id'),elem('span',c.title),
     elem('span',c.priority+' · '+c.project,'meta'));
   b.onclick=()=>{saveSelection();invalidateBrief();selected=c.issue;drawList();drawDetail()};li.append(b);issueList.append(li);
 });
 document.getElementById('info').textContent=board.cards.length+' bounded issue cards · '+view.length+
   ' shown · observed '+board.observed_at_utc+' · no active-claim authorization';
 if(selected&&!view.some(c=>c.issue===selected)){saveSelection();selected=null;drawDetail()}
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
['search','priority','project','disposition','change','followupStatus'].forEach(id=>document.getElementById(id).addEventListener(
 'input',()=>{saveSelection();invalidateBrief();drawList();drawDetail()}));
document.getElementById('export').onclick=()=>{
 saveSelection();
 const rows=[...handoffs].filter(([id,v])=>entries.has(id)&&permitted.has(v.decision))
 .map(([issue,value])=>({issue,decision:value.decision,note:value.note.slice(0,1000),
   owner:value.owner||'',due:value.due||'',carried_from:value.carried_from||'',
   followup_status:value.followup_status||'open',
   followup_changed_at_utc:value.followup_changed_at_utc||''}));
 const payload={schema_version:3,kind:'zsentinel-private-human-delivery-handoff',
   source_board_sha256:boardSha,source_observed_at_utc:board.observed_at_utc,
   authorizes_claim:false,authorizes_merge:false,authorizes_deploy:false,entries:rows};
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
   if(!data||Array.isArray(data)||![1,2,3].includes(data.schema_version)||
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
          (data.schema_version===3&&(
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
       followup_status:data.schema_version===3?v.followup_status:'open',
       followup_changed_at_utc:data.schema_version===3?(v.followup_changed_at_utc||''):''});
   }
   handoffs.clear();next.forEach((v,k)=>handoffs.set(k,v));
   clearBatchUndo();setUnsaved(false);invalidateBrief();drawList();drawDetail();
   status.textContent='Own human handoff restored from this exact original board.';
 }catch{
   status.textContent='Handoff not restored: invalid, foreign or from another board snapshot.';
 }finally{event.currentTarget.value=''}
});



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
  invalidateBrief();refreshPrDesk();
  document.getElementById('prGateClear').disabled=false;
  document.getElementById('prGateStatus').textContent=
   accepted.items.size+' linked PR receipts imported · '+accepted.time+
   (accepted.bridgeCoverage?' · COMPLETE SAVED BATCH SET '+
    accepted.bridgeCoverage.batches+' batches · manifest '+
    accepted.bridgeCoverage.manifest_sha256.slice(0,12)+
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
 prGateImport=null;invalidateBrief();refreshPrDesk();
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
    parser.add_argument("--output", type=Path, required=True,
                        help="New private HTML workspace file; never overwrites")
    args = parser.parse_args(argv)
    try:
        if args.board.resolve() == args.output.resolve():
            raise ValueError("Output must not overwrite board evidence")
        source, board_sha = read_source_and_sha(args.board)
        view = validated_view(source)
        write_create_only(args.output, html_document(view, board_sha))
        print(args.output)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("zSentinel workbench refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
