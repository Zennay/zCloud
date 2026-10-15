#!/usr/bin/env python3
"""Read-only, fail-closed integrity probe for a zCloud last-known-good snapshot.

This standalone preflight never repairs, restores, approves or deploys anything.
The existing rollback implementation remains owned by its separate PR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

MAX_JSON = 2 * 1024 * 1024
MAX_FILES = 50000
MAX_BYTES = 512 * 1024 * 1024
SNAPSHOT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-(?:[a-f0-9]{8}|working-tree)(?:-[0-9]+)?$")
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
MANAGED_ROOTS = {
    "server.py", "project_runtime.py", "lane_generator.py", "enhancements.py",
    "projects.json", "project-layout.json", "resource-policy.json",
    "project-contracts.json", "portfolio_queue.seed.json", "public",
    "firefox-extension", "deploy", "scripts",
}
PERSISTENT = {
    "history.db", "history.db-wal", "history.db-shm", ".watch-token",
    ".action-allowed-ips", "watch-tls.crt", "watch-tls.key",
    "signals", "alert-state.json", ".git", "repos",
}


class Denied(ValueError):
    """Public, fixed-category denial; never include filesystem contents."""


def _pairs(items: list[tuple[str, object]]) -> dict:
    obj = {}
    for key, value in items:
        if key in obj:
            raise Denied("ambiguous_json")
        obj[key] = value
    return obj


def _constant(_value: str) -> None:
    raise Denied("nonfinite_json")


def _regular(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except (OSError, ValueError) as exc:
        raise Denied("unreadable_path") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Denied("nonregular_file")
    return info


def _directory(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except (OSError, ValueError) as exc:
        raise Denied("unreadable_path") from exc
    if not stat.S_ISDIR(mode):
        raise Denied("nonregular_directory")


def _open_regular(path: Path):
    _regular(path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise Denied("nonregular_file")
        return os.fdopen(fd, "rb")
    except (OSError, ValueError) as exc:
        raise Denied("unreadable_path") from exc


def _json(path: Path) -> dict:
    with _open_regular(path) as f:
        data = f.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise Denied("oversized_json")
    try:
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, Denied):
            raise
        raise Denied("invalid_json") from exc
    if not isinstance(parsed, dict):
        raise Denied("invalid_json_shape")
    return parsed


def _canonical_file(name: object) -> bool:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        return False
    p = PurePosixPath(name)
    return (
        bool(p.parts)
        and not p.is_absolute()
        and ".." not in p.parts
        and "." not in p.parts
        and name == p.as_posix()
        and p.parts[0] in MANAGED_ROOTS
        and p.parts[0] not in PERSISTENT
    )


def inspect(state: Path) -> dict:
    """Verify pointer, manifest, tree shape and exact SHA256 contents; do not restore."""
    _directory(state)
    _directory(state / "snapshots")
    ptr = _json(state / "last-known-good.json")
    snap_id = ptr.get("snapshot_id")
    if not isinstance(snap_id, str) or SNAPSHOT_ID_RE.fullmatch(snap_id) is None:
        raise Denied("invalid_snapshot_id")
    snapshot = state / "snapshots" / snap_id
    files = snapshot / "files"
    _directory(snapshot)
    _directory(files)
    manifest = _json(snapshot / "manifest.json")
    if type(manifest.get("format_version")) is not int or manifest["format_version"] != 1 or manifest.get("snapshot_id") != snap_id:
        raise Denied("manifest_identity_mismatch")
    expected = manifest.get("hashes")
    if not isinstance(expected, dict) or any(
        not _canonical_file(key) or not isinstance(value, str)
        or SHA_RE.fullmatch(value) is None
        for key, value in expected.items()
    ):
        raise Denied("invalid_hash_manifest")
    # A snapshot with no control-plane entrypoint is not a safe rollback
    # candidate even if its empty hash inventory is self-consistent.
    if "server.py" not in expected:
        raise Denied("missing_required_source")
    count, size = 0, 0
    seen = set()
    # Bound the directory enumeration *before* sorting it. A malicious tree
    # must not exhaust CI/VPS memory by presenting millions of entries.
    entries = []
    try:
        for entry in files.rglob("*"):
            entries.append(entry)
            if len(entries) > MAX_FILES * 3:
                raise Denied("too_many_paths")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, Denied):
            raise
        raise Denied("invalid_snapshot_tree") from exc
    for path in sorted(entries):
        if path.is_symlink():
            raise Denied("symlink_in_snapshot")
        try:
            mode = path.lstat().st_mode
        except (OSError, ValueError) as exc:
            raise Denied("unreadable_path") from exc
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise Denied("special_file_in_snapshot")
        name = path.relative_to(files).as_posix()
        if not _canonical_file(name) or name not in expected:
            raise Denied("unexpected_snapshot_file")
        count += 1
        if count > MAX_FILES:
            raise Denied("too_many_files")
        digest = hashlib.sha256()
        with _open_regular(path) as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise Denied("oversized_snapshot")
                digest.update(chunk)
        if digest.hexdigest() != expected[name]:
            raise Denied("hash_mismatch")
        seen.add(name)
    if seen != set(expected):
        raise Denied("missing_snapshot_files")
    return {
        "ok": True, "snapshot_id": snap_id, "file_count": count,
        "verified_bytes": size, "mutation_performed": False,
        "authorizes_rollback": False, "authorizes_deploy": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = inspect(args.state_dir.absolute())
    except (Denied, OSError) as exc:
        code = str(exc) if isinstance(exc, Denied) else "io_error"
        result = {
            "ok": False, "reason": code, "mutation_performed": False,
            "authorizes_rollback": False, "authorizes_deploy": False,
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
