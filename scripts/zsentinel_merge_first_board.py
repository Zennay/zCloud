#!/usr/bin/env python3
"""Read-only cross-project merge-first board for zCloud operators.

Does not acquire claims, merge, trigger CI, touch SQLite or deploy. A CI green
observation is never a substitute for protected-branch requirements or review.
"""
import argparse
import hashlib
import datetime as dt
import html
import json
import os
from pathlib import Path
import re
import sys
import time

from portfolio_merge_ready import PROJECTS, REPO_URL, gh, summarize_required_workflows

MAX_PROJECTS = 3
MAX_PRS = 12
MAX_PER_REPO = 6
MAX_GETS = 84
MAX_SECONDS = 120
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
    if budget[0] >= MAX_GETS or time.monotonic() >= budget[1]:
        raise ValueError("github request/time budget exceeded")
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


def observed_workflows(receipt, sha):
    """Display the same observed GitHub PR CI gate as the merge helper."""
    if not isinstance(receipt, dict):
        return "UNKNOWN"
    records = receipt.get("workflow_runs")
    total = receipt.get("total_count")
    if (not isinstance(records, list) or type(total) is not int
            or len(records) != total or len(records) >= 100):
        return "INCOMPLETE"
    state = summarize_required_workflows(records, sha)
    if state == "GREEN":
        return "OBSERVED_GREEN_ONLY"
    if state == "HEAD_WORKFLOWS_NOT_FINISHED":
        return "PENDING"
    if state == "HEAD_WORKFLOWS_NOT_GREEN":
        return "FAILED"
    if state == "NO_PR_WORKFLOW_EVIDENCE":
        return "NO_PR_WORKFLOW_EVIDENCE"
    return state


