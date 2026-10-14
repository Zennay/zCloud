#!/usr/bin/env python3
"""Monotonic, read-only screen for automatically triggered VPS mutation workflows.

This is a conservative source tripwire, not permission to deploy or to recover a
runner. Existing automatic mutation workflows are grandfathered ONLY while their
entire file is byte-identical to the exact PR base.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

WORKFLOWS = Path(".github/workflows")
SUFFIXES = {".yml", ".yaml"}
MAX_FILES = 400
MAX_BYTES = 256 * 1024
AUTO = re.compile(
    r"(?m)^\s{2}(?:push|workflow_run|schedule|repository_dispatch|release):"
    r"|^\s*[\"']?on[\"']?:\s*(?:\[?\s*(?:push|workflow_run|schedule|repository_dispatch|release)\b|\$\{\{)"
)
RUNNER = re.compile(r"(?mi)^\s*runs-on:\s*.*self-hosted|^\s*-\s*self-hosted\s*$")
MUTATION = re.compile(
    r"(?mi)^\s*(?:-\s*)?(?:run:\s*)?"
    r".*\b(?:sudo\b|systemctl\s+(?:restart|stop|start|enable|disable)"
    r"|git\s+push\b|rsync\b|rm\s+-rf\b|killall\b|pkill\b"
    r"|gh\s+api\s+--method\s+(?:POST|PUT|PATCH|DELETE)\b"
    r"|curl\b[^\n]*\s-X\s*(?:POST|PUT|PATCH|DELETE)\b)"
)
SHA = re.compile(r"[0-9a-f]{40}\Z")


def _scan_text(text: str) -> bool:
    # Text-only detection intentionally overapproximates: it is a review gate,
    # never proof that a workflow is safe.
    if not AUTO.search(text):
        return False
    if not RUNNER.search(text):
        return False
    return bool(MUTATION.search(text))


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=25,
    )
    return result.stdout


def _decode(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ValueError("oversized workflow")
    return data.decode("utf-8", "strict")


def _read_worktree(root: Path) -> dict[str, bytes]:
    folder = root / WORKFLOWS
    if not folder.is_dir() or folder.is_symlink():
        raise ValueError("missing or symlinked workflow directory")
    rows: dict[str, bytes] = {}
    for item in sorted(folder.iterdir()):
        if item.suffix not in SUFFIXES:
            continue
        if item.is_symlink() or not item.is_file():
            raise ValueError("unexpected workflow file type")
        rel = item.relative_to(root).as_posix()
        data = item.read_bytes()
        _decode(data)
        rows[rel] = data
        if len(rows) > MAX_FILES:
            raise ValueError("too many workflows")
    return rows


def _read_git(root: Path, revision: str) -> dict[str, bytes]:
    if not SHA.fullmatch(revision):
        raise ValueError("base must be immutable lowercase SHA")
    actual = _git(root, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
    if actual != revision:
        raise ValueError("base commit unavailable")
    rows: dict[str, bytes] = {}
    for line in _git(root, "ls-tree", "-rz", revision, "--", WORKFLOWS.as_posix()).split(b"\0"):
        if not line:
            continue
        metadata, sep, path_raw = line.partition(b"\t")
        if not sep:
            raise ValueError("invalid tree entry")
        mode, kind, _oid = metadata.decode("ascii").split(" ", 2)
        path = path_raw.decode("utf-8", "strict")
        if Path(path).suffix not in SUFFIXES:
            continue
        if mode != "100644" and mode != "100755" or kind != "blob":
            raise ValueError("symlink or non-file in baseline")
        data = _git(root, "show", revision + ":" + path)
        _decode(data)
        rows[path] = data
        if len(rows) > MAX_FILES:
            raise ValueError("too many baseline workflows")
    return rows


def check(root: Path, base_sha: str) -> dict[str, object]:
    before = _read_git(root, base_sha)
    after = _read_worktree(root)
    baseline = {name for name, blob in before.items() if _scan_text(_decode(blob))}
    exposed = {name for name, blob in after.items() if _scan_text(_decode(blob))}
    introduced = sorted(
        path for path in exposed
        if path not in baseline or before[path] != after[path]
    )
    return {
        "policy": "zcloud-automatic-vps-mutation-no-new-exposure-v1",
        "base_sha": base_sha,
        "baseline_exposure_count": len(baseline),
        "head_exposure_count": len(exposed),
        "new_exposure_count": len(introduced),
        "new_exposure_paths": introduced,
        "safe_to_review": not introduced,
        "mutation_authorized": False,
        "production_verified": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-sha", required=True)
    p.add_argument("--repo-root", type=Path, default=Path("."))
    args = p.parse_args()
    try:
        result = check(args.repo_root.resolve(), args.base_sha)
    except (ValueError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"policy": "zcloud-automatic-vps-mutation-no-new-exposure-v1",
                          "safe_to_review": False, "mutation_authorized": False,
                          "production_verified": False, "error": type(exc).__name__},
                         sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["safe_to_review"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
