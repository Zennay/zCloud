"""Standalone private zCloud GitHub activity report.

Renders a human-readable, offline HTML dashboard from an operator-owned 0600
ledger. Does not read tokens, send requests, serve HTTP or touch live services.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html import escape
import argparse
import os
from pathlib import Path
import stat
import sys
import tempfile

from scripts.zcloud_activity_ledger import _fair, _read, assert_private_destination
from scripts.zcloud_github_activity import ActivitySourceError

MAX_RENDER = 500
CSS = """
:root {color-scheme:dark;--bg:#0c1320;--surface:#172337;--line:#35445a;--text:#f3f7fc;--muted:#b6c7d8;--accent:#80d6bd}
* {box-sizing:border-box}
body {margin:0;padding:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui, sans-serif}
main {max-width:1180px;padding:34px 20px 56px;margin:auto}
h1 {font-size:clamp(26px,4vw,42px);line-height:1.15;margin:0 0 12px}
h2 {font-size:24px;margin:0 0 12px}
p,small {color:var(--muted)}
header {border-bottom:1px solid var(--line);padding-bottom:24px;margin-bottom:25px}
aside {border-left:3px solid var(--accent);padding:9px 16px;margin:16px 0;background:var(--surface)}
section {margin:28px 0}
.grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.card {border:1px solid var(--line);border-radius:14px;background:var(--surface);padding:15px;min-width:0}
.card strong {display:block;font-size:24px;line-height:1.2}
.card small {display:block;margin-top:5px}
.status {display:inline-block;border:1px solid var(--line);padding:2px 8px;border-radius:20px;font-size:12px;font-weight:600}
table {width:100%;border-collapse:collapse}
th {font-weight:600;text-align:left}
td,th {padding:10px;border-bottom:1px solid var(--line);vertical-align:top}
td code {overflow-wrap:anywhere}
code, time {font-variant-numeric:tabular-nums}
code {font-family:ui-monospace,monospace;font-size:13px}
.wrap {overflow-x:auto}
footer {border-top:1px solid var(--line);margin-top:28px;padding-top:16px;color:var(--muted)}
a {color:var(--accent)}
.sr-only {position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0)}
@media(max-width:520px) {main {padding:24px 12px}td,th {padding:8px 7px;font-size:12px}}
"""

EVENT_LABELS = {
    "pr_created": "PR aangemaakt",
    "pr_updated": "PR bijgewerkt",
    "pr_merged": "PR gemerged",
    "main_commit": "Commit op main",
    "ci_run": "CI-uitvoering",
    "branch_tip_observed": "Branch-tip gezien",
}
STATUS_LABELS = {
    "partial": "Deels beschikbaar",
    "recent_window": "Recent venster beschikbaar",
}


def h(value):
    return escape(str(value), quote=True)


def format_date(value):
    if not value:
        return "Onbekend"
    return h(value)


def time_cell(item):
    when = item.get("source_timestamp")
    if when:
        return "<time datetime='" + h(when) + "'>" + h(when) + "</time>"
    observed = item.get("first_observed_at")
    return "Geen bron-tijdstip; gezien " + h(observed or "onbekend")


def event_summary(item):
    kind = item["kind"]
    if kind.startswith("pr_"):
        number = item.get("number")
        ident = ("PR #" + str(number)) if type(number) is int else "PR"
        if kind == "pr_merged":
            return ident + (" → main" if item.get("merged_main") is True else " → andere branch")
        return ident + (" (concept)" if item.get("draft") is True else "")
    if kind == "ci_run":
        state = item.get("state", "unknown")
        conclusion = item.get("conclusion")
        return "CI " + h(state) + (", uitslag: " + h(conclusion) if conclusion else ", niet afgerond")
    if kind == "main_commit":
        return "Main SHA " + h(item.get("sha", "")[:12])
    if kind == "branch_tip_observed":
        return "Branch-id " + h(item.get("branch_id", ""))
    return "Gebeurtenis"


def render_html(ledger, project=None):
    coverage = ledger["coverage"]
    ids = sorted(coverage)
    if project is not None:
        if project not in coverage:
            raise ActivitySourceError("activity_report_project_not_in_ledger")
        ids = [project]
    rows = _fair(ledger["events"], ids, MAX_RENDER)
    nav = []
    blocks = []
    for pid in ids:
        info = coverage[pid]
        if not isinstance(info, dict):
            raise ActivitySourceError("activity_report_coverage_invalid")
        events = [item for item in rows if item["project"] == pid]
        main_merges = {item.get("number") for item in events
                       if item["kind"] == "pr_merged" and item.get("merged_main") is True}
        main_commits = {item.get("sha") for item in events if item["kind"] == "main_commit"}
        ci = Counter(item.get("conclusion") or "onbekend" for item in events if item["kind"] == "ci_run")
        full_sync = info.get("last_successful_sync")
        last_fetch = info.get("last_successful_source_fetch")
        source_states = info.get("sources") or {}
        coverage_items = "".join(
            "<li><code>" + h(key) + "</code>: " + h(value) + "</li>"
            for key, value in sorted(source_states.items()))
        nav.append("<a href='#" + h(pid) + "'>" + h(pid) + "</a>")
        table_rows = []
        for item in events:
            table_rows.append(
                "<tr><td>" + h(EVENT_LABELS.get(item["kind"], item["kind"])) +
                "</td><td>" + event_summary(item) +
                "</td><td>" + time_cell(item) + "</td></tr>"
            )
        cards = [
            ("PR's gemerged naar main", len(main_merges)),
            ("Recent zichtbare main-commits", len(main_commits)),
            ("CI succesvol (geen bewijs van merge)", ci.get("success", 0)),
            ("Zichtbare brongebeurtenissen", len(events)),
        ]
        metrics = "".join(
            "<div class='card'><strong>" + h(value) + "</strong><small>" + h(name) + "</small></div>"
            for name, value in cards
        )
        blocks.append(
            "<section id='" + h(pid) + "' aria-labelledby='t-" + h(pid) + "'>"
            "<h2 id='t-" + h(pid) + "'>" + h(pid) +
            " <span class='status'>" + h(STATUS_LABELS.get(info.get("status"), "Onvolledig")) + "</span></h2>"
            "<p>GitHub repository: <code>" + h(info.get("repository", "onbekend")) + "</code></p>"
            "<div class='grid'>" + metrics + "</div>"
            "<p>Laatste succesvolle bron-ophaalactie: <strong>" + format_date(last_fetch) +
            "</strong> · laatste volledige recent-window synchronisatie: <strong>" +
            format_date(full_sync) + "</strong></p>"
            "<details><summary>Brondetails en beperkingen</summary><ul>" + coverage_items + "</ul></details>"
            "<div class='wrap'><table><caption class='sr-only'>GitHub bronactiviteit voor " + h(pid) +
            "</caption><thead><tr><th scope='col'>Type</th><th scope='col'>Status</th>"
            "<th scope='col'>Tijdstip</th></tr></thead><tbody>" +
            ("".join(table_rows) or "<tr><td colspan='3'>Geen gebeurtenissen in opgeslagen venster; geen bewijs van nul activiteit.</td></tr>") +
            "</tbody></table></div></section>"
        )
    observed = ledger.get("last_observed_at", "onbekend")
    return ("<!doctype html><html lang='nl'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
            "<title>zCloud · Privé GitHub-activiteit</title><style>" + CSS + "</style></head>"
            "<body><main><header><h1>Projectactiviteit · GitHub</h1>"
            "<p>Privé, lokaal en alleen-lezen. Gegevens zijn historisch of gedeeltelijk, niet live bevestigd.</p>"
            "<aside><strong>Belangrijk:</strong> een groene CI-run is geen gemergede feature. "
            "Een waargenomen branch of oude cache is geen nieuwe code-oplevering. "
            "Dit overzicht omvat geen lokale VPS-events.</aside>"
            "<p>Ledger laatst geobserveerd: " + h(observed or "onbekend") + "</p>"
            "<nav aria-label='Projecten'>" + " · ".join(nav) + "</nav></header>"
            + "".join(blocks) +
            "<footer>Bron: private zCloud GitHub-ledger. Maximaal 500 zichtbare gebeurtenissen, "
            "eerlijk verdeeld over projecten. Geen externe resources of actieve netwerkverbinding.</footer>"
            "</main></body></html>")


def save_private(path, content):
    assert_private_destination(path)
    path = Path(path)
    if path.is_symlink():
        raise ActivitySourceError("activity_report_output_symlink_refused")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".zcloud-github-report-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise ActivitySourceError("activity_report_output_symlink_refused")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render private local GitHub activity HTML from a source ledger")
    parser.add_argument("--ledger", required=True, help="Canonical private 0600 ledger")
    parser.add_argument("--project", help="Optional canonical project filter")
    parser.add_argument("--output", required=True, help="Local 0600 HTML file outside public/")
    args = parser.parse_args(argv)
    ledger_path = Path(args.ledger)
    if not ledger_path.exists():
        raise ActivitySourceError("activity_report_ledger_missing")
    report = render_html(_read(ledger_path), project=args.project)
    save_private(args.output, report)
    print("Private GitHub activity report generated (not live synced).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ActivitySourceError as exc:
        sys.stderr.write("Private GitHub activity report unavailable: " + str(exc) + "\n")
        raise SystemExit(2)
