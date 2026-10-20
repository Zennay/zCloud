#!/usr/bin/env python3
"""Release-first GitHub PR sweep for zCloud workers.

Read-only by default. --merge requires an existing live zCloud mainwriter claim,
an independently approved exact-head PR and passing GitHub checks. GitHub itself
remains the final enforcement point for branch rules. Never deploys.
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "history.db"

PROJECTS = ROOT / "projects.json"
REPO_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+?)/?$")


def repo_is_registered_for_project(project, repo):
    """Reject cross-repository use of a real mainwriter claim.

    A live claim for Supa does not grant release rights to a different repo.
    Fail closed on absent, ambiguous or malformed canonical registry entries.
    """
    try:
        records = json.loads(PROJECTS.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            return False
        matches = [item for item in records
                   if isinstance(item, dict) and item.get("id") == project]
        if len(matches) != 1:
            return False
        url = matches[0].get("repo_url")
        match = REPO_URL.fullmatch(url) if isinstance(url, str) else None
        if not match:
            return False
        actual = match.group(1) + "/" + match.group(2)
        return actual.casefold() == repo.casefold()
    except (OSError, ValueError, TypeError, UnicodeError):
        return False


def gh(path, method="GET", fields=None):
    argv = ["gh", "api", "-H", "Accept: application/vnd.github+json"]
    if method != "GET":
        argv.extend(["-X", method])
    for key, value in (fields or {}).items():
        argv.extend(["-f", f"{key}={value}"])
    argv.append(path)
    result = subprocess.run(argv, text=True, capture_output=True, timeout=30,
                            env={**os.environ, "GH_PROMPT_DISABLED": "1"})
    if result.returncode:
        raise RuntimeError(f"GitHub {method} request failed for {path}: exit {result.returncode}")
    return json.loads(result.stdout)

def approvals(repo, number, author, exact_head):
    """Only independently approved exact-source reviews; ambiguity denies."""
    if not isinstance(author, str):
        return False
    records = gh(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
    # A truncated review history is not evidence that older changes requests
    # have been resolved. Fail closed rather than guessing review chronology.
    if not isinstance(records, list) or len(records) >= 100:
        return False
    latest = {}
    for review in records:
        if not isinstance(review, dict):
            return False
        user = review.get("user") or {}
        if not isinstance(user, dict):
            return False
        login = user.get("login", "")
        if not isinstance(login, str) or not login:
            continue
        if login.casefold() == author.casefold() or user.get("type") == "Bot":
            continue
        state = review.get("state")
        if state in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[login.casefold()] = (
                state, review.get("commit_id"),
            )
    if any(state == "CHANGES_REQUESTED" for state, _ in latest.values()):
        return False
    return any(state == "APPROVED" and sha == exact_head
               for state, sha in latest.values())


def summarize_required_workflows(workflow_runs, sha):
    """Read-only relevant workflow status, without treating all events as gates.

    A manual workflow_dispatch or historical scheduled run is not a mandatory
    PR gate. Re-run/superseded attempts for the *same* workflow/event are not
    independent blockers, either. GitHub branch protection remains authoritative.
    """
    if not isinstance(workflow_runs, list):
        return "CI_SOURCE_UNKNOWN"
    applicable = {}
    for run in workflow_runs:
        if not isinstance(run, dict) or run.get("head_sha") != sha:
            return "CI_SOURCE_UNKNOWN"
        event = run.get("event")
        if event not in {
            "pull_request", "pull_request_target", "push",
            "merge_group", "workflow_run"
        }:
            continue
        workflow = run.get("workflow_id")
        run_number = run.get("run_number")
        attempt = run.get("run_attempt", 1)
        run_id = run.get("id")
        if any(type(n) is not int or n < 1
               for n in (workflow, run_number, attempt, run_id)):
            return "CI_SOURCE_UNKNOWN"
        key = (workflow, event, run.get("head_branch"))
        sort_key = (run_number, attempt, run_id)
        if key not in applicable or sort_key > applicable[key][0]:
            applicable[key] = (sort_key, run)
    if not applicable:
        return "NO_PR_WORKFLOW_EVIDENCE"
    current = [record for _, record in applicable.values()]
    if any(r.get("status") != "completed" for r in current):
        return "HEAD_WORKFLOWS_NOT_FINISHED"
    # An entirely skipped workflow need not block a separately successful
    # mandatory workflow, but skipped-only is not CI proof.
    allowed = {"success", "skipped", "neutral"}
    if any(r.get("conclusion") not in allowed for r in current):
        return "HEAD_WORKFLOWS_NOT_GREEN"
    if not any(r.get("conclusion") == "success" for r in current):
        return "NO_POSITIVE_PR_WORKFLOW_EVIDENCE"
    return "GREEN"


def check_state(repo, sha):
    """Conservatively verify exact-head checks AND associated workflow receipts.

    A completed subset of job check-runs can precede registration of other
    queued workflows. Never treat that partial view as mandatory CI success.
    """
    checks = gh(f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
    legacy = gh(f"repos/{repo}/commits/{sha}/status?per_page=100")
    workflows = gh(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100")
    if not all(isinstance(item, dict) for item in (checks, legacy, workflows)):
        return "CI_SOURCE_UNKNOWN"
    runs = checks.get("check_runs")
    statuses = legacy.get("statuses")
    workflow_runs = workflows.get("workflow_runs")
    total_checks = checks.get("total_count")
    total_workflows = workflows.get("total_count")
    if (not isinstance(runs, list) or not isinstance(statuses, list)
            or not isinstance(workflow_runs, list)
            or type(total_checks) is not int or type(total_workflows) is not int):
        return "CI_SOURCE_UNKNOWN"
    if (len(runs) != total_checks or len(workflow_runs) != total_workflows
            or len(runs) >= 100 or len(statuses) >= 100
            or len(workflow_runs) >= 100):
        return "CI_SOURCE_TRUNCATED"
    if (not all(isinstance(record, dict) for record in runs + statuses + workflow_runs)
            or any(record.get("head_sha") != sha for record in workflow_runs)):
        return "CI_SOURCE_UNKNOWN"
    # This repo's existing CI consists of GitHub workflow receipts. A lonely
    # successful optional check is insufficient proof when workflows are absent.
    if not runs and not statuses and not workflow_runs:
        return "NO_CI"
    workflow_gate = summarize_required_workflows(workflow_runs, sha)
    if workflow_gate != "GREEN":
        return workflow_gate
    # GitHub accepts skipped/neutral individual checks for optional jobs.
    # One successful actual workflow has already been required above.
    if any(r.get("status") != "completed"
           or r.get("conclusion") not in {"success", "skipped", "neutral"}
           for r in runs):
        return "CHECKS_NOT_GREEN"
    if statuses and legacy.get("state") != "success":
        return "LEGACY_STATUS_NOT_GREEN"
    return "GREEN"

def verify_default_contains_merge(repo, merged_sha, observed_head):
    """Corroborate a reported merge commit against the actual default ref.

    The branch may legitimately have advanced after GitHub accepted the merge.
    An unrelated head or an unavailable compare endpoint must be UNKNOWN, not
    reported as delivered on main. This makes no writes or claim changes.
    """
    if not (isinstance(merged_sha, str) and isinstance(observed_head, str)
            and re.fullmatch(r"[0-9a-f]{40}", merged_sha)
            and re.fullmatch(r"[0-9a-f]{40}", observed_head)):
        return False
    if merged_sha == observed_head:
        return True
    comparison = gh(f"repos/{repo}/compare/{merged_sha}...{observed_head}")
    if not isinstance(comparison, dict):
        return False
    base = comparison.get("base_commit")
    ahead = comparison.get("ahead_by")
    behind = comparison.get("behind_by")
    return (comparison.get("status") == "ahead" and isinstance(base, dict)
            and base.get("sha") == merged_sha
            and isinstance(ahead, int) and ahead > 0
            and behind == 0)


def live_claim(project, claim_key, owner, head_sha):
    """Read-only snapshot admission: exactly one valid exclusive mainwriter.

    zCloud can have several independent worker claims for one project. Those
    claims are not an invitation to take ownership. A *second live mainwriter*
    capability for the same canonical project, or ambiguous active metadata,
    makes the merge unsafe regardless of the requested claim's own validity.
    """
    if not owner or not claim_key or not isinstance(head_sha, str):
        return False
    try:
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        try:
            db.execute("BEGIN")
            rows = db.execute(
                "SELECT claim_key, owner_id, lease_until, metadata_json "
                "FROM task_claims WHERE project_id=? LIMIT 1001", (project,)
            ).fetchall()
            db.rollback()
        finally:
            db.close()
        if len(rows) >= 1001:
            return False  # incomplete owner inventory is not merge authority
        now = dt.datetime.now(dt.timezone.utc)
        wanted = None
        for row in rows:
            lease = row["lease_until"]
            if not isinstance(lease, str):
                return False
            expiration = dt.datetime.fromisoformat(lease.replace("Z", "+00:00"))
            if expiration.tzinfo is None:
                expiration = expiration.replace(tzinfo=dt.timezone.utc)
            if expiration <= now:
                continue
            meta = json.loads(row["metadata_json"] or "{}")
            if not isinstance(meta, dict):
                return False
            scope = meta.get("conflict_scope")
            if scope is None and row["claim_key"] != claim_key:
                # Ordinary independent task leases with no writer capability
                # are not exclusive mainwriter competitors.
                continue
            if not isinstance(scope, dict):
                return False
            capabilities = scope.get("capabilities")
            if (not isinstance(capabilities, list)
                    or not all(isinstance(cap, str) for cap in capabilities)):
                return False
            is_writer = any(cap in {"mainwriter", "main-merge"} for cap in capabilities)
            if row["claim_key"] == claim_key:
                if wanted is not None:
                    return False
                wanted = (row, meta, is_writer)
            elif is_writer:
                # Different lease with a live writer capability => conflicting.
                # Never displace the other worker or assume their work is stale.
                return False
        if wanted is None:
            return False
        row, meta, is_writer = wanted
        return bool(row["owner_id"] == owner and is_writer
                    and meta.get("sha") == head_sha)
    except (OSError, ValueError, sqlite3.Error, TypeError, KeyError, AttributeError):
        return False

def sweep(repo, project, args):
    if args.merge and not repo_is_registered_for_project(project, repo):
        raise ValueError("project/repository mainwriter scope mismatch")
    info = gh(f"repos/{repo}")
    default = info["default_branch"]
    if default != "main":
        print(json.dumps({"repo": repo, "result": "SKIPPED_NON_MAIN", "default": default}))
        return
    # A selected PR must never be missed just because the repository has >100
    # older open pull requests. Without --pr, report the newest 100 explicitly.
    if args.pr:
        prs = [gh(f"repos/{repo}/pulls/{number}") for number in sorted(set(args.pr))]
    else:
        prs = gh(f"repos/{repo}/pulls?state=open&per_page=100&sort=updated&direction=desc")
        if not isinstance(prs, list):
            raise ValueError("incomplete PR list")
        if len(prs) == 100:
            print(json.dumps({"repo": repo, "status": "SOURCE_WINDOW_100_PARTIAL",
                              "note": "older PRs may be absent; supply --pr to inspect them"}))
        if len(prs) > args.max_prs:
            print(json.dumps({"repo": repo, "status": "RECENT_PR_LIMIT_PARTIAL",
                              "observed": len(prs), "selected": args.max_prs,
                              "note": "only the newest PRs were inspected; supply --pr for others"}))
        prs = prs[:args.max_prs]
    for item in prs:
        number = item["number"]
        if args.pr and number not in args.pr:
            continue
        head = item["head"]["sha"]
        result = {"repo": repo, "pr": number, "head": head[:12],
                  "base": item["base"]["ref"], "merged": False}
        if item.get("state") != "open":
            result["merged"] = bool(item.get("merged_at"))
            result["status"] = "ALREADY_MERGED" if result["merged"] else "CLOSED"
            if result["merged"]:
                result["main_commit"] = item.get("merge_commit_sha")
        elif item["base"]["ref"] != default:
            result["status"] = "CHILD_PR_REQUIRES_PARENT_INTEGRATION"
        elif item["draft"]:
            result["status"] = "DRAFT"
        else:
            current = gh(f"repos/{repo}/pulls/{number}")
            if current["head"]["sha"] != head or current.get("mergeable") is not True:
                result["status"] = "CHANGED_OR_CONFLICTED"
            elif not approvals(repo, number, (current.get("user") or {}).get("login", ""), head):
                result["status"] = "INDEPENDENT_REVIEW_REQUIRED"
            else:
                result["status"] = check_state(repo, head)
                if result["status"] == "GREEN":
                    # Scope claim is checked again immediately before a requested write.
                    if args.merge and live_claim(project, args.claim_key, args.owner_id, head):
                        # All mutable GitHub inputs are refreshed before the
                        # explicit one-PR write, not borrowed from first read.
                        before = gh(f"repos/{repo}/pulls/{number}")
                        if (before["head"]["sha"] != head
                                or before["base"]["ref"] != default
                                or before.get("draft") is True
                                or before.get("mergeable") is not True):
                            result["status"] = "HEAD_BASE_OR_GATE_MOVED"
                        elif not approvals(
                            repo, number, (before.get("user") or {}).get("login", ""), head
                        ):
                            result["status"] = "REVIEW_CHANGED_BEFORE_MERGE"
                        elif check_state(repo, head) != "GREEN":
                            result["status"] = "CHECKS_CHANGED_BEFORE_MERGE"
                        elif not live_claim(project, args.claim_key, args.owner_id, head):
                            result["status"] = "MAINWRITER_LEASE_CHANGED"
                        else:
                            outcome = gh(f"repos/{repo}/pulls/{number}/merge", "PUT",
                                         {"sha": head, "merge_method": "squash"})
                            if outcome.get("merged") is not True or not outcome.get("sha"):
                                result["status"] = "MERGE_REJECTED"
                            else:
                                # A 200 response does not by itself prove a
                                # durable merged PR or the current default ref.
                                result["status"] = "MERGE_ACCEPTED_POSTCHECK_UNKNOWN"
                                result["reported_commit"] = outcome["sha"]
                                try:
                                    after = gh(f"repos/{repo}/pulls/{number}")
                                    default_ref = gh(f"repos/{repo}/git/ref/heads/{default}")
                                    observed_head = (default_ref.get("object") or {}).get("sha")
                                    result["default_branch_head_observed"] = observed_head
                                    if (after.get("merged_at")
                                            and after.get("merged") is True
                                            and after.get("merge_commit_sha") == outcome["sha"]
                                            and isinstance(after.get("base"), dict)
                                            and after["base"].get("ref") == default
                                            and isinstance(after.get("head"), dict)
                                            and after["head"].get("sha") == head
                                            and verify_default_contains_merge(
                                                repo, outcome["sha"], observed_head)):
                                        result["merged"] = True
                                        result["main_commit"] = outcome["sha"]
                                        result["status"] = "MERGED"
                                except (RuntimeError, ValueError, KeyError, TypeError,
                                        subprocess.TimeoutExpired):
                                    # Never retry the PUT automatically: it may
                                    # already have merged. Require manual review.
                                    pass
                    elif args.merge:
                        result["status"] = "MAINWRITER_LEASE_REQUIRED"
                    else:
                        result["status"] = "READY_PENDING_EXPLICIT_MERGE_WITH_LEASE"
        print(json.dumps(result, ensure_ascii=False))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", action="append", required=True, metavar="OWNER/REPO")
    parser.add_argument("--project", required=True, help="zCloud project_id for the mainwriter lease")
    parser.add_argument("--pr", action="append", type=int, help="inspect only selected PR numbers")
    parser.add_argument("--max-prs", type=int, default=12,
                        help="bounded newest-PR read-only inventory, 1..24 (default 12); explicit --pr unaffected")
    parser.add_argument("--merge", action="store_true", help="execute only with a real exclusive lease")
    parser.add_argument("--claim-key", default="")
    parser.add_argument("--owner-id", default="")
    args = parser.parse_args()
    if args.max_prs < 1 or args.max_prs > 24:
        parser.error("--max-prs must be between 1 and 24")
    if args.merge and (len(args.repo) != 1 or len(args.pr or []) != 1
                       or not args.owner_id or not args.claim_key):
        parser.error("--merge needs one --repo, one explicit --pr, --owner-id and --claim-key")
    for repo in args.repo:
        if repo.count("/") != 1 or any(not p.replace("-", "").replace("_", "").isalnum()
                                       for p in repo.split("/")):
            parser.error(f"invalid repo name: {repo}")
        try:
            sweep(repo, args.project, args)
        except (RuntimeError, KeyError, TypeError, ValueError, subprocess.TimeoutExpired) as exc:
            print(json.dumps({"repo": repo, "status": "INSPECTION_FAILED",
                              "kind": type(exc).__name__}), file=sys.stderr)
            if args.merge:
                raise SystemExit(2) from exc

if __name__ == "__main__":
    main()
