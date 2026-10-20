#!/usr/bin/env python3
"""Combine saved targeted zSentinel issue-search pages into an offline operator view.

Historical evidence only: this code performs no network calls, task claims,
GitHub writes, reviews, merges, CI dispatch or live recovery actions.
"""
import argparse
import datetime as dt
import html
import json
import os
from pathlib import Path
import stat
import sys

MAX_PAGES = 50
MAX_BYTES = 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_PRS = 200
PAGE_SIZE = 20
MAX_SPREAD_SECONDS = 600


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def private_json(path, data):
    """Create a NEW private file without following destination symlinks."""
    raw = (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
           + "\n").encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("private snapshot exceeds 1 MiB")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(fd)


def _read_private(path):
    """Bounded nofollow read; no shared/public evidence files."""
    p = Path(path)
    before = p.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o077:
        raise ValueError("source must be a private regular file (0600)")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    fd = os.open(str(p), flags)
    try:
        actual = os.fstat(fd)
        if (not stat.S_ISREG(actual.st_mode) or actual.st_mode & 0o077
                or actual.st_ino != before.st_ino or actual.st_dev != before.st_dev
                or actual.st_size > MAX_BYTES):
            raise ValueError("source changed, is public, or exceeds size cap")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("source exceeds size cap")
        after = p.lstat()
        final = os.fstat(fd)
        if (after.st_ino != actual.st_ino or after.st_dev != actual.st_dev
                or not stat.S_ISREG(after.st_mode)
                or final.st_size != actual.st_size
                or final.st_mtime_ns != actual.st_mtime_ns):
            raise ValueError("source path changed during read")
    finally:
        os.close(fd)
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs), len(raw)


def _utc(text):
    if not isinstance(text, str):
        raise ValueError("missing observation time")
    parsed = dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=dt.timezone.utc)


