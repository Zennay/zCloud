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
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from zsentinel_product_issue_inbox import QA as QA_ONLY_TITLE

ROOT = Path(__file__).resolve().parent.parent
MAX_REGISTRY_BYTES = 512 * 1024
MAX_REPLY_BYTES = 1024 * 1024
MAX_OPEN_PAGE = 48
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


def open_prs(repo, token, page_size=MAX_OPEN_PAGE):
    # Fetch one beyond the requested inspection count when possible.
    # A full page is UNKNOWN, even if it may be the actual final page.
    if type(page_size) is not int or not 1 <= page_size <= MAX_OPEN_PAGE:
        raise ValueError("invalid open-PR page size")
    suffix = ("/pulls?state=open&per_page=" + str(page_size) +
              "&page=1&sort=updated&direction=desc")
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
        product_candidate = (type(title) is str and 0 < len(title) <= 256
                             and not QA_ONLY_TITLE.match(title))
        # A malformed/oversized PR body cannot authenticate an issue link.
        # The normal file collision inventory remains independent.
        refs_covered = body is None or (type(body) is str and len(body) <= 32000)
        refs = sorted({int(m.group(1)) for m in re.finditer(
            r"(?<![A-Za-z0-9/_])#([1-9][0-9]{0,7})\b",
            body or "")}) if refs_covered else []
        refs_covered = refs_covered and len(refs) <= MAX_REFERENCED_ISSUES
        refs = refs[:MAX_REFERENCED_ISSUES] if refs_covered else []
        if (type(n) is not int or not 0 < n < 100000000 or n in seen
                or not isinstance(base_repo, dict)
                or base_repo.get("full_name") != repo
                or not isinstance(head_sha, str) or not SHA.fullmatch(head_sha)
                or not isinstance(base_sha, str) or not SHA.fullmatch(base_sha)):
            raise ValueError("incomplete PR identity")
        seen.add(n)
        result.append({"number": n, "head_sha": head_sha,
                       "base_sha": base_sha,
                       "referenced_issue_numbers_unverified": refs,
                       "issue_reference_body_covered": refs_covered,
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
            "base_sha": base_sha, "paths": files,
            "source_file_pages_observed": observed_pages,
            "product_candidate_based_on_title": product_candidate,
            "read_only": True}


def overlaps(wanted, changed):
    # A path ending in '/' explicitly denotes a directory, otherwise one file.
    return changed.startswith(wanted) if wanted.endswith("/") else changed == wanted


def observe(repo, wanted, token=None, limit=24, source_pr_number=None,
            expanded_source_files=False):
    if (not REPO.fullmatch(repo) or not isinstance(wanted, list)
            or len(wanted) > MAX_PATHS or len(set(wanted)) != len(wanted)
            or type(limit) is not int or not 1 <= limit <= MAX_INSPECT
            or not wanted and source_pr_number is None
            or expanded_source_files and source_pr_number is None):
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
    page_size = min(MAX_OPEN_PAGE, limit + 1)
    before = open_prs(repo, token, page_size)
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
                             "observed_base_sha": pr["base_sha"], "matching_files": matches[:32],
                             "matches_truncated": len(matches) > 32})
    after = open_prs(repo, token, page_size)
    snapshot_stable = before == after
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
    complete = (source_stable and len(before) < page_size and len(before) <= limit
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
        "inspected_prs": sum(pr["number"] != source["number"]
                              for pr in selected) if source is not None else len(selected),
        "open_pr_page_may_be_truncated": len(before) == page_size,
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
    source = report["selected_source_pr"]
    source_link = ("<p>Bron-PR: <a rel='noreferrer noopener' href='" +
                   escape(source["url"], quote=True) + "'>#" +
                   str(source["number"]) + "</a> — exacte head " +
                   escape(source["head_sha"][:12]) + "</p>" if source else "")
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
            "</p>" + source_link +
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
MAX_REFERENCED_ISSUES = 32
MAX_DUPLICATE_ISSUE_GROUPS = 80
MAX_LINKS_PER_DUPLICATE_ISSUE = 8


def observe_conflict_graph(repo, token=None, limit=24):
    """Show PR-to-PR file collisions over a bounded, double-read open PR window.

    Collision means *shared pathname*, NOT merge conflict, active owner, worker
    claim, or proof that either PR must be rejected.
    """
    if not REPO.fullmatch(repo) or type(limit) is not int or not 1 <= limit <= MAX_INSPECT:
        raise ValueError("invalid graph request")
    page_size = min(MAX_OPEN_PAGE, limit + 1)
    before = open_prs(repo, token, page_size)
    chosen = before[:limit]
    file_owners = {}
    suspicious_lists = []
    observed = []
    issue_links = []
    for pr in chosen:
        files, truncated = changed_paths(repo, pr["number"], token)
        observed.append({
            "number": pr["number"], "url": pr["url"],
            "head_sha": pr["head_sha"], "base_sha": pr["base_sha"],
            "product_candidate_based_on_title": pr["product_issue_reference_candidate"],
            "file_count_observed": len(files),
            "file_paths_observed": files,
            "file_list_complete": not truncated,
        })
        if truncated:
            suspicious_lists.append(pr["number"])
        if pr["issue_reference_body_covered"] and pr["product_issue_reference_candidate"]:
            for issue_number in pr["referenced_issue_numbers_unverified"]:
                issue_links.append({
                    "issue_number": issue_number,
                    "pr_url": pr["url"], "pr_head_sha": pr["head_sha"],
                    "relation": "unverified_text_mention_only",
                    "owner_verified": False,
                    "fix_verified": False,
                })
        for path in files:
            file_owners.setdefault(path, set()).add(pr["number"])
    # A second product overlap exists even when PRs modify disjoint files:
    # several open source PR bodies mentioning the same issue. These are
    # unverified text mentions, not issue fixes, owners, or merge authority.
    by_issue = {}
    for link in issue_links:
        by_issue.setdefault(link["issue_number"], {})[link["pr_url"]] = {
            "url": link["pr_url"], "head_sha": link["pr_head_sha"]}
    duplicate_issue_groups = sorted(
        [(issue, sorted(prs.values(), key=lambda item: item["url"]))
         for issue, prs in by_issue.items() if len(prs) > 1],
        key=lambda row: (-len(row[1]), row[0]))
    duplicate_issue_cards = [{
        "issue_url": "https://github.com/" + repo + "/issues/" + str(issue),
        "issue_number": issue,
        "open_pr_mentions_observed": len(prs),
        "shown_pr_sources": prs[:MAX_LINKS_PER_DUPLICATE_ISSUE],
        "source_pr_list_truncated": len(prs) > MAX_LINKS_PER_DUPLICATE_ISSUE,
        "relation": "same_issue_number_unverified_open_pr_body_text_only",
        "issue_fix_verified": False,
        "common_owner_verified": False,
        "merge_authorized": False,
    } for issue, prs in duplicate_issue_groups[:MAX_DUPLICATE_ISSUE_GROUPS]]
    pairs = {}
    for path, numbers in file_owners.items():
        involved = sorted(numbers)
        for index, left in enumerate(involved):
            for right in involved[index + 1:]:
                pairs.setdefault((left, right), []).append(path)
    # Priority for operator inspection: most shared files first, then exact
    # PR identifiers. This is NOT a claim or merge recommendation.
    ordered = sorted(pairs.items(), key=lambda item: (-len(item[1]), item[0]))
    cards = []
    by_num = {pr["number"]: pr for pr in observed}
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
    after = open_prs(repo, token, page_size)
    stable = before == after
    collided = set()
    for left, right in pairs:
        collided.add(left)
        collided.add(right)
    fully_sampled = (len(before) < page_size and len(before) <= limit
                     and not suspicious_lists and stable
                     and len(ordered) <= MAX_CONFLICT_PAIRS)
    # An unpaired PR is a useful independent-work candidate ONLY after the
    # entire open-PR source sample is complete and stable. An unchecked PR
    # could otherwise touch the exact same path.
    not_paired = ([pr for pr in observed if pr["number"] not in collided]
                  if fully_sampled else [])
    return {
        "schema": "zsentinel-pr-file-collision-graph-v1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": repo,
        "source": "bounded_double_read_current_open_github_prs_and_files",
        "observed_open_prs": len(before),
        "inspected_prs": len(chosen),
        "inspected_pr_heads": observed,
        "open_pr_issue_references_unverified": issue_links,
        "observed_duplicate_issue_groups": len(duplicate_issue_groups),
        "shown_duplicate_issue_groups": duplicate_issue_cards,
        "duplicate_issue_groups_truncated": (
            len(duplicate_issue_groups) > MAX_DUPLICATE_ISSUE_GROUPS),
        "duplicate_issue_group_coverage_complete": (
            len(before) < page_size and len(before) <= limit
            and stable and not any(
                not pr["issue_reference_body_covered"] for pr in chosen)
            and len(duplicate_issue_groups) <= MAX_DUPLICATE_ISSUE_GROUPS),
        "issue_reference_body_coverage_complete":
            all(pr["issue_reference_body_covered"] for pr in chosen),
        "observed_file_collision_pairs": len(ordered),
        "shown_file_collision_pairs": cards,
        "unpaired_open_prs_in_sample_not_claim_verified": not_paired,
        "unpaired_means_no_shared_paths_in_sample_only": True,
        "unpaired_hidden_because_source_incomplete": not fully_sampled,
        "collision_cards_truncated": len(ordered) > MAX_CONFLICT_PAIRS,
        "pr_file_lists_truncated": suspicious_lists,
        "open_pr_page_may_be_truncated": len(before) == page_size,
        "open_pr_snapshot_stable": stable,
        "pr_file_scope_sample_complete": fully_sampled,
        "source_result": ("collisions_observed" if ordered else
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
        "Gelezen open PR's: " + str(report["inspected_prs"]) +
        " / gezien " + str(report["observed_open_prs"]),
        "Gedeelde-bestandparen: " + str(report["observed_file_collision_pairs"]),
        "Meerdere open PR-tekstverwijzingen naar hetzelfde issue: " +
        str(report["observed_duplicate_issue_groups"]) +
        " (alleen onderzochte bronnen, geen fix-eigendom)",
        "Bron volledig: " + ("ja" if report["pr_file_scope_sample_complete"]
                               else "NEE (steekproef of veranderde bron)"),
    ]
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
    for group in report["shown_duplicate_issue_groups"]:
        result.append("GELIJK ISSUE IN PR-TEKST #" + str(group["issue_number"]) +
                      " · " + str(group["open_pr_mentions_observed"]) +
                      " PR-bronvermeldingen, geen geverifieerde oplossing")
        for link in group["shown_pr_sources"]:
            result.append("  " + link["url"] + " @ " + link["head_sha"][:12])
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
    completeness = ("volledig, uitsluitend voor huidige open PR's"
                    if report["pr_file_scope_sample_complete"]
                    else "onvolledig; bron/venster niet stabiel of afgekapt")
    unpaired = report["unpaired_open_prs_in_sample_not_claim_verified"]
    duplicate_cards = []
    for group in report["shown_duplicate_issue_groups"]:
        mentioned = "".join(
            "<li><a rel='noopener noreferrer' href='" +
            escape(item["url"], quote=True) + "'>PR @" +
            escape(item["head_sha"][:12]) + "</a></li>"
            for item in group["shown_pr_sources"])
        duplicate_cards.append(
            "<li><a rel='noopener noreferrer' href='" +
            escape(group["issue_url"], quote=True) + "'>Issue #" +
            str(group["issue_number"]) + "</a> · " +
            str(group["open_pr_mentions_observed"]) +
            " PR-tekstvermeldingen (geen fix-/eigenaarbewijs)<ul>" +
            mentioned + "</ul></li>")
    isolated_links = "".join(
        "<li><a rel='noopener noreferrer' href='" +
        escape(pr["url"], quote=True) + "'>PR #" + str(pr["number"]) +
        "</a> · " + escape(pr["head_sha"][:12]) + "</li>"
        for pr in unpaired)
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
            " waargenomen paren</p><aside>Bron: " + escape(completeness) +
            ". Gedeelde bestandspaden betekenen niet automatisch Git-mergeconflict."
            " Geen Notion-claim, eigenaar, bestandslease of PR-loze branch"
            " geverifieerd. Geen automatische claim of merge.</aside>"
            "<table><thead><tr><th>Open PR's</th><th>Overlap</th>"
            "<th>Gedeelde bestanden</th></tr></thead><tbody>" +
            "".join(rows) + "</tbody></table>"
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
            selected["head_sha"] + "`, base `" + selected["base_sha"] + "`.",
            "",
        ])
    lines.extend([
        "## Expliciete bestandscope",
        "",
    ])
    for name in report["requested_paths"]:
        # Canonical path strings are bounded and reject control characters.
        # Use a plain indented line instead of Markdown code fences.
        lines.append("- `" + name.replace("`", "\\`") + "`")
    lines.extend(["", "## Waargenomen gedeelde paden", ""])
    if not report["observed_overlaps"]:
        lines.append("Geen overeenkomende paden gezien; dit is GEEN vrijgave.")
    for hit in report["observed_overlaps"]:
        lines.append("- [PR #" + str(hit["pr_number"]) + "](" + hit["pr_url"] +
                     ") raakt `" + hit["path"].replace("`", "\\`") +
                     "`; gevonden bestanden: " + str(len(hit["matching_files"])) +
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
        "Geïnspecteerde PR's: " + str(report["inspected_prs"]) +
        " / gezien: " + str(report["observed_open_prs"]),
        "Compleet: " + ("ja (alleen open PR's)" if report["pr_file_scope_sample_complete"]
                         else "NEE / onzeker"),
        "",
        "## Gedeelde bestandsnamen",
    ]
    for row in report["shown_file_collision_pairs"]:
        left, right = row["left"], row["right"]
        lines.append("- [#" + str(left["number"]) + "](" + left["url"] +
                     ") ↔ [#" + str(right["number"]) + "](" + right["url"] +
                     "): " + str(row["shared_file_count_observed"]) +
                     " waargenomen gedeelde bestanden")
    if not report["shown_file_collision_pairs"]:
        lines.append("Geen gemeenschappelijke bestandsnamen waargenomen.")
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
    parser.add_argument("--audit-open-prs", action="store_true",
                        help="Toon een bestandsoverlapkaart voor open PR's zonder handmatige doelpaden.")
    parser.add_argument("--format", choices=("text", "json", "html", "markdown"), default="text")
    args = parser.parse_args(argv)
    try:
        repos = registry(args.registry)
        if args.project not in repos:
            raise ValueError("unknown canonical project")
        if args.audit_open_prs:
            if args.path or args.source_pr is not None or args.expanded_source_files:
                raise ValueError("--audit-open-prs cannot be combined with --path/--source-pr")
            report = observe_conflict_graph(repos[args.project],
                                            os.environ.get("GITHUB_TOKEN"), args.max_prs)
            if args.format == "json":
                print(json.dumps(report, ensure_ascii=False, indent=2))
            elif args.format == "html":
                print(graph_html(report))
            elif args.format == "markdown":
                print(graph_markdown(report))
            else:
                print(graph_operator_text(report))
            return (2 if report["observed_file_collision_pairs"] else
                    3 if not report["pr_file_scope_sample_complete"] else 0)
        report = observe(
            repos[args.project], args.path,
            os.environ.get("GITHUB_TOKEN"), args.max_prs, args.source_pr,
            expanded_source_files=args.expanded_source_files)
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
    except (ValueError, OSError) as error:
        print("zSentinel scope unavailable: " + str(error), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
