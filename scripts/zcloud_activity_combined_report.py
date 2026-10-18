"""Private offline view of *both* zCloud local events and GitHub receipts.

This is an operator-driven presentation layer, not a live ingest, worker,
scheduler or trusted release/merge authority. Local event titles/details never
leave their input file; they are not included in the rendered HTML.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import os
from pathlib import Path
import stat
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, ProxyHandler, build_opener

from scripts.zcloud_activity_ledger import _read, merge_and_store
from scripts.zcloud_activity_operator_report import h, render_html, save_private
from scripts.zcloud_github_activity import (ActivitySourceError, NoRedirect, strict_json,
                                           GithubReader, portfolio_registry, collect)

MAX_LOCAL_BYTES = 1024 * 1024
MAX_LOCAL_ROWS = 80


def _parse_local_rows(raw, projects):
    """Keep only allowlisted project/timestamp metadata from an API response."""
    if len(raw) > MAX_LOCAL_BYTES:
        raise ActivitySourceError("local_snapshot_too_large")
    try:
        data = strict_json(raw.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        raise ActivitySourceError("local_snapshot_invalid_json") from None
    if type(data) is not list or len(data) > MAX_LOCAL_ROWS:
        raise ActivitySourceError("local_snapshot_invalid_shape")
    rows = []
    seen = set()
    for value in data:
        if type(value) is not dict:
            raise ActivitySourceError("local_snapshot_invalid_event")
        project, identifier, when = (value.get(k) for k in ("project", "id", "ts"))
        if (type(project) is not str or project not in projects or
                type(identifier) is not str or not 1 <= len(identifier) <= 256 or
                type(when) is not str or len(when) > 42):
            raise ActivitySourceError("local_snapshot_untrusted_event")
        try:
            stamp = datetime.fromisoformat(when.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("timezone missing")
            utc = stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OverflowError):
            raise ActivitySourceError("local_snapshot_invalid_time") from None
        key = (project, identifier)
        if key in seen:
            continue
        seen.add(key)
        # Never emit raw ID/title/detail. Digest exists for deterministic
        # ordering only, not as an operator-visible identity.
        rows.append((project, utc, hashlib.sha256(identifier.encode("utf-8")).digest()))
    return sorted(rows, key=lambda row: (row[1], row[0], row[2]), reverse=True)


def read_local_snapshot(path, projects):
    """Accept one bounded /api/activity JSON response, never private detail."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except (OSError, ValueError):
        raise ActivitySourceError("local_snapshot_unavailable") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_LOCAL_BYTES:
            raise ActivitySourceError("local_snapshot_not_regular_or_too_large")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(MAX_LOCAL_BYTES + 1)
    finally:
        os.close(fd)
    return _parse_local_rows(raw, projects)


def read_local_api(projects):
    """Optional operator-initiated localhost GET only, per-project bounded.

    Never authenticates, follows redirects, uses environment proxies, or
    requests a network host other than literal 127.0.0.1:8765.
    A failed project is explicitly unavailable, not an empty successful feed.
    """
    if not projects or len(projects) > 12:
        raise ActivitySourceError("local_api_project_selection_out_of_bounds")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    rows, unavailable = [], []
    for pid in sorted(projects):
        url = "http://127.0.0.1:8765/api/activity?project=" + quote(pid, safe="")
        try:
            request = Request(url, headers={"Accept": "application/json",
                                            "User-Agent": "zcloud-private-activity/1"}, method="GET")
            with opener.open(request, timeout=3) as response:
                if response.status != 200 or response.geturl() != url:
                    raise ActivitySourceError("local_api_invalid_response")
                payload = response.read(MAX_LOCAL_BYTES + 1)
            observed = _parse_local_rows(payload, {pid})
            rows.extend(observed)
        except (HTTPError, URLError, OSError, TimeoutError, UnicodeError,
                ValueError, ActivitySourceError):
            unavailable.append(pid)
    rows.sort(key=lambda row: (row[1], row[0], row[2]), reverse=True)
    return rows, unavailable


