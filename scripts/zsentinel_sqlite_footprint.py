#!/usr/bin/env python3
"""Private bounded SQLite metadata desk; no SQL, checkpoint, writer or cleanup.

Header freelist counts are checkpoint metadata and NEVER proof of reclaimable
capacity, especially while WAL/active writers may exist.
"""
from __future__ import annotations
import argparse
import json
import os
import stat
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from zsentinel_disk_hotspots import _root, _sensitive, _open_directory_beneath
from zsentinel_private_output import write_private_output

SCHEMA = "zsentinel-sqlite-footprint-v1"
MAX_TARGETS = 8
EXT = (".db", ".sqlite", ".sqlite3")

def _parts(value):
    if type(value) is not str or not 1 <= len(value) <= 300 or value.startswith("/") or "\x00" in value:
        raise ValueError("relative SQLite path required")
    parts = Path(value).parts
    if (not parts or len(parts) > 8 or any(x in ("", ".", "..") or len(x) > 100
            or _sensitive(x) for x in parts) or not parts[-1].lower().endswith(EXT)):
        raise ValueError("unsafe or non-SQLite file")
    return parts

def _sidecar(parent_fd, name, dev):
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return {"observed": False, "coverage": "absent_at_sample", "allocated_bytes": 0, "size_bytes": 0}
    if not stat.S_ISREG(info.st_mode) or info.st_dev != dev:
        return {"observed": True, "coverage": "unsafe_nonregular_or_other_mount",
                "allocated_bytes": None, "size_bytes": None}
    return {"observed": True, "coverage": "metadata_only_racy",
            "allocated_bytes": max(0, info.st_blocks) * 512, "size_bytes": info.st_size}

def observe(root: Path, filename: str):
    workspace = _root(root)
    parts = _parts(filename)
    mount = workspace.stat().st_dev
    parent_fd = _open_directory_beneath(
        workspace, workspace.joinpath(*parts[:-1]), mount)
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(parts[-1], flags, dir_fd=parent_fd)
        try:
            first = os.fstat(fd)
            if (not stat.S_ISREG(first.st_mode) or first.st_dev != mount
                    or not 100 <= first.st_size <= 20 * 1024**4):
                raise ValueError("unsupported SQLite file identity or size")
            header = os.pread(fd, 100, 0)
            last = os.fstat(fd)
            path_stat = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            def identity(s):
                return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            stable = identity(first) == identity(last) == identity(path_stat)
            if len(header) != 100 or header[:16] != b"SQLite format 3\x00":
                raise ValueError("not an SQLite 3 database header")
            page_encoding = int.from_bytes(header[16:18], "big")
            page_size = 65536 if page_encoding == 1 else page_encoding
            pages = int.from_bytes(header[28:32], "big")
            freelist = int.from_bytes(header[36:40], "big")
            change_counter = int.from_bytes(header[24:28], "big")
            version_valid = int.from_bytes(header[92:96], "big")
            is_wal = header[18] == 2 or header[19] == 2
            valid = (stable and page_size in (512, 1024, 2048, 4096, 8192,
                      16384, 32768, 65536) and pages > 0
                     and pages * page_size == last.st_size
                     and freelist <= pages and change_counter == version_valid
                     and header[18] in (1, 2) and header[19] in (1, 2))
            return {
                "schema": SCHEMA,
                "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                "workspace": str(workspace),
                "relative_database": str(Path(*parts)),
                "database_bytes": last.st_size,
                "database_allocated_bytes": max(0, last.st_blocks) * 512,
                "database_identity_stable_during_read": stable,
                "wal_mode_indicated_by_header": is_wal,
                "wal_sidecar": _sidecar(parent_fd, parts[-1] + "-wal", mount),
                "shm_sidecar": _sidecar(parent_fd, parts[-1] + "-shm", mount),
                "checkpoint_header_coverage": (
                    "checkpoint_metadata_not_live_transaction" if valid
                    else "stale_unstable_or_invalid_header"),
                "checkpoint_page_size": page_size if valid else None,
                "checkpoint_pages": pages if valid else None,
                "checkpoint_freelist_pages": freelist if valid else None,
                "checkpoint_freelist_bytes_not_reclaim_proof": (
                    page_size * freelist if valid else None),
                "database_rows_or_schema_read": False,
                "live_transaction_verified": False,
                "safe_to_vacuum_or_delete": False,
                "cleanup_performed": False,
                "queue_claims_changed": False,
                "owner_next_step": (
                    "Verify active writers, WAL/checkpoint policy, backup, "
                    "maintenance window and extra free capacity with the "
                    "authorized database owner before any separate intervention."),
            }
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)

