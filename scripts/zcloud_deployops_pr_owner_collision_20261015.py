#!/usr/bin/env python3
"""Read-only, fail-closed changed-path collision proof for a complete PR snapshot.

Input is an independently collected GitHub PR/files snapshot. This tool never
calls GitHub, grants release authority, changes refs, or handles credentials.
A snapshot does NOT attest current PR ownership or immutable repository state.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import os
import re
import stat
import sys
from collections import defaultdict
from pathlib import Path

SHA = re.compile(r"^[a-f0-9]{40}$")
PATH = re.compile(r"^[A-Za-z0-9_./@+ -]{1,320}$")
MAX_PRS = 1000
MAX_FILES = 300
MAX_SNAPSHOT_BYTES = 4_000_000

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
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
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
            if type(item) is not dict or "status" not in item:
                raise InvalidInventory("invalid_file_shape")
            status = item["status"]
            if status not in ("added", "modified", "removed", "renamed"):
                raise InvalidInventory("unknown_change_status")
            expected_fields = ({"filename", "status", "previous_filename"}
                               if status == "renamed" else {"filename", "status"})
            if set(item) != expected_fields:
                raise InvalidInventory("invalid_file_shape")
            names = [item["filename"]]
            if status == "renamed":
                # GitHub changed_files counts a rename once. Both path
                # identities must nevertheless block a competing PR.
                names.append(item["previous_filename"])
            if any(not _path(name) or name in paths for name in names):
                raise InvalidInventory("unsafe_or_duplicate_file_path")
            if len(set(names)) != len(names):
                raise InvalidInventory("duplicate_rename_path")
            for name in names:
                paths.add(name)
                owners[name].add(number)

    collisions = [
        {"path": path, "prs": sorted(numbers)}
        for path, numbers in sorted(owners.items())
        if len(numbers) > 1
    ]
    # Show transitive file-ownership conflict groups, not just path pairs:
    # PR 1 ↔ 2 on file A and PR 2 ↔ 3 on file B form one review group.
    # File disjointness is strictly advisory; semantic and PR-less branch
    # collisions are NOT covered, nor does a group grant merge authority.
    parent = {number: number for number in seen_numbers}

    def root(number):
        while parent[number] != number:
            parent[number] = parent[parent[number]]
            number = parent[number]
        return number

    def join(left, right):
        lroot, rroot = root(left), root(right)
        if lroot != rroot:
            parent[max(lroot, rroot)] = min(lroot, rroot)

    conflicting = set()
    for numbers in owners.values():
        if len(numbers) > 1:
            group = sorted(numbers)
            conflicting.update(group)
            for number in group[1:]:
                join(group[0], number)
    # Git cannot simultaneously materialize a regular file at "docs/a"
    # and descendants such as "docs/a/readme.md". Two PRs touching those
    # distinct paths can therefore conflict even without an exact path match.
    # Consider only literal parent component boundaries, never substring
    # prefixes ("docs/a" must NOT conflict with "docs/abc").
    hierarchy_collisions = []
    hierarchy_collision_count = 0
    for child, descendants in sorted(owners.items()):
        for position, character in enumerate(child):
            if character != "/":
                continue
            ancestor = child[:position]
            ancestors = owners.get(ancestor)
            if not ancestors:
                continue
            members = sorted(ancestors | descendants)
            if len(members) < 2:
                continue  # A single PR can itself replace a file with a tree.
            hierarchy_collision_count += 1
            if len(hierarchy_collisions) < 200:
                hierarchy_collisions.append({
                    "ancestor": ancestor, "descendant": child,
                    "pr_count": len(members), "prs": members[:50],
                    "pr_ids_truncated": len(members) > 50,
                })
            conflicting.update(members)
            for number in members[1:]:
                join(members[0], number)
    connected = defaultdict(list)
    for number in sorted(conflicting):
        connected[root(number)].append(number)
    groups = sorted(connected.values(), key=lambda members: members[0])
    conflict_groups = [
        {"pr_count": len(members), "prs": members[:50],
         "pr_ids_truncated": len(members) > 50}
        for members in groups[:80]
    ]
    uncontested = sorted(seen_numbers - conflicting)
    # A deterministic digest makes two independent read-only operator
    # snapshots comparable without storing unbounded PR metadata in logs.
    # This is an evidence identifier, not a signature or authorization.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False).encode("utf-8")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    return {
        "policy": "zcloud-deployops-pr-owner-collision-v1",
        "snapshot_sha256": fingerprint,
        "repo": payload["repo"],
        "main_sha": payload["main_sha"],
        "prs_scanned": len(pulls),
        "files_scanned": files_examined,
        "unique_paths_scanned": len(owners),
        "stale_base_prs": sorted(stale_bases)[:50],
        "stale_base_count": len(stale_bases),
        "stale_base_report_truncated": len(stale_bases) > 50,
        "collision_count": len(collisions),
        "hierarchy_collision_count": hierarchy_collision_count,
        "hierarchy_collisions": hierarchy_collisions,
        "hierarchy_collision_report_truncated": hierarchy_collision_count > 200,
        "conflicted_pr_count": len(conflicting),
        "conflict_group_count": len(groups),
        "conflict_groups": conflict_groups,
        "conflict_group_report_truncated": len(groups) > 80,
        "uncontested_pr_count": len(uncontested),
        "uncontested_pr_ids": uncontested[:80],
        "uncontested_pr_ids_truncated": len(uncontested) > 80,
        "collisions": collisions[:200],
        "collision_report_truncated": len(collisions) > 200,
        "input_provenance": "unverified_snapshot",
        "release_authorized": False,
        "deployment_authorized": False,
        "mutation_performed": False,
    }


def read_snapshot(path):
    """Read bounded regular UTF-8 bytes without following symlinks or FIFOs."""
    # pathlib.Path returns platform-specific concrete subclasses (PosixPath).
    # Reject non-Path inputs, not valid concrete Path implementations.
    if not isinstance(path, Path):
        raise InvalidInventory("invalid_snapshot_path")
    # O_NOFOLLOW on the final file alone is insufficient: an ancestor
    # directory may be swapped for a symlink. Walk each path component using
    # directory file descriptors so a parent cannot redirect our read.
    # This tool deliberately runs on the GitHub-hosted Linux runner.
    components = path.parts
    if not components or ".." in components or path.name in ("", ".", ".."):
        raise InvalidInventory("invalid_snapshot_path")
    directory_flags = (os.O_RDONLY | os.O_DIRECTORY |
                       getattr(os, "O_NOFOLLOW", 0))
    file_flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                  getattr(os, "O_NONBLOCK", 0))
    directory_fd = None
    try:
        directory_fd = os.open("/" if path.is_absolute() else ".", directory_flags)
        parents = components[1:-1] if path.is_absolute() else components[:-1]
        for component in parents:
            if component == ".":
                continue
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        with os.fdopen(os.open(path.name, file_flags, dir_fd=directory_fd), "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SNAPSHOT_BYTES:
                raise InvalidInventory("unsafe_or_oversized_snapshot")
            data = stream.read(MAX_SNAPSHOT_BYTES + 1)
            after = os.fstat(stream.fileno())
            if (len(data) > MAX_SNAPSHOT_BYTES
                    or before.st_dev != after.st_dev
                    or before.st_ino != after.st_ino
                    or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns
                    or len(data) != before.st_size):
                raise InvalidInventory("changed_or_oversized_snapshot")
        return data.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise InvalidInventory("snapshot_read_failed") from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        raw = read_snapshot(args.snapshot)
        output = assess(unique_json(raw))
    except (OSError, UnicodeError, InvalidInventory) as exc:
        reason = str(exc) if isinstance(exc, InvalidInventory) else "snapshot_read_failed"
        print(json.dumps({"status": "refused", "reason": reason,
                          "release_authorized": False, "mutation_performed": False},
                         sort_keys=True))
        return 2
    print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return 1 if (output["collision_count"] or output["hierarchy_collision_count"]) else 0


if __name__ == "__main__":
    sys.exit(main())
