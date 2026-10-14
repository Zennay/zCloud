#!/usr/bin/env python3
"""Opt-in GitHub-hosted PR integration engine for zCloud development.

No VPS access, runner restart, queue write, secrets output or bypass of GitHub
branch protections. An explicit label is a per-PR authorization, not a blanket
instruction to merge every open branch. Default mode is dry-run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = "Zennay/zCloud"
LABEL = "zcloud-automerge-ready"
SHA = re.compile(r"^[a-f0-9]{40}$")
HIGH_RISK = (
    ".github/", "public/", "firefox-extension/", "server.py",
    "projects.json", "project-contracts.json", "lane_generator.py",
    "deploy/", "worker/", "config/", "systemd/",
)
ALLOWED_PREFIXES = ("docs/", "tests/", "scripts/")
ALLOWED_CHECK_CONCLUSIONS = {"success", "neutral", "skipped"}


class Refused(RuntimeError):
    pass


def valid_sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def safe_path(path):
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        return False
    components = path.split("/")
    if any(c in ("", ".", "..") for c in components):
        return False
    if path.startswith(HIGH_RISK) or path in HIGH_RISK:
        return False
    return path.startswith(ALLOWED_PREFIXES)


def has_label(pr):
    return any(isinstance(item, dict) and item.get("name") == LABEL
               for item in pr.get("labels", []))


def decide(pr, files, checks, statuses, main_sha):
    """Pure fail-closed admission. Return reason, never permission from text."""
    if not isinstance(pr, dict) or not valid_sha(main_sha):
        return "invalid_input"
    if not has_label(pr):
        return "no_explicit_label"
    if pr.get("state") != "open" or pr.get("draft") is not False:
        return "not_review_ready"
    if pr.get("mergeable") is not True or pr.get("mergeable_state") not in ("clean", "unstable"):
        return "not_cleanly_mergeable"
    head, base = pr.get("head"), pr.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        return "missing_refs"
    head_sha = head.get("sha")
    if not valid_sha(head_sha) or not valid_sha(base.get("sha")):
        return "invalid_sha"
    if base.get("sha") != main_sha or base.get("ref") != "main":
        return "base_drift"
    head_repo = (head.get("repo") or {}).get("full_name")
    base_repo = (base.get("repo") or {}).get("full_name")
    if head_repo != REPO or base_repo != REPO or head_sha == main_sha:
        return "foreign_or_empty_head"
    if not isinstance(files, list) or not (1 <= len(files) < 100):
        return "incomplete_file_inventory"
    filenames = set()
    for f in files:
        if not isinstance(f, dict) or f.get("status") not in ("added", "modified"):
            return "unsupported_file_change"
        name = f.get("filename")
        if not safe_path(name) or name in filenames:
            return "unsafe_or_duplicate_path"
        filenames.add(name)
    if not isinstance(checks, list) or not (1 <= len(checks) < 100):
        return "missing_or_unbounded_checks"
    if not isinstance(statuses, list) or len(statuses) >= 100:
        return "unbounded_statuses"
    smoke_green = False
    optional_cpu_pending = False
    for c in checks:
        if not isinstance(c, dict) or c.get("head_sha") != head_sha:
            return "unbound_check"
        name = c.get("name")
        if name == "critical-runner-flows":
            if c.get("status") != "completed" or c.get("conclusion") != "success":
                return "required_smoke_not_green"
            smoke_green = True
        elif name == "cpu-snapshot" and c.get("status") in ("queued", "in_progress", "pending") and c.get("conclusion") is None:
            # This optional VPS diagnostic can queue indefinitely when the
            # worker listener is absent. It is NOT the hosted regression gate.
            optional_cpu_pending = True
            continue
        elif c.get("status") != "completed" or c.get("conclusion") not in ALLOWED_CHECK_CONCLUSIONS:
            return "check_not_green"
    if not smoke_green:
        return "required_smoke_missing"
    # GitHub reports unstable when ANY status check is pending; the single
    # recognized optional VPS CPU snapshot may be queued indefinitely.
    # Never relax other required/unknown checks or a blocked branch-protection
    # state: the GitHub merge endpoint enforces protections again.
    if pr.get("mergeable_state") == "unstable" and not optional_cpu_pending:
        return "unstable_without_known_optional"
    for s in statuses:
        if not isinstance(s, dict) or s.get("state") != "success":
            return "status_not_green"
    # Source integration is independent of the VPS deployment health status.
    # Privileged/runtime paths are excluded above; a merge is never permission
    # to restart a worker, alter SQLite or promote production.
    return "eligible"


def api(token, method, path, data=None):
    url = "https://api.github.com/repos/" + REPO + path
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "zcloud-dev-automerge/1",
               "Authorization": "Bearer " + token,
               "X-GitHub-Api-Version": "2022-11-28"}
    payload = json.dumps(data, separators=(",", ":")).encode() if data is not None else None
    req = Request(url, data=payload, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise Refused("api_response_oversize")
            return json.loads(body)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise Refused("github_api_request_failed") from exc



def list_open_prs(transport, token, max_pages=10):
    """Use bounded GitHub pages; never silently ignore older opted-in PRs."""
    seen = set()
    prs = []
    for page in range(1, max_pages + 1):
        batch = transport(token, "GET",
                          f"/pulls?state=open&sort=updated&direction=desc&per_page=100&page={page}")
        if not isinstance(batch, list) or len(batch) > 100:
            raise Refused("invalid_pr_page")
        for pr in batch:
            number = pr.get("number") if isinstance(pr, dict) else None
            if type(number) is not int or not 1 <= number <= 10000000 or number in seen:
                raise Refused("ambiguous_pr_page")
            seen.add(number)
            prs.append(pr)
        if len(batch) < 100:
            return prs
    raise Refused("pr_inventory_exceeds_bounded_budget")


def main(argv=None, environ=None, transport=api):
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Merge exactly one eligible labelled PR")
    options = parser.parse_args(argv)
    if env.get("GITHUB_REPOSITORY") != REPO or env.get("GITHUB_REF") != "refs/heads/main":
        raise Refused("not_canonical_main")
    if env.get("GITHUB_EVENT_NAME") not in ("schedule", "workflow_dispatch"):
        raise Refused("untrusted_event")
    if options.apply and env.get("ZCLOUD_AUTOMERGE_ENABLED") != "true":
        raise Refused("explicit_feature_gate_disabled")
    if options.apply and (env.get("GITHUB_ACTOR") != "Zennay"
                          or env.get("GITHUB_TRIGGERING_ACTOR") != "Zennay"):
        raise Refused("owner_only_apply")
    token = env.get("GITHUB_TOKEN", "")
    if not token or any(ord(c) < 33 or ord(c) > 126 for c in token) or len(token) > 1024:
        raise Refused("missing_or_invalid_token")
    checked_sha = env.get("GITHUB_SHA")
    if not valid_sha(checked_sha):
        raise Refused("invalid_trigger_sha")
    branch = transport(token, "GET", "/branches/main")
    main_sha = (branch.get("commit") or {}).get("sha")
    if main_sha != checked_sha:
        raise Refused("workflow_source_is_stale")
    prs = list_open_prs(transport, token)
    receipts = []
    for p in prs:
        if not isinstance(p, dict) or not has_label(p):
            continue
        number = p.get("number")
        if type(number) is not int or not 1 <= number <= 10000000:
            raise Refused("invalid_pr_number")
        pr = transport(token, "GET", f"/pulls/{number}")
        head_sha = (pr.get("head") or {}).get("sha")
        if not valid_sha(head_sha):
            receipts.append({"pr": number, "decision": "invalid_sha"})
            continue
        files = transport(token, "GET", f"/pulls/{number}/files?per_page=100")
        checks = transport(token, "GET", f"/commits/{head_sha}/check-runs?per_page=100")
        statuses = transport(token, "GET", f"/commits/{head_sha}/statuses?per_page=100")
        if not isinstance(checks, dict) or checks.get("total_count") != len(checks.get("check_runs", [])):
            verdict = "incomplete_checks"
        else:
            verdict = decide(pr, files, checks["check_runs"], statuses, main_sha)
        receipts.append({"pr": number, "decision": verdict})
        if verdict != "eligible" or not options.apply:
            continue
        latest_main = (transport(token, "GET", "/branches/main").get("commit") or {}).get("sha")
        latest_pr = transport(token, "GET", f"/pulls/{number}")
        if latest_main != main_sha or (latest_pr.get("head") or {}).get("sha") != head_sha or latest_pr.get("base", {}).get("sha") != main_sha or not has_label(latest_pr):
            receipts[-1]["decision"] = "changed_before_merge"
            continue
        # Source integrations must not implicitly attempt production promotion.
        # An explicitly approved production release runs through its own gate.
        title = str(pr.get("title") or "Development integration")
        title = "".join(c if 32 <= ord(c) <= 126 and c != chr(92) else " " for c in title)[:120].strip()
        result = transport(token, "PUT", f"/pulls/{number}/merge",
                           {"sha": head_sha, "merge_method": "squash",
                            "commit_title": title + " [skip ci]"})
        if result.get("merged") is not True or not valid_sha(result.get("sha")):
            raise Refused("merge_not_confirmed")
        receipts[-1]["decision"] = "merged"
        receipts[-1]["merge_sha"] = result["sha"]
        break  # Deliberately only one merge per run; next run revalidates main.
    print(json.dumps({"mode": "apply" if options.apply else "dry_run",
                      "repo": REPO, "receipts": receipts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refused as exc:
        print(json.dumps({"state": "refused", "reason": str(exc)}))
        sys.exit(2)
