"""Conservative issue/PR activity age signal for zSentinel's open-issue worklist.

Activity is a triage hint, never proof of a stalled project, idle worker,
expired claim, available branch, or permission to interrupt ongoing work.
"""
from __future__ import annotations

from datetime import datetime, timezone

WINDOW_HOURS = 168
SIGNALS = {
    "source_clock_incoherent",
    "activity_timestamp_unknown",
    "recent_issue_activity_observed",
    "recent_linked_pr_activity_observed",
    "quiet_over_7_days_observed_claims_unknown",
    "quiet_over_7_days_partial_pr_coverage",
}
LABELS_NL = {
    "source_clock_incoherent": "Bronmomenten verschillen: activiteit onbekend",
    "activity_timestamp_unknown": "Activiteit onbekend: tijdstempel ontbreekt",
    "recent_issue_activity_observed": "Issue recent bijgewerkt",
    "recent_linked_pr_activity_observed": "Gekoppelde open PR recent bijgewerkt",
    "quiet_over_7_days_observed_claims_unknown":
        "Geen issue-/gekoppelde PR-update >7 dagen waargenomen; claims onbekend",
    "quiet_over_7_days_partial_pr_coverage":
        "Issue >7 dagen stil; PR-activiteitsdekking onvolledig",
}
NOTE_NL = (
    "Activiteitssignaal op basis van GitHub-updated_at en een begrensde open-PR-steekproef. "
    "Dit bewijst geen stilstand, vrije claim, PR-loze branch, eigenaar of herstel."
)


def _clock(value):
    if type(value) is not str or len(value) > 48:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(timezone.utc)


def classify(issue_updated_at, linked_pr_urls, observed_prs, as_of,
             source_clocks_coherent, pr_sample_complete,
             issue_reference_sample_complete):
    """Return one bounded, explicitly non-authorizing activity receipt."""
    if (type(linked_pr_urls) is not list or len(linked_pr_urls) > 5
            or len(set(linked_pr_urls)) != len(linked_pr_urls)
            or type(observed_prs) is not list or len(observed_prs) > 48
            or type(source_clocks_coherent) is not bool
            or type(pr_sample_complete) is not bool
            or type(issue_reference_sample_complete) is not bool):
        raise ValueError("invalid bounded issue activity evidence")
    ref = _clock(as_of)
    if ref is None:
        raise ValueError("invalid activity observation clock")
    if any(type(url) is not str or len(url) > 240 for url in linked_pr_urls):
        raise ValueError("invalid linked PR activity identity")
    pr_by_url = {}
    for pr in observed_prs:
        if type(pr) is not dict or type(pr.get("url")) is not str:
            raise ValueError("invalid open PR activity source")
        url = pr["url"]
        if url in pr_by_url:
            raise ValueError("duplicate open PR activity source")
        stamp = _clock(pr.get("updated_at"))
        if stamp is not None and stamp > ref:
            # A PR update after the observation clock cannot prove activity
            # at the time of that observation.
            stamp = None
        pr_by_url[url] = stamp
    issue_stamp = _clock(issue_updated_at)
    if issue_stamp is not None and issue_stamp > ref:
        issue_stamp = None
    observed_linked = [pr_by_url[url] for url in linked_pr_urls
                       if url in pr_by_url and pr_by_url[url] is not None]
    latest_pr = max(observed_linked) if observed_linked else None
    issue_hours = (max(0, int((ref - issue_stamp).total_seconds() // 3600))
                   if issue_stamp is not None else None)
    pr_hours = (max(0, int((ref - latest_pr).total_seconds() // 3600))
                if latest_pr is not None else None)
    all_linked_observed = all(url in pr_by_url and pr_by_url[url] is not None
                              for url in linked_pr_urls)
    coverage = (
        "complete_for_linked_prs_in_bounded_snapshot"
        if pr_sample_complete and issue_reference_sample_complete and all_linked_observed
        else "partial_or_unverified"
    )
    if not source_clocks_coherent:
        signal = "source_clock_incoherent"
    elif issue_hours is not None and issue_hours < WINDOW_HOURS:
        signal = "recent_issue_activity_observed"
    elif pr_hours is not None and pr_hours < WINDOW_HOURS:
        signal = "recent_linked_pr_activity_observed"
    elif issue_hours is None:
        signal = "activity_timestamp_unknown"
    elif coverage == "complete_for_linked_prs_in_bounded_snapshot":
        signal = "quiet_over_7_days_observed_claims_unknown"
    else:
        signal = "quiet_over_7_days_partial_pr_coverage"
    return {
        "signal": signal,
        "label_nl": LABELS_NL[signal],
        "note_nl": NOTE_NL,
        "issue_updated_at": issue_stamp.isoformat(timespec="seconds") if issue_stamp else None,
        "issue_inactivity_hours": issue_hours,
        "latest_linked_open_pr_updated_at": latest_pr.isoformat(timespec="seconds") if latest_pr else None,
        "latest_linked_open_pr_inactivity_hours": pr_hours,
        "linked_pr_activity_coverage": coverage,
        "window_hours": WINDOW_HOURS,
        "as_of_utc": ref.isoformat(timespec="seconds"),
        "active_claims_verified": False,
        "prless_branch_owners_verified": False,
        "stalled_project_proven": False,
        "actions_authorized": False,
    }
