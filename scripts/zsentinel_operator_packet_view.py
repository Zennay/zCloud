#!/usr/bin/env python3
"""Open a private source-bound zSentinel owner packet as a read-only action desk.

Shows bounded P0/P1/P2 owner follow-ups. Never interprets an archived packet
as live claims, successful CI or merge authority. No network or writes.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
from pathlib import Path
from zipfile import ZIP_STORED, BadZipFile, LargeZipFile, ZipFile

from zsentinel_operator_packet import MEMBERS, MAX_PACKET_BYTES, build_packet
from zsentinel_runtime_handoff import (
    SCHEMA, _short, _source_report_digest, handoff_html,
)

JSON_MAX = 1024 * 1024
SHA = re.compile(r"^[0-9a-f]{64}$")


def _unique(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate owner JSON field")
        output[key] = value
    return output


def _nonfinite(_):
    raise ValueError("non-finite owner JSON number")


def open_packet(path: Path):
    """Reject an altered ZIP; derive the verified action cards from its JSON."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("packet path must be absolute")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("no-follow read unavailable")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW |
                 getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PACKET_BYTES
                or info.st_size < 200):
            raise ValueError("packet is not a bounded regular file")
        # Keep the exact inode open; the filename may be swapped after open.
        with os.fdopen(fd, "rb", closefd=False) as stream:
            actual = stream.read(MAX_PACKET_BYTES + 1)
    finally:
        os.close(fd)
    if len(actual) > MAX_PACKET_BYTES:
        raise ValueError("packet exceeds size limit")
    import io
    with ZipFile(io.BytesIO(actual), "r", allowZip64=False) as archive:
        infos = archive.infolist()
        if (len(infos) != len(MEMBERS)
                or tuple(entry.filename for entry in infos) != MEMBERS
                or any(entry.is_dir() or entry.compress_type != ZIP_STORED
                       or entry.flag_bits & 0x1 or entry.file_size > JSON_MAX
                       or entry.file_size < 1 or entry.filename.startswith("/")
                       or ".." in entry.filename for entry in infos)):
            raise ValueError("untrusted packet member list")
        with archive.open(MEMBERS[0], "r") as source:
            original_json = source.read(JSON_MAX + 1)
        if len(original_json) > JSON_MAX:
            raise ValueError("owner JSON exceeds source budget")
    envelope = json.loads(
        original_json.decode("utf-8"), object_pairs_hook=_unique,
        parse_constant=_nonfinite)
    if type(envelope) is not dict or envelope.get("schema") != SCHEMA:
        raise ValueError("owner JSON has wrong schema")
    recorded_digest = envelope.get("source_report_sha256")
    if type(recorded_digest) is not str or not SHA.fullmatch(recorded_digest):
        raise ValueError("owner JSON has no canonical source digest")
    report = dict(envelope)
    del report["source_report_sha256"]
    digest = _source_report_digest(report)
    if not hmac.compare_digest(digest, recorded_digest):
        raise ValueError("owner JSON source digest mismatch")
    # Stronger than manifest verification: exact archive bytes must match
    # the original approved serializer for every member and archive header.
    rebuilt = build_packet(report)
    if not hmac.compare_digest(actual, rebuilt):
        raise ValueError("owner packet differs from its source-bound contents")
    handoff_html(report)
    return {
        "schema": "zsentinel-private-owner-packet-view-v1",
        "source_report_sha256": digest,
        "source_created_at_utc": report.get("created_at_utc"),
        "partial_source": True,
        "items_truncated": report["items_truncated"],
        "all_sources_complete": False,
        "current_owner_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "recovery_authorized": False,
        "priority_items": [
            {
                "priority": row["priority"],
                "project": row["project"],
                "problem": _short(row["kind"], 80),
                "owner_next_step": _short(row["next_step"], 220),
                "issue_url": row.get("issue_url"),
                "existing_pr_urls": row.get("existing_pr_urls", []),
                "observed_head_sha": row.get("pr_observed_head_sha"),
                "source_state": _short(row.get("evidence"), 80),
            } for row in report["priority_items"]
        ],
        "historical_source_incomplete": report.get("historical_source_incomplete") is True,
        "historical_rechecks_truncated": report.get("historical_rechecks_truncated") is True,
        "historical_owner_rechecks": [
            {
                "priority": row["priority"],
                "project": row["project"],
                "last_seen_utc": row["historical_source_last_seen_utc"],
                "owner_next_step": _short(row["next_step"], 220),
                "current_source_observed": False,
                "historical_reconciliation_required": True,
                "exact_old_job_url": row.get("exact_github_job_url"),
                "exact_old_commit_url": row.get("exact_github_commit_url"),
                "recovery_authorized": False,
            } for row in report.get("historical_owner_rechecks", [])
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Open source-exact offline zSentinel owner ZIP; do not run actions")
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    try:
        report = open_packet(args.packet)
    except (OSError, ValueError, TypeError, KeyError,
            UnicodeError, RecursionError, EOFError, BadZipFile,
            LargeZipFile, RuntimeError) as exc:
        parser.exit(2, f"zSentinel owner packet refused: {exc}\n")
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print("zSentinel · PRIVÉ / OFFLINE / BRONMOMENT " +
              str(report["source_created_at_utc"]))
        print("SHA256-bron: " + report["source_report_sha256"])
        print("Huidige claims, exacte CI, review en merge: NIET geverifieerd; "
              "GEEN automatisch herstel.")
        for row in report["priority_items"]:
            print(row["priority"] + " " + row["project"] + " · " +
                  row["problem"] + " · " + row["owner_next_step"])
            for url in row["existing_pr_urls"]:
                print("  Bestaande PR (opnieuw exact controleren): " + url)
            if row["issue_url"]:
                print("  Bestaand issue: " + row["issue_url"])
        if report["items_truncated"]:
            print("Extra eigenaaracties afgekapt: bron blijft onvolledig.")
        if report["historical_owner_rechecks"]:
            print("HISTORISCH · NIET ACTUEEL WAARGENOMEN; eigenaar hercontroleert bron:")
            for row in report["historical_owner_rechecks"]:
                print("  " + row["priority"] + " " + row["project"] +
                      " · laatst gezien " + row["last_seen_utc"])
                print("    " + row["owner_next_step"])
                if row["exact_old_job_url"]:
                    print("    Oude exacte job, NIET actuele status: " +
                          row["exact_old_job_url"])
        if report["historical_rechecks_truncated"]:
            print("Historische lijst afgekapt: niet-getoonde incidenten zijn NIET opgelost.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