def window_digest_html(ledger, rows, project=None):
    """Source-separated visible results; CI is never treated as code delivery."""
    now = datetime.now(timezone.utc)
    projects = [project] if project else sorted(ledger["coverage"])
    labels = (1, 6, 12)
    github = {pid: [] for pid in projects}
    local = {pid: [] for pid in projects}
    for item in ledger["events"]:
        pid = item["project"]
        stamp = item.get("source_timestamp")
        if pid in github and stamp:
            # First-observed branch tips have no source time and are excluded.
            at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if at.tzinfo is not None:
                github[pid].append((at.astimezone(timezone.utc), item))
    for pid, stamp, _ in rows:
        if pid in local:
            at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            local[pid].append(at.astimezone(timezone.utc))
    output = []
    for pid in projects:
        source_info = ledger["coverage"][pid]
        # Coverage reports the original fetch state, not a live GitHub check.
        # Always show the actual last-fetch timestamp so an old 'recent_window'
        # receipt cannot be mistaken for recent activity.
        source_label = ("Volledig bronvenster" if source_info.get("status") == "recent_window"
                        else "Gedeeltelijke bron") + " · opgehaald " + (
                            source_info.get("last_successful_source_fetch") or "onbekend")
        for hours in labels:
            lower = now - timedelta(hours=hours)
            entries = [item for at, item in github[pid] if lower <= at <= now]
            measures = (
                sum(i["kind"] == "pr_merged" and i.get("merged_main") is True for i in entries),
                sum(i["kind"] == "main_commit" for i in entries),
                sum(i["kind"] == "pr_created" for i in entries),
                sum(i["kind"] == "ci_run" and i.get("conclusion") == "success" for i in entries),
                sum(i["kind"] == "ci_run" and i.get("conclusion") in
                    ("failure", "timed_out", "action_required") for i in entries),
                sum(lower <= stamp <= now for stamp in local[pid]),
            )
            output.append("<tr><th scope='row'>" + h(pid) + "</th><td>" +
                          h(hours) + " uur</td>" +
                          "".join("<td>" + h(value) + "</td>" for value in measures) +
                          "<td>" + h(source_label) + "</td></tr>")
    clock = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        "<section aria-labelledby='activity-windows'><h2 id='activity-windows'>"
        "Wat is er gebeurd in 1, 6 en 12 uur?</h2>"
        "<p>Opgesteld <time datetime='" + h(clock) + "'>" + h(clock) +
        "</time>. Gebeurtenissen uit GitHub en de VPS blijven apart. "
        "PR-merges, main-commits en groene CI zijn geen optelbare productfeatures. "
        "Een zichtbare nul betekent niet dat er geen werk is uitgevoerd. "
        "Ontbrekende branch-brontijden tellen niet als recente activiteit.</p>"
        "<div class='wrap'><table><caption class='sr-only'>Per-project activiteitsvensters</caption>"
        "<thead><tr><th>Project</th><th>Venster</th><th>PR-merges naar main</th>"
        "<th>Main-commits</th><th>Nieuwe PR's</th><th>CI groen</th>"
        "<th>CI fout</th><th>Lokale events</th><th>GitHub-bron</th></tr></thead>"
        "<tbody>" + "".join(output) + "</tbody></table></div>"
        "<p>Bronvensters zijn beperkt en soms verouderd. "
        "CI-succes bewijst nooit een merge, release, deploy of hervatte worker.</p>"
        "</section>"
    )


