#!/usr/bin/env python3
"""Read-only cross-project merge-first board for zCloud operators.

Does not acquire claims, merge, trigger CI, touch SQLite or deploy. A CI green
observation is never a substitute for protected-branch requirements or review.
"""
import argparse
import datetime as dt
import html
import json
import os
from pathlib import Path
import re
import sys

from portfolio_merge_ready import PROJECTS, REPO_URL, gh

MAX_PROJECTS = 3
MAX_PRS = 12
MAX_PER_REPO = 6
MAX_GETS = 64
MAX_OUTPUT = 1024 * 1024
PR_SPEC = re.compile(r"^([A-Za-z0-9_-]+/[A-Za-z0-9_.-]+)#([1-9][0-9]{0,6})$")
ALLOWED_STATES = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}


def registry():
    records = json.loads(PROJECTS.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("invalid project registry")
    mapping = {}
    for entry in records:
        if not isinstance(entry, dict):
            continue
        match = REPO_URL.fullmatch(entry.get("repo_url", "")) if isinstance(entry.get("repo_url"), str) else None
        key = entry.get("id")
        if not match or not isinstance(key, str):
            continue
        repo = match.group(1) + "/" + match.group(2)
        if repo.casefold() in mapping:
            raise ValueError("duplicate canonical repository")
        mapping[repo.casefold()] = (repo, key)
    return mapping


def bounded_gh(path, budget):
    if budget[0] >= MAX_GETS:
        raise ValueError("github request budget exceeded")
    budget[0] += 1
    return gh(path)


def latest_approvals(reviews, author, sha):
    if not isinstance(reviews, list) or len(reviews) >= 100:
        return "UNKNOWN"
    latest = {}
    for row in reviews:
        if not isinstance(row, dict) or not isinstance(row.get("user"), dict):
            return "UNKNOWN"
        user = row["user"]
        login = user.get("login")
        if not isinstance(login, str) or not login:
            return "UNKNOWN"
        if login.casefold() == author.casefold() or user.get("type") == "Bot":
            continue
        state = row.get("state")
        if state in ALLOWED_STATES:
            latest[login.casefold()] = (state, row.get("commit_id"))
    if any(state == "CHANGES_REQUESTED" for state, _ in latest.values()):
        return "CHANGES_REQUESTED"
    if any(state == "APPROVED" and commit == sha for state, commit in latest.values()):
        return "EXACT_HEAD_APPROVED"
    return "INDEPENDENT_APPROVAL_MISSING"


def observed_checks(checks, legacy):
    if not isinstance(checks, dict) or not isinstance(legacy, dict):
        return "UNKNOWN"
    runs = checks.get("check_runs")
    statuses = legacy.get("statuses")
    total = checks.get("total_count")
    if not isinstance(runs, list) or not isinstance(statuses, list) or not isinstance(total, int):
        return "UNKNOWN"
    if total > len(runs) or len(runs) >= 100 or len(statuses) >= 100:
        return "INCOMPLETE"
    if not runs and not statuses:
        return "NO_CHECK_EVIDENCE"
    if any(not isinstance(r, dict) for r in runs) or any(not isinstance(s, dict) for s in statuses):
        return "UNKNOWN"
    if any(r.get("status") != "completed" or r.get("conclusion") != "success" for r in runs):
        return "PENDING_OR_FAILURE"
    if statuses and legacy.get("state") != "success":
        return "PENDING_OR_FAILURE"
    # This says only observed checks passed, not that required branch rules were met.
    return "OBSERVED_GREEN_ONLY"


def inspect(repo, number, budget):
    try:
        first = bounded_gh(f"repos/{repo}/pulls/{number}", budget)
        if not isinstance(first, dict) or not isinstance(first.get("head"), dict):
            raise ValueError("incomplete PR source")
        sha = first["head"].get("sha")
        base = first.get("base")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("invalid PR source commit")
        if not isinstance(base, dict) or not isinstance(base.get("ref"), str):
            raise ValueError("invalid PR base")
        title = str(first.get("title") or "")[:160]
        if first.get("state") != "open":
            return {"repo": repo, "number": number, "title": title, "head": sha,
                    "status": "CLOSED_OR_MERGED", "review": "NOT_INSPECTED",
                    "checks": "NOT_INSPECTED", "source": "COMPLETE", "actions": ["Verify default-branch receipt"]}
        if base["ref"] != "main":
            return {"repo": repo, "number": number, "title": title, "head": sha,
                    "status": "CHILD_PR", "review": "NOT_INSPECTED",
                    "checks": "NOT_INSPECTED", "source": "COMPLETE", "actions": ["Integrate parent PR first"]}
        if first.get("draft") is True:
            return {"repo": repo, "number": number, "title": title, "head": sha,
                    "status": "DRAFT", "review": "NOT_INSPECTED",
                    "checks": "NOT_INSPECTED", "source": "COMPLETE", "actions": ["Owner to mark ready when appropriate"]}
        revs = bounded_gh(f"repos/{repo}/pulls/{number}/reviews?per_page=100", budget)
        runs = bounded_gh(f"repos/{repo}/commits/{sha}/check-runs?per_page=100", budget)
        legacy = bounded_gh(f"repos/{repo}/commits/{sha}/status?per_page=100", budget)
        last = bounded_gh(f"repos/{repo}/pulls/{number}", budget)
        if (not isinstance(last, dict) or not isinstance(last.get("head"), dict)
                or last["head"].get("sha") != sha or last.get("state") != "open"
                or last.get("base", {}).get("ref") != "main"
                or last.get("draft") is True):
            return {"repo": repo, "number": number, "title": title, "head": sha,
                    "status": "SOURCE_MOVED", "review": "UNKNOWN",
                    "checks": "UNKNOWN", "source": "CHANGED", "actions": ["Refresh exact-head evidence"]}
        author = first.get("user", {}).get("login", "") if isinstance(first.get("user"), dict) else ""
        review = latest_approvals(revs, author, sha)
        check = observed_checks(runs, legacy)
        actions = []
        if last.get("mergeable") is not True:
            actions.append("Resolve conflict or wait for GitHub mergeability")
        if review != "EXACT_HEAD_APPROVED":
            actions.append("Obtain independent exact-head approval")
        if check != "OBSERVED_GREEN_ONLY":
            actions.append("Wait for or repair mandatory exact-head CI")
        # Even when all source signals look green, ownership and branch protection
        # have NOT been independently verified. Never label a PR merge-authorized.
        actions.append("Verify branch rules and existing exclusive mainwriter claim")
        return {"repo": repo, "number": number, "title": title, "head": sha,
                "status": "OBSERVED_GATES_ONLY" if len(actions) == 1 else "HOLD",
                "review": review, "checks": check, "source": "COMPLETE",
                "actions": actions}
    except (KeyError, TypeError, ValueError, RuntimeError, OSError):
        return {"repo": repo, "number": number, "title": "", "head": None,
                "status": "SOURCE_UNAVAILABLE", "review": "UNKNOWN", "checks": "UNKNOWN",
                "source": "INCOMPLETE", "actions": ["Refresh this PR independently"]}


def render(rows, observed):
    esc = lambda x: html.escape(str(x), quote=True)
    cards = []
    for row in rows:
        url = f"https://github.com/{row['repo']}/pull/{row['number']}"
        actions = "".join("<li>" + esc(act) + "</li>" for act in row["actions"])
        cards.append(
            '<article><h2><a href="' + esc(url) + '">' + esc(row["repo"]) + " #" +
            esc(row["number"]) + '</a></h2><p class="title">' + esc(row["title"]) +
            '</p><p><strong>' + esc(row["status"]) + '</strong> · CI: ' +
            esc(row["checks"]) + ' · Review: ' + esc(row["review"]) +
            '</p><p class="sub">Bron: ' + esc(row["source"]) + ' · HEAD: ' +
            esc((row["head"] or "unknown")[:12]) + '</p><ul>' + actions + '</ul></article>'
        )
    return ('<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>zSentinel · Merge-first werkbak</title>'
            '<style>body{font:16px/1.5 system-ui,sans-serif;background:#111827;color:#f3f4f6;'
            'margin:auto;max-width:1000px;padding:2rem 1rem}a{color:#93c5fd}article{background:#1f2937;'
            'border:1px solid #475569;border-radius:1rem;margin:1rem 0;padding:1rem 1.4rem}'
            'h1{font-size:2rem}h2{font-size:1.25rem}p{margin:.45rem 0}.sub{color:#cbd5e1}'
            'li{margin:.2rem 0}a:focus-visible{outline:3px solid #fbbf24}'
            '@media(min-width:760px){main{display:grid;grid-template-columns:1fr 1fr;gap:1rem}'
            'article{margin:0}}'
            '</style><h1>zSentinel · Merge-first werkbak</h1><p>Opgehaald ' + esc(observed) +
            ' UTC. Alleen momentopname; geen live updates.</p><p>Een groene CI-waarneming is '
            '<strong>geen</strong> mergeautorisatie. Vereiste branch checks, actieve claims, '
            'review- en releaserechten blijven onafhankelijk.</p><main>' +
            "".join(cards) + '</main></html>')


def private_write(path, data):
    if path.suffix.lower() != ".html":
        raise ValueError("output must end in .html")
    payload = data.encode("utf-8")
    if len(payload) > MAX_OUTPUT:
        raise ValueError("output size limit exceeded")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", action="append", required=True, metavar="OWNER/REPO#123",
                        help="one existing PR, repeat max twelve (max three projects)")
    parser.add_argument("--output", type=Path, help="new private offline .html file; never overwritten")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    if len(args.pr) > MAX_PRS:
        parser.error("maximum twelve PRs")
    try:
        allowed = registry()
        selected, counts, seen = [], {}, set()
        for spec in args.pr:
            match = PR_SPEC.fullmatch(spec)
            if not match or match.group(1).casefold() not in allowed:
                raise ValueError("invalid or unregistered PR reference")
            repo, project = allowed[match.group(1).casefold()]
            number = int(match.group(2))
            key = (repo.casefold(), number)
            if key in seen:
                raise ValueError("duplicate PR reference")
            seen.add(key)
            counts[repo] = counts.get(repo, 0) + 1
            selected.append((repo, project, number))
        if len(counts) > MAX_PROJECTS or max(counts.values()) > MAX_PER_REPO:
            raise ValueError("maximum three projects and six PRs per repository")
        budget = [0]
        rows = [inspect(repo, number, budget) for repo, _, number in selected]
        observed = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if args.output:
            private_write(args.output, render(rows, observed))
        if args.format == "json":
            print(json.dumps({"observed_utc": observed, "github_gets": budget[0],
                              "main_merged": False, "live_verified": False,
                              "gate_authorization": "NOT_ASSESSED", "prs": rows},
                             ensure_ascii=False))
        else:
            for row in rows:
                print(f"{row['repo']}#{row['number']} {row['status']} | CI={row['checks']} "
                      f"| review={row['review']} | next: {'; '.join(row['actions'])}")
            if args.output:
                print("Private HTML created; no browser network or automatic merge.")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        parser.error("invalid source, selection or private output (no changes made)")


if __name__ == "__main__":
    main()
