#!/usr/bin/env python3
"""Read-only static-dashboard candidate plan; NEVER authorizes VPS promotion.

The comparison baseline is caller-provided Git history, NOT an attestation that
these bytes are deployed. The plan cannot establish live server.js/API
compatibility, safe idle, active generations, or a serialized release lease.
No filesystem, SQLite, browser, runner or GitHub mutating operations are made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

SHA = re.compile(r"[0-9a-f]{40}\Z")
STATIC_ALLOWLIST = frozenset((
    "public/index.html",
    "public/app.js",
    "public/design-system.css",
    "public/enhancements.css",
))
MAX_ASSET_BYTES = 3 * 1024 * 1024
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_DIFF_PATHS = 128


class PlanError(ValueError):
    """An untrusted or incompatible proposed static release."""


def git(root: Path, *argv: str, timeout: int = 15) -> bytes:
    try:
        result = subprocess.run(
            ["git", *argv],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise PlanError("git_identity_or_object_unavailable") from None
    return result.stdout


def require_sha(value: str) -> str:
    if type(value) is not str or not SHA.fullmatch(value):
        raise PlanError("invalid_commit_sha")
    return value


def blob_at(root: Path, sha: str, path: str) -> str | None:
    output = git(root, "ls-tree", "-z", sha, "--", path)
    if not output:
        return None
    rows = output.split(b"\0")
    if len(rows) != 2 or rows[-1] != b"":
        raise PlanError("ambiguous_git_tree_entry")
    try:
        mode_type_blob, actual = rows[0].split(b"\t", 1)
        mode, kind, blob = mode_type_blob.decode("ascii").split(" ")
    except (ValueError, UnicodeError):
        raise PlanError("malformed_git_tree_entry") from None
    if actual != path.encode("utf-8") or mode != "100644" or kind != "blob":
        raise PlanError("non_regular_dashboard_asset")
    return require_sha(blob)


def plan(root: Path, baseline: str, candidate: str) -> dict:
    baseline = require_sha(baseline)
    candidate = require_sha(candidate)
    if baseline == candidate:
        raise PlanError("no_commit_delta")
    if git(root, "rev-parse", "HEAD").decode("ascii").strip() != candidate:
        raise PlanError("candidate_does_not_equal_checked_out_head")
    for sha in (baseline, candidate):
        if git(root, "cat-file", "-t", sha).strip() != b"commit":
            raise PlanError("revision_not_commit")
    try:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", baseline, candidate],
            cwd=root, timeout=15, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=True,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        raise PlanError("baseline_not_ancestor") from None

    raw = git(root, "diff", "--name-status", "--no-renames", "-z",
              baseline, candidate, "--")
    fields = raw.split(b"\0")
    if fields[-1:] != [b""]:
        raise PlanError("unterminated_git_diff")
    fields.pop()
    if not fields or len(fields) % 2:
        raise PlanError("no_or_malformed_git_diff")
    if len(fields) // 2 > MAX_DIFF_PATHS:
        raise PlanError("too_many_changed_paths")
    changed: list[tuple[str, str]] = []
    for index in range(0, len(fields), 2):
        try:
            state = fields[index].decode("ascii")
            path = fields[index + 1].decode("utf-8")
        except UnicodeError:
            raise PlanError("invalid_diff_encoding") from None
        if path not in STATIC_ALLOWLIST or state not in {"A", "M"}:
            raise PlanError("non_static_or_deleted_change")
        if any(existing == path for existing, _ in changed):
            raise PlanError("duplicate_diff_path")
        changed.append((path, state))

    result_assets = []
    total = 0
    for path, status in sorted(changed):
        base_blob = blob_at(root, baseline, path)
        new_blob = blob_at(root, candidate, path)
        if new_blob is None or (status == "M" and base_blob is None):
            raise PlanError("missing_or_unexpected_static_blob")
        if status == "A" and base_blob is not None:
            raise PlanError("unexpected_static_addition")
        try:
            size = int(git(root, "cat-file", "-s", new_blob).strip())
        except (ValueError, UnicodeError):
            raise PlanError("invalid_static_size") from None
        if size < 0 or size > MAX_ASSET_BYTES or total + size > MAX_TOTAL_BYTES:
            raise PlanError("static_byte_budget_exceeded")
        raw_bytes = git(root, "cat-file", "blob", new_blob)
        if len(raw_bytes) != size or b"\0" in raw_bytes:
            raise PlanError("untrusted_static_asset_bytes")
        try:
            raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise PlanError("non_utf8_static_asset") from None
        total += size
        result_assets.append({
            "path": path,
            "change": status,
            "git_blob_sha": new_blob,
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "bytes": size,
        })

    return {
        "schema": "zcloud-dashboard-static-candidate-plan-v1",
        "baseline_sha": baseline,
        "candidate_sha": candidate,
        "baseline_proven_deployed": False,
        "changed_static_assets_only": True,
        "static_diff_eligible": True,
        "live_runtime_compatibility_proven": False,
        "sqlite_ownership_proven": False,
        "production_status_proven": False,
        "release_authorized": False,
        "promotion_authorized": False,
        "total_bytes": total,
        "assets": result_assets,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--baseline-sha", required=True)
    parser.add_argument("--candidate-sha", required=True)
    args = parser.parse_args(argv)
    try:
        evidence = plan(args.root, args.baseline_sha, args.candidate_sha)
    except PlanError as exc:
        print(json.dumps({
            "schema": "zcloud-dashboard-static-candidate-plan-v1",
            "static_diff_eligible": False,
            "release_authorized": False,
            "promotion_authorized": False,
            "reason": str(exc),
        }, sort_keys=True))
        return 2
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