def inspect(repo, number, budget, expected_body_digest=None,
            expected_title_digest=None):
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
        first_title = first.get("title")
        if not isinstance(first_title, str) or len(first_title) > 5000:
            raise ValueError("untrusted PR title")
        title = first_title[:160]
        if expected_title_digest is not None:
            if (not isinstance(expected_title_digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", expected_title_digest)):
                raise ValueError("invalid PR title fingerprint")
            if hashlib.sha256(first_title.encode("utf-8")).hexdigest() != expected_title_digest:
                return {"repo": repo, "number": number, "title": title, "head": sha,
                        "status": "PR_TITLE_CHANGED", "review": "UNKNOWN",
                        "checks": "UNKNOWN", "source": "CHANGED",
                        "actions": ["Refresh current issue references"]}
        if expected_body_digest is not None:
            if not isinstance(expected_body_digest, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", expected_body_digest):
                raise ValueError("invalid PR body fingerprint")
            body = first.get("body") or ""
            if not isinstance(body, str) or len(body) > 128000:
                raise ValueError("untrusted PR body")
            first_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if first_digest != expected_body_digest:
                return {"repo": repo, "number": number, "title": title, "head": sha,
                        "status": "PR_DESCRIPTION_CHANGED", "review": "UNKNOWN",
                        "checks": "UNKNOWN", "source": "CHANGED",
                        "actions": ["Refresh current issue references"]}
        if first.get("state") != "open":
            # Closed != merged, and merged != currently contained in main.
            # Reconcile the immutable PR receipt with the *current* default ref.
            closed = {"repo": repo, "number": number, "title": title, "head": sha,
                      "status": "CLOSED_NOT_MERGED", "review": "NOT_APPLICABLE",
                      "checks": "NOT_APPLICABLE", "source": "COMPLETE",
                      "actions": ["Choose another open product PR"]}
            if first.get("merged") is not True or not first.get("merged_at"):
                return closed
            closed.update(status="MERGED_MAIN_UNVERIFIED", source="PARTIAL",
                          actions=["Verify the current default-branch ancestry"])
            if base["ref"] != "main":
                closed.update(status="MERGED_INTO_NONDEFAULT_BRANCH", source="COMPLETE",
                              actions=["Integrate the parent branch into main"])
                return closed
            merge_sha = first.get("merge_commit_sha")
            if not isinstance(merge_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", merge_sha):
                return closed
            closed["reported_commit"] = merge_sha
            current = bounded_gh(f"repos/{repo}/pulls/{number}", budget)
            ref = bounded_gh(f"repos/{repo}/git/ref/heads/main", budget)
            ref_object = ref.get("object") if isinstance(ref, dict) else None
            default_sha = ref_object.get("sha") if isinstance(ref_object, dict) else None
            if not (isinstance(current, dict) and isinstance(current.get("head"), dict)
                    and current["head"].get("sha") == sha
                    and current.get("state") == "closed" and current.get("merged") is True
                    and current.get("merged_at")
                    and current.get("merge_commit_sha") == merge_sha
                    and isinstance(current.get("base"), dict)
                    and current["base"].get("ref") == "main"
                    and isinstance(default_sha, str)
                    and re.fullmatch(r"[0-9a-f]{40}", default_sha)):
                return closed
            contained = merge_sha == default_sha
            if not contained:
                compare = bounded_gh(
                    f"repos/{repo}/compare/{merge_sha}...{default_sha}", budget)
                compare_base = compare.get("base_commit") if isinstance(compare, dict) else None
                contained = (isinstance(compare_base, dict)
                             and compare.get("status") == "ahead"
                             and compare_base.get("sha") == merge_sha
                             and type(compare.get("ahead_by")) is int
                             and compare["ahead_by"] > 0
                             and compare.get("behind_by") == 0)
            if contained:
                closed.update(status="MERGED_ON_MAIN_VERIFIED", source="COMPLETE",
                              main_commit=merge_sha,
                              actions=["Deployment requires separate live verification"])
            return closed
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
        workflows = bounded_gh(
            f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100", budget)
        last = bounded_gh(f"repos/{repo}/pulls/{number}", budget)
        if (not isinstance(last, dict) or not isinstance(last.get("head"), dict)
                or last["head"].get("sha") != sha or last.get("state") != "open"
                or last.get("base", {}).get("ref") != "main"
                or last.get("draft") is True):
            return {"repo": repo, "number": number, "title": title, "head": sha,
                    "status": "SOURCE_MOVED", "review": "UNKNOWN",
                    "checks": "UNKNOWN", "source": "CHANGED", "actions": ["Refresh exact-head evidence"]}
        if expected_title_digest is not None:
            final_title = last.get("title")
            if (not isinstance(final_title, str) or len(final_title) > 5000
                    or hashlib.sha256(final_title.encode("utf-8")).hexdigest()
                    != expected_title_digest):
                return {"repo": repo, "number": number, "title": title, "head": sha,
                        "status": "PR_TITLE_CHANGED", "review": "UNKNOWN",
                        "checks": "UNKNOWN", "source": "CHANGED",
                        "actions": ["Refresh current issue references"]}
        if expected_body_digest is not None:
            latest_body = last.get("body") or ""
            if (not isinstance(latest_body, str) or len(latest_body) > 128000
                    or hashlib.sha256(latest_body.encode("utf-8")).hexdigest()
                    != expected_body_digest):
                return {"repo": repo, "number": number, "title": title, "head": sha,
                        "status": "PR_DESCRIPTION_CHANGED", "review": "UNKNOWN",
                        "checks": "UNKNOWN", "source": "CHANGED",
                        "actions": ["Refresh current issue references"]}
        author = first.get("user", {}).get("login", "") if isinstance(first.get("user"), dict) else ""
        review = latest_approvals(revs, author, sha)
        check = observed_checks(runs, legacy)
        workflow = observed_workflows(workflows, sha)
        actions = []
        if last.get("mergeable") is not True:
            actions.append("Resolve conflict or wait for GitHub mergeability")
        if review != "EXACT_HEAD_APPROVED":
            actions.append("Obtain independent exact-head approval")
        if check != "OBSERVED_GREEN_ONLY":
            actions.append("Wait for or repair exact-head job/legacy checks")
        if workflow != "OBSERVED_GREEN_ONLY":
            actions.append("Wait for or repair pending exact-head workflow runs")
        # Even when all source signals look green, ownership and branch protection
        # have NOT been independently verified. Never label a PR merge-authorized.
        actions.append("Verify branch rules and existing exclusive mainwriter claim")
        return {"repo": repo, "number": number, "title": title, "head": sha,
                "status": "OBSERVED_GATES_ONLY" if len(actions) == 1 else "HOLD",
                "review": review, "checks": check, "workflows": workflow,
                "source": "COMPLETE", "actions": actions}
    except (KeyError, TypeError, ValueError, RuntimeError, OSError):
        return {"repo": repo, "number": number, "title": "", "head": None,
                "status": "SOURCE_UNAVAILABLE", "review": "UNKNOWN", "checks": "UNKNOWN",
                "source": "INCOMPLETE", "actions": ["Refresh this PR independently"]}

def delivery_receipt(row):
    """Attach actual default-branch delivery truth to each operator PR card.

    A historical merge timestamp or an unavailable ancestry query cannot
    authorize main delivery, production deployment or issue closure.
    """
    record = dict(row)
    verified = (record.get("status") == "MERGED_ON_MAIN_VERIFIED"
                and isinstance(record.get("main_commit"), str)
                and re.fullmatch(r"[0-9a-f]{40}", record["main_commit"]) is not None)
    record["main_merged"] = bool(verified)
    record["live_verified"] = False
    if not verified:
        record.pop("main_commit", None)
    return record


def render(rows, observed):
    esc = lambda x: html.escape(str(x), quote=True)
    cards = []
    if not rows:
        cards.append("<article><h2>Geen recente PRs geselecteerd</h2>"
                     "<p>Dit bewijst niet dat er geen open productwerk is.</p>"
                     "<p>Gebruik een gericht --pr voor een ouder of uitgesloten PR.</p></article>")
    for row in rows:
        url = f"https://github.com/{row['repo']}/pull/{row['number']}"
        actions = "".join("<li>" + esc(act) + "</li>" for act in row["actions"])
        cards.append(
            '<article><h2><a href="' + esc(url) + '">' + esc(row["repo"]) + " #" +
            esc(row["number"]) + '</a></h2><p class="title">' + esc(row["title"]) +
            '</p><p><strong>' + esc(row["status"]) + '</strong> · CI: ' +
            esc(row["checks"]) + ' · Review: ' + esc(row["review"]) +
            ' · Workflows: ' + esc(row.get("workflows", "UNKNOWN")) +
            '</p><p class="sub">Bron: ' + esc(row["source"]) + ' · HEAD: ' +
            esc((row["head"] or "unknown")[:12]) +
            (' · Main: <a href="' + esc('https://github.com/' + row['repo'] +
                                      '/commit/' + row['main_commit']) + '">' +
             esc(row['main_commit'][:12]) + '</a>' if row.get("status") ==
             "MERGED_ON_MAIN_VERIFIED" and row.get("main_commit") else '') +
            '</p><ul>' + actions + '</ul></article>'
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


def discovery(projects, allowed, budget, max_per_project):
    """Bounded and deliberately partial portfolio source: recent open PRs only.

    We never infer owner availability, claim rights, merge eligibility or
    completed delivery from a search result. A 100-item source is partial.
    """
    selected = []
    partial = []
    for project in projects:
        matches = [(repo, key) for repo, key in allowed.values() if key == project]
        if len(matches) != 1:
            raise ValueError("missing or ambiguous project registry")
        repo, key = matches[0]
        try:
            inventory = bounded_gh(
                f"repos/{repo}/pulls?state=open&per_page=100&sort=updated&direction=desc",
                budget)
        except (RuntimeError, ValueError, KeyError, TypeError):
            partial.append(repo)
            continue  # A failed project does not discard unrelated PR sources.
        if not isinstance(inventory, list):
            partial.append(repo)
            continue
        if len(inventory) >= 100:
            partial.append(repo)
        eligible = []
        malformed = False
        for entry in inventory:
            if not isinstance(entry, dict):
                malformed = True
                break
            title = entry.get("title")
            number = entry.get("number")
            if (not isinstance(title, str) or not isinstance(number, int)
                    or number <= 0 or number > 9999999):
                malformed = True
                break
            # QA-only work is excluded from discovery, not from observed
            # collision evidence or other workers' authoritative ownership.
            normalized = title.strip().casefold()
            if (type(entry.get("draft")) is not bool
                    or normalized.startswith(("test(", "test:", "qa(", "qa:", "chore(qa)"))
                    or "quality-validation" in normalized):
                continue
            if entry.get("state") != "open":
                continue
            base = entry.get("base")
            if not isinstance(base, dict) or base.get("ref") != "main":
                continue
            rank = (0 if normalized.startswith(("p0", "fix(p0", "fix:", "fix("))
                    else 1 if normalized.startswith(("p1", "feat(", "feat:"))
                    else 2)
            # Ready PRs take precedence within the same priority, while
            # DRAFT remains visible as owner-controlled unfinished work.
            eligible.append((rank, 1 if entry["draft"] else 0, len(eligible), number))
        if malformed:
            partial.append(repo)
            continue  # Fail closed for this inventory, retain other projects.
        # Priority is only an ordering aid, never ownership or merge evidence.
        for _, _, _, number in sorted(eligible)[:max_per_project]:
            selected.append((repo, key, number))
    return selected, partial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", action="append", metavar="OWNER/REPO#123",
                        help="explicit existing PR; max twelve across three repositories")
    parser.add_argument("--project", action="append", metavar="PROJECT_ID",
                        help="discover recent open product PRs for this canonical project")
    parser.add_argument("--max-per-project", type=int, default=4,
                        help="discovery cap per project (1–4); three-project budget safe")
    parser.add_argument("--output", type=Path, help="new private offline .html file; never overwritten")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    if not args.pr and not args.project:
        parser.error("provide --pr or --project")
    if args.pr and args.project:
        parser.error("choose explicit --pr or discovery --project, not both")
    if not 1 <= args.max_per_project <= 4:
        parser.error("--max-per-project must be between 1 and 4")
    if args.pr and len(args.pr) > MAX_PRS:
        parser.error("maximum twelve PRs")
    try:
        allowed = registry()
        selected, counts, seen, partial, budget = [], {}, set(), [], [0, time.monotonic() + MAX_SECONDS]
        if args.project:
            if len(args.project) > MAX_PROJECTS or len(set(args.project)) != len(args.project):
                raise ValueError("maximum three distinct projects")
            selected, partial = discovery(args.project, allowed, budget,
                                          args.max_per_project)
        else:
            for spec in args.pr:
                match = PR_SPEC.fullmatch(spec)
                if not match or match.group(1).casefold() not in allowed:
                    raise ValueError("invalid or unregistered PR reference")
                repo, project = allowed[match.group(1).casefold()]
                selected.append((repo, project, int(match.group(2))))
        for repo, _, number in selected:
            key = (repo.casefold(), number)
            if key in seen:
                raise ValueError("duplicate PR reference")
            seen.add(key)
            counts[repo] = counts.get(repo, 0) + 1
        if len(counts) > MAX_PROJECTS or (counts and max(counts.values()) > MAX_PER_REPO):
            raise ValueError("maximum three projects and six PRs per repository")
        if len(selected) > MAX_PRS:
            raise ValueError("maximum twelve PRs")
        rows = [delivery_receipt(inspect(repo, number, budget))
                for repo, _, number in selected]
        observed = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if args.output:
            heading = ("<p><strong>Onvolledige recente PR-bron:</strong> "
                       + html.escape(", ".join(partial), quote=True)
                       + ". Oudere PRs kunnen ontbreken. Gebruik --pr voor gerichte bron.</p>"
                       if partial else "")
            page = render(rows, observed)
            if heading:
                page = page.replace("<main>", heading + "<main>", 1)
            private_write(args.output, page)
        if args.format == "json":
            print(json.dumps({"observed_utc": observed, "github_gets": budget[0],
                              "discovery_partial_repositories": partial,
                              "selection": "recent_open_product_only" if args.project else "explicit",
                              "main_delivery_scope": "PER_PR_EXACT_SOURCE_ONLY",
                              "main_verified_prs": sum(row["main_merged"] for row in rows),
                              "live_verified": False,
                              "gate_authorization": "NOT_ASSESSED", "prs": rows},
                             ensure_ascii=False))
        else:
            if partial:
                print("PARTIAL RECENT INVENTORY: " + ", ".join(partial)
                      + " (older open PRs not enumerated)")
            for row in rows:
                print(f"{row['repo']}#{row['number']} {row['status']} | CI={row['checks']} "
                      f"| review={row['review']} | workflows={row.get('workflows', 'UNKNOWN')}"
                      f" | main_merged={str(row['main_merged']).lower()}"
                      f" | main_commit={row.get('main_commit', 'NOT_VERIFIED')}"
                      f" | live_verified=false | next: {'; '.join(row['actions'])}")
            if not rows:
                print("No eligible recent product PRs observed; not proof backlog is empty.")
            if args.output:
                print("Private HTML created; no browser network or automatic merge.")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        parser.error("invalid source, selection or private output (no changes made)")


if __name__ == "__main__":
    main()
