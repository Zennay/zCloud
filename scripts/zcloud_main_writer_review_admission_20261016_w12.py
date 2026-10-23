#!/usr/bin/env python3
"""Deny-first offline review triage for immutable main-writer source inventory.

The review JSON is self-declared evidence, NOT authenticated GitHub approval.
No git, network, repository write, protected-branch or release operations.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import stat
import sys
from pathlib import Path

MAX_INVENTORY_BYTES = 320 * 1024
MAX_REVIEWS_BYTES = 128 * 1024
MAX_NESTING = 24
MAX_CANDIDATES = 512
HEX40 = re.compile(r"[a-f0-9]{40}\Z")
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
PATH = re.compile(r"\.github/workflows/[A-Za-z0-9_.+/-]{1,190}\.ya?ml\Z")
HANDLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z")
EVIDENCE_URL = re.compile(
    r"https://github\.com/Zennay/zCloud/(?:issues|pull)/[1-9][0-9]{0,8}"
    r"(?:#issuecomment-[1-9][0-9]{0,18}|#discussion_r[1-9][0-9]{0,18})?\Z")
KINDS = {"direct_main_ref", "other_git_push", "git_refs_api", "pull_request_merge",
         "contents_write", "write_all", "github_script"}
STRONG = {"direct_main_ref", "git_refs_api", "pull_request_merge"}
DECISIONS = {"migrate", "retire", "false_positive"}
CSV_HEADERS = (
    "main_sha", "workflow_path", "source_sha256", "signal_lines_and_kinds",
    "priority", "owner", "reviewed_by", "decision", "evidence_url",
    "review_verified", "branch_protection_authorized",
)


class ReviewError(Exception):
    """Stable non-sensitive error code, no caller data included."""


def no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReviewError("duplicate_json_key")
        result[key] = value
    return result


def reject_numeric(_: str):
    raise ReviewError("unsupported_number")


def read_bytes(path: Path, limit: int) -> bytes:
    """Bounded, fd-anchored read: no symlink in any parent or the final file."""
    if not path.name or any(part == ".." for part in path.parts):
        raise ReviewError("unsafe_path")
    full = path if path.is_absolute() else Path.cwd() / path
    directory_fd = None
    try:
        directory_fd = os.open("/", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        for part in full.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                              getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(full.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                     getattr(os, "O_NONBLOCK", 0), dir_fd=directory_fd)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ReviewError("unsafe_or_oversized_file")
            blob = handle.read(limit + 1)
        if len(blob) > limit:
            raise ReviewError("unsafe_or_oversized_file")
    except (OSError, ValueError) as exc:
        raise ReviewError("unreadable_file") from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    return blob


def read_json(path: Path, limit: int) -> dict:
    blob = read_bytes(path, limit)
    try:
        parsed = json.loads(blob.decode("utf-8"),
                            object_pairs_hook=no_duplicates,
                            parse_constant=reject_numeric, parse_float=reject_numeric,
                            parse_int=lambda s: int(s) if len(s) < 12
                            else reject_numeric(s))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ReviewError("invalid_json") from exc
    stack = [(parsed, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_NESTING:
            raise ReviewError("nested_json")
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
            stack.extend((k, depth + 1) for k in node)
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
        elif isinstance(node, str):
            if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or
                   0x7F <= ord(c) <= 0x9F or
                   c in "\u200b\u200c\u200d\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
                   for c in node):
                raise ReviewError("unsafe_unicode")
    if not isinstance(parsed, dict):
        raise ReviewError("invalid_schema")
    return parsed


def fields(obj, allowed: set[str]) -> None:
    if not isinstance(obj, dict) or set(obj) != allowed:
        raise ReviewError("invalid_schema")


def valid_path(value: object) -> bool:
    return (isinstance(value, str) and PATH.fullmatch(value) is not None and
            "//" not in value and "/../" not in value and "/./" not in value)


def checked_inventory(data: dict, expected_sha: str) -> dict[str, str]:
    fields(data, {"schema", "main_sha", "static_scan_complete",
                  "workflows_scanned", "candidate_count",
                  "direct_writer_candidate_count", "no_static_signal_count",
                  "candidates", "historical_activity_verified",
                  "github_branch_protection_verified", "pr_branch_ownership_verified",
                  "safe_to_enable_branch_protection", "production_mutation_authorized"})
    if (data["schema"] != "zcloud-main-writer-compat-v1" or
        data["main_sha"] != expected_sha or
        data["static_scan_complete"] is not True or
        any(data[k] is not False for k in (
            "historical_activity_verified", "github_branch_protection_verified",
            "pr_branch_ownership_verified", "safe_to_enable_branch_protection",
            "production_mutation_authorized"))):
        raise ReviewError("untrusted_inventory_claim")
    cand = data["candidates"]
    if not isinstance(cand, list) or len(cand) > MAX_CANDIDATES:
        raise ReviewError("invalid_inventory_candidates")
    for key in ("workflows_scanned", "candidate_count",
                "direct_writer_candidate_count", "no_static_signal_count"):
        if type(data[key]) is not int or not 0 <= data[key] <= MAX_CANDIDATES:
            raise ReviewError("invalid_inventory_counts")
    if (data["candidate_count"] != len(cand) or
        data["workflows_scanned"] != len(cand) + data["no_static_signal_count"]):
        raise ReviewError("invalid_inventory_counts")
    seen = {}
    strong = 0
    for row in cand:
        fields(row, {"path", "source_sha256", "classification", "signals", "disposition"})
        path, digest = row["path"], row["source_sha256"]
        if (not valid_path(path) or path in seen or
            not isinstance(digest, str) or not HEX64.fullmatch(digest) or
            row["disposition"] != "manual_review_required" or
            not isinstance(row["signals"], list) or not row["signals"] or
            len(row["signals"]) > 160):
            raise ReviewError("invalid_inventory_candidates")
        kinds = set()
        signal_keys = set()
        for signal in row["signals"]:
            fields(signal, {"line", "kind"})
            line, kind = signal["line"], signal["kind"]
            if (type(line) is not int or not 1 <= line <= 100000 or
                not isinstance(kind, str) or kind not in KINDS or
                (line, kind) in signal_keys):
                raise ReviewError("invalid_inventory_signal")
            kinds.add(kind)
            signal_keys.add((line, kind))
        classification = ("direct_writer_candidate" if kinds & STRONG else
                          "capability_or_indirect_writer_candidate")
        if row["classification"] != classification:
            raise ReviewError("contradictory_inventory_classification")
        strong += bool(kinds & STRONG)
        seen[path] = digest
    if strong != data["direct_writer_candidate_count"]:
        raise ReviewError("invalid_inventory_counts")
    return seen


def csv_to_reviews(inventory: dict, raw: bytes, expected_sha: str) -> dict:
    """Convert the *exact* inventory CSV back into untrusted review claims.

    Every original candidate must be present exactly once, with immutable
    provenance, signal codes, priority and deny flags unchanged. Only owner,
    reviewer, decision and evidence_url may be filled in. No approvals follow.
    """
    paths = checked_inventory(inventory, expected_sha)
    try:
        content = raw.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise ReviewError("invalid_csv_encoding") from exc
    if any((ord(c) < 32 and c not in "\r\n\t") or
           0x7f <= ord(c) <= 0x9f or
           c in "\u200b\u200c\u200d\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
           for c in content):
        raise ReviewError("unsafe_csv_controls")
    try:
        reader = csv.reader(io.StringIO(content, newline=""), strict=True)
        rows = list(reader)
    except (csv.Error, ValueError) as exc:
        raise ReviewError("invalid_csv") from exc
    if (not rows or tuple(rows[0]) != CSV_HEADERS or
        len(rows) - 1 > MAX_CANDIDATES):
        raise ReviewError("invalid_csv_header_or_size")
    indexed = {row["path"]: row for row in inventory["candidates"]}
    seen = set()
    claims = []
    for values in rows[1:]:
        if len(values) != len(CSV_HEADERS):
            raise ReviewError("invalid_csv_columns")
        item = dict(zip(CSV_HEADERS, values))
        path = item["workflow_path"]
        if not valid_path(path) or path not in paths or path in seen:
            raise ReviewError("duplicate_or_unknown_csv_candidate")
        seen.add(path)
        source = indexed[path]
        signals = "|".join(
            str(signal["line"]) + ":" + signal["kind"]
            for signal in source["signals"]
        )
        priority = ("P1" if source["classification"] == "direct_writer_candidate"
                    else "P2")
        if (item["main_sha"] != expected_sha or
            item["source_sha256"] != paths[path] or
            item["signal_lines_and_kinds"] != signals or
            item["priority"] != priority):
            raise ReviewError("tampered_csv_provenance")
        if (item["review_verified"] != "false" or
            item["branch_protection_authorized"] != "false"):
            raise ReviewError("forged_csv_authorization")
        mutable = ("owner", "reviewed_by", "decision", "evidence_url")
        filled = [bool(item[field]) for field in mutable]
        if any(filled) and not all(filled):
            raise ReviewError("incomplete_csv_review")
        if all(filled):
            claims.append({
                "path": path, "source_sha256": paths[path],
                "owner": item["owner"], "reviewed_by": item["reviewed_by"],
                "decision": item["decision"], "evidence_url": item["evidence_url"],
            })
    if seen != set(paths):
        raise ReviewError("missing_csv_candidates")
    return {"schema": "zcloud-main-writer-review-v1",
            "main_sha": expected_sha, "decisions": claims}


def review(inventory: dict, reviews: dict, expected_sha: str) -> dict:
    if not isinstance(expected_sha, str) or not HEX40.fullmatch(expected_sha):
        raise ReviewError("invalid_expected_sha")
    available = checked_inventory(inventory, expected_sha)
    fields(reviews, {"schema", "main_sha", "decisions"})
    if (reviews["schema"] != "zcloud-main-writer-review-v1" or
        reviews["main_sha"] != expected_sha or
        not isinstance(reviews["decisions"], list) or
        len(reviews["decisions"]) > MAX_CANDIDATES):
        raise ReviewError("stale_or_invalid_reviews")
    accepted = set()
    counts = {name: 0 for name in sorted(DECISIONS)}
    for row in reviews["decisions"]:
        fields(row, {"path", "source_sha256", "owner", "reviewed_by",
                     "decision", "evidence_url"})
        path, digest = row["path"], row["source_sha256"]
        if (not valid_path(path) or path not in available or path in accepted or
            digest != available[path]):
            raise ReviewError("stale_duplicate_or_unknown_review")
        if (not isinstance(row["decision"], str) or
            row["decision"] not in DECISIONS):
            raise ReviewError("invalid_review_decision")
        if (not isinstance(row["owner"], str) or
            not isinstance(row["reviewed_by"], str) or
            not HANDLE.fullmatch(row["owner"]) or
            not HANDLE.fullmatch(row["reviewed_by"]) or
            row["owner"].lower() == row["reviewed_by"].lower()):
            raise ReviewError("invalid_review_identity")
        if (not isinstance(row["evidence_url"], str) or
            not EVIDENCE_URL.fullmatch(row["evidence_url"])):
            raise ReviewError("invalid_review_evidence")
        accepted.add(path)
        counts[row["decision"]] += 1
    return {
        "schema": "zcloud-main-writer-triage-v1",
        "main_sha": expected_sha,
        "candidate_count": len(available),
        "review_claim_count": len(accepted),
        "pending_owner_review_count": len(available) - len(accepted),
        "review_claims_by_disposition": counts,
        "unreviewed_paths": sorted(set(available) - accepted),
        "offline_review_claims_complete": len(available) == len(accepted),
        "review_claims_independently_verified": False,
        "historical_activity_verified": False,
        "safe_to_enable_branch_protection": False,
        "production_mutation_authorized": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline historical writer review (deny-first)")
    parser.add_argument("--inventory", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--reviews", help="Strict offline JSON review claims")
    source.add_argument("--reviews-csv", help="Edited exact-inventory CSV review queue")
    parser.add_argument("--expected-main-sha", required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    try:
        if not HEX40.fullmatch(args.expected_main_sha):
            raise ReviewError("invalid_expected_sha")
        inventory = read_json(Path(args.inventory), MAX_INVENTORY_BYTES)
        reviews = (read_json(Path(args.reviews), MAX_REVIEWS_BYTES)
                   if args.reviews is not None else
                   csv_to_reviews(inventory,
                                  read_bytes(Path(args.reviews_csv), MAX_REVIEWS_BYTES),
                                  args.expected_main_sha))
        result = review(inventory, reviews, args.expected_main_sha)
    except (ReviewError, OSError) as exc:
        code = str(exc) if isinstance(exc, ReviewError) else "unreadable_file"
        if args.format == "markdown":
            print("## Bronbeoordeling geweigerd\n\nOorzaak: " + code +
                  "\n\n**GEEN VRIJGAVE VOOR MAIN OF PRODUCTIE.**")
        else:
            print(json.dumps({"ok": False, "error": code,
                              "review_claims_independently_verified": False,
                              "safe_to_enable_branch_protection": False,
                              "production_mutation_authorized": False}, sort_keys=True))
        return 2
    if args.format == "markdown":
        print("## zCloud main-schrijvers: beoordeling per eigenaar")
        print("\nBronmain: " + result["main_sha"])
        print("Kandidaten: " + str(result["candidate_count"]))
        print("Ingediende claims: " + str(result["review_claim_count"]))
        print("Nog zonder geldige claim: " + str(result["pending_owner_review_count"]))
        print("Migreren: " + str(result["review_claims_by_disposition"]["migrate"]))
        print("Uitfaseren: " + str(result["review_claims_by_disposition"]["retire"]))
        print("False positive: " + str(result["review_claims_by_disposition"]["false_positive"]))
        print("\n**GEEN VRIJGAVE.** Identiteit, activiteit en reviewlinks zijn "
              "niet onafhankelijk op GitHub geverifieerd.")
        for path in result["unreviewed_paths"]:
            print("- " + path + " — eigenaar en onderbouwing vereist")
    else:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
