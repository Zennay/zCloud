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
LIVE_FOLLOWUP = {
    UNCHECKED: ("check_original_issue_state", "Check the original issue manually."),
    "OPEN_AT_LIVE_RECHECK": (
        "continue_existing_issue_with_verified_owner",
        "Issue is open; inspect its existing PR and active owner before any change.",
    ),
    "CLOSED_AT_LIVE_RECHECK": (
        "verify_closed_issue_release_evidence",
        "Issue is closed on GitHub; separately verify PR merge, release and actual runtime behavior.",
    ),
    "SOURCE_UNAVAILABLE_AT_LIVE_RECHECK": (
        "confirm_github_source_access_without_retry",
        "Read failed; ask the authorized source owner to inspect access and provider admission.",
    ),
    "NOT_CHECKED_AFTER_SOURCE_ERROR": (
        "defer_until_provider_access_restored",
        "Earlier GitHub detail read failed; avoid more probes until the source recovers.",
    ),
    "NOT_CHECKED_REPOSITORY_SOURCE_ERROR": (
        "confirm_access_to_affected_repository",
        "This repository already returned inaccessible/not-found; avoid further reads of it.",
    ),
    "NOT_CHECKED_PRIOR_PROVIDER_STOP": (
        "defer_after_portfolio_provider_stop",
        "Earlier portfolio read stopped on provider error; retain UNKNOWN without additional GETs.",
    ),
    "NOT_CHECKED_SOURCE_ADMISSION_DENIED": (
        "request_explicit_live_read_admission",
        "Optional GitHub live reads were not admitted; retain UNKNOWN and ask the operator to opt in.",
    ),
    "NOT_CHECKED_READ_LIMIT": (
        "recheck_manually_if_priority_warrants",
        "This issue exceeded the bounded read allowance; check directly if still urgent.",
    ),
}


def _set_status(item, state):
    code, action = LIVE_FOLLOWUP[state]
    item["live_issue_recheck_state"] = state
    item["live_next_action_code"] = code
    item["live_next_action"] = action
    item["live_action_executed"] = False


def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def recheck_missing_live(report, *, token=None, max_reads=3,
                         source_read_admitted=False):
    """Annotate bounded missing cards with explicit read-only GitHub state.

    On provider-wide admission/network or malformed-source errors, stop all
    subsequent GETs. On one canonical repository 404, skip only later reads
    for that repository while preserving independent source reads elsewhere.
    Never retry, infer deletion, or guess the issue's state.
    """
    if type(max_reads) is not int or not 1 <= max_reads <= MAX_LIVE_RECHECKS:
        raise PolicyInputError("live recheck limit must be 1..6")
    if type(source_read_admitted) is not bool:
        raise PolicyInputError("source read admission must be explicit boolean")
    # Importers must explicitly establish this snapshot's admission state.
    # The default is NO reads, even with a well-formed historical delta.
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
    inaccessible_repositories = set()
    for item in missing:
        _set_status(item, UNCHECKED)
        item["live_issue_recheck_observed_at_utc"] = None
        item["live_issue_state_reason"] = None
        item["live_issue_priority_label"] = None
        item["live_issue_closed_does_not_prove_fix_merged"] = True
        if not source_read_admitted:
            _set_status(item, "NOT_CHECKED_SOURCE_ADMISSION_DENIED")
            continue
        if stopped:
            _set_status(item, "NOT_CHECKED_AFTER_SOURCE_ERROR")
            continue
        if item["repository"] in inaccessible_repositories:
            _set_status(item, "NOT_CHECKED_REPOSITORY_SOURCE_ERROR")
            continue
        if attempted >= max_reads:
            _set_status(item, "NOT_CHECKED_READ_LIMIT")
            continue
        attempted += 1
        try:
            payload = _limited_json(
                "/repos/" + item["repository"] + "/issues/" + str(item["number"]),
                token=token,
            )
            if type(payload) is not dict or payload.get("html_url") != item["url"]:
                raise GitHubReadError("GitHub issue detail canonical URL mismatch")
            summary = _issue_summary(payload, item["repository"], item["number"])
        except GitHubReadError as exc:
            # The existing GitHub reader distinguishes a canonical 404 from
            # provider-wide quota/auth/network errors. A 404 makes only that
            # repository inaccessible in this read window; don't send more
            # requests to the same repo, but preserve other independent repos.
            source_errors += 1
            _set_status(item, "SOURCE_UNAVAILABLE_AT_LIVE_RECHECK")
            if "inaccessible or not found" in str(exc):
                inaccessible_repositories.add(item["repository"])
            else:
                stopped = True
            continue
        except (ValueError, KeyError, TypeError):
            # Contradictory source identity or malformed API payload is not
            # a verified local 404. Stop further provider reads safely.
            source_errors += 1
            stopped = True
            _set_status(item, "SOURCE_UNAVAILABLE_AT_LIVE_RECHECK")
            continue
        state = summary["observed_state"]
        _set_status(item, (
            "OPEN_AT_LIVE_RECHECK" if state == "open"
            else "CLOSED_AT_LIVE_RECHECK"
        ))
        item["live_issue_recheck_observed_at_utc"] = summary["observed_at_utc"]
        item["live_issue_state_reason"] = summary["reported_state_reason"]
        item["live_issue_priority_label"] = summary["priority_from_explicit_labels"]
        if state == "closed" and item["live_issue_state_reason"] == "not_planned":
            item["live_next_action_code"] = "inspect_not_planned_closure_decision"
            item["live_next_action"] = (
                "GitHub closed this as not planned; inspect the recorded owner "
                "decision and do not infer a shipped fix, merge or deploy."
            )
        elif state == "closed" and item["live_issue_state_reason"] != "completed":
            item["live_next_action_code"] = "inspect_unclassified_closure_reason"
            item["live_next_action"] = (
                "GitHub reports CLOSED without an explicit completed reason; "
                "inspect closure history and independently verify any product fix."
            )
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
        "repository_local_source_gaps": sorted(inaccessible_repositories),
        "provider_reads_stopped_after_error": stopped,
        "prior_provider_stop_suppressed_reads": False,
        "live_read_admission_denied": not source_read_admitted,
        "not_displayed_cards_not_checked": report.get("omitted_change_cards", 0),
        "sample_absence_never_proves_closure": True,
        "github_closed_not_proof_of_fix_or_main": True,
        "current_worker_claims_verified": False,
        "merged_main_or_deployed_verified": False,
        "actions_executed": False,
    }
    # Refresh only the advisory human workorder view. The original sampled
    # change remains unchanged even when GitHub detail reports CLOSED.
    from zsentinel_portfolio_history import _operator_workorders
    report["operator_workorders"] = _operator_workorders(report)
    return report
