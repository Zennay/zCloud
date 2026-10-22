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
from zsentinel_private_report_writer import write_private_report
import re
import stat
import sys

from zsentinel_pr_gate_now import (
    MAX_PRS, REVIEW_MAX_PRS, REVIEW_SCHEMA,
    SCHEMA as GATE_SCHEMA, read_review_manifest,
    GATE_STEPS_NL as HUMAN_GATE_LABELS_NL,
)

SCHEMA = "zsentinel-pr-gate-batch-reconciliation-v1"
MAX_RECEIPTS = (REVIEW_MAX_PRS + MAX_PRS - 1) // MAX_PRS
MAX_RECEIPT_BYTES = 1024 * 1024
HEAD = re.compile(r"^[a-f0-9]{40}$")
OBSERVED = "observed_current_head_partial_gate_evidence"
UNKNOWN = "github_source_unavailable_or_head_changed"
# Only the canonical live gate collector may supply a saved human action.
# Never render arbitrary instructions embedded in a local receipt as advice.
HUMAN_GATE_STEPS = frozenset(HUMAN_GATE_LABELS_NL)

# Saved observations can guide a human without inventing current readiness.
# Distinguish source-refresh, real owner work, review requests and external
# waits so a large portfolio does not strand actionable defects behind CI.
OWNER_LANES = {
    "refresh_source": "Bron vernieuwen: huidige PR/HEAD opnieuw lezen",
    "fix_source": "Actie eigenaar: bestaande productcode of gate herstellen",
    "inspect_ci": "CI/runner-toewijzing onderzoeken zonder automatische retry",
    "request_review": "Onafhankelijke reviewer aanvragen",
    "external_wait": "Externe CI/review afhankelijkheid parkeren",
    "release_handoff": "Bevoegde release-eigenaar laat geldende gates controleren",
}
OWNER_LANE_ORDER = tuple(OWNER_LANES)


def _owner_lane(category):
    if category in ("source_unavailable", "saved_evidence_stale", "source_incomplete"):
        return "refresh_source"
    if category in ("saved_draft", "observed_merge_conflict", "observed_check_failure",
                    "observed_review_changes"):
        return "fix_source"
    if category == "observed_slow_ci_owner_triage":
        return "inspect_ci"
    if category == "saved_review_needed":
        return "request_review"
    if category in ("observed_checks_waiting", "saved_review_requested_waiting"):
        return "external_wait"
    return "release_handoff"


def _load_regular(path: Path, max_bytes: int) -> dict:
    """Read one private, stable saved receipt without blocking on pipes.

    The operator's offline gate bundles may include sensitive PR/claim
    context. Treat all paths and saved JSON as untrusted: the old open-before-
    fstat reader could wait forever on a FIFO and had no race/owner checks.
    """
    if not isinstance(path, Path):
        raise ValueError("private receipt path must be explicit")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise ValueError("safe private receipt opening unavailable")
    before = path.lstat()
    def identity(meta):
        return (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns)
    if (not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_mode & 0o077
            or not 2 <= before.st_size <= max_bytes):
        raise ValueError("saved receipt must be private, owned, regular and bounded")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode)
                or meta.st_uid != os.geteuid() or meta.st_mode & 0o077
                or identity(meta) != identity(before)):
            raise ValueError("saved receipt changed before read")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(max_bytes + 1)
        after = os.fstat(fd)
        try:
            path_after = path.lstat()
        except OSError:
            raise ValueError("saved receipt disappeared during read") from None
        if (len(raw) != meta.st_size
                or identity(meta) != identity(after)
                or identity(meta) != identity(path_after)
                or after.st_mode & 0o077 or path_after.st_mode & 0o077
                or after.st_uid != os.geteuid()
                or path_after.st_uid != os.geteuid()
                or not stat.S_ISREG(path_after.st_mode)):
            raise ValueError("saved receipt changed during read")
    finally:
        os.close(fd)

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate saved receipt JSON key")
            result[key] = value
        return result

    def reject_nonfinite(value):
        raise ValueError("non-finite receipt JSON value")

    try:
        parsed = json.loads(raw.decode("utf-8", "strict"),
                            object_pairs_hook=unique_fields,
                            parse_constant=reject_nonfinite)
        # Escaped lone surrogates cannot encode back to canonical UTF-8.
        json.dumps(parsed, ensure_ascii=False).encode("utf-8", "strict")
        if type(parsed) is not dict:
            raise ValueError("saved receipt JSON must be an object")
        return parsed
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid or ambiguous private saved receipt JSON") from None


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


