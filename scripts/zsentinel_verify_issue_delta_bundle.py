#!/usr/bin/env python3
"""Verify a zSentinel historical issue-delta ZIP without extracting or executing it.

This checks internal archive shape, per-member SHA256/size receipts and the
manifest's explicit non-authority flags. It does not authenticate a publisher,
refresh source data, or prove that saved evidence is current.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import zlib
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

MAX_MEMBER_BYTES = 48 * 1024
MAX_BUNDLE_BYTES = 256 * 1024
MAX_TOTAL_UNCOMPRESSED = 5 * MAX_MEMBER_BYTES
SCHEMA = "zsentinel-private-historical-issue-portfolio-delta-bundle-v1"
EXPECTED_MEMBERS = {
    "issue-delta.html",
    "issue-delta.md",
    "issue-delta.csv",
    "issue-delta.json",
}
EXPECTED_ARCHIVE_MEMBERS = EXPECTED_MEMBERS | {"manifest.json"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NON_AUTHORITY_FLAGS = (
    "current_issue_open_status_verified",
    "worker_claim_authorized",
    "required_ci_review_verified",
    "merged_on_main_verified",
    "live_verified",
    "writes_performed_to_external_systems",
)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON manifest key")
        value[key] = item
    return value


def _read_bounded_file(path: Path) -> bytes:
    """Open only a regular non-symlink input and cap bytes before ZIP parsing."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("bundle input must be a regular file")
        if info.st_size < 1 or info.st_size > MAX_BUNDLE_BYTES:
            raise ValueError("bundle input exceeds the permitted size")
        chunks = []
        remaining = MAX_BUNDLE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if not data or len(data) > MAX_BUNDLE_BYTES:
            raise ValueError("bundle input exceeds the permitted size")
        return data
    finally:
        os.close(fd)


