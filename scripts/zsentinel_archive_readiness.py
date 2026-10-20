#!/usr/bin/env python3
"""zSentinel external-storage cold-data candidate desk (metadata only).

An external mount with ample space is NOT proof a file can be safely moved.
No copy, delete, rename, archive upload, active-worker or database change.
All candidate file names stay in an explicit private report, never stdout.
"""
from __future__ import annotations
import argparse
import json
import os
import stat
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from zsentinel_disk_hotspots import (observe as scan_disk, _root,
                                     SCHEMA as DISK_SCHEMA, ARCHIVE_SUFFIX_HINTS)
from zsentinel_private_output import write_private_output

SCHEMA = "zsentinel-external-archive-readiness-v1"
# Candidate hints only. Some of these files may still be actively used or private.
COLD_SUFFIXES = ARCHIVE_SUFFIX_HINTS
MIN_AGE_DAYS = 7

def observe(report: dict, destination: Path, *, min_age_days=MIN_AGE_DAYS):
    if (type(report) is not dict or report.get("schema") != DISK_SCHEMA
            or report.get("file_contents_read") is not False
            or report.get("deletion_authorized") is not False
            or report.get("runner_restart_authorized") is not False
            or report.get("queue_claims_changed") is not False
            or type(report.get("top_files")) is not list
            or len(report["top_files"]) > 15
            or type(min_age_days) is not int or not 7 <= min_age_days <= 365):
        raise ValueError("untrusted bounded source disk observation")
    src = _root(Path(report["root"]))
    target = _root(destination)
    sr = src.stat()
    dr = target.stat()
    if (not stat.S_ISDIR(sr.st_mode) or not stat.S_ISDIR(dr.st_mode)
            or sr.st_dev == dr.st_dev or target == src
            or src in target.parents or target in src.parents):
        raise ValueError("archive must be a separate existing mount outside source workspace")
    target_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(target, target_flags)
    try:
        opened = os.fstat(fd)
        if opened.st_dev != dr.st_dev or opened.st_ino != dr.st_ino:
            raise ValueError("archive mount identity changed")
        fs = os.fstatvfs(fd)
        if not (fs.f_frsize > 0 and fs.f_blocks > 0
                and 0 <= fs.f_bavail <= fs.f_blocks):
            raise ValueError("invalid target mount capacity")
        available = fs.f_bavail * fs.f_frsize
        if available > 10**16:
            raise ValueError("unbounded archive mount capacity")
    finally:
        os.close(fd)
    now = datetime.now(timezone.utc)
    candidates = []
    cold_rows = report.get("cold_candidates_sample", report["top_files"])
    if type(cold_rows) is not list or len(cold_rows) > 15:
        raise ValueError("untrusted bounded cold-data sample")
    omitted = {"unsupported_or_suspect": 0, "too_recent": 0, "invalid_metadata": 0}
    for item in cold_rows:
        if (type(item) is not dict or type(item.get("relative_path")) is not str
                or type(item.get("allocated_mib")) not in (float, int)
                or type(item.get("last_modified_utc")) is not str):
            raise ValueError("invalid disk candidate row")
        name = item["relative_path"]
        if (not any(name.lower().endswith(ext) for ext in COLD_SUFFIXES)
                or any(part.startswith(".") for part in Path(name).parts)):
            omitted["unsupported_or_suspect"] += 1
            continue
        try:
            seen_at = datetime.fromisoformat(item["last_modified_utc"])
            if seen_at.tzinfo is None:
                raise ValueError("unverified datetime")
            days_old = (now - seen_at).total_seconds() / 86400
        except (ValueError, OverflowError):
            omitted["invalid_metadata"] += 1
            continue
        if days_old < min_age_days or days_old > 36525:
            omitted["too_recent"] += 1
            continue
        # No auto-copy or automatic safety classification. File may be live,
        # shared, licensed, personal or changed after metadata observation.
        candidates.append({
            "relative_file": name,
            "observed_allocated_mib": item["allocated_mib"],
            "last_modified_utc": item["last_modified_utc"],
            "age_days_at_observation": round(days_old, 1),
            "writer_and_rights_status": "unverified_owner_check_required",
            "copy_authorized": False,
            "delete_authorized": False,
        })
    return {
        "schema": SCHEMA,
        "source_observed_at_utc": report["observed_at_utc"],
        "archive_observed_at_utc": now.isoformat(),
        "source_workspace": str(src),
        "destination_mount": str(target),
        "source_device": sr.st_dev,
        "target_device": dr.st_dev,
        "archive_available_gib_estimate": round(available / 1024**3, 2),
        "archive_statvfs_not_usable_space_guarantee": True,
        "candidate_age_days_minimum": min_age_days,
        "candidate_files": candidates,
        "candidate_count": len(candidates),
        "cold_extension_hints_seen_in_source_lower_bound": report.get(
            "archive_suffix_candidates_seen_lower_bound"),
        "candidate_bytes_lower_bound": round(
            sum(x["observed_allocated_mib"] for x in candidates) * 1048576),
        "candidate_omitted": omitted,
        "source_scan_coverage": report["coverage"],
        "source_unscanned_directories": report["directories_unscanned"],
        "remote_archive_write_verified": False,
        "safe_to_transfer_verified": False,
        "safe_to_delete_source_verified": False,
        "files_transferred_or_removed": False,
        "claim_or_runner_mutated": False,
        "next_owner_action": (
            "Verify archive privacy, permissions, quotas, retention, restore access, "
            "per-file source ownership and active writers; execute a separately "
            "authorized integrity-checked transfer before any source deletion."),
    }

