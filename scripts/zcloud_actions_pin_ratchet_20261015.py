#!/usr/bin/env python3
"""Fail-closed, read-only GitHub Actions immutable action-reference debt ratchet.

Compares exact Git trees, never the mutable working tree. It reports additions of
floating action references without requiring unrelated historic debt to be fixed.
This is an advisory source gate, never production/release authorization.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SHA = re.compile(r"[0-9a-f]{40}\Z")
IMMUTABLE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-fA-F]{40}\Z")
REMOTE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@.+\Z")
USES = re.compile(r"^\s*(?:-\s+)?uses\s*:\s*(.*?)\s*$")
BLOCK = re.compile(r"^(\s*)(?:-\s+)?run\s*:\s*[>|][-+0-9]*\s*(?:#.*)?$")
MAX_WORKFLOWS = 512
MAX_FILE = 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_REFS_PER_FILE = 256


class Refusal(Exception):
    """Incomplete, ambiguous or unsafe source inventory."""


def git(root: Path, *args: str) -> bytes:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True,
            timeout=25, env=env,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise Refusal("git_source_unavailable") from exc
    if len(result.stdout) > MAX_TOTAL + 1024 * 1024:
        raise Refusal("git_response_oversize")
    return result.stdout


def workflow_tree(root: Path, revision: str) -> dict[str, str]:
    if not SHA.fullmatch(revision):
        raise Refusal("invalid_revision")
    if git(root, "cat-file", "-t", revision).strip() != b"commit":
        raise Refusal("revision_not_commit")
    for tree_part in (".github", ".github/workflows"):
        if git(root, "cat-file", "-t", revision + ":" + tree_part).strip() != b"tree":
            raise Refusal("workflow_root_not_directory")
    # Composite Actions may themselves invoke floating third-party Actions.
    # Include only their GitHub-recognised action.yml/action.yaml metadata;
    # refuse symlinked action directories so the scan cannot miss their steps.
    raw = git(root, "ls-tree", "-rz", "--full-tree", revision,
              ".github/workflows", ".github/actions")
    paths: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, filename = entry.split(b"\t", 1)
            mode, kind, blob = metadata.decode("ascii").split()
            path = filename.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise Refusal("invalid_tree_entry") from exc
        is_workflow = (path.startswith(".github/workflows/") and
                       path.endswith((".yml", ".yaml")))
        is_action_source = path.startswith(".github/actions/")
        is_action_meta = (is_action_source and
                          path.rsplit("/", 1)[-1] in ("action.yml", "action.yaml"))
        if path == ".github/actions" or (
            is_action_source and (mode == "120000" or kind != "blob")
        ):
            raise Refusal("nonregular_composite_action_source")
        if not is_workflow and not is_action_meta:
            continue
        if is_workflow and "/" in path[len(".github/workflows/"):]:
            raise Refusal("unexpected_workflow_path")
        if ("\n" in path or "\r" in path or "\0" in path or
            ".." in path.split("/") or
            (is_action_meta and len(path.split("/")) < 4)):
            raise Refusal("unexpected_workflow_path")
        if mode != "100644" or kind != "blob" or not SHA.fullmatch(blob):
            raise Refusal("nonregular_workflow")
        if path in paths:
            raise Refusal("duplicate_workflow")
        paths[path] = blob
        if len(paths) > MAX_WORKFLOWS:
            raise Refusal("workflow_count_limit")
    return paths


def refs(text: str, path: str) -> list[tuple[tuple[str, str], int]]:
    result: list[tuple[tuple[str, str], int]] = []
    block_indent: int | None = None
    for lineno, line in enumerate(text.splitlines(), 1):
        trimmed = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if not trimmed or indent > block_indent:
                continue
            block_indent = None
        if not trimmed or trimmed.startswith("#"):
            continue
        if "\t" in line[:len(line) - len(line.lstrip())]:
            raise Refusal("ambiguous_yaml_indent")
        match_block = BLOCK.match(line)
        if match_block:
            block_indent = len(match_block.group(1))
            continue
        # GitHub Actions YAML anchors/aliases may duplicate complete steps
        # without adding another literal uses: line. Refuse ambiguous reuse,
        # including flow-map merges and aliases in flow sequences.
        if re.search(r"(?<![A-Za-z0-9_])<<\s*:", line):
            raise Refusal("yaml_merge_key")
        if re.search(r"(?<![A-Za-z0-9_])\*[A-Za-z_][A-Za-z0-9_-]*\b", line):
            raise Refusal("yaml_alias")
        if re.search(r'''(?<![A-Za-z0-9_])['"]?uses['"]?\s*:''', line) and not USES.match(line):
            raise Refusal("ambiguous_uses_syntax")
        match = USES.match(line)
        if not match:
            continue
        raw = match.group(1).split(" #", 1)[0].strip()
        if len(raw) > 512:
            raise Refusal("action_ref_oversize")
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        if raw.startswith("./"):
            if raw.startswith("../") or ".." in raw.split("/") or not re.fullmatch(r"\./[A-Za-z0-9_./-]+", raw):
                raise Refusal("invalid_local_action")
            continue
        if IMMUTABLE.fullmatch(raw) or re.fullmatch(r"docker://[^ ]+@sha256:[0-9a-fA-F]{64}", raw):
            continue
        # Unknown/dynamic syntax is debt, not implicit permission to run it.
        kind = "floating" if REMOTE.fullmatch(raw) or raw.startswith("docker://") else "dynamic_or_invalid"
        # Hash reference instead of logging attacker-controlled action syntax.
        identity = hashlib.sha256(raw.encode("utf-8", "strict")).hexdigest()
        result.append(((kind, identity), lineno))
        if len(result) > MAX_REFS_PER_FILE:
            raise Refusal("action_count_limit")
    return result


def snapshot(root: Path, revision: str) -> dict[str, list[tuple[tuple[str, str], int]]]:
    tree = workflow_tree(root, revision)
    total = 0
    output = {}
    for path, blob in sorted(tree.items()):
        raw = git(root, "cat-file", "blob", blob)
        total += len(raw)
        if len(raw) > MAX_FILE or total > MAX_TOTAL:
            raise Refusal("workflow_bytes_limit")
        try:
            text = raw.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise Refusal("workflow_non_utf8") from exc
        output[path] = refs(text, path)
    return output


def compare(base: dict, head: dict) -> dict:
    base_counts = Counter((path, *identity) for path, rows in base.items()
                          for identity, _ in rows)
    head_counts = Counter((path, *identity) for path, rows in head.items()
                          for identity, _ in rows)
    added_counts = head_counts - base_counts
    remaining = added_counts.copy()
    examples = []
    for path in sorted(head):
        for (kind, fingerprint), line in head[path]:
            key = (path, kind, fingerprint)
            if remaining[key] > 0:
                if len(examples) < 50:
                    examples.append({"path": path, "line": line, "status": kind,
                                     "ref_fingerprint": fingerprint[:16]})
                remaining[key] -= 1
    count = sum(added_counts.values())
    return {
        "policy": "zcloud-actions-pin-no-new-debt-v1",
        "base_unpinned": sum(base_counts.values()),
        "head_unpinned": sum(head_counts.values()),
        "new_unpinned": count,
        "examples": examples,
        "examples_truncated": count > len(examples),
        "release_authorized": False,
        "deployment_authorized": False,
        "mutation_performed": False,
        "ok": count == 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        root = args.repo_root.resolve(strict=True)
        result = compare(snapshot(root, args.base), snapshot(root, args.head))
    except (Refusal, OSError, UnicodeError, ValueError) as exc:
        result = {
            "policy": "zcloud-actions-pin-no-new-debt-v1",
            "ok": False,
            "error": str(exc) if isinstance(exc, Refusal) else "invalid_source",
            "release_authorized": False,
            "deployment_authorized": False,
            "mutation_performed": False,
        }
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
