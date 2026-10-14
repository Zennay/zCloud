#!/usr/bin/env python3
"""Print a bounded, human-readable, observation-only queue route summary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from scripts import zcloud_queue_route_triage_20261014_w1 as triage
except ModuleNotFoundError:
    import zcloud_queue_route_triage_20261014_w1 as triage

_MAX_BYTES = 32768
_SCOPES = {"current_main", "historical_main", "other_branch", "foreign_repository"}
_ROUTES = {"no_job_records", "github_hosted", "zcloud_vps", "ftmo_research",
           "other_or_mixed", "mixed_job_routes"}
_ASSIGNMENT = {"no_job_records", "not_assigned", "assigned",
               "indeterminate", "mixed_assignment"}
_STATES = {"queued", "no_job_records", "mixed_or_active"}


def _unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("duplicate_json_keys")
        result[k] = v
    return result


def safe_summary(raw: bytes, collector_rc: int) -> tuple[str, int]:
    intro = "### zCloud GitHub-wachtrij — alleen leestoegang\n"
    disclaimer = (
        "- **Geen bevoegdheid om runs te annuleren of runners/workers te herstarten.**\n"
        "- **Geen bewijs van productiestatus, runner-beschikbaarheid of AI-generatie.**\n"
    )
    denied = intro + "- Uitkomst: **ONBETROUWBAAR / NIET COMPLEET**. Geen aantallen gepubliceerd.\n" + disclaimer
    try:
        if type(raw) is not bytes or not 0 < len(raw) < _MAX_BYTES:
            raise ValueError("invalid_report_size")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        if (
            type(data) is not dict or data.get("policy") != triage.IDENTITY
            or data.get("decision") != "OBSERVED" or type(collector_rc) is not int
            or collector_rc != 0 or data.get("runner_recovery_authorized") is not False
            or data.get("production_verified") is not False
            or data.get("worker_generation_verified") is not False
            or data.get("cancel_run_ids") != []
        ):
            raise ValueError("unauthorized_or_invalid")
        rows = data.get("runs")
        n = data.get("observed_runs")
        summary = data.get("route_counts")
        if type(n) is not int or not 0 <= n <= 40 or type(rows) is not list or len(rows) != n:
            raise ValueError("inconsistent_counts")
        if type(summary) is not dict:
            raise ValueError("missing_counts")
        computed = {}
        seen = set()
        rendered = []
        for row in rows:
            if type(row) is not dict:
                raise ValueError("invalid_row")
            number = row.get("run_id")
            if type(number) is not int or number <= 0 or number in seen:
                raise ValueError("invalid_run_id")
            seen.add(number)
            if (
                row.get("scope") not in _SCOPES or type(row.get("scope")) is not str
                or row.get("route") not in _ROUTES or type(row.get("route")) is not str
                or row.get("assignment") not in _ASSIGNMENT or type(row.get("assignment")) is not str
                or row.get("job_state") not in _STATES or type(row.get("job_state")) is not str
                or type(row.get("job_count")) is not int
                or not 0 <= row["job_count"] <= 100
                or row.get("url") != f"https://github.com/Zennay/zCloud/actions/runs/{number}"
            ):
                raise ValueError("invalid_route_claim")
            if (row["route"] == "no_job_records") != (row["job_count"] == 0):
                raise ValueError("job_count_disagrees")
            bucket = f'{row["scope"]}/{row["route"]}/{row["assignment"]}/{row["job_state"]}'
            computed[bucket] = computed.get(bucket, 0) + 1
            rendered.append(
                f'- [Run {number}](https://github.com/Zennay/zCloud/actions/runs/{number}): '
                f'{row["scope"]} · {row["route"]} · {row["assignment"]} · {row["job_state"]}.'
            )
        if summary != computed:
            raise ValueError("group_counts_disagree")
        lines = [intro, f"- Uitkomst: **ALLEEN OBSERVATIE**; {n} runs teruggekregen.\n"]
        if n == 0:
            lines.append("- GitHub gaf geen wachtende runs terug op het moment van uitlezen.\n")
        else:
            lines.append(
                "- Een job zonder runnertoewijzing bewijst **niet** dat een listener defect is; "
                "een run zonder jobrecord is evenmin een veilige annuleringskandidaat.\n"
            )
            lines += [line + "\n" for line in rendered]
        lines.append(disclaimer)
        return "".join(lines), 0
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
        return denied, 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--collector-exit", required=True, type=int)
    parser.add_argument("--summary-path", required=True, type=Path)
    args = parser.parse_args()
    try:
        with args.report.open("rb") as stream:
            raw = stream.read(_MAX_BYTES + 1)
    except OSError:
        raw = b""
    markdown, status = safe_summary(raw, args.collector_exit)
    with args.summary_path.open("a", encoding="utf-8") as stream:
        stream.write(markdown)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