def load_pages(paths):
    """Verify targeted page identity, limits and coherent bounded evidence."""
    if not 1 <= len(paths) <= MAX_PAGES or len(set(paths)) != len(paths):
        raise ValueError("expected 1–50 distinct saved pages")
    pages = []
    total_size = 0
    for path in paths:
        page, length = _read_private(path)
        total_size += length
        if total_size > MAX_TOTAL:
            raise ValueError("aggregate evidence exceeds 8 MiB")
        pages.append(page)
    selected = {}
    reference = None
    observations = []
    problems = set()
    total_hits = None
    repo = None
    candidate_seen = set()
    for payload in pages:
        if not isinstance(payload, dict) or payload.get("selection") != "TARGETED_OPEN_ISSUE":
            raise ValueError("not a targeted zSentinel issue snapshot")
        issue, coverage = payload.get("targeted_issue"), payload.get("targeted_coverage")
        projects = payload.get("project_ids")
        if (not isinstance(issue, dict) or not isinstance(coverage, dict)
                or not isinstance(projects, list) or len(projects) != 1
                or not isinstance(projects[0], str)):
            raise ValueError("missing target project, issue or coverage")
        number, url = issue.get("number"), issue.get("url")
        if (type(number) is not int or not 1 <= number <= 9999999
                or type(url) is not str or not url.startswith("https://github.com/")
                or not url.endswith("/issues/" + str(number))):
            raise ValueError("invalid canonical issue")
        identity = (projects[0], number, url)
        if reference is None:
            reference = identity
            repo = url[len("https://github.com/"):].split("/issues/", 1)[0]
            if not repo or "/" not in repo:
                raise ValueError("invalid issue repository")
        elif identity != reference:
            raise ValueError("mixed project / issue snapshots")
        index = coverage.get("candidate_page")
        count = coverage.get("search_total")
        start, end = coverage.get("window_start"), coverage.get("window_end")
        examined = coverage.get("search_examined")
        if (type(index) is not int or not 1 <= index <= MAX_PAGES
                or index in selected or type(count) is not int or count < 0
                or type(start) is not int or start != (index - 1) * PAGE_SIZE
                or type(end) is not int or not start <= end <= start + PAGE_SIZE
                or type(examined) is not int or not 0 <= examined <= end - start
                or coverage.get("source") != "TARGETED_ISSUE_SEARCH"
                or type(coverage.get("search_complete")) is not bool
                or type(payload.get("source_partial")) is not bool):
            raise ValueError("invalid page boundaries / duplicate page")
        candidate_numbers = coverage.get("candidate_pr_numbers")
        if (not isinstance(candidate_numbers, list)
                or len(candidate_numbers) != examined
                or any(type(n) is not int or not 1 <= n <= 9999999
                       for n in candidate_numbers)
                or len(set(candidate_numbers)) != len(candidate_numbers)):
            problems.add("UNVERIFIED_CANDIDATE_IDS")
        else:
            if candidate_seen.intersection(candidate_numbers):
                problems.add("CANDIDATE_OVERLAP_BETWEEN_PAGES")
            candidate_seen.update(candidate_numbers)
        selected[index] = payload
        observations.append(_utc(payload.get("observed_utc")))
        if total_hits is None:
            total_hits = count
        elif total_hits != count:
            problems.add("SEARCH_TOTAL_CHANGED")
        limitations = payload.get("partial_reasons")
        if not isinstance(limitations, list) or any(not isinstance(x, dict) or
                not isinstance(x.get("reason"), str) for x in limitations):
            raise ValueError("missing explicit search source limitations")
        # A saved page is incomplete by itself when other search pages exist.
        # That expected paging marker may be resolved by a full coherent
        # bundle. All *other* limitations survive bundling.
        substantive = {item["reason"] for item in limitations
                       if item["reason"] != "SEARCH_WINDOW_PARTIAL"}
        if substantive:
            problems.update(substantive)
        if payload["source_partial"] and not limitations:
            problems.add("UNEXPLAINED_PARTIAL_SOURCE")
        if not coverage["search_complete"] and not limitations:
            problems.add("UNEXPLAINED_INCOMPLETE_COVERAGE")
        if issue.get("current_state") != "OPEN_RECONFIRMED":
            problems.add("ISSUE_NOT_RECONFIRMED")
        if examined != end - start:
            problems.add("CANDIDATES_NOT_ALL_EXAMINED")
        if count > 1000:
            problems.add("GITHUB_SEARCH_HARD_CAP")
        if not isinstance(payload.get("prs"), list) or len(payload["prs"]) > 4:
            raise ValueError("invalid bounded PR page")
        for pr in payload["prs"]:
            if (not isinstance(pr, dict) or pr.get("repo") != repo
                    or type(pr.get("number")) is not int
                    or not 1 <= pr["number"] <= 9999999):
                raise ValueError("invalid PR identity in page")
    spread = (max(observations) - min(observations)).total_seconds()
    if spread > MAX_SPREAD_SECONDS:
        problems.add("OBSERVATIONS_OVER_TEN_MINUTES_APART")
    expected_pages = max(1, (min(total_hits, 1000) + PAGE_SIZE - 1) // PAGE_SIZE)
    if total_hits and any(page["targeted_coverage"]["window_end"] <
                          min(total_hits, 1000, index * PAGE_SIZE)
                          for index, page in selected.items()):
        problems.add("SHORT_OR_SHIFTED_SEARCH_WINDOW")
    missing = [n for n in range(1, expected_pages + 1) if n not in selected]
    if missing:
        problems.add("UNSAVED_SEARCH_PAGES")
    prs = {}
    for index, payload in sorted(selected.items()):
        for pr in payload["prs"]:
            number = pr["number"]
            row = {
                "number": number, "url": f"https://github.com/{repo}/pull/{number}",
                "head": pr.get("head"), "status": pr.get("status", "UNKNOWN"),
                "review": pr.get("review", "UNKNOWN"),
                "checks": pr.get("checks", "UNKNOWN"),
                "workflows": pr.get("workflows", "UNKNOWN"),
                "actions": pr.get("actions", []),
                "observed_issue_refs": pr.get("observed_issue_refs", []),
                "source_page": index,
            }
            if (not isinstance(row["head"], str) or len(row["head"]) != 40
                    or any(c not in "0123456789abcdef" for c in row["head"])
                    or not isinstance(row["actions"], list)
                    or not all(isinstance(x, str) for x in row["actions"])):
                raise ValueError("malformed PR gate observation")
            if number in prs:
                other = prs[number]
                if (other["head"] != row["head"] or other["review"] != row["review"]
                        or other["checks"] != row["checks"]
                        or other["workflows"] != row["workflows"]):
                    problems.add("PR_OBSERVATION_DRIFT")
                else:
                    problems.add("DUPLICATE_PR_ACROSS_PAGES")
                # Retain the earliest observation, never infer a stable current state.
                continue
            prs[number] = row
    if len(prs) > MAX_PRS:
        raise ValueError("unbounded combined PR source")
    result = {
        "project_id": reference[0], "issue_number": reference[1],
        "issue_url": reference[2], "repository": repo,
        "observed_first_utc": min(observations).isoformat(),
        "observed_last_utc": max(observations).isoformat(),
        "snapshot_pages": sorted(selected),
        "missing_pages": missing, "github_search_total_observed": total_hits,
        "candidate_rows_examined": sum(x["targeted_coverage"]["search_examined"]
                                       for x in selected.values()),
        "distinct_linked_prs_observed": len(prs),
        "unique_candidate_ids_observed": len(candidate_seen),
        "prs": [prs[n] for n in sorted(prs)],
        "partial_reasons": sorted(problems),
        # Any saved GitHub pages are historical. Even complete historical
        # coverage cannot authorize a current claim, review, merge, or release.
        # Contiguous pages are not an atomic search result, even without
        # detected overlap: result ranking can shift between requests.
        "coverage": ("CONTIGUOUS_SAVED_WINDOWS_NONATOMIC" if not problems
                     else "PARTIAL_HISTORICAL_SNAPSHOT"),
        "current_github_state": "NOT_VERIFIED",
        "claim_free": "NOT_INFERABLE", "merge_authorized": False,
        "main_merged": "NOT_VERIFIED", "live_verified": False,
    }
    return result


def render(data):
    esc = lambda value: html.escape(str(value), quote=True)
    header = (
        "<!doctype html><html lang='nl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>zSentinel • opgeslagen zoekpagina's</title>"
        "<style>body{font:16px system-ui;max-width:1100px;margin:auto;padding:1.3rem;"
        "background:#f8f9fb;color:#17202a}table{border-collapse:collapse;width:100%}"
        "th,td{padding:.65rem;text-align:left;border-bottom:1px solid #ccd1d7}"
        "a{color:#124b8e}th{background:#edf0f4}code{overflow-wrap:anywhere}"
        ".note{border-left:4px solid #555;padding:1rem;background:#fff}</style></head><body>"
        "<h1>zSentinel · opgeslagen PR-zoekpagina's</h1>"
        "<p class='note'>Historische, lokale, read-only bronwaarnemingen — geen actuele "
        "ownerclaim, GitHub-mergeautorisatie, groen CI-bewijs of live herstel.</p>"
        f"<p><a href='{esc(data['issue_url'])}'>Issue #{data['issue_number']}</a> "
        f"· project {esc(data['project_id'])} · {esc(data['coverage'])}</p>"
        f"<p>Pagina's: {esc(', '.join(map(str, data['snapshot_pages'])))} · "
        f"ontbrekend: {esc(', '.join(map(str, data['missing_pages'])) or 'geen')} · "
        f"kandidaten bekeken: {data['candidate_rows_examined']} · "
        f"gevonden PRs: {data['distinct_linked_prs_observed']}</p>"
        f"<p>Momentopnamen: {esc(data['observed_first_utc'])} tot "
        f"{esc(data['observed_last_utc'])}</p>"
    )
    if data["partial_reasons"]:
        header += ("<p class='note'><strong>Bronbeperkingen:</strong> " +
                   esc(", ".join(data["partial_reasons"])) + "</p>")
    rows = []
    for pr in data["prs"]:
        rows.append(
            "<tr><td><a href='" + esc(pr["url"]) + "'>#" + str(pr["number"]) +
            "</a> · pagina " + str(pr["source_page"]) + "</td><td><code>" +
            esc(pr["head"][:12]) + "</code></td><td>" + esc(pr["status"]) +
            "</td><td>" + esc(pr["review"]) + "</td><td>" +
            esc(pr["checks"]) + "</td><td>" + esc(pr["workflows"]) +
            "</td><td>" + esc("; ".join(pr["actions"])[:300]) + "</td></tr>")
    return (header + "<table><thead><tr><th>PR / pagina</th><th>HEAD</th>"
            "<th>Status</th><th>Review</th><th>Checks</th><th>Workflows</th>"
            "<th>Waargenomen vervolg</th></tr></thead><tbody>" +
            "".join(rows) + "</tbody></table></body></html>\n")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--page", action="append", required=True,
                     help="private JSON created via --snapshot-output (repeat ≤50)")
    cli.add_argument("--format", choices=("text", "json", "html"), default="text")
    cli.add_argument("--output", help="exclusive-create private result file, never overwrite")
    args = cli.parse_args()
    try:
        result = load_pages(args.page)
        if args.format == "json":
            message = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        elif args.format == "html":
            message = render(result)
        else:
            message = (f"{result['issue_url']} {result['coverage']}; "
                       f"saved pages={result['snapshot_pages']}; "
                       f"missing={result['missing_pages']}; "
                       f"PRs={result['distinct_linked_prs_observed']}; "
                       f"reasons={result['partial_reasons']}; "
                       "current GitHub/claim/merge state NOT VERIFIED\n")
        if args.output:
            if len(message.encode("utf-8")) > MAX_BYTES:
                raise ValueError("output exceeds 1 MiB")
            _write_text(args.output, message)
        else:
            print(message, end="")
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        cli.error(f"private historical source invalid: {type(exc).__name__}")


def _write_text(path, message):
    data = message.encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("output exceeds 1 MiB")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
