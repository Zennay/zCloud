#!/usr/bin/env python3
"""Optional bounded live follow-up for issues missing from a sampled portfolio desk.

This module never writes GitHub, changes a worker claim, retries CI or marks
remediation complete. The caller must explicitly opt into live reads. One
canonical single-issue GET can establish an observed GitHub issue state, not
the fix, merge, release or live production state.
"""
from __future__ import annotations

from datetime import datetime, timezone

from zsentinel_pr_live_read import (
    GitHubReadError, _issue_summary, _limited_json,
)
from zsentinel_recovery_policy import PolicyInputError

MAX_LIVE_RECHECKS = 6
MISSING = "NOT_IN_CURRENT_DISPLAY_NOT_PROVEN_RESOLVED"
UNCHECKED = "NOT_RECHECKED"
CODES = frozenset({
    "OPEN_AT_LIVE_RECHECK", "CLOSED_AT_LIVE_RECHECK",
    "SOURCE_UNAVAILABLE_AT_LIVE_RECHECK", "NOT_CHECKED_AFTER_SOURCE_ERROR",
    "NOT_CHECKED_READ_LIMIT", UNCHECKED,
})


def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def recheck_missing_live(report, *, token=None, max_reads=3,
                         source_read_admitted=True):
    """Annotate bounded missing cards with explicit read-only GitHub state.

    On any source/admission error, stop additional network reads and retain an
    UNKNOWN result. No automatic retries, concurrent probes, or guessed states.
    """
    if type(max_reads) is not int or not 1 <= max_reads <= MAX_LIVE_RECHECKS:
        raise PolicyInputError("live recheck limit must be 1..6")
    if type(source_read_admitted) is not bool:
        raise PolicyInputError("source read admission must be explicit boolean")
    if type(report) is not dict or report.get("policy") != (
        "zsentinel-portfolio-visible-issue-delta-v1"
    ):
        raise PolicyInputError("live recheck requires a canonical portfolio delta")
    changes = report.get("changes")
    if type(changes) is not list:
        raise PolicyInputError("canonical portfolio delta changes missing")
    # Recheck the most recently OBSERVED urgent cards first. A currently
    # missing issue has no proven current GitHub priority; previous rank is
    # only a triage ordering hint, not a new priority classification.
    missing = sorted(
        (item for item in changes if item.get("change") == MISSING),
        key=lambda item: (
            item["prior_priority_rank"], item["repository"], item["number"],
        ),
    )
    attempted = 0
    stopped = False
    observed_open = 0
    observed_closed = 0
    source_errors = 0
    for item in missing:
        item["live_issue_recheck_state"] = UNCHECKED
        item["live_issue_recheck_observed_at_utc"] = None
        item["live_issue_state_reason"] = None
        item["live_issue_priority_label"] = None
        item["live_issue_closed_does_not_prove_fix_merged"] = True
        if not source_read_admitted:
            item["live_issue_recheck_state"] = "NOT_CHECKED_PRIOR_PROVIDER_STOP"
            continue
        if stopped:
            item["live_issue_recheck_state"] = "NOT_CHECKED_AFTER_SOURCE_ERROR"
            continue
        if attempted >= max_reads:
            item["live_issue_recheck_state"] = "NOT_CHECKED_READ_LIMIT"
            continue
        attempted += 1
        try:
            payload = _limited_json(
                "/repos/" + item["repository"] + "/issues/" + str(item["number"]),
                token=token,
            )
            summary = _issue_summary(payload, item["repository"], item["number"])
        except (GitHubReadError, ValueError, KeyError, TypeError):
            # 404 can mean private, removed, or inaccessible. There is no
            # permission to classify it as closed or to repeat the provider
            # request after a shared quota/network/admission failure.
            source_errors += 1
            stopped = True
            item["live_issue_recheck_state"] = "SOURCE_UNAVAILABLE_AT_LIVE_RECHECK"
            continue
        state = summary["observed_state"]
        item["live_issue_recheck_state"] = (
            "OPEN_AT_LIVE_RECHECK" if state == "open"
            else "CLOSED_AT_LIVE_RECHECK"
        )
        item["live_issue_recheck_observed_at_utc"] = summary["observed_at_utc"]
        item["live_issue_state_reason"] = summary["reported_state_reason"]
        item["live_issue_priority_label"] = summary["priority_from_explicit_labels"]
        observed_open += int(state == "open")
        observed_closed += int(state == "closed")

    report["live_issue_recheck"] = {
        "requested": True,
        "read_only": True,
        "requested_at_utc": _utc(),
        "maximum_reads": max_reads,
        "attempted_gets": attempted,
        "missing_cards_displayed": len(missing),
        "open_at_recheck": observed_open,
        "closed_at_recheck": observed_closed,
        "source_errors": source_errors,
        "provider_reads_stopped_after_error": stopped,
        "prior_provider_stop_suppressed_reads": not source_read_admitted,
        "not_displayed_cards_not_checked": report.get("omitted_change_cards", 0),
        "sample_absence_never_proves_closure": True,
        "github_closed_not_proof_of_fix_or_main": True,
        "current_worker_claims_verified": False,
        "merged_main_or_deployed_verified": False,
        "actions_executed": False,
    }
    # A subset of displayed missing cards may have a present-time observed
    # state; this does not change the historical difference classification.
    return report
