#!/usr/bin/env python3
"""zSentinel: cross-project live PR file-scope collision observation.

Read-only, one-shot GitHub evidence for a proposed source edit. An empty
sample NEVER grants claim, edit, merge, release, or worker authority.
"""
from __future__ import annotations

import argparse
import csv
import io
from datetime import datetime, timezone, timedelta
from html import escape
import json
import os
from pathlib import Path
import re
import sys
import stat
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from zsentinel_product_issue_inbox import QA as QA_ONLY_TITLE
from zsentinel_private_report_writer import write_private_report

ROOT = Path(__file__).resolve().parent.parent
MAX_REGISTRY_BYTES = 512 * 1024
MAX_REPLY_BYTES = 1024 * 1024
MAX_OPEN_PAGE = 48
MAX_SEARCH_PAGES = 50
MAX_MULTI_PAGES = 3
MAX_MULTI_PRS = 48
MAX_INSPECT = 48
MAX_PATHS = 12
MAX_SOURCE_FILES = 80
SHA = re.compile(r"^[a-f0-9]{40}$")
PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
REPO = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in evidence")
        result[key] = value
    return result


def csv_formula_inert(value):
    """Make spreadsheet formulas inert after Unicode whitespace/format prefixes.

    CSV quoting protects record structure, not spreadsheet formula execution.
    Keep the original evidence intact; prefix an apostrophe when the first
    meaningful character can start a formula. Unicode format controls (for
    example zero-width and bidi marks) are ignored for detection only.
    """
    if type(value) is not str:
        raise ValueError("CSV cell must be a string")
    index = 0
    while index < len(value):
        char = value[index]
        category = unicodedata.category(char)
        if char.isspace() or category in ("Cf", "Zs", "Zl", "Zp"):
            index += 1
            continue
        break
    if value[index:].startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def edit_path(value):
    """Require explicit repository-relative file or trailing-slash folder."""
    if not isinstance(value, str) or not 1 <= len(value) <= 180:
        raise ValueError("invalid target path length")
    if value.startswith("/") or "\\" in value or "//" in value:
        raise ValueError("target must be a relative POSIX path")
    parts = value.rstrip("/").split("/")
    if any(part in ("", ".", "..") or len(part) > 180 or
           any(ord(char) < 32 or ord(char) == 127 or
               unicodedata.category(char).startswith("C") for char in part)
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


def source_clock(value):
    """Normalize one GitHub activity timestamp without accepting naive/future data."""
    if type(value) is not str or len(value) > 48:
        raise ValueError("missing bounded GitHub activity timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid GitHub activity timestamp") from None
    if (stamp.tzinfo is None or stamp.utcoffset() is None
            or stamp > datetime.now(timezone.utc) + timedelta(minutes=5)):
        raise ValueError("timezone-free or future GitHub activity timestamp")
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds")


def open_prs(repo, token, page_size=MAX_OPEN_PAGE, page=1):
    # Fetch one beyond the requested inspection count when possible.
    # A full page is UNKNOWN, even if it may be the actual final page.
    if (type(page_size) is not int or not 1 <= page_size <= MAX_OPEN_PAGE
            or type(page) is not int or not 1 <= page <= MAX_SEARCH_PAGES):
        raise ValueError("invalid bounded open-PR page")
    suffix = ("/pulls?state=open&per_page=" + str(page_size) +
              "&page=" + str(page) + "&sort=updated&direction=desc")
    rows = github_json(repo, suffix, token)
    if not isinstance(rows, list) or len(rows) > page_size:
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
        base_sha = base.get("sha") if isinstance(base, dict) else None
        body = row.get("body")
        title = row.get("title")
        updated_at = source_clock(row.get("updated_at"))
        product_candidate = (type(title) is str and 0 < len(title) <= 256
                             and not QA_ONLY_TITLE.match(title))
        # Reuse the existing issue-first product semantics: explicit #N in
        # a PR title is an unverified hint even when the body has no refs.
        # Body and title coverage are independent. A truncated/invalid body
        # must never silently count as a fully inspected issue source.
        body_covered = body is None or (type(body) is str and len(body) <= 32000)
        title_covered = type(title) is str and 0 < len(title) <= 256
        body_refs = ({int(m.group(1)) for m in re.finditer(
            r"(?<![A-Za-z0-9/_])#([1-9][0-9]{0,7})\b",
            body or "")} if body_covered else set())
        title_refs = ({int(m.group(1)) for m in re.finditer(
            r"(?<![A-Za-z0-9_])#([1-9][0-9]{0,6})(?![0-9])",
            title)} if title_covered else set())
        refs_covered = (body_covered and title_covered
                        and len(body_refs | title_refs) <= MAX_REFERENCED_ISSUES)
        refs = sorted(body_refs | title_refs)[:MAX_REFERENCED_ISSUES]
        ref_provenance = {
            str(n): (["body"] if n in body_refs else []) +
                    (["title"] if n in title_refs else [])
            for n in refs
        }
        if (type(n) is not int or not 0 < n < 100000000 or n in seen
                or not isinstance(base_repo, dict)
                or base_repo.get("full_name") != repo
                or not isinstance(head_sha, str) or not SHA.fullmatch(head_sha)
                or not isinstance(base_sha, str) or not SHA.fullmatch(base_sha)):
            raise ValueError("incomplete PR identity")
        seen.add(n)
        result.append({"number": n, "head_sha": head_sha,
                       "base_sha": base_sha, "updated_at": updated_at,
                       "referenced_issue_numbers_unverified": refs,
                       "issue_reference_provenance": ref_provenance,
                       "issue_reference_title_covered": title_covered,
                       "issue_reference_body_covered": body_covered,
                       "issue_reference_text_covered": refs_covered,
                       "product_issue_reference_candidate": product_candidate,
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



MAX_FOCUSED_PR_FILE_PAGES = 3
MAX_FOCUSED_PR_PATHS = 240


def changed_paths_focused(repo, number, token):
    """Bounded complete path collection for one *explicitly* selected PR.

    This is never used for the ordinary multi-PR collision scan: large
    portfolios keep their original one-page-per-PR GitHub rate budget.
    Exactly 100 files on the last permitted page is ambiguous and refused.
    """
    paths = set()
    seen_filenames = set()
    for page in range(1, MAX_FOCUSED_PR_FILE_PAGES + 1):
        rows = github_json(
            repo, "/pulls/" + str(number) +
            "/files?per_page=100&page=" + str(page), token)
        if type(rows) is not list or len(rows) > 100:
            raise ValueError("invalid focused PR page")
        for row in rows:
            if type(row) is not dict:
                raise ValueError("invalid focused PR file metadata")
            name = row.get("filename")
            if type(name) is not str:
                raise ValueError("missing focused PR path")
            name = edit_path(name)
            if name in seen_filenames:
                raise ValueError("duplicate focused PR file across pages")
            seen_filenames.add(name)
            paths.add(name)
            if row.get("status") == "renamed":
                previous = row.get("previous_filename")
                if type(previous) is not str:
                    raise ValueError("missing renamed source path")
                paths.add(edit_path(previous))
            if len(paths) > MAX_FOCUSED_PR_PATHS:
                raise ValueError("focused PR exceeds bounded path budget")
        if len(rows) < 100:
            if not paths:
                raise ValueError("focused PR contains no source files")
            return sorted(paths), page
    raise ValueError("focused PR file pagination remains incomplete")


def selected_source_pr(repo, number, token, expanded_files=False):
    """Pin one explicitly selected existing PR, never infer its owner."""
    if type(number) is not int or not 0 < number < 100000000:
        raise ValueError("invalid selected source PR")
    receipt = github_json(repo, "/pulls/" + str(number), token)
    if not isinstance(receipt, dict):
        raise ValueError("selected source PR metadata unavailable")
    base = receipt.get("base")
    head = receipt.get("head")
    base_repo = base.get("repo") if isinstance(base, dict) else None
    sha = head.get("sha") if isinstance(head, dict) else None
    base_sha = base.get("sha") if isinstance(base, dict) else None
    updated_at = source_clock(receipt.get("updated_at"))
    if (receipt.get("number") != number or receipt.get("state") != "open"
            or not isinstance(base_repo, dict)
            or base_repo.get("full_name") != repo
            or not isinstance(sha, str) or not SHA.fullmatch(sha)
            or not isinstance(base_sha, str) or not SHA.fullmatch(base_sha)):
        raise ValueError("selected PR is not a canonical open project PR")
    # Directly specified old PRs need the same visible product-only policy
    # as the bounded recent-PR inventory. Do not silently elevate test/QA
    # work just because it has an explicit numeric PR reference.
    title = receipt.get("title")
    if type(title) is not str or not 0 < len(title) <= 256:
        raise ValueError("selected PR product title unavailable")
    product_candidate = QA_ONLY_TITLE.match(title) is None
    if expanded_files:
        files, observed_pages = changed_paths_focused(repo, number, token)
    else:
        files, truncated = changed_paths(repo, number, token)
        if truncated or not 1 <= len(files) <= MAX_SOURCE_FILES:
            raise ValueError("source PR file scope is incomplete or exceeds 80 paths")
        observed_pages = 1
    return {"number": number, "url": "https://github.com/" + repo +
            "/pull/" + str(number), "head_sha": sha,
            "base_sha": base_sha, "updated_at": updated_at, "paths": files,
            "source_file_pages_observed": observed_pages,
            "product_candidate_based_on_title": product_candidate,
            "read_only": True}


def overlaps(wanted, changed):
    """Detect exact and file/directory ancestor collisions in either direction."""
    # A path ending in "/" denotes a directory. Git trees cannot contain a file
    # and a directory at the same path, or a file where the other path needs an
    # ancestor directory. Compare segment boundaries so "src2" never matches
    # "src/".
    target = wanted.rstrip("/")
    return (changed == target or changed.startswith(target + "/")
            or target.startswith(changed + "/"))


def overlap_relation(wanted, changed):
    """Explain the exact relationship for a path pair already known to overlap."""
    target = wanted.rstrip("/")
    if changed == target:
        return ("same_path_file_directory_conflict" if wanted.endswith("/")
                else "exact_path")
    if wanted.endswith("/") and changed.startswith(wanted):
        return "target_directory_contains_changed_file"
    if changed.startswith(target + "/"):
        return "target_file_conflicts_with_descendant_path"
    if target.startswith(changed + "/"):
        return "changed_file_is_ancestor_of_target"
    raise ValueError("path pair does not overlap")
def observe(repo, wanted, token=None, limit=24, source_pr_number=None,
            expanded_source_files=False, open_page=1, page_count=1):
    if (not REPO.fullmatch(repo) or not isinstance(wanted, list)
            or len(wanted) > MAX_PATHS or len(set(wanted)) != len(wanted)
            or type(limit) is not int or not 1 <= limit <= MAX_INSPECT
            or not wanted and source_pr_number is None
            or expanded_source_files and source_pr_number is None
            or type(open_page) is not int or not 1 <= open_page <= MAX_SEARCH_PAGES
            or type(page_count) is not int or not 1 <= page_count <= MAX_MULTI_PAGES
            or open_page + page_count - 1 > MAX_SEARCH_PAGES
            or page_count * limit > MAX_MULTI_PRS):
        raise ValueError("invalid scope request")
    wanted = [edit_path(path) for path in wanted]
    source = (selected_source_pr(
        repo, source_pr_number, token, expanded_files=expanded_source_files)
        if source_pr_number is not None else None)
    if source is not None and expanded_source_files:
        if source["product_candidate_based_on_title"] is not True:
            raise ValueError("expanded source scope is product-only")
    if source is not None:
        wanted = sorted(set(wanted).union(source["paths"]))
    page_size = limit if page_count > 1 else min(MAX_OPEN_PAGE, limit + 1)
    page_numbers = list(range(open_page, open_page + page_count))
    snapshots_before = [open_prs(repo, token, page_size, page=p) for p in page_numbers]
    before = [pr for rows in snapshots_before for pr in rows]
    selected = before if page_count > 1 else before[:limit]
    duplicate_numbers = len({pr["number"] for pr in before}) != len(before)
    page_receipts = [{
        "page": n, "first_position": (n - 1) * page_size + 1,
        "last_position": (n - 1) * page_size + len(rows),
        "observed_prs": len(rows),
    } for n, rows in zip(page_numbers, snapshots_before)]
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
                             "observed_base_sha": pr["base_sha"], "matching_files": matches[:32],
                             "matching_path_relations": [
                                 {"requested_path": candidate, "changed_path": changed,
                                  "relation": overlap_relation(candidate, changed)}
                                 for changed in matches[:32]],
                             "matches_truncated": len(matches) > 32})
    snapshots_after = [open_prs(repo, token, page_size, page=p) for p in page_numbers]
    snapshot_stable = not duplicate_numbers and snapshots_before == snapshots_after
    source_stable = True
    if source is not None:
        fresh = github_json(repo, "/pulls/" + str(source["number"]), token)
        if not isinstance(fresh, dict):
            raise ValueError("selected source PR freshness unavailable")
        current_head = fresh.get("head")
        current_base = fresh.get("base")
        source_stable = (fresh.get("state") == "open"
                         and isinstance(current_head, dict)
                         and current_head.get("sha") == source["head_sha"]
                         and isinstance(current_base, dict)
                         and current_base.get("sha") == source["base_sha"])
    complete = (page_count == 1 and open_page == 1 and source_stable
                and len(before) < page_size and len(before) <= limit
                and not truncated_files and snapshot_stable)
    return {
        "schema": "zsentinel-pr-scope-observation-v1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": repo,
        "requested_paths": wanted,
        "selected_source_pr": (None if source is None else {
            "url": source["url"], "number": source["number"],
            "head_sha": source["head_sha"],
            "base_sha": source["base_sha"],
            "source_head_stable": source_stable,
            "source_file_count": len(source["paths"]),
            "source_file_pages_observed": source["source_file_pages_observed"],
            "expanded_source_file_pages_requested": bool(expanded_source_files),
        }),
        "source": "bounded_double_read_current_open_github_prs",
        "observed_open_prs": len(before),
        "open_pr_page_number": open_page,
        "open_pr_page_count": page_count,
        "open_pr_page_receipts": page_receipts,
        "open_pr_page_size": page_size,
        "open_pr_window_start": (open_page - 1) * page_size + 1,
        "open_pr_window_end": (open_page - 1) * page_size + len(before),
        "earlier_pages_not_inspected": open_page > 1,
        "cross_page_duplicate_prs_observed": duplicate_numbers,
        "inspected_prs": sum(pr["number"] != source["number"]
                              for pr in selected) if source is not None else len(selected),
        # Only the final selected page controls an undiscovered next page;
        # earlier intentionally full pages are normal in a contiguous scan.
        "open_pr_page_may_be_truncated": bool(snapshots_before and
                                              len(snapshots_before[-1]) == page_size),
        "inspection_limit_reached": len(before) >= limit * page_count,
        "requested_scan_max_prs": limit * page_count,
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
        "Open PR-zoekpagina: " + str(report["open_pr_page_number"]) +
        " · posities " + str(report["open_pr_window_start"]) + "–" +
        str(report["open_pr_window_end"]) +
        (" · vorige pagina's NIET gelezen" if report["earlier_pages_not_inspected"] else ""),
        "Open PR's over " + str(report["open_pr_page_count"]) +
        " pagina's gezien: " + str(report["observed_open_prs"]) +
        "; bestandlijsten bekeken: " + str(report["inspected_prs"]),
        "Bron compleet voor geopende PR's: " +
        ("ja" if report["pr_file_scope_sample_complete"] else "NEE / onzeker"),
    ]
    for row in report["open_pr_page_receipts"]:
        lines.append("BRONPAGINA " + str(row["page"]) +
                     " · posities " + str(row["first_position"]) + "–" +
                     str(row["last_position"]) + " · " +
                     str(row["observed_prs"]) + " PRs")
    if report["cross_page_duplicate_prs_observed"]:
        lines.append("ONSTABIEL: dezelfde PR op meerdere pagina's.")
    if report["selected_source_pr"] is not None:
        src = report["selected_source_pr"]
        lines.append(
            "Bron-PR bestanden: " + str(src["source_file_count"]) +
            " paden uit " + str(src["source_file_pages_observed"]) +
            " gelezen pagina('s); uitgebreide bron: " +
            str(src["expanded_source_file_pages_requested"]) + ".")
    if report["observed_overlaps"]:
        for hit in report["observed_overlaps"]:
            relations = "; ".join(
                item["changed_path"] + " [" + item["relation"] + "]"
                for item in hit["matching_path_relations"])
            lines.append("OVERLAP " + hit["path"] + " | PR #" +
                         str(hit["pr_number"]) + " | " + hit["pr_url"] +
                         " | " + ", ".join(hit["matching_files"]) +
                         " | relatie: " + relations)
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
    page_list = "".join(
        "<li>Pagina " + str(row["page"]) + " · posities " +
        str(row["first_position"]) + "–" + str(row["last_position"]) +
        " · " + str(row["observed_prs"]) + " PRs</li>"
        for row in report["open_pr_page_receipts"])
    for hit in report["observed_overlaps"]:
        link = escape(hit["pr_url"], quote=True)
        relations = "; ".join(
            item["changed_path"] + " [" + item["relation"] + "]"
            for item in hit["matching_path_relations"])
        rows.append("<tr><td>" + escape(hit["path"]) +
                    "</td><td><a rel='noreferrer noopener' href='" + link +
                    "'>PR #" + str(hit["pr_number"]) + "</a></td><td>" +
                    escape(", ".join(hit["matching_files"])) +
                    "<br><small>Relatie: " + escape(relations) +
                    "</small></td></tr>")
    if not rows:
        rows.append("<tr><td colspan='3'>Geen botsing waargenomen in gelezen steekproef; geen vrijgave.</td></tr>")
    coverage = "Volledige huidige open-PR steekproef" if report["pr_file_scope_sample_complete"] else "Onvolledige bron: controleer overige PR's"
    source = report["selected_source_pr"]
    source_link = ("<p>Bron-PR: <a rel='noreferrer noopener' href='" +
                   escape(source["url"], quote=True) + "'>#" +
                   str(source["number"]) + "</a> — exacte head " +
                   escape(source["head_sha"][:12]) +
                   " · " + str(source["source_file_count"]) + " paden uit " +
                   str(source["source_file_pages_observed"]) +
                   " pagina('s) · uitgebreid: " +
                   str(source["expanded_source_file_pages_requested"]) +
                   "</p>" if source else "")
    targets = "<ul>" + "".join(
        "<li><code>" + escape(path) + "</code></li>"
        for path in report["requested_paths"]) + "</ul>"
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
            "</p><p>Open PR-zoekpagina " + str(report["open_pr_page_number"]) +
            " · posities " + str(report["open_pr_window_start"]) + "–" +
            str(report["open_pr_window_end"]) +
            (" · eerdere pagina's niet onderzocht" if report["earlier_pages_not_inspected"] else "") +
            " · geen uitspraak over overige pagina's.</p><ul>" +
            page_list + "</ul>" + source_link +
            "<details><summary>Bekeken doelpaden (" +
            str(len(report["requested_paths"])) + ")</summary>" +
            targets + "</details><p>Geïnspecteerde andere PR's: " +
            str(report["inspected_prs"]) + " / " +
            str(report["observed_open_prs"]) + "</p>"
            "<aside>Dit is uitsluitend gelezen GitHub-PR-bewijs. "
            "Notion-claims, niet-geopende branches en workerleases zijn NIET geverifieerd. "
            "Geen edit-, claim-, merge- of deploytoestemming.</aside>"
            "<h2>Waargenomen bestandsoverlap</h2><table><thead><tr>"
            "<th>Gevraagd pad</th><th>PR</th><th>Gewijzigde bestanden</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            "<p>Bronmoment " + escape(report["observed_at_utc"]) + "</p></html>")



MAX_CONFLICT_PAIRS = 200
MAX_SHARED_FILES_PER_PAIR = 24
MAX_SCOPE_PATH_PAIRS_PER_PAIR = 24
MAX_REFERENCED_ISSUES = 32
MAX_DUPLICATE_ISSUE_GROUPS = 80
MAX_IDENTITY_LOOKUPS = 8
MAX_LINKS_PER_DUPLICATE_ISSUE = 8


def issue_identity(repo, number, token):
    """Optional bounded GitHub identity + source-derived issue priority.

    Identity is not a PR-to-issue fix link. Priority only applies to
    explicitly observed real *open* issues with bounded source metadata.
    """
    if not REPO.fullmatch(repo) or type(number) is not int or not 0 < number < 100000000:
        raise ValueError("invalid issue identity")

    def observation(status, priority="not_observed"):
        return {"status": status, "priority": priority}

    try:
        result = github_json(repo, "/issues/" + str(number), token)
    except ValueError:
        return observation("source_unavailable")
    if not isinstance(result, dict) or result.get("number") != number:
        return observation("identity_unconfirmed")
    if "pull_request" in result:
        if result.get("html_url") == (
                "https://github.com/" + repo + "/pull/" + str(number)):
            return observation("reference_is_pull_request_not_issue")
        return observation("identity_unconfirmed")
    if result.get("html_url") != (
            "https://github.com/" + repo + "/issues/" + str(number)):
        return observation("identity_unconfirmed")
    if result.get("state") == "closed":
        return observation("confirmed_closed_issue_at_observation")
    if result.get("state") != "open":
        return observation("issue_state_unknown")

    # Only explicit P0/P1 labels or title prefixes from this issue's actual
    # GitHub response determine priority. Anything malformed stays unknown.
    title, labels = result.get("title"), result.get("labels")
    priority = "unclassified"
    if type(title) is str and len(title) <= 500 and (
            isinstance(labels, list) and len(labels) <= 100):
        names = set()
        for label in labels:
            if type(label) is not dict or type(label.get("name")) is not str:
                names.clear()
                break
            names.add(label["name"].strip().casefold())
        prefix = re.match(r"^\s*\[?P([01])(?:\]|\b|\s*[-:])", title, re.IGNORECASE)
        p0 = {"p0", "priority:p0", "priority/p0", "critical", "severity:critical",
              "severity/p0", "urgent", "blocker"}
        p1 = {"p1", "priority:p1", "priority/p1", "high", "severity:high",
              "severity/p1"}
        if names & p0 or (prefix and prefix.group(1) == "0"):
            priority = "P0"
        elif names & p1 or (prefix and prefix.group(1) == "1"):
            priority = "P1"
    return observation("confirmed_open_issue_at_observation", priority)


MAX_CLUSTER_DISPLAY_PATHS = 16


def linked_pr_collision_clusters(observed, pairs):
    """Group PRs by transitive shared filenames: observation, not permission."""
    by_num = {row["number"]: row for row in observed}
    parent = {number: number for number in by_num}

    def root(number):
        while parent[number] != number:
            parent[number] = parent[parent[number]]
            number = parent[number]
        return number

    for left, right in sorted(pairs):
        x, y = root(left), root(right)
        if x != y:
            parent[max(x, y)] = min(x, y)

    groups = {}
    for (left, right), paths in pairs.items():
        leader = root(left)
        group = groups.setdefault(leader, {
            "pr_numbers": set(), "shared_paths": set(),
            "observed_edge_count": 0, "degree": {},
        })
        group["pr_numbers"].update((left, right))
        group["shared_paths"].update(paths)
        group["observed_edge_count"] += 1
        group["degree"][left] = group["degree"].get(left, 0) + 1
        group["degree"][right] = group["degree"].get(right, 0) + 1

    clusters = []
    for group in groups.values():
        numbers = sorted(group["pr_numbers"])
        paths = sorted(group["shared_paths"])
        ranked = sorted(numbers, key=lambda n: (-group["degree"][n], n))
        most_links = group["degree"][ranked[0]]
        # The exact file -> touching PR matrix must come from source file
        # receipts, not the capped collision-pair cards. No additional GETs.
        observed_files = {
            number: set(by_num[number]["file_paths_observed"]) for number in numbers
        }
        file_ownership = [{
            "path": path,
            "touching_prs": [{
                "number": number, "url": by_num[number]["url"],
                "head_sha": by_num[number]["head_sha"],
                "source_file_list_complete": by_num[number]["file_list_complete"],
            } for number in ranked if path in observed_files[number]],
            "ownership_verified": False,
        } for path in paths[:MAX_CLUSTER_DISPLAY_PATHS]]
        clusters.append({
            "pr_count_observed": len(numbers),
            "shared_file_edge_count_observed": group["observed_edge_count"],
            "distinct_shared_file_count_observed": len(paths),
            "shared_files_sample": paths[:MAX_CLUSTER_DISPLAY_PATHS],
            "shared_file_pr_matrix": file_ownership,
            "shared_files_sample_truncated": len(paths) > MAX_CLUSTER_DISPLAY_PATHS,
            "participating_prs": [
                {"number": number, "url": by_num[number]["url"],
                 "head_sha": by_num[number]["head_sha"],
                 "base_sha": by_num[number]["base_sha"],
                 "other_prs_with_shared_files_observed": group["degree"][number]}
                for number in ranked
            ],
            "highest_connected_pr_numbers_observed": [
                number for number in ranked if group["degree"][number] == most_links
            ],
            "component_has_unobserved_prs": "NOT_INFERABLE",
            "ownership_verified": False,
            "merge_conflict_verified": False,
            "integration_order_authorized": False,
        })
    clusters.sort(key=lambda c: (-c["distinct_shared_file_count_observed"],
                                 -c["pr_count_observed"],
                                 -c["shared_file_edge_count_observed"],
                                 min(pr["number"] for pr in c["participating_prs"])))
    for index, cluster in enumerate(clusters, 1):
        cluster["component_number"] = index
    return clusters


MAX_SCOPE_CLUSTER_CARDS = 40
MAX_SCOPE_CLUSTER_EXAMPLES = 8


def linked_pr_scope_collision_clusters(observed, pairs, pair_counts):
    """Group transitive file/directory ancestor conflicts without implying merge authority."""
    by_num = {row["number"]: row for row in observed}
    parent = {number: number for number in by_num}

    def root(number):
        while parent[number] != number:
            parent[number] = parent[parent[number]]
            number = parent[number]
        return number

    for left, right in sorted(pairs):
        x, y = root(left), root(right)
        if x != y:
            parent[max(x, y)] = min(x, y)

    groups = {}
    for (left, right), examples in sorted(pairs.items()):
        leader = root(left)
        group = groups.setdefault(leader, {
            "pr_numbers": set(), "pair_count": 0,
            "path_conflict_count": 0, "degree": {}, "examples": [],
        })
        group["pr_numbers"].update((left, right))
        group["pair_count"] += 1
        group["path_conflict_count"] += pair_counts[(left, right)]
        group["degree"][left] = group["degree"].get(left, 0) + 1
        group["degree"][right] = group["degree"].get(right, 0) + 1
        # Keep a small component-wide sample. The pair counts retain the
        # exact observed total even when these example records are truncated.
        for conflict in examples:
            if len(group["examples"]) >= MAX_SCOPE_CLUSTER_EXAMPLES:
                break
            group["examples"].append({
                "left_pr_number": left, "right_pr_number": right,
                "left_pr_url": by_num[left]["url"],
                "right_pr_url": by_num[right]["url"],
                "left_pr_head_sha": by_num[left]["head_sha"],
                "right_pr_head_sha": by_num[right]["head_sha"],
                "left_path": conflict["left_path"],
                "right_path": conflict["right_path"],
                "relation": conflict["relation"],
            })

    clusters = []
    for group in groups.values():
        numbers = sorted(group["pr_numbers"])
        ranked = sorted(numbers, key=lambda number: (-group["degree"][number], number))
        clusters.append({
            "pr_count_observed": len(numbers),
            "scope_collision_edge_count_observed": group["pair_count"],
            "path_conflict_count_observed": group["path_conflict_count"],
            "path_conflicts_sample": group["examples"],
            "path_conflicts_sample_truncated":
                group["path_conflict_count"] > len(group["examples"]),
            "participating_prs": [{
                "number": number, "url": by_num[number]["url"],
                "head_sha": by_num[number]["head_sha"],
                "base_sha": by_num[number]["base_sha"],
                "file_count_observed": by_num[number]["file_count_observed"],
                "file_list_complete": by_num[number]["file_list_complete"],
                "other_prs_with_scope_conflicts_observed": group["degree"][number],
            } for number in ranked],
            "component_has_unobserved_prs": "NOT_INFERABLE",
            "ownership_verified": False,
            "merge_conflict_verified": False,
            "integration_order_authorized": False,
        })
    clusters.sort(key=lambda cluster: (
        -cluster["path_conflict_count_observed"],
        -cluster["pr_count_observed"],
        -cluster["scope_collision_edge_count_observed"],
        min(pr["number"] for pr in cluster["participating_prs"]),
    ))
    for index, cluster in enumerate(clusters, 1):
        cluster["component_number"] = index
    return clusters


def observe_conflict_graph(repo, token=None, limit=24, open_page=1,
                           page_count=1, resolve_issue_identity=False):
    """Show PR-to-PR file collisions over a bounded, double-read open PR window.

    Collision means *shared pathname*, NOT merge conflict, active owner, worker
    claim, or proof that either PR must be rejected.
    """
    if (not REPO.fullmatch(repo) or type(limit) is not int or not 1 <= limit <= MAX_INSPECT
            or type(open_page) is not int or not 1 <= open_page <= MAX_SEARCH_PAGES
            or type(page_count) is not int or not 1 <= page_count <= MAX_MULTI_PAGES
            or open_page + page_count - 1 > MAX_SEARCH_PAGES
            or page_count * limit > MAX_MULTI_PRS
            or type(resolve_issue_identity) is not bool):
        raise ValueError("invalid bounded cross-page graph request")
    # A single-page lookahead skips one PR in a multi-page traversal.
    # Multi-page mode therefore requests an exact contiguous page size.
    page_size = limit if page_count > 1 else min(MAX_OPEN_PAGE, limit + 1)
    page_numbers = list(range(open_page, open_page + page_count))
    snapshots_before = [open_prs(repo, token, page_size, page=p) for p in page_numbers]
    before = [pr for rows in snapshots_before for pr in rows]
    chosen = before if page_count > 1 else before[:limit]
    duplicate_numbers = len({pr["number"] for pr in before}) != len(before)
    page_receipts = [{
        "page": n, "first_position": (n - 1) * page_size + 1,
        "last_position": (n - 1) * page_size + len(rows),
        "observed_prs": len(rows),
    } for n, rows in zip(page_numbers, snapshots_before)]
    file_owners = {}
    suspicious_lists = []
    observed = []
    issue_links = []
    for pr in chosen:
        files, truncated = changed_paths(repo, pr["number"], token)
        observed.append({
            "number": pr["number"], "url": pr["url"],
            "head_sha": pr["head_sha"], "base_sha": pr["base_sha"],
            "updated_at": pr["updated_at"],
            "product_candidate_based_on_title": pr["product_issue_reference_candidate"],
            "file_count_observed": len(files),
            "file_paths_observed": files,
            "file_list_complete": not truncated,
        })
        if truncated:
            suspicious_lists.append(pr["number"])
        if pr["product_issue_reference_candidate"]:
            for issue_number in pr["referenced_issue_numbers_unverified"]:
                issue_links.append({
                    "issue_number": issue_number,
                    "pr_url": pr["url"], "pr_head_sha": pr["head_sha"],
                    "mentioned_in": pr["issue_reference_provenance"].get(
                        str(issue_number), []),
                    "relation": "unverified_title_or_body_mention_only",
                    "owner_verified": False,
                    "fix_verified": False,
                })
        for path in files:
            file_owners.setdefault(path, set()).add(pr["number"])
    # A second product overlap exists even when PRs modify disjoint files:
    # several open PR titles/bodies mentioning the same issue are hints
    # only, NOT verified fixes, owners or merge authority.
    by_issue = {}
    for link in issue_links:
        by_issue.setdefault(link["issue_number"], {})[link["pr_url"]] = {
            "url": link["pr_url"], "head_sha": link["pr_head_sha"],
            "mentioned_in": link["mentioned_in"]}
    duplicate_issue_groups = sorted(
        [(issue, sorted(prs.values(), key=lambda item: item["url"]))
         for issue, prs in by_issue.items() if len(prs) > 1],
        key=lambda row: (-len(row[1]), row[0]))
    checked_identities = {}
    if resolve_issue_identity:
        # Strictly optional and bounded. A body/title '#N' can identify an
        # actual pull request, not an issue; source evidence distinguishes it.
        for number, _ in duplicate_issue_groups[:MAX_IDENTITY_LOOKUPS]:
            checked_identities[number] = issue_identity(repo, number, token)
    def observed_status(number):
        return checked_identities.get(number, {}).get("status", "not_observed")

    def observed_priority(number):
        return checked_identities.get(number, {}).get("priority", "not_observed")

    # P0-then-P1 first *among verified lookups*; unobserved candidates stay
    # visible and unknown rather than being assigned an invented priority.
    ranked_groups = sorted(duplicate_issue_groups, key=lambda row: (
        0 if observed_status(row[0]) == "confirmed_open_issue_at_observation"
             and observed_priority(row[0]) == "P0" else
        1 if observed_status(row[0]) == "confirmed_open_issue_at_observation"
             and observed_priority(row[0]) == "P1" else 2,
        -len(row[1]), row[0]))

    def next_human_step(status):
        # Display-only operator routing. Nothing here claims, edits, merges,
        # schedules a workflow or authorizes a release.
        if status == "confirmed_open_issue_at_observation":
            return "review_existing_prs_for_same_open_issue"
        if status == "confirmed_closed_issue_at_observation":
            return "review_closed_issue_before_more_product_work"
        if status == "reference_is_pull_request_not_issue":
            return "inspect_referenced_pr_not_issue"
        return "verify_reference_identity_before_routing"

    duplicate_issue_cards = [{
        "operator_next_step": next_human_step(observed_status(issue)),
        "observed_issue_priority": observed_priority(issue),
        "issue_url": "https://github.com/" + repo + "/issues/" + str(issue),
        "issue_number": issue,
        "identity_status": observed_status(issue),
        "identity_lookup_authorizes_fix_or_claim": False,
        "open_pr_mentions_observed": len(prs),
        "shown_pr_sources": prs[:MAX_LINKS_PER_DUPLICATE_ISSUE],
        "source_pr_list_truncated": len(prs) > MAX_LINKS_PER_DUPLICATE_ISSUE,
        "relation": "same_issue_number_unverified_open_pr_title_or_body_only",
        "issue_fix_verified": False,
        "common_owner_verified": False,
        "merge_authorized": False,
    } for issue, prs in ranked_groups[:MAX_DUPLICATE_ISSUE_GROUPS]]
    pairs = {}
    for path, numbers in file_owners.items():
        involved = sorted(numbers)
        for index, left in enumerate(involved):
            for right in involved[index + 1:]:
                pairs.setdefault((left, right), []).append(path)
    # Exact same-file collisions and file/directory ancestor collisions are
    # distinct evidence classes. The latter is missed by a filename-keyed
    # index (e.g. PR A adds `src` while PR B adds `src/app.py`).
    by_num = {pr["number"]: pr for pr in observed}
    def scope_pr_ref(pr):
        # Ancestor-pair cards can be numerous; avoid copying each PR's full
        # changed-file inventory into every edge in the bounded output.
        return {key: pr[key] for key in (
            "number", "url", "head_sha", "base_sha", "updated_at",
            "file_count_observed", "file_list_complete")}
    scope_pairs = {}
    scope_pair_counts = {}
    scope_prs = sorted(observed, key=lambda row: row["number"])
    for left_index, left in enumerate(scope_prs):
        for right in scope_prs[left_index + 1:]:
            # Count every matching pair, but retain only the bounded sorted
            # prefix needed by the operator report. Two 100-file PRs can
            # otherwise allocate up to 10,000 conflict dictionaries for one
            # edge even though the public contract displays at most 24.
            conflict_count = 0
            examples = []
            for left_path in left["file_paths_observed"]:
                for right_path in right["file_paths_observed"]:
                    if left_path == right_path:
                        continue  # reported separately as an exact shared file
                    if right_path.startswith(left_path + "/"):
                        relation = "left_file_conflicts_with_right_descendant"
                    elif left_path.startswith(right_path + "/"):
                        relation = "right_file_conflicts_with_left_descendant"
                    else:
                        continue
                    conflict_count += 1
                    if len(examples) < MAX_SCOPE_PATH_PAIRS_PER_PAIR:
                        examples.append({"left_path": left_path,
                                         "right_path": right_path,
                                         "relation": relation})
            if conflict_count:
                key = (left["number"], right["number"])
                # changed_paths() returns sorted unique paths, so the retained
                # examples are deterministic without materializing the full edge.
                scope_pairs[key] = examples
                scope_pair_counts[key] = conflict_count
    ordered_scope = sorted(scope_pairs.items(),
                           key=lambda item: (-scope_pair_counts[item[0]], item[0]))
    scope_cards = [{
        "left": scope_pr_ref(by_num[left]), "right": scope_pr_ref(by_num[right]),
        "path_conflict_count_observed": scope_pair_counts[(left, right)],
        "path_conflicts": conflicts,
        "path_conflicts_truncated": scope_pair_counts[(left, right)] > len(conflicts),
        "action": "inspect_existing_prs_and_current_claims_before_edit",
        "merge_conflict_verified": False,
        "same_active_owner_verified": False,
    } for (left, right), conflicts in ordered_scope[:MAX_CONFLICT_PAIRS]]
    scope_pairs_truncated = len(ordered_scope) > MAX_CONFLICT_PAIRS
    # Priority for operator inspection: most shared files first, then exact
    # PR identifiers. This is NOT a claim or merge recommendation.
    ordered = sorted(pairs.items(), key=lambda item: (-len(item[1]), item[0]))
    cards = []
    for (a, b), paths in ordered[:MAX_CONFLICT_PAIRS]:
        cards.append({
            "left": by_num[a], "right": by_num[b],
            "shared_file_count_observed": len(paths),
            "shared_files": sorted(paths)[:MAX_SHARED_FILES_PER_PAIR],
            "shared_files_truncated": len(paths) > MAX_SHARED_FILES_PER_PAIR,
            "action": "inspect_existing_prs_and_current_claims_before_edit",
            "merge_conflict_verified": False,
            "same_active_owner_verified": False,
        })
    snapshots_after = [open_prs(repo, token, page_size, page=p) for p in page_numbers]
    stable = not duplicate_numbers and snapshots_before == snapshots_after
    collided = set()
    for left, right in pairs:
        collided.add(left)
        collided.add(right)
    for left, right in scope_pairs:
        collided.add(left)
        collided.add(right)
    # Combining bounded pages improves collision discovery, not claim admission.
    # With more than one page we never infer whole-inventory completeness.
    fully_sampled = (page_count == 1 and open_page == 1
                     and len(before) < page_size and len(before) <= limit
                     and not suspicious_lists and stable
                     and len(ordered) <= MAX_CONFLICT_PAIRS
                     and not scope_pairs_truncated)
    # An unpaired PR is a useful independent-work candidate ONLY after the
    # entire open-PR source sample is complete and stable. An unchecked PR
    # could otherwise touch the exact same path.
    not_paired = ([pr for pr in observed if pr["number"] not in collided]
                  if fully_sampled else [])
    clusters = linked_pr_collision_clusters(observed, pairs)
    scope_clusters = linked_pr_scope_collision_clusters(
        observed, scope_pairs, scope_pair_counts)
    shown_scope_clusters = scope_clusters[:MAX_SCOPE_CLUSTER_CARDS]
    return {
        "schema": "zsentinel-pr-file-collision-graph-v1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": repo,
        "source": "bounded_double_read_current_open_github_prs_and_files",
        "observed_open_prs": len(before),
        "open_pr_page_number": open_page,
        "open_pr_page_count": page_count,
        "open_pr_page_receipts": page_receipts,
        "open_pr_page_size": page_size,
        "open_pr_window_start": (open_page - 1) * page_size + 1,
        "open_pr_window_end": (open_page - 1) * page_size + len(before),
        "earlier_pages_not_inspected": open_page > 1,
        "cross_page_duplicate_prs_observed": duplicate_numbers,
        "requested_scan_max_prs": limit * page_count,
        "inspected_prs": len(chosen),
        "inspected_pr_heads": observed,
        "open_pr_issue_references_unverified": issue_links,
        "observed_duplicate_issue_groups": len(duplicate_issue_groups),
        "issue_identity_lookups_requested": bool(resolve_issue_identity),
        "issue_identity_lookups_performed": len(checked_identities),
        "issue_identity_lookup_budget": MAX_IDENTITY_LOOKUPS,
        # A requested GET may be denied, unavailable, or return an
        # ambiguous identity. Completed requests alone are not complete
        # *evidence*; keep unresolved observations explicitly incomplete.
        "issue_identity_lookup_incomplete":
            len(duplicate_issue_groups) > len(checked_identities)
            or any(v not in (
                "confirmed_open_issue_at_observation",
                "confirmed_closed_issue_at_observation",
                "reference_is_pull_request_not_issue")
                for v in (item["status"] for item in checked_identities.values())),
        "observed_confirmed_open_issue_groups": sum(
            item["status"] == "confirmed_open_issue_at_observation"
            for item in checked_identities.values()),
        "observed_confirmed_open_P0_issue_groups": sum(
            item["status"] == "confirmed_open_issue_at_observation"
            and item["priority"] == "P0"
            for item in checked_identities.values()),
        "observed_confirmed_open_P1_issue_groups": sum(
            item["status"] == "confirmed_open_issue_at_observation"
            and item["priority"] == "P1"
            for item in checked_identities.values()),
        "observed_issue_number_is_pr_groups": sum(
            item["status"] == "reference_is_pull_request_not_issue"
            for item in checked_identities.values()),
        "shown_duplicate_issue_groups": duplicate_issue_cards,
        "duplicate_issue_groups_truncated": (
            len(duplicate_issue_groups) > MAX_DUPLICATE_ISSUE_GROUPS),
        "duplicate_issue_group_coverage_complete": (
            page_count == 1 and open_page == 1
            and len(before) < page_size and len(before) <= limit
            and stable and not any(
                not pr["issue_reference_text_covered"] for pr in chosen)
            and len(duplicate_issue_groups) <= MAX_DUPLICATE_ISSUE_GROUPS),
        "issue_reference_body_coverage_complete":
            all(pr["issue_reference_body_covered"] for pr in chosen),
        "issue_reference_title_coverage_complete":
            all(pr["issue_reference_title_covered"] for pr in chosen),
        "issue_reference_text_coverage_complete":
            all(pr["issue_reference_text_covered"] for pr in chosen),
        "observed_file_collision_pairs": len(ordered),
        "shown_file_collision_pairs": cards,
        "observed_path_scope_collision_pairs": len(ordered_scope),
        "shown_path_scope_collision_pairs": scope_cards,
        "path_scope_collision_pairs_truncated": scope_pairs_truncated,
        "path_scope_examples_truncated": any(
            row["path_conflicts_truncated"] for row in scope_cards),
        "observed_connected_path_scope_collision_clusters": len(scope_clusters),
        "linked_pr_path_scope_collision_clusters": shown_scope_clusters,
        "path_scope_collision_clusters_truncated": len(scope_clusters) > MAX_SCOPE_CLUSTER_CARDS,
        "path_scope_cluster_examples_truncated": any(
            row["path_conflicts_sample_truncated"] for row in shown_scope_clusters),
        "scope_clusters_are_observed_edges_only_not_ownership_or_merge_authority": True,
        "observed_connected_pr_collision_clusters": len(clusters),
        "linked_pr_collision_clusters": clusters,
        "clusters_are_observed_edges_only_not_ownership_or_merge_authority": True,
        "unpaired_open_prs_in_sample_not_claim_verified": not_paired,
        "unpaired_means_no_shared_paths_in_sample_only": True,
        "unpaired_hidden_because_source_incomplete": not fully_sampled,
        "collision_cards_truncated": len(ordered) > MAX_CONFLICT_PAIRS,
        "pr_file_lists_truncated": suspicious_lists,
        # Only the final selected page controls an undiscovered next page;
        # earlier intentionally full pages are normal in a contiguous scan.
        "open_pr_page_may_be_truncated": bool(snapshots_before and
                                              len(snapshots_before[-1]) == page_size),
        "open_pr_snapshot_stable": stable,
        "pr_file_scope_sample_complete": fully_sampled,
        "source_result": ("collisions_observed" if ordered or ordered_scope else
                          "incomplete_no_collisions_observed" if not fully_sampled
                          else "no_open_pr_shared_paths_observed"),
        "findings_are_merge_conflicts": False,
        "claim_inventory_verified": False,
        "notion_claims_verified": False,
        "prless_branches_verified": False,
        "worker_file_leases_verified": False,
        "file_edit_authorized": False,
        "claim_authorized": False,
        "merge_authorized": False,
        "writes_performed": False,
    }


def graph_operator_text(report):
    result = [
        "zSentinel | PR-tegen-PR bestandsoverlap (ALLEEN LEZEN)",
        "Repository: " + report["repository"],
        "Open PR-zoekpagina: " + str(report["open_pr_page_number"]) +
        " · posities " + str(report["open_pr_window_start"]) + "–" +
        str(report["open_pr_window_end"]) +
        (" · eerdere pagina's NIET bekeken" if report["earlier_pages_not_inspected"] else ""),
        "Gelezen open PR's in venster: " + str(report["inspected_prs"]) +
        " / gezien " + str(report["observed_open_prs"]) +
        " · opeenvolgende pagina's: " + str(report["open_pr_page_count"]),
        "Gedeelde-bestandparen: " + str(report["observed_file_collision_pairs"]),
        "Bestand/map-ancestorparen: " +
        str(report["observed_path_scope_collision_pairs"]),
        "Optionele GH issueidentiteit: " +
        str(report["issue_identity_lookups_performed"]) + " opgevraagd, " +
        str(report["observed_confirmed_open_issue_groups"]) + " open issues (" +
        str(report["observed_confirmed_open_P0_issue_groups"]) + " P0, " +
        str(report["observed_confirmed_open_P1_issue_groups"]) + " P1), " +
        str(report["observed_issue_number_is_pr_groups"]) + " waren PR-nummers; " +
        ("onvolledig/ongezien" if report["issue_identity_lookup_incomplete"]
         else "waargenomen bronnen beschikbaar"),
        "Meerdere open PR-tekstverwijzingen naar hetzelfde issue: " +
        str(report["observed_duplicate_issue_groups"]) +
        " (alleen onderzochte bronnen, geen fix-eigendom)",
        "Bron volledig: " + ("ja" if report["pr_file_scope_sample_complete"]
                               else "NEE (steekproef of veranderde bron)"),
    ]
    for receipt in report["open_pr_page_receipts"]:
        result.append("BRONPAGINA " + str(receipt["page"]) + " · posities " +
                      str(receipt["first_position"]) + "–" +
                      str(receipt["last_position"]) + " · gezien " +
                      str(receipt["observed_prs"]))
    if report["cross_page_duplicate_prs_observed"]:
        result.append("WAARSCHUWING: PR dubbel op pagina's; bron mogelijk verschoven.")
    for cluster in report["linked_pr_collision_clusters"]:
        result.append(
            "BESTANDSCONNECTIE-GROEP " + str(cluster["component_number"]) + " · " +
            str(cluster["pr_count_observed"]) + " open PRs · " +
            str(cluster["shared_file_edge_count_observed"]) + " paren · " +
            str(cluster["distinct_shared_file_count_observed"]) + " gedeelde bestanden")
        for pr in cluster["participating_prs"]:
            result.append(
                "  PR #" + str(pr["number"]) + " · " +
                str(pr["other_prs_with_shared_files_observed"]) +
                " verbindingen | " + pr["url"] + " @ " + pr["head_sha"][:12])
        for file_row in cluster["shared_file_pr_matrix"]:
            result.append("  gedeeld bestand: " + file_row["path"])
            for pr in file_row["touching_prs"]:
                result.append("    PR #" + str(pr["number"]) + " | " +
                              pr["url"] + " @ " + pr["head_sha"][:12] +
                              " (bronbestandslijst " +
                              ("volledig" if pr["source_file_list_complete"] else "gedeeltelijk") +
                              ")")
        if cluster["shared_files_sample_truncated"]:
            result.append("  Meer gedeelde paden; weergave ingekort.")
        result.append("  Alleen waargenomen bestandspaden; geen eigenaar of mergevolgorde.")
    result.append("Verbonden bestand/map-scopegroepen: " +
                  str(report["observed_connected_path_scope_collision_clusters"]) +
                  " waargenomen · getoond " +
                  str(len(report["linked_pr_path_scope_collision_clusters"])) +
                  " · ancestorpaden zijn geen bewezen Git-mergeconflicten.")
    for cluster in report["linked_pr_path_scope_collision_clusters"]:
        result.append(
            "BESTAND/MAP-SCOPEGROEP " + str(cluster["component_number"]) + " · " +
            str(cluster["pr_count_observed"]) + " open PRs · " +
            str(cluster["scope_collision_edge_count_observed"]) + " PR-paren · " +
            str(cluster["path_conflict_count_observed"]) + " padconflicten")
        for pr in cluster["participating_prs"]:
            result.append(
                "  PR #" + str(pr["number"]) + " · " +
                str(pr["other_prs_with_scope_conflicts_observed"]) +
                " scopeverbindingen | " + pr["url"] + " @ " + pr["head_sha"][:12])
        for conflict in cluster["path_conflicts_sample"]:
            result.append(
                "  PR #" + str(conflict["left_pr_number"]) + " ↔ PR #" +
                str(conflict["right_pr_number"]) + ": " + conflict["left_path"] +
                " ↔ " + conflict["right_path"] + " [" + conflict["relation"] + "]")
        if cluster["path_conflicts_sample_truncated"]:
            result.append("  Extra padconflicten in deze verbonden groep niet afgebeeld.")
        result.append("  Geen eigenaar, mergeconflict of integratievolgorde bewezen.")
    if report["path_scope_collision_clusters_truncated"]:
        result.append("Verbonden bestand/map-scopegroepen afgekapt; overige groepen onbekend.")
    for row in report["shown_file_collision_pairs"]:
        a, b = row["left"], row["right"]
        result.append("PR #" + str(a["number"]) + " ↔ PR #" + str(b["number"]) +
                      ": " + str(row["shared_file_count_observed"]) +
                      " gemeenschappelijke bestanden | " +
                      a["url"] + " | " + b["url"])
        for file in row["shared_files"]:
            result.append("  " + file)
        if row["shared_files_truncated"]:
            result.append("  Meer bestandspaden niet weergegeven.")
    for row in report["shown_path_scope_collision_pairs"]:
        a, b = row["left"], row["right"]
        result.append("BESTAND/MAP-CONFLICT PR #" + str(a["number"]) +
                      " ↔ PR #" + str(b["number"]) + ": " +
                      str(row["path_conflict_count_observed"]) +
                      " padparen (geen mergeconflict- of eigenaarsbewijs)")
        for conflict in row["path_conflicts"]:
            result.append("  " + conflict["left_path"] + " ↔ " +
                          conflict["right_path"] + " [" +
                          conflict["relation"] + "]")
        if row["path_conflicts_truncated"]:
            result.append("  Extra padparen buiten begrensde voorvertoning.")
    if not report["shown_path_scope_collision_pairs"]:
        result.append("Geen bestand/map-ancestorparen waargenomen in deze steekproef.")
    if report["path_scope_collision_pairs_truncated"]:
        result.append("Bestand/map-conflictpaarlijst afgekapt; overige paren onbekend.")
    for group in report["shown_duplicate_issue_groups"]:
        result.append("GELIJK NUMMER IN PR-TEKST #" + str(group["issue_number"]) +
                      " · " + str(group["open_pr_mentions_observed"]) +
                      " PR-bronvermeldingen · GitHub identiteit: " +
                      group["identity_status"] +
                      " · prioriteit: " + group["observed_issue_priority"] +
                      " · actie: " + group["operator_next_step"] +
                      " · geen geverifieerde oplossing/eigenaar")
        for link in group["shown_pr_sources"]:
            result.append("  " + link["url"] + " @ " + link["head_sha"][:12] +
                          " · vermeld in: " + "/".join(link["mentioned_in"]))
    if report["duplicate_issue_groups_truncated"]:
        result.append("Meer mogelijke dubbele issue-PR vermeldingen afgekapt.")
    if report["unpaired_open_prs_in_sample_not_claim_verified"]:
        result.append("PR's zonder gedeelde paden IN DEZE STEEKPROEF (geen eigenaarvrijgave):")
        for pr in report["unpaired_open_prs_in_sample_not_claim_verified"]:
            result.append("  PR #" + str(pr["number"]) + " | " + pr["url"])
    if report["pr_file_lists_truncated"]:
        result.append("Onvolledige bestandlijsten: " +
                      ", ".join(str(n) for n in report["pr_file_lists_truncated"]))
    result.append("Dit zijn gedeelde bestandspaden, GEEN bewezen Git-mergeconflicten.")
    result.append("Open PR-steekproef is GEEN verificatie van Notion-claims, eigenaren, leases of PR-loze branches.")
    result.append("Er wordt niets gewijzigd of geclaimd; bestaande releasegates blijven intact.")
    return "\n".join(result)


def graph_html(report):
    rows = []
    for row in report["shown_file_collision_pairs"]:
        a, b = row["left"], row["right"]
        left, right = escape(a["url"], quote=True), escape(b["url"], quote=True)
        files = "<br>".join("<code>" + escape(path) + "</code>"
                             for path in row["shared_files"])
        if row["shared_files_truncated"]:
            files += "<br>...meer paden"
        rows.append(
            "<tr><td><a rel='noopener noreferrer' href='" + left +
            "'>PR #" + str(a["number"]) + "</a> ↔ "
            "<a rel='noopener noreferrer' href='" + right +
            "'>PR #" + str(b["number"]) + "</a></td><td>" +
            str(row["shared_file_count_observed"]) + "</td><td>" + files +
            "</td></tr>")
    if not rows:
        rows.append("<tr><td colspan='3'>Geen gedeelde bestandspaden in de gelezen PR-steekproef.</td></tr>")
    scope_rows = []
    for row in report["shown_path_scope_collision_pairs"]:
        a, b = row["left"], row["right"]
        examples = "<br>".join(
            "<code>" + escape(item["left_path"]) + "</code> ↔ <code>" +
            escape(item["right_path"]) + "</code><br><small>" +
            escape(item["relation"]) + "</small>"
            for item in row["path_conflicts"])
        if row["path_conflicts_truncated"]:
            examples += "<br>...meer padparen"
        scope_rows.append(
            "<tr><td><a rel='noopener noreferrer' href='" +
            escape(a["url"], quote=True) + "'>PR #" + str(a["number"]) +
            "</a> ↔ <a rel='noopener noreferrer' href='" +
            escape(b["url"], quote=True) + "'>PR #" + str(b["number"]) +
            "</a></td><td>" + str(row["path_conflict_count_observed"]) +
            "</td><td>" + examples + "</td></tr>")
    if not scope_rows:
        scope_rows.append("<tr><td colspan='3'>Geen bestand/map-ancestorparen waargenomen in de gelezen PR-steekproef.</td></tr>")
    completeness = ("volledig, uitsluitend voor huidige open PR's"
                    if report["pr_file_scope_sample_complete"]
                    else "onvolledig; bron/venster niet stabiel of afgekapt")
    unpaired = report["unpaired_open_prs_in_sample_not_claim_verified"]
    page_rows = "".join(
        "<li>Pagina " + str(r["page"]) + " · posities " +
        str(r["first_position"]) + "–" + str(r["last_position"]) +
        " · " + str(r["observed_prs"]) + " PRs</li>"
        for r in report["open_pr_page_receipts"])
    source_warning = (
        "<p role='alert'>ONSTABIELE BRON: dezelfde PR staat op meerdere "
        "pagina's of de resultaten verschoven tussen dubbele lezingen. "
        "Niet op basis hiervan mergen of claimen.</p>"
        if report["cross_page_duplicate_prs_observed"] or
           not report["open_pr_snapshot_stable"] else "")
    duplicate_cards = []
    for group in report["shown_duplicate_issue_groups"]:
        mentioned = "".join(
            "<li><a rel='noopener noreferrer' href='" +
            escape(item["url"], quote=True) + "'>PR @" +
            escape(item["head_sha"][:12]) + "</a> · in " +
            escape("/".join(item["mentioned_in"])) + "</li>"
            for item in group["shown_pr_sources"])
        duplicate_cards.append(
            "<li><a rel='noopener noreferrer' href='" +
            escape(group["issue_url"], quote=True) + "'>Issue #" +
            str(group["issue_number"]) + "</a> · " +
            str(group["open_pr_mentions_observed"]) +
            " PR-tekstvermeldingen · GitHub identiteit: " +
            escape(group["identity_status"]) +
            " · prioriteit: " + escape(group["observed_issue_priority"]) +
            " · actie: " + escape(group["operator_next_step"]) +
            " (geen fix-/eigenaarbewijs)<ul>" +
            mentioned + "</ul></li>")
    isolated_links = "".join(
        "<li><a rel='noopener noreferrer' href='" +
        escape(pr["url"], quote=True) + "'>PR #" + str(pr["number"]) +
        "</a> · " + escape(pr["head_sha"][:12]) + "</li>"
        for pr in unpaired)
    cluster_cards = []
    for cluster in report["linked_pr_collision_clusters"]:
        members = "".join(
            "<li><a rel='noopener noreferrer' href='" +
            escape(pr["url"], quote=True) + "'>PR #" + str(pr["number"]) +
            "</a> · HEAD <code>" + escape(pr["head_sha"][:12]) +
            "</code> · " + str(pr["other_prs_with_shared_files_observed"]) +
            " directe bestandsverbindingen</li>"
            for pr in cluster["participating_prs"])
        samples = "".join(
            "<li><code>" + escape(file_row["path"]) + "</code><ul>" +
            "".join(
                "<li><a rel='noopener noreferrer' href='" +
                escape(pr["url"], quote=True) + "'>PR #" + str(pr["number"]) +
                "</a> · HEAD <code>" + escape(pr["head_sha"][:12]) +
                "</code> · " +
                ("volledige" if pr["source_file_list_complete"] else "gedeeltelijke") +
                " bestandslijst</li>"
                for pr in file_row["touching_prs"]) + "</ul></li>"
            for file_row in cluster["shared_file_pr_matrix"])
        cluster_cards.append(
            "<li><h3>Connectiegroep " + str(cluster["component_number"]) +
            " · " + str(cluster["pr_count_observed"]) + " open PRs</h3>" +
            "<p>" + str(cluster["shared_file_edge_count_observed"]) +
            " waargenomen PR-paren · " +
            str(cluster["distinct_shared_file_count_observed"]) +
            " unieke gedeelde bestandspaden. Veel verbindingen betekenen geen " +
            "eigenaarschap of veilige mergevolgorde.</p><ul>" +
            members + "</ul><details><summary>Gedeelde bestanden (begrensd)</summary><ul>" +
            samples + "</ul>" +
            ("<p>Meer gedeelde bestandspaden zijn niet afgebeeld.</p>"
             if cluster["shared_files_sample_truncated"] else "") +
            "</details></li>")
    scope_cluster_cards = []
    for cluster in report["linked_pr_path_scope_collision_clusters"]:
        members = "".join(
            "<li><a rel='noopener noreferrer' href='" +
            escape(pr["url"], quote=True) + "'>PR #" + str(pr["number"]) +
            "</a> · HEAD <code>" + escape(pr["head_sha"][:12]) +
            "</code> · " + str(pr["other_prs_with_scope_conflicts_observed"]) +
            " directe scopeverbindingen · " +
            ("volledige" if pr["file_list_complete"] else "gedeeltelijke") +
            " bestandsscope</li>"
            for pr in cluster["participating_prs"])
        examples = "".join(
            "<li><a rel='noopener noreferrer' href='" +
            escape(conflict["left_pr_url"], quote=True) + "'>PR #" +
            str(conflict["left_pr_number"]) + "</a> <code>" +
            escape(conflict["left_path"]) + "</code> ↔ <a rel='noopener noreferrer' href='" +
            escape(conflict["right_pr_url"], quote=True) + "'>PR #" +
            str(conflict["right_pr_number"]) + "</a> <code>" +
            escape(conflict["right_path"]) + "</code><br><small>" +
            escape(conflict["relation"]) + "</small></li>"
            for conflict in cluster["path_conflicts_sample"])
        scope_cluster_cards.append(
            "<li><h3>Scopegroep " + str(cluster["component_number"]) +
            " · " + str(cluster["pr_count_observed"]) + " open PRs</h3><p>" +
            str(cluster["scope_collision_edge_count_observed"]) +
            " PR-paren · " + str(cluster["path_conflict_count_observed"]) +
            " waargenomen padconflicten. Geen bewijs van Git-mergeconflict, "
            "eigenaar of integratievolgorde.</p><ul>" + members +
            "</ul><details><summary>Begrensde padvoorbeelden</summary><ul>" +
            examples + "</ul>" +
            ("<p>Meer padconflicten in deze groep niet afgebeeld.</p>"
             if cluster["path_conflicts_sample_truncated"] else "") +
            "</details></li>")
    scope_cluster_section = (
        "<h2>Verbonden bestand/map-scopegroepen (" +
        str(report["observed_connected_path_scope_collision_clusters"]) +
        " waargenomen)</h2><p>Transitive groepen van bestand/map-ancestorparen. "
        "Alleen de waargenomen open-PR-steekproef; geen mergevolgorde of eigenaarschap.</p><ol>" +
        ("".join(scope_cluster_cards) or "<li>Geen verbonden scopegroepen waargenomen.</li>") +
        "</ol>" +
        ("<p role='alert'>Weergave afgekapt; overige scopegroepen onbekend.</p>"
         if report["path_scope_collision_clusters_truncated"] else ""))
    return ("<!doctype html><html lang='nl'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>zSentinel PR-conflictenkaart</title>"
            "<style>body{background:#111820;color:#f2f5f8;font:16px system-ui;"
            "max-width:1050px;margin:auto;padding:24px}"
            "table{width:100%;border-collapse:collapse}td,th{padding:12px;"
            "border-bottom:1px solid #566078;text-align:left;vertical-align:top;"
            "overflow-wrap:anywhere}a{color:#b5d7ff}"
            "aside{padding:12px;border-left:4px solid #bac1c9;background:#243140}"
            "code{overflow-wrap:anywhere}</style>"
            "<h1>zSentinel · PR-bestandsoverlap</h1><p>" +
            escape(report["repository"]) + " | " +
            str(report["inspected_prs"]) + " van " +
            str(report["observed_open_prs"]) +
            " open PR's gelezen | " +
            str(report["observed_file_collision_pairs"]) +
            " waargenomen paren</p><p>Open PR-zoekpagina " +
            str(report["open_pr_page_number"]) + " · posities " +
            str(report["open_pr_window_start"]) + "–" +
            str(report["open_pr_window_end"]) +
            " · geen oordeel over andere pagina's.</p><ul>" + page_rows +
            "</ul>" + source_warning + "<aside>Bron: " + escape(completeness) +
            ". Gedeelde bestandspaden betekenen niet automatisch Git-mergeconflict."
            " Geen Notion-claim, eigenaar, bestandslease of PR-loze branch"
            " geverifieerd. Geen automatische claim of merge.</aside>"
            "<h2>Verbonden PR-bestandsgroepen</h2>"
            "<p>Exact waargenomen gelijke bestandspaden verbinden PRs binnen het "
            "gelezen venster. Geen claim-, mergevolgorde- of Git-conflictbewijs.</p><ol>" +
            ("".join(cluster_cards) or
             "<li>Geen verbonden groepen in de bekeken bron.</li>") +
            "</ol><table><thead><tr><th>Open PR's</th><th>Overlap</th>"
            "<th>Gedeelde bestanden</th></tr></thead><tbody>" +
            "".join(rows) + "</tbody></table>"
            "<h2>Bestand/map-ancestorconflicten (" +
            str(report["observed_path_scope_collision_pairs"]) + ")</h2>"
            "<p>Een PR gebruikt een pad als bestand terwijl een andere PR daaronder bestanden toevoegt. Dit is een scopeconflict-signaal, geen bewezen Git-mergeconflict of eigenaarschap.</p>" +
            scope_cluster_section +
            "<table><thead><tr><th>Open PR's</th><th>Padparen</th><th>Waargenomen paden en relatie</th></tr></thead><tbody>" +
            "".join(scope_rows) + "</tbody></table>" +
            ("<p role='alert'>ONVOLLEDIG: aanvullende bestand/map-conflicpparen ontbreken buiten de weergavelimiet.</p>"
             if report["path_scope_collision_pairs_truncated"] else "") +
            "<h2>Meerdere open PR's noemen hetzelfde issue</h2>"
            "<p>Positieve PR-tekstvermeldingen, geen bewezen fix of mergevrijgave.</p><ul>" +
            ("".join(duplicate_cards) or
             "<li>Geen dubbele verwijzingen in het onderzochte bronvenster.</li>") +
            "</ul><h2>Geen gedeelde paden in de steekproef</h2>"
            "<p>Dit zijn niet automatisch vrije taken. Raadpleeg actuele claims, "
            "PR-loze branches, bestandsleases en reviews.</p><ul>" +
            (isolated_links or "<li>Geen overige open PR's in de steekproef.</li>") +
            "</ul><p>Bronmoment: " +
            escape(report["observed_at_utc"]) + "</p></html>")





def as_markdown(report):
    """Private operator handoff. Every link is canonical GitHub evidence."""
    lines = [
        "# zSentinel · GitHub PR-bestandsscope",
        "",
        "Repository: " + report["repository"],
        "Bronmoment: " + report["observed_at_utc"],
        "Open PR-zoekpagina: " + str(report["open_pr_page_number"]) +
        " · posities " + str(report["open_pr_window_start"]) + "–" +
        str(report["open_pr_window_end"]) + " (andere pagina's onbekend)",
        "",
        "**Bronuitkomst:** " +
        ("Volledige, stabiele huidige open-PR-steekproef."
         if report["pr_file_scope_sample_complete"]
         else "ONVOLLEDIG of veranderd: geen uitspraken over ongeziene PR's."),
        "",
    ]
    selected = report["selected_source_pr"]
    if selected:
        lines.extend([
            "Geselecteerde bron-PR: [#" + str(selected["number"]) +
            "](" + selected["url"] + "), head `" +
            selected["head_sha"] + "`, base `" + selected["base_sha"] +
            "`. Bestandspaden: " + str(selected["source_file_count"]) +
            " uit " + str(selected["source_file_pages_observed"]) +
            " pagina('s). Uitgebreide bron: " +
            str(selected["expanded_source_file_pages_requested"]) + ".",
            "",
        ])
    lines.extend([
        "## Expliciete bestandscope",
        "",
    ])
    lines.extend(["", "## Waargenomen bronpagina's", ""])
    for row in report["open_pr_page_receipts"]:
        lines.append("- Pagina " + str(row["page"]) + " · posities " +
                     str(row["first_position"]) + "–" +
                     str(row["last_position"]) + " · " +
                     str(row["observed_prs"]) + " PRs.")
    if report["cross_page_duplicate_prs_observed"]:
        lines.append("**ONSTABIEL:** dezelfde PR op meerdere bronpagina's.")
    for name in report["requested_paths"]:
        # Canonical path strings are bounded and reject control characters.
        # Use a plain indented line instead of Markdown code fences.
        lines.append("- `" + name.replace("`", "\\`") + "`")
    lines.extend(["", "## Waargenomen gedeelde paden", ""])
    if not report["observed_overlaps"]:
        lines.append("Geen overeenkomende paden gezien; dit is GEEN vrijgave.")
    for hit in report["observed_overlaps"]:
        relations = "; ".join(
            item["changed_path"] + " [" + item["relation"] + "]"
            for item in hit["matching_path_relations"])
        lines.append("- [PR #" + str(hit["pr_number"]) + "](" + hit["pr_url"] +
                     ") raakt `" + hit["path"].replace("`", "\\`") +
                     "`; gevonden bestanden: " + str(len(hit["matching_files"])) +
                     "; relatie: " + relations +
                     (" (gedeeltelijke weergave)" if hit["matches_truncated"] else ""))
    lines.extend([
        "",
        "**Geen toestemming:** Notion-/SQLite-claims, PR-loze branches, "
        "bestandsleases, review en CI zijn niet geverifieerd.",
        "Geen codewijziging, claim, merge, betaling, runnerherstart of deploy.",
    ])
    return "\n".join(lines) + "\n"


def graph_markdown(report):
    lines = [
        "# zSentinel · Open PR-bestandsoverlap",
        "",
        "Repository: " + report["repository"],
        "Bronmoment: " + report["observed_at_utc"],
        "Open PR-zoekpagina: " + str(report["open_pr_page_number"]) +
        " · posities " + str(report["open_pr_window_start"]) + "–" +
        str(report["open_pr_window_end"]) + " (andere pagina's onbekend)",
        "Geïnspecteerde PR's: " + str(report["inspected_prs"]) +
        " / gezien: " + str(report["observed_open_prs"]),
        "Compleet: " + ("ja (alleen open PR's)" if report["pr_file_scope_sample_complete"]
                         else "NEE / onzeker"),
        "",
        "## Gedeelde bestandsnamen",
    ]
    lines.extend(["", "## Waargenomen GitHub-pagina's", ""])
    for receipt in report["open_pr_page_receipts"]:
        lines.append("- Pagina " + str(receipt["page"]) + " · posities " +
                     str(receipt["first_position"]) + "–" +
                     str(receipt["last_position"]) + " · " +
                     str(receipt["observed_prs"]) + " PRs.")
    if report["cross_page_duplicate_prs_observed"]:
        lines.append("**ONSTABIEL:** dezelfde PR op meerdere pagina's.")
    lines.extend(["", "## Verbonden PR-bestandsgroepen", "",
                  "Alleen gedeelde bestandspaden in het bekeken venster; "
                  "geen eigenaarschap, Git-conflictbewijs of mergeautorisatie.", ""])
    for cluster in report["linked_pr_collision_clusters"]:
        lines.append("### Groep " + str(cluster["component_number"]) +
                     " · " + str(cluster["pr_count_observed"]) + " open PRs · " +
                     str(cluster["shared_file_edge_count_observed"]) + " PR-paren")
        lines.append(str(cluster["distinct_shared_file_count_observed"]) +
                     " verschillende gedeelde paden.")
        for pr in cluster["participating_prs"]:
            lines.append("- [PR #" + str(pr["number"]) + "](" + pr["url"] +
                         ") · HEAD " + pr["head_sha"][:12] + " · " +
                         str(pr["other_prs_with_shared_files_observed"]) +
                         " directe bestandsverbindingen")
        for file_row in cluster["shared_file_pr_matrix"]:
            lines.append("  - Gedeeld bestand: " + repr(file_row["path"]))
            for pr in file_row["touching_prs"]:
                lines.append("    - [PR #" + str(pr["number"]) + "](" + pr["url"] +
                             ") · HEAD " + pr["head_sha"][:12] +
                             (" · gedeeltelijke bronbestandslijst"
                              if not pr["source_file_list_complete"] else ""))
        if cluster["shared_files_sample_truncated"]:
            lines.append("  - Extra gedeelde bestanden niet afgebeeld.")
    if not report["linked_pr_collision_clusters"]:
        lines.append("Geen verbonden groepen waargenomen.")
    lines.extend(["", "## Bestandsbotsingen (ook over paginagrenzen)", ""])
    for row in report["shown_file_collision_pairs"]:
        left, right = row["left"], row["right"]
        lines.append("- [#" + str(left["number"]) + "](" + left["url"] +
                     ") ↔ [#" + str(right["number"]) + "](" + right["url"] +
                     "): " + str(row["shared_file_count_observed"]) +
                     " waargenomen gedeelde bestanden")
    if not report["shown_file_collision_pairs"]:
        lines.append("Geen gemeenschappelijke bestandsnamen waargenomen.")
    lines.extend(["", "## Bestand/map-ancestorconflicten", "",
                  "Een PR gebruikt een pad als bestand terwijl een andere PR "
                  "daaronder bestanden toevoegt. Dit is een scopeconflict-signaal, "
                  "geen bewezen Git-mergeconflict of eigenaarschap.", ""])
    for row in report["shown_path_scope_collision_pairs"]:
        left, right = row["left"], row["right"]
        lines.append("- [#" + str(left["number"]) + "](" + left["url"] +
                     ") ↔ [#" + str(right["number"]) + "](" + right["url"] +
                     "): " + str(row["path_conflict_count_observed"]) + " padparen")
        for conflict in row["path_conflicts"]:
            lines.append("  - `" + conflict["left_path"].replace("`", "\\`") +
                         "` ↔ `" + conflict["right_path"].replace("`", "\\`") +
                         "` [" + conflict["relation"] + "]")
        if row["path_conflicts_truncated"]:
            lines.append("  - Extra padparen buiten begrensde voorvertoning.")
    if not report["shown_path_scope_collision_pairs"]:
        lines.append("Geen bestand/map-ancestorparen waargenomen in de gelezen bron.")
    if report["path_scope_collision_pairs_truncated"]:
        lines.append("**ONVOLLEDIG:** overige bestand/map-conflicpparen buiten de weergavelimiet.")
    lines.extend([
        "", "## Verbonden bestand/map-scopegroepen", "",
        "Transitive groepen van waargenomen bestand/map-ancestorconflicten; geen merge- of eigenaarsbewijs.",
        "Waargenomen groepen: " +
        str(report["observed_connected_path_scope_collision_clusters"]) +
        " · getoond: " + str(len(report["linked_pr_path_scope_collision_clusters"])), "",
    ])
    for cluster in report["linked_pr_path_scope_collision_clusters"]:
        lines.append("### Scopegroep " + str(cluster["component_number"]) +
                     " · " + str(cluster["pr_count_observed"]) + " open PRs")
        lines.append(str(cluster["scope_collision_edge_count_observed"]) +
                     " PR-paren · " + str(cluster["path_conflict_count_observed"]) +
                     " waargenomen padconflicten.")
        for pr in cluster["participating_prs"]:
            lines.append("- [PR #" + str(pr["number"]) + "](" + pr["url"] +
                         ") · HEAD " + pr["head_sha"][:12] + " · " +
                         str(pr["other_prs_with_scope_conflicts_observed"]) +
                         " directe scopeverbindingen")
        for conflict in cluster["path_conflicts_sample"]:
            lines.append("- Padrelatie: [PR #" + str(conflict["left_pr_number"]) +
                         "](" + conflict["left_pr_url"] + ") ↔ [PR #" +
                         str(conflict["right_pr_number"]) + "](" +
                         conflict["right_pr_url"] + ") · " + conflict["relation"] +
                         " (padvoorbeelden staan in HTML/tekstweergave)")
        if cluster["path_conflicts_sample_truncated"]:
            lines.append("  - Extra padconflicten in deze groep niet afgebeeld.")
    if report["path_scope_collision_clusters_truncated"]:
        lines.append("**ONVOLLEDIG:** overige verbonden scopegroepen buiten de weergavelimiet.")
    lines.extend(["", "## Mogelijk dubbel werk rond hetzelfde issue (titel of body)", ""])
    for group in report["shown_duplicate_issue_groups"]:
        lines.append("- [Nummer #" + str(group["issue_number"]) + "](" +
                     group["issue_url"] + ") · " +
                     str(group["open_pr_mentions_observed"]) +
                     " open PR-tekstverwijzingen · GitHub identiteit: " +
                     group["identity_status"] +
                     " · prioriteit: " + group["observed_issue_priority"] +
                     " · actie: " + group["operator_next_step"] +
                     " · geen geverifieerde fix/eigenaar")
        for row in group["shown_pr_sources"]:
            lines.append("  - [PR](" + row["url"] + ") · " +
                         "/".join(row["mentioned_in"]) +
                         " · HEAD " + row["head_sha"][:12])
    if not report["shown_duplicate_issue_groups"]:
        lines.append("Geen dubbele issuevermelding in de gelezen bronsteekproef.")
    if report["duplicate_issue_groups_truncated"]:
        lines.append("Overige groepen buiten begrensde weergave ONBEKEND.")
    if report["unpaired_open_prs_in_sample_not_claim_verified"]:
        lines.extend(["", "## PR's zonder gedeelde paden (geen claimvrijgave)"])
        for pr in report["unpaired_open_prs_in_sample_not_claim_verified"]:
            lines.append("- [#" + str(pr["number"]) + "](" + pr["url"] + ")")
    lines.extend([
        "",
        "**Dit bewijst geen Git-mergeconflict of vrije werkclaim.**",
        "Huidige Notion-claims, bestandsleases, PR-loze branches, verplichte "
        "CI/review en release-autorisatie blijven onafhankelijk.",
    ])
    return "\n".join(lines) + "\n"



MAX_GRAPH_CSV_RECORDS = 2500
MAX_GRAPH_CSV_BYTES = 2 * 1024 * 1024


def graph_private_csv(report):
    """Source-linked private single-project PR/file report, not permission."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, dialect="excel", lineterminator="\r\n")
    header = (
        "record_type", "repository", "source_page", "source_complete",
        "cluster_number", "shared_filename", "pr_number", "pr_url",
        "pr_head_sha", "related_pr_url", "related_pr_head_sha",
        "shared_files_observed", "scope_conflict_pairs_observed", "scope_conflict_relation",
        "scope_conflict_left_path", "scope_conflict_right_path",
        "unverified_issue_number", "issue_identity_status", "observed_issue_priority",
        "owner_verified", "merge_authorized", "operator_notice"
    )
    writer.writerow(header)
    count = 0
    def safe(value):
        raw = "".join("\\u%04x" % ord(c) if ord(c) < 32 or ord(c) == 127
                      else c for c in str(value))
        return csv_formula_inert(raw)

    def row(kind, **values):
        nonlocal count
        if not set(values).issubset(header):
            raise ValueError("unknown CSV observation field")
        count += 1
        if count > MAX_GRAPH_CSV_RECORDS:
            raise ValueError("private PR CSV observation too large")
        base = {
            "record_type": kind, "repository": report["repository"],
            "source_page": report["open_pr_page_number"],
            "source_complete": ("true" if report["pr_file_scope_sample_complete"]
                                else "false"),
            "owner_verified": "false", "merge_authorized": "false",
            **values,
        }
        writer.writerow([safe(base.get(col, "")) for col in header])
        if output.tell() > MAX_GRAPH_CSV_BYTES:
            raise ValueError("private PR CSV byte count too large")

    row("source_summary", operator_notice=(
        "Partial or non-atomic GitHub PR sample; claims, owner and merge gates UNKNOWN."))
    for cluster in report["linked_pr_collision_clusters"]:
        for file_row in cluster["shared_file_pr_matrix"]:
            for pr in file_row["touching_prs"]:
                row("shared_file_exact_pr",
                    cluster_number=cluster["component_number"],
                    shared_filename=file_row["path"],
                    pr_number=pr["number"], pr_url=pr["url"],
                    pr_head_sha=pr["head_sha"],
                    operator_notice=("Exact PR path observation; owner NOT verified. " +
                        ("PR source files partial." if not pr["source_file_list_complete"]
                         else "PR file sample complete.")))
        if cluster["shared_files_sample_truncated"]:
            row("source_warning", cluster_number=cluster["component_number"],
                operator_notice="Additional paths beyond bounded cluster sample UNKNOWN.")
    for pair in report["shown_file_collision_pairs"]:
        row("shared_file_pr_pair", pr_number=pair["left"]["number"],
            pr_url=pair["left"]["url"],
            pr_head_sha=pair["left"]["head_sha"],
            related_pr_url=pair["right"]["url"],
            related_pr_head_sha=pair["right"]["head_sha"],
            shared_files_observed=pair["shared_file_count_observed"],
            operator_notice="Same filename is NOT a proven merge conflict.")
    for pair in report["shown_path_scope_collision_pairs"]:
        for conflict in pair["path_conflicts"]:
            row("file_directory_scope_conflict",
                pr_number=pair["left"]["number"],
                pr_url=pair["left"]["url"],
                pr_head_sha=pair["left"]["head_sha"],
                related_pr_url=pair["right"]["url"],
                related_pr_head_sha=pair["right"]["head_sha"],
                scope_conflict_pairs_observed=pair["path_conflict_count_observed"],
                scope_conflict_relation=conflict["relation"],
                scope_conflict_left_path=conflict["left_path"],
                scope_conflict_right_path=conflict["right_path"],
                operator_notice="File/directory ancestor scope conflict; inspect both PRs. Not merge authorization.")
        if pair["path_conflicts_truncated"]:
            row("source_warning", operator_notice="More file/directory path conflict examples omitted for PR pair.")
    if report["path_scope_collision_pairs_truncated"]:
        row("source_warning", operator_notice="Additional file/directory PR pairs beyond bounded output cap.")
    for group in report["shown_duplicate_issue_groups"]:
        for pr in group["shown_pr_sources"]:
            row("unverified_issue_number", pr_url=pr["url"],
                pr_head_sha=pr["head_sha"],
                unverified_issue_number=group["issue_number"],
                issue_identity_status=group["identity_status"],
                observed_issue_priority=group["observed_issue_priority"],
                operator_notice=("PR-title/body mention; even a confirmed open "
                                 "issue is NOT proof of ownership or fix."))
    for pr in report["unpaired_open_prs_in_sample_not_claim_verified"]:
        row("unpaired_pr_in_sample",
            pr_number=pr["number"], pr_url=pr["url"],
            pr_head_sha=pr["head_sha"],
            operator_notice=("No identical file within the observed sample; "
                             "NOT claim-free, merge-ready or owner-verified."))
    if report["collision_cards_truncated"] or report["duplicate_issue_groups_truncated"]:
        row("source_warning", operator_notice=(
            "Some file collision pairs or issue hints are beyond the output cap."))
    if not report["pr_file_scope_sample_complete"]:
        row("source_warning", operator_notice=(
            "Other PRs, source files or GitHub pages not established; "
            "absence of conflicts cannot be inferred."))
    return "\ufeff" + output.getvalue()


def save_graph_private_csv(report, path):
    """Create a single new private CSV. Never echo its content to stdout."""
    if not isinstance(path, Path):
        raise ValueError("private CSV needs an explicit new output pathname")
    data = graph_private_csv(report)
    if len(data.encode("utf-8")) > MAX_GRAPH_CSV_BYTES:
        raise ValueError("private CSV exceeds actual UTF-8 byte bound")
    write_private_report(path, data, max_bytes=MAX_GRAPH_CSV_BYTES)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--project", required=True)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--source-pr", type=int, help="Gebruik gewijzigde paden van één bestaande open PR als doel.")
    parser.add_argument("--expanded-source-files", action="store_true",
                        help="Optioneel: alleen voor expliciet gekozen product-PR "
                             "tot drie bestandspagina's/240 paden ophalen")
    parser.add_argument("--max-prs", type=int, default=24)
    parser.add_argument("--open-page", type=int, default=1,
                        help="Selecteer 1..50 van maximaal 48 open PRs per sorteerpagina; "
                             "oudere pagina's zijn altijd gedeeltelijk, nooit vrijgave")
    parser.add_argument("--page-count", type=int, default=1,
                        help="1..3 opeenvolgende pagina's voor --audit-open-prs, "
                             "--source-pr of --path; hoogstens 48 PRs totaal")
    parser.add_argument("--resolve-issue-identity", action="store_true",
                        help="Optioneel: GET-only maximaal acht bronissues "
                             "voor PR-tekstverwijzingsgroepen, nooit claim/merge")
    parser.add_argument("--audit-open-prs", action="store_true",
                        help="Toon een bestandsoverlapkaart voor open PR's zonder handmatige doelpaden.")
    parser.add_argument("--format", choices=("text", "json", "html", "markdown", "csv"), default="text")
    parser.add_argument("--output", type=Path,
                        help="Only for --audit-open-prs --format csv: NEW private 0600 file")
    args = parser.parse_args(argv)
    try:
        repos = registry(args.registry)
        if (args.format == "csv" and
                (not args.audit_open_prs or args.output is None)):
            raise ValueError("private CSV requires --audit-open-prs and --output")
        if args.output is not None and args.format != "csv":
            raise ValueError("--output is reserved for private CSV")
        if args.project not in repos:
            raise ValueError("unknown canonical project")
        if args.audit_open_prs:
            if args.path or args.source_pr is not None or args.expanded_source_files:
                raise ValueError("--audit-open-prs cannot be combined with --path/--source-pr")
            report = observe_conflict_graph(repos[args.project],
                                            os.environ.get("GITHUB_TOKEN"), args.max_prs,
                                            open_page=args.open_page, page_count=args.page_count,
                                            resolve_issue_identity=args.resolve_issue_identity)
            if args.format == "csv":
                save_graph_private_csv(report, args.output)
                print("Created NEW private file-scope CSV; no claim or merge authority.",
                      file=sys.stderr)
            elif args.format == "json":
                print(json.dumps(report, ensure_ascii=False, indent=2))
            elif args.format == "html":
                print(graph_html(report))
            elif args.format == "markdown":
                print(graph_markdown(report))
            else:
                print(graph_operator_text(report))
            return (2 if report["observed_file_collision_pairs"] else
                    3 if not report["pr_file_scope_sample_complete"] else 0)
        if args.resolve_issue_identity:
            raise ValueError("--resolve-issue-identity requires --audit-open-prs")
        report = observe(
            repos[args.project], args.path,
            os.environ.get("GITHUB_TOKEN"), args.max_prs, args.source_pr,
            expanded_source_files=args.expanded_source_files,
            open_page=args.open_page, page_count=args.page_count)
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.format == "html":
            print(html_view(report))
        elif args.format == "markdown":
            print(as_markdown(report))
        else:
            print(operator_text(report))
        # Exit status 2 is a real observed collision; status 3 is insufficient
        # evidence even if some overlaps are visible. Never exit 0 for permission.
        return 2 if report["observed_overlaps"] else (
            3 if not report["pr_file_scope_sample_complete"] else 0)
    except (ValueError, OSError):
        print("zSentinel source unavailable or invalid; no claim or merge clearance",
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
