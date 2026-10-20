#!/usr/bin/env python3
"""Standalone, script-free offline merge-first desk from validated saved gate batches.

Only render the bounded and non-authorizing result of combined_manifest().
Never inspect GitHub, claim a worker, dispatch CI, merge or deploy.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html import escape
import re

MAX_PRS = 300
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
HEAD = re.compile(r"[a-f0-9]{40}\Z")
PR = re.compile(r"https://github\.com/Zennay/[A-Za-z0-9_.-]{1,100}/pull/[1-9][0-9]{0,8}\Z")
ISSUE = re.compile(r"https://github\.com/Zennay/[A-Za-z0-9_.-]{1,100}/issues/[1-9][0-9]{0,8}\Z")
PRIORITIES = ("P0", "P1", "P2", "Unspecified")
GATE_STATUS = {"observed_current_head_partial_gate_evidence",
               "github_source_unavailable_or_head_changed"}
REASONS = (
    ("source", "Bron of batch ontbreekt", "Nieuwe exacte GitHub PR-bron ophalen."),
    ("failed", "Niet-geslaagde checks gezien", "Laat de bevoegde PR-eigenaar falende checks oplossen."),
    ("pending", "Checks nog niet afgerond", "Volg exact dezelfde PR-HEAD tot verplichte CI is afgerond."),
    ("changes", "Reviewwijzigingen gevraagd", "Vraag de oorspronkelijke code-eigenaar reviewfeedback af te handelen."),
    ("partial", "Check- of reviewbron onvolledig", "Controleer de volledige actuele gate-inventaris."),
    ("review", "Onafhankelijke review niet gezien", "Vraag de vereiste onafhankelijke review aan."),
    ("policy", "Branchregels en eigenaarschap onbekend", "Verifieer actuele mainregels, eigenaar en releasebevoegdheid."),
)
LABELS = {key: (label, action) for key, label, action in REASONS}


def _utc(value):
    if type(value) is not str or len(value) > 48:
        raise ValueError("Missing saved UTC timestamp")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset().total_seconds() != 0:
        raise ValueError("Saved gate timestamps must be UTC")
    if moment > datetime.now(timezone.utc):
        raise ValueError("Future snapshot cannot represent available review evidence")
    return moment


def _admit(selected, integrated):
    if (type(integrated) is not dict
            or integrated.get("schema") != "zsentinel-integrated-issue-pr-scope-v1"
            or any(integrated.get(flag) is not False for flag in
                   ("claim_authorized", "merge_authorized", "deploy_authorized",
                    "may_take_ownership", "writes_performed"))
            or integrated.get("saved_receipts_not_required_checks_verified") is not True
            or type(selected) is not list or not 1 <= len(selected) <= MAX_PRS
            or type(integrated.get("review_candidate_pr_count")) is not int
            or integrated["review_candidate_pr_count"] != len(selected)):
        raise ValueError("Not a bounded non-authorizing saved PR source")
    for key in ("original_review_manifest_sha256", "original_board_sha256"):
        value = integrated.get(key)
        if type(value) is not str or not HEX64.fullmatch(value):
            raise ValueError("Missing private source provenance")
    oldest = _utc(integrated.get("earliest_observed_at_utc"))
    latest = _utc(integrated.get("latest_observed_at_utc"))
    if latest < oldest or (latest - oldest).total_seconds() > 600:
        raise ValueError("Inconsistent saved gate window")
    missing = integrated.get("missing_batch_indexes")
    total = integrated.get("expected_batch_count")
    if (type(total) is not int or not 1 <= total <= 75
            or type(missing) is not list or len(missing) > total
            or any(type(i) is not int or i < 1 or i > total for i in missing)
            or len(missing) != len(set(missing))
            or type(integrated.get("missing_batch_count")) is not int
            or integrated["missing_batch_count"] != len(missing)
            or type(integrated.get("observed_batch_count")) is not int
            or integrated["observed_batch_count"] != total - len(missing)):
        raise ValueError("Inconsistent saved batch coverage")
    return oldest, latest, set(missing)


def _observed_gate(integrated, selected, missing):
    linked = {}
    known_pairs = set()
    for priority_number, _repo, _number, ref in selected:
        if (type(priority_number) is not int or not 0 <= priority_number < 4
                or type(ref) is not dict or type(ref.get("url")) is not str
                or not PR.fullmatch(ref["url"])):
            raise ValueError("Noncanonical review source")
        for issue in ref.get("observed_issue_refs", []):
            if type(issue) is not dict or type(issue.get("issue")) is not str:
                raise ValueError("Malformed linked issue")
            if issue.get("url") != "https://github.com/" + issue["issue"].replace("#", "/issues/"):
                raise ValueError("Untrusted issue link")
            if not ISSUE.fullmatch(issue["url"]):
                raise ValueError("Unsafe source issue URL")
            known_pairs.add((issue["issue"], ref["url"]))
    rows = integrated.get("issues")
    if type(rows) is not list or len(rows) > 60:
        raise ValueError("Unbounded saved issue inventory")
    seen_issues = set()
    for issue in rows:
        if (type(issue) is not dict or type(issue.get("issue")) is not str
                or issue["issue"] in seen_issues or type(issue.get("existing_prs")) is not list
                or len(issue["existing_prs"]) > 5):
            raise ValueError("Untrusted issue or duplicate identity")
        seen_issues.add(issue["issue"])
        for candidate in issue["existing_prs"]:
            url = candidate.get("url") if type(candidate) is dict else None
            gate = candidate.get("live_exact_head_review_checks_observation") if type(candidate) is dict else None
            if ((issue["issue"], url) not in known_pairs or type(gate) is not dict
                    or gate.get("status") not in GATE_STATUS
                    or gate.get("read_only") is not True
                    or any(gate.get(flag) is not False for flag in
                           ("merge_authorized", "deployment_authorized",
                            "required_branch_checks_and_rules_verified"))):
                raise ValueError("Untrusted saved gate candidate")
            if url in linked and linked[url] != gate:
                raise ValueError("Inconsistent gate row on several linked issues")
            linked[url] = gate
    for index, (_, _repo, _number, ref) in enumerate(selected):
        if index // 4 + 1 not in missing and ref["url"] not in linked:
            raise ValueError("Observed gate batch omitted an expected PR")
    return linked


def _classify(gate):
    if gate is None or gate.get("status") != "observed_current_head_partial_gate_evidence":
        return "source"
    head = gate.get("head_sha_observed")
    if (type(head) is not str or not HEAD.fullmatch(head)
            or gate.get("exact_head_stable_on_second_read") is not True
            or gate.get("required_review_policy_verified") is not False
            or gate.get("mergable_by_automation") is not False):
        raise ValueError("Invalid exact-head gate facts")
    counters = ("observed_checks_not_successful", "legacy_status_failure_or_error_observed",
                "observed_checks_pending", "legacy_status_pending_observed",
                "observed_exact_head_changes_requested",
                "observed_independent_exact_head_approvals", "observed_checks_completed")
    if any(type(gate.get(k)) is not int or not 0 <= gate[k] <= 100 for k in counters):
        raise ValueError("Invalid bounded gate counts")
    for field in ("check_runs_page_complete", "review_page_may_be_truncated",
                  "legacy_status_source_may_be_truncated"):
        if type(gate.get(field)) is not bool:
            raise ValueError("Invalid gate completeness")
    if gate["observed_checks_not_successful"] or gate["legacy_status_failure_or_error_observed"]:
        return "failed"
    if gate["observed_checks_pending"] or gate["legacy_status_pending_observed"]:
        return "pending"
    if gate["observed_exact_head_changes_requested"]:
        return "changes"
    if (not gate["check_runs_page_complete"] or gate["review_page_may_be_truncated"]
            or gate["legacy_status_source_may_be_truncated"]
            or not gate["observed_checks_completed"]):
        return "partial"
    if not gate["observed_independent_exact_head_approvals"]:
        return "review"
    return "policy"


def render_html(selected, integrated):
    """Static operator dashboard: no JS, no remote sources, no approval inference."""
    oldest, latest, missing = _admit(selected, integrated)
    gates = _observed_gate(integrated, selected, missing)
    grouped = {key: [] for key, _label, _action in REASONS}
    for index, (priority, repo, number, ref) in enumerate(selected):
        url = ref["url"]
        gate = None if index // 4 + 1 in missing else gates.get(url)
        bucket = _classify(gate)
        if bucket != "source" and gate is None:
            raise ValueError("Invalid classified gate")
        grouped[bucket].append((PRIORITIES[priority], repo, number, ref, gate))
    counts = Counter({name: len(rows) for name, rows in grouped.items()})
    age_seconds = max(0, (datetime.now(timezone.utc) - oldest).total_seconds())
    stale = age_seconds >= 6 * 3600
    n = lambda s: escape(str(s), quote=True)
    summary = "".join(
        '<a class="stat" href="#' + key + '"><strong>' + str(counts[key]) +
        '</strong><span>' + n(label) + '</span></a>'
        for key, label, _action in REASONS)
    cards = []
    for key, label, action in REASONS:
        items = []
        for priority, repo, number, ref, gate in grouped[key]:
            url = ref["url"]
            if not PR.fullmatch(url):
                raise ValueError("Invalid source PR URL")
            issue_links = "".join(
                '<a href="' + n(issue["url"]) + '" target="_blank" rel="noopener noreferrer">' +
                n(issue["issue"]) + '</a> '
                for issue in ref["observed_issue_refs"])
            head = (gate.get("head_sha_observed") if gate and
                    gate.get("status") == "observed_current_head_partial_gate_evidence" else None)
            sha = '<code>' + n(head[:12]) + '</code>' if head else "HEAD onbekend"
            details = ""
            if head:
                details = ('<small>Waargenomen CI: ' +
                           str(gate["observed_checks_completed"]) + ' afgerond, ' +
                           str(gate["observed_checks_pending"]) + ' lopend, ' +
                           str(gate["observed_checks_not_successful"]) + ' niet geslaagd. ' +
                           'Onafhankelijke approvals gezien: ' +
                           str(gate["observed_independent_exact_head_approvals"]) +
                           ' (policy onbekend).</small>')
            items.append(
                '<article><div class="line"><strong>' + n(priority) + ' · ' +
                n(repo) + ' #' + str(number) + '</strong>' +
                '<a href="' + n(url) + '" target="_blank" rel="noopener noreferrer">Open PR ↗</a></div>' +
                '<p>' + sha + ' · ' + n(label) + '</p>' +
                '<p class="linked">Bronissues: ' + issue_links + '</p>' + details +
                '<p class="next">Volgende menselijke actie: ' + n(action) + '</p></article>')
        cards.append('<section id="' + key + '"><details' + (' open' if items else '') +
                     '><summary>' + n(label) + ' (' + str(len(items)) +
                     ')</summary><div class="cards">' +
                     (''.join(items) if items else '<p>Niet gezien in deze steekproef.</p>') +
                     '</div></details></section>')
    source = n(integrated["original_review_manifest_sha256"])
    board = n(integrated["original_board_sha256"])
    warning = ('<p class="warn">De oudste opgeslagen gatebron is ouder dan zes uur. '
               'Alle actuele PR-statussen moeten opnieuw worden gecontroleerd.</p>' if stale else '')
    html = '''<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'none'; script-src 'none'; connect-src 'none'; form-action 'none'; object-src 'none'; base-uri 'none'; style-src 'unsafe-inline'">
<title>zSentinel · privé merge-first werkbak</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0c1322;color:#edf5ff;font:15px/1.6 system-ui,sans-serif}
main{max-width:1120px;margin:auto;padding:26px 20px 90px}h1{font-size:clamp(24px,4vw,40px);margin:6px 0}
a{color:#a6dcff}a:hover{text-decoration-thickness:2px}header p{color:#bad0e3}
.subtitle{letter-spacing:.12em;text-transform:uppercase;font-size:12px;color:#9ebed3}
.caution,.warn{border:1px solid #c9a45a;border-radius:13px;background:#302919;padding:14px;color:#fff1d3}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px;margin:22px 0}
.stat{color:#eff7ff;text-decoration:none;border:1px solid #426382;border-radius:12px;background:#14263a;padding:14px;display:flex;flex-direction:column}
.stat strong{font-size:27px}.stat span{font-size:13px;color:#bed3e9}
details{margin:15px 0;background:#111f32;border:1px solid #3e526a;border-radius:14px;overflow:hidden}
summary{cursor:pointer;padding:16px;font-size:18px;font-weight:700}
.cards{padding:0 16px 16px;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:12px}
article{background:#1c3047;border:1px solid #496882;border-radius:11px;padding:16px;overflow-wrap:anywhere}
article p{margin:9px 0}.line{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
.linked{color:#d4e4f5}.linked a{margin-right:5px}
.next{color:#b9e8d5;border-top:1px solid #405a72;padding-top:8px}
small{display:block;color:#c3d6e6}code{font-size:12px;background:#0b182a;border-radius:5px;padding:3px 6px}
.provenance{padding-top:18px;border-top:1px solid #405774;color:#b1c6d8;font-size:13px;overflow-wrap:anywhere}
@media print{body{background:white;color:black}.stat,details,article{background:white;color:black;border-color:#555}.cards{display:block}article{margin:10px 0}summary{color:black}}
</style></head><body><main>
<header><div class="subtitle">zCloud / zSentinel / opgeslagen bewijs</div>
<h1>Bestaande PR's eerst</h1>
<p>PR-blokkades en vervolgstappen op basis van één begrensde opgeslagen GitHub-steekproef.</p>
<div class="caution"><strong>NIET mergeklaar of live geverifieerd.</strong>
Deze pagina kan geen claim, CI, vereiste branchregels, onafhankelijke review,
merge of productieactie autoriseren. Controleer altijd de actuele exacte PR-HEAD in GitHub.</div>
</header>'''
    html += (warning + '<div class="stats">' + summary + '</div>' +
             '<p>Bronvenster: ' + n(integrated["earliest_observed_at_utc"]) +
             ' — ' + n(integrated["latest_observed_at_utc"]) +
             ' · batches: ' + str(integrated["observed_batch_count"]) + '/' +
             str(integrated["expected_batch_count"]) + ' · onbekende batches: ' +
             str(len(missing)) + ' · PR-kandidaten: ' + str(len(selected)) +
             ' (niet noodzakelijk nog open).</p>' + ''.join(cards) +
             '<footer class="provenance">Reviewmanifest SHA-256: <code>' + source +
             '</code><br>Bronboard SHA-256: <code>' + board +
             '</code><p>Privé offline overdracht, geen live status, actieve claim, herstelbewijs of mergegoedkeuring.</p></footer></main></body></html>')
    if len(html.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Offline HTML exceeds the private one-MiB output limit")
    return html