def public_summary(data):
    if (type(data) is not dict or data.get("schema") != SCHEMA
            or data.get("files_transferred_or_removed") is not False
            or data.get("safe_to_transfer_verified") is not False
            or data.get("safe_to_delete_source_verified") is not False
            or type(data.get("candidate_count")) is not int
            or not 0 <= data["candidate_count"] <= 15
            or data.get("source_scan_coverage") not in (
                "partial", "bounded_complete_with_no_private_exclusions")):
        raise ValueError("untrusted archive candidate receipt")
    return {
        "schema": "zsentinel-external-archive-summary-v1",
        "archive_separate_mount_observed": True,
        "archive_available_gib_estimate": data["archive_available_gib_estimate"],
        "cold_extension_hints_seen_in_source_lower_bound": data.get(
            "cold_extension_hints_seen_in_source_lower_bound"),
        "candidate_count_sampled_not_transferable": data["candidate_count"],
        "candidate_gib_lower_bound_not_savings": round(
            data["candidate_bytes_lower_bound"]/1024**3, 2),
        "source_coverage": data["source_scan_coverage"],
        "archive_write_verified": False,
        "owner_transfer_authorized": False,
        "files_transferred_or_removed": False,
        "source_disk_space_reclaimed": False,
        "filenames_or_mount_paths_disclosed": False,
    }

def markdown(data):
    public_summary(data)
    out = [
        "# zSentinel | koude bestanden naar externe opslag", "",
        "ALLEEN LEZEN. Geen bestand verplaatst, gekopieerd of verwijderd.",
        "Bronworkspace: " + data["source_workspace"],
        "Geobserveerde doelmount: " + data["destination_mount"],
        "Andere mount aangetroffen; doelruimte-indicatie: " +
        str(data["archive_available_gib_estimate"]) + " GiB (geen schrijfgarantie).",
        "Bronscan: " + data["source_scan_coverage"] +
        "; mappen niet onderzocht: " + str(data["source_unscanned_directories"]),
        "Mogelijke oudere bestandskandidaten in beperkte topbestandenscan: " +
        str(data["candidate_count"]) + ". Geen bestandsveiligheid afgeleid.", "",
    ]
    for x in data["candidate_files"]:
        name = x["relative_file"].replace("|", "/").replace(chr(96), "'")
        out.append("- " + name + " | minstens " +
                   str(x["observed_allocated_mib"]) + " MiB | ouderdom " +
                   str(x["age_days_at_observation"]) +
                   " dagen; actief writer/claim/retentie ONBEKEND")
    out.extend(["", "De kandidaatselectie is geen complete inventaris en "
                "geen toestemming om data naar externe opslag te sturen.",
                "Bevoegde eigenaar: " + data["next_owner_action"],
                "Resultaat: files_transferred_or_removed=false; "
                "source_disk_space_reclaimed=false.", ""])
    return "\n".join(out)

def html(data):
    return (
        '<!doctype html><html lang="nl"><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; '
        'style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>zSentinel | koude data en archiefroute</title>'
        '<style>body{font:16px system-ui;background:#0f172a;color:#f1f5f9;'
        'line-height:1.6;max-width:920px;margin:2rem auto;padding:1rem}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;'
        'border:1px solid #475569;border-radius:12px;padding:1rem}</style>'
        '<main><h1>Archiefroute | controle door eigenaar</h1><pre>' +
        escape(markdown(data)) + '</pre></main></html>'
    )

def main():
    p = argparse.ArgumentParser(description="Read-only external archive candidate workbench")
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--archive-root", required=True, type=Path)
    p.add_argument("--min-age-days", type=int, default=MIN_AGE_DAYS)
    p.add_argument("--max-entries", type=int, default=5000)
    p.add_argument("--max-depth", type=int, default=3)
    p.add_argument("--max-seconds", type=int, default=5)
    p.add_argument("--format", choices=("html","json","md"), default="html")
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    try:
        report = scan_disk(args.root, min_mib=1, max_entries=args.max_entries,
                           max_depth=args.max_depth, max_seconds=args.max_seconds)
        plan = observe(report,args.archive_root,min_age_days=args.min_age_days)
        value = (json.dumps(plan, ensure_ascii=True, sort_keys=True, indent=2)
                 if args.format == "json" else markdown(plan)
                 if args.format == "md" else html(plan))
        write_private_output(args.output,value,args.format)
        print("Private archive candidate workbench created; no files transferred or removed.")
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        p.exit(2,"zSentinel archive metadata unavailable or unsafe; no changes made.\n")

if __name__ == "__main__":
    raise SystemExit(main())
