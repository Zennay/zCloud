#!/usr/bin/env python3
"""Collect a bounded, read-only inventory of local branches without open PRs.

A PR-less branch is not necessarily abandoned, unowned, safe to delete, or
eligible to merge. This collector never changes refs or authorizes release.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

from scripts.zcloud_deployops_pr_owner_collision_20261015 import (
    InvalidInventory, MAX_PRS, SHA,
)
from scripts.zcloud_deployops_pr_owner_snapshot_20261015 import (
    REPO, _main_sha, github_reader,
)

MAX_BRANCH_PAGES = 50
MAX_BRANCHES = 5000
MAX_PR_PAGES = 10
MAX_OUTPUT_BYTES = 1_800_000


def _bounded_text(value, limit=255):
    return (type(value) is str and 1 <= len(value) <= limit
            and all(ord(char) >= 32 and ord(char) != 127 for char in value))


def _branches(read):
    """Read every branch page; refuse truncation, duplicates, and weak identities."""
    rows = []
    seen = set()
    complete = False
    for page in range(1, MAX_BRANCH_PAGES + 1):
        batch = read(f"/branches?per_page=100&page={page}")
        if type(batch) is not list or len(batch) > 100:
            raise InvalidInventory("invalid_branch_page")
        for entry in batch:
            if type(entry) is not dict:
                raise InvalidInventory("invalid_branch_page_item")
            name = entry.get("name")
            commit = entry.get("commit")
            sha = commit.get("sha") if type(commit) is dict else None
            protected = entry.get("protected")
            if (not _bounded_text(name) or name in seen
                    or type(sha) is not str or SHA.fullmatch(sha) is None
                    or type(protected) is not bool):
                raise InvalidInventory("invalid_or_duplicate_branch_identity")
            seen.add(name)
            rows.append({"name": name, "head_sha": sha, "protected": protected})
            if len(rows) > MAX_BRANCHES:
                raise InvalidInventory("branch_inventory_limit_exceeded")
        if len(batch) < 100:
            complete = True
            break
    if not complete:
        raise InvalidInventory("incomplete_branch_pagination")
    return rows


def _open_pr_heads(read):
    """Capture full open-PR identity needed to classify same-repository heads."""
    rows = []
    seen = set()
    complete = False
    for page in range(1, MAX_PR_PAGES + 1):
        batch = read(f"/pulls?state=open&per_page=100&page={page}")
        if type(batch) is not list or len(batch) > 100:
            raise InvalidInventory("invalid_open_pr_page")
        for entry in batch:
            if type(entry) is not dict:
                raise InvalidInventory("invalid_open_pr_item")
            number = entry.get("number")
            head, base = entry.get("head"), entry.get("base")
            head_repo = head.get("repo") if type(head) is dict else None
            head_repo_name = (head_repo.get("full_name")
                              if type(head_repo) is dict else None)
            head_ref = head.get("ref") if type(head) is dict else None
            head_sha = head.get("sha") if type(head) is dict else None
            base_sha = base.get("sha") if type(base) is dict else None
            if (type(number) is not int or not 1 <= number <= 10_000_000
                    or number in seen or entry.get("state") != "open"
                    or not _bounded_text(head_repo_name, 200)
                    or not _bounded_text(head_ref)
                    or type(head_sha) is not str or SHA.fullmatch(head_sha) is None
                    or type(base_sha) is not str or SHA.fullmatch(base_sha) is None):
                raise InvalidInventory("incomplete_open_pr_identity")
            seen.add(number)
            rows.append({
                "number": number, "head_repo": head_repo_name,
                "head_ref": head_ref, "head_sha": head_sha,
                "base_sha": base_sha,
            })
            if len(rows) > MAX_PRS:
                raise InvalidInventory("open_pr_inventory_limit_exceeded")
        if len(batch) < 100:
            complete = True
            break
    if not complete:
        raise InvalidInventory("incomplete_open_pr_pagination")
    return rows


def collect(read):
    """Require complete, repeatable branch/PR inventories and an unchanged main."""
    main_before = _main_sha(read)
    branches_before = _branches(read)
    prs_before = _open_pr_heads(read)
    branches_after = _branches(read)
    prs_after = _open_pr_heads(read)
    main_after = _main_sha(read)

    if branches_before != branches_after:
        raise InvalidInventory("branch_inventory_changed_during_snapshot")
    if prs_before != prs_after:
        raise InvalidInventory("open_pr_inventory_changed_during_snapshot")
    if main_before != main_after:
        raise InvalidInventory("main_changed_during_snapshot")

    main_rows = [row for row in branches_before if row["name"] == "main"]
    if len(main_rows) != 1 or main_rows[0]["head_sha"] != main_before:
        raise InvalidInventory("main_branch_mismatch")

    open_by_ref = {}
    for pr in prs_before:
        if pr["head_repo"] == REPO:
            open_by_ref.setdefault(pr["head_ref"], []).append(pr["number"])

    annotated = []
    prless = []
    for row in sorted(branches_before, key=lambda item: item["name"]):
        open_numbers = sorted(open_by_ref.get(row["name"], []))
        item = {
            **row,
            "open_pr_numbers": open_numbers,
            "classification": ("main" if row["name"] == "main"
                               else "has_open_pr" if open_numbers
                               else "prless"),
        }
        annotated.append(item)
        if item["classification"] == "prless":
            prless.append({
                "name": item["name"], "head_sha": item["head_sha"],
                "protected": item["protected"],
            })

    result = {
        "policy": "zcloud-deployops-prless-branch-inventory-v1",
        "repo": REPO,
        "main_sha": main_before,
        "branch_inventory_complete": True,
        "open_pr_inventory_complete": True,
        "branches_scanned": len(annotated),
        "open_prs_scanned": len(prs_before),
        "prless_branch_count": len(prless),
        "branches": annotated,
        "prless_branches": prless,
        "mutation_performed": False,
        "release_authorized": False,
        "deployment_authorized": False,
    }
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    result["snapshot_sha256_unverified"] = hashlib.sha256(canonical).hexdigest()
    rendered = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if len(rendered.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise InvalidInventory("branch_inventory_output_limit_exceeded")
    return rendered


def main():
    try:
        print(collect(github_reader(os.environ.get("GITHUB_TOKEN", ""))))
    except (InvalidInventory, UnicodeError, RecursionError) as exc:
        reason = str(exc) if isinstance(exc, InvalidInventory) else "invalid_github_response"
        print(json.dumps({
            "policy": "zcloud-deployops-prless-branch-inventory-v1",
            "state": "refused", "reason": reason,
            "mutation_performed": False, "release_authorized": False,
            "deployment_authorized": False,
        }, sort_keys=True, separators=(",", ":")))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
