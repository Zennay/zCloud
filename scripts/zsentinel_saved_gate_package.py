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
