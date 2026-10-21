#!/usr/bin/env python3
"""Offline, source-only merge-first owner CSV from admitted zSentinel board/ZIPs.

No network access, worker assignment, claim acquisition, CI retry, merge,
deployment, or modification of any original source is performed.
"""
from __future__ import annotations

import argparse
import csv
import json
from html import escape
from io import StringIO
from pathlib import Path
from datetime import datetime, timezone
import re
import sys
import unicodedata
from zipfile import BadZipFile

from zsentinel_operator_workbench import read_source_and_sha, validated_view
from zsentinel_product_delivery_board import write_create_only
from zsentinel_saved_dossier_timeline import build_timeline, source_freshness
from zsentinel_saved_issue_timeline import issue_worklist

RANK = {"P0": 0, "P1": 1, "P2": 2, "Unspecified": 3}
MAX_CSV_BYTES = 1024 * 1024
FIELDS = (
    "original_board_sha256", "board_observed_at_utc",
    "board_age_at_export", "archive_age_at_export",
    "device_clock_authoritative", "priority",
    "source_issue_urls", "source_issue_titles", "project_scope",
    "pr_reference_state", "pr_url", "linked_issue_count",
    "multiple_issues_share_pr", "issue_has_alternative_prs",
    "board_heads_observed", "archive_heads_observed",
    "head_variation_is_historical_not_merge_conflict",
    "historical_gate_labels", "sampled_board_unknown_gates",
    "pr_missing_last_saved_snapshot", "issue_link_missing_in_saved_source",
    "historical_candidates_truncated", "unlinked_in_saved_archive",
    "source_archive_manifest_sha256s", "saved_pr_archive_supplied",
    "saved_archive_batches_missing", "human_next_step_only",
    "claim_authorized", "dispatch_authorized", "required_ci_verified",
    "independent_review_verified", "main_merge_verified", "deploy_authorized",
    "live_state_verified",
    "selection_repository", "selection_priority", "selection_record_type",
    "selection_pr_url", "selection_issue_url",
    "source_record_count", "selected_record_count",
    "excluded_source_record_count", "selection_filter_applied",
)


def _cell(value):
    """Keep spreadsheet formulas inert, including Unicode-obfuscated prefixes.

    CSV quoting does not prevent spreadsheet formula execution. Preserve the
    original evidence except for control characters, and inspect past Unicode
    whitespace, separators, zero-width and bidirectional format marks before
    deciding whether to prefix the whole cell with an apostrophe.
    """
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    result = "".join(
        " " if unicodedata.category(char) == "Cc" else char
        for char in str(value)
    )
    index = 0
    while index < len(result):
        char = result[index]
        category = unicodedata.category(char)
        if char.isspace() or category in ("Cf", "Zs", "Zl", "Zp"):
            index += 1
            continue
        break
    if result[index:].startswith(("=", "+", "-", "@")):
        return "'" + result
    return result


