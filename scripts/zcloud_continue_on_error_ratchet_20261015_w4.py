#!/usr/bin/env python3
"""Deny newly introduced fail-open GitHub Actions continue-on-error settings.

Source-only, read-only; this is not deployment, review, or runner authorization.
Only byte-identical legacy workflow blobs are grandfathered.
"""
from __future__ import annotations

import argparse
import os
import re
import selectors
import subprocess
import time
import unicodedata
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
GIT_DEADLINE_SECONDS = 15
SAFE_DENIAL_CODES = frozenset({
    "invalid_git_limit", "git_output_limit", "invalid_sha",
    "checkout_head_changed", "not_a_commit", "unsafe_workflow_root",
    "incomplete_or_duplicate_inventory", "unsafe_workflow_path",
    "unsafe_workflow_mode", "workflow_blob_too_large", "nul_in_workflow",
    "invisible_or_directional_unicode",
})


def git(root: Path, *args: str, limit: int = MAX_WORKFLOW) -> bytes:
    """Drain a subprocess with a hard elapsed deadline AND bounded stdout.

    subprocess.run(capture_output=True) buffers arbitrary stdout before a
    post-hoc size check. Git stderr is discarded to avoid credential leakage.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("invalid_git_limit")
    command = ["git", "-C", str(root), *args]
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    )
    output = bytearray()
    deadline = time.monotonic() + GIT_DEADLINE_SECONDS
    try:
        assert proc.stdout is not None
        with selectors.DefaultSelector() as reader:
            reader.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("git_elapsed_deadline")
                ready = reader.select(remaining)
                if not ready:
                    raise TimeoutError("git_elapsed_deadline")
                chunk = os.read(proc.stdout.fileno(), min(65536, limit + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    raise ValueError("git_output_limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("git_elapsed_deadline")
        status = proc.wait(timeout=remaining)
        if status:
            raise subprocess.CalledProcessError(status, command)
        return bytes(output)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        raise
    finally:
        if proc.stdout is not None:
            proc.stdout.close()


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
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    )
    # Replacing the workflow directory with a symlink can otherwise evade
    # the `.github/workflows/` pathspec entirely.
    root_mode = git(root, "ls-tree", head, "--", ".github/workflows", limit=512)
    if not root_mode.startswith(b"040000 tree "):
        raise ValueError("unsafe_workflow_root")
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
    run_block_header: int | None = None
    run_block_content: int | None = None
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        prefix = line[:len(line) - len(line.lstrip(" " + chr(9)))]
        if chr(9) in prefix:
            issues.append(f"line_{number}:tab_indent_unreviewed")
            continue
        if run_block_header is not None:
            # YAML infers a block scalar's indentation from the FIRST nonempty
            # body line, not from the 'run: |' header. A mapping sibling can
            # indent farther than the header but less than the literal body.
            if not stripped:
                continue
            if run_block_content is None:
                if indent > run_block_header:
                    run_block_content = indent
                    continue
            elif indent >= run_block_content:
                continue
            run_block_header = None
            run_block_content = None
        if stripped.startswith("#"):
            continue
        # Only plain | and > with an optional chomping indicator are supported.
        # Explicit block indentation indicators remain fail-closed.
        if re.fullmatch(r"(?:- +)?run:[ ]*[|>]-?(?:[ ]+#.*)?", stripped):
            run_block_header = indent
            run_block_content = None
            continue
        # Merges/aliases can hide a fail-open key from textual detection.
        if re.match(r"^[ ]*\?[ ]*['\"]?continue-on-error", line, re.I):
            issues.append(f"line_{number}:explicit_yaml_key_unreviewed")
        # YAML quoted scalar keys can decode escaped characters before mapping.
        # Do not let \u0063ontinue-on-error silently masquerade as another key.
        if re.search(r'''(?:^|[,{])[ ]*"[^"\n]*\\(?:u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2})[^"\n]*"[ ]*:''', line):
            issues.append(f"line_{number}:encoded_yaml_key_unreviewed")
        if re.search(r"(^|[ {,])<<[ \t]*:", line):
            issues.append(f"line_{number}:yaml_merge_unreviewed")
        # Display labels are never executable continue-on-error mappings;
        # check this *before* searching their quoted text for policy syntax.
        if re.match(r"^[ ]*(?:-[ ]+)?name[ ]*:", line):
            continue
        match = KEY.search(line)
        if match:
            value = line[match.end():].strip()
            # Only a direct YAML false scalar (with optional comment) is safe.
            if not re.fullmatch(r"false(?:[ \t]+#.*)?", value):
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
        blob_size = int(git(root, "cat-file", "-s", f"{head}:{path}", limit=32).strip())
        if blob_size > MAX_WORKFLOW:
            raise ValueError("workflow_blob_too_large")
        raw = git(root, "show", f"{head}:{path}", limit=MAX_WORKFLOW)
        if b"\0" in raw:
            raise ValueError("nul_in_workflow")
        source = raw.decode("utf-8", "strict")
        # Bidirectional/zero-width format marks can spoof apparently identical
        # YAML keys. U+2028/U+2029 alter Python line splitting semantics.
        if any(unicodedata.category(ch) == "Cf" or ord(ch) in (0x2028, 0x2029) for ch in source):
            raise ValueError("invisible_or_directional_unicode")
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
        # Only codes in our own constant allowlist may be emitted. Never leak
        # arbitrary exception text, Git stderr, checked-out content or tokens.
        if type(exc) is ValueError and str(exc) in SAFE_DENIAL_CODES:
            code = str(exc)
        elif isinstance(exc, TimeoutError):
            code = "git_elapsed_deadline"
        elif isinstance(exc, UnicodeError):
            code = "invalid_workflow_utf8"
        elif isinstance(exc, subprocess.TimeoutExpired):
            code = "git_subprocess_deadline"
        elif isinstance(exc, subprocess.CalledProcessError):
            code = "git_command_failed"
        else:
            code = "untrusted_source_io"
        print(f"DENY: source inventory could not be trusted [{code}]", file=sys.stderr)
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
