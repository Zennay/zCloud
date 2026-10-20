#!/usr/bin/env python3
"""Merge private, bounded zSentinel PR-graph snapshots into one offline collision handoff.

This makes cross-window file overlap *visible* without additional GitHub GETs.
It cannot prove globally complete source coverage, ownership, CI, review,
actual Git merge conflicts, worker recovery or release authorization.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import io
from html import escape
import json
import os
from pathlib import Path
import stat
import sys

from zsentinel_pr_scope_guard import (
    REPO, SHA, edit_path, unique, observe_conflict_graph,
)

SCHEMA = "zsentinel-saved-pr-cross-window-collisions-v1"
SOURCE = "zsentinel-pr-file-collision-graph-v1"
MAX_INPUTS = 6
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 12 * 1024 * 1024
MAX_PRS = 288
MAX_PAIRS = 200
MAX_FILES_PER_PR = 100
MAX_SHARED_PATHS = 24
MAX_CROSS_ISSUES = 80
MAX_ISSUE_LINKS = 8
MAX_SECONDS_APART = 600
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_LIVE_PRS = 36
MAX_LIVE_WINDOWS = 3


def read_bounded(path: Path):
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise ValueError("safe regular-file opening is unavailable")
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode)
            or not 2 <= before.st_size <= MAX_SOURCE_BYTES
            or before.st_uid != os.geteuid()
            or before.st_mode & 0o077):
        raise ValueError("source must be owner-private 0600 and bounded")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode)
                or meta.st_uid != os.geteuid() or meta.st_mode & 0o077
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns)):
            raise ValueError("source changed before read")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(MAX_SOURCE_BYTES + 1)
        after = os.fstat(fd)
        if (len(raw) != meta.st_size or len(raw) > MAX_SOURCE_BYTES
                or after.st_uid != os.geteuid() or after.st_mode & 0o077
                or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                != (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns)):
            raise ValueError("source changed during read")
    finally:
        os.close(fd)
    parsed = json.loads(raw.decode("utf-8", errors="strict"),
                        object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(
                            ValueError("nonfinite evidence number")))
    if type(parsed) is not dict:
        raise ValueError("source must contain a JSON object")
    return parsed, len(raw)


def utc_seconds(value):
    if type(value) is not str or not 1 <= len(value) <= 40:
        raise ValueError("invalid observation time")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("observation must use UTC")
    unix = stamp.timestamp()
    if unix > datetime.now(timezone.utc).timestamp() + 300:
        raise ValueError("future observation")
    return unix


def inspect_source(data, repository):
    if (data.get("schema") != SOURCE or data.get("repository") != repository
            or data.get("source") != "bounded_double_read_current_open_github_prs_and_files"
            or data.get("open_pr_snapshot_stable") is not True
            or any(data.get(key) is not False for key in
                   ("file_edit_authorized", "claim_authorized",
                    "merge_authorized", "writes_performed",
                    "notion_claims_verified", "worker_file_leases_verified"))):
        raise ValueError("not a stable, nonauthorizing PR collision source")
    observed = utc_seconds(data.get("observed_at_utc"))
    heads = data.get("inspected_pr_heads")
    receipts = data.get("open_pr_page_receipts")
    size = data.get("open_pr_page_size")
    if (type(heads) is not list or len(heads) > 48
            or type(data.get("inspected_prs")) is not int
            or data["inspected_prs"] != len(heads)
            or type(receipts) is not list or not 1 <= len(receipts) <= 3
            or type(size) is not int or not 1 <= size <= 48):
        raise ValueError("unbounded graph source inventory")
    pages = []
    for receipt in receipts:
        if type(receipt) is not dict:
            raise ValueError("invalid page evidence")
        page, seen = receipt.get("page"), receipt.get("observed_prs")
        if (type(page) is not int or not 1 <= page <= 50
                or type(seen) is not int or not 0 <= seen <= size
                or receipt.get("first_position") != (page - 1) * size + 1
                or receipt.get("last_position") != (page - 1) * size + seen):
            raise ValueError("invalid page position")
        pages.append(page)
    if pages != list(range(pages[0], pages[0] + len(pages))):
        raise ValueError("source pages must be contiguous")
    if data.get("open_pr_page_number") != pages[0]:
        raise ValueError("source page provenance mismatch")
    found = {}
    incomplete = []
    for pr in heads:
        if type(pr) is not dict:
            raise ValueError("invalid PR evidence")
        number, url = pr.get("number"), pr.get("url")
        files = pr.get("file_paths_observed")
        head, base = pr.get("head_sha"), pr.get("base_sha")
        if (type(number) is not int or not 0 < number < 100000000
                or url != "https://github.com/" + repository + "/pull/" + str(number)
                or type(head) is not str or SHA.fullmatch(head) is None
                or type(base) is not str or SHA.fullmatch(base) is None
                or type(files) is not list or len(files) > MAX_FILES_PER_PR
                or type(pr.get("file_list_complete")) is not bool
                or type(pr.get("file_count_observed")) is not int
                or pr["file_count_observed"] != len(files)
                or number in found):
            raise ValueError("invalid, duplicate or unsupported PR inventory")
        names = []
        for name in files:
            if type(name) is not str or name.endswith("/"):
                raise ValueError("PR source paths must name individual files")
            names.append(edit_path(name))
        if len(names) != len(set(names)):
            raise ValueError("duplicate file evidence within PR")
        found[number] = {
            "number": number, "url": url,
            "head_sha": head, "base_sha": base, "files": names,
        }
        if pr["file_list_complete"] is False:
            incomplete.append(number)
    # The current graph's body/title references are unverified hints only.
    # They may point to a pull request with the same number, not an issue.
    raw_links = data.get("open_pr_issue_references_unverified")
    if type(raw_links) is not list or len(raw_links) > 48 * 32:
        raise ValueError("invalid issue-reference inventory")
    refs = []
    seen_refs = set()
    for link in raw_links:
        if type(link) is not dict:
            raise ValueError("invalid issue reference")
        number, url = link.get("issue_number"), link.get("pr_url")
        if type(number) is not int or not 0 < number < 100000000:
            raise ValueError("invalid referenced issue number")
        if type(url) is not str or not url.startswith(
                "https://github.com/" + repository + "/pull/"):
            raise ValueError("foreign issue-to-PR hint")
        tail = url.rsplit("/", 1)[-1]
        if not tail.isascii() or not tail.isdecimal() or int(tail) not in found:
            raise ValueError("unobserved PR referenced by issue hint")
        pr = found[int(tail)]
        origin = link.get("mentioned_in")
        if (pr["url"] != url or link.get("pr_head_sha") != pr["head_sha"]
                or link.get("owner_verified") is not False
                or link.get("fix_verified") is not False
                or type(origin) is not list or not origin
                or len(origin) > 2 or len(set(origin)) != len(origin)
                or any(v not in ("title", "body") for v in origin)
                or (number, url) in seen_refs):
            raise ValueError("inconsistent issue-link provenance")
        seen_refs.add((number, url))
        refs.append({"number": number, "url": url, "head_sha": pr["head_sha"],
                     "mentioned_in": origin})
    return observed, pages, found, incomplete, refs


MAX_CROSS_WINDOW_CLUSTERS = 40
MAX_CLUSTER_EXAMPLE_PATHS = 18


def connected_cross_window_groups(all_prs, owners):
    """Bound memory by distinct PR edges; filenames are indexed just once.

    Includes within-window edges when joining a component spanning windows.
    Historical overlap is not merge/ownership evidence.
    """
    parent = {number: number for number in all_prs}
    edges = set()

    def root(number):
        while parent[number] != number:
            parent[number] = parent[parent[number]]
            number = parent[number]
        return number

    for numbers in owners.values():
        ordered = sorted(numbers)
        if len(ordered) < 2:
            continue
        first = ordered[0]
        for number in ordered[1:]:
            x, y = root(first), root(number)
            if x != y:
                parent[max(x, y)] = min(x, y)
        # At worst 288 distinct PRs => 41,328 unique edges. Never create
        # a list of every filename for every edge, which multiplies memory.
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:]:
                edges.add((left, right))

    all_groups = {}
    for number in all_prs:
        all_groups.setdefault(root(number), []).append(number)
    group_edges = {}
    for left, right in edges:
        group_edges.setdefault(root(left), []).append((left, right))
    cards = []
    for leader, members in all_groups.items():
        member_ids = sorted(members)
        windows = sorted({all_prs[number]["snapshot"] for number in member_ids})
        if len(windows) < 2:
            continue
        member_set = set(member_ids)
        degree = {number: 0 for number in member_ids}
        within_edges = 0
        crossing_edges = 0
        for left, right in group_edges.get(leader, []):
            degree[left] += 1
            degree[right] += 1
            if all_prs[left]["snapshot"] == all_prs[right]["snapshot"]:
                within_edges += 1
            else:
                crossing_edges += 1
        # File ownership comes from the existing per-path index. We only
        # materialize the capped operator matrix, not per-pair path copies.
        candidates = {name for number in member_ids
                      for name in all_prs[number]["files"]}
        # Cross-window connectors are the reason this view exists. Surface
        # them first, then the paths touched by the most observed PRs.
        # Keep lexical tie-breaking for repeatable private exports.
        file_priority = []
        for name in candidates:
            touching = owners[name] & member_set
            if len(touching) < 2:
                continue
            windows_for_file = {all_prs[n]["snapshot"] for n in touching}
            file_priority.append(
                (len(windows_for_file) > 1, len(touching), name))
        file_priority.sort(key=lambda row: (-int(row[0]), -row[1], row[2]))
        files = [row[2] for row in file_priority]
        ranked = sorted(member_ids, key=lambda number: (-degree[number], number))
        file_ownership_rows = []
        for path in files[:MAX_CLUSTER_EXAMPLE_PATHS]:
            touching = sorted(owners[path] & member_set)
            source_windows = sorted({all_prs[n]["snapshot"] for n in touching})
            file_ownership_rows.append({
                "path": path,
                "participating_pr_count_observed": len(touching),
                "source_windows_observed": source_windows,
                "cross_window_overlap_observed": len(source_windows) > 1,
                "prs": [{
                    "number": n, "url": all_prs[n]["url"],
                    "head_sha": all_prs[n]["head_sha"],
                    "source_window": all_prs[n]["snapshot"],
                } for n in touching],
            })
        cards.append({
            "source_windows_observed": windows,
            "pr_count_observed": len(member_ids),
            "within_window_filename_edges_observed": within_edges,
            "cross_window_filename_edges_observed": crossing_edges,
            "distinct_shared_files_observed": len(files),
            "shared_file_samples": files[:MAX_CLUSTER_EXAMPLE_PATHS],
            "shared_file_ownership_samples": file_ownership_rows,
            "shared_file_samples_truncated": len(files) > MAX_CLUSTER_EXAMPLE_PATHS,
            "participating_prs": [
                {"number": number, "url": all_prs[number]["url"],
                 "head_sha": all_prs[number]["head_sha"],
                 "base_sha": all_prs[number]["base_sha"],
                 "source_window": all_prs[number]["snapshot"],
                 "direct_filename_neighbors_observed": degree[number]}
                for number in ranked
            ],
            "source_complete_for_all_open_prs": False,
            "owner_verified": False,
            "merge_conflict_verified": False,
            "merge_authorized": False,
        })
    cards.sort(key=lambda row: (-row["cross_window_filename_edges_observed"],
                                -row["distinct_shared_files_observed"],
                                -row["pr_count_observed"],
                                min(p["number"] for p in row["participating_prs"])))
    for index, row in enumerate(cards, 1):
        row["group_number_in_this_observation"] = index
    return cards


def combine_sources(sources, repository):
    """One bounded source-reconciliation path for private files or live GETs."""
    if not REPO.fullmatch(repository) or not 2 <= len(sources) <= MAX_INPUTS:
        raise ValueError("supply two to six bounded graph sources")
    times, all_pages, all_prs, partial_prs, all_issue_refs = [], set(), {}, [], []
    total_bytes = 0
    for index, (data, size) in enumerate(sources):
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("combined sources exceed bounded size")
        seen_at, pages, prs, incomplete, refs = inspect_source(data, repository)
        if any(page in all_pages for page in pages):
            raise ValueError("overlapping source pages cannot be combined")
        all_pages.update(pages)
        times.append(seen_at)
        partial_prs.extend({"pr": number, "snapshot": index + 1}
                           for number in incomplete)
        all_issue_refs.extend({**ref, "snapshot": index + 1} for ref in refs)
        for number, pr in prs.items():
            if number in all_prs:
                # A PR moving between dated pages breaks cross-window
                # ownership proof even if it happens to have the same SHA.
                raise ValueError("one PR appears in multiple source windows")
            pr["snapshot"] = index + 1
            all_prs[number] = pr
    if max(times) - min(times) > MAX_SECONDS_APART:
        raise ValueError("saved PR snapshots are too far apart in time")
    if len(all_prs) > MAX_PRS:
        raise ValueError("too many combined PR identities")

    # Detect NEW possible duplicate work when two PR windows mention the
    # same #N. We deliberately do not reuse saved 'issue verified' flags:
    # the whole aggregate is historical, non-atomic and nonauthorizing.
    by_issue = {}
    for item in all_issue_refs:
        by_issue.setdefault(item["number"], {})[item["url"]] = item
    cross_issue_groups = sorted(
        [(number, sorted(links.values(), key=lambda link: link["url"]))
         for number, links in by_issue.items()
         if len({link["snapshot"] for link in links.values()}) > 1],
        key=lambda item: (-len(item[1]), item[0]))
    issue_cards = [{
        "number": number,
        "unverified_reference_url":
            "https://github.com/" + repository + "/issues/" + str(number),
        "observed_distinct_prs": len(links),
        "shown_prs": links[:MAX_ISSUE_LINKS],
        "pr_list_truncated": len(links) > MAX_ISSUE_LINKS,
        "actual_issue_identity_verified": False,
        "fix_verified": False,
        "merge_authorized": False,
    } for number, links in cross_issue_groups[:MAX_CROSS_ISSUES]]

    owners = {}
    for pr in all_prs.values():
        for name in pr["files"]:
            owners.setdefault(name, set()).add(pr["number"])
    paired = {}
    for name, numbers in owners.items():
        ordered = sorted(numbers)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1:]:
                # Existing individual graph reports already cover within-
                # snapshot collisions; this reports NEW cross-window ones.
                if all_prs[left]["snapshot"] == all_prs[right]["snapshot"]:
                    continue
                key = (left, right)
                paired[key] = paired.get(key, 0) + 1
    # Counts are enough to rank the entire bounded inventory; only the
    # visible top pairs need concrete path materialization.
    collisions = sorted(paired.items(), key=lambda item: (-item[1], item[0]))
    # Include within-window filename edges when forming a connected graph.
    # A-B within page 1 and B-C on page 2 is ONE product integration group.
    clusters = connected_cross_window_groups(all_prs, owners)
    rows = []
    for (left, right), overlap_count in collisions[:MAX_PAIRS]:
        a, b = all_prs[left], all_prs[right]
        names = sorted(set(a["files"]) & set(b["files"]))
        if len(names) != overlap_count:
            raise ValueError("inconsistent observed cross-window file counts")
        rows.append({
            "left_url": a["url"], "right_url": b["url"],
            "left_head_sha": a["head_sha"], "right_head_sha": b["head_sha"],
            "left_snapshot": a["snapshot"], "right_snapshot": b["snapshot"],
            "shared_files_observed": overlap_count,
            "shared_paths": names[:MAX_SHARED_PATHS],
            "shared_paths_truncated": overlap_count > MAX_SHARED_PATHS,
            "actual_git_merge_conflict_verified": False,
        })
    # A compact operator worklist groups repeat offenders across observed
    # windows, so an operator can inspect an existing PR instead of
    # overlooking it in 200 pair cards. File overlaps outrank unverified
    # '#N' references; both remain nonauthorizing observations.
    by_pr = {}
    for number, pr in all_prs.items():
        by_pr[number] = {
            "pr_number": number, "url": pr["url"],
            "head_sha_observed": pr["head_sha"],
            "snapshot": pr["snapshot"],
            "cross_window_file_pair_count": 0,
            "cross_window_shared_file_count": 0,
            "cross_window_number_hint_count": 0,
            "claim_authorized": False, "merge_authorized": False,
        }
    for (left, right), overlap_count in collisions:
        for number in (left, right):
            by_pr[number]["cross_window_file_pair_count"] += 1
            by_pr[number]["cross_window_shared_file_count"] += overlap_count
    for _, references in cross_issue_groups:
        for item in references:
            number = int(item["url"].rsplit("/", 1)[-1])
            by_pr[number]["cross_window_number_hint_count"] += 1
    ranking = sorted(
        (row for row in by_pr.values()
         if row["cross_window_file_pair_count"]
            or row["cross_window_number_hint_count"]),
        key=lambda row: (-row["cross_window_file_pair_count"],
                         -row["cross_window_shared_file_count"],
                         -row["cross_window_number_hint_count"],
                         row["pr_number"]))

    return {
        "schema": SCHEMA,
        "repository": repository,
        "source": "bounded_sequential_double_read_github_graphs_not_atomic", 
        "collection_mode": "private_saved_graph_files",
        "snapshot_count": len(sources),
        "total_observed_prs": len(all_prs),
        "observed_source_page_numbers": sorted(all_pages),
        "observation_window_utc": [
            datetime.fromtimestamp(t, timezone.utc).isoformat()
            for t in (min(times), max(times))],
        "cross_snapshot_file_collision_pairs_observed": len(collisions),
        "shown_collision_pairs": rows,
        "connected_cross_window_groups_observed": len(clusters),
        "shown_connected_cross_window_groups": clusters[:MAX_CROSS_WINDOW_CLUSTERS],
        "connected_cross_window_groups_truncated":
            len(clusters) > MAX_CROSS_WINDOW_CLUSTERS,
        "groups_are_source_observations_not_ownership_or_merge_authority": True,
        "impacted_existing_prs_observed": len(ranking),
        "prioritized_existing_prs": ranking[:40],
        "prioritized_existing_prs_truncated": len(ranking) > 40,
        "cross_snapshot_issue_number_hints_observed": len(cross_issue_groups),
        "shown_issue_number_hints": issue_cards,
        "issue_hint_groups_truncated": len(cross_issue_groups) > MAX_CROSS_ISSUES,
        "issue_identity_verified_for_aggregate": False,
        "collision_pairs_truncated": len(collisions) > MAX_PAIRS,
        "prs_with_partial_file_scope": partial_prs[:48],
        "partial_file_scope_list_truncated": len(partial_prs) > 48,
        "source_complete_for_all_open_prs": False,
        "source_is_atomic_current_snapshot": False,
        "worker_claims_verified": False,
        "prless_branches_verified": False,
        "required_ci_and_reviews_verified": False,
        "merge_authorized": False,
        "claim_authorized": False,
        "deploy_authorized": False,
        "writes_performed": False,
    }


def combine(paths, repository):
    if not 2 <= len(paths) <= MAX_INPUTS:
        raise ValueError("supply two to six private graph snapshots")
    return combine_sources([read_bounded(path) for path in paths], repository)


def collect_live(repository, token, *, start_page=1, windows=2, prs_per_page=8):
    """Read-only bounded current GitHub GET collection, no queue or writer.

    Each window contains exactly two adjacent pages so the source collector
    uses contiguous page geometry instead of single-page lookahead. Results
    are never promoted to a globally atomic or claim-verified inventory.
    """
    if (not REPO.fullmatch(repository)
            or type(start_page) is not int or not 1 <= start_page <= 50
            or type(windows) is not int or not 2 <= windows <= MAX_LIVE_WINDOWS
            or type(prs_per_page) is not int or not 1 <= prs_per_page <= 12
            or windows * 2 * prs_per_page > MAX_LIVE_PRS
            or start_page + 2 * windows - 1 > 50):
        raise ValueError("live graph scan exceeds bounded GET or page limits")
    evidence = []
    for index in range(windows):
        graph = observe_conflict_graph(
            repository, token=token, limit=prs_per_page,
            open_page=start_page + 2 * index,
            page_count=2, resolve_issue_identity=False)
        encoded = json.dumps(graph, ensure_ascii=True, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_SOURCE_BYTES:
            raise ValueError("one live graph inventory is oversized")
        evidence.append((graph, len(encoded)))
    combined = combine_sources(evidence, repository)
    # Explicit provenance: the operator knows whether these were supplied
    # private saved files or freshly collected bounded GitHub GET windows.
    # Sequential GET windows remain non-atomic and cannot grant a claim.
    combined["collection_mode"] = "current_run_bounded_github_GET_only"
    combined["live_collection_observed_windows"] = windows
    combined["live_collection_prs_per_page"] = prs_per_page
    combined["live_collection_max_http_gets"] = windows * (4 + 2 * prs_per_page)
    return combined


MAX_CSV_RECORDS = 3000


def render_private_csv(report):
    """Private sortable handoff of the existing bounded operator evidence.

    Source inventory, PR worklist, real filename collisions and unverified
    issue-number text hints have separate record types. Never infer a safe
    owner, a merge conflict, a complete inventory, or release permission.
    """
    output = io.StringIO(newline="")
    writer = csv.writer(output, dialect="excel", lineterminator="\r\n")
    columns = (
        "record_type", "repository", "source_mode", "source_windows_utc",
        "group_number", "group_pr_count", "group_shared_files_observed",
        "filename", "file_cross_window", "file_pr_count", "pr_number",
        "pr_url", "pr_head_sha", "pr_source_window", "related_pr_url",
        "related_pr_head_sha", "related_pr_source_window", "pair_shared_files",
        "cross_window_pairs_for_pr", "shared_files_for_pr",
        "unverified_issue_number", "unverified_issue_mentions_for_pr",
        "source_complete_for_all_open_prs", "claims_verified",
        "merge_authorized", "operator_notice"
    )
    writer.writerow(columns)
    count = 0
    defaults = {
        "repository": report["repository"],
        "source_mode": report["collection_mode"],
        "source_windows_utc": " / ".join(report["observation_window_utc"]),
        "source_complete_for_all_open_prs": "false",
        "claims_verified": "false",
        "merge_authorized": "false",
    }
    def safe(value):
        original = str(value)
        # No active control bytes inside CSV fields. Visible escapes preserve
        # operator evidence of unusual git paths without spreadsheet row breaks.
        raw = "".join(
            "\\u%04x" % ord(char) if ord(char) < 32 or ord(char) == 127
            else char for char in original
        )
        # Quote dangerous formula prefixes, including after invisible Unicode
        # format characters that some spreadsheet parsers strip automatically.
        prefix = raw.lstrip(" \u200b\u200c\u200d\u2060\ufeff")
        if prefix.startswith(("=", "+", "-", "@")):
            return "'" + raw
        return raw

    def row(kind, **facts):
        nonlocal count
        if not set(facts).issubset(columns):
            raise ValueError("unknown private CSV source field")
        count += 1
        if count > MAX_CSV_RECORDS:
            raise ValueError("private CSV row budget exceeded")
        data = {**defaults, "record_type": kind, **facts}
        writer.writerow([safe(data.get(column, "")) for column in columns])
        # Character count is a fast preflight. The caller additionally checks
        # actual UTF-8 byte length including the BOM before creating a file.
        if output.tell() > MAX_REPORT_BYTES:
            raise ValueError("private CSV too large")

    row("source_summary", operator_notice=(
        "OBSERVED ONLY: read-only, non-atomic partial PR evidence; "
        "owner, required CI, review, Git conflicts and release UNKNOWN."))
    for pr in report["prioritized_existing_prs"]:
        row("existing_pr_priority", pr_number=pr["pr_number"],
            pr_url=pr["url"], pr_head_sha=pr["head_sha_observed"],
            pr_source_window=pr["snapshot"],
            cross_window_pairs_for_pr=pr["cross_window_file_pair_count"],
            shared_files_for_pr=pr["cross_window_shared_file_count"],
            unverified_issue_mentions_for_pr=pr["cross_window_number_hint_count"],
            operator_notice="PR to inspect, not a claim or integration order.")
    for group in report["shown_connected_cross_window_groups"]:
        for file_row in group["shared_file_ownership_samples"]:
            for pr in file_row["prs"]:
                row("shared_file_pr", group_number=group["group_number_in_this_observation"],
                    group_pr_count=group["pr_count_observed"],
                    group_shared_files_observed=group["distinct_shared_files_observed"],
                    filename=file_row["path"],
                    file_cross_window="true" if file_row["cross_window_overlap_observed"]
                                      else "false",
                    file_pr_count=file_row["participating_pr_count_observed"],
                    pr_number=pr["number"], pr_url=pr["url"],
                    pr_head_sha=pr["head_sha"],
                    pr_source_window=pr["source_window"],
                    operator_notice="Shared filename only, not a proven git conflict.")
        if group["shared_file_samples_truncated"]:
            row("source_warning",
                group_number=group["group_number_in_this_observation"],
                group_pr_count=group["pr_count_observed"],
                group_shared_files_observed=group["distinct_shared_files_observed"],
                operator_notice="Additional shared filenames in this group are NOT shown.")
    for pair in report["shown_collision_pairs"]:
        row("cross_window_collision_pair", pr_url=pair["left_url"],
            pr_head_sha=pair["left_head_sha"],
            pr_source_window=pair["left_snapshot"],
            related_pr_url=pair["right_url"],
            related_pr_head_sha=pair["right_head_sha"],
            related_pr_source_window=pair["right_snapshot"],
            pair_shared_files=pair["shared_files_observed"],
            operator_notice="Same filename in both PRs, not a verified git conflict.")
    for group in report["shown_issue_number_hints"]:
        for pr in group["shown_prs"]:
            row("unverified_issue_number_hint",
                unverified_issue_number=group["number"],
                pr_url=pr["url"], pr_head_sha=pr["head_sha"],
                pr_source_window=pr["snapshot"],
                operator_notice="Unverified text number; not issue identity, owner or fix.")
        if group["pr_list_truncated"]:
            row("source_warning", unverified_issue_number=group["number"],
                operator_notice="Additional source PRs mention this unverified number.")
    warnings = (
        ("connected_cross_window_groups_truncated",
         "More connected PR groups exist beyond this bounded view."),
        ("prioritized_existing_prs_truncated",
         "Additional existing PRs excluded from the ranked sample."),
        ("collision_pairs_truncated",
         "More shared filename pairs exist than this CSV displays."),
        ("issue_hint_groups_truncated",
         "Additional unverified issue-number groups were not exported."),
        ("partial_file_scope_list_truncated",
         "Additional source PRs have incomplete changed-file lists."),
    )
    for flag, message in warnings:
        if report[flag]:
            row("source_warning", operator_notice=message)
    if report["prs_with_partial_file_scope"]:
        row("source_warning", operator_notice=(
            "One or more PR source file lists were incomplete; no absence proof."))
    return "\ufeff" + output.getvalue()


def render(report, fmt):
    if fmt == "csv":
        return render_private_csv(report)
    lines = [
        "# zSentinel — privé cross-window PR-bestandsbotsingen",
        "",
        "Repository: " + report["repository"],
        "Bronmomenten (UTC): " + " tot ".join(report["observation_window_utc"]),
        "Bronmodus: " + report["collection_mode"] +
        " (niet-atomair, geen owner- of mergevrijgave)",
        "Opgeslagen momentopnamen: " + str(report["snapshot_count"]) +
        " · PRs: " + str(report["total_observed_prs"]),
        "Geziene GitHub-PR-pagina's: " +
        ", ".join(map(str, report["observed_source_page_numbers"])),
        "Nieuwe OVER-PAGINA bestandsparen: " +
        str(report["cross_snapshot_file_collision_pairs_observed"]),
        "Verbonden groepen over PR-pagina's: " +
        str(report["connected_cross_window_groups_observed"]),
        "Geen atomische live bron, geen eigenaarsclaim en geen mergevrijgave.",
        "",
    ]
    lines.extend(["## Bestaande PR's om eerst te inspecteren", "",
                  "Sortering: echte gedeelde bestanden, daarna tekstuele "
                  "issue/PR-nummerhints; nooit merge- of claimvrijgave.", ""])
    for item in report["prioritized_existing_prs"]:
        lines.append("- [PR #" + str(item["pr_number"]) + "](" + item["url"] +
                     ") · " + str(item["cross_window_file_pair_count"]) +
                     " bestandspaar-botsingen / " +
                     str(item["cross_window_shared_file_count"]) +
                     " gedeelde bestanden · " +
                     str(item["cross_window_number_hint_count"]) +
                     " ongeverifieerde issue-/PR-nummerhints")
    if not report["prioritized_existing_prs"]:
        lines.append("Geen getroffen bestaande PRs in deze bronvensters gezien.")
    if report["prioritized_existing_prs_truncated"]:
        lines.append("Overige bestaande PRs buiten de verkorte werklijst.")
    lines.extend(["", "## Samenhangende PR-groepen over bronvensters", "",
                  "Ook binnen-venster bestandsverbindingen tellen mee bij het bepalen " +
                  "van groepen. Alleen geobserveerde bron-PR's: geen eigenaar, " +
                  "Git-mergeconflict of toestemming.", ""])
    for group in report["shown_connected_cross_window_groups"]:
        lines.append("### Groep " + str(group["group_number_in_this_observation"]) +
                     " · " + str(group["pr_count_observed"]) +
                     " PR's · pagina's " +
                     "/".join(map(str, group["source_windows_observed"])))
        lines.append(str(group["cross_window_filename_edges_observed"]) +
                     " directe over-venster PR-paren; " +
                     str(group["within_window_filename_edges_observed"]) +
                     " binnen-venster PR-paren; " +
                     str(group["distinct_shared_files_observed"]) +
                     " verschillende gedeelde bestanden.")
        for pr in group["participating_prs"]:
            lines.append("- [PR #" + str(pr["number"]) + "](" + pr["url"] +
                         ") · venster " + str(pr["source_window"]) +
                         " · HEAD " + pr["head_sha"][:12] +
                         " · directe bestandspadverbindingen " +
                         str(pr["direct_filename_neighbors_observed"]))
        for file_row in group["shared_file_ownership_samples"]:
            lines.append("  - Bestandsmatrix: " + file_row["path"].replace("\n", " ") +
                         " · " + str(file_row["participating_pr_count_observed"]) +
                         " PRs, bronvensters " +
                         "/".join(map(str, file_row["source_windows_observed"])) +
                         (" · OVER pagina's" if file_row["cross_window_overlap_observed"]
                          else " · binnen één venster"))
            for related in file_row["prs"]:
                lines.append("    - [PR #" + str(related["number"]) +
                             "](" + related["url"] + ") · venster " +
                             str(related["source_window"]) + " · HEAD " +
                             related["head_sha"][:12])
        if group["shared_file_samples_truncated"]:
            lines.append("  - Aanvullende gedeelde bestanden niet getoond.")
    if not report["shown_connected_cross_window_groups"]:
        lines.append("Geen verbonden groepen over de bekeken vensters waargenomen.")
    if report["connected_cross_window_groups_truncated"]:
        lines.append("Meer verbonden groepen bestaan buiten de begrensde weergave.")
    lines.extend(["", "## Cross-window bestandsparen", ""])
    for row in report["shown_collision_pairs"]:
        lines.append("- [PR](" + row["left_url"] + ") ↔ [PR](" +
                     row["right_url"] + ") · " +
                     str(row["shared_files_observed"]) +
                     " gedeelde bestandspaden, bronmomenten " +
                     str(row["left_snapshot"]) + "/" + str(row["right_snapshot"]))
        for name in row["shared_paths"]:
            lines.append("    - " + name.replace("\n", " "))
        if row["shared_paths_truncated"]:
            lines.append("    - … overige paden niet getoond")
    if not report["shown_collision_pairs"]:
        lines.append("Geen cross-window botsing in deze bestanden gezien; GEEN vrijgave.")
    lines.extend([
        "",
        "## Mogelijk dubbel productwerk: zelfde issue-nummer OVER vensters",
        "",
        "Dit zijn ongeverifieerde tekstverwijzingen, geen bewezen issuefix.",
    ])
    for group in report["shown_issue_number_hints"]:
        lines.append("- [Nummer #" + str(group["number"]) + "](" +
                     group["unverified_reference_url"] + ") · " +
                     str(group["observed_distinct_prs"]) + " PR-bronvermeldingen")
        for entry in group["shown_prs"]:
            lines.append("  - [PR](" + entry["url"] + ") · bronvenster " +
                         str(entry["snapshot"]) + " · " +
                         "/".join(entry["mentioned_in"]) +
                         " · HEAD " + entry["head_sha"][:12])
        if group["pr_list_truncated"]:
            lines.append("  - overige gekoppelde PR-bronnen niet getoond")
    if not report["shown_issue_number_hints"]:
        lines.append("Geen nieuw dubbel nummer in deze vensters gezien; geen vrijgave.")
    if report["issue_hint_groups_truncated"]:
        lines.append("Extra nummergroepen buiten de begrensde weergave.")
    if report["collision_pairs_truncated"]:
        lines.append("Meer conflicten buiten de begrensde weergave.")
    if report["prs_with_partial_file_scope"]:
        lines.append("WAARSCHUWING: enkele PR-bestandlijsten zijn niet volledig.")
    lines.extend([
        "",
        "Vereiste CI, review, huidige Notion-claims en production owner "
        "worden in dit rapport niet beoordeeld.",
        "",
    ])
    if fmt == "markdown":
        return "\n".join(lines)
    if fmt == "text":
        return "\n".join(line.replace("**", "") for line in lines)
    if fmt == "json":
        return json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    rows = "".join(
        "<tr><td><a rel='noopener noreferrer' href='" +
        escape(item["left_url"], quote=True) + "'>PR</a> ↔ "
        "<a rel='noopener noreferrer' href='" +
        escape(item["right_url"], quote=True) + "'>PR</a></td><td>" +
        str(item["shared_files_observed"]) + "</td><td>" +
        "<br>".join(escape(name) for name in item["shared_paths"]) +
        "</td></tr>"
        for item in report["shown_collision_pairs"])
    shortlist_rows = "".join(
        "<li><a rel='noopener noreferrer' href='" +
        escape(item["url"], quote=True) + "'>PR #" +
        str(item["pr_number"]) + "</a> · " +
        str(item["cross_window_file_pair_count"]) +
        " botsende PR-paren / " +
        str(item["cross_window_shared_file_count"]) +
        " gedeelde bestanden · " +
        str(item["cross_window_number_hint_count"]) +
        " onzekere issue-/PR-nummers</li>"
        for item in report["prioritized_existing_prs"])
    cluster_rows = []
    for group in report["shown_connected_cross_window_groups"]:
        linked = "".join(
            "<li><a rel='noopener noreferrer' href='" +
            escape(pr["url"], quote=True) + "'>PR #" +
            str(pr["number"]) + "</a> · venster " +
            str(pr["source_window"]) + " · HEAD " +
            escape(pr["head_sha"][:12]) + " · " +
            str(pr["direct_filename_neighbors_observed"]) +
            " directe bestandsverbindingen</li>"
            for pr in group["participating_prs"])
        samples = "".join(
            "<li><code>" + escape(file_row["path"]) + "</code> · " +
            str(file_row["participating_pr_count_observed"]) + " PRs · " +
            ("over bronvensters" if file_row["cross_window_overlap_observed"]
             else "binnen bronvenster") + "<ul>" +
            "".join("<li><a rel='noopener noreferrer' href='" +
                    escape(pr["url"], quote=True) + "'>PR #" +
                    str(pr["number"]) + "</a> · venster " +
                    str(pr["source_window"]) + " · HEAD " +
                    escape(pr["head_sha"][:12]) + "</li>"
                    for pr in file_row["prs"]) + "</ul></li>"
            for file_row in group["shared_file_ownership_samples"])
        cluster_rows.append(
            "<li><strong>Groep " +
            str(group["group_number_in_this_observation"]) + "</strong> · " +
            str(group["pr_count_observed"]) + " PRs · " +
            str(group["cross_window_filename_edges_observed"]) +
            " over-vensterparen · " +
            str(group["within_window_filename_edges_observed"]) +
            " binnen-vensterparen · " +
            str(group["distinct_shared_files_observed"]) +
            " verschillende gedeelde bestanden<ul>" + linked +
            "</ul><details><summary>Gedeelde bestandspaden</summary><ul>" +
            samples + "</ul>" +
            ("<p>Extra gedeelde paden niet afgebeeld.</p>"
             if group["shared_file_samples_truncated"] else "") +
            "</details></li>")
    issue_rows = "".join(
        "<li><a rel='noopener noreferrer' href='" +
        escape(group["unverified_reference_url"], quote=True) + "'>Nummer #" +
        str(group["number"]) + "</a> · " +
        str(group["observed_distinct_prs"]) +
        " PR-vermeldingen (GEEN fix/issue-identiteitsbewijs)<ul>" +
        "".join(
            "<li><a rel='noopener noreferrer' href='" +
            escape(row["url"], quote=True) + "'>PR</a> · venster " +
            str(row["snapshot"]) + " · " +
            escape("/".join(row["mentioned_in"])) + "</li>"
            for row in group["shown_prs"]) + "</ul></li>"
        for group in report["shown_issue_number_hints"])
    return (
        "<!doctype html><html lang='nl'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta http-equiv='Content-Security-Policy' "
        "content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; connect-src 'none'\">"
        "<title>zSentinel · opgeslagen PR-vensters</title>"
        "<style>body{font:16px system-ui;background:#111820;color:#f0f3f8;"
        "margin:28px auto;max-width:1000px;padding:16px}"
        "table{width:100%;border-collapse:collapse}th,td{padding:12px;"
        "border-bottom:1px solid #445;text-align:left;vertical-align:top;"
        "overflow-wrap:anywhere}a{color:#b5d9ff}</style>"
        "<h1>zSentinel · cross-window PR-bestandsbotsingen</h1>"
        "<p>Repository: " + escape(report["repository"]) +
        " · momentopnamen: " + str(report["snapshot_count"]) +
        " · onderzochte PRs: " + str(report["total_observed_prs"]) +
        " · bron: " + escape(report["collection_mode"]) +
        "</p><aside>Geen atomische live bron, eigenaars- of mergevrijgave. "
        "Andere GitHub-pagina's blijven UNKNOWN.</aside>"
        "<h2>Bestaande PRs om eerst te inspecteren</h2><p>Bestandsbotsingen "
        "eerst, onzekere nummerhints daarna. GEEN vrijgave.</p><ol>" +
        (shortlist_rows or "<li>Geen botsing in de gelezen vensters.</li>") +
        "</ol><p>" +
        ("WAARSCHUWING: sommige PR-bestandlijsten zijn gedeeltelijk."
         if report["prs_with_partial_file_scope"] else
         "Alle afzonderlijk getoonde PR-bestandlijsten zijn binnen hun "
         "eigen bronvenster volledig; portfolio-dekking blijft onbekend.") +
        "</p><h2>Verbonden PR-groepen over bronvensters</h2>"
        "<p>Alleen waargenomen bestandsverbindingen, ook binnen bronpagina's. "
        "Geen mergevolgorde, Git-conflict of eigenaarsvrijgave.</p><ol>" +
        ("".join(cluster_rows) or "<li>Geen cross-window groepen gezien.</li>") +
        ("<li>Meer groepen buiten de begrensde weergave.</li>"
         if report["connected_cross_window_groups_truncated"] else "") +
        "</ol>"
        "<table><thead><tr><th>Open bron-PRs</th><th>Gedeelde paden</th>"
        "<th>Geobserveerde bestanden</th></tr></thead><tbody>" +
        (rows or "<tr><td colspan='3'>Geen botsing gezien, geen vrijgave.</td></tr>") +
        "</tbody></table><h2>Zelfde nummer in PR-titel of body over vensters</h2>"
        "<p>Niet geverifieerd als bestaand issue of bewezen fix. Controleer GitHub.</p>"
        "<ul>" + (issue_rows or "<li>Geen nieuwe nummeroverlap in deze "
                "steekproef, GEEN vrijgave.</li>") + "</ul></html>"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True,
                        help="Exacte canonieke Zennay/eigen-repo identiteit")
    parser.add_argument("--input", type=Path, action="append", default=[],
                        help="Privé JSON bron uit --audit-open-prs --format json")
    parser.add_argument("--live-scan", action="store_true",
                        help="Read-only: verzamel 2–3 opeenvolgende 2-pagina "
                             "bronvensters direct uit GitHub (max 36 PRs)")
    parser.add_argument("--start-page", type=int, default=1,
                        help="Startpagina voor expliciete begrensde --live-scan")
    parser.add_argument("--windows", type=int, default=2,
                        help="Aantal 2-pagina-bronvensters, 2 of 3")
    parser.add_argument("--prs-per-page", type=int, default=8,
                        help="1..12 PRs/pagina, samen <=36, max 48 GETs")
    parser.add_argument("--format", choices=("json", "markdown", "text", "html", "csv"),
                        default="html")
    parser.add_argument("--output", type=Path, required=True,
                        help="Nieuw privé bestand, nooit bestaande data overschrijven")
    args = parser.parse_args(argv)
    try:
        if args.live_scan:
            if args.input:
                raise ValueError("--live-scan cannot mix with saved --input files")
            report = collect_live(
                args.repo, os.environ.get("GITHUB_TOKEN"),
                start_page=args.start_page,
                windows=args.windows, prs_per_page=args.prs_per_page)
        else:
            if (args.start_page != 1 or args.windows != 2 or
                    args.prs_per_page != 8):
                raise ValueError("live scan flags require --live-scan")
            report = combine(args.input, args.repo)
        raw = render(report, args.format).encode("utf-8")
        if len(raw) > MAX_REPORT_BYTES:
            raise ValueError("output too large")
        fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            args.output.unlink(missing_ok=True)
            raise
        print("Private " + report["collection_mode"] +
              " cross-window PR handoff written; NO claim/merge authority.")
        return 0
    except (OSError, ValueError, UnicodeError, OverflowError, TypeError):
        print("Refused: invalid or conflicting saved PR windows; no actions.",
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
