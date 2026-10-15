#!/usr/bin/env python3
"""Fail-closed, source-only ratchet for direct Git writes in new/edited workflows.

Advisory PR policy: no deployment, merge, runner, queue, or permission authority.
Existing workflow blobs are grandfathered only while byte-for-byte unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time

HEX = re.compile(r"^[0-9a-f]{40}$")
WORKFLOW = re.compile(r"^\.github/workflows/[^/\x00]+\.(?:yml|yaml)$")
GIT_PUSH = re.compile(r"(?i)(?<![\w-])git\s+(?:(?:-C\s+\S+|-c\s+\S+|--git-dir(?:=\S+|\s+\S+))\s+)*push\b")
REFS_API = re.compile(r"(?i)(?:git/refs/heads/|git/refs/heads\x2f|git/refs/heads\\u002f)")
GH_REF_WRITE = re.compile(r"(?i)\bgh\s+api\b.{0,200}(?:\b(?:PATCH|POST|DELETE|PUT)\b|(?:-X|--method)\s+)")
MAX_BLOB = 512 * 1024
MAX_OUTPUT = 16 * 1024 * 1024
MAX_PATHS = 2000


class AdmissionError(ValueError):
    pass


def git(*args: str) -> bytes:
    """Bound stdout during collection, kill/reap on timeout and never print stderr."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               GIT_NO_LAZY_FETCH="1")
    p = None
    selector = selectors.DefaultSelector()
    chunks: list[bytes] = []
    size = 0
    try:
        p = subprocess.Popen(["git", "-c", "core.pager=cat", *args],
                             env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, bufsize=0)
        assert p.stdout is not None
        selector.register(p.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + 15
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AdmissionError("git_timeout")
            if not selector.select(min(remaining, 0.25)):
                continue
            data = os.read(p.stdout.fileno(), min(65536, MAX_OUTPUT - size + 1))
            if not data:
                break
            size += len(data)
            if size > MAX_OUTPUT:
                raise AdmissionError("git_failed_or_oversize")
            chunks.append(data)
        remaining = max(0.01, deadline - time.monotonic())
        if p.wait(timeout=remaining) != 0:
            raise AdmissionError("git_failed_or_oversize")
        return b"".join(chunks)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdmissionError("git_unavailable") from exc
    finally:
        selector.close()
        if p is not None:
            if p.poll() is None:
                p.kill()
            p.wait()
            if p.stdout is not None:
                p.stdout.close()


def validate_commit(value: str) -> str:
    if not HEX.fullmatch(value):
        raise AdmissionError("invalid_commit_sha")
    obj_type = git("cat-file", "-t", value).strip()
    if obj_type != b"commit":
        raise AdmissionError("not_commit")
    return value


def tree(commit: str) -> dict[str, str]:
    listing = git("ls-tree", "-rz", "--full-tree", commit, "--", ".github/workflows")
    if not listing:
        raise AdmissionError("missing_workflows")
    entries = listing.split(b"\x00")
    if entries[-1] != b"":
        raise AdmissionError("corrupt_tree")
    if len(entries) > MAX_PATHS:
        raise AdmissionError("too_many_paths")
    result: dict[str, str] = {}
    for entry in entries[:-1]:
        try:
            metadata, path_bytes = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ")
            path = path_bytes.decode("utf-8", errors="strict")
        except (ValueError, UnicodeError) as exc:
            raise AdmissionError("invalid_tree_entry") from exc
        if not WORKFLOW.fullmatch(path):
            # We intentionally do not claim to validate non-workflow content.
            continue
        if mode != "100644" or kind != "blob" or not HEX.fullmatch(oid):
            raise AdmissionError("nonregular_workflow")
        if path in result:
            raise AdmissionError("duplicate_workflow_path")
        result[path] = oid
    if not result:
        raise AdmissionError("missing_workflows")
    return result


def suspicious_lines(source: str) -> list[str]:
    findings: list[str] = []
    # Conservative lexical policy. Multiline/dynamic commands are not parsed
    # as shell or YAML; see operator guide for explicit limitations.
    for index, line in enumerate(source.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if GIT_PUSH.search(line):
            findings.append("git_push")
        if REFS_API.search(line) and (
            re.search(r"(?i)\b(?:gh\s+api|curl|wget|PATCH|POST|DELETE|PUT)\b", line)
            or "api.github.com" in line.lower()
        ):
            findings.append("git_refs_api")
        if re.search(r"(?i)\bgh\s+api\b", line) and GH_REF_WRITE.search(line):
            # Dynamic gh API writes are unsafe when target cannot be proven.
            findings.append("gh_api_write")
        if len(findings) >= 15:
            break
    return sorted(set(findings))


def audit(base: str, head: str) -> dict[str, object]:
    validate_commit(base)
    validate_commit(head)
    base_tree = tree(base)
    head_tree = tree(head)
    changed = sorted(p for p in head_tree if base_tree.get(p) != head_tree[p])
    if len(changed) > MAX_PATHS:
        raise AdmissionError("too_many_changes")
    flagged: list[dict[str, object]] = []
    for path in changed:
        # Check immutable object size before requesting any potentially large bytes.
        oid = head_tree[path]
        raw_size = git("cat-file", "-s", oid).strip()
        if not raw_size.isdigit() or int(raw_size) > MAX_BLOB:
            raise AdmissionError("invalid_workflow_blob")
        blob = git("cat-file", "blob", oid)
        if len(blob) != int(raw_size) or b"\x00" in blob:
            raise AdmissionError("invalid_workflow_blob")
        try:
            content = blob.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise AdmissionError("invalid_workflow_encoding") from exc
        if any(ord(ch) in (0x2028, 0x2029) for ch in content):
            raise AdmissionError("opaque_workflow_encoding")
        reasons = suspicious_lines(content)
        if reasons:
            flagged.append({"path": path, "reasons": reasons})
    return {
        "status": "deny" if flagged else "pass",
        "base": base, "head": head,
        "workflows_in_head": len(head_tree),
        "changed_workflows_checked": len(changed),
        "legacy_workflows_not_rescanned": len(head_tree) - len(changed),
        "findings": flagged,
        "source_only": True, "authorizes_merge": False,
        "authorizes_deploy": False, "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        result = audit(args.base, args.head)
    except AdmissionError as exc:
        result = {"status": "deny", "reason": str(exc), "source_only": True,
                  "authorizes_merge": False, "authorizes_deploy": False,
                  "mutation_performed": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
