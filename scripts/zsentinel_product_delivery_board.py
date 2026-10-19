#!/usr/bin/env python3
"""Offline zSentinel portfolio delivery board. Never claims work or mutates source evidence."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
from html import escape
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

SCHEMAS = {
    "inbox": "zsentinel-product-issue-inbox-v1",
    "retry": "zsentinel-retry-incident-v1",
    "branches": "zsentinel-recent-prless-ownership-v1",
    "claims": "zsentinel-operator-claims-v1",
}
PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ISSUE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]{0,8}$")
URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(?:issues|pull)/[1-9][0-9]{0,8}$")
PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "Unspecified": 3}
MAX_INPUT = 2 * 1024 * 1024


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous duplicate JSON key")
        result[key] = value
    return result


def read(path: Path, schema: str) -> dict:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_INPUT:
        raise ValueError("source is not a bounded regular file")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("secure no-follow opening not supported")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (
                opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("source identity drift")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(MAX_INPUT + 1)
        after = os.fstat(fd)
        if len(raw) > MAX_INPUT or (opened.st_size, opened.st_mtime_ns) != (
                after.st_size, after.st_mtime_ns):
            raise ValueError("source changed during bounded read")
    finally:
        os.close(fd)
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              ValueError("invalid JSON constant")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("source must be UTF-8 JSON") from exc
    if type(data) is not dict or data.get("schema") != SCHEMAS[schema]:
        raise ValueError("unrecognized source schema: " + schema)
    return data


def when(value):
    if type(value) is not str or len(value) > 45:
        raise ValueError("invalid source clock")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid source clock") from exc
    if stamp.tzinfo is None:
        raise ValueError("clock lacks UTC offset")
    stamp = stamp.astimezone(timezone.utc)
    if stamp > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("future-dated evidence")
    return stamp


def project(value):
    if type(value) is not str or not PROJECT.fullmatch(value):
        raise ValueError("malformed project ID")
    return value


def count(value):
    if type(value) is not int or not 0 <= value <= 100000000:
        raise ValueError("malformed source count")
    return value


def prepare(inbox, retry=None, branches=None, claims=None, hours=24, limit=40):
    if (inbox.get("actions_authorized") is not False
            or inbox.get("active_claims_verified") is not False
            or inbox.get("queue_ownership_known") is not False):
        raise ValueError("issue inbox unexpectedly claims authority")
    observed = when(inbox.get("observed_at"))
    source_times = {"inbox": observed}
    coverage_rows = inbox.get("coverage")
    items = inbox.get("items")
    if (type(coverage_rows) is not list or len(coverage_rows) > 50
            or type(items) is not list or len(items) > 120):
        raise ValueError("issue inbox coverage or bounds invalid")
    coverage = {}
    for row in coverage_rows:
        if type(row) is not dict:
            raise ValueError("malformed project coverage")
        key = project(row.get("project"))
        if key in coverage or row.get("status") not in ("partial", "degraded", "unavailable"):
            raise ValueError("ambiguous project coverage")
        coverage[key] = row["status"]
    if (type(inbox.get("truncated")) is not bool
            or type(inbox.get("stopped_after_rate_or_auth_denial")) is not bool):
        raise ValueError("inbox lacks explicit completeness status")
    issues, seen = [], set()
    for row in items:
        if type(row) is not dict:
            raise ValueError("malformed issue")
        key = row.get("id")
        pid = project(row.get("project"))
        if type(key) is not str or not ISSUE.fullmatch(key) or key in seen or pid not in coverage:
            raise ValueError("invalid, uncovered or duplicate issue")
        seen.add(key)
        url = row.get("url")
        if (type(url) is not str or not URL.fullmatch(url) or
                "/issues/" not in url or key.replace("#", "/issues/") not in url):
            raise ValueError("issue URL mismatch")
        if row.get("priority") not in PRIORITY or type(row.get("qa_only")) is not bool:
            raise ValueError("invalid issue priority/classification")
        if row.get("active_claim") != "not_verified":
            raise ValueError("issue inbox cannot provide claim proof")
        refs = row.get("related_open_prs")
        if type(refs) is not list or len(refs) > 3:
            raise ValueError("invalid PR reference bounds")
        links = []
        for ref in refs:
            link = ref.get("url") if type(ref) is dict else None
            if (type(link) is not str or not URL.fullmatch(link)
                    or "/pull/" not in link or ref.get("active_claim") != "not_verified"):
                raise ValueError("non-canonical or authoritative PR reference")
            links.append(link)
        title = row.get("title")
        if type(title) is not str or not title or len(title) > 180:
            raise ValueError("invalid issue title")
        if not row["qa_only"]:
            issues.append({"issue": key, "project": pid, "priority": row["priority"],
                           "title": title, "url": url,
                           "open_pr_candidates": sorted(set(links))})
    retry_by_project = {}
    retry_meta = None
    if retry is not None:
        flags = retry.get("coverage")
        if (type(flags) is not dict or flags.get("database_read_only") is not True
                or flags.get("safe_to_authorize_retry") is not False
                or flags.get("attempts_count_claims_not_failures") is not True):
            raise ValueError("retry evidence attempted authorization")
        source_times["retry"] = when(retry.get("observed_at_utc"))
        groups = retry.get("project_groups")
        scope = retry.get("scope")
        if (type(groups) is not list or len(groups) > 2500
                or type(scope) is not dict or type(flags.get("sample_is_complete")) is not bool):
            raise ValueError("retry evidence lacks bounds")
        selected = scope.get("project")
        if selected != "all":
            project(selected)
        for row in groups:
            if type(row) is not dict:
                raise ValueError("malformed queue aggregation")
            pid = project(row.get("project_id"))
            if selected != "all" and selected != pid:
                raise ValueError("cross-project retry scope")
            total, high, expired = (count(row.get(k)) for k in (
                "items", "high_attempts", "expired_lease_rows"))
            if high > total or expired > total:
                raise ValueError("incoherent queue aggregation")
            item = retry_by_project.setdefault(pid, {"active_items": 0,
                "high_claim_attempts": 0, "expired_active_leases": 0})
            for name, amount in zip(item, (total, high, expired)):
                item[name] += amount
        retry_meta = {"scope": selected,
                      "sample_is_complete": flags["sample_is_complete"]}
    branch_meta = None
    if branches is not None:
        if (branches.get("repository") != "Zennay/zCloud"
                or branches.get("snapshot_complete") is not True
                or branches.get("ownership_granted") is not False
                or branches.get("may_admit_work") is not False
                or branches.get("release_authorized") is not False):
            raise ValueError("PR-less branch evidence lacks no-authorization boundary")
        source_times["branches"] = when(branches.get("observed_at"))
        rows = branches.get("branches")
        if type(rows) is not list or len(rows) > 12 or type(branches.get("coverage_complete")) is not bool:
            raise ValueError("invalid PR-less inventory slice")
        overlaps = 0
        for row in rows:
            if type(row) is not dict or row.get("owner_proven") is not False:
                raise ValueError("branch is not an authorized worker owner")
            if row.get("classification") == "recent_unmerged":
                paths = row.get("overlapping_paths")
                if type(paths) is not list or len(paths) > 80:
                    raise ValueError("malformed branch overlap evidence")
                overlaps += bool(paths)
            elif row.get("classification") not in ("historical", "not_ahead"):
                raise ValueError("unknown branch age classification")
        branch_meta = {"coverage_complete": branches["coverage_complete"],
                       "recent_overlap_candidates": overlaps,
                       "repository": "Zennay/zCloud"}
    claims_by_project = None
    if claims is not None:
        if claims.get("authorizes_work") is not False:
            raise ValueError("operator claim snapshot cannot grant work")
        source_times["claims"] = when(claims.get("observed_at_utc"))
        rows = claims.get("claims")
        if type(rows) is not list or len(rows) > 150:
            raise ValueError("invalid claims snapshot")
        claims_by_project = {}
        for row in rows:
            if type(row) is not dict or row.get("status") not in ("Claimed", "Running"):
                raise ValueError("invalid active-claim entry")
            pid, work_id = project(row.get("project")), count(row.get("work_id"))
            claims_by_project.setdefault(pid, []).append(work_id)
    now = datetime.now(timezone.utc)
    fresh = {name: (now - stamp <= timedelta(hours=hours))
             for name, stamp in source_times.items()}
    for name in ("retry", "branches", "claims"):
        if name in fresh and abs(source_times[name] - observed) > timedelta(hours=6):
            fresh[name] = False
    issues.sort(key=lambda x: (PRIORITY[x["priority"]],
                -retry_by_project.get(x["project"], {}).get("high_claim_attempts", 0),
                x["project"], x["issue"]))
    cards = []
    for row in issues[:limit]:
        pid = row["project"]
        queue = (retry_by_project.get(pid) if fresh.get("retry") and
                 retry_meta["scope"] in ("all", pid) else None)
        claim_ids = (claims_by_project.get(pid, []) if fresh.get("claims")
                     and claims_by_project is not None else None)
        if claim_ids:
            next_step = "Coordinate with the current claim holder, do not displace."
        elif row["open_pr_candidates"]:
            next_step = "Inspect the already open PR, CI and independent review first."
        elif queue and queue["expired_active_leases"]:
            next_step = "Investigate lease expiry with owner; do not auto-reclaim."
        elif queue and queue["high_claim_attempts"]:
            next_step = "Investigate claim-cycle churn, not inferred failures."
        else:
            next_step = "Verify current claims and file ownership before implementation."
        gaps = []
        if fresh.get("inbox") is not True:
            gaps.append("Issue evidence is stale; verify GitHub before deciding.")
        if coverage[pid] != "partial" or inbox["truncated"]:
            gaps.append("The GitHub issue window is bounded/incomplete.")
        if claim_ids is None:
            gaps.append("Fresh active-claim snapshot unavailable; never infer free ownership.")
        else:
            gaps.append("Operator-provided claim observations are not authoritative leases.")
        if queue is None:
            gaps.append("Synchronized queue incident evidence unavailable.")
        if not fresh.get("branches"):
            gaps.append("Current branch overlap evidence unavailable.")
        elif pid == "cloud" and not branch_meta["coverage_complete"]:
            gaps.append("zCloud PR-less branch sample covers only part of the inventory.")
        cards.append({**row, "queue": queue, "observed_claim_ids": claim_ids,
                      "project_issue_coverage": coverage[pid],
                      "recommended_next_step": next_step, "evidence_gaps": gaps,
                      "claim_authorized": False, "merge_authorized": False,
                      "deploy_authorized": False})
    # Independent project-level incident work is visible even without any
    # issue card; a queue with no recent GitHub issue must not vanish.
    issue_totals = {}
    for row in issues:
        counts = issue_totals.setdefault(row["project"], {"P0": 0, "P1": 0, "P2": 0, "Unspecified": 0})
        counts[row["priority"]] += 1
    names = sorted(set(coverage) | set(retry_by_project) |
                   set(claims_by_project or ()))
    project_operations = []
    for pid in names:
        queue = retry_by_project.get(pid) if fresh.get("retry") and (
            retry_meta["scope"] in ("all", pid)) else None
        active = (claims_by_project.get(pid, []) if claims_by_project is not None
                  and fresh.get("claims") else None)
        if active:
            action = "Preserve active owner; review claim-specific next steps."
        elif queue and queue["expired_active_leases"]:
            action = "Escalate expired leases to the authorized queue owner."
        elif queue and queue["high_claim_attempts"]:
            action = "Inspect recurring claim cycles; do not auto-retry."
        elif issue_totals.get(pid, {}).get("P0", 0):
            action = "Review P0 issue and current owner/PR state."
        else:
            action = "Review live claims and existing PRs before action."
        project_operations.append({
            "project": pid, "issue_counts_in_sample": issue_totals.get(pid, {
                "P0": 0, "P1": 0, "P2": 0, "Unspecified": 0}),
            "queue_observation": queue,
            "observed_claim_ids": active,
            "issue_coverage": coverage.get(pid, "unavailable"),
            "recommended_next_step": action,
            "is_complete_inventory": False,
            "action_authorized": False,
        })
    project_operations.sort(key=lambda item: (
        -item["issue_counts_in_sample"]["P0"],
        -(item["queue_observation"] or {}).get("high_claim_attempts", 0),
        -item["issue_counts_in_sample"]["P1"], item["project"],
    ))

    return {
        "schema": "zsentinel-product-delivery-triage-v1",
        "observed_at_utc": now.isoformat(),
        "source_times_utc": {k: v.isoformat() for k, v in source_times.items()},
        "fresh": fresh,
        "coverage": {"projects": coverage, "inbox_truncated": inbox["truncated"],
                     "github_denial": inbox["stopped_after_rate_or_auth_denial"],
                     "retry": retry_meta, "branches": branch_meta,
                     "claims_supplied": claims is not None,
                     "cards_truncated": len(issues) > limit},
        "items_available": len(issues), "cards": cards,
        "project_operations": project_operations,
        "authorizes_claim": False, "authorizes_merge": False,
        "authorizes_deploy": False, "automatic_dispatch_enabled": False,
        "operator_note": "Bounded evidence only. Assignees, PR mentions, branch age and "
                         "snapshot observations do not establish current ownership. "
                         "Recheck live Notion/queue claims, exact-head CI, independent "
                         "review and release permissions before any mutation.",
    }


def markdown(data):
    lines = ["# zSentinel · Product delivery board", "",
             "**Read-only evidence; no work ownership, merge or deployment authority.**", "",
             "| Priority | Project | Issue | PR candidates | Owner-safe next step |",
             "|---|---|---|---|---|"]
    safe = lambda x: (str(x).replace("\\", "\\\\").replace("|", "\\|")
                      .replace("<", "&lt;").replace(">", "&gt;")
                      .replace("\n", " "))
    for row in data["cards"]:
        links = ", ".join(row["open_pr_candidates"]) or "none in bounded sample"
        lines.append("| " + " | ".join(map(safe, (row["priority"], row["project"],
            row["issue"], links, row["recommended_next_step"]))) + " |")
        lines.extend("- " + safe(row["issue"]) + ": " + safe(g)
                     for g in row["evidence_gaps"])
    lines.extend(["", "## Project-wide queue and ownership context",
                  "| Project | P0 | P1 | High claim cycles | Expired leases | Operator next step |",
                  "|---|---:|---:|---:|---:|---|"])
    for row in data["project_operations"]:
        queue = row["queue_observation"] or {}
        lines.append("| " + " | ".join(map(safe, (
            row["project"], row["issue_counts_in_sample"]["P0"],
            row["issue_counts_in_sample"]["P1"],
            queue.get("high_claim_attempts", "unknown"),
            queue.get("expired_active_leases", "unknown"),
            row["recommended_next_step"],
        ))) + " |")
    return "\n".join(lines + ["", data["operator_note"], ""])


def html(data):
    projects = []
    for entry in data["project_operations"]:
        queue = entry["queue_observation"] or {}
        projects.append("<tr><td>" + escape(entry["project"]) + "</td><td>" +
            str(entry["issue_counts_in_sample"]["P0"]) + "</td><td>" +
            str(entry["issue_counts_in_sample"]["P1"]) + "</td><td>" +
            escape(str(queue.get("high_claim_attempts", "unknown"))) + "</td><td>" +
            escape(str(queue.get("expired_active_leases", "unknown"))) + "</td><td>" +
            escape(entry["recommended_next_step"]) + "</td></tr>")
    rows = []
    for row in data["cards"]:
        links = "".join('<a target="_blank" rel="noopener noreferrer" href="' +
            escape(link, quote=True) + '">Existing PR ' +
            escape(link.rsplit("/", 1)[-1]) + '</a> ' for link in row["open_pr_candidates"])
        gaps = "".join("<li>" + escape(g) + "</li>" for g in row["evidence_gaps"])
        rows.append('<article><small>' + escape(row["priority"]) + " · " +
            escape(row["project"]) + " · " + escape(row["issue"]) +
            '</small><h2><a target="_blank" rel="noopener noreferrer" href="' +
            escape(row["url"], quote=True) + '">' + escape(row["title"]) +
            '</a></h2><p><strong>Next:</strong> ' +
            escape(row["recommended_next_step"]) + '</p><p>' + links +
            '</p><details><summary>Evidence gaps</summary><ul>' + gaps +
            '</ul></details></article>')
    css = """
