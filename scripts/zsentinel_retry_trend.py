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
    return {
        "schema": "zsentinel-retry-trend-v1",
        "before_utc": previous["observed_at_utc"],
        "after_utc": current["observed_at_utc"],
        "scope": current["scope"],
        "metrics": metrics,
        "projects": groups,
        "worsening_projects": sum(g["delta"] > 0 for g in groups),
        "non_authorizing": True,
        "notes": (
            "Claim attempts are claim cycles, not confirmed failures. Changes in totals "
            "may reflect arrivals, completions, priority changes or status transitions. "
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
<footer>Offline, zero-network, no queue writes or authorization.</footer></body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--format", choices=("json", "markdown", "html"), default="html")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = compare(read(args.previous), read(args.current))
        content = (json.dumps(result, indent=2, ensure_ascii=False) + "\n"
                   if args.format == "json" else
                   markdown(result) if args.format == "markdown" else html(result))
        if args.output:
            args.output.write_text(content, encoding="utf-8")
        else:
            print(content)
    except (ValueError, OSError) as exc:
        print("Trend not produced: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
