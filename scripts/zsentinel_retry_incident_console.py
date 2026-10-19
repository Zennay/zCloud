#!/usr/bin/env python3
"""zSentinel queue retry incident console. Never claims, reconciles or edits queue rows.

This is an observation aid for zCloud issue #677, not a retry state machine.
No privileged action is inferred from the number of claim attempts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import sqlite3
import sys


ACTIVE = ("queued", "claimed", "running", "verifying")
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
PROJECT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
REQUIRED = {
    "project_id", "priority", "status", "attempts", "eligible",
    "updated_at", "claim_expires", "title",
}
MAX_SAMPLE = 100


def read_snapshot(path: Path, *, project: str | None, threshold: int, limit: int,
                  include_titles: bool) -> dict:
    """One consistent SQLite snapshot; the connection is URI read-only and query_only."""
    if not path.is_file():
        raise ValueError("SQLite database does not exist or is not a regular file")
    now = datetime.now(timezone.utc)
    # sqlite3 URI escaping is handled by Path.as_uri; mode=ro never creates a DB.
    uri = path.resolve().as_uri().replace("file://", "file:") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=3000")
        connection.execute("BEGIN")  # stable read across grouped + sample queries
        available = {
            row["name"] for row in connection.execute("PRAGMA table_info(portfolio_queue)")
        }
        if not REQUIRED <= available:
            raise ValueError("Unsupported portfolio_queue schema (missing required columns)")
        where = "status IN ('queued','claimed','running','verifying')"
        args: list = []
        if project:
            where += " AND project_id=?"
            args.append(project)
        groups = [dict(row) for row in connection.execute(
            f"""SELECT project_id, priority, status, COUNT(*) AS items,
                       SUM(CASE WHEN eligible=1 THEN 1 ELSE 0 END) AS eligible_items,
                       SUM(CASE WHEN attempts>=? THEN 1 ELSE 0 END) AS high_attempts,
                       SUM(CASE WHEN attempts>=10 THEN 1 ELSE 0 END) AS ten_plus,
                       MAX(attempts) AS max_attempts,
                       SUM(CASE WHEN status IN ('claimed','running','verifying')
                                 AND claim_expires IS NOT NULL AND claim_expires<=?
                            THEN 1 ELSE 0 END) AS expired_lease_rows
                FROM portfolio_queue WHERE {where}
                GROUP BY project_id,priority,status""",
            [threshold, now.isoformat(), *args],
        )]
        rows = [dict(row) for row in connection.execute(
            f"""SELECT project_id,priority,status,attempts,eligible,updated_at,claim_expires
                       {',title' if include_titles else ''}
                FROM portfolio_queue
                WHERE {where} AND attempts>=?
                ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1
                         WHEN 'P2' THEN 2 ELSE 3 END,
                         attempts DESC,updated_at ASC
                LIMIT ?""",
            [*args, threshold, limit],
        )]
        connection.execute("ROLLBACK")
    finally:
        connection.close()

    grouped = sorted(groups, key=lambda x: (
        PRIORITY_ORDER.get(x["priority"], 4), -x["high_attempts"], x["project_id"], x["status"]
    ))
    for item in grouped:
        item["interpretation"] = (
            "requires-owner-review: active claim/lease"
            if item["status"] != "queued"
            else "candidate-for-manual-retry-policy-review"
        ) if item["high_attempts"] else "no-high-attempts-observed"
    samples = []
    for row in rows:
        status = str(row["status"])
        if status == "queued" and row["eligible"]:
            next_action = "Investigate repeat claim/requeue; do not auto-retry."
        elif status in ("claimed", "running", "verifying"):
            next_action = "Preserve current owner; check lease and worker evidence."
        else:
            next_action = "Review eligibility with queue owner."
        sample = {
            "project": row["project_id"],
            "priority": row["priority"],
            "status": status,
            "attempts": row["attempts"],
            "eligible": bool(row["eligible"]),
            "updated_at": row["updated_at"],
            "lease_expires_at": row["claim_expires"],
            "operator_action": next_action,
        }
        if include_titles:
            sample["title"] = str(row.get("title") or "")[:150]
        samples.append(sample)
    return {
        "schema": "zsentinel-retry-incident-v1",
        "observed_at_utc": now.isoformat(),
        "scope": {"project": project or "all", "minimum_claim_attempts": threshold},
        "coverage": {
            "active_queue_states_only": list(ACTIVE),
            "sample_limit": limit,
            "sample_is_complete": len(samples) < limit,
            "database_read_only": True,
            "safe_to_authorize_retry": False,
            "attempts_count_claims_not_failures": True,
            "requires_release_owner_for_mutations": True,
        },
        "totals": {
            "active_items": sum(group["items"] for group in grouped),
            "high_attempt_items": sum(group["high_attempts"] for group in grouped),
            "ten_plus_items": sum(group["ten_plus"] for group in grouped),
            "expired_active_leases": sum(group["expired_lease_rows"] for group in grouped),
            "max_claim_attempts": max((group["max_attempts"] for group in grouped), default=0),
        },
        "project_groups": grouped,
        "high_attempt_samples": samples,
        "operator_note": (
            "Claim attempts are not a failure counter. This report does not grant a "
            "worker claim, owner scope, retries, cleanup, merge or deploy. "
            "Native finite retry disposition and backoff remain open in issue #677."
        ),
    }


def markdown(data: dict) -> str:
    lines = [
        "# zSentinel · Queue retry incident console",
        "",
        "Source: read-only SQLite observation. **Not a retry authorization or recovery proof.**",
        f"Observed: {data['observed_at_utc']}",
        f"Scope: {data['scope']['project']}; claim-attempt threshold: {data['scope']['minimum_claim_attempts']}",
        "",
        "## Current queue",
    ]
    for key, value in data["totals"].items():
        lines.append(f"- {key.replace('_', ' ')}: **{value}**")
    lines += ["", "## Affected project/status groups",
              "| Project | Priority | Status | Active | Above threshold | 10+ | Expired leases |",
              "|---|---|---|---:|---:|---:|---:|"]
    for group in data["project_groups"]:
        lines.append(
            f"| {group['project_id']} | {group['priority']} | {group['status']} | "
            f"{group['items']} | {group['high_attempts']} | {group['ten_plus']} | "
            f"{group['expired_lease_rows']} |"
        )
    lines += ["", "## Bounded high-attempt observations"]
    for sample in data["high_attempt_samples"]:
        label = sample["project"]
        if "title" in sample:
            label += " — " + sample["title"].replace("\n", " ")[:150]
        lines.append(
            f"- {label}: {sample['priority']} / {sample['status']}, "
            f"{sample['attempts']} claims. {sample['operator_action']}"
        )
    lines += ["", f"Sample complete: {data['coverage']['sample_is_complete']}.",
              "", data["operator_note"], ""]
    return "\n".join(lines)


def html_page(data: dict) -> str:
    title = "zSentinel · Queue retry incident console"
    css = """body{font:16px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:1.25rem;
background:#101827;color:#e8ecf6}h1{font-size:1.7rem}small,.muted{color:#bdc8d7}
summary{cursor:pointer}table{border-collapse:collapse;width:100%;margin:1rem 0}
td,th{padding:.7rem;text-align:left;border-bottom:1px solid #39465a}th{color:#a7d7ff}
.wrap{overflow-x:auto}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:.7rem}
.card{background:#1d2c40;padding:1rem;border-radius:12px}strong{font-size:1.35rem;display:block}
.warning{border-left:4px solid #e0a650;padding:.7rem 1rem;background:#302a22}
tr:nth-child(even){background:#172234}"""
    def td(value: object) -> str:
        return "<td>" + escape(str(value if value is not None else "—")) + "</td>"
    cards = "".join(
        f"<div class='card'><span>{escape(k.replace('_',' '))}</span>"
        f"<strong>{escape(str(v))}</strong></div>"
        for k, v in data["totals"].items()
    )
    group_rows = "".join(
        "<tr>" + "".join(td(g[k]) for k in (
            "project_id", "priority", "status", "items", "high_attempts",
            "ten_plus", "expired_lease_rows"
        )) + "</tr>" for g in data["project_groups"]
    )
    sample_rows = "".join(
        "<tr>" + "".join(td(row.get(k)) for k in (
            "project", "priority", "status", "attempts", "updated_at", "operator_action"
        )) + "</tr>" for row in data["high_attempt_samples"]
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{css}</style></head><body>
<h1>{escape(title)}</h1><p class="muted">Observed {escape(data['observed_at_utc'])}
· Project {escape(data['scope']['project'])}
· Threshold {data['scope']['minimum_claim_attempts']} claims</p>
<p class="warning">{escape(data['operator_note'])}</p><section class="cards">{cards}</section>
<h2>Project and status breakdown</h2><div class="wrap"><table><thead><tr>
<th>Project</th><th>Priority</th><th>Status</th><th>Active</th>
<th>High attempts</th><th>10+</th><th>Expired leases</th></tr></thead>
<tbody>{group_rows}</tbody></table></div>
<h2>High-attempt sample</h2><p>Up to {data['coverage']['sample_limit']} entries,
ordered by priority and attempts; complete: {str(data['coverage']['sample_is_complete']).lower()}.</p>
<div class="wrap"><table><thead><tr><th>Project</th><th>Priority</th><th>Status</th>
<th>Claims</th><th>Last updated</th><th>Operator action</th></tr></thead>
<tbody>{sample_rows}</tbody></table></div>
<footer class="muted">Offline HTML · no JavaScript, external assets, API calls,
queue writes, or authorization to restart or dispatch.</footer></body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Existing zCloud SQLite DB")
    parser.add_argument("--project", help="Optional exact canonical project ID")
    parser.add_argument("--threshold", type=int, default=3)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--format", choices=("json", "markdown", "html"), default="html")
    parser.add_argument("--output", type=Path, help="Write a local report; otherwise stdout")
    parser.add_argument("--include-titles", action="store_true",
                        help="Explicitly include local task titles in JSON/Markdown (never HTML)")
    args = parser.parse_args(argv)
    if args.project and not PROJECT_RE.fullmatch(args.project):
        parser.error("--project must be a canonical lowercase project ID")
    if not 1 <= args.threshold <= 1000 or not 1 <= args.limit <= MAX_SAMPLE:
        parser.error("--threshold must be 1..1000; --limit must be 1..100")
    if args.include_titles and args.format == "html":
        parser.error("--include-titles is deliberately unavailable for HTML")
    try:
        report = read_snapshot(args.database, project=args.project,
                               threshold=args.threshold, limit=args.limit,
                               include_titles=args.include_titles)
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(f"Cannot read a consistent queue snapshot: {exc}", file=sys.stderr)
        return 2
    payload = (json.dumps(report, indent=2, ensure_ascii=False) + "\n"
               if args.format == "json" else
               markdown(report) if args.format == "markdown" else html_page(report))
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
