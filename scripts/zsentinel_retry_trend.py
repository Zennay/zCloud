#!/usr/bin/env python3
"""Compare two saved zSentinel retry console JSON snapshots without opening a live DB.

This is a source-only incident trend report for #677; no retry authority is granted.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
import json
from pathlib import Path
import re
import sys

# Works both as python3 scripts/zsentinel_retry_trend.py and as
# import scripts.zsentinel_retry_trend from the repository root.
if __package__:
    from .zsentinel_retry_incident_console import write_new_private_report
else:
    from zsentinel_retry_incident_console import write_new_private_report

SCHEMA = "zsentinel-retry-incident-v1"
PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
METRICS = ("active_items", "high_attempt_items", "ten_plus_items",
           "expired_active_leases", "max_claim_attempts")


def read(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError("Snapshot is missing or larger than 1 MB")
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Snapshot must be valid UTF-8 JSON") from exc
    if not isinstance(item, dict) or item.get("schema") != SCHEMA:
        raise ValueError("Unrecognized snapshot schema")
    if not isinstance(item.get("totals"), dict):
        raise ValueError("Snapshot totals missing")
    if not isinstance(item.get("project_groups"), list) or len(item["project_groups"]) > 2000:
        raise ValueError("Snapshot project groups invalid or too large")
    if not isinstance(item.get("scope"), dict):
        raise ValueError("Snapshot scope missing")
    for metric in METRICS:
        val = item["totals"].get(metric)
        if type(val) is not int or val < 0:
            raise ValueError("Snapshot contains invalid total: " + metric)
    try:
        observed = datetime.fromisoformat(item["observed_at_utc"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Snapshot has invalid observed time") from exc
    if observed.tzinfo is None:
        raise ValueError("Snapshot observed time must be timezone-aware")
    return item


def project_counts(item: dict) -> dict[str, int]:
    result: dict[str, int] = {}
    for group in item["project_groups"]:
        if not isinstance(group, dict):
            raise ValueError("Malformed project group")
        name, count = group.get("project_id"), group.get("high_attempts")
        if (not isinstance(name, str) or not PROJECT.fullmatch(name)
                or type(count) is not int or count < 0):
            raise ValueError("Malformed project name or count")
        result[name] = result.get(name, 0) + count
    return result



def sample_changes(previous: dict, current: dict) -> tuple[dict, list[dict]]:
    """Compare only records appearing in BOTH bounded samples, never imply disappearance.

    queue_ref is a truncated hash, not a credential, row ownership token or proof
    that a record is unchanged outside these two observed read transactions.
    """
    def extract(item: dict) -> dict[str, dict]:
        coverage = item.get("coverage")
        rows = item.get("high_attempt_samples")
        if (not isinstance(coverage, dict)
                or type(coverage.get("sample_limit")) is not int
                or not 1 <= coverage["sample_limit"] <= 100
                or type(coverage.get("sample_is_complete")) is not bool
                or coverage.get("database_read_only") is not True
                or coverage.get("safe_to_authorize_retry") is not False
                or coverage.get("attempts_count_claims_not_failures") is not True
                or not isinstance(rows, list)
                or len(rows) > coverage["sample_limit"]):
            raise ValueError("Incomplete or unsafe queue sample contract")
        indexed: dict[str, dict] = {}
        raw_scope = item.get("scope")
        if not isinstance(raw_scope, dict) or not isinstance(raw_scope.get("project"), str):
            raise ValueError("Missing queue snapshot scope")
        scope = raw_scope["project"]
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Malformed queue sample")
            ref = row.get("queue_ref")
            name = row.get("project")
            if (not isinstance(ref, str)
                    or not re.fullmatch(r"[0-9a-f]{16}", ref)
                    or ref in indexed
                    or not isinstance(name, str) or not PROJECT.fullmatch(name)
                    or scope != "all" and scope != name
                    or row.get("priority") not in ("P0", "P1", "P2", "P3")
                    or row.get("status") not in ("queued", "claimed", "running", "verifying")
                    or type(row.get("attempts")) is not int or row["attempts"] < 0
                    or type(row.get("eligible")) is not bool):
                raise ValueError("Malformed, duplicate or out-of-scope queue sample")
            indexed[ref] = row
        return indexed

    before = extract(previous)
    after = extract(current)
    common = before.keys() & after.keys()
    rows: list[dict] = []
    for ref in common:
        old, new = before[ref], after[ref]
        if old["project"] != new["project"]:
            raise ValueError("Queue reference changed project between snapshots")
        delta = new["attempts"] - old["attempts"]
        if delta > 0:
            kind = "claim_cycles_increased"
        elif delta < 0:
            kind = "claim_counter_decreased_review"
        elif old["status"] != new["status"]:
            kind = "status_changed"
        elif old["priority"] != new["priority"]:
            kind = "priority_changed"
        else:
            continue
        rows.append({
            "queue_ref": ref, "project": new["project"],
            "priority_before": old["priority"], "priority_after": new["priority"],
            "status_before": old["status"], "status_after": new["status"],
            "attempts_before": old["attempts"], "attempts_after": new["attempts"],
            "claim_cycle_delta": delta, "observation": kind,
        })
    rows.sort(key=lambda r: (
        {"P0": 0, "P1": 1, "P2": 2, "P3": 3}[r["priority_after"]],
        -r["claim_cycle_delta"], r["project"], r["queue_ref"],
    ))
    coverage = {
        "previous_sampled": len(before),
        "current_sampled": len(after),
        "matched_samples": len(common),
        "only_previous_sample": len(before.keys() - after.keys()),
        "only_current_sample": len(after.keys() - before.keys()),
        "both_samples_complete": (
            previous["coverage"]["sample_is_complete"]
            and current["coverage"]["sample_is_complete"]),
        "matched_rows_with_increased_claim_cycles": sum(r["claim_cycle_delta"] > 0 for r in rows),
        "known_increase_in_claim_cycles": sum(max(0, r["claim_cycle_delta"]) for r in rows),
        "not_in_both_samples_is_not_completion": True,
    }
    return coverage, rows

def compare(previous: dict, current: dict) -> dict:
    if previous["scope"] != current["scope"]:
        raise ValueError("Snapshot project/threshold scopes differ")
    if datetime.fromisoformat(current["observed_at_utc"]) <= datetime.fromisoformat(previous["observed_at_utc"]):
        raise ValueError("Current snapshot is not newer than previous")
    earlier = project_counts(previous)
    later = project_counts(current)
    groups = [{
        "project": project,
        "before_high_attempts": earlier.get(project, 0),
        "after_high_attempts": later.get(project, 0),
        "delta": later.get(project, 0) - earlier.get(project, 0),
    } for project in sorted(set(earlier) | set(later))]
    groups.sort(key=lambda g: (-g["delta"], g["project"]))
    metrics = {
        key: {
            "before": previous["totals"][key], "after": current["totals"][key],
            "delta": current["totals"][key] - previous["totals"][key],
        } for key in METRICS
    }
    observation_coverage, record_changes = sample_changes(previous, current)
    return {
        "schema": "zsentinel-retry-trend-v1",
        "before_utc": previous["observed_at_utc"],
        "after_utc": current["observed_at_utc"],
        "scope": current["scope"],
        "metrics": metrics,
        "projects": groups,
        "sample_coverage": observation_coverage,
        "record_changes": record_changes,
        "worsening_projects": sum(g["delta"] > 0 for g in groups),
        "non_authorizing": True,
        "notes": (
            "Claim attempts are claim cycles, not confirmed failures. Changes in totals "
            "may reflect arrivals, completions, priority changes or status transitions. "
            "Record deltas are observed only when the same pseudonymous ref is in BOTH bounded "
            "samples. Disappearing from a sample does not establish job completion. "
            "Do not infer worker ownership, retry permission or recovery from this comparison."
        ),
    }


def markdown(trend: dict) -> str:
    lines = [
        "# zSentinel · Retry incident trend",
        "",
        "Comparison is source-only and non-authorizing.",
        "Earlier: " + trend["before_utc"],
        "Later: " + trend["after_utc"],
        "Scope: " + json.dumps(trend["scope"], ensure_ascii=False),
        "",
        "## Queue movement",
    ]
    for name, value in trend["metrics"].items():
        lines.append("- " + name.replace("_", " ") + ": "
                     + str(value["before"]) + " → " + str(value["after"])
                     + " (" + format(value["delta"], "+d") + ")")
    lines += ["", "## Project movement",
              "| Project | High-attempt before | After | Delta |",
              "|---|---:|---:|---:|"]
    for group in trend["projects"]:
        lines.append("| " + group["project"] + " | "
                     + str(group["before_high_attempts"]) + " | "
                     + str(group["after_high_attempts"]) + " | "
                     + format(group["delta"], "+d") + " |")
    cover = trend["sample_coverage"]
    lines += ["", "## Matched queue-item movement (read-only)",
              f"Matched {cover['matched_samples']} of {cover['previous_sampled']} earlier and "
              f"{cover['current_sampled']} later sampled items; complete samples: "
              f"{cover['both_samples_complete']}.",
              f"Observed increased claim cycles: {cover['known_increase_in_claim_cycles']} "
              f"across {cover['matched_rows_with_increased_claim_cycles']} matched items.",
              f"Only in earlier sample: {cover['only_previous_sample']}; only in later "
              f"sample: {cover['only_current_sample']} (neither proves completion or arrival).",
              "", "| Opaque ref | Project | Priority | Status before → after | Claim cycles before → after | Change |",
              "|---|---|---|---|---|---|"]
    for row in trend["record_changes"][:100]:
        lines.append(f"| `{row['queue_ref']}` | {row['project']} | {row['priority_after']} | "
                     f"{row['status_before']} → {row['status_after']} | "
                     f"{row['attempts_before']} → {row['attempts_after']} "
                     f"({row['claim_cycle_delta']:+d}) | {row['observation']} |")
    lines += ["", trend["notes"], ""]
    return "\n".join(lines)


def html(trend: dict) -> str:
    def cells(*values: object) -> str:
        return "".join("<td>" + escape(str(value)) + "</td>" for value in values)
    metric_rows = "".join(
        "<tr>" + cells(name.replace("_", " "), *[row[k] for k in ("before", "after", "delta")]) + "</tr>"
        for name, row in trend["metrics"].items()
    )
    project_rows = "".join(
        "<tr>" + cells(g["project"], g["before_high_attempts"],
                       g["after_high_attempts"], g["delta"]) + "</tr>"
        for g in trend["projects"]
    )
    coverage = trend["sample_coverage"]
    record_rows = "".join(
        "<tr>" + cells(r["queue_ref"], r["project"], r["priority_after"],
                         r["status_before"] + " → " + r["status_after"],
                         f"{r['attempts_before']} → {r['attempts_after']} ({r['claim_cycle_delta']:+d})",
                         r["observation"]) + "</tr>"
        for r in trend["record_changes"][:100]
    )
    summary = (
        f"Matched {coverage['matched_samples']} sampled items. "
        f"Observed {coverage['known_increase_in_claim_cycles']} additional claim cycles "
        f"across {coverage['matched_rows_with_increased_claim_cycles']} matched items. "
        f"Both samples complete: {coverage['both_samples_complete']}. "
        f"Earlier-only {coverage['only_previous_sample']}; later-only "
        f"{coverage['only_current_sample']}; neither means completed or new."
    )
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>zSentinel · Retry incident trend</title><style>
body{margin:0 auto;max-width:960px;padding:1rem;font:16px/1.5 system-ui,sans-serif;background:#101827;color:#edf1fc}
h1{font-size:1.6rem}p{color:#c4d2e7}.wrap{overflow-x:auto}table{width:100%;border-collapse:collapse}
th,td{padding:.7rem;border-bottom:1px solid #41516a;text-align:left}th{color:#9fd9f9}
.warn{padding:.8rem;background:#302b25;border-left:4px solid #ddb25b}</style></head><body>
<h1>zSentinel · Retry incident trend</h1><p>""" + escape(trend["before_utc"]) + " → " + escape(trend["after_utc"]) + """</p>
<p class="warn">""" + escape(trend["notes"]) + """</p><h2>Queue movement</h2>
<div class="wrap"><table><tr><th>Metric</th><th>Earlier</th><th>Later</th><th>Delta</th></tr>""" + metric_rows + """</table></div>
<h2>Project movement</h2><div class="wrap"><table><tr><th>Project</th>
<th>Earlier high-attempt</th><th>Later</th><th>Delta</th></tr>""" + project_rows + """</table></div>
<h2>Matched queue-item movement</h2><p>""" + escape(summary) + """</p>
<div class="wrap"><table><tr><th>Opaque ref</th><th>Project</th><th>Priority</th>
<th>Status before → after</th><th>Claim cycles before → after</th><th>Observation</th></tr>""" + record_rows + """</table></div>
<footer>Offline, zero-network, no queue writes or authorization.</footer></body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--format", choices=("json", "markdown", "html"), default="html")
    parser.add_argument("--output", type=Path,
                        help="Create a NEW private trend report; existing snapshots are never overwritten")
    args = parser.parse_args(argv)
    try:
        result = compare(read(args.previous), read(args.current))
        content = (json.dumps(result, indent=2, ensure_ascii=False) + "\n"
                   if args.format == "json" else
                   markdown(result) if args.format == "markdown" else html(result))
        if args.output:
            write_new_private_report(args.output, content)
        else:
            print(content)
    except FileExistsError:
        print("Trend not produced: output already exists. Select a new filename "
              "rather than overwriting an input snapshot or previous report.",
              file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print("Trend not produced: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
