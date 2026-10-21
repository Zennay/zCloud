#!/usr/bin/env python3
"""One private self-contained zSentinel project-dossier ZIP from saved sources.

This is an offline source-bound human handoff, NOT an executable work order,
release approval, claim, workflow dispatch, merge, or proof of live recovery.
"""
from __future__ import annotations

from io import BytesIO
import hashlib
import json
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from zsentinel_saved_gate_portfolio import (
    portfolio_page_html, portfolio_markdown, portfolio_pr_csv, portfolio_json,
    portfolio_single_pr_html, portfolio_single_pr_markdown, project_worklist,
    single_pr_related_peers,
)

MAX_ENTRY_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * MAX_ENTRY_BYTES
FILENAMES = ("project-dossier.html", "project-actions.md",
             "project-prs.csv", "source-receipt.json")


def private_project_package(selected, integrated, *, project=None):
    """Bundle four independent views of the *same* validated saved receipts.

    All outputs render before ZIP creation. The caller writes exactly one
    create-only mode-0600 file, or refuses without leaving a partial artifact.
    """
    rendered = (
        portfolio_page_html(selected, integrated, project=project).encode("utf-8"),
        portfolio_markdown(selected, integrated, project=project).encode("utf-8"),
        portfolio_pr_csv(selected, integrated, project=project).encode("utf-8"),
        portfolio_json(selected, integrated, project=project).encode("utf-8"),
    )
    if (any(not data or len(data) > MAX_ENTRY_BYTES for data in rendered)
            or sum(map(len, rendered)) > MAX_TOTAL_BYTES):
        raise ValueError("Source-bound project handoff exceeds private size budget")
    filenames = dict(zip(FILENAMES, rendered))
    receipt = {
        "schema": "zsentinel-private-project-dossier-zip-v1",
        "source": "offline_saved_manifest_and_gate_batches_not_live",
        "selected_project_filter": project,
        "original_review_manifest_sha256":
            integrated["original_review_manifest_sha256"],
        "original_board_sha256": integrated["original_board_sha256"],
        "earliest_observed_at_utc": integrated["earliest_observed_at_utc"],
        "latest_observed_at_utc": integrated["latest_observed_at_utc"],
        "missing_batch_indexes": sorted(integrated["missing_batch_indexes"]),
        "files_sha256": {
            name: hashlib.sha256(data).hexdigest()
            for name, data in filenames.items()
        },
        "read_only": True,
        "claim_authorized": False,
        "worker_dispatch_authorized": False,
        "required_ci_review_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "live_verified": False,
    }
    manifest = (json.dumps(receipt, ensure_ascii=False, sort_keys=True,
                           indent=2) + "\n").encode("utf-8")
    filenames["source-checksums.json"] = manifest
    out = BytesIO()
    with ZipFile(out, mode="w", compression=ZIP_DEFLATED,
                 compresslevel=6, allowZip64=False) as archive:
        for filename, data in filenames.items():
            # A private archive has fixed entry names, fixed metadata and no
            # traversals, symlinks, worker credentials or executable scripts.
            entry = ZipInfo(filename=filename, date_time=(2020, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100600 << 16
            archive.writestr(entry, data, compress_type=ZIP_DEFLATED)
    content = out.getvalue()
    if not content or len(content) > MAX_ARCHIVE_BYTES:
        raise ValueError("Private project ZIP exceeds one-MiB output budget")
    return content


def private_single_pr_package(selected, integrated, *, pr_url, project=None):
    """Portable two-format dossier and matching hash inventory for ONE source PR.

    Validate the entire original source before choosing the exact canonical
    candidate. No API calls, ownership decisions or release authorization.
    """
    # The renderers individually enforce original full-manifest admission.
    html = portfolio_single_pr_html(
        selected, integrated, pr_url=pr_url, project=project).encode("utf-8")
    markdown = portfolio_single_pr_markdown(
        selected, integrated, pr_url=pr_url, project=project).encode("utf-8")
    groups = project_worklist(selected, integrated)
    matches = [(group, item) for group in groups for item in group["candidates"]
               if item["url"] == pr_url]
    if len(matches) != 1:
        raise ValueError("Single PR package source selection is not unique")
    group, item = matches[0]
    peer_issues = single_pr_related_peers(
        selected, integrated, pr_url=pr_url)
    peer_count = len({peer["url"] for peers in peer_issues.values()
                      for peer in peers})
    peer_receipt = {
        "schema": "zsentinel-private-single-pr-related-source-prs-v1",
        "source": "saved_bounded_manifest_and_gate_batches_not_live",
        "selected_pr_url": pr_url,
        "original_review_manifest_sha256":
            integrated["original_review_manifest_sha256"],
        "linked_issue_peer_prs": peer_issues,
        "total_distinct_other_prs_observed": peer_count,
        "complete_current_pr_inventory_verified": False,
        "shared_file_scope_verified": False,
        "issue_fixed_verified": False,
        "claim_authorized": False,
        "merge_authorized": False,
    }
    receipt = {
        "schema": "zsentinel-private-single-pr-dossier-zip-v1",
        "source": "saved_manifest_and_exact_head_gate_batch_evidence_not_live",
        "selected_pr_url": pr_url,
        "selected_project": group["repository"],
        "source_head_observed": item["head_observed"],
        "source_gate": item["gate_source"],
        "source_batch_index": item["batch_index"],
        "observed_linked_issue_count": len(item["linked_issue_urls"]),
        "observed_other_prs_sharing_source_issues": peer_count,
        "original_review_manifest_sha256":
            integrated["original_review_manifest_sha256"],
        "original_board_sha256": integrated["original_board_sha256"],
        "earliest_observed_at_utc": integrated["earliest_observed_at_utc"],
        "latest_observed_at_utc": integrated["latest_observed_at_utc"],
        "missing_batch_indexes": sorted(integrated["missing_batch_indexes"]),
        "original_source_pr_candidates": len(selected),
        "complete_live_pr_inventory_verified": False,
        "current_head_verified_at_export": False,
        "claim_authorized": False,
        "required_ci_review_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "live_verified": False,
    }
    data = {
        "single-pr-dossier.html": html,
        "single-pr-actions.md": markdown,
        "linked-peer-prs.json": (
            json.dumps(peer_receipt, ensure_ascii=False, sort_keys=True, indent=2) +
            "\n").encode("utf-8"),
        "source-receipt.json": (
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) +
            "\n").encode("utf-8"),
    }
    if (any(not payload or len(payload) > MAX_ENTRY_BYTES for payload in data.values())
            or sum(map(len, data.values())) > MAX_TOTAL_BYTES):
        raise ValueError("One-PR package exceeds private source bounds")
    checksum = {
        "schema": "zsentinel-private-single-pr-checksums-v1",
        "selected_pr_url": pr_url,
        "original_review_manifest_sha256":
            integrated["original_review_manifest_sha256"],
        "files_sha256": {
            name: hashlib.sha256(payload).hexdigest()
            for name, payload in data.items()
        },
    }
    data["source-checksums.json"] = (
        json.dumps(checksum, ensure_ascii=False, sort_keys=True, indent=2) +
        "\n").encode("utf-8")
    out = BytesIO()
    with ZipFile(out, mode="w", compression=ZIP_DEFLATED,
                 compresslevel=6, allowZip64=False) as archive:
        for name, payload in data.items():
            entry = ZipInfo(filename=name, date_time=(2020, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100600 << 16
            archive.writestr(entry, payload, compress_type=ZIP_DEFLATED)
    content = out.getvalue()
    if not content or len(content) > MAX_ARCHIVE_BYTES:
        raise ValueError("Private single-PR dossier ZIP exceeds one MiB")
    return content

