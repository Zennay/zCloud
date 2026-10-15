#!/usr/bin/env python3
"""Read-only, fail-closed changed-path collision proof for a complete PR snapshot.

Input is an independently collected GitHub PR/files snapshot. This tool never
calls GitHub, grants release authority, changes refs, or handles credentials.
A snapshot does NOT attest current PR ownership or immutable repository state.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

SHA = re.compile(r"^[a-f0-9]{40}$")
PATH = re.compile(r"^[A-Za-z0-9_./@+ -]{1,320}$")
MAX_PRS = 1000
MAX_FILES = 99

class InvalidInventory(ValueError):
    pass


def _sha(value):
    return type(value) is str and SHA.fullmatch(value) is not None


def _path(value):
    return (
        type(value) is str
        and PATH.fullmatch(value) is not None
        and not value.startswith(("/", " "))
        and "\\" not in value
        and all(part not in ("", ".", "..") for part in value.split("/"))
        and not any(ord(char) < 32 for char in value)
    )


def unique_json(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4_000_000:
        raise InvalidInventory("oversize_or_non_text_snapshot")

    def unique(pairs):
        d = {}
        for key, value in pairs:
            if key in d:
                raise InvalidInventory("duplicate_json_key")
            d[key] = value
        return d

    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              InvalidInventory("nonfinite_json_number")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, InvalidInventory):
            raise
        raise InvalidInventory("malformed_snapshot") from exc


def assess(payload):
    """Return bounded evidence, never an approval, from a complete frozen input."""
    if type(payload) is not dict or set(payload) != {"repo", "main_sha", "pulls"}:
        raise InvalidInventory("invalid_root_shape")
    if payload["repo"] != "Zennay/zCloud" or not _sha(payload["main_sha"]):
        raise InvalidInventory("invalid_repo_or_main")
    pulls = payload["pulls"]
    if type(pulls) is not list or not 1 <= len(pulls) <= MAX_PRS:
        raise InvalidInventory("missing_or_unbounded_pr_inventory")
    owners: dict[str, set[int]] = defaultdict(set)
    seen_numbers = set()
    stale_bases = []
    files_examined = 0
    for pr in pulls:
        required = {"number", "state", "head_sha", "base_sha", "head_repo",
                    "base_repo", "changed_files", "files"}
        if type(pr) is not dict or set(pr) != required:
            raise InvalidInventory("invalid_pr_shape")
        number = pr["number"]
        if type(number) is not int or not 1 <= number <= 10_000_000 or number in seen_numbers:
            raise InvalidInventory("invalid_or_duplicate_pr")
        seen_numbers.add(number)
        if (pr["state"] != "open" or not _sha(pr["head_sha"])
                or not _sha(pr["base_sha"])
                or pr["head_repo"] != payload["repo"]
                or pr["base_repo"] != payload["repo"]):
            raise InvalidInventory("untrusted_or_foreign_pr")
        # Stale-base PRs still own their changed paths and MUST remain in the
        # conflict graph. Record drift rather than silently omitting them.
        if pr["base_sha"] != payload["main_sha"]:
            stale_bases.append(number)
        files = pr["files"]
        count = pr["changed_files"]
        if (type(files) is not list or type(count) is not int
                or not 1 <= count <= MAX_FILES or len(files) != count):
            raise InvalidInventory("incomplete_changed_file_inventory")
        files_examined += count
        paths = set()
        for item in files:
            if type(item) is not dict or set(item) != {"filename", "status"}:
                raise InvalidInventory("invalid_file_shape")
            if item["status"] not in ("added", "modified", "removed", "renamed"):
                raise InvalidInventory("unknown_change_status")
            name = item["filename"]
            if not _path(name) or name in paths:
                raise InvalidInventory("unsafe_or_duplicate_file_path")
            paths.add(name)
            owners[name].add(number)

    collisions = [
        {"path": path, "prs": sorted(numbers)}
        for path, numbers in sorted(owners.items())
        if len(numbers) > 1
    ]
    return {
        "policy": "zcloud-deployops-pr-owner-collision-v1",
        "repo": payload["repo"],
        "main_sha": payload["main_sha"],
        "prs_scanned": len(pulls),
        "files_scanned": files_examined,
        "unique_paths_scanned": len(owners),
        "stale_base_prs": sorted(stale_bases)[:50],
        "stale_base_count": len(stale_bases),
        "stale_base_report_truncated": len(stale_bases) > 50,
        "collision_count": len(collisions),
        "collisions": collisions[:200],
        "collision_report_truncated": len(collisions) > 200,
        "input_provenance": "unverified_snapshot",
        "release_authorized": False,
        "deployment_authorized": False,
        "mutation_performed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        raw = args.snapshot.read_text(encoding="utf-8")
        output = assess(unique_json(raw))
    except (OSError, UnicodeError, InvalidInventory) as exc:
        reason = str(exc) if isinstance(exc, InvalidInventory) else "snapshot_read_failed"
        print(json.dumps({"status": "refused", "reason": reason,
                          "release_authorized": False, "mutation_performed": False},
                         sort_keys=True))
        return 2
    print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return 1 if output["collision_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
