#!/usr/bin/env python3
"""Source-bound history for zSentinel's independent product issue workboard.

Two bounded GitHub snapshots are NOT a complete GitHub inventory. A missing
issue in the current sample is never closed/resolved or safe to reclaim.
No network, scheduler, local state write, worker claim or release action.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zsentinel_product_issue_inbox import object_unique, observed_time

MAX_PRIOR_BYTES = 512 * 1024
MAX_ISSUES = 24
MAX_PROJECTS = 3
ISSUE = re.compile(r"^https://github\.com/Zennay/[A-Za-z0-9_.-]+/issues/[1-9][0-9]{0,8}$")
PR = re.compile(r"^https://github\.com/Zennay/[A-Za-z0-9_.-]+/pull/[1-9][0-9]{0,8}$")
SHA = re.compile(r"^[a-f0-9]{40}$")
PRIORITIES = frozenset(("P0", "P1", "P2", "Unspecified"))


def load_private_prior(path: Path):
    """Read an explicitly supplied regular local JSON file. Do not create."""
    if not isinstance(path, Path) or path.is_symlink() or not path.is_file():
        raise ValueError("previous snapshot must be a regular, non-symlink file")
    if path.stat().st_size <= 1 or path.stat().st_size > MAX_PRIOR_BYTES:
        raise ValueError("previous snapshot exceeds 512 KiB or is empty")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_unique)


def _source(report):
    if (not isinstance(report, dict)
            or report.get("schema") != "zsentinel-issue-delivery-board-v1"
            or report.get("writes_performed") is not False
            or report.get("claims_taken") is not False
            or report.get("dispatch_authorized") is not False
            or report.get("ownership_verified") is not False
            or report.get("merge_authorized") is not False
            or type(report.get("projects")) is not list
            or not 1 <= len(report["projects"]) <= MAX_PROJECTS
            or len(set(report["projects"])) != len(report["projects"])
            or type(report.get("items")) is not list
            or len(report["items"]) > MAX_ISSUES
            or type(report.get("issue_count")) is not int
            or report["issue_count"] != len(report["items"])
            or type(report.get("coverage")) is not list
            or len(report["coverage"]) != len(report["projects"])):
        raise ValueError("untrusted issue board snapshot")
    projects = report["projects"]
    if any(type(p) is not str or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", p)
           for p in projects):
        raise ValueError("invalid project in previous snapshot")
    coverage = {}
    for row in report["coverage"]:
        if (not isinstance(row, dict) or row.get("project") not in projects
                or row["project"] in coverage
                or row.get("status") not in ("partial", "degraded", "unavailable")):
            raise ValueError("invalid project observation coverage")
        coverage[row["project"]] = row["status"]
    if len(coverage) != len(projects):
        raise ValueError("incomplete per-project coverage metadata")
    items = {}
    for item in report["items"]:
        if (not isinstance(item, dict)
                or item.get("project_id") not in projects
                or type(item.get("title")) is not str
                or len(item["title"]) > 180
                or item.get("priority") not in PRIORITIES
                or type(item.get("issue_url")) is not str
                or not ISSUE.fullmatch(item["issue_url"])
                or item["issue_url"] in items
                or item.get("issue_fixed") is not False
                or item.get("owner_verified") is not False
                or item.get("merge_authorized") is not False
                or not isinstance(item.get("existing_pr_links_unverified"), list)
                or len(item["existing_pr_links_unverified"]) > 3
                or not isinstance(item.get("inspected_prs"), list)
                or len(item["inspected_prs"]) > 3):
            raise ValueError("invalid sampled issue identity")
        repo_prefix = item["issue_url"].rsplit("/issues/", 1)[0]
        if (any(type(link) is not str or not PR.fullmatch(link)
                or not link.startswith(repo_prefix + "/pull/")
                for link in item["existing_pr_links_unverified"])):
            raise ValueError("untrusted cross-repository PR links")
        linked = {}
        for pr in item["inspected_prs"]:
            if (not isinstance(pr, dict)
                    or pr.get("url") not in item["existing_pr_links_unverified"]
                    or pr["url"] in linked
                    or pr.get("owner_verified") is not False
                    or pr.get("merge_authorized") is not False
                    or pr.get("issue_fixed") is not False
                    or type(pr.get("action")) is not str
                    or len(pr["action"]) > 400):
                raise ValueError("invalid exact PR evidence in snapshot")
            head = pr.get("head_sha")
            if head is not None and (type(head) is not str or not SHA.fullmatch(head)):
                raise ValueError("invalid exact PR head")
            linked[pr["url"]] = {"head_sha": head, "action": pr["action"]}
        items[item["issue_url"]] = {
            "project_id": item["project_id"],
            "priority": item["priority"],
            "title": item["title"],
            "pr_links": tuple(item["existing_pr_links_unverified"]),
            "inspected": linked,
        }
    return observed_time(report.get("observed_at")), coverage, items


def progress_since(current, previous, now=None):
    """Compare one bounded current sample with one explicit older sample."""
    current_at, current_cov, current_items = _source(current)
    prior_at, prior_cov, prior_items = _source(previous)
    if current["projects"] != previous["projects"]:
        raise ValueError("compare only the same explicitly ordered project window")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("comparison clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    if (prior_at > current_at or current_at > now + timedelta(minutes=2)
            or prior_at < now - timedelta(days=30)):
        raise ValueError("previous snapshot out of chronological range")
    duration = int((current_at - prior_at).total_seconds())
    clock_warning = (now - current_at > timedelta(minutes=10) or
                     current_at > now + timedelta(minutes=2))
    comparable = {p: current_cov[p] == "partial" and prior_cov[p] == "partial"
                  for p in current["projects"]}
    rows = []
    for url, item in current_items.items():
        prior = prior_items.get(url)
        verified_both_samples = comparable[item["project_id"]]
        if prior is None:
            state = ("newly_sampled_not_new_issue" if verified_both_samples
                     else "source_incomplete_newly_observed")
        else:
            state = ("observed_again" if verified_both_samples else
                     "source_incomplete_observed_again")
        changes = []
        if prior and verified_both_samples:
            if item["priority"] != prior["priority"]:
                changes.append("priority_changed")
            if set(item["pr_links"]) != set(prior["pr_links"]):
                changes.append("pr_references_changed")
            for link, evidence in item["inspected"].items():
                old = prior["inspected"].get(link)
                if not old:
                    changes.append("newly_inspected_existing_pr")
                elif evidence["head_sha"] and old["head_sha"] and evidence["head_sha"] != old["head_sha"]:
                    changes.append("observed_pr_head_changed")
                elif evidence["action"] != old["action"]:
                    changes.append("observed_pr_next_action_changed")
        rows.append({
            "issue_url": url, "project_id": item["project_id"],
            "priority": item["priority"], "title": item["title"],
            "observation": state, "observed_changes": sorted(set(changes)),
            "source_comparable": verified_both_samples,
            "ownership_verified": False, "issue_fixed": False,
            "dispatch_authorized": False, "merge_authorized": False,
        })
    no_longer_sampled = []
    for url, item in prior_items.items():
        if url not in current_items:
            no_longer_sampled.append({
                "issue_url": url, "project_id": item["project_id"],
                "title": item["title"], "priority_at_prior_observation": item["priority"],
                "observation": "not_in_current_bounded_sample_NOT_CLOSED",
                "source_comparable": comparable[item["project_id"]],
                "issue_fixed": False, "closure_verified": False,
                "ownership_verified": False, "dispatch_authorized": False,
            })
    return {
        "schema": "zsentinel-issue-progress-v1",
        "previous_observed_at_utc": prior_at.isoformat(),
        "current_observed_at_utc": current_at.isoformat(),
        "seconds_between_snapshots": duration,
        "current_snapshot_stale": clock_warning,
        "scope": list(current["projects"]),
        "full_issue_inventory_known": False,
        "all_selected_projects_comparably_sampled": all(comparable.values()),
        "observed_changes": rows,
        "prior_issues_outside_current_sample": no_longer_sampled,
        "newly_sampled_count": sum(x["observation"] == "newly_sampled_not_new_issue" for x in rows),
        "changed_source_receipts_count": sum(bool(x["observed_changes"]) for x in rows),
        "previously_sampled_missing_count": len(no_longer_sampled),
        "closed_issues_verified": 0,
        "claims_taken": False, "writes_performed": False,
        "merge_authorized": False, "dispatch_authorized": False,
    }
