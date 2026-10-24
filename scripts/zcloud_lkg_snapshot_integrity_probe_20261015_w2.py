#!/usr/bin/env python3
"""Read-only, fail-closed integrity probe for a zCloud last-known-good snapshot.

This standalone preflight never repairs, restores, approves or deploys anything.
The existing rollback implementation remains owned by its separate PR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path, PurePosixPath
from datetime import datetime

MAX_JSON = 2 * 1024 * 1024
MAX_FILES = 50000
MAX_SNAPSHOTS = 16
MAX_BATCH_BYTES = 1024 * 1024 * 1024
MAX_BYTES = 512 * 1024 * 1024
SNAPSHOT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-(?:[a-f0-9]{8}|working-(?:tree)?)(?:-[0-9]+)?$")
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
MANAGED_ROOTS = {
    "server.py", "project_runtime.py", "lane_generator.py", "enhancements.py",
    "projects.json", "project-layout.json", "resource-policy.json",
    "project-contracts.json", "portfolio_queue.seed.json", "public",
    "firefox-extension", "deploy", "scripts",
}
DIRECTORY_ROOTS = {"public", "firefox-extension", "deploy", "scripts"}
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


def _finite_float(raw: str) -> float:
    number = float(raw)
    if not math.isfinite(number):
        raise Denied("nonfinite_json")
    return number


def _valid_unicode_tree(document: object) -> None:
    """Reject unpaired JSON surrogate escapes, including in ignored metadata."""
    pending = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
                raise Denied("invalid_unicode")
        elif isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def _snapshot_id_valid(value: object) -> bool:
    if not isinstance(value, str) or SNAPSHOT_ID_RE.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value[:16], "%Y%m%dT%H%M%SZ")
    except ValueError:
        return False
    return True


def _regular(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except (OSError, ValueError) as exc:
        raise Denied("unreadable_path") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Denied("nonregular_file")
    return info


def _open_directory_nofollow(path: Path) -> int:
    """Open every ancestor from filesystem root using directory descriptors.

    Checking only the leaf path permits a swapped parent symlink to redirect
    metadata reads. All components are required to be actual directories.
    """
    absolute = path.absolute()
    parts = absolute.parts
    if not parts or not absolute.is_absolute() or any(x in (".", "..") for x in parts[1:]):
        raise Denied("noncanonical_directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    current = None
    try:
        current = os.open(parts[0], flags)
        for component in parts[1:]:
            following = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = following
        owned = current
        current = None
        return owned
    except OSError as exc:
        raise Denied("nonregular_directory") from exc
    finally:
        if current is not None:
            os.close(current)


def _directory(path: Path) -> None:
    fd = _open_directory_nofollow(path)
    os.close(fd)


def _directory_identity(path: Path) -> tuple[int, int]:
    """Observe the actual directory inode without following any ancestor links."""
    fd = _open_directory_nofollow(path)
    try:
        info = os.fstat(fd)
        return (info.st_dev, info.st_ino)
    finally:
        os.close(fd)


def _open_regular(path: Path):
    _regular(path)
    parent_fd = _open_directory_nofollow(path.parent)
    try:
        fd = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise Denied("nonregular_file")
        try:
            return os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
    except OSError as exc:
        raise Denied("unreadable_path") from exc
    finally:
        os.close(parent_fd)


def _open_snapshot_regular(root: Path, name: str):
    """Walk snapshot components with O_NOFOLLOW, not a path-join open.

    A symlink swapped into an intermediate directory after enumeration must
    never redirect the file reader outside the selected snapshot.
    """
    if not _canonical_file(name):
        raise Denied("unexpected_snapshot_file")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    parent_fd = None
    try:
        parent_fd = _open_directory_nofollow(root)
        parts = PurePosixPath(name).parts
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        fd = os.open(parts[-1], file_flags, dir_fd=parent_fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise Denied("nonregular_file")
        try:
            return os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
    except OSError as exc:
        raise Denied("unreadable_path") from exc
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _json(path: Path) -> dict:
    with _open_regular(path) as f:
        data = f.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise Denied("oversized_json")
    try:
        parsed = json.loads(
            data.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=_constant, parse_float=_finite_float,
        )
        _valid_unicode_tree(parsed)
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
        and ((p.parts[0] in DIRECTORY_ROOTS) == (len(p.parts) > 1))
    )


def _iter_snapshot_entries(root: Path):
    """Enumerate every child or deny; never silently skip unreadable dirs.

    pathlib.rglob may skip enumeration errors, which could hide unlisted files.
    Walk open directory descriptors with nofollow and an explicit depth cap.
    """
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)

    def visit(fd: int, prefix: tuple[str, ...], depth: int):
        if depth > 64:
            raise Denied("snapshot_tree_too_deep")
        with os.scandir(fd) as entries:
            for child in entries:
                parts = prefix + (child.name,)
                path = root.joinpath(*parts)
                yield path
                if child.is_dir(follow_symlinks=False):
                    next_fd = os.open(child.name, flags, dir_fd=fd)
                    try:
                        yield from visit(next_fd, parts, depth + 1)
                    finally:
                        os.close(next_fd)

    fd = _open_directory_nofollow(root)
    try:
        yield from visit(fd, (), 0)
    finally:
        os.close(fd)


def _string_set(value: object, *, kind: str, allowed: set[str]) -> set[str]:
    """Deny ambiguous metadata, duplicates and unrecognized managed roots."""
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) for item in value)
        or len(value) != len(set(value))
        or any(item not in allowed for item in value)
    ):
        raise Denied("invalid_" + kind)
    return set(value)


def _verify_manifest_metadata(manifest: dict, expected: dict[str, str]) -> set[str]:
    managed = _string_set(
        manifest.get("managed_paths"), kind="managed_paths", allowed=MANAGED_ROOTS,
    )
    if managed != MANAGED_ROOTS:
        raise Denied("managed_contract_drift")
    persistent = _string_set(
        manifest.get("persistent_paths_never_restored"),
        kind="persistent_paths", allowed=PERSISTENT,
    )
    if persistent != PERSISTENT:
        raise Denied("persistent_contract_drift")
    present = _string_set(
        manifest.get("present_paths"), kind="present_paths", allowed=MANAGED_ROOTS,
    )
    if not present.issubset(managed) or not {key.split("/", 1)[0] for key in expected}.issubset(present):
        raise Denied("manifest_inventory_mismatch")
    config = manifest.get("config_hashes")
    config_names = {
        "projects.json", "project-layout.json", "resource-policy.json",
        "project-contracts.json",
    }
    if not isinstance(config, dict) or set(config) != (set(expected) & config_names):
        raise Denied("config_inventory_mismatch")
    if any(
        not isinstance(value, str)
        or SHA_RE.fullmatch(value) is None
        or expected[name] != value
        for name, value in config.items()
    ):
        raise Denied("config_hash_mismatch")
    return present


def inspect(
    state: Path, *, snapshot_id: str | None = None,
    _batch_bytes: list[int] | None = None,
) -> dict:
    """Verify selected snapshot without ever modifying the active LKG pointer.

    A batch caller supplies a shared byte counter. Rejected snapshots count
    against the cap as soon as bytes are read, not only after hash success.
    """
    _directory(state)
    _directory(state / "snapshots")
    ptr = _json(state / "last-known-good.json")
    current_id = ptr.get("snapshot_id")
    if not _snapshot_id_valid(current_id):
        raise Denied("invalid_snapshot_id")
    snap_id = current_id if snapshot_id is None else snapshot_id
    if not _snapshot_id_valid(snap_id):
        raise Denied("invalid_snapshot_id")
    snapshot = state / "snapshots" / snap_id
    files = snapshot / "files"
    snapshot_identity = _directory_identity(snapshot)
    files_identity = _directory_identity(files)
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
    declared_roots = _verify_manifest_metadata(manifest, expected)
    count, size = 0, 0
    seen = set()
    # Bound the directory enumeration *before* sorting it. A malicious tree
    # must not exhaust CI/VPS memory by presenting millions of entries.
    entries = []
    try:
        for entry in _iter_snapshot_entries(files):
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
        with _open_snapshot_regular(files, name) as f:
            before = os.fstat(f.fileno())
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if _batch_bytes is not None:
                    _batch_bytes[0] += len(chunk)
                    if _batch_bytes[0] > MAX_BATCH_BYTES:
                        raise Denied("batch_byte_limit")
                if size > MAX_BYTES:
                    raise Denied("oversized_snapshot")
                digest.update(chunk)
            after = os.fstat(f.fileno())
        identity = lambda st: (
            st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns,
        )
        if identity(before) != identity(after):
            raise Denied("source_changed_during_scan")
        # Catch ordinary replacement of the pathname after the descriptor was
        # opened. The descriptor scan itself already follows no symlink.
        try:
            current = path.lstat()
        except OSError as exc:
            raise Denied("source_changed_during_scan") from exc
        if identity(before) != identity(current) or not stat.S_ISREG(current.st_mode):
            raise Denied("source_changed_during_scan")
        if digest.hexdigest() != expected[name]:
            raise Denied("hash_mismatch")
        seen.add(name)
    if seen != set(expected):
        raise Denied("missing_snapshot_files")
    # A manifest can truthfully describe an empty managed directory. Check the
    # direct children as well, not just the nonempty file hash list.
    root_fd = _open_directory_nofollow(files)
    try:
        with os.scandir(root_fd) as root_entries:
            roots = tuple(
                (entry.name, entry.stat(follow_symlinks=False))
                for entry in root_entries
            )
    finally:
        os.close(root_fd)
    actual_roots = {name for name, _ in roots}
    if actual_roots != declared_roots:
        raise Denied("manifest_inventory_mismatch")
    for name, info in roots:
        if (name in DIRECTORY_ROOTS) != stat.S_ISDIR(info.st_mode):
            raise Denied("managed_path_type_mismatch")
    # A source tree might be changed during a long scan. This second check
    # catches ordinary pointer/manifest churn; it is not a filesystem lock.
    if _json(state / "last-known-good.json") != ptr:
        raise Denied("pointer_changed_during_scan")
    if _json(snapshot / "manifest.json") != manifest:
        raise Denied("manifest_changed_during_scan")
    if (
        _directory_identity(snapshot) != snapshot_identity
        or _directory_identity(files) != files_identity
    ):
        raise Denied("snapshot_changed_during_scan")
    return {
        "ok": True, "snapshot_id": snap_id, "is_current": snap_id == current_id,
        "file_count": count, "verified_bytes": size, "mutation_performed": False,
        "authorizes_rollback": False, "authorizes_deploy": False,
    }


def _snapshot_entries(state: Path) -> list[tuple[str, bool, int, int]]:
    """Bounded identity inventory; detect same-name directory replacements."""
    directory_fd = _open_directory_nofollow(state / "snapshots")
    try:
        with os.scandir(directory_fd) as iterator:
            snapshots = []
            for item in iterator:
                info = item.stat(follow_symlinks=False)
                snapshots.append((
                    item.name, stat.S_ISDIR(info.st_mode), info.st_dev, info.st_ino,
                ))
                if len(snapshots) > MAX_SNAPSHOTS:
                    raise Denied("too_many_snapshots")
    finally:
        os.close(directory_fd)
    return sorted(snapshots)


def audit_all(state: Path) -> dict:
    """Bounded all-snapshot inventory; never elect or authorize an alternative."""
    _directory(state)
    _directory(state / "snapshots")
    pointer = _json(state / "last-known-good.json")
    current = pointer.get("snapshot_id")
    if not _snapshot_id_valid(current):
        raise Denied("invalid_snapshot_id")

    snapshots = _snapshot_entries(state)
    if not snapshots:
        raise Denied("no_snapshots")

    results: list[dict[str, object]] = []
    verified_config_hashes: dict[str, dict[str, str]] = {}
    batch_bytes = [0]
    for name, is_dir, _device, _inode in snapshots:
        if not _snapshot_id_valid(name) or not is_dir:
            entry = {"snapshot_id": None, "ok": False, "reason": "invalid_snapshot_entry"}
        else:
            try:
                manifest_path = state / "snapshots" / name / "manifest.json"
                manifest_before = _json(manifest_path)
                evidence = inspect(
                    state, snapshot_id=name, _batch_bytes=batch_bytes,
                )
                if manifest_before != _json(manifest_path):
                    raise Denied("manifest_changed_during_scan")
            except (Denied, OSError) as exc:
                # A shared resource limit is terminal, never a mere
                # per-snapshot finding which could allow later extra reads.
                if isinstance(exc, Denied) and str(exc) == "batch_byte_limit":
                    raise
                reason = str(exc) if isinstance(exc, Denied) else "io_error"
                entry = {"snapshot_id": name, "ok": False, "reason": reason}
            else:
                # Only snapshots which passed the complete file/manifest proof
                # may contribute to the advisory config comparison.
                verified_config_hashes[name] = dict(manifest_before["config_hashes"])
                entry = {
                    "snapshot_id": name, "ok": True,
                    "file_count": evidence["file_count"],
                    "verified_bytes": evidence["verified_bytes"],
                }
        entry["is_current"] = name == current
        results.append(entry)
    if _json(state / "last-known-good.json") != pointer:
        raise Denied("pointer_changed_during_scan")
    if _snapshot_entries(state) != snapshots:
        raise Denied("snapshot_inventory_changed")
    ids = {name for name, *_identity in snapshots}
    if current not in ids:
        raise Denied("current_snapshot_missing")
    active_config = verified_config_hashes.get(current)
    if active_config is not None:
        for item in results:
            historic = verified_config_hashes.get(item["snapshot_id"])
            if historic is None:
                continue
            old_names, current_names = set(historic), set(active_config)
            item["config_drift_from_current"] = {
                "changed": sorted(
                    name for name in old_names & current_names
                    if historic[name] != active_config[name]
                ),
                "only_in_snapshot": sorted(old_names - current_names),
                "only_in_current": sorted(current_names - old_names),
            }
    return {
        "ok": all(item["ok"] for item in results),
        "snapshot_count": len(snapshots),
        "current_snapshot_id": current,
        "results": results,
        "mutation_performed": False,
        "authorizes_rollback": False,
        "authorizes_deploy": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument(
        "--all-snapshots", action="store_true",
        help="Audit every retained snapshot without selecting or restoring any",
    )
    args = parser.parse_args(argv)
    try:
        result = (
            audit_all(args.state_dir.absolute()) if args.all_snapshots
            else inspect(args.state_dir.absolute())
        )
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
