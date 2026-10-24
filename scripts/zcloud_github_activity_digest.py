#!/usr/bin/env python3
"""Render the zCloud read-only GitHub activity receipt as an operator digest.

Never interprets CI success as deployment, PR creation as merged work, or an
unavailable provider as no work. Safe for local stdin pipeline use.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys

PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
REPOSITORY = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")
EVENT_ID = re.compile(r"^github:Zennay/[A-Za-z0-9_.-]{1,100}:(?:(?:pr_created|pr_updated|pr_merged|pr_closed|ci_run):[0-9]{1,15}|main_commit:[a-f0-9]{40}|branch_head:[A-Za-z0-9._/-]{1,80}:[a-f0-9]{40})$")
KINDS = {"pr_created", "pr_updated", "pr_merged", "pr_closed", "ci_run", "main_commit", "branch_head"}
OUTCOMES = {"success", "failure", "cancelled", "skipped", "timed_out",
            "action_required", "neutral", "stale", "startup_failure", None}
# Constant operator language: never interpolate GitHub response text or tokens.
PROVIDER_ADMISSIONS = {
    "none": "",
    "authentication_required": (
        "GitHub authenticatie geweigerd: controleer de verbinding/bevoegde token; "
        "volgende projectbronnen zijn mogelijk niet opgevraagd."
    ),
    "rate_limit_confirmed": (
        "GitHub rate-limit aantoonbaar bereikt: verzoeken gestopt. "
        "Ontbrekende activiteit blijft onbekend."
    ),
    "repository_or_secondary_limit_unknown": (
        "GitHub 403 op deze repository: rechten of secundaire limiet onbekend. "
        "Alleen deze repository is gepauzeerd; andere blijven onafhankelijk."
    ),
    "global_admission_denied_prior_project": (
        "GitHub-collector eerder globaal gestopt wegens 401 of bevestigde limiet. "
        "Dit project is niet opgevraagd."
    ),
}


def safe_receipt(raw: bytes) -> dict:
    if len(raw) > 1024 * 1024:
        raise ValueError("oversized")
    def unique(pairs: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result
    payload = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(payload, dict) or payload.get("schema") != "zcloud-github-activity-readonly-v1":
        raise ValueError("unexpected_schema")
    if payload.get("feed_complete") is not False:
        raise ValueError("false_complete")
    if any(payload.get(key) is not False for key in
           ("authorizes_repair", "authorizes_merge", "authorizes_deploy")):
        raise ValueError("unsafe_authority")
    if not isinstance(payload.get("coverage"), list) or not isinstance(payload.get("events"), list):
        raise ValueError("invalid_collections")
    if len(payload["coverage"]) > 8 or len(payload["events"]) > 200:
        raise ValueError("oversized_collections")
    if not isinstance(payload.get("previous_snapshot_compared"), bool):
        raise ValueError("previous_snapshot_flag_invalid")
    return payload


MAX_SNAPSHOT_AGE_SECONDS = 6 * 60 * 60
MAX_FUTURE_SKEW_SECONDS = 120


def snapshot_freshness(value: object, now: dt.datetime | None = None) -> dict:
    """Classify snapshot age without treating an invalid clock as current."""
    unknown = {"state": "unknown", "observed_at": None, "age_seconds": None}
    if not isinstance(value, str) or len(value) > 40:
        return unknown
    try:
        observed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return unknown
    if observed.tzinfo is None or observed.utcoffset() is None:
        return unknown
    observed = observed.astimezone(dt.timezone.utc)
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        return unknown
    age = int((current.astimezone(dt.timezone.utc) - observed).total_seconds())
    normalized = observed.isoformat(timespec="seconds").replace("+00:00", "Z")
    if age < -MAX_FUTURE_SKEW_SECONDS:
        return {"state": "future_clock_skew", "observed_at": normalized,
                "age_seconds": None}
    age = max(0, age)
    return {"state": "stale" if age > MAX_SNAPSHOT_AGE_SECONDS else "recent",
            "observed_at": normalized, "age_seconds": age}


def digest(payload: dict) -> str:
    lines = [
        "# zCloud · GitHub activity receipt",
        "",
        "**Status:** PARTIAL GitHub observation only. Branch heads are bounded snapshots, "
        "not full branch history; no production deployment, worker resumption or project-complete claim.",
        "",
    ]
    freshness = snapshot_freshness(payload.get("observed_at"))
    if freshness["state"] == "recent":
        lines.append("**Snapshot freshness:** recent source observation " +
                     freshness["observed_at"] + " · age " +
                     str(freshness["age_seconds"] // 60) +
                     " minutes (six-hour freshness limit).")
    elif freshness["state"] == "stale":
        lines.append("**STALE SNAPSHOT:** source observation " +
                     freshness["observed_at"] + " is " +
                     str(freshness["age_seconds"] // 3600) +
                     " hours old. Refresh the read-only source before drawing current Activity conclusions.")
    elif freshness["state"] == "future_clock_skew":
        lines.append("**UNKNOWN SNAPSHOT FRESHNESS:** source timestamp " +
                     freshness["observed_at"] +
                     " is more than two minutes in the future; inspect clock/source integrity.")
    else:
        lines.append("**UNKNOWN SNAPSHOT FRESHNESS:** missing or invalid source timestamp; this is not a current observation.")
    lines.append("")
    known: set[str] = set()
    for item in payload["coverage"]:
        if not isinstance(item, dict):
            raise ValueError("invalid_coverage")
        project, repo = item.get("project"), item.get("repository")
        if not isinstance(project, str) or not PROJECT.fullmatch(project):
            raise ValueError("invalid_project")
        if not isinstance(repo, str) or not REPOSITORY.fullmatch(repo):
            raise ValueError("invalid_repository")
        if project in known:
            raise ValueError("duplicate_project")
        known.add(project)
        state = item.get("status")
        if state not in {"partial_sources", "partial_error", "unavailable"}:
            raise ValueError("invalid_coverage_state")
        source_pr = item.get("github_pr")
        source_ci = item.get("github_ci")
        source_commit = item.get("github_main_commits")
        accepted = {"unavailable", "partial", "bounded_window", "bounded_truncated"}
        if any(value not in accepted for value in (source_pr, source_ci, source_commit)):
            raise ValueError("invalid_source_state")
        lines.append("## " + project)
        lines.append("")
        branches = item.get("github_branches")
        if branches not in {"not_synchronized", "observed_pr_heads_only", "unavailable",
                            "partial", "bounded_window", "bounded_truncated", "bounded_partial"}:
            raise ValueError("invalid_branch_coverage")
        lines.append("GitHub PR: **" + source_pr + "** · GitHub Actions: **" +
                     source_ci + "** · main commits: **" + source_commit +
                     "** · branches: **" + branches + "** (not fully synchronized).")
        page_limits = item.get("page_limit_reached", {})
        any_page_limited = isinstance(page_limits, dict) and any(value is True
                            for value in page_limits.values())
        if ("bounded_truncated" in (source_pr, source_ci, source_commit, branches)
                or any_page_limited):
            lines.append("**Source page budget reached:** older GitHub events were not scanned; counts are lower bounds.")
        if state != "partial_sources":
            lines.append("Source is **incomplete or inaccessible**. Missing events are unknown, not zero.")
        admission = item.get("provider_admission", "none")
        if type(admission) is not str or admission not in PROVIDER_ADMISSIONS:
            raise ValueError("invalid_provider_admission")
        if admission != "none":
            lines.append("**Bronblokkade:** " + PROVIDER_ADMISSIONS[admission])
        project_events = [event for event in payload["events"]
                          if isinstance(event, dict) and event.get("project") == project]
        if len(project_events) > 200:
            raise ValueError("invalid_event_count")
        visible_main_shas = {
            event.get("sha") for event in project_events
            if event.get("kind") == "main_commit"
            and isinstance(event.get("sha"), str)
            and re.fullmatch(r"[a-f0-9]{40}", event["sha"])
        }
        validated: list[dict] = []
        for event in project_events:
            kind, event_id = event.get("kind"), event.get("id")
            if kind not in KINDS or not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
                raise ValueError("invalid_event")
            expected_prefix = "github:" + repo + ":" + kind + ":"
            if not event_id.startswith(expected_prefix):
                raise ValueError("cross_repository_event")
            current_seen = event.get("first_observed_at")
            if not isinstance(current_seen, str) or len(current_seen) > 40:
                raise ValueError("first_seen_invalid")
            if event.get("new_since_previous") not in (None, True, False):
                raise ValueError("new_since_previous_invalid")
            if payload["previous_snapshot_compared"] and not isinstance(event.get("new_since_previous"), bool):
                raise ValueError("new_since_previous_missing")
            if not payload["previous_snapshot_compared"] and event.get("new_since_previous") is not None:
                raise ValueError("unexpected_previous_comparison")
            if kind == "branch_head":
                branch_ref = event.get("branch_ref")
                sha = event.get("sha")
                if (not isinstance(branch_ref, str) or len(branch_ref) > 80
                        or not re.fullmatch(r"[A-Za-z0-9._/-]{1,80}", branch_ref)
                        or branch_ref.startswith("/") or branch_ref.endswith("/")
                        or "//" in branch_ref or ".." in branch_ref or branch_ref.endswith(".")
                        or any(not part or part.endswith(".lock") for part in branch_ref.split("/"))
                        or not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha)
                        or event_id != "github:" + repo + ":branch_head:" + branch_ref + ":" + sha
                        or event.get("main_merged") is not False
                        or event.get("branch_owner_verified") is not False
                        or event.get("deploy_verified") is not False
                        or event.get("worker_resumed") is not False):
                    raise ValueError("invalid_branch_head")
            if kind in {"pr_created", "pr_updated", "pr_merged", "pr_closed"}:
                target = event.get("reported_target")
                if target is not None and (not isinstance(target, str) or
                                           (len(target) > 180 or not re.fullmatch(r"[A-Za-z0-9_./-]+", target) or target.startswith("/") or target.endswith("/") or "//" in target or ".." in target)):
                    raise ValueError("invalid_reported_target")
                main_report = event.get("reported_main_merge")
                if not isinstance(main_report, bool) or main_report != (
                    event.get("merged") is True and target == "main"
                ):
                    raise ValueError("invalid_reported_merge")
                main_evidence = event.get("main_merged")
                if main_evidence is not False and main_evidence is not True:
                    raise ValueError("invalid_main_merge_state")
                if main_evidence is True:
                    merge_sha = event.get("merge_commit_sha")
                    if (event.get("merged") is not True
                            or target != "main"
                            or not isinstance(merge_sha, str)
                            or not re.fullmatch(r"[a-f0-9]{40}", merge_sha)
                            or merge_sha not in visible_main_shas
                            or event.get("main_merge_evidence") !=
                               "canonical_main_history_exact_sha"):
                        raise ValueError("uncorroborated_main_history_in_receipt")
                head_sha = event.get("pr_head_sha")
                if not isinstance(head_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", head_sha):
                    raise ValueError("invalid_pr_head_sha")
                # Older valid receipts do not contain this optional lifecycle.
                # Never interpret a legacy missing field as an open PR.
                lifecycle = event.get("pr_lifecycle")
                if kind == "pr_closed":
                    # A closure is a provider event, not an inferred delivery.
                    if (lifecycle != "closed_without_merge"
                            or event.get("merged") is not False
                            or event.get("main_merged") is not False
                            or event.get("reported_main_merge") is not False
                            or not isinstance(event.get("pr_closed_at"), str)):
                        raise ValueError("invalid_unmerged_closure_evidence")
                if lifecycle is not None:
                    if lifecycle not in ("open", "merged", "closed_without_merge"):
                        raise ValueError("invalid_pr_lifecycle")
                    if (lifecycle == "merged") != (event.get("merged") is True):
                        raise ValueError("pr_lifecycle_merge_conflict")
                    closed_time = event.get("pr_closed_at")
                    if (lifecycle == "open" and closed_time is not None
                            or lifecycle != "open" and (
                                not isinstance(closed_time, str)
                                or len(closed_time) > 40)):
                        raise ValueError("invalid_pr_closure_source")
            if kind == "ci_run":
                if event.get("conclusion") not in OUTCOMES:
                    raise ValueError("invalid_outcome")
                if event.get("deploy_verified") is not False or event.get("worker_resumed") is not False:
                    raise ValueError("invalid_deployment_claim")
            if kind == "main_commit":
                sha = event.get("sha")
                if not isinstance(sha, str) or not re.fullmatch("[a-f0-9]{40}", sha):
                    raise ValueError("invalid_main_commit")
                if event.get("main_merged") is not True or event.get("deploy_verified") is not False or event.get("worker_resumed") is not False:
                    raise ValueError("invalid_main_commit_claim")
                if event_id != "github:" + repo + ":main_commit:" + sha:
                    raise ValueError("invalid_main_commit_identity")
            validated.append(event)
        created = sum(e["kind"] == "pr_created" for e in validated)
        # Count explicit closure events at provider close-time; these are
        # unique per PR and never count as shipped to main.
        closed_unmerged = len({
            e["pr"] for e in validated if e["kind"] == "pr_closed"
        })
        merged = sum(e["kind"] == "pr_merged" for e in validated)
        merged_to_main_reported = sum(e["kind"] == "pr_merged" and
                                      e["reported_main_merge"] for e in validated)
        source_matched_merges = sum(
            e["kind"] == "pr_merged" and e.get("main_merged") is True
            for e in validated
        )
        ci_success = sum(e["kind"] == "ci_run" and e.get("conclusion") == "success"
                         for e in validated)
        main_commits = sum(e["kind"] == "main_commit" for e in validated)
        branch_heads = sum(e["kind"] == "branch_head" for e in validated)
        first_seen_in_view = sum(e.get("new_since_previous") is True for e in validated)
        lines.append("Observed event counts (bounded window, may be truncated): "
                     + str(created) + " PR-created; " + str(closed_unmerged)
                     + " observed PR closures without merge (not deliveries); "
                     + str(merged)
                     + " PR-merged-to-some-base (of which " +
                     str(merged_to_main_reported) + " reported target=main; " +
                     str(source_matched_merges) + " with matching SHA in the same imported main history); " +
                     str(ci_success)
                     + " workflow-success; " + str(main_commits)
                     + " canonical-main commits; " + str(branch_heads)
                     + " observed branch heads. Branch observation time is not commit time. "
                     + "**None proves a main deployment.**")
        if payload["previous_snapshot_compared"]:
            lines.append(str(first_seen_in_view) +
                         " events absent from previous bounded snapshot (not proof of new GitHub work).")
        for event in validated[:10]:
            kind = event["kind"]
            if kind == "ci_run":
                number = event.get("run_id")
                if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                    raise ValueError("invalid_ci_id")
                label = "CI #" + str(number) + " · " + str(event.get("status")) + " / " + str(event.get("conclusion"))
                url = "https://github.com/" + repo + "/actions/runs/" + str(number)
            elif kind == "main_commit":
                sha = event["sha"]
                label = "main commit " + sha[:12]
                url = "https://github.com/" + repo + "/commit/" + sha
            elif kind == "branch_head":
                branch_ref = event["branch_ref"]
                sha = event["sha"]
                label = "branch head " + branch_ref + " · " + sha[:12] + " · owner/merge/deploy unverified"
                url = "https://github.com/" + repo + "/tree/" + branch_ref
            else:
                number = event.get("pr")
                if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                    raise ValueError("invalid_pr_id")
                label = kind.replace("_", " ") + " #" + str(number) + " · head " + event["pr_head_sha"][:12]
                if event.get("draft") is True:
                    label += " (draft)"
                if kind == "pr_closed":
                    label += " (closed at provider source time; NOT delivered)"
                elif event.get("pr_lifecycle") == "closed_without_merge":
                    label += " (now closed without merge; NOT delivered)"
                elif event.get("pr_lifecycle") == "open":
                    label += " (currently open)"
                if kind == "pr_merged":
                    target = event.get("reported_target") or "unknown"
                    label += (
                        " (target: " + target + "; " +
                        ("exact main SHA found in imported receipt, not independent deploy proof"
                         if event.get("main_merged") is True
                         else "main reachability not proven in receipt") + ")"
                    )
                url = "https://github.com/" + repo + "/pull/" + str(number)
            lines.append("- [" + label + "](" + url + ")")
        lines.append("")
    if not known:
        lines.append("No project coverage returned: **unknown**, not healthy.")
    lines.append("**Next gate:** owner review + canonical-main source/CI + actual "
                 "release/worker proof before marking fixed, deployed or resumed.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Read a bounded GitHub activity receipt from stdin")
    parser.parse_args()
    try:
        data = sys.stdin.buffer.read(1024 * 1024 + 1)
        print(digest(safe_receipt(data)), end="")
        return 0
    except (ValueError, UnicodeError, json.JSONDecodeError):
        print("GitHub activity receipt invalid; no recovery claim.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