def _valid_branch_ref(value):
    return bool(
        type(value) is str and 1 <= len(value) <= 255
        and value == value.strip() and not value.startswith("/")
        and not value.endswith("/") and "//" not in value and ".." not in value
        and "@{" not in value
        and not any(not char.isprintable() or char.isspace()
                    or char in ("~", "^", ":", "?", "*", "[", "\\")
                    for char in value)
        and not any(not part or part.startswith(".") or part.endswith(".")
                    or part.lower().endswith(".lock")
                    for part in value.split("/"))
    )


def _gate_class(item: dict, observed: bool, old: bool) -> str:
    """Conservative saved triage: never represents merge clearance."""
    if not observed:
        return "source_unavailable"
    if old:
        return "saved_evidence_stale"
    if item.get("draft_at_read") is True:
        return "saved_draft"
    if item.get("merge_conflict_observed") is True:
        return "observed_merge_conflict"
    if (item.get("check_runs_page_complete") is not True
            or item.get("legacy_status_source_may_be_truncated") is not False
            or item.get("review_page_may_be_truncated") is not False
            or not _valid_branch_ref(item.get("base_ref_observed"))):
        return "source_incomplete"
    if (item["observed_checks_not_successful"] > 0
            or item.get("legacy_status_failure_or_error_observed", 0) > 0):
        return "observed_check_failure"
    if item.get("observed_exact_head_changes_requested", 0) > 0:
        return "observed_review_changes"
    # Preserve the real exact-head collector's actionable owner route.
    # A long-running CI job is not just passive waiting, and missing
    # independent review can be requested while checks are pending.
    if item.get("human_next_gate_step") == "owner_triage_observed_slow_current_head_checks":
        return "observed_slow_ci_owner_triage"
    if (item.get("human_next_gate_step") == "request_independent_current_head_review"
            and item["observed_independent_exact_head_approvals"] < 1
            and not item.get("pending_individual_review_requests_observed", 0)
            and not item.get("pending_team_review_requests_observed", 0)):
        return "saved_review_needed"
    if (item["observed_checks_pending"] > 0
            or item.get("legacy_status_pending_observed", 0) > 0):
        return "observed_checks_waiting"
    if item["observed_independent_exact_head_approvals"] < 1:
        if (item.get("pending_individual_review_requests_observed", 0) or
                item.get("pending_team_review_requests_observed", 0)):
            return "saved_review_requested_waiting"
        return "saved_review_needed"
    return "verify_live_branch_and_owner_rules"