def pr_worklist_html(ledger, project=None):
    """Fair, bounded review worklist from retained source metadata only.

    Advisory classification cannot grant reviewer consent, an owner claim,
    full CI success, production scope or merge/deploy authority.
    """
    projects = [project] if project else sorted(ledger["coverage"])
    snapshots = {pid: {} for pid in projects}
    merged = set()
    jobs = {}
    for item in ledger["events"]:
        pid, kind = item["project"], item["kind"]
        if pid not in snapshots:
            continue
        if kind == "ci_run":
            sha = item.get("sha")
            if sha:
                key = (pid, sha)
                existing = jobs.get(key)
                when = item.get("source_timestamp") or ""
                if existing is None or when > (existing.get("source_timestamp") or ""):
                    jobs[key] = item
        if kind not in ("pr_updated", "pr_merged"):
            continue
        number = item.get("number")
        if type(number) is not int:
            continue
        key = (pid, number)
        if kind == "pr_merged":
            merged.add(key)
            continue
        # Existing event IDs include update timestamps; keep only the newest
        # saved update for each PR and never mistake a historical opened
        # event for proof that an old PR remains open.
        previous = snapshots[pid].get(number)
        if previous is None or (item.get("source_timestamp") or "") > (
                previous.get("source_timestamp") or ""):
            snapshots[pid][number] = item
    streams = {}
    for pid in projects:
        candidates = [item for number, item in snapshots[pid].items()
                      if (pid, number) not in merged and
                      item.get("state") == "open"]
        candidates.sort(key=lambda e: (e.get("source_timestamp") or "",
                                       e["number"]), reverse=True)
        streams[pid] = candidates[:6]
    # Fair project order: no single high-volume repository can consume
    # the entire visible review worklist.
    visible = []
    for index in range(6):
        for pid in projects:
            if len(visible) >= 60:
                break
            if index < len(streams[pid]):
                visible.append((pid, streams[pid][index]))
    rows = []
    for pid, item in visible:
        number = item["number"]
        head = item.get("head_sha")
        job = jobs.get((pid, head))
        if item.get("draft") is True:
            next_step = "Concept: eigenaar moet scope en review bevestigen"
        elif job is None:
            next_step = "Vereiste CI en eigenaarstoestemming controleren"
        elif job.get("state") != "completed":
            next_step = "Laatst geziene CI is nog niet afgerond; runnerstatus controleren"
        elif job.get("conclusion") in ("failure", "timed_out", "action_required"):
            next_step = "Laatste CI had problemen; herstel bron of platform"
        else:
            next_step = "Alle vereiste checks, actuele claim en review apart verifiëren"
        ci = ("Niet waargenomen" if job is None else
              (job.get("conclusion") or job.get("state") or "Onbekend"))
        url = "https://github.com/" + ledger["coverage"][pid]["repository"] + "/pull/" + str(number)
        fetched = ledger["coverage"][pid].get("last_successful_source_fetch") or "onbekend"
        rows.append(
            "<tr><th scope='row'>" + h(pid) + "</th><td><a href='" + h(url) +
            "' target='_blank' rel='noopener noreferrer'>PR #" + h(number) + "</a></td><td>" +
            ("Concept" if item.get("draft") else "Open bij laatste bronleesactie") +
            "</td><td><code>" + h(head[:12] if head else "onbekend") +
            "</code></td><td>" + h(ci) + "</td><td>" +
            h(item.get("source_timestamp") or "onbekend") + "</td><td>" +
            h(fetched) + "</td><td>" + h(next_step) + "</td></tr>"
        )
    return (
        "<section aria-labelledby='pr-worklist-title'><h2 id='pr-worklist-title'>"
        "PR-werkvoorraad · alleen ter beoordeling</h2>"
        "<p>Maximaal zes laatst waargenomen open PR's per project, eerlijk verdeeld. "
        "Een open PR in oude GitHub-gegevens kan inmiddels gewijzigd of gesloten zijn. "
        "De CI-kolom is uitsluitend de laatst waargenomen run voor de exacte SHA, "
        "niet de volledige verplichte checksuite. Geen PR krijgt hier merge-, "
        "deployment- of eigenaarstoestemming.</p>"
        "<div class='wrap'><table><thead><tr><th>Project</th><th>PR</th>"
        "<th>Status</th><th>HEAD</th><th>Laatste CI</th><th>PR bijgewerkt</th>"
        "<th>Bron opgehaald</th><th>Volgende controle</th></tr></thead><tbody>" +
        ("".join(rows) or "<tr><td colspan='8'>Geen open PR's in de beperkte "
         "historische bron gevonden; dit is geen bewijs van een lege GitHub-queue.</td></tr>") +
        "</tbody></table></div></section>"
    )


def combined_html(ledger, rows, project=None, unavailable=(), from_local_api=False):
    """Keep two sources visibly separate; never count GitHub and local twice."""
    html = render_html(ledger, project=project)
    visible = [row for row in rows if project is None or row[0] == project]
    count = {pid: 0 for pid in (sorted(ledger["coverage"]) if project is None else [project])}
    for pid, _, _ in visible:
        count[pid] += 1
    cards = "".join(
        "<div class='card'><strong>" + ("—" if pid in unavailable else h(amount)) +
        "</strong><small>" + h(pid) +
        (" bron onbereikbaar, geen nulmeting</small></div>" if pid in unavailable else
         " zichtbare lokale rijen (max 80 per bron), geen nulmeting</small></div>")
        for pid, amount in count.items()
    )
    body = "".join(
        "<tr><td>" + h(pid) + "</td><td><time datetime='" + h(when) +
        "'>" + h(when) + "</time></td><td>Lokaal geregistreerd; niet geverifieerd als merge of CI</td></tr>"
        for pid, when, _ in visible
    )
    section = (
        "<section aria-labelledby='local-source-title'><h2 id='local-source-title'>"
        "Lokale VPS-activiteit · eenmalige export</h2>"
        "<p>Afzonderlijke /api/activity-momentopname. Iedere eventbron is maximaal "
        "80 records diep; ontbrekende activiteit is geen nulmeting. " +
        ("Op verzoek direct via uitsluitend lokale 127.0.0.1-verbinding ingelezen; "
         "geen blijvende beschikbaarheid of GitHub-livesynchronisatie aangetoond.</p>"
         if from_local_api else
         "Aangeleverde JSON-export; geen actuele VPS-verbinding aangetoond.</p>")
        "<div class='grid'>" + cards + "</div><div class='wrap'><table>"
        "<thead><tr><th scope='col'>Project</th><th scope='col'>Bron-tijd</th>"
        "<th scope='col'>Betekenis</th></tr></thead><tbody>" +
        (body or "<tr><td colspan='3'>Geen lokale regels in deze export; "
         "andere activiteit is mogelijk.</td></tr>") +
        "</tbody></table></div></section>"
    )
    pivot = "</nav></header>"
    if html.count(pivot) != 1:
        raise ActivitySourceError("activity_report_markup_unexpected")
    html = html.replace(pivot, pivot + window_digest_html(ledger, rows, project) + pr_worklist_html(ledger, project) + section, 1)
    html = html.replace("Dit overzicht omvat geen lokale VPS-events.",
                        "Lokale VPS-events worden hieronder als afzonderlijke export getoond.", 1)
    html = html.replace("<title>zCloud · Privé GitHub-activiteit</title>",
                        "<title>zCloud · Privé gecombineerde activiteit</title>", 1)
    html = html.replace("<h1>Projectactiviteit · GitHub</h1>",
                        "<h1>Projectactiviteit · VPS en GitHub</h1>", 1)
    html = html.replace("Bron: private zCloud GitHub-ledger.",
                        "Bronnen: private GitHub-ledger en afzonderlijke lokale VPS-export.", 1)
    return html


