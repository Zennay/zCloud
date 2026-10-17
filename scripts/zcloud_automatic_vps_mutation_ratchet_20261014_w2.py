#!/usr/bin/env python3
"""Monotonic, read-only screen for automatically triggered workflows with possible VPS access.

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
TRIGGERS = (
    "push", "pull_request", "pull_request_target", "workflow_call",
    "workflow_run", "schedule", "repository_dispatch", "release",
    "issue_comment", "check_run", "check_suite",
)
AUTO_BLOCK = re.compile(
    r"(?m)^[ ]{2}(" + "|".join(TRIGGERS) + r"):[ \t]*"
)
ON_INLINE = re.compile(r"(?m)^[ \t]*(?:on|\"on\"|'on'):[ \t]*(?P<value>[^\n]*)$")
RUNS_ON = re.compile(r"(?m)^[ \t]+runs-on:[ \t]*(?P<value>[^\n#]*)")
JOB_USES = re.compile(r"(?m)^ {4}uses:[ \t]*[^\s#]+")
HOSTED = re.compile(
    r"(?:ubuntu|windows|macos)-(?:latest|[0-9]+(?:\.[0-9]+)?(?:-arm|-large|-xlarge)?)",
    re.IGNORECASE,
)
SHA = re.compile(r"[0-9a-f]{40}\Z")


def _automatic(text: str) -> bool:
    if AUTO_BLOCK.search(text):
        return True
    for match in ON_INLINE.finditer(text):
        value = match.group("value").strip()
        if value.startswith(("*", "${{")):
            return True
        if any(re.search(r"(?<![A-Za-z0-9_-])" + name + r"(?![A-Za-z0-9_-])", value)
               for name in TRIGGERS):
            return True
    return False


def _possible_vps(text: str) -> bool:
    # Job-level reusable workflows may hide self-hosted jobs behind uses:.
    # Step-level uses: is not a job-level dispatch.
    if JOB_USES.search(text):
        return True
    for match in RUNS_ON.finditer(text):
        raw = match.group("value").strip().strip(chr(34) + chr(39))
        if HOSTED.fullmatch(raw):
            continue
        if raw.startswith("[") and raw.endswith("]"):
            labels = [part.strip().strip(chr(34) + chr(39)) for part in raw[1:-1].split(",")]
            if labels and all(HOSTED.fullmatch(label) for label in labels):
                continue
        # A name, alias, expression, multiline array or group is not proof
        # of a hosted runner. Even if the job invokes an innocent script,
        # it may have write access to VPS runtime state.
        return True
    return False


def _scan_text(text: str) -> bool:
    # Text-only detection intentionally overapproximates: it is a review gate,
    # never proof that a workflow is safe.
    return _automatic(text) and _possible_vps(text)


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