def prepare_rows(view, board_sha, saved, exported_at=None):
    if saved is not None and (
        saved.get("owner_claim_verified") is not False
        or saved.get("main_merge_verified") is not False
    ):
        raise ValueError("historical PR source cannot authorize work")
    archives = saved["source_snapshots"] if saved is not None else []
    # Advisory source age is calculated at export time, not board creation.
    exported_at = exported_at or datetime.now(timezone.utc)
    board_age = source_freshness(
        [{"latest_observed_utc": view["observed_at_utc"]}],
        evaluated_at=exported_at)["status"]
    archive_age = (source_freshness(archives, evaluated_at=exported_at)["status"]
                   if archives else "not-provided")
    manifests = " | ".join(row["original_review_manifest_sha256"] for row in archives) or "not-provided"
    missing_batches = any(bool(row["missing_batch_indexes"]) for row in archives)
    historical = {row["issue_url"]: row for row in (saved["issues"] if saved else [])}
    unlinked = {row["url"]: row for row in (saved["unlinked_prs"] if saved else [])}
    if len(unlinked) != len(saved["unlinked_prs"] if saved else []):
        raise ValueError("duplicate unlinked source PR")
    if len(historical) != len(saved["issues"] if saved else []):
        raise ValueError("duplicate archived issue")
    mapped = {}
    unknown = []
    # A private archive may cover additional repositories. Keep unlinked
    # PRs only when their canonical repository has a selected original
    # issue-card counterpart; never broaden a handoff through ZIP scope.
    selected_repositories = {
        card["url"].split("/issues/", 1)[0] for card in view["cards"]
    }
    for card in view["cards"]:
        current = historical.get(card["url"])
        archive_refs = {p["pr_url"]: p for p in
                        current["linked_existing_prs"]} if current else {}
        refs = set(card["open_pr_candidates"]) | set(archive_refs)
        # The issue-first PR source is independently bounded to at most
        # 600 distinct saved PR identities. An issue can legitimately
        # reference more than twelve historical PRs: do not silently drop
        # them or reject a valid P0 owner worklist because of a UI-only
        # twelve-card display limit. The whole output stays capped below.
        if len(refs) > 600:
            raise ValueError("unbounded existing PR references for one issue")
        if not refs:
            unknown.append(card)
        # Board "project" is an internal slug (e.g. cloud), not the
        # GitHub owner/repository. The already validated issue URL is
        # authoritative for repository identity.
        repository_url, _, issue_number = card["url"].partition("/issues/")
        if not issue_number or not repository_url.startswith("https://github.com/"):
            raise ValueError("original board issue lacks canonical repository")
        for url in refs:
            if not re.fullmatch(
                re.escape(repository_url) + r"/pull/[1-9][0-9]{0,8}", url
            ):
                raise ValueError("cross-repository or noncanonical saved PR candidate")
            slot = mapped.setdefault(url, {
                "url": url, "issues": {}, "board_heads": set(),
                "archive_heads": set(), "gates": set(),
                "unknown_gates": set(), "missing": False,
                "lost_link": False, "truncated": False,
            })
            slot["issues"][card["issue"]] = (card, len(refs) > 1)
            board = next((p for p in card["pr_gate_observations"]
                          if p["url"] == url), None)
            if board:
                slot["board_heads"].add(board["head_sha"])
                slot["unknown_gates"].update(board["blocking_unknowns"])
            archived = archive_refs.get(url)
            if archived:
                for key in ("observed_head", "head_at_last_issue_link"):
                    if archived.get(key):
                        slot["archive_heads"].add(archived[key])
                slot["gates"].add(archived["observed_primary_gate"])
                slot["missing"] |= archived["missing_from_final_sample"]
                slot["lost_link"] |= (archived["issue_ref_absent_in_last_snapshot"]
                                      or archived["pr_observed_after_link_absent"])
                slot["truncated"] |= len(current["linked_existing_prs"]) > 12

    shared = {
        "original_board_sha256": board_sha,
        "board_observed_at_utc": view["observed_at_utc"],
        "board_age_at_export": board_age,
        "archive_age_at_export": archive_age,
        "device_clock_authoritative": False,
        "source_archive_manifest_sha256s": manifests,
        "saved_pr_archive_supplied": bool(archives),
        "saved_archive_batches_missing": missing_batches,
        "claim_authorized": False, "dispatch_authorized": False,
        "required_ci_verified": False, "independent_review_verified": False,
        "main_merge_verified": False, "deploy_authorized": False,
        "live_state_verified": False,
    }
    rows = []
    for pr in mapped.values():
        issues = sorted((item for item, _ in pr["issues"].values()),
                        key=lambda item: (RANK[item["priority"]], item["issue"]))
        heads = pr["board_heads"] | pr["archive_heads"]
        alternative = any(value for _, value in pr["issues"].values())
        variation = len(heads) > 1
        action = ("inspect_competing_existing_prs_and_owner_claims" if alternative else
                  "refresh_current_exact_head_and_required_gates" if variation else
                  "refresh_live_pr_and_issue_association" if pr["missing"] or pr["lost_link"] else
                  "inspect_existing_pr_exact_head_owner_ci_review_and_protection")
        rows.append({
            **shared, "priority": issues[0]["priority"],
            "source_issue_urls": " | ".join(i["url"] for i in issues),
            "source_issue_titles": " | ".join(i["title"] for i in issues),
            "project_scope": " | ".join(sorted({i["project"] for i in issues})),
            "pr_reference_state": "saved_pr_candidate_not_live",
            "pr_url": pr["url"], "linked_issue_count": len(issues),
            "multiple_issues_share_pr": len(issues) > 1,
            "issue_has_alternative_prs": alternative,
            "board_heads_observed": " | ".join(sorted(pr["board_heads"])),
            "archive_heads_observed": " | ".join(sorted(pr["archive_heads"])),
            "head_variation_is_historical_not_merge_conflict": variation,
            "historical_gate_labels": " | ".join(sorted(pr["gates"])),
            "sampled_board_unknown_gates": " | ".join(sorted(pr["unknown_gates"])),
            "pr_missing_last_saved_snapshot": pr["missing"],
            "issue_link_missing_in_saved_source": pr["lost_link"],
            "historical_candidates_truncated": pr["truncated"],
            "unlinked_in_saved_archive": pr["url"] in unlinked,
            "human_next_step_only": action,
        })
    for card in unknown:
        rows.append({
            **shared, "priority": card["priority"],
            "source_issue_urls": card["url"],
            "source_issue_titles": card["title"],
            "project_scope": card["project"],
            "pr_reference_state": "not_observed_in_bounded_sources_unknown",
            "pr_url": "", "linked_issue_count": 1,
            "multiple_issues_share_pr": False, "issue_has_alternative_prs": False,
            "board_heads_observed": "UNKNOWN", "archive_heads_observed": "UNKNOWN",
            "head_variation_is_historical_not_merge_conflict": False,
            "historical_gate_labels": "UNKNOWN",
            "sampled_board_unknown_gates": "UNKNOWN",
            "pr_missing_last_saved_snapshot": False,
            "issue_link_missing_in_saved_source": False,
            "historical_candidates_truncated": False,
            "unlinked_in_saved_archive": False,
            "human_next_step_only": "check_live_issue_pr_inventory_and_owner_before_new_work",
        })
    # The original saved ZIP inventory can contain PRs without any issue
    # link in its bounded sample. They still exist as previously observed
    # PR candidates and must not disappear from a merge-first owner handoff.
    for pr_url, unlinked_pr in unlinked.items():
        if pr_url in mapped:
            continue  # Original issue board had an independent PR link.
        repo = pr_url.split("/pull/", 1)[0]
        if repo not in selected_repositories:
            continue  # Outside selected board issue repository scope.
        if not re.fullmatch(
            r"https://github[.]com/Zennay/[A-Za-z0-9_.-]{1,100}", repo
        ):
            raise ValueError("noncanonical unlinked historical PR")
        rows.append({
            **shared, "priority": unlinked_pr["priority"],
            "source_issue_urls": "",
            "source_issue_titles": "No issue relationship observed in bounded PR ZIP",
            "project_scope": repo.removeprefix("https://github.com/"),
            "pr_reference_state": "historical_pr_without_sampled_issue_link",
            "pr_url": pr_url, "linked_issue_count": 0,
            "multiple_issues_share_pr": False,
            "issue_has_alternative_prs": False,
            "board_heads_observed": "UNKNOWN", "archive_heads_observed": "UNKNOWN",
            "head_variation_is_historical_not_merge_conflict": False,
            "historical_gate_labels": "UNKNOWN",
            "sampled_board_unknown_gates": "UNKNOWN",
            "pr_missing_last_saved_snapshot": unlinked_pr["missing_from_final_sample"],
            "issue_link_missing_in_saved_source": False,
            "historical_candidates_truncated": False,
            "unlinked_in_saved_archive": True,
            "human_next_step_only": "inspect_unlinked_existing_pr_current_owner_and_issue_relations",
        })
    rows.sort(key=lambda row: (RANK[row["priority"]],
                               row["pr_reference_state"] != "saved_pr_candidate_not_live",
                               row["pr_url"] or row["source_issue_urls"]))
    if len(rows) > 900:
        raise ValueError("source-only PR worklist exceeds bounded inventory")
    # No pagination or truncation in the machine-readable owner handoff:
    # either preserve all admitted candidates, or refuse before writing.
    return rows


