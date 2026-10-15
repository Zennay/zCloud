#!/usr/bin/env python3
"""Deny newly introduced fail-open GitHub Actions continue-on-error settings.

Source-only, read-only; this is not deployment, review, or runner authorization.
Only byte-identical legacy workflow blobs are grandfathered.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
# Recognise plain and quoted keys (including inline mapping keys).
KEY = re.compile(r"""(?<![\w-])(?:continue-on-error|"continue-on-error"|'continue-on-error')[ \t]*:""", re.I)
ROOT = Path(__file__).resolve().parents[1]
MAX_CHANGED = 250
MAX_INVENTORY = 128 * 1024
MAX_WORKFLOW = 1024 * 1024


def git(root: Path, *args: str, limit: int = MAX_WORKFLOW) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
        timeout=20,
    )
    if len(result.stdout) > limit:
        raise ValueError("git_output_limit")
    return result.stdout


def changed_workflows(root: Path, base: str, head: str) -> list[str]:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise ValueError("invalid_sha")
    if git(root, "rev-parse", "HEAD", limit=64).decode().strip() != head:
        raise ValueError("checkout_head_changed")
    for sha in (base, head):
        if git(root, "cat-file", "-t", sha, limit=64).strip() != b"commit":
            raise ValueError("not_a_commit")
    subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", base, head],
        check=True, capture_output=True, timeout=10,
    )
    raw = git(
        root, "diff", "--name-only", "-z", "--diff-filter=ACMRT",
        f"{base}...{head}", "--", ".github/workflows/",
        limit=MAX_INVENTORY,
    )
    paths = [p.decode("utf-8", "strict") for p in raw.split(b"\0") if p]
    if len(paths) > MAX_CHANGED or len(set(paths)) != len(paths):
        raise ValueError("incomplete_or_duplicate_inventory")
    if any(not WORKFLOW.fullmatch(path) for path in paths):
        raise ValueError("unsafe_workflow_path")
    return paths


def check_workflow(source: str) -> list[str]:
    issues = []
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Merges/aliases can hide a fail-open key from textual detection.
        if re.search(r"(^|[ {,])<<[ \t]*:", line):
            issues.append(f"line_{number}:yaml_merge_unreviewed")
        match = KEY.search(line)
        if match:
            value = line[match.end():].strip()
            # Only a direct YAML false scalar (with optional comment) is safe.
            if not re.fullmatch(r"false(?:[ \t]*#.*)?", value):
                issues.append(f"line_{number}:continue_on_error_not_literal_false")
        elif ":" in line and re.search(r"continue.{0,70}error", line, re.I):
            # Includes encoded YAML key or shadowed/malformed mapping forms.
            issues.append(f"line_{number}:ambiguous_continue_on_error")
    return issues


def scan(root: Path, base: str, head: str) -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {}
    for path in changed_workflows(root, base, head):
        mode_data = git(root, "ls-tree", head, "--", path, limit=512)
        if not mode_data.startswith((b"100644 blob ", b"100755 blob ")):
            raise ValueError("unsafe_workflow_mode")
        raw = git(root, "show", f"{head}:{path}", limit=MAX_WORKFLOW)
        if b"\0" in raw:
            raise ValueError("nul_in_workflow")
        source = raw.decode("utf-8", "strict")
        problems = check_workflow(source)
        if problems:
            findings[path] = problems
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        findings = scan(args.root, args.base, args.head)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
        # Never print arbitrary git stderr, file contents, or secret-bearing payloads.
        print(f"DENY: source inventory could not be trusted ({type(exc).__name__})", file=sys.stderr)
        return 2
    for path, reasons in sorted(findings.items()):
        for reason in reasons[:20]:
            print(f"DENY {path}: {reason}", file=sys.stderr)
    if findings:
        return 1
    print("PASS: changed workflows do not introduce fail-open continue-on-error settings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
