#!/usr/bin/env python3
"""Render the zCloud read-only GitHub activity receipt as an operator digest.

Never interprets CI success as deployment, PR creation as merged work, or an
unavailable provider as no work. Safe for local stdin pipeline use.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
REPOSITORY = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")
EVENT_ID = re.compile(r"^github:Zennay/[A-Za-z0-9_.-]{1,100}:(?:(?:pr_created|pr_updated|pr_merged|ci_run):[0-9]{1,15}|main_commit:[a-f0-9]{40})$")
KINDS = {"pr_created", "pr_updated", "pr_merged", "ci_run", "main_commit"}
OUTCOMES = {"success", "failure", "cancelled", "skipped", "timed_out",
            "action_required", "neutral", "stale", "startup_failure", None}


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
    return payload


def digest(payload: dict) -> str:
    lines = [
        "# zCloud · GitHub activity receipt",
        "",
        "**Status:** PARTIAL GitHub observation only. No non-main branch inventory, "
        "production deployment, worker resumption or project-complete claim.",
        "",
    ]
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
        lines.append("GitHub PR: **" + source_pr + "** · GitHub Actions: **" +
                     source_ci + "** · main commits: **" + source_commit +
                     "** · non-main branches: **not synchronized**.")
        if "bounded_truncated" in (source_pr, source_ci, source_commit):
            lines.append("**Source page budget reached:** older GitHub events were not scanned; counts are lower bounds.")
        if state != "partial_sources":
            lines.append("Source is **incomplete or inaccessible**. Missing events are unknown, not zero.")
        project_events = [event for event in payload["events"]
                          if isinstance(event, dict) and event.get("project") == project]
        if len(project_events) > 200:
            raise ValueError("invalid_event_count")
        validated: list[dict] = []
        for event in project_events:
            kind, event_id = event.get("kind"), event.get("id")
            if kind not in KINDS or not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
                raise ValueError("invalid_event")
            expected_prefix = "github:" + repo + ":" + kind + ":"
            if not event_id.startswith(expected_prefix):
                raise ValueError("cross_repository_event")
            if kind in {"pr_created", "pr_updated", "pr_merged"}:
                target = event.get("reported_target")
                if target is not None and (not isinstance(target, str) or
                                           not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", target)):
                    raise ValueError("invalid_reported_target")
                main_report = event.get("reported_main_merge")
                if not isinstance(main_report, bool) or main_report != (
                    event.get("merged") is True and target == "main"
                ):
                    raise ValueError("invalid_reported_merge")
                if event.get("main_merged") is not False:
                    raise ValueError("main_merge_not_independently_verified")
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
        merged = sum(e["kind"] == "pr_merged" for e in validated)
        merged_to_main_reported = sum(e["kind"] == "pr_merged" and
                                      e["reported_main_merge"] for e in validated)
        ci_success = sum(e["kind"] == "ci_run" and e.get("conclusion") == "success"
                         for e in validated)
        main_commits = sum(e["kind"] == "main_commit" for e in validated)
        lines.append("Observed event counts (bounded window, may be truncated): "
                     + str(created) + " PR-created; " + str(merged)
                     + " PR-merged-to-some-base (of which " +
                     str(merged_to_main_reported) + " reported target=main); " + str(ci_success)
                     + " workflow-success; " + str(main_commits)
                     + " canonical-main commits. **None proves a main deployment.**")
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
            else:
                number = event.get("pr")
                if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                    raise ValueError("invalid_pr_id")
                label = kind.replace("_", " ") + " #" + str(number)
                if event.get("draft") is True:
                    label += " (draft)"
                if kind == "pr_merged":
                    target = event.get("reported_target") or "unknown"
                    label += " (reported target: " + target + "; reachability unverified)"
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
