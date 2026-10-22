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


def workflow_tree(root: Path, revision: str, *, untrusted_roots: set[str] | None = None) -> dict[str, str]:
    if not SHA.fullmatch(revision):
        raise Refusal("invalid_revision")
    if git(root, "cat-file", "-t", revision).strip() != b"commit":
        raise Refusal("revision_not_commit")
    for tree_part in (".github", ".github/workflows"):
        if git(root, "cat-file", "-t", revision + ":" + tree_part).strip() != b"tree":
            raise Refusal("workflow_root_not_directory")
    # A local composite Action can live anywhere in the repository, not
    # just under .github/actions. Enumerate the entire immutable Git tree,
    # but read only workflow files and recognised action.yml/action.yaml
    # metadata. Refuse symlinked .github/actions sources and metadata blobs.
    raw = git(root, "ls-tree", "-rz", "--full-tree", revision)
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
        # Both symlinks and Git submodules (gitlinks) can redirect a
        # local Action to a source outside the checked immutable tree.
        if untrusted_roots is not None and mode in ("120000", "160000"):
            untrusted_roots.add(path)
        is_workflow = (path.startswith(".github/workflows/") and
                       path.endswith((".yml", ".yaml")))
        is_action_source = path.startswith(".github/actions/")
        is_action_meta = path.rsplit("/", 1)[-1] in ("action.yml", "action.yaml")
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
            (is_action_meta and path.startswith(".github/actions/") and
             len(path.split("/")) < 4)):
            raise Refusal("unexpected_workflow_path")
        if mode != "100644" or kind != "blob" or not SHA.fullmatch(blob):
            raise Refusal("nonregular_workflow")
        if path in paths:
            raise Refusal("duplicate_workflow")
        paths[path] = blob
        if len(paths) > MAX_WORKFLOWS:
            raise Refusal("workflow_count_limit")
    return paths


def refs(text: str, path: str, *, local_refs: list[str] | None = None) -> list[tuple[tuple[str, str], int]]:
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
        # An empty or YAML folded/literal scalar is not a complete source
        # reference. Comparing just '>' would mask changes to indented
        # external action values across commits.
        if not raw or re.fullmatch(r"[>|][-+0-9]*", raw):
            raise Refusal("ambiguous_uses_value")
        if raw.startswith("./"):
            # Refuse path-equivalent spellings before comparing against
            # immutable Git-tree symlink ancestry; ././x and //x otherwise
            # bypass the literal symlink-prefix proof.
            if not re.fullmatch(r"\./[A-Za-z0-9_./-]+", raw):
                raise Refusal("invalid_local_action")
            segments = raw[2:].split("/")
            if any(part in ("", ".", "..") for part in segments):
                raise Refusal("ambiguous_local_action_path")
            if local_refs is not None:
                local_refs.append("/".join(segments))
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
    untrusted_roots: set[str] = set()
    tree = workflow_tree(root, revision, untrusted_roots=untrusted_roots)
    total = 0
    local_refs: list[str] = []
    output = {}
    for path, blob in sorted(tree.items()):
        # Git cat-file blob buffers stdout. Check the immutable object's
        # reported byte size *before* loading it into memory. Reject both
        # per-file and aggregate oversize inputs without fetching bytes.
        size_raw = git(root, "cat-file", "-s", blob).strip()
        if not size_raw.isdigit() or len(size_raw) > 20:
            raise Refusal("invalid_blob_size")
        blob_size = int(size_raw)
        if blob_size > MAX_FILE or total + blob_size > MAX_TOTAL:
            raise Refusal("workflow_bytes_limit")
        raw = git(root, "cat-file", "blob", blob)
        if len(raw) != blob_size:
            raise Refusal("git_blob_size_mismatch")
        total += blob_size
        try:
            text = raw.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise Refusal("workflow_non_utf8") from exc
        output[path] = refs(text, path, local_refs=local_refs)
    # Git symlinks and submodule gitlinks can redirect ./local-action
    # references outside the exact reviewed source tree. Refuse only a
    # referenced link or its ancestor; unrelated links remain allowed.
    for local in local_refs:
        if any(local == link or local.startswith(link + "/") for link in untrusted_roots):
            raise Refusal("nonregular_local_action_source")
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
