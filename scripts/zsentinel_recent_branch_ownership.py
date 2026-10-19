#!/usr/bin/env python3
"""Bounded read-only recent un-PR branch ownership evidence (zCloud #870).

This is an advisory collision preflight, not a lease, worker heartbeat or
authorized claim. It never writes to GitHub, zCloud, Notion, or the VPS.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

from zsentinel_product_issue_inbox import ROOT, bounded_get, canonical_projects

SHA = re.compile(r"^[0-9a-f]{40}$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,119}$")
PATH = re.compile(r"^[A-Za-z0-9_.@/+ -]{1,220}$")
REPO = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")
MAX_BRANCH_PAGES = 4
MAX_PR_PAGES = 3
PAGE_SIZE = 100
MAX_CANDIDATES = 12
MAX_FILES = 100
RECENT_HOURS = 72
UTC = timezone.utc


def bounded_branch(name):
    if (not isinstance(name, str) or not BRANCH.fullmatch(name)
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise ValueError("unsafe GitHub branch ref")
    return name


def bounded_path(name):
    if (not isinstance(name, str) or not PATH.fullmatch(name)
            or name.startswith("/") or name.endswith("/")
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise ValueError("invalid GitHub changed path")
    return name


def overlaps(a, b):
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def pages(root, suffix, token, maximum, extra=None):
    all_rows = []
    for page in range(1, maximum + 1):
        query = {"per_page": PAGE_SIZE, "page": page}
        if extra:
            query.update(extra)
        data = bounded_get(root + suffix + "?" + urlencode(query), token)
        if not isinstance(data, list) or len(data) > PAGE_SIZE:
            raise ValueError("invalid bounded GitHub list source")
        all_rows.extend(data)
        if len(data) < PAGE_SIZE:
            return all_rows
    raise ValueError("incomplete branch/PR listing; manual owner inspection needed")


def parse_time(value):
    if not isinstance(value, str) or len(value) > 45:
        raise ValueError("invalid exact commit clock")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid exact commit clock") from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("commit clock requires timezone")
    return stamp.astimezone(UTC)


def inventory(repo, token=None, prefix=None, current_paths=None, now=None):
    if not isinstance(repo, str) or not REPO.fullmatch(repo):
        raise ValueError("use a canonical Zennay repository")
    if prefix is not None:
        prefix = bounded_branch(prefix)
    current_paths = [] if current_paths is None else current_paths
    if not isinstance(current_paths, list) or len(current_paths) > 25:
        raise ValueError("at most 25 current scoped changed paths")
    current_paths = [bounded_path(p) for p in current_paths]
    instant = (now or datetime.now(UTC))
    if instant.tzinfo is None:
        raise ValueError("timezone-aware clock required")
    instant = instant.astimezone(UTC)
    root = "https://api.github.com/repos/" + repo
    branches = pages(root, "/branches", token, MAX_BRANCH_PAGES)
    pulls = pages(root, "/pulls", token, MAX_PR_PAGES, {"state":"open"})
    represented = set()
    for pr in pulls:
        if not isinstance(pr, dict) or not isinstance(pr.get("head"), dict):
            raise ValueError("incomplete open PR head ownership")
        head = pr["head"]
        source = head.get("repo")
        if not isinstance(source, dict) or not isinstance(source.get("full_name"), str):
            raise ValueError("unknown open PR head repository")
        if source["full_name"] == repo:
            represented.add(bounded_branch(head.get("ref")))
    ref = bounded_get(root + "/git/ref/heads/main", token)
    base = ref.get("object") if isinstance(ref, dict) else None
    main_sha = base.get("sha") if isinstance(base, dict) else None
    if not isinstance(main_sha, str) or not SHA.fullmatch(main_sha):
        raise ValueError("exact current main reference unavailable")
    candidates = []
    seen = set()
    for branch in branches:
        if not isinstance(branch, dict) or not isinstance(branch.get("commit"), dict):
            raise ValueError("invalid branch inventory")
        name = bounded_branch(branch.get("name"))
        commit = branch["commit"].get("sha")
        if not isinstance(commit, str) or not SHA.fullmatch(commit) or name in seen:
            raise ValueError("duplicate or invalid exact branch SHA")
        seen.add(name)
        if name == "main" or name in represented or (prefix and not name.startswith(prefix)):
            continue
        candidates.append((name, commit))
    # Alphabetical branch listing is not proof that omitted names are stale.
    coverage_complete = len(candidates) <= MAX_CANDIDATES
    candidates = candidates[:MAX_CANDIDATES]
    recent = []
    historical = 0
    not_ahead = 0
    incomplete_paths = 0
    for name, sha in candidates:
        commit = bounded_get(root + "/commits/" + sha, token)
        if not isinstance(commit, dict) or commit.get("sha") != sha:
            raise ValueError("exact commit receipt mismatch")
        inner = commit.get("commit")
        committer = inner.get("committer") if isinstance(inner, dict) else None
        if not isinstance(committer, dict):
            raise ValueError("missing exact commit timestamp")
        written = parse_time(committer.get("date"))
        if written > instant + timedelta(minutes=2):
            raise ValueError("future commit timestamp, source untrusted")
        age_hours = max(0, int((instant-written).total_seconds() // 3600))
        if age_hours > RECENT_HOURS:
            historical += 1
            continue
        # Compare immutable SHAs: ref names can move between requests.
        url = root + "/compare/" + main_sha + "..." + sha + "?per_page=100&page=1"
        compared = bounded_get(url, token)
        if (not isinstance(compared, dict)
                or compared.get("base_commit", {}).get("sha") != main_sha
                or compared.get("head_commit", {}).get("sha") != sha):
            raise ValueError("compare changed while sampling; do not claim ownership")
        ahead = compared.get("ahead_by")
        if type(ahead) is not int or ahead < 0:
            raise ValueError("invalid ahead-of-main evidence")
        if ahead == 0:
            not_ahead += 1
            continue
        files = compared.get("files")
        # GitHub compare returns at most 300 filenames on the first page.
        # Never call the first page a complete scope at that ceiling.
        if (not isinstance(files, list) or len(files) >= 300
                or type(compared.get("total_commits")) is not int
                or compared["total_commits"] > 100):
            incomplete_paths += 1
            coverage_complete = False
            continue
        paths = set()
        for entry in files:
            if not isinstance(entry, dict):
                raise ValueError("invalid GitHub changed file record")
            paths.add(bounded_path(entry.get("filename")))
            if entry.get("status") == "renamed":
                paths.add(bounded_path(entry.get("previous_filename")))
        if len(paths) > MAX_FILES:
            incomplete_paths += 1
            coverage_complete = False
            continue
        changed = sorted(paths)
        collide = [p for p in changed if any(overlaps(p, own) for own in current_paths)]
        recent.append({
            "branch": name, "head_sha": sha, "main_sha": main_sha,
            "commit_age_hours": age_hours, "ahead_by": ahead,
            "paths": changed, "path_count": len(changed),
            "overlapping_paths": collide[:MAX_FILES],
            "potential_overlap": bool(collide),
            "owner_verified": False, "claim_authorized": False,
        })
    return {
        "schema": "zsentinel-recent-unpr-branches-v1",
        "source": "github_exact_readonly_commit_compare",
        "repository": repo, "main_sha": main_sha,
        "observed_at": instant.isoformat().replace("+00:00", "Z"),
        "recent_window_hours": RECENT_HOURS,
        "coverage": "bounded_complete" if coverage_complete else "incomplete_owner_review",
        "sampled_unpr_branches": len(candidates),
        "historical_in_sample": historical,
        "not_ahead_in_sample": not_ahead,
        "incomplete_path_samples": incomplete_paths,
        "current_paths_known": bool(current_paths),
        "current_slice_safe_to_claim": False,
        "recent_candidate_count": len(recent),
        "recent_candidates": recent,
        "potential_overlap_observed": any(x["potential_overlap"] for x in recent),
        "branches_deleted": False, "worker_claims_changed": False,
        "workflows_retried": False,
    }


def main():
    parser = argparse.ArgumentParser(description="Read-only bounded un-PR branch collision candidates")
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--project", required=True)
    parser.add_argument("--branch-prefix", help="optional bounded branch ref prefix")
    parser.add_argument("--current-path", action="append", default=[],
                        help="current proposed project-relative scope, repeat up to 25")
    args = parser.parse_args()
    try:
        projects = canonical_projects(args.registry)
        if args.project not in projects:
            raise ValueError("project absent from canonical registry")
        print(json.dumps(inventory(projects[args.project], os.environ.get("GITHUB_TOKEN"),
                                   args.branch_prefix, args.current_path),
                         sort_keys=True, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, "zSentinel branch ownership evidence unavailable: " +
                    str(exc)[:140] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
