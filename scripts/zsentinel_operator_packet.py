#!/usr/bin/env python3
"""One-file, private, source-bound zSentinel operator packet.

Bundles the existing read-only owner workbench into a single create-only ZIP.
No provider reads, claim/retry, CI dispatch, merge, or deploy capabilities.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
from html import escape
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from zsentinel_runtime_handoff import (
    SCHEMA, _source_report_digest, csv_export, handoff_html, markdown,
    serialize,
)

MAX_PACKET_BYTES = 3 * 1024 * 1024
MEMBERS = (
    "owner-handoff.json",
    "owner-actions.csv",
    "owner-handoff.md",
    "owner-handoff.html",
    "manifest.json",
)


def _standalone_html(report, digest):
    # Reuse the existing safe, source-validated action cards verbatim.
    panel = handoff_html(report)
    return (
        '<!doctype html><html lang="nl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        """<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">"""
        '<meta name="referrer" content="no-referrer">'
        '<title>zSentinel · private eigenaarsoverdracht</title>'
        '<style>body{font:16px/1.55 system-ui,sans-serif;max-width:64rem;'
        'margin:auto;padding:1.5rem;color:#18202b}a{color:#07549e}'
        'section{border:1px solid #cbd3dd;padding:1rem;margin:1rem 0}'
        'h1,h2{line-height:1.2}code{overflow-wrap:anywhere}</style>'
        '</head><body><h1>zSentinel · private owner-handoff</h1>'
        '<p>Alleen historische/partieel waargenomen bronmomenten. '
        'Geen live vrijgave, claim, CI retry, merge of deploy.</p>'
        '<p>Canonieke handoff-bron: <code>' + escape(digest) + '</code></p>'
        + panel + '</body></html>\n'
    )


def build_packet(report):
    """Derive every artifact and checksum from one already assembled object."""
    if (type(report) is not dict or report.get("schema") != SCHEMA
            or report.get("actions_performed") is not False
            or report.get("owner_and_current_claim_verified") is not False
            or report.get("merge_authorized") is not False
            or report.get("deploy_authorized") is not False
            or report.get("all_sources_complete") is not False):
        raise ValueError("untrusted or authorizing owner handoff")
    digest = _source_report_digest(report)
    rendered = {
        MEMBERS[0]: serialize(report, ".json").encode("utf-8"),
        MEMBERS[1]: csv_export(report).encode("utf-8"),
        MEMBERS[2]: markdown(report).encode("utf-8"),
        MEMBERS[3]: _standalone_html(report, digest).encode("utf-8"),
    }
    manifest = {
        "schema": "zsentinel-private-operator-packet-v1",
        "source_report_sha256": digest,
        "source_created_at_utc": report.get("created_at_utc"),
        "source_coverage": "partial_not_release_authority",
        "owner_card_count": len(report.get("priority_items", [])),
        "historical_recheck_count": len(report.get("historical_owner_rechecks", [])),
        "items_truncated": report.get("items_truncated"),
        "all_sources_complete": False,
        "claim_authorized": False,
        "retry_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "live_recovered_verified": False,
        "files": {
            name: {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            } for name, payload in rendered.items()
        },
    }
    # Tight source contract: never let a forged report hide unresolved actions.
    if (type(manifest["owner_card_count"]) is not int
            or not 0 <= manifest["owner_card_count"] <= 24
            or type(manifest["historical_recheck_count"]) is not int
            or not 0 <= manifest["historical_recheck_count"] <= 5
            or type(manifest["items_truncated"]) is not bool):
        raise ValueError("invalid source packet counts")
    rendered[MEMBERS[4]] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) +
        "\n").encode("utf-8")
    target = io.BytesIO()
    with ZipFile(target, "w", compression=ZIP_STORED, allowZip64=False) as archive:
        for name in MEMBERS:
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_STORED
            entry.external_attr = 0o600 << 16
            archive.writestr(entry, rendered[name])
    payload = target.getvalue()
    if not 1 <= len(payload) <= MAX_PACKET_BYTES:
        raise ValueError("private operator packet exceeds 3 MiB")
    return payload


def write_packet(destination: Path, report):
    """Create exactly one private file, pinning the destination directory inode."""
    if (not isinstance(destination, Path) or destination.suffix != ".zip"
            or not destination.is_absolute() or destination.name in ("", ".", "..")):
        raise ValueError("operator packet needs a new absolute .zip path")
    payload = build_packet(report)
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("private no-follow writes unsupported")
    parent = destination.parent
    original = parent.lstat()
    if (not stat.S_ISDIR(original.st_mode) or original.st_uid != os.geteuid()
            or stat.S_IMODE(original.st_mode) & 0o022):
        raise ValueError("private packet directory must be owner-controlled")
    directory_fd = os.open(
        parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
        getattr(os, "O_CLOEXEC", 0))
    try:
        actual = os.fstat(directory_fd)
        if (not stat.S_ISDIR(actual.st_mode)
                or (actual.st_dev, actual.st_ino) != (original.st_dev, original.st_ino)
                or actual.st_uid != os.geteuid()
                or stat.S_IMODE(actual.st_mode) & 0o022):
            raise ValueError("private packet directory changed during export")
        fd = None
        created = None
        try:
            fd = os.open(destination.name, os.O_CREAT | os.O_EXCL |
                         os.O_WRONLY | os.O_NOFOLLOW |
                         getattr(os, "O_CLOEXEC", 0),
                         0o600, dir_fd=directory_fd)
            info = os.fstat(fd)
            created = (info.st_dev, info.st_ino)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("unsafe private packet output inode")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as output:
                fd = None
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.fsync(directory_fd)
        except BaseException:
            if fd is not None:
                os.close(fd)
            if created is not None:
                try:
                    current = os.stat(destination.name, dir_fd=directory_fd,
                                      follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == created:
                        os.unlink(destination.name, dir_fd=directory_fd)
                except OSError:
                    pass
            raise
    finally:
        os.close(directory_fd)
    return len(payload)
