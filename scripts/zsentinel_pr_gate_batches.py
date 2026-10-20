#!/usr/bin/env python3
"""zSentinel offline merge-first desk: reconcile bounded PR gate receipt batches.

This is saved, historical GitHub observation, NOT a live authorization engine.
No GitHub, queue, runner, claim, workflow, reviewer or deployment writes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import stat
import sys

from zsentinel_pr_gate_now import (
    MAX_PRS, REVIEW_MAX_PRS, REVIEW_SCHEMA,
    SCHEMA as GATE_SCHEMA, read_review_manifest,
)

SCHEMA = "zsentinel-pr-gate-batch-reconciliation-v1"
MAX_RECEIPTS = (REVIEW_MAX_PRS + MAX_PRS - 1) // MAX_PRS
MAX_RECEIPT_BYTES = 1024 * 1024
HEAD = re.compile(r"^[a-f0-9]{40}$")
OBSERVED = "observed_current_head_partial_gate_evidence"
UNKNOWN = "github_source_unavailable_or_head_changed"
# Only the canonical live gate collector may supply a saved human action.
# Never render arbitrary instructions embedded in a local receipt as advice.
HUMAN_GATE_STEPS = frozenset({
    "inspect_incomplete_current_head_gate_source",
    "inspect_current_head_failed_checks_with_owner",
    "wait_for_existing_current_head_checks",
    "owner_address_exact_head_review_changes",
    "verify_missing_current_head_checks_and_rules",
    "request_independent_current_head_review",
    "verify_required_rules_ownership_and_release_separately",
})


def _load_regular(path: Path, max_bytes: int) -> dict:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 2 <= info.st_size <= max_bytes:
            raise ValueError("source must be a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(max_bytes + 1)
    finally:
        os.close(fd)
    if len(raw) > max_bytes:
        raise ValueError("oversized saved receipt")
    try:
        return json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, ValueError):
        raise ValueError("invalid UTF-8 saved JSON") from None


def _utc(value):
    if type(value) is not str or len(value) > 50:
        raise ValueError("invalid observation time")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid observation time") from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("observation time must include UTC offset")
    return moment.astimezone(timezone.utc)


def _gate_class(item: dict, observed: bool, old: bool) -> str:
    """Conservative saved triage: never represents merge clearance."""
    if not observed:
        return "source_unavailable"
    if old:
        return "saved_evidence_stale"
    if (item.get("check_runs_page_complete") is not True
            or item.get("legacy_status_source_may_be_truncated") is not False
            or item.get("review_page_may_be_truncated") is not False):
        return "source_incomplete"
    if (item["observed_checks_not_successful"] > 0
            or item.get("legacy_status_failure_or_error_observed", 0) > 0):
        return "observed_check_failure"
    if item.get("draft_at_read") is True:
        return "saved_draft"
    if item.get("observed_exact_head_changes_requested", 0) > 0:
        return "observed_review_changes"
    if (item["observed_checks_pending"] > 0
            or item.get("legacy_status_pending_observed", 0) > 0):
        return "observed_checks_waiting"
    if item["observed_independent_exact_head_approvals"] < 1:
        return "saved_review_needed"
    return "verify_live_branch_and_owner_rules"


def reconcile(manifest: Path, receipt_paths: list[Path], now=None) -> dict:
    if not 1 <= len(receipt_paths) <= MAX_RECEIPTS:
        raise ValueError("choose one to 75 separately created saved gate receipts")
    # Reuse the existing strict canonical reader. It pins manifest bytes and
    # deterministically sorts priority and PR identity in each 4-PR batch.
    first = read_review_manifest(manifest, batch=1)
    count = first["source_review_pr_count"]
    total = first["source_review_batches_total"]
    if total > (REVIEW_MAX_PRS + MAX_PRS - 1) // MAX_PRS:
        raise ValueError("invalid manifest batch count")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    batches = {}
    for path in receipt_paths:
        saved = _load_regular(path, MAX_RECEIPT_BYTES)
        if (type(saved) is not dict or saved.get("schema") != GATE_SCHEMA
                or saved.get("merge_authorized") is not False
                or saved.get("deployment_authorized") is not False
                or saved.get("writes_performed") is not False
                or saved.get("required_branch_protection_verified") is not False
                or saved.get("source_review_manifest_sha256") != first["source_review_manifest_sha256"]
                or saved.get("source_review_board_sha256") != first["source_review_board_sha256"]
                or saved.get("source_review_pr_count") != count
                or type(saved.get("source_review_batch_index")) is not int):
            raise ValueError("receipt identity or no-authority contract invalid")
        index = saved["source_review_batch_index"]
        if index in batches:
            raise ValueError("duplicate observed batch; refusing overwrite")
        if not 1 <= index <= total:
            raise ValueError("batch index exceeds original review")
        expected = read_review_manifest(manifest, batch=index)
        if expected["source_review_manifest_sha256"] != first["source_review_manifest_sha256"]:
            raise ValueError("review manifest changed during batch reconciliation")
        for key in ("source_review_batches_total", "source_review_batch_first_pr",
                    "source_review_batch_count", "review_only_top_four_observed"):
            if saved.get(key) != expected[key]:
                raise ValueError("receipt does not match manifest batch boundaries")
        items = saved.get("items")
        if (type(items) is not list or len(items) != expected["source_review_batch_count"]
                or saved.get("observed_prs") != len(items)):
            raise ValueError("missing or extra PR evidence rows")
        clock = _utc(saved.get("observed_at_utc"))
        if clock > observed_at.replace(microsecond=999999):
            raise ValueError("receipt observation in the future")
        age = int((observed_at - clock).total_seconds())
        if age < 0:
            age = 0
        rows = []
        for item, (repo, number) in zip(items, expected["prs"]):
            url = "https://github.com/" + repo + "/pull/" + str(number)
            if (type(item) is not dict or item.get("repository") != repo
                    or item.get("number") != number or item.get("url") != url
                    or item.get("merge_authorized") is not False
                    or item.get("deployment_authorized") is not False
                    or item.get("read_only") is not True
                    or item.get("status") not in (OBSERVED, UNKNOWN)):
                raise ValueError("receipt PR identity, ordering or authority invalid")
            observed = item["status"] == OBSERVED
            if observed and (
                type(item.get("head_sha_observed")) is not str
                or not HEAD.fullmatch(item["head_sha_observed"])
                or item.get("required_branch_checks_and_rules_verified") is not False
                or item.get("required_review_policy_verified") is not False
                or type(item.get("observed_checks_pending")) is not int
                or type(item.get("observed_checks_not_successful")) is not int
                or type(item.get("observed_independent_exact_head_approvals")) is not int
                or item.get("human_next_gate_step") not in HUMAN_GATE_STEPS
            ):
                raise ValueError("incomplete observed gate evidence")
            if observed:
                for key in (
                    "legacy_status_failure_or_error_observed",
                    "legacy_status_pending_observed",
                    "observed_exact_head_changes_requested",
                ):
                    if type(item.get(key)) is not int or item[key] < 0:
                        raise ValueError("invalid saved legacy or review gate count")
                for key in (
                    "check_runs_page_complete",
                    "legacy_status_source_may_be_truncated",
                    "review_page_may_be_truncated",
                    "draft_at_read",
                ):
                    if type(item.get(key)) is not bool:
                        raise ValueError("invalid saved source-coverage flag")
                if any(item[k] < 0 for k in (
                        "observed_checks_pending", "observed_checks_not_successful",
                        "observed_independent_exact_head_approvals")):
                    raise ValueError("invalid negative observed gate count")
            rows.append({
                "url": url,
                "exact_head_observed": item["head_sha_observed"] if observed else None,
                "source_status": "observed_partial" if observed else "unavailable_or_drifted",
                "checks_pending_observed": item["observed_checks_pending"] if observed else None,
                "checks_failed_observed": item["observed_checks_not_successful"] if observed else None,
                "independent_exact_head_approvals_observed": (
                    item["observed_independent_exact_head_approvals"] if observed else None),
                "next_human_gate": item["human_next_gate_step"] if observed else "recollect_exact_head_source",
                "historic_gate_category": _gate_class(item, observed, age > 900),
                "saved_source_stale_over_15_minutes": age > 900,
                "merge_authorized": False, "deployment_authorized": False,
            })
        batches[index] = {
            "batch": index, "observed_at_utc": clock.isoformat(),
            "age_seconds": age, "stale": age > 900, "prs": rows,
        }
    missing = [i for i in range(1, total + 1) if i not in batches]
    all_rows = [
        ((i - 1) * MAX_PRS + local_index + 1, row)
        for i in sorted(batches)
        for local_index, row in enumerate(batches[i]["prs"])
    ]
    unavailable = sum(row["source_status"] != "observed_partial"
                      for _, row in all_rows)
    stale = sum(row["saved_source_stale_over_15_minutes"] for _, row in all_rows)
    category_counts = {}
    for _, row in all_rows:
        category = row["historic_gate_category"]
        category_counts[category] = category_counts.get(category, 0) + 1
    # Saved observations are never atomic live GitHub state.
    batch_clocks = [_utc(batches[i]["observed_at_utc"]) for i in sorted(batches)]
    spread = int((max(batch_clocks) - min(batch_clocks)).total_seconds())
    source_window_coherent = spread <= 600
    # Existing manifest order is P0/P1-first and deduplicated.
    manual_queue = [
        {
            "manifest_position": pos,
            "url": row["url"],
            "historic_gate_category": row["historic_gate_category"],
            "human_next_gate": row["next_human_gate"],
            "exact_head_observed": row["exact_head_observed"],
            "receipt_old": row["saved_source_stale_over_15_minutes"],
            "current_head_verified": False,
            "owner_and_release_authorized": False,
            "merge_authorized": False,
        }
        for pos, row in all_rows
    ]
    first_actions = manual_queue[:24]
    return {
        "schema": SCHEMA,
        "generated_at_utc": observed_at.isoformat(),
        "source_review_manifest_sha256": first["source_review_manifest_sha256"],
        "source_review_board_sha256": first["source_review_board_sha256"],
        "source_review_schema": REVIEW_SCHEMA,
        "total_manifest_prs": count,
        "total_batches": total,
        "included_batches": sorted(batches),
        "missing_batches": missing,
        "observed_prs": len(all_rows),
        "unavailable_or_drifted_prs": unavailable,
        "stale_over_15_minutes_prs": stale,
        "saved_historic_gate_category_counts": category_counts,
        "cross_batch_observation_spread_seconds": spread,
        "cross_batch_observation_within_10_minutes": source_window_coherent,
        "manual_review_queue_preview": first_actions,
        "manual_review_queue_hidden_after_24": max(0, len(manual_queue) - len(first_actions)),
        "all_saved_batches_present": not missing,
        "all_observations_present_and_recent": (
            not missing and not unavailable and not stale and source_window_coherent),
        "saved_historical_receipts_only": True,
        "live_github_state_verified": False,
        "current_heads_revalidated": False,
        "required_branch_protection_verified": False,
        "independent_review_policy_verified": False,
        "claim_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "writes_performed": False,
        "batches": [batches[i] for i in sorted(batches)],
    }


def human(report, markdown=False):
    lines = [
        "# zSentinel · Merge-first gatebatches (opgeslagen momentopnamen)" if markdown
        else "zSentinel | Merge-first gatebatches · opgeslagen momentopnamen",
        "Bronboard: " + report["source_review_board_sha256"],
        "Review-manifest: " + report["source_review_manifest_sha256"],
        "Ontvangen: " + str(report["observed_prs"]) + "/" + str(report["total_manifest_prs"]) +
        " PRs, batches " + str(len(report["included_batches"])) +
        "/" + str(report["total_batches"]),
        "Ontbrekende batches: " + (", ".join(map(str, report["missing_batches"])) or "geen"),
        "Onbereikbare of veranderde HEADs: " + str(report["unavailable_or_drifted_prs"]),
        "Verouderde (>15 min) opgeslagen PR-waarnemingen: " + str(report["stale_over_15_minutes_prs"]),
        "Tijdverschil tussen bronbatches: " +
            str(report["cross_batch_observation_spread_seconds"]) +
            " seconden · onderlinge bron binnen 10 min: " +
            str(report["cross_batch_observation_within_10_minutes"]),
        "Historische gatecategorieën: " + ", ".join(
            key + "=" + str(value) for key, value in
            sorted(report["saved_historic_gate_category_counts"].items())),
        "Belangrijk: alle gegevens zijn historisch; beschermde main, exacte CI, "
        "review, actieve ownerclaim en release zijn NIET geverifieerd.",
    ]
    lines.append("")
    lines.append("## Eerst bekijken: menselijke merge-first werkbak" if markdown
                 else "Eerst bekijken: menselijke merge-first werkbak")
    for action in report["manual_review_queue_preview"]:
        link = ("[" + action["url"] + "](" + action["url"] + ")"
                if markdown else action["url"])
        lines.append("- " + str(action["manifest_position"]) + ". " + link +
                     " · [" + action["historic_gate_category"] + "] " +
                     action["human_next_gate"] + " · geen mergevrijgave")
    if report["manual_review_queue_hidden_after_24"]:
        lines.append(str(report["manual_review_queue_hidden_after_24"]) +
                     " extra PR's buiten compacte shortlist, zie volledige bronbatches.")
    for batch in report["batches"]:
        lines.append(("\n## " if markdown else "\n") + "Batch " + str(batch["batch"]) +
                     " · bron UTC " + batch["observed_at_utc"])
        for row in batch["prs"]:
            name = ("[" + row["url"] + "](" + row["url"] + ")") if markdown else row["url"]
            lines.append("- " + name + " · [" + row["historic_gate_category"] + "] " + row["next_human_gate"] +
                         (" · VEROUDERD (>15 min)" if row["saved_source_stale_over_15_minutes"] else "") +
                         " · MERGE NEE")
    lines.append("\nGeen claims, workflows, GitHub-writes, merge of deploy uitgevoerd.")
    return "\n".join(lines) + "\n"


def html(report):
    cards = []
    for batch in report["batches"]:
        items = []
        for row in batch["prs"]:
            url = escape(row["url"], quote=True)
            step = escape(row["next_human_gate"])
            category = escape(row["historic_gate_category"])
            head = escape(row["exact_head_observed"] or "onbekend")
            note = " · oud bronbewijs" if row["saved_source_stale_over_15_minutes"] else ""
            items.append('<li><a rel="noopener noreferrer" href="' + url + '">' + url +
                         '</a><br><small>HEAD ' + head + ' · [' + category + '] ' + step + escape(note) +
                         ' · geen mergevrijgave</small></li>')
        cards.append(
            "<details class='receipt-batch'><summary>Batch " +
            str(batch["batch"]) + " · " + str(len(batch["prs"])) +
            " PRs · " + ("bron ouder dan 15 minuten" if batch["stale"]
                        else "historische bron ≤15 minuten oud") +
            "</summary><ol>" + "".join(items) + "</ol></details>")
    summary = ("Batches " + str(len(report["included_batches"])) + "/" +
               str(report["total_batches"]) + " · PRs " +
               str(report["observed_prs"]) + "/" + str(report["total_manifest_prs"]) +
               " · niet waargenomen " + str(report["unavailable_or_drifted_prs"]) +
               " · verouderd " + str(report["stale_over_15_minutes_prs"]))
    missing = ", ".join(map(str, report["missing_batches"])) or "geen"
    queue_items = []
    for action in report["manual_review_queue_preview"]:
        url = escape(action["url"], quote=True)
        category = escape(action["historic_gate_category"])
        step = escape(action["human_next_gate"])
        queue_items.append("<li><a rel='noopener noreferrer' href='" + url + "'>" +
                           str(action["manifest_position"]) + ". " + url +
                           "</a><br><small>" + category + " · " + step +
                           " · HANDMATIG / GEEN MERGE</small></li>")
    queue_section = ("<section><h2>Eerst bekijken: menselijke PR-werkbak</h2>"
                     "<p>Prioriteit volgt het originele reviewmanifest. "
                     "Controleer actuele HEAD, eigenaar en vereiste rules live.</p><ol>" +
                     "".join(queue_items) + "</ol><p>" +
                     str(report["manual_review_queue_hidden_after_24"]) +
                     " extra PR's buiten de compacte voorvertoning.</p></section>")
    cats = ", ".join(escape(k) + ": " + str(v) for k, v in
                     sorted(report["saved_historic_gate_category_counts"].items()))
    return ('<!doctype html><html lang="nl"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>zSentinel · Merge-first gatebatches</title>'
            '<style>body{font:16px/1.55 system-ui;margin:2rem auto;max-width:840px;'
            'padding:0 1rem;color:#182534;background:#f7fafc}'
            'section,header{background:white;padding:1rem 1.5rem;margin:1rem 0;'
            'border:1px solid #d5dee9;border-radius:12px}'
            'details.receipt-batch{background:white;border:1px solid #d5dee9;'
            'border-radius:12px;margin:1rem 0;padding:1rem 1.5rem}'
            'summary{cursor:pointer;font-weight:650;list-style-position:inside}'
            'summary:focus-visible,a:focus-visible{outline:2px solid currentColor;outline-offset:3px}'
            'a{overflow-wrap:anywhere}li{margin:.9rem 0}small{color:#41546a}'
            '</style><header><h1>zSentinel · opgeslagen PR-gates</h1><p>' +
            escape(summary) + '</p><p>Ontbrekende batches: ' + escape(missing) +
            '</p><p>Historische gates: ' + cats +
            '</p><p>Tijdverschil tussen bronbatches: ' +
            str(report["cross_batch_observation_spread_seconds"]) +
            ' seconden · binnen tien minuten: ' +
            str(report["cross_batch_observation_within_10_minutes"]) +
            '</p><strong>GEEN merge-, claim-, deploy- of reviewvrijgave. '
            'Bron is niet live.</strong></header>' + queue_section + "".join(cards) +
            '<p>Geen GitHub- of runtime-mutaties uitgevoerd.</p></html>\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-review", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, action="append", required=True)
    parser.add_argument("--format", choices=("html", "json", "text", "markdown"),
                        default="html")
    parser.add_argument("--output", type=Path,
                        help="Exclusive NEW private 0600 output; default stdout")
    args = parser.parse_args(argv)
    try:
        report = reconcile(args.from_review, args.receipt)
        output = (json.dumps(report, indent=2, ensure_ascii=True) + "\n"
                  if args.format == "json" else html(report) if args.format == "html"
                  else human(report, markdown=args.format == "markdown"))
        if args.output is None:
            sys.stdout.write(output)
        else:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(args.output, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(output)
                stream.flush()
                os.fsync(stream.fileno())
            print("Private saved receipt overview created (0600). No live authority.",
                  file=sys.stderr)
        return 3 if (report["missing_batches"] or report["unavailable_or_drifted_prs"]
                     or report["stale_over_15_minutes_prs"]
                     or not report["cross_batch_observation_within_10_minutes"]) else 0
    except (OSError, ValueError):
        print("zSentinel merge-first batch reconciliation refused: saved source invalid or unavailable",
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