def verify_bundle(data: bytes) -> dict:
    """Verify exact members, safe archive metadata and manifest receipts in memory."""
    if not 1 <= len(data) <= MAX_BUNDLE_BYTES:
        raise ValueError("bundle input exceeds the permitted size")
    with ZipFile(BytesIO(data), "r") as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(infos) != len(EXPECTED_ARCHIVE_MEMBERS):
            raise ValueError("bundle must contain exactly five members")
        if len(set(names)) != len(names):
            raise ValueError("bundle contains duplicate member names")
        if set(names) != EXPECTED_ARCHIVE_MEMBERS:
            raise ValueError("bundle contains missing or unexpected member names")

        payloads = {}
        total_uncompressed = 0
        for info in infos:
            if info.is_dir() or info.flag_bits & 0x1:
                raise ValueError("directories and encrypted members are not allowed")
            if info.compress_type != ZIP_DEFLATED:
                raise ValueError("unexpected ZIP compression method")
            if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
                raise ValueError("bundle member exceeds the permitted size")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
                raise ValueError("bundle uncompressed size exceeds the permitted limit")
            mode = info.external_attr >> 16
            if info.create_system != 3 or not stat.S_ISREG(mode):
                raise ValueError("bundle members must be regular Unix files")
            if stat.S_IMODE(mode) != 0o600:
                raise ValueError("bundle member permissions must be owner-only")
            # Stream in bounded chunks: a hostile deflate stream may expand beyond
            # its declared ZIP file_size, so never call archive.read() unbounded.
            chunks = []
            actual_size = 0
            with archive.open(info, "r") as member:
                while actual_size <= MAX_MEMBER_BYTES:
                    chunk = member.read(min(8192, MAX_MEMBER_BYTES + 1 - actual_size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    actual_size += len(chunk)
                    if actual_size > MAX_MEMBER_BYTES:
                        raise ValueError("bundle member exceeds the permitted decompressed size")
            content = b"".join(chunks)
            if len(content) != info.file_size:
                raise ValueError("bundle member size does not match ZIP metadata")
            payloads[info.filename] = content

    try:
        manifest = json.loads(
            payloads["manifest.json"].decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("bundle manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise ValueError("unsupported bundle manifest schema")
    if not isinstance(manifest.get("source"), str) or not manifest["source"]:
        raise ValueError("bundle manifest has no source identifier")
    for key in ("old_manifest_sha256", "new_manifest_sha256"):
        value = manifest.get(key)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise ValueError("bundle manifest has an invalid source receipt")

    receipts = manifest.get("members")
    if not isinstance(receipts, list) or len(receipts) != len(EXPECTED_MEMBERS):
        raise ValueError("bundle manifest must describe exactly four payloads")
    receipt_by_name = {}
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != {"name", "sha256", "size_bytes"}:
            raise ValueError("bundle manifest contains an invalid member receipt")
        name, digest, size = receipt["name"], receipt["sha256"], receipt["size_bytes"]
        if name not in EXPECTED_MEMBERS or name in receipt_by_name:
            raise ValueError("bundle manifest has duplicate or unexpected payloads")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ValueError("bundle manifest contains an invalid member SHA256")
        if type(size) is not int or not 0 <= size <= MAX_MEMBER_BYTES:
            raise ValueError("bundle manifest contains an invalid member size")
        receipt_by_name[name] = receipt
    if set(receipt_by_name) != EXPECTED_MEMBERS:
        raise ValueError("bundle manifest does not cover every payload")

    for name, receipt in receipt_by_name.items():
        payload = payloads[name]
        if len(payload) != receipt["size_bytes"]:
            raise ValueError("member size receipt mismatch: " + name)
        if sha256(payload).hexdigest() != receipt["sha256"]:
            raise ValueError("member SHA256 receipt mismatch: " + name)

    # Bind the manifest to the JSON payload too: a self-consistent checksum list
    # must not conceal disagreement between duplicated source/provenance metadata.
    try:
        report = json.loads(
            payloads["issue-delta.json"].decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("issue-delta.json is not valid UTF-8 JSON") from exc
    if not isinstance(report, dict):
        raise ValueError("issue-delta.json must contain an object")
    matching_fields = (
        ("source", "source"),
        ("source_scope", "source_scope"),
        ("old_manifest_sha256", "old_manifest_sha256"),
        ("new_manifest_sha256", "new_manifest_sha256"),
        ("old_latest_observed_utc", "old_latest_observed_utc"),
        ("new_latest_observed_utc", "new_latest_observed_utc"),
        ("old_source_freshness", "old_source_freshness"),
        ("new_source_freshness", "new_source_freshness"),
        ("counts", "counts"),
    )
    for manifest_key, report_key in matching_fields:
        if manifest.get(manifest_key) != report.get(report_key):
            raise ValueError("bundle source metadata disagrees with JSON payload")
    if manifest.get("selection") != report.get("display"):
        raise ValueError("bundle selection metadata disagrees with JSON payload")
    if report.get("sample_absence_is_not_resolution") is not True:
        raise ValueError("JSON payload lost its non-resolution warning")
    for key in ("worker_claim_authorized", "required_ci_review_verified",
                "merged_on_main_verified", "live_verified"):
        if report.get(key) is not False or manifest.get(key) is not False:
            raise ValueError("bundle payload has unsafe authority flag: " + key)
    if manifest.get("current_issue_open_status_verified") is not False:
        raise ValueError("bundle manifest must not claim current issue status")
    rows = report.get("issues")
    if (not isinstance(rows, list) or len(rows) > 60
            or not isinstance(report.get("display"), dict)
            or report["display"].get("shown") != len(rows)):
        raise ValueError("bundle JSON payload has inconsistent bounded selection")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("bundle JSON payload has an invalid issue row")
        for key in ("current_issue_open_status_verified", "claim_verified",
                    "merge_verified", "deploy_verified"):
            if row.get(key) is not False:
                raise ValueError("issue row has unsafe authority flag: " + key)
    for key in NON_AUTHORITY_FLAGS:
        if manifest.get(key) is not False:
            raise ValueError("bundle manifest has unsafe authority flag: " + key)

    return {
        "integrity": "verified",
        "schema": SCHEMA,
        "source": manifest["source"],
        "old_manifest_sha256": manifest["old_manifest_sha256"],
        "new_manifest_sha256": manifest["new_manifest_sha256"],
        "payloads": [
            {"name": name, "size_bytes": receipt_by_name[name]["size_bytes"],
             "sha256": receipt_by_name[name]["sha256"]}
            for name in sorted(EXPECTED_MEMBERS)
        ],
        "source_freshness_or_authenticity": "not established by bundle integrity",
        "current_issue_status_or_ownership": "not verified or authorized",
        "main_merge_or_live_deployment": "not verified",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="private ZIP bundle to inspect")
    args = parser.parse_args(argv)
    try:
        result = verify_bundle(_read_bounded_file(args.bundle))
    except (OSError, ValueError, TypeError, KeyError, BadZipFile, OverflowError,
            EOFError, RuntimeError, struct.error, zlib.error) as exc:
        print("Bundle verification refused: " + type(exc).__name__, file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    print("Integrity verified only; publisher authenticity, live status and release authority remain unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
