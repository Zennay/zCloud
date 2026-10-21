#!/usr/bin/env python3
"""Read-only exact-head GitHub PR delivery evidence for zSentinel operator use.

Never approves, requests review, merges, dispatches CI or mutates GitHub.
A mergeable flag, successful checks, or a review are NOT a release authorization.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from zsentinel_product_delivery_board import write_create_only

API = "https://api.github.com"
TARGET = re.compile(r"^Zennay/([A-Za-z0-9_.-]{1,80})#([1-9][0-9]{0,7})$")
SHA = re.compile(r"^[a-f0-9]{40}$")
MAX_TARGETS = 5
MAX_REQUESTS = 4 * MAX_TARGETS
MAX_RESPONSE = 512 * 1024


def unique(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("ambiguous GitHub JSON key")
        data[key] = value
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("untrusted GitHub redirect")


def github(path, token):
    if not path.startswith("/repos/Zennay/") or "://" in path:
        raise ValueError("invalid API path")
    req = urllib.request.Request(
        API + path,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "zsentinel-source-pr-admission-v1",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": "Bearer " + token} if token else {}),
        },
        method="GET",
    )
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=8) as response:
            if response.status != 200:
                raise ValueError("GitHub evidence request was not successful")
            if response.headers.get("Link", "") and 'rel="next"' in response.headers["Link"]:
                raise ValueError("GitHub pagination incomplete; refuse inferred approval")
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ValueError("GitHub response over size budget")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 429):
            raise ValueError("GitHub auth/rate/billing admission denied") from None
        if exc.code == 404:
            raise ValueError("GitHub PR/evidence unavailable or access denied") from None
        raise ValueError("GitHub returned HTTP " + str(exc.code)) from None
    except urllib.error.URLError:
        raise ValueError("GitHub network evidence unavailable") from None
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              ValueError("non-finite evidence")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed GitHub JSON evidence") from exc


def sha(value):
    if type(value) is not str or not SHA.fullmatch(value):
        raise ValueError("GitHub returned invalid commit SHA")
    return value


def status_checks(repo, head, token):
    escaped = urllib.parse.quote(repo, safe="/")
    checks = github("/repos/" + escaped + "/commits/" + head + "/check-runs?per_page=100", token)
    combined = github("/repos/" + escaped + "/commits/" + head + "/status?per_page=100", token)
    if type(checks) is not dict or type(combined) is not dict:
        raise ValueError("invalid GitHub status payload")
    if sha(combined.get("sha")) != head:
        raise ValueError("combined status has another head")
    runs = checks.get("check_runs")
    total = checks.get("total_count")
    if (type(runs) is not list or len(runs) >= 100 or
            type(total) is not int or total != len(runs)):
        raise ValueError("incomplete check-run inventory")
    statuses = combined.get("statuses")
    if type(statuses) is not list or len(statuses) >= 100:
        raise ValueError("incomplete legacy status inventory")
    dedup = set()
    run_state = []
    for run in runs:
        if type(run) is not dict or sha(run.get("head_sha")) != head:
            raise ValueError("check run belongs to another head")
        key = run.get("id")
        name = run.get("name")
        if (type(key) is not int or key <= 0 or key in dedup or
                type(name) is not str or not 0 < len(name) <= 160):
            raise ValueError("ambiguous check run identity")
        dedup.add(key)
        status = run.get("status")
        conclusion = run.get("conclusion")
        if status not in ("queued", "in_progress", "completed", "pending", "waiting", "requested"):
            raise ValueError("unknown check lifecycle")
        if status == "completed":
            if conclusion not in ("success", "failure", "neutral", "cancelled",
                                  "timed_out", "skipped", "action_required", "stale"):
                raise ValueError("unknown check conclusion")
        elif conclusion is not None:
            raise ValueError("pending check has unexpected conclusion")
        # CI run names are arbitrary remote free text. Keep numeric/state
        # evidence only, never export unbounded operator-visible job labels.
        run_state.append({"state": status, "conclusion": conclusion})
    state = combined.get("state")
    if state not in ("pending", "failure", "success", "error"):
        raise ValueError("unknown combined status state")
    if any(type(s) is not dict or s.get("state") not in (
            "success", "failure", "error", "pending") for s in statuses):
        raise ValueError("unknown legacy commit context")
    pending = [x for x in run_state if x["state"] != "completed"]
    bad = [x for x in run_state if x["state"] == "completed" and x["conclusion"] != "success"]
    observed_run_success = bool(run_state) and not pending and not bad
    # GitHub's combined status can remain pending when no legacy contexts
    # exist. Report native check success independently; neither value
    # attests the repository's required branch protection rules.
    return {
        "exact_head_sha": head,
        "run_count": len(run_state),
        "pending_runs": len(pending),
        "non_success_runs": len(bad),
        "completed_success_runs": sum(
            x["state"] == "completed" and x["conclusion"] == "success"
            for x in run_state
        ),
        "legacy_context_count": len(statuses),
        "legacy_combined_state": state,
        "observed_check_runs_success": observed_run_success,
        "observed_legacy_contexts_success": (
            state == "success" if statuses else None),
        "branch_required_check_rules_verified": False,
    }


def reviews_for(repo, number, author, token):
    escaped = urllib.parse.quote(repo, safe="/")
    reviews = github("/repos/" + escaped + "/pulls/" + str(number) + "/reviews?per_page=100", token)
    if type(reviews) is not list or len(reviews) >= 100:
        raise ValueError("incomplete PR review inventory")
    latest = {}
    for review in reviews:
        if type(review) is not dict or type(review.get("user")) is not dict:
            raise ValueError("invalid review evidence")
        actor = review["user"].get("login")
        state = review.get("state")
        rid = review.get("id")
        if type(actor) is not str or not 0 < len(actor) <= 120 or type(rid) is not int:
            raise ValueError("invalid review author or ID")
        if state not in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"):
            raise ValueError("unknown review state")
        if actor == author or actor.endswith("[bot]"):
            continue
        # API returns reviews chronologically. A newer COMMENTED submission
        # does not withdraw earlier APPROVED/CHANGES_REQUESTED decisions.
        if state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[actor] = state
    return {
        "observed_distinct_non_author_approvals":
            sum(x == "APPROVED" for x in latest.values()),
        "observed_changes_requested":
            sum(x == "CHANGES_REQUESTED" for x in latest.values()),
        "review_freshness_against_latest_push_verified": False,
        "codeowner_approval_verified": False,
    }


def fetch_target(target, token):
    match = TARGET.fullmatch(target)
    if not match:
        raise ValueError("PR target must be explicit Zennay/<repo>#<number>")
    repo = "Zennay/" + match.group(1)
    number = int(match.group(2))
    escaped = urllib.parse.quote(repo, safe="/")
    pr = github("/repos/" + escaped + "/pulls/" + str(number), token)
    if type(pr) is not dict or type(pr.get("head")) is not dict or type(pr.get("base")) is not dict:
        raise ValueError("invalid PR evidence")
    if (pr.get("number") != number or type(pr.get("user")) is not dict
            or type(pr["head"].get("repo")) is not dict):
        raise ValueError("PR identity mismatch")
    author = pr["user"].get("login")
    if type(author) is not str or not author:
        raise ValueError("PR author not observable")
    head = sha(pr["head"].get("sha"))
    base = sha(pr["base"].get("sha"))
    if pr["head"]["repo"].get("full_name") != repo:
        raise ValueError("cross-repository PR heads excluded from this operator command")
    if pr["base"].get("ref") != "main":
        raise ValueError("only canonical main-based PRs accepted")
    if pr.get("state") not in ("open", "closed") or type(pr.get("draft")) is not bool:
        raise ValueError("unknown PR lifecycle")
    checks = status_checks(repo, head, token)
    reviews = reviews_for(repo, number, author, token)
    reasons = []
    if pr["state"] != "open":
        reasons.append("pull_request_not_open")
    if pr["draft"]:
        reasons.append("draft_pr")
    if pr.get("mergeable") is not True:
        reasons.append("mergeability_unverified_or_conflicted")
    if not checks["observed_check_runs_success"]:
        reasons.append("check_runs_pending_or_unsuccessful")
    if checks["observed_legacy_contexts_success"] is False:
        reasons.append("legacy_commit_context_not_successful")
    if checks["observed_legacy_contexts_success"] is None:
        reasons.append("legacy_context_requirement_unknown")
    if reviews["observed_distinct_non_author_approvals"] == 0:
        reasons.append("no_independent_approval_observed")
    if reviews["observed_changes_requested"]:
        reasons.append("changes_requested_observed")
    reasons.extend(("required_branch_policy_not_attested",
                    "codeowner_and_review_freshness_not_attested",
                    "authorized_release_owner_not_attested"))
    return {
        "repository": repo, "number": number,
        "url": "https://github.com/" + repo + "/pull/" + str(number),
        "head_sha": head, "base_sha": base,
        "state": pr["state"], "draft": pr["draft"],
        "mergeable_observed": pr.get("mergeable") is True,
        "checks": checks, "reviews": reviews,
        "blocking_unknowns": sorted(set(reasons)),
        "merge_authorized": False, "deploy_authorized": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", required=True,
                        help="Zennay/<repository>#<PR-number>; maximum five")
    parser.add_argument("--output", type=os.fspath,
                        help="Create-only local JSON evidence file")
    args = parser.parse_args(argv)
    if not 1 <= len(args.target) <= MAX_TARGETS or len(set(args.target)) != len(args.target):
        parser.error("provide one to five distinct PR targets")
    try:
        token = os.environ.get("GITHUB_TOKEN", "")
        if token and ("\r" in token or "\n" in token or len(token) > 1024):
            raise ValueError("invalid token envelope")
        candidates = [fetch_target(item, token) for item in args.target]
        report = {
            "schema": "zsentinel-pr-delivery-observation-v1",
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "github_readonly_exact_head",
            "request_budget": 4 * len(candidates),
            "targets": candidates,
            "inventory_complete_for_requested_targets": True,
            "required_branch_protection_verified": False,
            "active_claims_verified": False,
            "merge_authorized": False,
            "deploy_authorized": False,
            "operator_note": "Bounded read-only GitHub evidence only. Observed checks, "
                "mergeability and approvals cannot replace mandatory branch protection, "
                "independent code-owner review, current queue claims or release authority.",
        }
        rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            from pathlib import Path
            write_create_only(Path(args.output), rendered)
        else:
            print(rendered)
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print("zSentinel PR evidence denied: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
