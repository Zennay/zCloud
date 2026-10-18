#!/usr/bin/env python3
"""Read-only portfolio issue inbox for zSentinel product delivery.

GitHub issue metadata is not queue ownership or authorization. This program
never takes claims, merges, dispatches, restarts workers or writes runtime data.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
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
CANONICAL_OWNER = "Zennay"
MAX_REPOS = 18
MAX_CALLS = 54
MAX_ITEMS = 150
MAX_HTTP_BYTES = 512 * 1024
MAX_CONFIG_BYTES = 512 * 1024
PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
SEVERITY = re.compile(r"^\s*(?:\[(P[01])\]|(P[01])(?=\s|:|\-)|(?:[A-Za-z][A-Za-z -]{0,38}\s+)(P[01])\s*:)", re.I)
QA = re.compile(r"^\s*(?:P[0-3](?=\s|:|\-)(?:\s*[:\-]?\s*)?)?(?:QA|test|regression|quality[ -]validation)\b", re.I)

def object_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result

def canonical_projects(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError("invalid_registry")
    blob = path.read_bytes()
    if len(blob) > MAX_CONFIG_BYTES:
        raise ValueError("registry_oversized")
    rows = json.loads(blob, object_pairs_hook=object_unique)
    if not isinstance(rows, list):
        raise ValueError("invalid_registry_schema")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key, url = row.get("id"), row.get("repo_url")
        if not isinstance(key, str) or not PROJECT.fullmatch(key) or not isinstance(url, str):
            continue
        try:
            p = urllib.parse.urlsplit(url)
            if p.scheme != "https" or p.hostname != "github.com" or p.username or p.password or p.port not in (None, 443):
                continue
        except ValueError:
            continue
        names = p.path.strip("/").split("/")
        if p.query or p.fragment or len(names) != 2 or names[0] != CANONICAL_OWNER or not REPO.fullmatch(names[1]):
            continue
        if key in result:
            raise ValueError("duplicate_project")
        result[key] = names[0] + "/" + names[1]
    if not result or len(result) > MAX_REPOS:
        raise ValueError("invalid_canonical_repositories")
    return result

def observed_time(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid_time")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_time") from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("missing_timezone")
    return dt.astimezone(timezone.utc)

def priority(issue):
    raw = issue.get("labels")
    if not isinstance(raw, list):
        raise ValueError("invalid_issue_labels")
    labels = set()
    for label in raw:
        if isinstance(label, dict) and isinstance(label.get("name"), str):
            labels.add(label["name"].strip().lower())
    title = issue.get("title")
    if not isinstance(title, str):
        raise ValueError("invalid_issue_title")
    match = SEVERITY.match(title)
    prefix = next((v.upper() for v in match.groups() if v), "") if match else ""
    if "p0" in labels or "priority:p0" in labels or prefix == "P0":
        severity = "P0"
    elif "p1" in labels or "priority:p1" in labels or prefix == "P1":
        severity = "P1"
    elif "p2" in labels or "priority:p2" in labels:
        severity = "P2"
    else:
        severity = "Unspecified"
    qa = bool(QA.match(title) or labels.intersection({
        "qa", "quality-validation", "qa-validation", "regression", "tests-only"
    }))
    return severity, qa

class _NoGithubRedirects(urllib.request.HTTPRedirectHandler):
    """Never replay the GitHub token on an HTTP redirect to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_GITHUB_OPENER = urllib.request.build_opener(_NoGithubRedirects())


def bounded_get(url, token):
    # A redirect is a degraded source result, not permission to forward credentials.
    if urllib.parse.urlsplit(url).scheme != "https" or urllib.parse.urlsplit(url).hostname != "api.github.com":
        raise ValueError("invalid_github_api_origin")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "zsentinel-readonly-issue-inbox",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with _GITHUB_OPENER.open(request, timeout=8) as response:
            if response.status != 200:
                raise ValueError("github_unavailable")
            data = response.read(MAX_HTTP_BYTES + 1)
            if len(data) > MAX_HTTP_BYTES:
                raise ValueError("github_response_oversized")
            return json.loads(data, object_pairs_hook=object_unique)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ValueError("repository_inaccessible") from None
        if error.code in (401, 403, 429):
            raise ValueError("github_permission_or_rate_limit") from None
        raise ValueError("github_http_unavailable") from None
    except (TimeoutError, OSError, urllib.error.URLError):
        raise ValueError("github_network_unavailable") from None
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("github_payload_invalid") from None