def observe_many(root: Path, names: list[str]):
    if (type(names) is not list or not 1 <= len(names) <= MAX_TARGETS
            or len(set(names)) != len(names)):
        raise ValueError("choose between 1 and 8 distinct relative DB paths")
    reports = [observe(root, name) for name in names]
    return {
        "schema": "zsentinel-sqlite-owner-desk-v1",
        "reports": reports,
        "source_coverage": "bounded_metadata_only",
        "database_content_read": False,
        "checkpoint_or_database_write": False,
        "cleanup_performed": False,
        "runner_or_claim_action_authorized": False,
        "reclaimable_bytes_verified": False,
    }

def markdown(report):
    if (type(report) is not dict or report.get("schema") != "zsentinel-sqlite-owner-desk-v1"
            or report.get("checkpoint_or_database_write") is not False
            or report.get("reclaimable_bytes_verified") is not False
            or type(report.get("reports")) is not list
            or len(report["reports"]) > MAX_TARGETS):
        raise ValueError("untrusted SQLite owner report")
    out = [
        "# zSentinel | SQLite-schijfruimte voor bevoegde eigenaar", "",
        "Alleen metadata; geen SQL, checkpoint, VACUUM, delete of runneractie.",
        "Freelist-data is indicatief voor het laatste zichtbare checkpoint; "
        "WAL kan nieuwere wijzigingen bevatten. Geen ruimtewinst gegarandeerd.", "",
    ]
    for row in report["reports"]:
        if row.get("schema") != SCHEMA or row.get("cleanup_performed") is not False:
            raise ValueError("invalid SQLite item")
        label = row["relative_database"].replace(chr(96), "'").replace("|", "/")
        out.extend([
            "## " + label,
            "Bestandsomvang: " + str(round(row["database_bytes"] / 1048576, 1)) +
            " MiB; toegewezen: " + str(round(row["database_allocated_bytes"] / 1048576, 1)) + " MiB.",
            "WAL aangegeven: " + str(row["wal_mode_indicated_by_header"]) +
            "; apart WAL-bestand waargenomen: " + str(row["wal_sidecar"]["observed"]) +
            "; omvang: " + str(row["wal_sidecar"]["size_bytes"]) + " bytes.",
            "Headerdekking: " + row["checkpoint_header_coverage"],
            ("Checkpoint freelist: " + str(row["checkpoint_freelist_pages"]) +
             " paginas (" + str(round(row["checkpoint_freelist_bytes_not_reclaim_proof"] /
             1048576, 1)) + " MiB interne pagina's; geen vrijmaakbaarheidsbewijs)."
             if row["checkpoint_freelist_pages"] is not None
             else "Pagina-vrijlijst onbekend door onstabiele/verouderde bron."),
            "Actie voor bevoegde eigenaar: " + row["owner_next_step"], "",
        ])
    out.append("Geen productieherstel, claims, vrijgemaakte bytes of schijfmutaties bewezen.")
    return "\n".join(out) + "\n"

def html(report):
    safe = escape(markdown(report))
    return (
        '<!doctype html><html lang="nl"><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; '
        'style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>zSentinel SQLite ruimtewerkbank</title>'
        '<style>body{font:16px system-ui;background:#0b1220;color:#f8fafc;'
        'max-width:940px;margin:2rem auto;line-height:1.5}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:1.5rem;'
        'background:#1e293b;border-radius:14px}</style>'
        '<main><h1>SQLite | eigenaarwerkbank</h1><pre>' + safe +
        '</pre></main></html>')

def main():
    parser = argparse.ArgumentParser(description="Bounded SQLite header and WAL disk footprint")
    parser.add_argument("--root", required=True, type=Path,
                        help="existing specific project workspace")
    parser.add_argument("--sqlite", action="append", required=True,
                        help="existing relative SQLite path in workspace; max 8")
    parser.add_argument("--format", choices=("html", "json", "md"), default="html")
    parser.add_argument("--output", required=True, type=Path,
                        help="new private create-only .html/.json/.md file")
    args = parser.parse_args()
    try:
        report = observe_many(args.root, args.sqlite)
        raw = (json.dumps(report, sort_keys=True, ensure_ascii=True, indent=2)
               if args.format == "json" else
               markdown(report) if args.format == "md" else html(report))
        write_private_output(args.output, raw, args.format)
        print("Private SQLite owner report created; no SQL, paths or cleanup printed.")
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        parser.exit(2, "SQLite header evidence unavailable or unsafe; no changes made.\n")

if __name__ == "__main__":
    raise SystemExit(main())
