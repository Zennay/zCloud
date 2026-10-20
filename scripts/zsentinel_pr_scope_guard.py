#!/usr/bin/env python3
"""zSentinel: cross-project live PR file-scope collision observation.

Read-only, one-shot GitHub evidence for a proposed source edit. An empty
sample NEVER grants claim, edit, merge, release, or worker authority.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
MAX_REGISTRY_BYTES = 512 * 1024
MAX_REPLY_BYTES = 1024 * 1024
MAX_OPEN_PAGE = 100
MAX_INSPECT = 48
MAX_PATHS = 12
MAX_SOURCE_FILES = 80
SHA = re.compile(r"^[a-f0-9]{40}$")
PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
REPO = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")
SEGMENT = re.compile(r"^[A-Za-z0-9_.@+ -]{1,180}$")


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in evidence")
        result[key] = value
    return result


def edit_path(value):
    """Require explicit repository-relative file or trailing-slash folder."""
    if not isinstance(value, str) or not 1 <= len(value) <= 180:
        raise ValueError("invalid target path length")
    if value.startswith("/") or "\\" in value or "//" in value:
        raise ValueError("target must be a relative POSIX path")
    parts = value.rstrip("/").split("/")
    if any(part in ("", ".", "..") or not SEGMENT.fullmatch(part)
           for part in parts):
        raise ValueError("unsafe or ambiguous path segment")
    return value


def registry(path):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_REGISTRY_BYTES:
        raise ValueError("missing or oversized canonical registry")
    rows = json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=unique)
    if not isinstance(rows, list):
        raise ValueError("invalid canonical registry")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key, url = row.get("id"), row.get("repo_url")
        if not isinstance(key, str) or not PROJECT.fullmatch(key) or not isinstance(url, str):
            continue
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "github.com"
                or parsed.query or parsed.fragment or parsed.path.count("/") != 2):
            continue
        repo = parsed.path.lstrip("/")
        if not REPO.fullmatch(repo):
            continue
        if key in result:
            raise ValueError("duplicate project in canonical registry")
        result[key] = repo
    if not 1 <= len(result) <= 24:
        raise ValueError("no canonical registered projects")
    return result


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirects)


def github_json(repo, suffix, token):
    if not REPO.fullmatch(repo) or not suffix.startswith("/") or "//" in suffix:
        raise ValueError("invalid GitHub request")
    url = "https://api.github.com/repos/" + repo + suffix
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "zsentinel-scope-guard",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with OPENER.open(req, timeout=10) as response:
            if response.status != 200:
                raise ValueError("github_source_unavailable")
            body = response.read(MAX_REPLY_BYTES + 1)
            if len(body) > MAX_REPLY_BYTES:
                raise ValueError("github_response_oversized")
    except (TimeoutError, OSError, urllib.error.URLError, urllib.error.HTTPError):
        raise ValueError("github_source_unavailable_or_denied") from None
    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("github_payload_invalid") from None


def open_prs(repo, token):
    suffix = "/pulls?state=open&per_page=100&page=1&sort=updated&direction=desc"
    rows = github_json(repo, suffix, token)
    if not isinstance(rows, list) or len(rows) > MAX_OPEN_PAGE:
        raise ValueError("invalid open PR listing")
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid PR metadata")
        n = row.get("number")
        base = row.get("base")
        head = row.get("head")
        base_repo = base.get("repo") if isinstance(base, dict) else None
        head_sha = head.get("sha") if isinstance(head, dict) else None
        if (type(n) is not int or not 0 < n < 100000000 or n in seen
                or not isinstance(base_repo, dict)
                or base_repo.get("full_name") != repo
                or not isinstance(head_sha, str) or not SHA.fullmatch(head_sha)):
            raise ValueError("incomplete PR identity")
        seen.add(n)
        result.append({"number": n, "head_sha": head_sha,
                       "url": "https://github.com/" + repo + "/pull/" + str(n)})
    return result


def changed_paths(repo, number, token):
    rows = github_json(
        repo, "/pulls/" + str(number) + "/files?per_page=100&page=1", token)
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("invalid PR file listing")
    files = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid PR file")
        names = [row.get("filename")]
        if row.get("status") == "renamed":
            names.append(row.get("previous_filename"))
        for name in names:
            if not isinstance(name, str):
                raise ValueError("missing source file identity")
            files.add(edit_path(name))
    return sorted(files), len(rows) == 100


def selected_source_pr(repo, number, token):
    """Pin one explicitly selected existing PR, never infer its owner."""
    if type(number) is not int or not 0 < number < 100000000:
        raise ValueError("invalid selected source PR")
    receipt = github_json(repo, "/pulls/" + str(number), token)
    base = receipt.get("base") if isinstance(receipt, dict) else None
    head = receipt.get("head") if isinstance(receipt, dict) else None
    base_repo = base.get("repo") if isinstance(base, dict) else None
    sha = head.get("sha") if isinstance(head, dict) else None
    if (receipt.get("number") != number or receipt.get("state") != "open"
            or not isinstance(base_repo, dict)
            or base_repo.get("full_name") != repo
            or not isinstance(sha, str) or not SHA.fullmatch(sha)):
        raise ValueError("selected PR is not a canonical open project PR")
    files, truncated = changed_paths(repo, number, token)
    if truncated or not 1 <= len(files) <= MAX_SOURCE_FILES:
        raise ValueError("source PR file scope is incomplete or exceeds 80 paths")
    return {"number": number, "url": "https://github.com/" + repo +
            "/pull/" + str(number), "head_sha": sha, "paths": files}


def overlaps(wanted, changed):
    # A path ending in '/' explicitly denotes a directory, otherwise one file.
    return changed.startswith(wanted) if wanted.endswith("/") else changed == wanted


def observe(repo, wanted, token=None, limit=24, source_pr_number=None):
    if (not REPO.fullmatch(repo) or not isinstance(wanted, list)
            or len(wanted) > MAX_PATHS or len(set(wanted)) != len(wanted)
            or type(limit) is not int or not 1 <= limit <= MAX_INSPECT
            or not wanted and source_pr_number is None):
        raise ValueError("invalid scope request")
    wanted = [edit_path(path) for path in wanted]
    source = (selected_source_pr(repo, source_pr_number, token)
              if source_pr_number is not None else None)
    if source is not None:
        wanted = sorted(set(wanted).union(source["paths"]))
    before = open_prs(repo, token)
    selected = before[:limit]
    hits = []
    truncated_files = []
    for pr in selected:
        if source is not None and pr["number"] == source["number"]:
            continue
        files, truncated = changed_paths(repo, pr["number"], token)
        if truncated:
            truncated_files.append(pr["number"])
        for candidate in wanted:
            matches = [file for file in files if overlaps(candidate, file)]
            if matches:
                hits.append({"path": candidate, "pr_number": pr["number"],
                             "pr_url": pr["url"], "observed_head_sha": pr["head_sha"],
                             "matching_files": matches[:32],
                             "matches_truncated": len(matches) > 32})
    after = open_prs(repo, token)
    snapshot_stable = before == after
    source_stable = True
    if source is not None:
        fresh = github_json(repo, "/pulls/" + str(source["number"]), token)
        current_head = fresh.get("head") if isinstance(fresh, dict) else None
        source_stable = (fresh.get("state") == "open"
                         and isinstance(current_head, dict)
                         and current_head.get("sha") == source["head_sha"])
    complete = (source_stable and len(before) < MAX_OPEN_PAGE and len(before) <= limit
                and not truncated_files and snapshot_stable)
    return {
        "schema": "zsentinel-pr-scope-observation-v1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": repo,
        "requested_paths": wanted,
        "selected_source_pr": (None if source is None else {
            "url": source["url"], "number": source["number"],
            "head_sha": source["head_sha"],
            "source_head_stable": source_stable,
            "source_file_count": len(source["paths"])
        }),
        "source": "bounded_double_read_current_open_github_prs",
        "observed_open_prs": len(before),
        "inspected_prs": len(selected),
        "open_pr_page_may_be_truncated": len(before) == MAX_OPEN_PAGE,
        "inspection_limit_reached": len(before) > limit,
        "pr_file_lists_truncated": truncated_files,
        "open_pr_snapshot_stable": snapshot_stable,
        "pr_file_scope_sample_complete": complete,
        "observed_overlaps": hits,
        "source_result": ("overlap_observed" if hits else
                          "no_overlap_in_sample_only" if not complete else
                          "no_overlap_in_open_pr_inventory_only"),
        "notion_claims_verified": False,
        "prless_branches_verified": False,
        "worker_file_leases_verified": False,
        "file_edit_authorized": False,
        "claim_authorized": False,
        "merge_authorized": False,
        "writes_performed": False,
    }


def operator_text(report):
    lines = [
        "zSentinel | Live PR-bestandsscope (ALLEEN LEZEN)",
        "Repository: " + report["repository"],
        "Te wijzigen paden: " + ", ".join(report["requested_paths"]),
        "Bron-PR: " + (report["selected_source_pr"]["url"]
                        if report["selected_source_pr"] else "handmatige doelpaden"),
        "Open PR's gezien: " + str(report["observed_open_prs"]) +
        "; bestandlijsten bekeken: " + str(report["inspected_prs"]),
        "Bron compleet voor geopende PR's: " +
        ("ja" if report["pr_file_scope_sample_complete"] else "NEE / onzeker"),
    ]
    if report["observed_overlaps"]:
        for hit in report["observed_overlaps"]:
            lines.append("OVERLAP " + hit["path"] + " | PR #" +
                         str(hit["pr_number"]) + " | " + hit["pr_url"] +
                         " | " + ", ".join(hit["matching_files"]))
    else:
        lines.append("Geen overlap in de gelezen steekproef; dit is GEEN vrijgave.")
    if not report["open_pr_snapshot_stable"]:
        lines.append("Let op: open PR-lijst of heads veranderden tijdens inspectie.")
    if (report["selected_source_pr"] is not None
            and not report["selected_source_pr"]["source_head_stable"]):
        lines.append("Let op: geselecteerde bron-PR veranderde tijdens inspectie.")
    if report["pr_file_lists_truncated"]:
        lines.append("Onvolledige PR-bestandlijsten: " +
                     ", ".join(str(n) for n in report["pr_file_lists_truncated"]))
    lines.append("Notion-claims, PR-loze branches en bestandsleases ONBEKEND.")
    lines.append("NOOIT toestemming om werk over te nemen, te claimen of te mergen.")
    return "\n".join(lines)


def html_view(report):
    rows = []
    for hit in report["observed_overlaps"]:
        link = escape(hit["pr_url"], quote=True)
        rows.append("<tr><td>" + escape(hit["path"]) +
                    "</td><td><a rel='noreferrer noopener' href='" + link +
                    "'>PR #" + str(hit["pr_number"]) + "</a></td><td>" +
                    escape(", ".join(hit["matching_files"])) + "</td></tr>")
    if not rows:
        rows.append("<tr><td colspan='3'>Geen botsing waargenomen in gelezen steekproef; geen vrijgave.</td></tr>")
    coverage = "Volledige huidige open-PR steekproef" if report["pr_file_scope_sample_complete"] else "Onvolledige bron: controleer overige PR's"
    return ("<!doctype html><html lang='nl'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>zSentinel • bestandsscope</title>"
            "<style>body{font:16px system-ui;background:#121820;color:#f2f5f8;"
            "max-width:950px;margin:30px auto;padding:20px}h1{font-size:1.6rem}"
            "table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #536;"
            "padding:12px;text-align:left;overflow-wrap:anywhere}a{color:#aaccff}"
            "aside{border-left:3px solid #aaa;padding:1rem;background:#222d3a}</style>"
            "<h1>zSentinel · Live PR-bestandsscope</h1><p>" +
            escape(report["repository"]) + " · " + escape(coverage) +
            "</p><aside>Dit is uitsluitend gelezen GitHub-PR-bewijs. "
            "Notion-claims, niet-geopende branches en workerleases zijn NIET geverifieerd. "
            "Geen edit-, claim-, merge- of deploytoestemming.</aside>"
            "<h2>Waargenomen bestandsoverlap</h2><table><thead><tr>"
            "<th>Gevraagd pad</th><th>PR</th><th>Gewijzigde bestanden</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            "<p>Bronmoment " + escape(report["observed_at_utc"]) + "</p></html>")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--project", required=True)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--source-pr", type=int, help="Gebruik alle gewijzigde paden van deze open PR als doel; sla PR zelf over.")
    parser.add_argument("--max-prs", type=int, default=24)
    parser.add_argument("--format", choices=("text", "json", "html"), default="text")
    args = parser.parse_args(argv)
    try:
        repos = registry(args.registry)
        if args.project not in repos:
            raise ValueError("unknown canonical project")
        report = observe(repos[args.project], args.path,
                         os.environ.get("GITHUB_TOKEN"), args.max_prs, args.source_pr)
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.format == "html":
            print(html_view(report))
        else:
            print(operator_text(report))
        # Exit status 2 is a real observed collision; status 3 is insufficient
        # evidence even if some overlaps are visible. Never exit 0 for permission.
        return 2 if report["observed_overlaps"] else (
            3 if not report["pr_file_scope_sample_complete"] else 0)
    except (ValueError, OSError) as error:
        print("zSentinel scope unavailable: " + str(error), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