def collect(projects, names, token, max_items=MAX_ITEMS):
    if not isinstance(names, list) or not names or len(names) > MAX_REPOS or len(set(names)) != len(names):
        raise ValueError("invalid_project_selection")
    if any(name not in projects for name in names):
        raise ValueError("unknown_project")
    if not isinstance(max_items, int) or isinstance(max_items, bool) or not 1 <= max_items <= MAX_ITEMS:
        raise ValueError("invalid_max_items")
    observed = datetime.now(timezone.utc)
    records = {}
    coverage = []
    calls = 0
    rate_or_auth_denied = False
    for project in names:
        repo = projects[project]
        statuses = []
        for direction in ("asc", "desc"):
            if rate_or_auth_denied:
                statuses.append("not_fetched_after_rate_or_auth_denial")
                continue
            if calls >= MAX_CALLS:
                statuses.append("request_budget_exhausted")
                continue
            calls += 1
            params = urllib.parse.urlencode({
                "state": "open", "sort": "created" if direction == "asc" else "updated",
                "direction": direction, "per_page": 12, "page": 1
            })
            url = "https://api.github.com/repos/" + urllib.parse.quote(repo, safe="/") + "/issues?" + params
            try:
                issues = bounded_get(url, token)
                if not isinstance(issues, list):
                    raise ValueError("github_issue_shape_invalid")
                for issue in issues:
                    if not isinstance(issue, dict) or "pull_request" in issue:
                        continue
                    num, title = issue.get("number"), issue.get("title")
                    if not isinstance(num, int) or isinstance(num, bool) or num <= 0 or num > 100000000:
                        continue
                    if not isinstance(title, str) or not 0 < len(title) <= 1000:
                        continue
                    try:
                        severity, is_qa = priority(issue)
                        created = observed_time(issue.get("created_at"))
                        updated = observed_time(issue.get("updated_at"))
                    except ValueError:
                        continue
                    if created > observed or updated > observed or updated < created:
                        continue
                    key = repo + "#" + str(num)
                    assigned = issue.get("assignees")
                    if not isinstance(assigned, list):
                        continue
                    # A GitHub assignee never proves an active queue/worker lease.
                    records[key] = {
                        "id": key, "project": project, "repository": repo,
                        "number": num, "title": title[:180], "priority": severity,
                        "qa_only": is_qa, "has_assignee": len(assigned) > 0,
                        "active_claim": "not_verified", "related_open_prs": [],
                        "created_at": created.isoformat(timespec="seconds"),
                        "updated_at": updated.isoformat(timespec="seconds"),
                        "url": "https://github.com/" + repo + "/issues/" + str(num),
                        "state": "open",
                    }
                statuses.append("bounded_truncated" if len(issues) == 12 else "bounded_window")
            except ValueError as error:
                statuses.append(str(error))
                if str(error) == "github_permission_or_rate_limit":
                    rate_or_auth_denied = True
        successful = sum(s.startswith("bounded_") for s in statuses)
        # Candidate reuse first: check a bounded recent window of existing
        # open PRs for explicit #issue references. This is NOT proof of
        # active ownership or acceptance: never claim the PR's branch.
        pr_coverage = ("not_fetched_after_rate_or_auth_denial" if rate_or_auth_denied
                       else "not_fetched_budget")
        if not rate_or_auth_denied and calls < MAX_CALLS:
            calls += 1
            params = urllib.parse.urlencode({
                "state": "open", "sort": "updated", "direction": "desc",
                "per_page": 8, "page": 1
            })
            pr_url = ("https://api.github.com/repos/" +
                      urllib.parse.quote(repo, safe="/") + "/pulls?" + params)
            try:
                open_prs = bounded_get(pr_url, token)
                if not isinstance(open_prs, list):
                    raise ValueError("github_pr_shape_invalid")
                for pr in open_prs:
                    if not isinstance(pr, dict) or not isinstance(pr.get("number"), int):
                        continue
                    number = pr["number"]
                    if isinstance(number, bool) or number < 1 or number > 100000000:
                        continue
                    body = pr.get("body")
                    if not isinstance(body, str) or len(body) > 32000:
                        continue
                    referenced = set(
                        int(m.group(1)) for m in re.finditer(
                            r"(?<![A-Za-z0-9/_])#([1-9][0-9]{0,7})\b", body
                        )
                    )
                    closing_references = {
                        int(m.group(1)) for m in re.finditer(
                            r"(?i)\b(?:fix(?:es)?|clos(?:e|es)|resolv(?:e|es))\s+#([1-9][0-9]{0,7})\b",
                            body
                        )
                    }
                    for issue_num in referenced:
                        record = records.get(repo + "#" + str(issue_num))
                        if record is not None and len(record["related_open_prs"]) < 3:
                            record["related_open_prs"].append({
                                "number": number,
                                "draft": pr.get("draft") is True,
                                "url": "https://github.com/" + repo + "/pull/" + str(number),
                                "relationship": ("closing_keyword_only" if issue_num in closing_references
                                                 else "text_reference_only"),
                                "active_claim": "not_verified",
                            })
                pr_coverage = "bounded_truncated" if len(open_prs) == 8 else "bounded_window"
            except ValueError as error:
                pr_coverage = str(error)
                if str(error) == "github_permission_or_rate_limit":
                    rate_or_auth_denied = True
        coverage.append({
            "project": project, "repository": repo, "oldest_window": statuses[0], "open_pr_window": pr_coverage,
            "recent_window": statuses[1],
            "status": ("partial" if successful == 2 and pr_coverage.startswith("bounded_")
                       else "degraded" if successful else "unavailable"),
            "active_claims_verified": False,
        })
    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "Unspecified": 3}
    grouped = defaultdict(list)
    excluded_qa = 0
    for entry in records.values():
        if entry["qa_only"]:
            excluded_qa += 1
            continue
        grouped[entry["project"]].append(entry)
    for values in grouped.values():
        values.sort(key=lambda x: (priority_rank[x["priority"]], x["created_at"], x["number"]))
    # One old high-impact issue must not be buried by a noisy repository.
    result = []
    offsets = {name: 0 for name in names}
    for severity in ("P0", "P1", "P2", "Unspecified"):
        while len(result) < max_items:
            progressed = False
            for name in names:
                values = grouped[name]
                i = offsets[name]
                while i < len(values) and priority_rank[values[i]["priority"]] < priority_rank[severity]:
                    i += 1
                if i < len(values) and values[i]["priority"] == severity:
                    result.append(values[i])
                    i += 1
                    progressed = True
                offsets[name] = i
                if len(result) >= max_items:
                    break
            if not progressed:
                break
    return {
        "schema": "zsentinel-product-issue-inbox-v1",
        "observed_at": observed.isoformat(timespec="seconds"),
        "projects": names, "coverage": coverage, "request_count": calls,
        "items": result, "suppressed_qa_only": excluded_qa,
        "stopped_after_rate_or_auth_denial": rate_or_auth_denied,
        "truncated": len(result) >= max_items or any("bounded_truncated" in
            (row["oldest_window"], row["recent_window"]) for row in coverage),
        "queue_ownership_known": False,
        "active_claims_verified": False,
        "actions_authorized": False,
        "fully_synchronized": False,
    }

