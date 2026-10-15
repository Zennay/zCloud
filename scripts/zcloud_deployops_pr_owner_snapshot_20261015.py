#!/usr/bin/env python3
"""Collect a read-only exact-main GitHub PR ownership snapshot (not approval).

GitHub API data is advisory and never authorizes merge, deploy, runner recovery,
SQLite mutation or an operator action. PR-less branches are OUT OF SCOPE.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from scripts.zcloud_deployops_pr_owner_collision_20261015 import (
    InvalidInventory, MAX_FILES, MAX_PRS, SHA, unique_json, assess,
)

REPO = "Zennay/zCloud"
API = "https://api.github.com/repos/" + REPO


class DenyRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise InvalidInventory("github_redirect_denied")


def github_reader(token):
    """Build a GET-only reader; never redirect or include credentials in errors."""
    if not isinstance(token, str) or not 1 <= len(token) <= 1024 or any(
        ord(c) < 33 or ord(c) > 126 for c in token
    ):
        raise InvalidInventory("invalid_read_token")
    opener = build_opener(DenyRedirect())

    def read(path):
        if not isinstance(path, str) or not path.startswith("/") or ".." in path:
            raise InvalidInventory("invalid_api_path")
        url = API + path
        request = Request(
            url, method="GET",
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "User-Agent": "zcloud-pr-ownership-readonly/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with opener.open(request, timeout=15) as response:
                if (response.geturl() != url
                        or response.headers.get_content_type() != "application/json"):
                    raise InvalidInventory("untrusted_github_response")
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise InvalidInventory("oversize_github_response")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise InvalidInventory("github_read_failed") from exc
        return unique_json(raw.decode("utf-8"))
    return read


def _main_sha(read):
    info = read("/branches/main")
    commit = info.get("commit") if type(info) is dict else None
    sha = commit.get("sha") if type(commit) is dict else None
    if type(sha) is not str or SHA.fullmatch(sha) is None:
        raise InvalidInventory("untrusted_main_revision")
    return sha


def collect(read, *, max_pages=10):
    """Require full bounded PR pagination and exact stable main before reporting."""
    if type(max_pages) is not int or not 1 <= max_pages <= 10:
        raise InvalidInventory("invalid_page_budget")
    main_before = _main_sha(read)
    seen = set()
    original_order = []
    prs = []
    completed = False
    for page in range(1, max_pages + 1):
        batch = read(f"/pulls?state=open&per_page=100&page={page}")
        if type(batch) is not list or len(batch) > 100:
            raise InvalidInventory("invalid_pr_page")
        for entry in batch:
            if type(entry) is not dict:
                raise InvalidInventory("invalid_pr_page_item")
            number = entry.get("number")
            if (type(number) is not int or not 1 <= number <= 10_000_000
                    or number in seen):
                raise InvalidInventory("duplicate_or_invalid_pr_page")
            seen.add(number)
            original_order.append(number)
            if len(seen) > MAX_PRS:
                raise InvalidInventory("unbounded_pr_inventory")
            detail = read(f"/pulls/{number}")
            if (type(detail) is not dict or type(detail.get("number")) is not int
                    or detail["number"] != number):
                raise InvalidInventory("wrong_pr_detail")
            changed = detail.get("changed_files")
            if type(changed) is not int or not 1 <= changed <= MAX_FILES:
                raise InvalidInventory("incomplete_or_oversized_pr_files")
            # The GitHub files API is independently paginated at 100 entries.
            # Never trust page one as a complete PR, even when its 100 entries
            # look valid. Reject empty, overflowing or non-list pages.
            batch_files = []
            for file_page in range(1, (changed + 99) // 100 + 1):
                fragment = read(f"/pulls/{number}/files?per_page=100&page={file_page}")
                remaining = changed - len(batch_files)
                expected_size = min(100, remaining)
                if type(fragment) is not list or len(fragment) != expected_size:
                    raise InvalidInventory("incomplete_pr_files_page")
                batch_files.extend(fragment)
            if len(batch_files) != changed:
                raise InvalidInventory("incomplete_pr_files_page")
            def ident(ref):
                return ref.get("sha") if type(ref) is dict else None
            def origin(ref):
                repo = ref.get("repo") if type(ref) is dict else None
                return repo.get("full_name") if type(repo) is dict else None
            head, base = detail.get("head"), detail.get("base")
            files = []
            for item in batch_files:
                if type(item) is not dict:
                    raise InvalidInventory("invalid_file_detail")
                file_info = {"filename": item.get("filename"),
                             "status": item.get("status")}
                if item.get("status") == "renamed":
                    file_info["previous_filename"] = item.get("previous_filename")
                files.append(file_info)
            proof = {
                "number": number, "state": detail.get("state"),
                "head_sha": ident(head), "base_sha": ident(base),
                "head_repo": origin(head), "base_repo": origin(base),
                "changed_files": changed, "files": files,
            }
            # Files endpoint is NOT head-SHA pinned. Re-read the PR
            # immediately to reject a ref moving between the two GETs.
            after = read(f"/pulls/{number}")
            if (type(after) is not dict
                    or type(after.get("number")) is not int
                    or type(after.get("changed_files")) is not int
                    or (
                after.get("number"), after.get("state"),
                after.get("changed_files"),
                ident(after.get("head")), ident(after.get("base")),
                origin(after.get("head")), origin(after.get("base")),
            ) != (
                number, proof["state"], changed, proof["head_sha"],
                proof["base_sha"], proof["head_repo"], proof["base_repo"],
            )):
                raise InvalidInventory("pr_changed_during_snapshot")
            prs.append(proof)
        if len(batch) < 100:
            completed = True
            break
    if not completed:
        raise InvalidInventory("incomplete_pr_pagination")
    # Offset pagination can silently skip/duplicate PRs if an owner
    # opens/closes a PR during the scan. Compare the full ordered list.
    second_order = []
    second_complete = False
    for page in range(1, max_pages + 1):
        batch = read(f"/pulls?state=open&per_page=100&page={page}")
        if type(batch) is not list or len(batch) > 100:
            raise InvalidInventory("invalid_pr_recheck_page")
        for entry in batch:
            if type(entry) is not dict or type(entry.get("number")) is not int:
                raise InvalidInventory("invalid_pr_recheck_item")
            second_order.append(entry["number"])
        if len(batch) < 100:
            second_complete = True
            break
    if not second_complete or second_order != original_order:
        raise InvalidInventory("pr_inventory_changed_during_snapshot")
    # A PR may advance while another PR's files are inspected without
    # changing the numbered PR list or main. Reconfirm EVERY captured
    # source identity before emitting a cross-PR ownership graph.
    for proof in prs:
        latest = read(f"/pulls/{proof['number']}")
        if type(latest) is not dict:
            raise InvalidInventory("pr_changed_before_snapshot_completed")
        head, base = latest.get("head"), latest.get("base")
        def identity(ref, field):
            if type(ref) is not dict:
                return None
            if field == "sha":
                return ref.get("sha")
            origin = ref.get("repo")
            return origin.get("full_name") if type(origin) is dict else None
        if (type(latest.get("number")) is not int
                or type(latest.get("changed_files")) is not int
                or latest["number"] != proof["number"]
                or latest.get("state") != proof["state"]
                or latest["changed_files"] != proof["changed_files"]
                or identity(head, "sha") != proof["head_sha"]
                or identity(base, "sha") != proof["base_sha"]
                or identity(head, "repo") != proof["head_repo"]
                or identity(base, "repo") != proof["base_repo"]):
            raise InvalidInventory("pr_changed_before_snapshot_completed")
    main_after = _main_sha(read)
    if main_before != main_after:
        raise InvalidInventory("main_changed_during_snapshot")
    result = {"repo": REPO, "main_sha": main_before, "pulls": prs}
    assess(result)  # enforce canonical shapes and complete ownership evidence
    return result


def main():
    try:
        token = os.environ.get("GITHUB_TOKEN", "")
        snapshot = collect(github_reader(token))
        print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
    except (InvalidInventory, UnicodeError, RecursionError) as exc:
        reason = str(exc) if isinstance(exc, InvalidInventory) else "invalid_github_response"
        print(json.dumps({"state": "refused", "reason": reason,
                          "mutation_performed": False}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
