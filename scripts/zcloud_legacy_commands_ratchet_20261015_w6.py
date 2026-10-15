#!/usr/bin/env python3
"""No-new-debt, source-only detector for deprecated GitHub Actions commands.

This PR-controlled check is *not* independent merge, release, or VPS authorization.
Historical unchanged workflow blobs are grandfathered, not certified safe.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from pathlib import PurePosixPath

SHA = re.compile(r"^[0-9a-f]{40}$")
COMMANDS = re.compile(r"::\\s*(?:set-env|add-path|set-output|save-state)\\b", re.I)
ENV = re.compile(r"actions_allow_unsecure_commands", re.I)
MAX_CHANGED = 250
MAX_BLOB = 512 * 1024
MAX_OUTPUT = 1024 * 1024
TIMEOUT = 15


class Denied(ValueError):
    """A source proof could not be established safely."""


def git(*args: str, max_output: int = MAX_OUTPUT) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "--no-replace-objects", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Denied("Git source proof unavailable") from exc
    if proc.returncode != 0 or len(proc.stdout) > max_output:
        raise Denied("Git source proof invalid or too large")
    return proc.stdout


def require_sha(value: str) -> str:
    if not SHA.fullmatch(value):
        raise Denied("Expected immutable 40-character lowercase Git SHA")
    if git("cat-file", "-t", value).strip() != b"commit":
        raise Denied("SHA is not a commit")
    return value


def entry(commit: str, path: str) -> tuple[str, str] | None:
    raw = git("ls-tree", "-z", commit, "--", path)
    if not raw:
        return None
    records = [r for r in raw.split(b"\\0") if r]
    if len(records) != 1:
        raise Denied("Ambiguous tree path")
    try:
        metadata, name = records[0].split(b"\\t", 1)
        mode, kind, _oid = metadata.decode("ascii").split()
        if name.decode("utf-8") != path:
            raise Denied("Unexpected Git tree path")
    except (UnicodeDecodeError, ValueError) as exc:
        raise Denied("Malformed Git tree entry") from exc
    return mode, kind


def proper_file(commit: str, path: str) -> bool:
    for prefix in (".github", ".github/workflows" if path.startswith(".github/workflows/") else ".github/actions"):
        parent = entry(commit, prefix)
        if parent != ("040000", "tree"):
            raise Denied("Missing or unsafe workflow ancestry")
    parts = PurePosixPath(path).parts
    for end in range(3, len(parts)):
        parent = entry(commit, "/".join(parts[:end]))
        if parent != ("040000", "tree"):
            raise Denied("Workflow path contains a non-directory")
    node = entry(commit, path)
    if node is None:
        return False
    if node != ("100644", "blob") and node != ("100755", "blob"):
        raise Denied("Workflow is not a regular Git blob")
    return True


def candidate(path: str) -> bool:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or "\\x00" in path:
        raise Denied("Invalid workflow pathname")
    if len(p.parts) == 3 and p.parts[:2] == (".github", "workflows"):
        return p.suffix in (".yml", ".yaml")
    if len(p.parts) >= 4 and p.parts[:2] == (".github", "actions"):
        return p.name in ("action.yml", "action.yaml")
    return False


def check_content(content: bytes) -> None:
    if len(content) > MAX_BLOB:
        raise Denied("Workflow blob exceeds size limit")
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Denied("Workflow blob is not UTF-8") from exc
    if any(unicodedata.category(ch) == "Cf" for ch in source):
        raise Denied("Unicode format controls in workflow source")
    if ENV.search(source) or COMMANDS.search(source):
        raise Denied("New or changed workflow uses legacy unsafe command syntax")


def scan(base: str, head: str) -> tuple[int, int]:
    base, head = require_sha(base), require_sha(head)
    # Deny disconnected/unrelated bases, including stale unrelated source trees.
    try:
        git("merge-base", "--is-ancestor", base, head)
    except Denied as exc:
        raise Denied("Base is not an ancestor of HEAD") from exc
    for root in (".github", ".github/workflows"):
        if entry(head, root) != ("040000", "tree"):
            raise Denied("Required workflow root missing or unsafe")
    actions = entry(head, ".github/actions")
    if actions is not None and actions != ("040000", "tree"):
        raise Denied("Composite action root is not a Git tree")
    raw = git("diff", "--no-ext-diff", "--no-renames", "--name-only", "-z",
              "--diff-filter=ACMRT", base, head, "--", ".github/workflows", ".github/actions")
    try:
        changed = [p.decode("utf-8") for p in raw.split(b"\\0") if p]
    except UnicodeDecodeError as exc:
        raise Denied("Non-UTF8 Git changed path") from exc
    if len(changed) > MAX_CHANGED:
        raise Denied("Too many changed workflow paths")
    checked = 0
    for path in changed:
        if not candidate(path):
            continue
        if not proper_file(head, path):
            continue  # A deleted file cannot introduce a new unsafe command.
        size = git("cat-file", "-s", f"{head}:{path}")
        try:
            nbytes = int(size)
        except ValueError as exc:
            raise Denied("Malformed Git blob size") from exc
        if nbytes > MAX_BLOB:
            raise Denied("Workflow blob exceeds size limit")
        content = git("show", f"{head}:{path}", max_output=MAX_BLOB)
        check_content(content)
        checked += 1
    return len(changed), checked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    args = ap.parse_args()
    try:
        changed, checked = scan(args.base, args.head)
    except Denied as exc:
        print(f"DENY: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: {changed} changed workflow/action paths; {checked} new or edited YAML files checked. "
          "Unchanged legacy unreviewed; authorizes_merge=false; authorizes_deploy=false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
