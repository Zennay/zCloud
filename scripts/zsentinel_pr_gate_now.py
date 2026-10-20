#!/usr/bin/env python3
"""zSentinel bounded current exact-head PR gate desk, read-only.

Current checks and submitted reviews are OBSERVATIONS, never GitHub's
full required-check rule set, branch protection, release or merge permission.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys

from zsentinel_pr_scope_guard import github_json, REPO, SHA

SCHEMA = "zsentinel-current-pr-gate-observation-v1"
MAX_PRS = 4
NUMBER = re.compile(r"^[1-9][0-9]{0,8}$")
OK_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})


def _sha(value):
    if type(value) is not str or not SHA.fullmatch(value):
        raise ValueError("missing exact commit SHA")
    return value


def _one(repo, number, token):
    if not REPO.fullmatch(repo) or type(number) is not int or not 1 <= number <= 999999999:
        raise ValueError("invalid canonical PR identity")
    prefix = "/pulls/" + str(number)
    first = github_json(repo, prefix, token)
    if (type(first) is not dict or first.get("number") != number
            or first.get("state") != "open" or type(first.get("base")) is not dict
            or type(first.get("head")) is not dict):
        raise ValueError("PR not open or source missing")
    head = _sha(first["head"].get("sha"))
    base = _sha(first["base"].get("sha"))
    base_repo = first["base"].get("repo")
    author = first.get("user")
    if (type(base_repo) is not dict or base_repo.get("full_name") != repo
            or type(author) is not dict or type(author.get("login")) is not str):
        raise ValueError("PR not based in canonical repository")
    checks = github_json(repo, "/commits/" + head + "/check-runs?per_page=100", token)
    if (type(checks) is not dict or type(checks.get("total_count")) is not int
            or not 0 <= checks["total_count"] <= 1000000
            or type(checks.get("check_runs")) is not list
            or len(checks["check_runs"]) > 100):
        raise ValueError("bounded check-runs source unavailable")
    check_rows = checks["check_runs"]
    statuses = {"completed_observed": 0, "pending_observed": 0,
                "failure_observed": 0, "other_observed": 0}
    seen_check_ids = set()
    for row in check_rows:
        if type(row) is not dict or type(row.get("id")) is not int or row["id"] in seen_check_ids:
            raise ValueError("invalid or repeated check source identity")
        seen_check_ids.add(row["id"])
        if row.get("head_sha") not in (None, head):
            raise ValueError("check source head drift")
        state, conclusion = row.get("status"), row.get("conclusion")
        if state == "completed":
            statuses["completed_observed"] += 1
            if conclusion not in OK_CONCLUSIONS:
                statuses["failure_observed"] += 1
        elif state in ("queued", "in_progress", "pending", "waiting", "requested"):
            statuses["pending_observed"] += 1
        else:
            statuses["other_observed"] += 1
    # Check Runs and legacy commit statuses are distinct GitHub evidence
    # surfaces. A successful Check Run is not proof that a required legacy
    # commit-status context succeeded. Keep both visible and non-authorizing.
    legacy = github_json(repo, "/commits/" + head + "/status?per_page=100", token)
    if (type(legacy) is not dict or legacy.get("sha") != head
            or type(legacy.get("statuses")) is not list
            or len(legacy["statuses"]) > 100):
        raise ValueError("legacy commit statuses missing or head changed")
    latest_context = {}
    seen_status_ids = set()
    for row in legacy["statuses"]:
        if (type(row) is not dict or type(row.get("id")) is not int
                or row["id"] in seen_status_ids
                or type(row.get("context")) is not str
                or not 1 <= len(row["context"]) <= 200
                or row.get("state") not in ("success", "pending", "failure", "error")):
            raise ValueError("invalid legacy status receipt")
        seen_status_ids.add(row["id"])
        # GitHub may retain earlier failed statuses for a now-green context.
        # Use the highest status ID observed for each exact context rather
        # than reporting superseded failures as active, but never claim that
        # the sampled set enumerates all branch-protection requirements.
        name = row["context"]
        if name not in latest_context or row["id"] > latest_context[name]["id"]:
            latest_context[name] = row
    legacy_states = {"success": 0, "pending": 0, "failure": 0, "error": 0}
    for row in latest_context.values():
        legacy_states[row["state"]] += 1
    legacy_partial = len(legacy["statuses"]) == 100
    reviews = github_json(repo, prefix + "/reviews?per_page=100", token)
    if type(reviews) is not list or len(reviews) > 100:
        raise ValueError("bounded reviews source unavailable")
    most_recent = {}
    for row in reviews:
        if (type(row) is not dict or type(row.get("id")) is not int
                or type(row.get("user")) is not dict):
            raise ValueError("invalid review receipt")
        reviewer = row["user"].get("login")
        if type(reviewer) is not str or not 1 <= len(reviewer) <= 70:
            raise ValueError("untrusted reviewer identity")
        # Only reviews explicitly submitted for the current exact commit
        # can count as fresh positive observations. Dismissals supersede.
        if row.get("commit_id") != head:
            continue
        if reviewer == author["login"]:
            continue
        review_state = row.get("state")
        if review_state not in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"):
            continue
        current = most_recent.get(reviewer)
        if current is None or row["id"] > current[0]:
            most_recent[reviewer] = (row["id"], review_state)
    last = github_json(repo, prefix, token)
    if (type(last) is not dict or last.get("number") != number
            or last.get("state") != "open"
            or type(last.get("head")) is not dict
            or type(last.get("base")) is not dict
            or last["head"].get("sha") != head
            or last["base"].get("sha") != base):
        raise ValueError("PR head/base changed during observation")
    approved = sum(state == "APPROVED" for _, state in most_recent.values())
    requested = sum(state == "CHANGES_REQUESTED" for _, state in most_recent.values())
    check_complete = (len(check_rows) == checks["total_count"] and
                      statuses["pending_observed"] == 0 and
                      statuses["other_observed"] == 0)
    if legacy_partial or len(check_rows) != checks["total_count"] or len(reviews) == 100:
        gate_step = "inspect_incomplete_current_head_gate_source"
    elif (statuses["failure_observed"] or
          legacy_states["failure"] or legacy_states["error"]):
        gate_step = "inspect_current_head_failed_checks_with_owner"
    elif (statuses["pending_observed"] or statuses["other_observed"] or
          legacy_states["pending"]):
        gate_step = "wait_for_existing_current_head_checks"
    elif requested:
        gate_step = "owner_address_exact_head_review_changes"
    elif not check_rows and not latest_context:
        gate_step = "verify_missing_current_head_checks_and_rules"
    elif not approved:
        gate_step = "request_independent_current_head_review"
    else:
        gate_step = "verify_required_rules_ownership_and_release_separately"
    return {
        "url": "https://github.com/" + repo + "/pull/" + str(number),
        "repository": repo, "number": number,
        "head_sha_observed": head, "base_sha_observed": base,
        "draft_at_read": last.get("draft") is True,
        "github_mergeable_boolean_observed": last.get("mergeable"),
        "check_runs_total_observed": checks["total_count"],
        "check_runs_page_complete": len(check_rows) == checks["total_count"],
        "check_runs_sample_complete_and_terminal": check_complete,
        "observed_checks_completed": statuses["completed_observed"],
        "observed_checks_pending": statuses["pending_observed"],
        "observed_checks_not_successful": statuses["failure_observed"],
        "observed_checks_unknown": statuses["other_observed"],
        "legacy_status_contexts_observed": len(latest_context),
        "legacy_status_success_observed": legacy_states["success"],
        "legacy_status_pending_observed": legacy_states["pending"],
        "legacy_status_failure_or_error_observed": (
            legacy_states["failure"] + legacy_states["error"]),
        "legacy_status_source_may_be_truncated": legacy_partial,
        "legacy_status_and_checks_required_set_verified": False,
        "human_next_gate_step": gate_step,
        "legacy_status_sample_complete_and_terminal": (
            not legacy_partial and legacy_states["pending"] == 0),
        "observed_independent_exact_head_approvals": approved,
        "observed_exact_head_changes_requested": requested,
        "review_page_may_be_truncated": len(reviews) == 100,
        "exact_head_stable_on_second_read": True,
        "required_branch_checks_and_rules_verified": False,
        "required_review_policy_verified": False,
        "mergable_by_automation": False,
        "merge_authorized": False,
        "deployment_authorized": False,
        "read_only": True,
        "status": "observed_current_head_partial_gate_evidence",
    }


def observe_many(prs, token=None):
    if (type(prs) is not list or not 1 <= len(prs) <= MAX_PRS
            or len({tuple(p) for p in prs if type(p) in (tuple, list) and len(p) == 2})
                   != len(prs)):
        raise ValueError("choose one to four distinct canonical existing PRs")
    items = []
    for entry in prs:
        if (type(entry) not in (tuple, list) or len(entry) != 2
                or type(entry[0]) is not str or not REPO.fullmatch(entry[0])
                or type(entry[1]) is not int or not 1 <= entry[1] <= 999999999):
            raise ValueError("invalid PR identity")
        repo, number = entry
        try:
            result = _one(repo, number, token)
        except (OSError, ValueError):
            result = {
                "url": "https://github.com/" + repo + "/pull/" + str(number),
                "repository": repo, "number": number,
                "status": "github_source_unavailable_or_head_changed",
                "required_branch_checks_and_rules_verified": False,
                "required_review_policy_verified": False,
                "merge_authorized": False, "deployment_authorized": False,
                "read_only": True,
            }
        items.append(result)
    return {
        "schema": SCHEMA,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "observed_prs": len(items),
        "max_github_gets": len(items) * 5,
        "current_review_and_check_samples_only": True,
        "required_branch_protection_verified": False,
        "merge_authorized": False, "deployment_authorized": False,
        "writes_performed": False,
    }


PR_ARGUMENT = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}#[1-9][0-9]{0,8}$")


def human_gate_summary(source: dict) -> str:
    """Compact offline operator view; observed receipts never authorize merging."""
    if (type(source) is not dict or source.get("schema") != SCHEMA
            or source.get("merge_authorized") is not False
            or source.get("writes_performed") is not False):
        raise ValueError("invalid current-head gate source")
    lines = [
        "zSentinel | actuele PR-gates · uitsluitend waarneming",
        "Bronmoment UTC: " + source["observed_at_utc"],
        "Protected main, verplichte CI, onafhankelijke review en eigenaar: NIET vrijgegeven.",
    ]
    for item in source["items"]:
        lines.append("")
        lines.append("PR: " + item["url"])
        if item.get("status") != "observed_current_head_partial_gate_evidence":
            lines.append("GitHub-bron onbereikbaar of HEAD veranderd; PR niet vrijgeven.")
            continue
        lines.append("Waargenomen HEAD: " + item["head_sha_observed"])
        lines.append(
            "Checks: " + str(item["observed_checks_pending"]) + " lopend, " +
            str(item["observed_checks_not_successful"]) + " niet geslaagd")
        lines.append(
            "Oudere statuscontexten: " +
            str(item["legacy_status_pending_observed"]) + " lopend, " +
            str(item["legacy_status_failure_or_error_observed"]) + " niet geslaagd")
        lines.append(
            "Onafhankelijke exact-head approvals (waargenomen): " +
            str(item["observed_independent_exact_head_approvals"]))
        lines.append("Volgende menselijke actie: " + item["human_next_gate_step"])
        if (item["legacy_status_source_may_be_truncated"] or
                not item["check_runs_page_complete"] or
                item["review_page_may_be_truncated"]):
            lines.append("WAARSCHUWING: huidige bronnen onvolledig of afgekapt.")
        lines.append("Merge-/deployautorisatie: NEE.")
    lines.append("")
    lines.append("Geen claims, herdispatch, merge of deploy uitgevoerd.")
    return "\n".join(lines) + "\n"



REVIEW_SCHEMA = "zsentinel-offline-merge-first-review-v1"
REVIEW_MAX_BYTES = 1024 * 1024
REVIEW_MAX_PRS = 300
REVIEW_PR_URL = re.compile(
    r"^https://github[.]com/(Zennay/[A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,8})$")


def read_review_manifest(path: Path):
    """Bounded read-only handoff selection; no claim or merge permission."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 2 <= info.st_size <= REVIEW_MAX_BYTES:
            raise ValueError("review manifest must be a bounded regular JSON file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(REVIEW_MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > REVIEW_MAX_BYTES:
        raise ValueError("review manifest exceeds one MiB")
    try:
        data = json.loads(raw.decode("utf-8", errors="strict"))
    except (ValueError, UnicodeError):
        raise ValueError("invalid UTF-8 JSON review manifest") from None
    if (type(data) is not dict or data.get("schema") != REVIEW_SCHEMA
            or data.get("claim_authorized") is not False
            or data.get("merge_authorized") is not False
            or data.get("deploy_authorized") is not False
            or data.get("dispatch_authorized") is not False
            or data.get("evidence_scope") != "bounded_saved_snapshot_not_live"
            or type(data.get("source_board_sha256")) is not str
            or re.fullmatch(r"[a-f0-9]{64}", data["source_board_sha256"]) is None):
        raise ValueError("not a canonical non-authorizing review manifest")
    rows = data.get("pr_references")
    if type(rows) is not list or not 1 <= len(rows) <= REVIEW_MAX_PRS:
        raise ValueError("review manifest has no bounded PR references")
    priorities = {"P0": 0, "P1": 1, "P2": 2, "Unspecified": 3}
    selected = []
    seen = set()
    for row in rows:
        if (type(row) is not dict or type(row.get("url")) is not str
                or row.get("highest_linked_priority") not in priorities
                or row.get("merge_authorized") is not False
                or type(row.get("observed_issue_refs")) is not list
                or not row["observed_issue_refs"]):
            raise ValueError("invalid captured PR reference")
        match = REVIEW_PR_URL.fullmatch(row["url"])
        if match is None:
            raise ValueError("PR must be a canonical Zennay GitHub reference")
        identity = (match.group(1), int(match.group(2)))
        if identity in seen:
            raise ValueError("duplicate PR in review manifest")
        seen.add(identity)
        selected.append((priorities[row["highest_linked_priority"]], identity))
    selected.sort(key=lambda row: (row[0], row[1]))
    return {
        "prs": [identity for _, identity in selected[:MAX_PRS]],
        "source_review_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "source_review_board_sha256": data["source_board_sha256"],
        "source_review_pr_count": len(selected),
        "review_only_top_four_observed": len(selected) > MAX_PRS,
    }


def main(argv=None):
    """Produce one bounded source receipt for the offline merge-first workbench."""
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--pr", action="append",
        help="Canonical Zennay/repository#number (repeat one to four times)")
    selector.add_argument(
        "--from-review", type=Path,
        help="Review-only JSON manifest from the offline Merge-first Workbench")
    parser.add_argument(
        "--format", choices=("json", "text"), default="json",
        help="JSON for offline import (default), or human-readable Dutch gate triage")
    parser.add_argument(
        "--output", type=Path,
        help="Create NEW private 0600 report; omit to print to stdout")
    args = parser.parse_args(argv)
    try:
        review = None
        if args.from_review is not None:
            review = read_review_manifest(args.from_review)
            refs = review["prs"]
        else:
            if not 1 <= len(args.pr) <= MAX_PRS:
                raise ValueError("choose one to four PR references")
            refs = []
            for raw in args.pr:
                if type(raw) is not str or PR_ARGUMENT.fullmatch(raw) is None:
                    raise ValueError("PR must use Zennay/repository#number")
                repo, number = raw.rsplit("#", 1)
                refs.append((repo, int(number)))
            if len(set(refs)) != len(refs):
                raise ValueError("duplicate PR reference")
        source = observe_many(refs, token=os.environ.get("GITHUB_TOKEN"))
        if review is not None:
            for key in ("source_review_manifest_sha256",
                        "source_review_board_sha256", "source_review_pr_count",
                        "review_only_top_four_observed"):
                source[key] = review[key]
        rendered = (
            json.dumps(source, sort_keys=True, ensure_ascii=True, indent=2) + "\n"
            if args.format == "json" else human_gate_summary(source))
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            target = args.output
            if target.is_dir():
                raise ValueError("output must be a newly created file")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target, flags, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(rendered)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                # Creation was exclusive. Never overwrite an existing output.
                target.unlink(missing_ok=True)
                raise
            # Never print private source content or path in a shared log.
            print("Created private read-only PR gate receipt (0600); no merges or GitHub writes.",
                  file=sys.stderr)
        return 0
    except OSError:
        # A failed private output may contain its local path in OSError text.
        # Do not echo that location to shared terminal or worker logs.
        print("zSentinel PR gate desk refused: GitHub or private output unavailable",
              file=sys.stderr)
        return 2
    except ValueError as exc:
        print("zSentinel PR gate desk refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