STYLE = """
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#111923;color:#edf4ff}
body{max-width:1120px;margin:auto;padding:clamp(12px,3vw,38px);line-height:1.5}
header{margin-bottom:25px}h1{font-size:clamp(1.8rem,4vw,2.8rem)}.muted{color:#aec0d2}
.notice{border-left:4px solid #f6d08b;padding:14px 18px;background:#2a2928;border-radius:5px}
section{padding:16px;margin:16px 0;background:#1d2a37;border-radius:12px;border:1px solid #45586a}
.issue{padding:13px 0;border-bottom:1px solid #45586a}.issue:last-child{border-bottom:0}
.issue-title{display:block;font-size:1.07rem;font-weight:650}a{color:#c1f2a4;text-underline-offset:3px}
a:focus-visible,summary:focus-visible{outline:3px solid #c1f2a4;outline-offset:3px}
.tag{font-size:.8rem;font-weight:700;border:1px solid #a2b0c1;padding:3px 6px;border-radius:5px;margin-right:7px}
small{color:#b6c4d4}summary{cursor:pointer}footer{margin:35px 0;color:#aec0d2}
"""

def html_view(data):
    sections = []
    for priority_name in ("P0", "P1", "P2", "Unspecified"):
        rows = [row for row in data["items"] if row["priority"] == priority_name]
        lines = []
        for row in rows:
            title = escape(row["title"])
            url = escape(row["url"], quote=True)
            lines.append("<div class='issue'><a class='issue-title' href='" + url
                         + "' target='_blank' rel='noopener noreferrer'>"
                         + title + "</a><span class='tag'>" + escape(row["project"]) +
                         "</span><small>" + str(row["number"]) +
                         " · created " + escape(row["created_at"][:10]) +
                         " · updated " + escape(row["updated_at"][:10]) +
                         " · " + ("GitHub assignee present" if row["has_assignee"]
                                   else "No GitHub assignee") +
                         " · active worker ownership unknown</small></div>")
            candidates = row.get("related_open_prs") or []
            if candidates:
                refs = []
                for candidate in candidates:
                    ref = escape(candidate["url"], quote=True)
                    relationship = ("mentions" if candidate["relationship"] == "text_reference_only"
                                    else "closing keyword")
                    label = (("Draft PR #" if candidate["draft"] else "Open PR #") +
                             str(candidate["number"]) + " · " + relationship)
                    refs.append("<a href='" + ref + "' target='_blank' rel='noopener noreferrer'>" +
                                escape(label) + "</a>")
                lines.append("<div class='muted'><small>Open PR text references (not verified fixes or owners): " +
                             " · ".join(refs) + "</small></div>")
        sections.append("<section><h2>" + escape(priority_name) + " · " + str(len(rows))
                        + " sampled issues</h2>" +
                        ("".join(lines) or "<p class='muted'>No sampled issues in this class. Coverage is not exhaustive.</p>")
                        + "</section>")
    coverage = []
    for item in data["coverage"]:
        coverage.append("<li><b>" + escape(item["project"]) + "</b> — oldest " +
                        escape(item["oldest_window"]) + "; recent " +
                        escape(item["recent_window"]) + "; recent open PRs " + escape(item["open_pr_window"]) + "</li>")
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<meta name='referrer' content='no-referrer'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'; connect-src 'none'\">"
            "<title>zSentinel — Product Issue Inbox</title><style>" + STYLE + "</style></head>"
            "<body><header><p class='muted'>ZCLOUD · READ-ONLY PRODUCT DELIVERY</p>"
            "<h1>zSentinel Issue Inbox</h1><p>Existing GitHub issues prioritized by "
            "explicit P0/P1 label or title, oldest first within each priority. "
            "QA-only work excluded by the 17 October product policy.</p><p class='notice'>"
            "Partial bounded sample, not a full backlog or active claim/runner inventory. "
            "A GitHub assignee is not an active worker claim. On a GitHub 403/429, later repos are not queried. Do not overwrite owned work, "
            "perform recovery, merge or deploy from this page.</p><p class='muted'>"
            "Observed " + escape(data["observed_at"]) + " · requests " +
            str(data["request_count"]) + " · QA-only excluded " +
            str(data["suppressed_qa_only"]) + "</p></header><main>" +
            "".join(sections) +
            "<section><details><summary>GitHub source coverage by project</summary>"
            "<ul>" + "".join(coverage) + "</ul></details></section></main>"
            "<footer>All external links open GitHub. Read-only, no tracking or remote scripts. "
            "A product commit, mandatory CI/review and authorized deploy are separate gates."
            "</footer></body></html>")

def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only product P0/P1 GitHub issue inbox")
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--all", action="store_true")
    selector.add_argument("--project", action="append")
    parser.add_argument("--max-items", type=int, default=75)
    parser.add_argument("--format", choices=["html", "json"], default="html")
    args = parser.parse_args(argv)
    try:
        projects = canonical_projects(args.registry)
        selected = list(projects) if args.all else args.project
        data = collect(projects, selected, os.getenv("GITHUB_TOKEN"), args.max_items)
    except (ValueError, OSError, json.JSONDecodeError):
        print("zSentinel issue source unavailable or invalid (no claim taken).", file=sys.stderr)
        return 2
    if args.format == "html":
        sys.stdout.write(html_view(data))
    else:
        print(json.dumps(data, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0 if all(row["status"] == "partial" for row in data["coverage"]) else 3

if __name__ == "__main__":
    raise SystemExit(main())