def select_owner_rows(rows, *, repository=None, priority=None, record_type="all",
                      pr_url=None, issue_url=None):
    """Narrow a fully admitted historical owner list without granting ownership.

    Repository identity always comes from canonical issue/PR URLs, not from
    inconsistent internal project slugs. A missing match never means that
    the complete live issue/PR inventory was checked.
    """
    if repository is not None and not re.fullmatch(
        r"Zennay/[A-Za-z0-9_.-]{1,100}", repository
    ):
        raise ValueError("repository filter must be canonical Zennay/<repo>")
    if priority is not None and priority not in RANK:
        raise ValueError("unrecognized owner priority filter")
    if record_type not in ("all", "existing-pr", "issue-without-pr", "unlinked-pr"):
        raise ValueError("unrecognized owner record type")
    if pr_url is not None and not re.fullmatch(
        r"https://github[.]com/Zennay/[A-Za-z0-9_.-]{1,100}/pull/[1-9][0-9]{0,8}",
        pr_url
    ):
        raise ValueError("PR selector must be a canonical full GitHub PR URL")
    if issue_url is not None and not re.fullmatch(
        r"https://github[.]com/Zennay/[A-Za-z0-9_.-]{1,100}/issues/[1-9][0-9]{0,8}",
        issue_url
    ):
        raise ValueError("issue selector must be a canonical full GitHub issue URL")
    result = []
    for row in rows:
        original = row["pr_url"] or row["source_issue_urls"].split(" | ", 1)[0]
        source_repo = original.split("/pull/", 1)[0].split("/issues/", 1)[0]
        if repository and source_repo != "https://github.com/" + repository:
            continue
        if priority and row["priority"] != priority:
            continue
        if pr_url is not None and row["pr_url"] != pr_url:
            continue
        if issue_url is not None and issue_url not in row["source_issue_urls"].split(" | "):
            continue
        state = row["pr_reference_state"]
        if record_type == "existing-pr" and not row["pr_url"]:
            continue
        if record_type == "issue-without-pr" and row["pr_url"]:
            continue
        if record_type == "unlinked-pr" and state != "historical_pr_without_sampled_issue_link":
            continue
        result.append(row)
    # Exact PR/issue packets must exist in the admitted source selection.
    # An absent observation is not evidence that work has been completed.
    if (pr_url is not None or issue_url is not None) and not result:
        raise ValueError(
            "requested PR/issue not observed in this bounded source selection; "
            "refresh current evidence before preparing an owner packet")
    return result