def _owner_check_links_from_receipt(item, repository, head):
    """Create bounded numeric GitHub check links from saved, pinned receipts.

    An offline receipt is historical: these are owner navigation aids, never
    proof of current CI, runtime recovery or branch-protection requirements.
    """
    source = item.get("observed_actionable_check_runs", [])
    if type(source) is not list or len(source) > 12:
        raise ValueError("unbounded saved exact-head check preview")
    result, seen = [], set()
    for row in source:
        if type(row) is not dict:
            raise ValueError("malformed saved exact-head check entry")
        check_id = row.get("check_run_id")
        if (type(check_id) is not int or check_id <= 0 or check_id in seen):
            raise ValueError("duplicate or invalid saved GitHub check identity")
        seen.add(check_id)
        url = "https://github.com/" + repository + "/runs/" + str(check_id)
        if row.get("url") != url:
            raise ValueError("check link escaped pinned repository identity")
        state = row.get("state")
        if state == "completed":
            if row.get("conclusion") in ("success", "neutral", "skipped"):
                raise ValueError("successful check incorrectly marked actionable")
            kind = "failed_check"
        elif (state == "in_progress" and
              row.get("running_attention_60m") is True):
            kind = "long_running_check"
        elif state == "queued" and row.get("queued_attention_30m") is True:
            kind = "long_queued_check"
        else:
            continue
        if len(result) < 8:
            result.append({"url": url, "kind": kind,
                           "saved_head": head, "live_state_verified": False})
    return result


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
                or (item.get("base_ref_observed") is not None
                    and not _valid_branch_ref(item.get("base_ref_observed")))
                or type(item.get("observed_checks_pending")) is not int
                or type(item.get("observed_checks_not_successful")) is not int
                or type(item.get("observed_independent_exact_head_approvals")) is not int
                or item.get("human_next_gate_step") not in HUMAN_GATE_STEPS
                or (item.get("parallel_independent_review_step") is not None
                    and item.get("parallel_independent_review_step") not in HUMAN_GATE_STEPS)
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
                # Older stored observations did not expose these distinct
                # signals; they remain unknown instead of being upgraded to
                # conflict-free evidence.
                for key in ("merge_conflict_observed", "mergeability_not_yet_evaluated",
                            "draft_state_stable_on_second_read",
                            "pending_review_requests_stable_on_second_read"):
                    if key in item and type(item[key]) is not bool:
                        raise ValueError("invalid saved draft/review/conflict evidence")
                for key in ("pending_individual_review_requests_observed",
                            "pending_team_review_requests_observed"):
                    if key in item and (type(item[key]) is not int or
                                        not 0 <= item[key] <= 100):
                        raise ValueError("invalid bounded pending review count")
                if "pending_review_request_evidence_sha256" in item and (
                    type(item["pending_review_request_evidence_sha256"]) is not str or
                    re.fullmatch(r"[a-f0-9]{64}",
                                 item["pending_review_request_evidence_sha256"]) is None):
                    raise ValueError("invalid pending review source fingerprint")
                if any(item[k] < 0 for k in (
                        "observed_checks_pending", "observed_checks_not_successful",
                        "observed_independent_exact_head_approvals")):
                    raise ValueError("invalid negative observed gate count")
            # Only a pinned exact-head receipt can supply direct historical
            # owner links. Never reuse links from drifted/unavailable PR reads.
            owner_check_links = (_owner_check_links_from_receipt(
                item, repo, item["head_sha_observed"]) if observed else [])
            rows.append({
                "owner_check_links": owner_check_links,
                "url": url,
                "target_branch_observed": (
                    item.get("base_ref_observed") if
                    observed and _valid_branch_ref(item.get("base_ref_observed")) else None),
                "target_branch_identity_known": bool(
                    observed and _valid_branch_ref(item.get("base_ref_observed"))),
                "exact_head_observed": item["head_sha_observed"] if observed else None,
                "source_status": "observed_partial" if observed else "unavailable_or_drifted",
                "checks_pending_observed": item["observed_checks_pending"] if observed else None,
                "checks_failed_observed": item["observed_checks_not_successful"] if observed else None,
                "independent_exact_head_approvals_observed": (
                    item["observed_independent_exact_head_approvals"] if observed else None),
                "pending_individual_review_requests_observed": (
                    item.get("pending_individual_review_requests_observed") if observed else None),
                "pending_team_review_requests_observed": (
                    item.get("pending_team_review_requests_observed") if observed else None),
                "next_human_gate": item["human_next_gate_step"] if observed else "recollect_exact_head_source",
                "parallel_human_gate": (item.get("parallel_independent_review_step")
                                        if observed else None),
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
            "owner_lane": _owner_lane(row["historic_gate_category"]),
            "human_next_gate": row["next_human_gate"],
            "parallel_human_gate": row["parallel_human_gate"],
            "pending_individual_review_requests_observed":
                row["pending_individual_review_requests_observed"],
            "pending_team_review_requests_observed":
                row["pending_team_review_requests_observed"],
            "target_branch_observed": row["target_branch_observed"],
            "target_branch_identity_known": row["target_branch_identity_known"],
            "exact_head_observed": row["exact_head_observed"],
            "owner_check_links": row["owner_check_links"],
            "receipt_old": row["saved_source_stale_over_15_minutes"],
            "current_head_verified": False,
            "owner_and_release_authorized": False,
            "merge_authorized": False,
        }
        for pos, row in all_rows
    ]
    first_actions = manual_queue[:24]
    lane_totals = {lane: sum(action["owner_lane"] == lane for action in manual_queue)
                   for lane in OWNER_LANE_ORDER}
    # Separate *prioritized operator attention* from the original P0/P1-first
    # manifest order. Within each category, preserve source manifest order.
    # The prioritization is a visual handoff, not a merge/claim decision.
    owner_priority = ("fix_source", "inspect_ci", "refresh_source", "request_review",
                      "release_handoff", "external_wait")
    attention_queue = sorted(manual_queue,
                             key=lambda action: (owner_priority.index(action["owner_lane"]),
                                                 action["manifest_position"]))
    first_attention = attention_queue[:24]
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
        "prs_with_unknown_target_branch": sum(
            not row["target_branch_identity_known"] for row in all_rows),
        "target_branch_identity_complete": all(
            row["target_branch_identity_known"] for row in all_rows),
        "unavailable_or_drifted_prs": unavailable,
        "stale_over_15_minutes_prs": stale,
        "saved_historic_gate_category_counts": category_counts,
        "cross_batch_observation_spread_seconds": spread,
        "cross_batch_observation_within_10_minutes": source_window_coherent,
        "manual_review_queue_preview": first_actions,
        "manual_review_queue_hidden_after_24": max(0, len(manual_queue) - len(first_actions)),
        "owner_attention_lane_counts": lane_totals,
        "owner_attention_queue_preview": first_attention,
        "owner_attention_queue_hidden_after_24": max(0, len(manual_queue) - len(first_attention)),
        "all_saved_batches_present": not missing,
        "all_observations_present_and_recent": (
            not missing and not unavailable and not stale and source_window_coherent
            and all(row["target_branch_identity_known"] for row in all_rows)),
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


