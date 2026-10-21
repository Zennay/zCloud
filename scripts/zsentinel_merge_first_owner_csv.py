#!/usr/bin/env python3
"""Offline, source-only merge-first owner CSV from admitted zSentinel board/ZIPs.

No network access, worker assignment, claim acquisition, CI retry, merge,
deployment, or modification of any original source is performed.
"""
from __future__ import annotations

import argparse
import csv
from html import escape
from io import StringIO
from pathlib import Path
import re
import sys

from zsentinel_operator_workbench import read_source_and_sha, validated_view
from zsentinel_product_delivery_board import write_create_only
from zsentinel_saved_dossier_timeline import build_timeline
from zsentinel_saved_issue_timeline import issue_worklist

RANK = {"P0": 0, "P1": 1, "P2": 2, "Unspecified": 3}
MAX_CSV_BYTES = 1024 * 1024
FIELDS = (
    "original_board_sha256", "board_observed_at_utc", "priority",
    "source_issue_urls", "source_issue_titles", "project_scope",
    "pr_reference_state", "pr_url", "linked_issue_count",
    "multiple_issues_share_pr", "issue_has_alternative_prs",
    "board_heads_observed", "archive_heads_observed",
    "head_variation_is_historical_not_merge_conflict",
    "historical_gate_labels", "sampled_board_unknown_gates",
    "pr_missing_last_saved_snapshot", "issue_link_missing_in_saved_source",
    "historical_candidates_truncated", "source_archive_manifest_sha256s",
    "saved_archive_batches_missing", "human_next_step_only",
    "claim_authorized", "dispatch_authorized", "required_ci_verified",
    "independent_review_verified", "main_merge_verified", "deploy_authorized",
    "live_state_verified",
)


def _cell(value):
    """Keep local CSV viewers from evaluating source strings as formulas."""
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    result = "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch
                     for ch in str(value)).strip()
    if result.startswith(("=", "+", "-", "@")):
        result = "'" + result
    return result


def prepare_rows(view, board_sha, saved):
    if saved is not None and (
        saved.get("owner_claim_verified") is not False
        or saved.get("main_merge_verified") is not False
    ):
        raise ValueError("historical PR source cannot authorize work")
    archives = saved["source_snapshots"] if saved is not None else []
    manifests = " | ".join(row["original_review_manifest_sha256"] for row in archives)
    missing_batches = any(bool(row["missing_batch_indexes"]) for row in archives)
    historical = {row["issue_url"]: row for row in (saved["issues"] if saved else [])}
    if len(historical) != len(saved["issues"] if saved else []):
        raise ValueError("duplicate archived issue")
    mapped = {}
    unknown = []
    for card in view["cards"]:
        current = historical.get(card["url"])
        archive_refs = {p["pr_url"]: p for p in
                        current["linked_existing_prs"]} if current else {}
        refs = set(card["open_pr_candidates"]) | set(archive_refs)
        if len(refs) > 15:
            raise ValueError("more than fifteen source references for one issue")
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
        "source_archive_manifest_sha256s": manifests,
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
            "human_next_step_only": "check_live_issue_pr_inventory_and_owner_before_new_work",
        })
    rows.sort(key=lambda row: (RANK[row["priority"]],
                               row["pr_reference_state"] != "saved_pr_candidate_not_live",
                               row["pr_url"] or row["source_issue_urls"]))
    if len(rows) > 900:
        raise ValueError("source-only PR worklist exceeds bounded inventory")
    return rows


def render_private_html(rows):
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
    for row in rows:
        priority = escape(str(row["priority"]))
        has_pr = bool(row["pr_url"])
        parts.append('<article><h2>' + priority + ' · ' +
                     ('Existing saved PR candidate' if has_pr else
                      'PR relationship UNKNOWN') + '</h2>')
        parts.append('<p><strong>' + escape(str(row["source_issue_titles"])) +
                     '</strong> · ' + escape(str(row["project_scope"])) + '</p>')
        for url in row["source_issue_urls"].split(" | "):
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
        parts.append('<details><summary>Original source custody</summary>'
                     '<small>Original board SHA256: ' +
                     escape(str(row["original_board_sha256"])) +
                     ' · UTC ' + escape(str(row["board_observed_at_utc"])) +
                     '</small><small>Saved archive manifest SHA256s: ' +
                     escape(str(row["source_archive_manifest_sha256s"])) +
                     '</small><small>Missing saved batches: ' +
                     escape(str(row["saved_archive_batches_missing"])) +
                     '; historic gate labels: ' +
                     escape(str(row["historical_gate_labels"])) +
                     '; PR absent final sample: ' +
                     escape(str(row["pr_missing_last_saved_snapshot"])) +
                     '</small></details>')
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
    parser.add_argument("--format", choices=("csv", "html"), default="csv",
                        help="P0-first owner CSV or standalone print-ready HTML")
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
        rows = prepare_rows(view, sha, saved)
        if args.format == "csv":
            stream = StringIO()
            writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\r\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: _cell(row.get(key)) for key in FIELDS})
            payload = stream.getvalue()
        else:
            payload = render_private_html(rows)
        if len(payload.encode("utf-8")) > MAX_CSV_BYTES:
            raise ValueError("private owner export exceeds one MiB size limit")
        write_create_only(args.output, payload)
        print(args.output)
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        print("zSentinel merge-first CSV refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
