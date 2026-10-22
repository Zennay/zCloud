#!/usr/bin/env python3
"""Inspect a private zSentinel history handoff ZIP and its internal provenance.

No source collection, QA suite, network, worker claim, merge or deploy.
Checksums detect accidental alteration inside the saved package; they do NOT
authenticate the original source or authorize production recovery.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import re
import stat
import sys
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from zsentinel_product_delivery_board import unique

PACKAGE = re.compile(r"^[a-z][a-z0-9_-]{0,50}\.index\.zip$")
HEX = re.compile(r"^[a-f0-9]{64}$")
MEMBERS = {"history.html", "history.json", "history.csv",
           "README.txt", "manifest.json"}
MAX_ARCHIVE = 4 * 1024 * 1024
MAX_MEMBER = 1024 * 1024


def private_archive_bytes(path: Path) -> bytes:
    """Refuse symlinks, unsafe permissions and changed private file identity."""
    if not PACKAGE.fullmatch(path.name):
        raise ValueError("unexpected history-package filename")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("private no-follow inspection unavailable")
    directory = path.parent
    before_dir = directory.lstat()
    if (not stat.S_ISDIR(before_dir.st_mode)
            or before_dir.st_uid != os.geteuid()
            or stat.S_IMODE(before_dir.st_mode) & 0o077):
        raise ValueError("history parent is not an owned private directory")
    directory_fd = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        actual_dir = os.fstat(directory_fd)
        if (actual_dir.st_dev, actual_dir.st_ino) != (
                before_dir.st_dev, before_dir.st_ino):
            raise ValueError("history directory changed")
        before = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or not 1 <= before.st_size <= MAX_ARCHIVE):
            raise ValueError("history package is not a bounded private file")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW |
                     getattr(os, "O_NONBLOCK", 0), dir_fd=directory_fd)
        try:
            current = os.fstat(fd)
            if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("history source inode changed")
            with os.fdopen(os.dup(fd), "rb") as stream:
                raw = stream.read(MAX_ARCHIVE + 1)
            after = os.fstat(fd)
            if (len(raw) != before.st_size
                    or (after.st_size, after.st_mtime_ns) !=
                       (before.st_size, before.st_mtime_ns)
                    or len(raw) > MAX_ARCHIVE):
                raise ValueError("history source modified during read")
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)


def _json(data: bytes) -> dict:
    document = json.loads(
        data.decode("utf-8"), object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("invalid non-finite JSON value")))
    if type(document) is not dict:
        raise ValueError("source JSON must be an object")
    return document


def inspect_history_bytes(archive: bytes) -> dict:
    """Verify internal JSON/CSV/manifest binding of these exact bytes."""
    if type(archive) is not bytes or not 1 <= len(archive) <= MAX_ARCHIVE:
        raise ValueError("invalid archive bytes")
    with ZipFile(BytesIO(archive)) as package:
        info = package.infolist()
        if (len(info) != len(MEMBERS) or
                {member.filename for member in info} != MEMBERS):
            raise ValueError("history ZIP has missing, duplicate or extra entries")
        parts = {}
        for member in info:
            mode = member.external_attr >> 16
            if (member.compress_type != ZIP_DEFLATED
                    or stat.S_IFMT(mode) != stat.S_IFREG
                    or stat.S_IMODE(mode) != 0o600
                    or not 1 <= member.file_size <= MAX_MEMBER):
                raise ValueError("invalid saved history ZIP member")
            parts[member.filename] = package.read(member)
            if len(parts[member.filename]) != member.file_size:
                raise ValueError("truncated history ZIP member")
        if sum(len(data) for data in parts.values()) > 4 * MAX_MEMBER:
            raise ValueError("unbounded history ZIP expansion")

    manifest = _json(parts["manifest.json"])
    report = _json(parts["history.json"])
    if (manifest.get("schema") !=
            "zsentinel-private-recovery-history-package-v1"
            or report.get("schema") != "zsentinel-saved-bundle-history-v1"
            or manifest.get("source") != "verified_saved_operator_bundles_not_live"
            or manifest.get("generated_at_local_utc") !=
               report.get("generated_at_local_utc")
            or any(manifest.get(key) is not False for key in (
                "current_claim_verified", "required_ci_review_verified",
                "merge_authorized", "deploy_authorized", "live_verified"))
            or manifest.get("historical_sample_only") is not True
            or report.get("current_live_source_checked") is not False
            or report.get("merge_authorized") is not False
            or report.get("deploy_authorized") is not False):
        raise ValueError("historical release authority contract mismatch")
    expected_digests = {
        name: hashlib.sha256(parts[name]).hexdigest()
        for name in MEMBERS - {"manifest.json"}
    }
    if manifest.get("members_sha256") != expected_digests:
        raise ValueError("saved history member digest mismatch")
    snapshots = report.get("snapshots")
    changes = report.get("sample_transitions")
    if (type(snapshots) is not list or not 2 <= len(snapshots) <= 24
            or type(changes) is not list or len(changes) != len(snapshots) - 1):
        raise ValueError("invalid historical snapshot series")
    source_heads = {}
    expected = set()
    for snapshot in snapshots:
        stem = snapshot.get("bundle")
        sha = snapshot.get("board_sha256")
        projects = snapshot.get("projects")
        if (type(stem) is not str or stem in source_heads
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,50}", stem)
                or type(sha) is not str or HEX.fullmatch(sha) is None
                or type(projects) is not list):
            raise ValueError("invalid saved historical source identities")
        source_heads[stem] = sha
        for project in projects:
            pid = project.get("project")
            if type(pid) is not str:
                raise ValueError("invalid project snapshot")
            key = ("snapshot", stem, "", pid)
            if key in expected:
                raise ValueError("duplicate sampled project")
            expected.add(key)
    if manifest.get("saved_bundle_board_sha256") != source_heads:
        raise ValueError("source board identity does not match ZIP manifest")
    for change in changes:
        old, new = change.get("from_bundle"), change.get("to_bundle")
        if old not in source_heads or new not in source_heads:
            raise ValueError("unbound historical source transition")
        for project in change.get("projects", []):
            pid = project.get("project")
            if type(pid) is not str:
                raise ValueError("invalid transition project")
            key = ("sample_change", new, old, pid)
            if key in expected:
                raise ValueError("duplicate sampled source transition")
            expected.add(key)

    csv_text = parts["history.csv"].decode("utf-8-sig")
    if not parts["history.csv"].startswith(b"\xef\xbb\xbf"):
        raise ValueError("missing spreadsheet UTF-8 BOM")
    reader = csv.DictReader(StringIO(csv_text), delimiter=";")
    required = ("record_kind", "source_bundle", "previous_bundle",
                "board_sha256", "project", "historical_sample_only",
                "current_claim_verified", "merge_authorized", "live_verified")
    if any(name not in (reader.fieldnames or []) for name in required):
        raise ValueError("CSV history provenance columns are missing")
    observed = set()
    for row in reader:
        key = (row["record_kind"], row["source_bundle"],
               row["previous_bundle"], row["project"])
        if key not in expected or key in observed:
            raise ValueError("history CSV contains missing/foreign project row")
        if (row["board_sha256"] != source_heads[key[1]]
                or row["historical_sample_only"] != "true"
                or any(row[field] != "false" for field in (
                    "current_claim_verified", "merge_authorized",
                    "live_verified"))):
            raise ValueError("CSV record misstates historical provenance")
        observed.add(key)
    if observed != expected:
        raise ValueError("history CSV project coverage incomplete")

    # Row identity alone misses contradictory numbers or human guidance in a
    # spreadsheet. Rebuild the *entire* canonical CSV from this exact saved
    # source JSON, including the UTF-8 BOM, headers and every column.
    # Deliberately do not reread GitHub, Notion, queue or original bundles.
    from zsentinel_private_bundle_index import render_csv_history
    canonical_csv = render_csv_history(
        [], report.get("index_name", ""),
        machine_text=parts["history.json"].decode("utf-8"),
    ).encode("utf-8")
    if not hashlib.sha256(canonical_csv).digest() == hashlib.sha256(
            parts["history.csv"]).digest() or canonical_csv != parts["history.csv"]:
        raise ValueError("CSV values do not match the saved machine history")
    return {
        "schema": "zsentinel-private-recovery-history-inspection-v1",
        "package_sha256": hashlib.sha256(archive).hexdigest(),
        "source_bundle_count": len(snapshots),
        "project_snapshot_and_change_rows": len(expected),
        "saved_checksum_consistent": True,
        "all_csv_values_rebuilt_from_saved_json": True,
        "source_authenticity_independently_verified": False,
        "current_claim_verified": False,
        "required_ci_review_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "live_verified": False,
    }


def inspect_history_package(path: Path) -> dict:
    """Verify one existing private history package without changing it."""
    return inspect_history_bytes(private_archive_bytes(path))


def extract_verified_history(archive: bytes, destination: Path,
                             basename: str) -> list[str]:
    """Install five reviewed, inert data files into an owned 0700 directory.

    The caller MUST inspect these exact bytes first. Create-only destination
    names are derived from the canonical package basename, not ZIP metadata.
    Every file remains private, the original ZIP and source bundles untouched.
    """
    if not PACKAGE.fullmatch(basename + ".index.zip"):
        raise ValueError("invalid private export basename")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("no-follow private extraction is unavailable")
    # Inspect the same byte sequence we are about to publish. This is a
    # read-model/content-boundary check, not a QA or production action.
    inspect_history_bytes(archive)
    outputs = (
        ("history.html", basename + ".index.html"),
        ("history.json", basename + ".index.json"),
        ("history.csv", basename + ".index.csv"),
        ("README.txt", basename + ".index.README.txt"),
        ("manifest.json", basename + ".index.manifest.json"),
    )
    prior = destination.lstat()
    if (not stat.S_ISDIR(prior.st_mode)
            or prior.st_uid != os.geteuid()
            or stat.S_IMODE(prior.st_mode) != 0o700):
        raise ValueError("extraction requires an existing owned 0700 directory")
    directory_fd = os.open(
        destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created = []
    try:
        current = os.fstat(directory_fd)
        if ((prior.st_dev, prior.st_ino) != (current.st_dev, current.st_ino)
                or current.st_uid != os.geteuid()
                or stat.S_IMODE(current.st_mode) != 0o700):
            raise ValueError("private extraction directory changed")
        with ZipFile(BytesIO(archive)) as package:
            # Named, validated and bounded member access. Never use
            # ZipFile.extract or names/paths supplied by an archive entry.
            values = [(filename, package.read(member))
                      for member, filename in outputs]
        for _, filename in values:
            try:
                os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise FileExistsError("create-only extracted history already exists")
        for filename, data in values:
            fd = os.open(
                filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600, dir_fd=directory_fd)
            created.append((filename, os.fstat(fd).st_ino))
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        os.fsync(directory_fd)
    except BaseException:
        # Only remove files we created and that retain the exact inode.
        # Never delete a caller's pre-existing file or a swapped pathname.
        for filename, inode in reversed(created):
            try:
                found = os.stat(
                    filename, dir_fd=directory_fd, follow_symlinks=False)
                if (stat.S_ISREG(found.st_mode) and found.st_ino == inode):
                    os.unlink(filename, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        os.close(directory_fd)
    return [filename for filename, _ in values]




def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="Existing privately stored .index.zip package")
    parser.add_argument("--extract-to", type=Path,
                        help="Optional existing owned 0700 folder for five "
                             "create-only private companion files")
    args = parser.parse_args(argv)
    try:
        archive = private_archive_bytes(args.source)
        result = inspect_history_bytes(archive)
        if args.extract_to is not None:
            stem = args.source.name[:-len(".index.zip")]
            created = extract_verified_history(
                archive, args.extract_to, stem)
            result["private_extracted_files"] = created
            result["private_extraction_only"] = True
    except (OSError, ValueError, KeyError, TypeError, UnicodeError,
            BadZipFile, OverflowError, csv.Error) as exc:
        print("History package inspection refused: " +
              type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