def _review_request_note(row):
    """Saved observation, never proof that a review is currently pending."""
    people = row.get("pending_individual_review_requests_observed")
    teams = row.get("pending_team_review_requests_observed")
    if people is None or teams is None:
        return "reviewaanvragen: ONBEKEND (historische bron)"
    return "historisch review aangevraagd bij " + str(people) + (
        " personen en " + str(teams) + " teams; geen goedkeuring")


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
        "PRs met onbekende doelbranch: " + str(report["prs_with_unknown_target_branch"]),
        "Doelbranchidentiteit volledig: " + str(report["target_branch_identity_complete"]),
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
    lines.append("## Nu oppakken: brongebonden eigenaaracties" if markdown
                 else "Nu oppakken: brongebonden eigenaaracties")
    for lane in OWNER_LANE_ORDER:
        lines.append("- " + OWNER_LANES[lane] + ": " +
                     str(report["owner_attention_lane_counts"][lane]) + " waargenomen")
    for action in report["owner_attention_queue_preview"]:
        ref = ("[" + action["url"] + "](" + action["url"] + ")"
               if markdown else action["url"])
        lines.append("- " + str(action["manifest_position"]) + ". " + ref +
                     " · " + OWNER_LANES[action["owner_lane"]] +
                     " · " + HUMAN_GATE_LABELS_NL.get(action["human_next_gate"],
                                                     "Herlees actuele GitHub-bron.") +
                     (" · parallel: " + HUMAN_GATE_LABELS_NL[action["parallel_human_gate"]]
                      if action["parallel_human_gate"] else "") +
                     " · GEEN merge-/claimvrijgave")
        for link in action["owner_check_links"]:
            label = {
                "failed_check": "Historische mislukte Check Run",
                "long_running_check": "Historische langlopende Check Run",
                "long_queued_check": "Historische lang queued Check Run",
            }.get(link["kind"])
            if label is None:
                raise ValueError("unrecognized saved check action kind")
            lines.append("  ↳ " + ("[" + label + "](" + link["url"] + ")"
                                   if markdown else label + ": " + link["url"]) +
                         " · HERLEES actuele HEAD voor actie")
    if report["owner_attention_queue_hidden_after_24"]:
        lines.append(str(report["owner_attention_queue_hidden_after_24"]) +
                     " extra bronitems buiten de compacte eigenaarselectie.")
    lines.append("")
    lines.append("## Oorspronkelijke manifestprioriteit: menselijke merge-first werkbak" if markdown
                 else "Eerst bekijken: menselijke merge-first werkbak")
    for action in report["manual_review_queue_preview"]:
        link = ("[" + action["url"] + "](" + action["url"] + ")"
                if markdown else action["url"])
        lines.append("- " + str(action["manifest_position"]) + ". " + link +
                     " · doelbranch " + str(action.get("target_branch_observed") or "ONBEKEND") +
                     " · [" + action["historic_gate_category"] + "] " +
                     HUMAN_GATE_LABELS_NL.get(action["human_next_gate"], "Actueel bronbewijs nodig.") +
                     " · " + _review_request_note(action) + " · geen mergevrijgave")
    if report["manual_review_queue_hidden_after_24"]:
        lines.append(str(report["manual_review_queue_hidden_after_24"]) +
                     " extra PR's buiten compacte shortlist, zie volledige bronbatches.")
    for batch in report["batches"]:
        lines.append(("\n## " if markdown else "\n") + "Batch " + str(batch["batch"]) +
                     " · bron UTC " + batch["observed_at_utc"])
        for row in batch["prs"]:
            name = ("[" + row["url"] + "](" + row["url"] + ")") if markdown else row["url"]
            lines.append("- " + name + " · doelbranch " +
                         str(row.get("target_branch_observed") or "ONBEKEND") +
                         " · [" + row["historic_gate_category"] + "] " +
                         HUMAN_GATE_LABELS_NL.get(row["next_human_gate"], "Actueel bronbewijs nodig.") +
                         " · " + _review_request_note(row) +
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
            step = escape(HUMAN_GATE_LABELS_NL.get(row["next_human_gate"],
                                                       "Actueel bronbewijs nodig."))
            category = escape(row["historic_gate_category"])
            head = escape(row["exact_head_observed"] or "onbekend")
            target = escape(row.get("target_branch_observed") or "ONBEKEND")
            note = " · oud bronbewijs" if row["saved_source_stale_over_15_minutes"] else ""
            items.append('<li><a rel="noopener noreferrer" href="' + url + '">' + url +
                         '</a><br><small>HEAD ' + head + ' · doelbranch ' + target +
                         ' · [' + category + '] ' + step + escape(note) +
                         ' · ' + escape(_review_request_note(row)) +
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
                           "</a><br><small>Doelbranch " +
                           escape(action.get("target_branch_observed") or "ONBEKEND") +
                           " · " + category + " · " + step +
                           " · HANDMATIG / GEEN MERGE</small></li>")
    lane_items = []
    for action in report["owner_attention_queue_preview"]:
        ref = escape(action["url"], quote=True)
        lane = action["owner_lane"]
        if lane not in OWNER_LANES:
            raise ValueError("unknown private owner attention lane")
        reason = escape(HUMAN_GATE_LABELS_NL.get(action["human_next_gate"],
                                                 "Herlees actuele GitHub-bron."))
        check_links_html = []
        for link in action["owner_check_links"]:
            label = {
                "failed_check": "Mislukte check",
                "long_running_check": "Langlopende check",
                "long_queued_check": "Lang queued check",
            }.get(link["kind"])
            if label is None:
                raise ValueError("unrecognized saved CI check kind")
            check_links_html.append(
                "<li><a rel='noopener noreferrer' href='" +
                escape(link["url"], quote=True) + "'>" + escape(label) +
                "</a> · bron-HEAD opnieuw controleren vóór actie</li>")
        lane_items.append("<li><strong>" + escape(OWNER_LANES[lane]) + "</strong> · "
                          "<a rel='noopener noreferrer' href='" + ref + "'>" +
                          str(action["manifest_position"]) + ". " + ref + "</a>" +
                          " · " + reason +
                          (" · <em>Parallel: " +
                           escape(HUMAN_GATE_LABELS_NL[action["parallel_human_gate"]]) +
                           "</em>" if action["parallel_human_gate"] else "") +
                          ("<ul>" + "".join(check_links_html) + "</ul>" if check_links_html else "") +
                          " <small>HISTORISCH / geen mergevrijgave</small></li>")
    lane_totals = " · ".join(escape(OWNER_LANES[lane]) + ": " +
                             str(report["owner_attention_lane_counts"][lane])
                             for lane in OWNER_LANE_ORDER)
    attention_section = (
        "<section><h2>Nu oppakken: eigenaaracties</h2>"
        "<p>Geprioriteerd op geobserveerde problemen: reparaties en brononderzoek "
        "vóór passief CI-wachten. De oorspronkelijke P0/P1-manifestprioriteit "
        "blijft hieronder zichtbaar; dit zijn historische, niet-autoriserende "
        "suggesties. Controleer actuele claims en HEAD opnieuw.</p>"
        "<p>" + lane_totals + "</p><ol>" + "".join(lane_items) + "</ol><p>" +
        str(report["owner_attention_queue_hidden_after_24"]) +
        " extra PRs buiten deze compacte eigenaarselectie.</p></section>")
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
            '</p><p>PRs met onbekende doelbranch: ' +
            str(report["prs_with_unknown_target_branch"]) +
            ' · doelbranchidentiteit volledig: ' +
            str(report["target_branch_identity_complete"]) +
            '</p><p>Historische gates: ' + cats +
            '</p><p>Tijdverschil tussen bronbatches: ' +
            str(report["cross_batch_observation_spread_seconds"]) +
            ' seconden · binnen tien minuten: ' +
            str(report["cross_batch_observation_within_10_minutes"]) +
            '</p><strong>GEEN merge-, claim-, deploy- of reviewvrijgave. '
            'Bron is niet live.</strong></header>' + attention_section + queue_section + "".join(cards) +
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
            write_private_report(args.output, output)
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
