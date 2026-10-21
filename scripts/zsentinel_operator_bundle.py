#!/usr/bin/env python3
"""Build a source-pinned private zSentinel board and interactive operator desk.

One-shot read-only collector over *saved* sources. This command never observes
the network, acquires a worker claim, dispatches CI, merges, or deploys.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

from zsentinel_product_delivery_board import (
    MAX_OUTPUT_BYTES, prepare, read, write_create_only,
)
from zsentinel_operator_workbench import html_document, validated_view

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,50}$")


def build_bundle(*, inbox: Path, output_dir: Path, name: str,
                 retry: Path | None = None, branches: Path | None = None,
                 claims: Path | None = None, prs: Path | None = None,
                 pr_dossiers: list[Path] | None = None, limit: int = 40,
                 max_age_hours: int = 24, owner_handoff: bool = False) -> dict:
    """Precompute both complete products before publishing either artifact."""
    if not NAME.fullmatch(name):
        raise ValueError("name must be a short lowercase safe filename stem")
    if not 1 <= limit <= 60 or not 1 <= max_age_hours <= 168:
        raise ValueError("limit must be 1–60 and max-age-hours 1–168")
    dossiers = pr_dossiers or []
    if len(dossiers) > 12:
        raise ValueError("at most twelve saved PR source archives are supported")

    board_path = output_dir / (name + ".board.json")
    desk_path = output_dir / (name + ".desk.html")
    handoff_path = output_dir / (name + ".owner.md")
    sources = [p for p in (inbox, retry, branches, claims, prs, *dossiers)
               if p is not None]
    if len({str(p.resolve()) for p in sources}) != len(sources):
        raise ValueError("duplicate source paths are not permitted")
    destinations = (board_path, desk_path, handoff_path) if owner_handoff else (board_path, desk_path)
    if any(p.resolve() in {s.resolve() for s in sources} for p in destinations):
        raise ValueError("source files cannot be output destinations")
    if any(p.exists() or p.is_symlink() for p in destinations):
        raise FileExistsError("create-only destination already exists")

    board = prepare(
        read(inbox, "inbox"),
        retry=read(retry, "retry") if retry else None,
        branches=read(branches, "branches") if branches else None,
        claims=read(claims, "claims") if claims else None,
        prs=read(prs, "prs") if prs else None,
        hours=max_age_hours, limit=limit,
    )
    serialized = json.dumps(board, ensure_ascii=False, indent=2) + "\n"
    board_sha = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    admitted = validated_view(board)
    archived = None
    if dossiers:
        from zsentinel_saved_dossier_timeline import build_timeline
        from zsentinel_saved_issue_timeline import issue_worklist
        archived = issue_worklist(build_timeline(dossiers, minimum_sources=1))
    desk = html_document(admitted, board_sha, saved_pr_context=archived)
    owner_markdown = None
    owner_count = None
    if owner_handoff:
        from zsentinel_merge_first_owner_csv import (
            prepare_rows, render_private_markdown, selection_metadata,
        )
        from zsentinel_saved_dossier_timeline import source_freshness
        exported_at = datetime.now(timezone.utc)
        snapshots = archived["source_snapshots"] if archived else []
        rows = prepare_rows(admitted, board_sha, archived, exported_at=exported_at)
        source_meta = {
            "original_board_sha256": board_sha,
            "board_observed_at_utc": admitted["observed_at_utc"],
            "board_age_at_export": source_freshness(
                [{"latest_observed_utc": admitted["observed_at_utc"]}],
                evaluated_at=exported_at)["status"],
            "archive_age_at_export": (
                source_freshness(snapshots, evaluated_at=exported_at)["status"]
                if snapshots else "not-provided"),
            "source_archive_manifest_sha256s": " | ".join(
                row["original_review_manifest_sha256"] for row in snapshots
            ) or "not-provided",
            "saved_pr_archive_supplied": bool(snapshots),
            "saved_archive_batches_missing": any(
                bool(row["missing_batch_indexes"]) for row in snapshots),
            **selection_metadata(len(rows), rows),
        }
        owner_markdown = render_private_markdown(rows, source_meta)
        owner_count = len(rows)
        if not 1 <= len(owner_markdown.encode("utf-8")) <= 1024 * 1024:
            raise ValueError("private owner handoff exceeds one MiB")

    if any(not 1 <= len(payload.encode("utf-8")) <= MAX_OUTPUT_BYTES
           for payload in (serialized, desk)):
        raise ValueError("one of the generated products exceeds the private report limit")
    # Both payloads are admitted first, and the shared writer publishes every
    # file through directory-bound no-follow, create-only 0600 operations.
    # On an unexpected second publish failure, preserve the first immutable
    # artifact rather than risk deleting somebody else's newly replaced path.
    write_create_only(board_path, serialized)
    write_create_only(desk_path, desk)
    if owner_markdown is not None:
        write_create_only(handoff_path, owner_markdown)
    return {
        "board": str(board_path),
        "desk": str(desk_path),
        "owner_handoff": str(handoff_path) if owner_markdown is not None else None,
        "owner_records_in_bounded_sample": owner_count,
        "board_sha256": board_sha,
        "cards_in_bounded_sample": len(admitted["cards"]),
        "source_is_live": False,
        "claim_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox", required=True, type=Path,
                        help="Previously saved bounded GitHub issue inbox JSON")
    parser.add_argument("--retry", type=Path, help="Optional read-only queue retry snapshot")
    parser.add_argument("--branches", type=Path, help="Optional PR-less branch snapshot")
    parser.add_argument("--claims", type=Path, help="Optional observed-claims snapshot")
    parser.add_argument("--prs", type=Path, help="Optional exact-head PR gate snapshot")
    parser.add_argument("--pr-dossier", action="append", type=Path,
                        help="Optional previously saved PR archive ZIP, repeat up to 12")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--max-age-hours", type=int, default=24)
    parser.add_argument("--owner-handoff", action="store_true",
                        help="Also create a P0-first saved existing-PR owner worklist Markdown")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Existing private (0700) operator-owned directory")
    parser.add_argument("--name", required=True,
                        help="New create-only lowercase artifact name")
    args = parser.parse_args(argv)
    try:
        result = build_bundle(
            inbox=args.inbox, output_dir=args.output_dir, name=args.name,
            retry=args.retry, branches=args.branches, claims=args.claims,
            prs=args.prs, pr_dossiers=args.pr_dossier,
            limit=args.limit, max_age_hours=args.max_age_hours,
            owner_handoff=args.owner_handoff)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        print("zSentinel operator bundle refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
