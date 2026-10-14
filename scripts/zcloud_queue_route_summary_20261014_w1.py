#!/usr/bin/env python3
"""Print a bounded, human-readable, observation-only queue route summary."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts import zcloud_queue_route_triage_20261014_w1 as triage
except ModuleNotFoundError:
    import zcloud_queue_route_triage_20261014_w1 as triage

_MAX_BYTES = 32768
_MAX_REPORT_AGE_SECONDS = 60
_MAX_GITHUB_ID = (1 << 63) - 1
_SCOPES = {"current_main", "historical_main", "other_branch", "foreign_repository"}
_ROUTES = {"no_job_records", "github_hosted", "zcloud_vps", "ftmo_research",
           "other_or_mixed", "mixed_job_routes"}
_ASSIGNMENT = {"no_job_records", "not_assigned", "assigned",
               "indeterminate", "mixed_assignment"}
_STATES = {
    "queued", "in_progress", "completed", "waiting", "requested", "pending",
    "no_job_records", "mixed_job_states",
}
_SCOPE_NL = {
    "current_main": "huidige hoofdbranch",
    "historical_main": "eerdere hoofdbranch",
    "other_branch": "andere branch",
    "foreign_repository": "externe bron",
}
_ROUTE_NL = {
    "no_job_records": "geen jobgegevens",
    "github_hosted": "GitHub-hosted route",
    "zcloud_vps": "zCloud-VPS-route",
    "ftmo_research": "FTMO-onderzoeksroute",
    "other_or_mixed": "onbekende route",
    "mixed_job_routes": "verschillende jobroutes",
}
_ASSIGNMENT_NL = {
    "no_job_records": "geen jobgegevens",
    # Also applies to completed/skipped jobs: do not imply that a runner
    # is still being awaited when GitHub merely has no runner recorded.
    "not_assigned": "geen runner geregistreerd",
    "assigned": "runner toegewezen",
    "indeterminate": "runnertoewijzing onzeker",
    "mixed_assignment": "gemengde runnertoewijzing",
}
_STATE_NL = {
    "queued": "wachtend",
    "no_job_records": "geen jobgegevens",
    "in_progress": "bezig met uitvoeren",
    "completed": "al afgerond",
    "waiting": "wacht op volgende stap",
    "requested": "uitvoering aangevraagd",
    "pending": "in behandeling",
    "mixed_job_states": "jobs in verschillende fasen",
}


def _unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("duplicate_json_keys")
        result[k] = v
    return result


def safe_summary(raw: bytes, collector_rc: int, *, now=None) -> tuple[str, int]:
    """Reject stale/future source timestamps before printing any observations."""
    if now is None:
        now = datetime.now(timezone.utc)
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
            or data.get("reason") != "read_only_route_counts_not_runner_liveness"
            or data.get("mutation_authorized") is not False
        ):
            raise ValueError("unauthorized_or_invalid")
        # This remains a source-local claim, not cryptographic attestation.
        # Nevertheless reject reports that contradict the collector's stated
        # canonical main SHA before presenting route observations.
        before = data.get("main_sha_before")
        after = data.get("main_sha_after")
        observed_at = data.get("observed_at")
        if type(observed_at) is not str:
            raise ValueError("invalid_observation_time")
        try:
            parsed_at = datetime.strptime(observed_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise ValueError("invalid_observation_time") from exc
        if parsed_at.strftime("%Y-%m-%dT%H:%M:%SZ") != observed_at:
            raise ValueError("noncanonical_observation_time")
        if (
            not isinstance(now, datetime) or now.tzinfo is None
            or now.utcoffset() is None
            or now.utcoffset().total_seconds() != 0
        ):
            raise ValueError("invalid_current_clock")
        age = (now - parsed_at.replace(tzinfo=timezone.utc)).total_seconds()
        if not 0 <= age <= _MAX_REPORT_AGE_SECONDS:
            raise ValueError("stale_or_future_report")
        if (
            type(before) is not str or len(before) != 40 or
            any(c not in "0123456789abcdef" for c in before)
            or type(after) is not str or after != before
            or type(data.get("observed_at")) is not str
            or not data["observed_at"].endswith("Z")
            or len(data["observed_at"]) > 40
        ):
            raise ValueError("invalid_source_identity")
        rows = data.get("runs")
        n = data.get("observed_runs")
        summary = data.get("route_counts")
        if type(n) is not int or not 0 <= n <= 40 or type(rows) is not list or len(rows) != n:
            raise ValueError("inconsistent_counts")
        if type(summary) is not dict:
            raise ValueError("missing_counts")
        # JSON true compares equal to Python integer 1. Never let a boolean
        # bucket pass a numeric equality check even when the rendered summary
        # later derives its own counts from validated run rows.
        if any(type(key) is not str or type(value) is not int or value <= 0
               for key, value in summary.items()):
            raise ValueError("invalid_bucket_count_types")
        computed = {}
        seen = set()
        rendered = []
        for row in rows:
            if type(row) is not dict:
                raise ValueError("invalid_row")
            number = row.get("run_id")
            if type(number) is not int or not 0 < number <= _MAX_GITHUB_ID or number in seen:
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
            empty = row["job_count"] == 0
            # The route, assignment and status must agree about missing job
            # records. A synthetic internally consistent bucket is not proof
            # of real GitHub evidence, but a contradictory bucket is NEVER
            # safe to publish as an observed operator count.
            if (
                (row["route"] == "no_job_records") != empty
                or (row["assignment"] == "no_job_records") != empty
                or (row["job_state"] == "no_job_records") != empty
                or (not empty and row["route"] == "github_hosted"
                    and row["assignment"] == "no_job_records")
            ):
                raise ValueError("contradictory_job_route")
            bucket = f'{row["scope"]}/{row["route"]}/{row["assignment"]}/{row["job_state"]}'
            computed[bucket] = computed.get(bucket, 0) + 1
            rendered.append(
                f'- [Run {number}](https://github.com/Zennay/zCloud/actions/runs/{number}): '
                f'{_SCOPE_NL[row["scope"]]} · {_ROUTE_NL[row["route"]]} · '
                f'{_ASSIGNMENT_NL[row["assignment"]]} · {_STATE_NL[row["job_state"]]}.'
            )
        if summary != computed:
            raise ValueError("group_counts_disagree")
        lines = [intro, f"- Uitkomst: **ALLEEN OBSERVATIE**; {n} runs teruggekregen.\n"]
        if n == 0:
            lines.append("- GitHub gaf geen wachtende runs terug op het moment van uitlezen.\n")
        else:
            # Numeric totals come only from the same rigorously checked rows,
            # never from upstream free text or self-declared summary fields.
            no_jobs = sum(row["route"] == "no_job_records" for row in rows)
            ftmo = sum(row["route"] == "ftmo_research" for row in rows)
            zcloud = sum(row["route"] == "zcloud_vps" for row in rows)
            # One summary row represents one GitHub run, not the number of
            # jobs inside it. Count only runs whose known jobs are all still
            # queued and unassigned, never active/completed/approval-pending.
            unassigned = sum(
                row["assignment"] == "not_assigned"
                and row["job_state"] == "queued" for row in rows
            )
            current = sum(row["scope"] == "current_main" for row in rows)
            lines.append(
                "- Routes: "
                f"**{ftmo} FTMO-onderzoek**, **{zcloud} zCloud-VPS**, "
                f"**{no_jobs} zonder jobrecords**; "
                f"**{unassigned} wachtende runs zonder runnertoewijzing**, "
                f"**{current} op huidige main**.\n"
            )
            lines.append(
                "- Een job zonder runnertoewijzing bewijst **niet** dat een listener defect is; "
                "een run zonder jobrecord is evenmin een veilige annuleringskandidaat.\n"
            )
            # Source-local route distinctions yield one operator next step,
            # never a live restart/cancel/deploy instruction or runner verdict.
            current_vps_unassigned = sum(
                row["scope"] == "current_main" and row["route"] == "zcloud_vps"
                and row["assignment"] == "not_assigned"
                and row["job_state"] == "queued" for row in rows
            )
            ftmo_unassigned = sum(
                row["route"] == "ftmo_research"
                and row["assignment"] == "not_assigned"
                and row["job_state"] == "queued" for row in rows
            )
            if current_vps_unassigned:
                lines.append(
                    "- Controlepunt: bekijk de zCloud-runnerlabels en repositorytoegang "
                    "voor wachtende jobs op de huidige hoofdbranch. "
                    "Geen bewijs dat de listener offline is.\n"
                )
            elif ftmo_unassigned:
                lines.append(
                    "- Controlepunt: onderzoek afzonderlijk de FTMO-runnerroute "
                    "en repositorytoegang; leid hieruit geen zCloud-VPS-storing af.\n"
                )
            elif no_jobs:
                lines.append(
                    "- Controlepunt: inspecteer de workflowvoorwaarden van runs "
                    "zonder jobgegevens voordat een runnerstoring wordt vermoed.\n"
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