def selection_metadata(total, rows, *, repository=None, priority=None,
                       record_type="all", pr_url=None, issue_url=None):
    """Explicit whole-source vs selected coverage for every export format."""
    return {
        "selection_repository": repository or "ALL_SOURCE_REPOSITORIES",
        "selection_priority": priority or "ALL_SOURCE_PRIORITIES",
        "selection_record_type": record_type,
        "selection_pr_url": pr_url or "NOT_SELECTED",
        "selection_issue_url": issue_url or "NOT_SELECTED",
        "source_record_count": total,
        "selected_record_count": len(rows),
        "excluded_source_record_count": total - len(rows),
        "selection_filter_applied": bool(
            repository or priority or record_type != "all" or pr_url or issue_url),
    }


def render_private_html(rows, source_metadata=None):
    """One standalone script-free private owner worklist; source evidence only."""
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta name="referrer" content="no-referrer">',
        '<meta http-equiv="Content-Security-Policy" content="default-src '
        "'none'; style-src 'unsafe-inline'; script-src 'none'; "
        "img-src 'none'; connect-src 'none'; form-action 'none'; "
        "frame-src 'none'; object-src 'none'; base-uri 'none'" '>',
        '<title>zSentinel · Saved PR owner worklist</title>',
        '<style>body{font:15px/1.55 system-ui,sans-serif;max-width:900px;'
        'margin:20px auto;padding:0 16px;color:#14243b;background:#fff}'
        'a{color:#075b91}article{border:1px solid #afc0d0;border-radius:8px;'
        'margin:12px 0;padding:14px;break-inside:avoid}p,small{overflow-wrap:'
        'anywhere}small{display:block;color:#42556c}@media print{body{margin:0}}'
        '</style></head><body><h1>zSentinel · P0-first existing PR review</h1>',
        '<p>SAVED source only. Active owner, required exact-head CI, independent '
        'review, main merge and production release: NOT VERIFIED.</p>',
        '<p>' + str(len(rows)) + ' source-bound records, including issues '
        'without an observed PR. A missing PR is UNKNOWN, not a clear queue.</p>',
    ]
    metadata = source_metadata if isinstance(source_metadata, dict) else (
        rows[0] if rows else {})
    board_age = metadata.get("board_age_at_export", "unknown")
    archive_age = metadata.get("archive_age_at_export", "not-provided")
    board_sha = metadata.get("original_board_sha256", "UNKNOWN")
    board_moment = metadata.get("board_observed_at_utc", "UNKNOWN")
    manifests = metadata.get("source_archive_manifest_sha256s", "not-provided")
    archive_supplied = metadata.get("saved_pr_archive_supplied", "UNKNOWN")
    missing_batches = metadata.get("saved_archive_batches_missing", "UNKNOWN")
    selection = {key: metadata.get(key, "UNKNOWN") for key in (
        "selection_repository", "selection_priority", "selection_record_type",
        "selection_pr_url", "selection_issue_url",
        "source_record_count", "selected_record_count",
        "excluded_source_record_count", "selection_filter_applied")}
    parts.append(
        '<section aria-label="Owner selection and bounded coverage">'
        '<h2>Owner selection · saved source scope</h2>'
        '<p>Repository: ' + escape(str(selection["selection_repository"])) +
        ' · priority: ' + escape(str(selection["selection_priority"])) +
        ' · record type: ' + escape(str(selection["selection_record_type"])) + '</p>'
        '<p>Exact selected PR: ' + escape(str(selection["selection_pr_url"])) +
        ' · exact issue: ' + escape(str(selection["selection_issue_url"])) + '</p>'
        '<p>Source records before filtering: ' +
        escape(str(selection["source_record_count"])) +
        ' · displayed: ' + escape(str(selection["selected_record_count"])) +
        ' · excluded from this selection: ' +
        escape(str(selection["excluded_source_record_count"])) + '</p>'
        '<p>Filtered: ' + escape(str(selection["selection_filter_applied"])) +
        '. Excluded or absent entries are UNKNOWN, not fixed, merged or free.</p>'
        '</section>'
    )
    if not rows:
        parts.append('<p role="status">No saved records match this selection. '
                     'This is NOT a current PR inventory or backlog clearance.</p>')
    parts.append(
        '<section aria-label="Original source custody"><h2>Original source custody</h2>'
        '<p>Original board SHA256: ' + escape(str(board_sha)) +
        ' · source UTC: ' + escape(str(board_moment)) + '</p>'
        '<p>Saved PR archive supplied: ' + escape(str(archive_supplied)) +
        ' · manifest SHA256(s): ' + escape(str(manifests)) +
        ' · missing batches: ' + escape(str(missing_batches)) + '</p>'
        '<p>Board age at export: ' + escape(str(board_age)) +
        ' · historical PR archive age: ' + escape(str(archive_age)) +
        '. Device time is advisory; this is not live GitHub or worker validation.</p>'
        '</section>'
    )
    for row in rows:
        priority = escape(str(row["priority"]))
        has_pr = bool(row["pr_url"])
        parts.append('<article><h2>' + priority + ' · ' +
                     ('Existing saved PR candidate' if has_pr else
                      'PR relationship UNKNOWN') + '</h2>')
        parts.append('<p><strong>' + escape(str(row["source_issue_titles"])) +
                     '</strong> · ' + escape(str(row["project_scope"])) + '</p>')
        if not row["source_issue_urls"]:
            parts.append('<p>No linked source issue was observed for '
                         'this existing PR. Relation to a current issue '
                         'and present owner remain UNKNOWN.</p>')
        for url in filter(None, row["source_issue_urls"].split(" | ")):
            if not re.fullmatch(
                r'https://github[.]com/Zennay/[A-Za-z0-9_.-]{1,100}/issues/[1-9][0-9]{0,8}', url
            ):
                raise ValueError("noncanonical original issue URL")
            safe = escape(url, quote=True)
            parts.append('<p>Issue: <a href="' + safe +
                         '" target="_blank" rel="noopener noreferrer">' +
                         safe + '</a></p>')
        if has_pr:
            url = row["pr_url"]
            if not re.fullmatch(
                r'https://github[.]com/Zennay/[A-Za-z0-9_.-]{1,100}/pull/[1-9][0-9]{0,8}', url
            ):
                raise ValueError("noncanonical saved PR URL")
            safe = escape(url, quote=True)
            parts.append('<p>Existing PR: <a href="' + safe +
                         '" target="_blank" rel="noopener noreferrer">' +
                         safe + '</a></p>')
        else:
            parts.append('<p>No saved PR reference found. Inspect the live '
                         'GitHub issue and current owner before creating work.</p>')
        parts.append('<small>Original issue-board HEAD(s): ' +
                     escape(str(row["board_heads_observed"])) +
                     ' · historical archive HEAD(s): ' +
                     escape(str(row["archive_heads_observed"])) + '</small>')
        if row["head_variation_is_historical_not_merge_conflict"]:
            parts.append('<p>Saved HEAD observations differ; this is NOT '
                         'proof of conflict. Refresh current exact HEAD.</p>')
        if row["issue_has_alternative_prs"]:
            parts.append('<p>Multiple saved PR candidates for an issue; '
                         'check current owners before overlapping work.</p>')
        parts.append('<p>Owner next step (advisory): ' +
                     escape(str(row["human_next_step_only"])) + '</p>')
        # Printed handoffs must contain the actual provenance without
        # depending on recipients expanding a collapsed disclosure widget.
        parts.append('<section aria-label="Original source custody">'
                     '<h3>Original source custody</h3>'
                     '<small>Original board SHA256: ' +
                     escape(str(row["original_board_sha256"])) +
                     ' · UTC ' + escape(str(row["board_observed_at_utc"])) +
                     '</small><small>Saved archive manifest SHA256s: ' +
                     escape(str(row["source_archive_manifest_sha256s"])) +
                     ' · archive supplied: ' + escape(str(row["saved_pr_archive_supplied"])) +
                     '</small><small>Missing saved batches: ' +
                     escape(str(row["saved_archive_batches_missing"])) +
                     '; historic gate labels: ' +
                     escape(str(row["historical_gate_labels"])) +
                     '; PR absent final sample: ' +
                     escape(str(row["pr_missing_last_saved_snapshot"])) +
                     '</small></section>')
        parts.append('<small>Claim / CI / approval / merge / dispatch / '
                     'deployment: NOT AUTHORIZED.</small></article>')
    parts.append('<p>Recheck current GitHub/Notion/queue source and all '
                 'normal exact-head security and release gates before '
                 'any action.</p></body></html>\n')
    return ''.join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--pr-dossier", action="append", type=Path,
                        help="Optional original private PR archive ZIP; repeat 1-12")
    parser.add_argument("--format", choices=("csv", "html", "json"), default="csv",
                        help="P0-first CSV, printable HTML or structured source-only JSON")
    parser.add_argument("--repository",
                        help="Optional canonical repository filter, e.g. Zennay/zCloud")
    parser.add_argument("--priority", choices=tuple(RANK),
                        help="Filter P0/P1/P2/Unspecified after full source admission")
    parser.add_argument("--record-type", default="all",
                        choices=("all", "existing-pr", "issue-without-pr", "unlinked-pr"),
                        help="Select saved PRs, absent sampled PR links or unlinked PRs")
    parser.add_argument("--inspect-pr-url",
                        help="Create a source-observed exact-PR owner packet")
    parser.add_argument("--inspect-issue-url",
                        help="Select rows referencing an exact canonical source issue")
    parser.add_argument("--output", type=Path, required=True,
                        help="New private create-only destination")
    args = parser.parse_args(argv)
    try:
        if args.output.suffix != "." + args.format:
            raise ValueError("output suffix must match selected format")
        sources = [args.board, *(args.pr_dossier or [])]
        if len(sources) > 13 or len({str(x.resolve()) for x in sources + [args.output]}) != len(sources) + 1:
            raise ValueError("source archives, board and output must be distinct")
        source, sha = read_source_and_sha(args.board)
        view = validated_view(source)
        saved = None
        if args.pr_dossier:
            saved = issue_worklist(build_timeline(args.pr_dossier, minimum_sources=1))
        exported_at = datetime.now(timezone.utc)
        archives = saved["source_snapshots"] if saved is not None else []
        board_age = source_freshness(
            [{"latest_observed_utc": view["observed_at_utc"]}],
            evaluated_at=exported_at)["status"]
        archive_age = (source_freshness(archives, evaluated_at=exported_at)["status"]
                       if archives else "not-provided")
        archive_manifests = [
            item["original_review_manifest_sha256"] for item in archives]
        missing_archive_batches = any(
            bool(item["missing_batch_indexes"]) for item in archives)
        html_metadata = {
            "original_board_sha256": sha,
            "board_observed_at_utc": view["observed_at_utc"],
            "board_age_at_export": board_age,
            "archive_age_at_export": archive_age,
            "source_archive_manifest_sha256s": " | ".join(archive_manifests) or "not-provided",
            "saved_pr_archive_supplied": bool(archives),
            "saved_archive_batches_missing": missing_archive_batches,
        }
        admitted_rows = prepare_rows(view, sha, saved, exported_at=exported_at)
        rows = select_owner_rows(
            admitted_rows, repository=args.repository, priority=args.priority,
            record_type=args.record_type, pr_url=args.inspect_pr_url,
            issue_url=args.inspect_issue_url)
        selection = selection_metadata(
            len(admitted_rows), rows, repository=args.repository,
            priority=args.priority, record_type=args.record_type,
            pr_url=args.inspect_pr_url, issue_url=args.inspect_issue_url)
        # Keep complete-source coverage explicit, even in a single selected row.
        html_metadata.update(selection)
        for row in rows:
            row.update(selection)
        if args.format == "csv":
            if not rows:
                # A header-only file loses provenance and can be misread as a
                # clear backlog. Emit one explicitly non-actionable metadata row.
                rows = [{
                    "original_board_sha256": sha,
                    "board_observed_at_utc": view["observed_at_utc"],
                    "board_age_at_export": board_age,
                    "archive_age_at_export": archive_age,
                    "device_clock_authoritative": False,
                    "priority": "Unspecified",
                    "source_issue_urls": "",
                    "source_issue_titles": "Metadata only: no owner rows in bounded saved source",
                    "project_scope": "",
                    "pr_reference_state": "metadata_only_no_owner_rows",
                    "pr_url": "",
                    "linked_issue_count": 0,
                    "multiple_issues_share_pr": False,
                    "issue_has_alternative_prs": False,
                    "board_heads_observed": "UNKNOWN",
                    "archive_heads_observed": "UNKNOWN",
                    "head_variation_is_historical_not_merge_conflict": False,
                    "historical_gate_labels": "UNKNOWN",
                    "sampled_board_unknown_gates": "UNKNOWN",
                    "pr_missing_last_saved_snapshot": False,
                    "issue_link_missing_in_saved_source": False,
                    "historical_candidates_truncated": False,
                    "unlinked_in_saved_archive": False,
                    "source_archive_manifest_sha256s": " | ".join(archive_manifests) or "not-provided",
                    "saved_pr_archive_supplied": bool(archives),
                    "saved_archive_batches_missing": missing_archive_batches,
                    "human_next_step_only": (
                        "Inspect live issue/PR inventory; empty saved selection is not live backlog clearance"),
                    "claim_authorized": False,
                    "dispatch_authorized": False,
                    "required_ci_verified": False,
                    "independent_review_verified": False,
                    "main_merge_verified": False,
                    "deploy_authorized": False,
                    "live_state_verified": False,
                }]
            stream = StringIO()
            for row in rows:
                row.update(selection)
            writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\r\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: _cell(row.get(key)) for key in FIELDS})
            payload = stream.getvalue()
        elif args.format == "html":
            payload = render_private_html(rows, html_metadata)
        else:
            envelope = {
                "schema": "zsentinel-merge-first-owner-export-v1",
                "source": "validated_saved_board_and_optional_saved_pr_archives",
                "exported_at_utc": exported_at.isoformat(),
                "original_board_sha256": sha,
                "board_observed_at_utc": view["observed_at_utc"],
                "board_age_at_export": board_age,
                "archive_age_at_export": archive_age,
                "device_clock_authoritative": False,
                "source_archive_manifest_sha256s": archive_manifests,
                "saved_pr_archive_supplied": bool(archives),
                "saved_archive_batches_missing": missing_archive_batches,
                "record_count": len(rows),
                **selection,
                "records": rows,
                "claim_authorized": False,
                "dispatch_authorized": False,
                "required_ci_verified": False,
                "independent_review_verified": False,
                "main_merge_verified": False,
                "deploy_authorized": False,
                "live_state_verified": False,
            }
            payload = json.dumps(
                envelope, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if len(payload.encode("utf-8")) > MAX_CSV_BYTES:
            raise ValueError("private owner export exceeds one MiB size limit")
        write_create_only(args.output, payload)
        print(args.output)
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, BadZipFile) as exc:
        print("zSentinel merge-first export refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
