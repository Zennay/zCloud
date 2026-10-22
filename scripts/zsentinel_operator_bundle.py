#!/usr/bin/env python3
"""Build a source-pinned private zSentinel board and interactive operator desk.

One-shot read-only collector over *saved* sources. This command never observes
the network, acquires a worker claim, dispatches CI, merges, or deploys.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import stat
import sys

from zsentinel_product_delivery_board import (
    MAX_OUTPUT_BYTES, prepare, read, write_create_only,
)
from zsentinel_operator_workbench import html_document, validated_view

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,50}$")
CHECKPOINT_SCHEMA = "zsentinel-operator-bundle-checkpoint-v1"
MAX_CHECKPOINT_BYTES = 64 * 1024
MAX_SOURCE_FINGERPRINT_BYTES = 64 * 1024 * 1024


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key in private bundle checkpoint")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError("non-finite value in private bundle checkpoint")


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _assert_private_directory(path: Path) -> None:
    before = path.lstat()
    if (not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o077):
        raise ValueError("private output directory must be owned by current user and inaccessible to group/others")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("private directory-bound no-follow reads unsupported")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                 getattr(os, "O_CLOEXEC", 0))
    try:
        actual = os.fstat(fd)
        if (not stat.S_ISDIR(actual.st_mode)
                or (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino)
                or actual.st_uid != os.geteuid()
                or stat.S_IMODE(actual.st_mode) & 0o077):
            raise ValueError("private output directory was replaced or became accessible to group/others")
    finally:
        os.close(fd)


def _read_private_file(path: Path, max_bytes: int) -> bytes:
    """Read an owner-only file through a no-follow descriptor and reject drift."""
    _assert_private_directory(path.parent)
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size > max_bytes):
        raise ValueError("private bundle file is not an owner-only bounded regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW |
                 getattr(os, "O_CLOEXEC", 0))
    try:
        opened = os.fstat(fd)
        identity = (before.st_dev, before.st_ino, before.st_size,
                    before.st_mtime_ns, before.st_ctime_ns)
        if (not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino, opened.st_size,
                    opened.st_mtime_ns, opened.st_ctime_ns) != identity
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600):
            raise ValueError("private bundle file identity changed before read")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(max_bytes + 1)
        after = os.fstat(fd)
        current = path.lstat()
        after_identity = (after.st_dev, after.st_ino, after.st_size,
                          after.st_mtime_ns, after.st_ctime_ns)
        current_identity = (current.st_dev, current.st_ino, current.st_size,
                            current.st_mtime_ns, current.st_ctime_ns)
        if (len(raw) > max_bytes or identity != after_identity
                or identity != current_identity
                or current.st_uid != os.geteuid()
                or stat.S_IMODE(current.st_mode) != 0o600):
            raise ValueError("private bundle file changed during read")
        return raw
    finally:
        os.close(fd)


def _fingerprint_source(role: str, path: Path) -> dict:
    """Hash exact bounded source bytes without following symlinks."""
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode)
            or before.st_size > MAX_SOURCE_FINGERPRINT_BYTES):
        raise ValueError("bundle source is not a bounded regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW |
                 getattr(os, "O_CLOEXEC", 0))
    digest = hashlib.sha256()
    total = 0
    try:
        opened = os.fstat(fd)
        identity = (before.st_dev, before.st_ino, before.st_size,
                    before.st_mtime_ns, before.st_ctime_ns)
        if (not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino, opened.st_size,
                    opened.st_mtime_ns, opened.st_ctime_ns) != identity):
            raise ValueError("bundle source identity changed before fingerprint")
        while True:
            block = os.read(fd, min(65536, MAX_SOURCE_FINGERPRINT_BYTES + 1 - total))
            if not block:
                break
            total += len(block)
            if total > MAX_SOURCE_FINGERPRINT_BYTES:
                raise ValueError("bundle source exceeds fingerprint byte limit")
            digest.update(block)
        after = os.fstat(fd)
        current = path.lstat()
        after_identity = (after.st_dev, after.st_ino, after.st_size,
                          after.st_mtime_ns, after.st_ctime_ns)
        current_identity = (current.st_dev, current.st_ino, current.st_size,
                            current.st_mtime_ns, current.st_ctime_ns)
        if (identity != after_identity or identity != current_identity
                or total != after.st_size):
            raise ValueError("bundle source changed during fingerprint")
    finally:
        os.close(fd)
    return {"role": role, "sha256": digest.hexdigest(), "bytes": total}


def _read_checkpoint(path: Path) -> dict:
    raw = _read_private_file(path, MAX_CHECKPOINT_BYTES)
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("private bundle recovery checkpoint is malformed") from exc
    if type(value) is not dict or value.get("schema") != CHECKPOINT_SCHEMA:
        raise ValueError("unrecognized private bundle recovery checkpoint")
    return value


def _checkpoint_datetime(value: str) -> datetime:
    if type(value) is not str or len(value) > 45:
        raise ValueError("invalid checkpoint clock")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid checkpoint clock") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("checkpoint clock lacks UTC offset")
    stamp = stamp.astimezone(timezone.utc)
    if stamp.isoformat() != value:
        raise ValueError("checkpoint clock is not canonical UTC")
    return stamp


def _existing_payload_matches(path: Path, payload: str) -> bool:
    return _read_private_file(path, MAX_OUTPUT_BYTES) == payload.encode("utf-8")

def link_bundle_companions(desk: str, name: str, board_sha: str,
                           owner_handoff: bool) -> str:
    """Add local-only navigation to the finished desk, before receipt hashing.

    No external request, code execution or source authorization is created.
    The links deliberately use only the caller's restricted filename stem,
    not absolute operator paths or raw issue/worker metadata.
    """
    if (type(desk) is not str or type(name) is not str
            or NAME.fullmatch(name) is None or
            type(board_sha) is not str or
            re.fullmatch(r"[0-9a-f]{64}", board_sha) is None or
            type(owner_handoff) is not bool):
        raise ValueError("invalid private bundle navigation source")
    closing = "</script></html>"
    if desk.count(closing) != 1:
        raise ValueError("operator desk HTML has no unique safe append boundary")
    def local_link(ext: str, label: str) -> str:
        filename = name + ext
        return ("<li><a rel='noopener' href='" + escape(filename, quote=True) +
                "'>" + escape(label) + "</a></li>")
    links = (
        local_link(".board.json", "Open oorspronkelijke bronkaart (JSON)") +
        (local_link(".owner.md", "Open privé merge-first eigenaarsoverdracht")
         if owner_handoff else "") +
        local_link(".receipt.json", "Open brongebonden bundle receipt (JSON)")
    )
    nav = (
        "</script><footer class='bundle-companions' role='contentinfo'>"
        "<h2>Lokale bronbestanden van deze werkbak</h2>"
        "<p>Alleen bestanden in dezelfde bestaande privémap; geen netwerk,"
        " claim, review, CI, merge of deployment.</p>"
        "<nav aria-label='Bijbehorende privé bronbestanden'><ul>" +
        links + "</ul></nav><small>Oorspronkelijke board SHA-256: <code>" +
        board_sha + "</code> · huidige GitHub-/Notion-staat niet bevestigd."
        "</small></footer></html>"
    )
    return desk.replace(closing, nav)



def build_bundle(*, inbox: Path, output_dir: Path, name: str,
                 retry: Path | None = None, branches: Path | None = None,
                 claims: Path | None = None, prs: Path | None = None,
                 pr_dossiers: list[Path] | None = None, limit: int = 40,
                 max_age_hours: int = 24, owner_handoff: bool = False,
                 verify_after_write: bool = False,
                 resume_partial: bool = False) -> dict:
    """Publish a private bundle and resume only from its exact saved checkpoint."""
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
    receipt_path = output_dir / (name + ".receipt.json")
    checkpoint_path = output_dir / ("." + name + ".checkpoint.json")
    source_roles = [
        ("inbox", inbox), ("retry", retry), ("branches", branches),
        ("claims", claims), ("prs", prs),
    ]
    source_roles.extend(
        ("pr_dossier_" + str(index + 1), path)
        for index, path in enumerate(dossiers)
    )
    source_roles = [(role, path) for role, path in source_roles if path is not None]
    sources = [path for _, path in source_roles]
    if len({str(path.resolve()) for path in sources}) != len(sources):
        raise ValueError("duplicate source paths are not permitted")
    destinations = ((board_path, desk_path, handoff_path, receipt_path)
                    if owner_handoff else (board_path, desk_path, receipt_path))
    reserved_destinations = (*destinations, checkpoint_path)
    resolved_sources = {path.resolve() for path in sources}
    if any(path.resolve() in resolved_sources for path in reserved_destinations):
        raise ValueError("source files cannot be output destinations")

    _assert_private_directory(output_dir)
    source_fingerprints = [
        _fingerprint_source(role, path) for role, path in source_roles
    ]
    checkpoint_exists = _exists(checkpoint_path)
    existing_destinations = [path for path in destinations if _exists(path)]
    checkpoint_data = None
    options = {
        "limit": limit,
        "max_age_hours": max_age_hours,
        "owner_handoff": owner_handoff,
    }
    if not resume_partial:
        if checkpoint_exists or existing_destinations:
            raise FileExistsError("create-only destination or recovery checkpoint already exists")
        generated_at = datetime.now(timezone.utc)
    elif checkpoint_exists:
        checkpoint_data = _read_checkpoint(checkpoint_path)
        if (checkpoint_data.get("bundle_name") != name
                or checkpoint_data.get("options") != options
                or checkpoint_data.get("source_fingerprints") != source_fingerprints):
            raise ValueError("bundle checkpoint does not match exact source bytes or options; existing files preserved")
        generated_at = _checkpoint_datetime(checkpoint_data.get("generated_at_utc"))
    elif existing_destinations:
        raise ValueError("partial bundle has no matching recovery checkpoint; choose a new name; existing files preserved")
    else:
        generated_at = datetime.now(timezone.utc)

    board = prepare(
        read(inbox, "inbox"),
        retry=read(retry, "retry") if retry else None,
        branches=read(branches, "branches") if branches else None,
        claims=read(claims, "claims") if claims else None,
        prs=read(prs, "prs") if prs else None,
        hours=max_age_hours, limit=limit, evaluated_at=generated_at,
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
    # Companion files are authored by this same create-only bundle and their
    # relative names are source-independent. Include links before hashing the
    # HTML into the receipt, so verification covers the visible navigation.
    desk = link_bundle_companions(desk, name, board_sha, owner_handoff)
    owner_markdown = None
    owner_count = None
    if owner_handoff:
        from zsentinel_merge_first_owner_csv import (
            prepare_rows, render_private_markdown, selection_metadata,
        )
        from zsentinel_saved_dossier_timeline import source_freshness
        exported_at = generated_at
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
    # The final create-only receipt is a transfer manifest, not current proof.
    # Its presence tells an operator that all preceding artifacts were created;
    # an unexpected partial write leaves no misleading "complete" receipt.
    artifacts = [
        (board_path, serialized, "product_delivery_board"),
        (desk_path, desk, "interactive_private_operator_desk"),
    ]
    if owner_markdown is not None:
        artifacts.append((handoff_path, owner_markdown, "merge_first_owner_handoff"))
    receipt = {
        "schema": "zsentinel-operator-bundle-receipt-v1",
        "bundle_complete": True,
        "source_is_live": False,
        "source_board_observed_at_utc": admitted["observed_at_utc"],
        "original_source_clocks_utc": admitted["project_source_observed_at_utc"],
        "original_source_freshness": board["fresh"],
        "cards_in_bounded_sample": len(admitted["cards"]),
        "owner_records_in_bounded_sample": owner_count,
        "artifacts": [
            {
                "file": artifact_path.name,
                "role": role,
                "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                "bytes": len(payload.encode("utf-8")),
            }
            for artifact_path, payload, role in artifacts
        ],
        "claims_authorized": False,
        "ci_verified": False,
        "review_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
    }
    receipt_data = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if len(receipt_data.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("bundle receipt exceeds private report limit")
    # A private, create-only checkpoint pins the exact source bytes, options,
    # generation clock and every expected output digest before publication.
    # If publication is interrupted, --resume-partial regenerates the same
    # bytes and fills only missing files. Existing user files are never deleted
    # or overwritten; partial bundles without this checkpoint are not guessed.
    artifact_manifest = [
        {
            "file": artifact_path.name,
            "role": role,
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "bytes": len(payload.encode("utf-8")),
        }
        for artifact_path, payload, role in artifacts
    ]
    receipt_bytes = receipt_data.encode("utf-8")
    plan = {
        "schema": CHECKPOINT_SCHEMA,
        "bundle_name": name,
        "generated_at_utc": generated_at.isoformat(),
        "options": options,
        "source_fingerprints": source_fingerprints,
        "artifacts": artifact_manifest,
        "receipt": {
            "file": receipt_path.name,
            "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
            "bytes": len(receipt_bytes),
        },
    }
    if checkpoint_data is not None and checkpoint_data != plan:
        raise ValueError("generated bundle differs from its saved recovery checkpoint; existing files preserved")
    current_fingerprints = [
        _fingerprint_source(role, path) for role, path in source_roles
    ]
    if current_fingerprints != source_fingerprints:
        raise ValueError("bundle sources changed during generation; no outputs published")

    all_payloads = [*artifacts, (receipt_path, receipt_data, "bundle_receipt")]
    for artifact_path, payload, _ in all_payloads:
        if _exists(artifact_path):
            if not resume_partial:
                raise FileExistsError("create-only destination appeared during generation")
            if not _existing_payload_matches(artifact_path, payload):
                raise ValueError("existing private artifact differs from the saved recovery plan; existing files preserved")

    if checkpoint_data is None:
        checkpoint_payload = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
        if len(checkpoint_payload.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
            raise ValueError("private bundle checkpoint exceeds its byte limit")
        write_create_only(checkpoint_path, checkpoint_payload)
        checkpoint_data = plan

    for artifact_path, payload, _ in artifacts:
        if _exists(artifact_path):
            if not resume_partial or not _existing_payload_matches(artifact_path, payload):
                raise ValueError("existing private artifact changed before publication; existing files preserved")
        else:
            write_create_only(artifact_path, payload)
    if _exists(receipt_path):
        if not resume_partial or not _existing_payload_matches(receipt_path, receipt_data):
            raise ValueError("existing private receipt changed before publication; existing files preserved")
    else:
        write_create_only(receipt_path, receipt_data)
    verified = None
    if verify_after_write:
        # Optional end-to-end local delivery proof uses the exact verifier
        # exposed as an independent read-only operator command. It never
        # upgrades the unsigned saved receipt into current release authority.
        from zsentinel_verify_operator_bundle import verify_bundle
        verified = verify_bundle(output_dir, name)
    # Do not print private filesystem paths or worker/project source details
    # into shell, runner or service logs. Names are safe caller-chosen stems.
    return {
        "board_file": board_path.name,
        "desk_file": desk_path.name,
        "owner_handoff_file": handoff_path.name if owner_markdown is not None else None,
        "receipt_file": receipt_path.name,
        "board_sha256": board_sha,
        "bundle_complete": True,
        "delivery_integrity_verified": (
            verified["integrity_verified"] if verified is not None else None
        ),
        "delivery_integrity_check_performed": verify_after_write,
        "source_is_live": False,
        "claim_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "resume_partial_mode": resume_partial,
        "resume_checkpoint_retained": True,
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
    parser.add_argument("--verify-after-write", action="store_true",
                        help="After publishing the receipt, read it back and verify exact private artifact digests")
    parser.add_argument("--resume-partial", action="store_true",
                        help="Resume only from the exact private checkpoint using identical source bytes and options")
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
            owner_handoff=args.owner_handoff,
            verify_after_write=args.verify_after_write,
            resume_partial=args.resume_partial)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        print("zSentinel operator bundle refused: " + str(exc), file=sys.stderr)
        if isinstance(exc, OSError) and NAME.fullmatch(args.name):
            try:
                checkpoint = args.output_dir / ("." + args.name + ".checkpoint.json")
                if _exists(checkpoint):
                    print(
                        "A private recovery checkpoint may remain. If publication was "
                        "interrupted, rerun the identical command with --resume-partial; "
                        "it will verify exact source bytes and refuse mismatched existing "
                        "files. Existing files are never deleted or overwritten.",
                        file=sys.stderr,
                    )
            except OSError:
                pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
