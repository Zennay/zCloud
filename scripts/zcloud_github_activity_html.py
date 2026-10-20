#!/usr/bin/env python3
"""Standalone, offline, accessible HTML view of zCloud read-only GitHub receipts.

Reads the same bounded JSON as the Markdown digest. Does not host a server,
load remote scripts/styles/assets, persist credentials or claim deployment.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from html import escape
import re
import sys

from zcloud_github_activity_digest import safe_receipt, digest, PROVIDER_ADMISSIONS


CSS = """
:root{color-scheme:dark;--bg:#10151d;--card:#1c2531;--ink:#eef3fa;--muted:#acbed0;--line:#405063;--accent:#b3f48c}
*{box-sizing:border-box}body{max-width:1120px;margin:auto;padding:clamp(16px,3vw,44px);font:16px/1.55 system-ui,sans-serif;background:var(--bg);color:var(--ink)}
header{margin-bottom:30px}h1{font-size:clamp(1.7rem,4vw,2.8rem);margin:.2em 0}h2{font-size:1.35rem}p{max-width:86ch}
a{color:var(--accent);text-underline-offset:3px}a:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:4px}
.meta{color:var(--muted)}.warning{border-left:4px solid #ffd28a;background:#312921;padding:14px 18px;border-radius:5px;color:#fff0d9}
section{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:clamp(16px,2.2vw,26px);margin:18px 0}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}.stats span{border:1px solid var(--line);border-radius:7px;padding:6px 10px;background:#131a23}
.status{font-size:.92rem;color:#ffda9a}.row{border-top:1px solid var(--line);display:grid;grid-template-columns:minmax(110px,160px) minmax(90px,140px) 1fr;gap:12px;align-items:center;padding:12px 0}
.time{color:var(--muted);font-variant-numeric:tabular-nums;font-size:.9rem}.kind{font-weight:650;font-size:.85rem;text-transform:uppercase;letter-spacing:.03em}
summary{cursor:pointer;color:var(--accent);font-weight:650}details{margin-top:15px}
footer{padding:25px 0;color:var(--muted)}@media(max-width:650px){.row{grid-template-columns:1fr;gap:2px;padding:14px 0}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
"""

def safe_time(value: object) -> str:
    if not isinstance(value, str) or len(value) > 40:
        return "unknown timestamp"
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return "unknown timestamp"
    except ValueError:
        return "unknown timestamp"
    return stamp.strftime("%d %b %Y · %H:%M UTC")


def event_link(event: dict, repo: str) -> tuple[str, str]:
    kind = event["kind"]
    if kind == "main_commit":
        sha = event["sha"]
        return "Main commit " + sha[:12], f"https://github.com/{repo}/commit/{sha}"
    if kind == "ci_run":
        n = event["run_id"]
        state = str(event.get("conclusion") or event.get("status") or "unknown")
        return f"Actions #{n} · {state}", f"https://github.com/{repo}/actions/runs/{n}"
    n = event["pr"]
    suffix = " · head " + event["pr_head_sha"][:12]
    if event.get("draft") is True:
        suffix += " · draft"
    # Current PR state, not a claim about its state at creation time.
    if event.get("pr_lifecycle") == "closed_without_merge":
        suffix += " · NOW CLOSED WITHOUT MERGE (not delivered)"
    elif event.get("pr_lifecycle") == "open":
        suffix += " · currently open"
    if kind == "pr_merged":
        target = event.get("reported_target") or "unknown"
        suffix += (" · reported target " + target
                   + (" · exact main SHA in imported receipt (not live deploy proof)"
                      if event.get("main_merged") is True
                      else " · main not proven in imported history"))
    return f"Pull request #{n}{suffix}", f"https://github.com/{repo}/pull/{n}"


def html_page(data: dict) -> str:
    # Reuse the strict receipt and all-event validation of the text digest.
    # Never render untrusted source HTML or freeform upstream text.
    digest(data)
    groups: list[str] = []
    for coverage in data["coverage"]:
        project = coverage["project"]
        repo = coverage["repository"]
        rows = [event for event in data["events"] if event["project"] == project]
        counts = Counter(e["kind"] for e in rows)
        # Only one updated event per PR may establish currently closed
        # without merge in this bounded sample; never count as shipped.
        closed_unmerged = len({
            e["pr"] for e in rows if e["kind"] == "pr_updated"
            and e.get("pr_lifecycle") == "closed_without_merge"
        })
        labels = (("main_commit", "Main commits"), ("pr_created", "PRs created"),
                  ("pr_merged", "PRs merged"), ("ci_run", "Workflow runs"))
        chips = "".join("<span>" + escape(name) + ": <b>" + str(counts.get(key, 0)) +
                        "</b></span>" for key, name in labels)
        chips += ("<span>Now closed without merge (NOT delivered): <b>" +
                  str(closed_unmerged) + "</b></span>")
        main_target_merges = sum(e["kind"] == "pr_merged" and
                                 e.get("reported_main_merge") is True for e in rows)
        chips += ("<span>PR merges reporting main target: <b>" +
                  str(main_target_merges) + "</b></span>")
        observed_main_merges = sum(
            e["kind"] == "pr_merged" and e.get("main_merged") is True for e in rows
        )
        chips += ("<span>Matching main SHA in imported snapshot: <b>" +
                  str(observed_main_merges) + "</b></span>")
        windows = []
        for key, label in (("github_pr", "Pull requests"), ("github_ci", "Actions"),
                           ("github_main_commits", "Main history")):
            windows.append(escape(label) + ": " + escape(coverage[key]))
        states = " · ".join(windows)
        status = coverage["status"]
        compared = data.get("previous_snapshot_compared") is True
        newly_observed = sum(event.get("new_since_previous") is True for event in rows)
        if compared:
            chips += ("<span>Absent from last bounded snapshot: <b>" +
                      str(newly_observed) + "</b></span>")
        branch_scope = coverage.get("github_branches")
        if branch_scope == "observed_pr_heads_only":
            states += " · PR branch SHAs observed (partial)"
        else:
            states += " · branches not synchronized"
        admission = coverage.get("provider_admission", "none")
        # The strict digest() above already rejects unknown admission values.
        blocker = (
            "<p class='warning' role='alert'><b>GitHub bronblokkade:</b> " +
            escape(PROVIDER_ADMISSIONS[admission]) + "</p>"
            if admission != "none" else ""
        )
        warning = (
            blocker +
            "<p class='warning'>Partial source coverage. More events may exist "
            "than the displayed bounded window. Missing data is unknown, not zero.</p>"
        )
        table = []
        for item in rows:
            label, url = event_link(item, repo)
            fresh = " · not in prior sample" if item.get("new_since_previous") is True else ""
            table.append("<div class='row'><time class='time'>" +
                         escape(safe_time(item.get("source_at"))) +
                         "</time><span class='kind'>" + escape(item["kind"].replace("_", " ") + fresh) +
                         "</span><a href='" + escape(url, quote=True) +
                         "' rel='noopener noreferrer' target='_blank'>" +
                         escape(label) + "</a></div>")
        body = "".join(table) or "<p>No GitHub events appeared in this bounded observation. This is not proof of inactivity.</p>"
        groups.append("<section aria-label='Project " + escape(project, quote=True) +
                      "'><h2>" + escape(project) + "</h2><p class='meta'>" +
                      escape(repo) + "</p><p class='status'>" + escape(states) +
                      " · " + escape(status) + "</p><div class='stats'>" + chips +
                      "</div>" + warning + "<details open><summary>Show observed events (" +
                      str(len(rows)) + ")</summary>" + body + "</details></section>")
    observed = escape(safe_time(data.get("observed_at")))
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='referrer' content='no-referrer'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; connect-src 'none'\">"
            "<title>zCloud — GitHub activity</title><style>" + CSS +
            "</style></head><body><header><p class='meta'>ZCLOUD · READ-ONLY OPERATOR VIEW</p>"
            "<h1>GitHub activity</h1><p>PRs, Actions and actual canonical-main commit SHAs. "
            "No credentials, private content, deployment claims or automatic refresh.</p>"
            "<p class='meta'>Observed " + observed + " · UTC</p>"
            "<p class='warning'>This is a bounded GitHub snapshot, not the live zCloud dashboard. "
            "Branch-only work and runner/deployment status are not synchronized. "
            "Successful CI is not a deploy; a GitHub PR merge may target a different base. "
            "Snapshot comparison marks only absent events from a bounded previous sample, "
            "not necessarily newly created GitHub work.</p>"
            "</header><main>" + "".join(groups) +
            "</main><footer>Independent zSentinel source for zCloud #1518. "
            "Owner approval and existing production gates are required for live integration."
            "</footer></body></html>")

def main() -> int:
    if len(sys.argv) != 1:
        print("No arguments accepted; supply a bounded JSON receipt on stdin.", file=sys.stderr)
        return 2
    try:
        data = sys.stdin.buffer.read(1024 * 1024 + 1)
        page = html_page(safe_receipt(data))
    except (ValueError, TypeError, UnicodeError, KeyError, AttributeError):
        print("Invalid activity receipt. No HTML generated.", file=sys.stderr)
        return 2
    sys.stdout.write(page)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
