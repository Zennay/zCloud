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
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "history.db"

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

def approvals(repo, number, author):
    records = gh(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
    latest = {}
    for review in records:
        user = review.get("user") or {}
        login = user.get("login", "")
        if not login or login == author or user.get("type") == "Bot":
            continue
        if review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[login] = review["state"]
    return (any(v == "APPROVED" for v in latest.values())
            and not any(v == "CHANGES_REQUESTED" for v in latest.values()))

def check_state(repo, sha):
    checks = gh(f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
    runs = checks.get("check_runs") or []
    legacy = gh(f"repos/{repo}/commits/{sha}/status")
    statuses = legacy.get("statuses") or []
    if not runs and not statuses:
        return "NO_CI"
    if len(runs) < checks.get("total_count", len(runs)):
        return "CHECKS_TRUNCATED"
    if any(x.get("status") != "completed" or x.get("conclusion") != "success"
           for x in runs):
        return "CHECKS_NOT_GREEN"
    if legacy.get("state") not in ("success",) and statuses:
        return "LEGACY_STATUS_NOT_GREEN"
    return "GREEN"

def live_claim(project, claim_key, owner, head_sha):
    if not owner or not claim_key:
        return False
    try:
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT owner_id, lease_until, metadata_json FROM task_claims "
            "WHERE project_id=? AND claim_key=?", (project, claim_key)).fetchone()
        db.close()
        if not row or row["owner_id"] != owner:
            return False
        expiration = dt.datetime.fromisoformat(row["lease_until"].replace("Z", "+00:00"))
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=dt.timezone.utc)
        if expiration <= dt.datetime.now(dt.timezone.utc):
            return False
        meta = json.loads(row["metadata_json"] or "{}")
        scope = meta.get("conflict_scope") or {}
        capabilities = scope.get("capabilities") or []
        if "mainwriter" not in capabilities and "main-merge" not in capabilities:
            return False
        return meta.get("sha") == head_sha
    except (OSError, ValueError, sqlite3.Error, TypeError, KeyError):
        return False

def sweep(repo, project, args):
    info = gh(f"repos/{repo}")
    default = info["default_branch"]
    if default != "main":
        print(json.dumps({"repo": repo, "result": "SKIPPED_NON_MAIN", "default": default}))
        return
    prs = gh(f"repos/{repo}/pulls?state=open&per_page=100&sort=updated&direction=asc")
    for item in prs:
        number = item["number"]
        head = item["head"]["sha"]
        result = {"repo": repo, "pr": number, "head": head[:12],
                  "base": item["base"]["ref"], "merged": False}
        if item["base"]["ref"] != default:
            result["status"] = "CHILD_PR_REQUIRES_PARENT_INTEGRATION"
        elif item["draft"]:
            result["status"] = "DRAFT"
        else:
            current = gh(f"repos/{repo}/pulls/{number}")
            if current["head"]["sha"] != head or current.get("mergeable") is not True:
                result["status"] = "CHANGED_OR_CONFLICTED"
            elif not approvals(repo, number, (current.get("user") or {}).get("login", "")):
                result["status"] = "INDEPENDENT_REVIEW_REQUIRED"
            else:
                result["status"] = check_state(repo, head)
                if result["status"] == "GREEN":
                    # Scope claim is checked again immediately before a requested write.
                    if args.merge and live_claim(project, args.claim_key, args.owner_id, head):
                        before = gh(f"repos/{repo}/pulls/{number}")
                        if before["head"]["sha"] != head or before.get("mergeable") is not True:
                            result["status"] = "HEAD_MOVED"
                        else:
                            outcome = gh(f"repos/{repo}/pulls/{number}/merge", "PUT",
                                         {"sha": head, "merge_method": "squash"})
                            result["merged"] = outcome.get("merged") is True
                            result["main_commit"] = outcome.get("sha") if result["merged"] else None
                            result["status"] = "MERGED" if result["merged"] else "MERGE_REJECTED"
                    elif args.merge:
                        result["status"] = "MAINWRITER_LEASE_REQUIRED"
                    else:
                        result["status"] = "READY_PENDING_EXPLICIT_MERGE_WITH_LEASE"
        print(json.dumps(result, ensure_ascii=False))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", action="append", required=True, metavar="OWNER/REPO")
    parser.add_argument("--project", required=True, help="zCloud project_id for the mainwriter lease")
    parser.add_argument("--merge", action="store_true", help="execute only with a real exclusive lease")
    parser.add_argument("--claim-key", default="")
    parser.add_argument("--owner-id", default="")
    args = parser.parse_args()
    if args.merge and (len(args.repo) != 1 or not args.owner_id or not args.claim_key):
        parser.error("--merge needs exactly one --repo plus --owner-id and --claim-key")
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