:root{color-scheme:dark;background:#101722;color:#edf3fa;font:16px/1.5 system-ui,sans-serif}
body{max-width:1140px;margin:auto;padding:clamp(14px,4vw,36px)}
h1{font-size:clamp(1.6rem,4vw,2.5rem)}h2{font-size:1.1rem}
a{color:#bddfff}a:focus-visible,summary:focus-visible{outline:3px solid #ffd98b}
.board{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,350px),1fr));gap:14px}
article{padding:18px;background:#1d2b3c;border:1px solid #3b5268;border-radius:12px}
small{color:#cad8e8}.notice{border-left:4px solid #ffcf77;padding:14px;background:#302921}
summary{cursor:pointer}
table{border-collapse:collapse;width:100%;margin:20px 0}th,td{text-align:left;padding:8px;border-bottom:1px solid #395165}
.scroll{overflow-x:auto}
"""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>zSentinel Product Delivery Board</title><style>' + css +
            '</style></head><body><h1>zSentinel · Product delivery board</h1>'
            '<p class="notice">' + escape(data["operator_note"]) + '</p>'
            '<p>Issues observed: ' + str(data["items_available"]) +
            ' · Source truncated: ' + str(data["coverage"]["inbox_truncated"]).lower() +
            '</p><h2>Project-wide operations</h2><div class="scroll"><table>'
            '<thead><tr><th>Project</th><th>P0</th><th>P1</th>'
            '<th>Claim cycles</th><th>Expired leases</th><th>Next step</th>'
            '</tr></thead><tbody>' + "".join(projects) + '</tbody></table></div>'
            '<section class="board">' + "".join(rows) +
            '</section><footer>Offline: no scripts, fetches, queue or lease writes.</footer>'
            '</body></html>')


def write_create_only(path, payload):
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=".zsentinel-board-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(payload)
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox", type=Path, required=True)
    parser.add_argument("--retry", type=Path)
    parser.add_argument("--branches", type=Path)
    parser.add_argument("--claims", type=Path)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--max-age-hours", type=int, default=24)
    parser.add_argument("--format", choices=("json", "markdown", "html"), default="html")
    parser.add_argument("--output", type=Path, help="Create-only private report; never overwrite")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 60 or not 1 <= args.max_age_hours <= 168:
        parser.error("limit must be 1–60 and max-age-hours 1–168")
    try:
        paths = [x for x in (args.inbox, args.retry, args.branches, args.claims) if x]
        if len({x.resolve() for x in paths}) != len(paths):
            raise ValueError("sources must be separate immutable files")
        result = prepare(read(args.inbox, "inbox"),
            retry=read(args.retry, "retry") if args.retry else None,
            branches=read(args.branches, "branches") if args.branches else None,
            claims=read(args.claims, "claims") if args.claims else None,
            hours=args.max_age_hours, limit=args.limit)
        output = (json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                  if args.format == "json" else
                  markdown(result) if args.format == "markdown" else html(result))
        if args.output:
            write_create_only(args.output, output)
        else:
            print(output)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("zSentinel board denied: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
