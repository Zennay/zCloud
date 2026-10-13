#!/usr/bin/env python3
"""Read-only, fail-closed GitHub PR-less ownership snapshot (#1164).

This program does not grant release/ownership admission. It records complete
GitHub branch and PR inventories at one stable canonical main commit, or denies.
No VPS, SQLite, worker, queue, workflow or repository mutation is performed.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

OWNER, REPO = "Zennay", "zCloud"
API = "https://api.github.com"
SHA = re.compile(r"^[0-9a-f]{40}$")
NAME = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")
MAX_PAGES = 12
MAX_ENTRIES = 1000


class InventoryDenied(ValueError):
    pass


def _sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise InventoryDenied("invalid_commit_sha")
    return value


def _name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value) or ".." in value:
        raise InventoryDenied("invalid_branch_name")
    return value


def _pages(fetch, kind):
    """Fetch every page, denying gaps, repeats, malformed links and limits.

    fetch(kind, page) returns (JSON list, next_page_or_none). next_page is
    checked for contiguous progression; an exhausted cursor is mandatory.
    """
    entries, seen, page = [], set(), 1
    while True:
        if page > MAX_PAGES:
            raise InventoryDenied("page_cap")
        payload, following = fetch(kind, page)
        if not isinstance(payload, list):
            raise InventoryDenied("malformed_page")
        for entry in payload:
            if not isinstance(entry, dict):
                raise InventoryDenied("malformed_entry")
            entries.append(entry)
            if len(entries) > MAX_ENTRIES:
                raise InventoryDenied("entry_cap")
        if following is None:
            return entries, page
        if isinstance(following, bool) or not isinstance(following, int):
            raise InventoryDenied("malformed_cursor")
        if following in seen or following != page + 1:
            raise InventoryDenied("cursor_cycle_or_gap")
        seen.add(page)
        if not payload:
            raise InventoryDenied("empty_middle_page")
        page = following


def _map_branches(entries):
    result = {}
    for row in entries:
        name = _name(row.get("name"))
        sha = _sha((row.get("commit") or {}).get("sha")
                   if isinstance(row.get("commit"), dict) else None)
        if name in result:
            raise InventoryDenied("duplicate_branch")
        result[name] = sha
    return result


def _map_pr_heads(entries):
    result = {}
    for row in entries:
        head = row.get("head")
        if not isinstance(head, dict):
            raise InventoryDenied("invalid_pr_head")
        repo = head.get("repo")
        if repo is None:
            # Deleted forks need explicit reviewer handling, never "complete".
            raise InventoryDenied("missing_pr_head_repo")
        if not isinstance(repo, dict) or not isinstance(repo.get("full_name"), str):
            raise InventoryDenied("invalid_pr_head_repo")
        if repo["full_name"].lower() != f"{OWNER}/{REPO}".lower():
            continue  # external fork cannot own a local branch name
        branch, sha = _name(head.get("ref")), _sha(head.get("sha"))
        if branch in result and result[branch] != sha:
            raise InventoryDenied("conflicting_pr_heads")
        result[branch] = sha
    return result


def collect(fetch, expected_main_sha):
    """Only a fully reconciled snapshot is marked complete; never authorizes."""
    expected = _sha(expected_main_sha)
    if _sha(fetch("main", 0)[0]) != expected:
        raise InventoryDenied("main_drift_before")
    branches_raw, branch_pages = _pages(fetch, "branches")
    prs_raw, pr_pages = _pages(fetch, "pulls")
    branches = _map_branches(branches_raw)
    pr_heads = _map_pr_heads(prs_raw)
    for branch, head_sha in pr_heads.items():
        if branch in branches and branches[branch] != head_sha:
            raise InventoryDenied("pr_head_drift")
    # Repeat BOTH inventories after their complete first read: same names,
    # identities and page exhaustion. Do not admit partial snapshots.
    branches_again, _ = _pages(fetch, "branches")
    prs_again, _ = _pages(fetch, "pulls")
    if _map_branches(branches_again) != branches:
        raise InventoryDenied("branch_inventory_drift")
    if _map_pr_heads(prs_again) != pr_heads:
        raise InventoryDenied("pr_inventory_drift")
    if _sha(fetch("main", 0)[0]) != expected:
        raise InventoryDenied("main_drift_after")
    pr_less = [{"branch": name, "commit_sha": sha}
               for name, sha in sorted(branches.items())
               if name != "main" and name not in pr_heads]
    return {
        "schema": "zcloud-prless-branch-snapshot-v1",
        "main_sha": expected, "inventory_complete": True,
        "pr_less": pr_less, "branch_count": len(branches),
        "open_pr_head_count": len(pr_heads),
        "branch_pages": branch_pages, "pr_pages": pr_pages,
        "admission_authorized": False, "deployment_authorized": False,
        "paths_compared": False,
    }


class _DenyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an authenticated GitHub read to another origin.
        raise InventoryDenied("redirect_denied")


def github_fetcher(token):
    """GET-only transport; strict response size, Link origin/path and JSON."""
    def fetch(kind, page):
        root = f"/repos/{OWNER}/{REPO}"
        if kind == "main" and page == 0:
            path = root + "/git/ref/heads/main"
        elif kind in ("branches", "pulls") and 1 <= page <= MAX_PAGES:
            suffix = ("branches" if kind == "branches" else "pulls")
            extra = "&state=open" if kind == "pulls" else ""
            path = f"{root}/{suffix}?per_page=100&page={page}{extra}"
        else:
            raise InventoryDenied("invalid_request")
        headers = {"Accept": "application/vnd.github+json",
                   "User-Agent": "zcloud-deploy-readonly-inventory"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(API + path, headers=headers,
                                         method="GET")
        try:
            with urllib.request.build_opener(_DenyRedirect()).open(request, timeout=12) as response:
                if response.status != 200:
                    raise InventoryDenied("github_response_failed")
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise InventoryDenied("response_too_large")
                link = response.headers.get("Link", "")
        except (OSError, urllib.error.HTTPError, TimeoutError) as exc:
            raise InventoryDenied("github_request_failed") from None
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise InventoryDenied("invalid_github_json") from None
        if kind == "main":
            if not isinstance(payload, dict) or not isinstance(payload.get("object"), dict):
                raise InventoryDenied("invalid_main_ref")
            return payload["object"].get("sha"), None
        # Parse only GitHub's canonical Link rel=next, same exact repository,
        # list kind, state filter and per_page; no remote URL following.
        next_page = None
        for item in link.split(","):
            if not item.strip():
                continue
            parts = item.strip().split(";")
            if len(parts) < 2 or not parts[0].startswith("<") or not parts[0].endswith(">"):
                raise InventoryDenied("invalid_link")
            if not any(part.strip() == 'rel="next"' for part in parts[1:]):
                continue
            url = urllib.parse.urlparse(parts[0][1:-1])
            expected_path = root + "/" + kind
            q = urllib.parse.parse_qs(url.query, strict_parsing=True)
            required = {"per_page", "page"} | ({"state"} if kind == "pulls" else set())
            if (url.scheme != "https" or url.netloc != "api.github.com"
                    or url.path != expected_path or set(q) != required
                    or q.get("per_page") != ["100"]
                    or (kind == "pulls" and q.get("state") != ["open"])
                    or len(q.get("page", [])) != 1):
                raise InventoryDenied("link_origin_or_query_mismatch")
            try:
                value = int(q["page"][0])
            except ValueError:
                raise InventoryDenied("invalid_link_page") from None
            if next_page is not None:
                raise InventoryDenied("duplicate_next_link")
            next_page = value
        return payload, next_page
    return fetch


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 1:
            raise InventoryDenied("expected_exact_main_sha")
        evidence = collect(github_fetcher(os.environ.get("GITHUB_TOKEN", "")), argv[0])
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
        return 0
    except InventoryDenied as error:
        print(json.dumps({"schema": "zcloud-prless-branch-snapshot-v1",
                          "inventory_complete": False, "reason": str(error),
                          "admission_authorized": False,
                          "deployment_authorized": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
