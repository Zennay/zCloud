#!/usr/bin/env python3
"""Verify an existing private zSentinel operator bundle without writing files.

This checks local delivery integrity only. It neither establishes freshness nor
authorizes a claim, CI retry, approval, merge, or deployment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

STEM = re.compile(r"^[a-z][a-z0-9_-]{0,50}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_RECEIPT = 64 * 1024
MAX_PRODUCT = 6 * 1024 * 1024
MAX_OWNER = 1024 * 1024


class BundleInvalid(ValueError):
    """The saved delivery receipt cannot be trusted as a complete bundle."""


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise BundleInvalid("duplicate JSON key in private receipt")
        obj[key] = value
    return obj


def _open_private_directory(directory):
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise BundleInvalid("secure private directory access unavailable")
    before = directory.lstat()
    if (not stat.S_ISDIR(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o077):
        raise BundleInvalid("bundle directory must be owned and private")
    fd = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
        getattr(os, "O_CLOEXEC", 0))
    after = os.fstat(fd)
    if (not stat.S_ISDIR(after.st_mode)
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or after.st_uid != os.geteuid()
            or stat.S_IMODE(after.st_mode) & 0o077):
        os.close(fd)
        raise BundleInvalid("bundle directory identity changed")
    return fd


def _read_private(dir_fd, basename, maximum):
    # All basenames are generated from the CLI stem and fixed suffixes,
    # never read from arbitrary manifest paths.
    fd = os.open(
        basename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
        getattr(os, "O_CLOEXEC", 0), dir_fd=dir_fd)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if (not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1 or not 1 <= before.st_size <= maximum):
            raise BundleInvalid("private artifact identity or permissions invalid: " + basename)
        raw = source.read(maximum + 1)
        after = os.fstat(source.fileno())
        if (len(raw) != before.st_size
                or len(raw) > maximum
                or (before.st_dev, before.st_ino, before.st_mtime_ns,
                    before.st_size) !=
                   (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size)):
            raise BundleInvalid("private artifact changed during read: " + basename)
    # Detect replacement between opening the pathname and verifying it.
    named = os.stat(basename, dir_fd=dir_fd, follow_symlinks=False)
    if ((before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
            or not stat.S_ISREG(named.st_mode)):
        raise BundleInvalid("private artifact pathname changed: " + basename)
    return raw


def _decode_json(raw):
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                BundleInvalid("nonstandard JSON constant")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BundleInvalid("private JSON cannot be decoded") from exc


def verify_bundle(directory: Path, name: str) -> dict:
    if not isinstance(name, str) or STEM.fullmatch(name) is None:
        raise BundleInvalid("name must be a short lowercase safe stem")
    directory_fd = _open_private_directory(directory)
    try:
        receipt_name = name + ".receipt.json"
        receipt_raw = _read_private(directory_fd, receipt_name, MAX_RECEIPT)
        receipt = _decode_json(receipt_raw)
        if not isinstance(receipt, dict):
            raise BundleInvalid("receipt must be an object")
        if (receipt.get("schema") != "zsentinel-operator-bundle-receipt-v1"
                or receipt.get("bundle_complete") is not True
                or receipt.get("source_is_live") is not False
                or any(receipt.get(field) is not False for field in (
                    "claims_authorized", "ci_verified", "review_verified",
                    "merge_authorized", "deploy_authorized"))):
            raise BundleInvalid("receipt schema or non-authorizing contract invalid")

        artifacts = receipt.get("artifacts")
        if not isinstance(artifacts, list) or len(artifacts) not in (2, 3):
            raise BundleInvalid("receipt must list two or three operator artifacts")
        expected = [
            (name + ".board.json", "product_delivery_board", MAX_PRODUCT),
            (name + ".desk.html", "interactive_private_operator_desk", MAX_PRODUCT),
        ]
        if len(artifacts) == 3:
            expected.append((name + ".owner.md", "merge_first_owner_handoff", MAX_OWNER))
        owner_count = receipt.get("owner_records_in_bounded_sample")
        if (len(artifacts) == 3) != (type(owner_count) is int and owner_count >= 0):
            raise BundleInvalid("optional owner handoff receipt is inconsistent")
        card_count = receipt.get("cards_in_bounded_sample")
        if type(card_count) is not int or not 0 <= card_count <= 60:
            raise BundleInvalid("bounded issue-card receipt invalid")
        hashes = {}
        payloads = {}
        for entry, (basename, role, maximum) in zip(artifacts, expected):
            if (not isinstance(entry, dict)
                    or entry.get("file") != basename or entry.get("role") != role
                    or type(entry.get("bytes")) is not int
                    or not 1 <= entry["bytes"] <= maximum
                    or not isinstance(entry.get("sha256"), str)
                    or HEX64.fullmatch(entry["sha256"]) is None):
                raise BundleInvalid("artifact descriptor invalid: " + basename)
            raw = _read_private(directory_fd, basename, maximum)
            digest = hashlib.sha256(raw).hexdigest()
            if entry["bytes"] != len(raw) or digest != entry["sha256"]:
                raise BundleInvalid("artifact length or SHA256 mismatch: " + basename)
            hashes[role] = digest
            payloads[role] = raw

        board = _decode_json(payloads["product_delivery_board"])
        if (not isinstance(board, dict)
                or board.get("schema") != "zsentinel-product-delivery-triage-v1"
                or board.get("observed_at_utc") !=
                    receipt.get("source_board_observed_at_utc")):
            raise BundleInvalid("board provenance does not match the receipt")
        # The manifest's claimed card count and provenance must describe
        # the exact serialized board, not merely bytes with matching hashes.
        cards = board.get("cards")
        if (not isinstance(cards, list)
                or len(cards) != card_count
                or board.get("fresh") != receipt.get("original_source_freshness")):
            raise BundleInvalid("board sample/provenance disagrees with receipt")
        board_hash = hashes["product_delivery_board"].encode("ascii")
        if board_hash not in payloads["interactive_private_operator_desk"]:
            raise BundleInvalid("HTML desk lacks original board hash binding")
        if len(artifacts) == 3 and board_hash not in payloads["merge_first_owner_handoff"]:
            raise BundleInvalid("owner handoff lacks original board hash binding")
        return {
            "schema": "zsentinel-bundle-integrity-verification-v1",
            "bundle_name": name,
            "integrity_verified": True,
            "artifacts_verified": len(artifacts),
            "receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
            "board_sha256": hashes["product_delivery_board"],
            "verified_at_local_utc": datetime.now(timezone.utc).isoformat(),
            "saved_source_only": True,
            "current_github_or_worker_state_verified": False,
            "independent_review_verified": False,
            "merge_authorized": False,
            "deploy_authorized": False,
            "actions_executed": False,
        }
    finally:
        os.close(directory_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True,
                        help="Existing user-owned private bundle directory")
    parser.add_argument("--name", required=True,
                        help="Safe filename stem used for the original bundle")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    try:
        result = verify_bundle(args.directory, args.name)
    except (BundleInvalid, OSError, TypeError, UnicodeError) as exc:
        # Do not expose OSError filenames or private source bytes in logs.
        detail = str(exc) if isinstance(exc, BundleInvalid) else "private file read failed"
        print("zSentinel bundle integrity refused: " + detail, file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("# zSentinel saved operator bundle integrity")
        print("")
        print("- Bundle: `" + result["bundle_name"] + "`")
        print("- Offline artifact hashes match: YES (" +
              str(result["artifacts_verified"]) + " artifacts)")
        print("- Original board SHA-256: `" + result["board_sha256"] + "`")
        print("- Current GitHub/worker/review/merge/deploy authorized: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
