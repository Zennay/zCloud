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
            # A full final page is ambiguous if Link pagination was omitted.
            # Deny rather than treating potentially truncated ownership as clear.
            if len(payload) >= 100:
                raise InventoryDenied("full_page_without_cursor")
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


def _path(value):
    """Exact repository-relative source path, or a directory prefix ending /."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 300
            or value.startswith("/") or "\\" in value
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise InventoryDenied("invalid_changed_path")
    segments = value.rstrip("/").split("/")
    if not segments or any(segment in ("", ".", "..") for segment in segments):
        raise InventoryDenied("invalid_changed_path")
    return value


def _path_conflict(changed, owned):
    return (changed == owned or changed.startswith(owned.rstrip("/") + "/")
            or owned.startswith(changed.rstrip("/") + "/"))


def compare_owned_paths(fetch, snapshot, owned_paths):
    """Examine exact immutable compare objects; report collisions, never admit.

    GitHub's compare API caps changed-file reporting at 300 files. Refuse
    potentially truncated file lists and >250 commits. This is still not
    production ownership admission: caller supplied paths are not authority.
    """
    if (not isinstance(snapshot, dict)
            or snapshot.get("schema") != "zcloud-prless-branch-snapshot-v1"
            or snapshot.get("inventory_complete") is not True
            or snapshot.get("admission_authorized") is not False
            or snapshot.get("deployment_authorized") is not False):
        raise InventoryDenied("untrusted_inventory")
    main_sha = _sha(snapshot.get("main_sha"))
    if not isinstance(owned_paths, (list, tuple)) or not owned_paths:
        raise InventoryDenied("missing_owned_paths")
    if len(owned_paths) > 200:
        raise InventoryDenied("owned_path_cap")
    owned = sorted(set(_path(p) for p in owned_paths))
    candidates = snapshot.get("pr_less")
    if not isinstance(candidates, list) or len(candidates) > 150:
        raise InventoryDenied("branch_compare_cap")
    collisions = set()
    checked = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise InventoryDenied("malformed_candidate")
        _name(candidate.get("branch"))
        target_sha = _sha(candidate.get("commit_sha"))
        payload, next_page = fetch(f"compare:{main_sha}...{target_sha}", 0)
        if next_page is not None or not isinstance(payload, dict):
            raise InventoryDenied("incomplete_compare")
        base = payload.get("base_commit")
        if not isinstance(base, dict) or _sha(base.get("sha")) != main_sha:
            raise InventoryDenied("compare_base_mismatch")
        head = payload.get("head_commit")
        if not isinstance(head, dict) or _sha(head.get("sha")) != target_sha:
            raise InventoryDenied("compare_head_mismatch")
        commits = payload.get("total_commits")
        ahead = payload.get("ahead_by")
        if (type(commits) is not int or not 0 <= commits <= 250
                or type(ahead) is not int or not 0 <= ahead <= 250):
            raise InventoryDenied("compare_commit_cap_or_invalid")
        if payload.get("status") not in ("ahead", "behind", "diverged", "identical"):
            raise InventoryDenied("invalid_compare_status")
        # GitHub compare can paginate the commit history independently of files.
        # A first-page-only response cannot attest a complete source diff.
        listed_commits = payload.get("commits")
        if (not isinstance(listed_commits, list)
                or len(listed_commits) != commits
                or any(not isinstance(item, dict)
                       or not SHA.fullmatch(str(item.get("sha") or ""))
                       for item in listed_commits)):
            raise InventoryDenied("compare_commit_history_incomplete")
        files = payload.get("files")
        if not isinstance(files, list) or len(files) >= 300:
            raise InventoryDenied("compare_files_incomplete")
        for changed in files:
            if not isinstance(changed, dict):
                raise InventoryDenied("malformed_compare_file")
            paths = [_path(changed.get("filename"))]
            if changed.get("status") == "renamed":
                paths.append(_path(changed.get("previous_filename")))
            for item in paths:
                for claim in owned:
                    if _path_conflict(item, claim):
                        collisions.add((candidate["branch"], item, claim))
        checked += 1
    # Expensive comparisons cannot span a silently moving branch inventory.
    fresh = collect(fetch, main_sha)
    if (fresh["pr_less"] != candidates or
            fresh["branch_count"] != snapshot.get("branch_count") or
            fresh["open_pr_head_count"] != snapshot.get("open_pr_head_count")):
        raise InventoryDenied("post_compare_inventory_drift")
    return {
        "schema": "zcloud-prless-overlap-evidence-v1",
        "main_sha": main_sha,
        "inventory_complete": True,
        "paths_compared": True,
        "compared_branches": checked,
        "overlap_found": bool(collisions),
        "overlaps": [
            {"branch": branch, "changed_path": changed, "owned_path": owner}
            for branch, changed, owner in sorted(collisions)
        ],
        "admission_authorized": False,
        "deployment_authorized": False,
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
        elif kind.startswith("compare:") and page == 0:
            match = re.fullmatch(r"compare:([a-f0-9]{40})\.\.\.([a-f0-9]{40})", kind)
            if match is None:
                raise InventoryDenied("invalid_compare_identity")
            path = root + "/compare/" + match.group(1) + "..." + match.group(2)
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
        if kind.startswith("compare:"):
            # Compare's Link header indicates incomplete commit pagination;
            # GitHub only presents changed files on the first compare page.
            # This consumer deliberately refuses partial compares.
            if link.strip():
                raise InventoryDenied("paginated_compare_incomplete")
            return payload, None
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
        if not argv or len(argv) > 401 or len(argv[1:]) % 2:
            raise InventoryDenied("expected_exact_main_sha_and_optional_owned_paths")
        path_args = argv[1:]
        if any(path_args[i] != "--owned-path" for i in range(0, len(path_args), 2)):
            raise InventoryDenied("invalid_owned_path_argument")
        owned_paths = [path_args[i + 1] for i in range(0, len(path_args), 2)]
        fetch = github_fetcher(os.environ.get("GITHUB_TOKEN", ""))
        evidence = collect(fetch, argv[0])
        if owned_paths:
            evidence = compare_owned_paths(fetch, evidence, owned_paths)
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