def main(argv=None):
    parser = argparse.ArgumentParser(description="Combine private retained GitHub activity and one local VPS event export into offline HTML")
    parser.add_argument("--ledger", required=True, help="Existing private GitHub source ledger (0600)")
    local = parser.add_mutually_exclusive_group(required=True)
    local.add_argument("--local-json", help="Saved raw GET /api/activity response; never printed")
    local.add_argument("--local-api", action="store_true",
                       help="Read per-project GET from fixed loopback 127.0.0.1:8765 only")
    parser.add_argument("--output", required=True, help="Private local HTML outside public/ (0600)")
    parser.add_argument("--project", help="Optional one-project filter")
    parser.add_argument("--refresh-github", action="store_true",
                        help="Explicitly GET bounded allowlisted GitHub sources and update private ledger")
    parser.add_argument("--projects", help="Required with --refresh-github: comma-separated canonical projects (max 12)")
    args = parser.parse_args(argv)
    # Fail before any GET or write if an output would replace source evidence.
    destinations = [Path(args.ledger).resolve(), Path(args.output).resolve()]
    if args.local_json:
        destinations.append(Path(args.local_json).resolve())
    if len(set(destinations)) != len(destinations):
        raise ActivitySourceError("activity_input_output_paths_must_differ")
    if args.projects and not args.refresh_github:
        raise ActivitySourceError("activity_project_refresh_flag_required")
    if args.refresh_github:
        if not args.projects:
            raise ActivitySourceError("activity_refresh_project_selection_required")
        selected_ids = args.projects.split(",")
        if (not 1 <= len(selected_ids) <= 12 or
                len(set(selected_ids)) != len(selected_ids) or
                any(not pid or pid != pid.strip() for pid in selected_ids)):
            raise ActivitySourceError("activity_project_selection_invalid")
        registry = portfolio_registry(Path(__file__).resolve().parents[1] / "projects.json")
        if any(pid not in registry for pid in selected_ids):
            raise ActivitySourceError("activity_project_not_allowlisted")
        if args.project and args.project not in selected_ids:
            raise ActivitySourceError("activity_requested_project_not_selected")
        receipt = collect(registry, selected_ids, reader=GithubReader(
            token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_READ_TOKEN")))
        merge_and_store(Path(args.ledger), receipt)
        ledger = _read(Path(args.ledger))
        # A previous ledger may contain other projects. Never disclose them
        # in a report from this explicitly bounded selection.
        ledger = dict(ledger)
        ledger["coverage"] = {pid: ledger["coverage"][pid] for pid in selected_ids}
        ledger["events"] = [item for item in ledger["events"]
                            if item["project"] in ledger["coverage"]]
    else:
        ledger = _read(Path(args.ledger))
    if args.project is not None and args.project not in ledger["coverage"]:
        raise ActivitySourceError("activity_project_not_allowlisted")
    selected = {args.project} if args.project else set(ledger["coverage"])
    if args.local_api:
        rows, unavailable = read_local_api(selected)
    else:
        rows, unavailable = read_local_snapshot(args.local_json, selected), ()
    save_private(args.output, combined_html(
        ledger, rows, project=args.project,
        unavailable=set(unavailable), from_local_api=args.local_api))
    print("Private combined Activity report saved; GitHub and VPS source windows are bounded, not live synchronized.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ActivitySourceError as exc:
        sys.stderr.write("Private combined Activity report unavailable: " + str(exc) + "\n")
        raise SystemExit(2)
