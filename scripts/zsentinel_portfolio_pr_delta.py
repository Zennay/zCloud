#!/usr/bin/env python3
"""Compare two private zSentinel portfolio PR snapshots without claiming merges.

A PR missing from a bounded source window is UNKNOWN, never merged, closed,
reassigned or free. Does not access GitHub, claims, workers, queue or runtime.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zsentinel_product_issue_inbox import object_unique, observed_time

MAX_PREVIOUS_BYTES = 6 * 1024 * 1024
MAX_PRS = 162
PR = re.compile(r"https://github\.com/(Zennay/[A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,8})\Z")
SHA = re.compile(r"[a-f0-9]{40}\Z")
STATUS = frozenset(("partial", "degraded", "unavailable"))
EXACT = "bounded_exact_head_observed_not_authorized"


def load_previous_portfolio(path: Path):
    """One no-follow, bounded, identity-bound read of an operator's JSON."""
    if not isinstance(path, Path) or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("private previous portfolio must be a path with no-follow support")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 2 <= before.st_size <= MAX_PREVIOUS_BYTES:
        raise ValueError("previous portfolio must be a bounded regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    try:
        start = os.fstat(fd)
        if (not stat.S_ISREG(start.st_mode) or
                (start.st_dev, start.st_ino, start.st_size) !=
                (before.st_dev, before.st_ino, before.st_size)):
            raise ValueError("previous portfolio changed before read")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(MAX_PREVIOUS_BYTES + 1)
        end = os.fstat(fd)
        if (len(data) != start.st_size or len(data) > MAX_PREVIOUS_BYTES or
                (start.st_dev, start.st_ino, start.st_size, start.st_mtime_ns) !=
                (end.st_dev, end.st_ino, end.st_size, end.st_mtime_ns)):
            raise ValueError("previous portfolio changed during read")
    finally:
        os.close(fd)
    source = json.loads(data.decode("utf-8"), object_pairs_hook=object_unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(
                            ValueError("nonfinite previous source value")))
    if type(source) is not dict:
        raise ValueError("previous portfolio is not a JSON object")
    return source, hashlib.sha256(data).hexdigest()


def _read(board, registry):
    if (type(board) is not dict or
            board.get("schema") != "zsentinel-portfolio-workboard-v1" or
            any(board.get(k) is not False for k in
                ("writes_performed", "claims_taken", "dispatch_authorized",
                 "merge_authorized", "ownership_verified", "deploy_authorized"))):
        raise ValueError("unauthorized portfolio comparison source")
    projects = board.get("projects")
    candidates = board.get("recent_open_product_prs")
    coverage = board.get("coverage")
    if (type(projects) is not list or not 1 <= len(projects) <= 18 or
            len(set(projects)) != len(projects) or
            any(type(p) is not str or p not in registry for p in projects) or
            type(candidates) is not list or len(candidates) > MAX_PRS or
            type(coverage) is not list or len(coverage) != len(projects)):
        raise ValueError("invalid bounded portfolio source")
    statuses = {}
    project_times = {}
    for row in coverage:
        if (type(row) is not dict or row.get("project") not in projects or
                row["project"] in statuses or row.get("status") not in STATUS):
            raise ValueError("invalid portfolio project coverage")
        statuses[row["project"]] = row["status"]
        project_times[row["project"]] = observed_time(
            row.get("source_observed_at_utc"))
    if len(statuses) != len(projects):
        raise ValueError("incomplete portfolio source coverage")
    observed = observed_time(board.get("newest_source_observed_at_utc"))
    rows = {}
    for row in candidates:
        if type(row) is not dict:
            raise ValueError("untrusted portfolio PR receipt")
        pid, url = row.get("project_id"), row.get("url")
        match = PR.fullmatch(url) if type(url) is str else None
        head = row.get("listed_head_sha")
        if (pid not in projects or match is None or
                match.group(1) != registry[pid] or url in rows or
                type(head) is not str or SHA.fullmatch(head) is None or
                type(row.get("draft")) is not bool or
                type(row.get("title")) is not str or
                not 1 <= len(row["title"]) <= 180 or
                row.get("merge_authorized") is not False or
                row.get("ownership_verified") is not False or
                row.get("current_exact_head_verified") is not False):
            raise ValueError("invalid canonical PR identity, HEAD or authority")
        rows[url] = {
            "project": pid, "url": url, "head": head,
            "draft": row["draft"], "title": row["title"],
            "exact_read": row.get("gate_observation") == EXACT,
        }
    if any(moment > observed for moment in project_times.values()):
        raise ValueError("project observation later than portfolio source")
    return observed, projects, statuses, project_times, rows


def compare_portfolio_prs(current, previous, registry, previous_sha256, now=None):
    """Show observable PR changes; never infer missing PR closure/merge."""
    current_at, projects, current_coverage, current_times, current_prs = _read(current, registry)
    previous_at, older_projects, previous_coverage, previous_times, prior_prs = _read(previous, registry)
    if projects != older_projects:
        raise ValueError("portfolio source scope differs; select identical ordered projects")
    if (type(previous_sha256) is not str or
            re.fullmatch(r"[a-f0-9]{64}", previous_sha256) is None):
        raise ValueError("previous source fingerprint unavailable")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("invalid observation comparison clock")
    now = now.astimezone(timezone.utc)
    if (previous_at >= current_at or
            current_at > now + timedelta(minutes=2) or
            previous_at < now - timedelta(days=30)):
        raise ValueError("portfolio observations not chronologically comparable")
    # Portfolio windows are independently observed. A globally newer report
    # can still contain a project whose local window predates the old report.
    # Never describe old project data as a fresh changed HEAD or closed PR.
    newer_by_project = {
        pid: current_times[pid] > previous_times[pid] for pid in projects
    }
    changed = []
    for url, row in current_prs.items():
        old = prior_prs.get(url)
        project_newer = newer_by_project[row["project"]]
        comparable = (project_newer and
                      current_coverage[row["project"]] == "partial" and
                      previous_coverage[row["project"]] == "partial")
        notices = []
        if not project_newer:
            state = "current_project_window_not_newer_NO_DELTA_INFERRED"
        elif old is None:
            state = ("newly_in_bounded_sample_NOT_NEW_PR" if comparable
                     else "current_source_observed_prior_coverage_unknown")
        else:
            state = ("observed_in_both_bounded_samples" if comparable
                     else "source_coverage_incomplete")
            if old["head"] != row["head"]:
                notices.append("listed_head_changed_REINSPECT_EXACT_HEAD")
            if old["draft"] != row["draft"]:
                notices.append("draft_status_changed_in_source")
            if old["title"] != row["title"]:
                notices.append("product_pr_title_changed_in_source")
            if old["exact_read"] != row["exact_read"]:
                notices.append("exact_head_inspection_coverage_changed_NOT_GATE_PROOF")
        if old is None or notices or not project_newer:
            changed.append({
                "project_id": row["project"], "url": url, "state": state,
                "changes": notices, "listed_head_sha": row["head"],
                "previous_listed_head_sha": old["head"] if old else None,
                "source_comparable": comparable, "merge_verified": False,
                "owner_verified": False,
            })
    no_longer_listed = []
    for url, row in prior_prs.items():
        if url not in current_prs:
            no_longer_listed.append({
                "project_id": row["project"], "url": url,
                "state": ("current_project_window_not_newer_NO_CLOSURE_EVIDENCE"
                          if not newer_by_project[row["project"]] else
                          "missing_from_current_bounded_sample_NOT_MERGED"
                          if current_coverage[row["project"]] == "partial"
                          else "current_source_unavailable_NO_CLOSURE_EVIDENCE"),
                "previous_listed_head_sha": row["head"],
                "merged_verified": False, "closed_verified": False,
                "owner_verified": False,
            })
    # Merge-first navigation: genuine observed HEAD drift is the first
    # operator investigation, then stale project windows, then new samples.
    # This is only a suggested human source reread, never merge admission.
    changed.sort(key=lambda row: (
        0 if "listed_head_changed_REINSPECT_EXACT_HEAD" in row["changes"]
        else 1 if row["state"] == "current_project_window_not_newer_NO_DELTA_INFERRED"
        else 2 if row["changes"] else 3,
        projects.index(row["project_id"]), row["url"],
    ))
    return {
        "schema": "zsentinel-private-portfolio-pr-delta-v1",
        "previous_sha256": previous_sha256,
        "previous_observed_at_utc": previous_at.isoformat(),
        "current_observed_at_utc": current_at.isoformat(),
        "projects": list(projects),
        "projects_without_newer_read": [
            pid for pid in projects if not newer_by_project[pid]],
        "newly_sampled_not_new_pr_count": sum(
            x["state"] == "newly_in_bounded_sample_NOT_NEW_PR" for x in changed),
        "changed_listed_head_count": sum(
            "listed_head_changed_REINSPECT_EXACT_HEAD" in x["changes"]
            for x in changed),
        "prior_not_in_current_bounded_sample_count": len(no_longer_listed),
        "changed_or_newly_observed": changed,
        "prior_no_longer_observed_NOT_MERGED": no_longer_listed,
        "complete_pr_inventory_verified": False,
        "merged_pr_count_verified": 0,
        "owner_verified": False, "merge_authorized": False,
        "dispatch_authorized": False, "writes_performed": False,
    }
