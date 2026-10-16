#!/usr/bin/env python3
"""Read-only, fail-closed no-new-debt tripwire for cross-run artifact downloads.

This is a conservative source check, not a YAML interpreter or an authorization
for consuming untrusted artifacts. Existing identical legacy steps are recorded
without treating them as safe. No GitHub API write, runner, or deployment access.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import re
import subprocess
from pathlib import Path

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_USES = re.compile(r"^(?P<indent> *)-?\s*uses:\s*['\"]?actions/download-artifact@(?P<ref>[^\s'\"#]+)['\"]?\s*(?:#.*)?$")
_INPUT = re.compile(r"^(?P<indent> *)(?P<key>[\w-]+):\s*(?P<value>.*?)\s*$")
_TARGETS = frozenset({"run-id", "repository", "github-token"})
_MAX_FILE = 256_000
_MAX_WORKFLOWS = 500

class Refusal(ValueError):
    """Source cannot safely be classified."""


def _git(root: Path, *args: str, max_bytes: int = 2_000_000) -> bytes:
    try:
        p = subprocess.run(["git", "-C", str(root), *args],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refusal("git_command_unavailable") from exc
    if p.returncode or len(p.stdout) > max_bytes:
        raise Refusal("git_command_failed_or_oversized")
    return p.stdout


def _inventory(root: Path, sha: str) -> dict[str, str]:
    data = _git(root, "ls-tree", "-rz", sha, "--", ".github/workflows", max_bytes=400_000)
    result: dict[str, str] = {}
    for item in data.split(b"\x00"):
        if not item:
            continue
        try:
            meta, raw_path = item.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split()
            path = raw_path.decode("utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise Refusal("invalid_git_tree_entry") from exc
        if not path.startswith(".github/workflows/"):
            raise Refusal("unsafe_workflow_path")
        if not path.endswith((".yml", ".yaml")):
            continue
        if mode not in {"100644", "100664"} or kind != "blob" or not _SHA.fullmatch(oid):
            raise Refusal("non_regular_workflow")
        if path in result:
            raise Refusal("duplicate_workflow_path")
        result[path] = oid
        if len(result) > _MAX_WORKFLOWS:
            raise Refusal("workflow_inventory_too_large")
    if not result:
        raise Refusal("no_workflows")
    return result


def _risks(text: str, path: str) -> Counter[str]:
    lines = text.splitlines()
    risky: Counter[str] = Counter()
    for index, line in enumerate(lines):
        # Reject indented tabs/ambiguous aliases where a download action is mentioned.
        if "actions/download-artifact@" in line and ("\t" in line[:len(line)-len(line.lstrip())] or "*" in line.strip().split("#")[0]):
            raise Refusal("ambiguous_download_action")
        match = _USES.fullmatch(line)
        if not match:
            # Reject a syntactically unrecognized action reference, rather than
            # silently approving an additional renamed/quoted action invocation.
            if re.search(r"\bactions/download-artifact@", line) and not line.lstrip().startswith("#"):
                # Block scalar run scripts may quote the action as inert data;
                # conservatively require human review instead of false approval.
                raise Refusal("unparsed_download_action")
            continue
        indent = len(match.group("indent"))
        # YAML mapping order is not fixed. A preceding 'with:' can supply
        # privileged cross-run inputs even when 'uses:' appears last. Refuse
        # this shape rather than silently treating it as a safe download.
        for preceding in reversed(lines[:index]):
            stripped_pre = preceding.strip()
            if not stripped_pre or stripped_pre.startswith("#"):
                continue
            level_pre = len(preceding) - len(preceding.lstrip(" "))
            if level_pre < indent - 2:
                break
            if stripped_pre.startswith("- ") and level_pre <= indent:
                if stripped_pre.startswith("- with:"):
                    raise Refusal("preceding_artifact_with_block")
                break
            if stripped_pre == "with:" and level_pre == indent:
                raise Refusal("preceding_artifact_with_block")
        with_indent: int | None = None
        fields: dict[str, str] = {}
        for following in lines[index+1:]:
            stripped = following.strip()
            if not stripped or stripped.startswith("#"):
                continue
            level = len(following) - len(following.lstrip(" "))
            if level < indent or (level == indent and stripped.startswith("- ")):
                break
            if with_indent is None:
                if stripped == "with:":
                    with_indent = level
                elif stripped.startswith("with:"):
                    raise Refusal("ambiguous_with_block")
            elif level > with_indent:
                field = _INPUT.fullmatch(following)
                if field is None or field.group("key") in fields:
                    raise Refusal("ambiguous_artifact_input")
                fields[field.group("key")] = field.group("value").split(" #",1)[0].strip()
            elif stripped == "with:":
                raise Refusal("duplicate_with_block")
            else:
                with_indent = None
        if _TARGETS.intersection(fields):
            # The raw step and source location are deliberately bound together.
            # An identical historical step may remain, but moving/copying it or
            # changing its trust-sensitive inputs requires independent review.
            raw = "\n".join(lines[index:index+1]) + "\n" + "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
            key = path + ":" + sha256(raw.encode("utf-8")).hexdigest()
            risky[key] += 1
    return risky


def _snapshot(root: Path, sha: str) -> Counter[str]:
    risk: Counter[str] = Counter()
    for path, oid in _inventory(root, sha).items():
        content = _git(root, "cat-file", "blob", oid, max_bytes=_MAX_FILE+1)
        if len(content) > _MAX_FILE:
            raise Refusal("oversized_workflow")
        try:
            risk.update(_risks(content.decode("utf-8"), path))
        except UnicodeDecodeError as exc:
            raise Refusal("invalid_utf8_workflow") from exc
    return risk


def evaluate(root: Path, base: str, head: str) -> dict[str, object]:
    if not _SHA.fullmatch(base) or not _SHA.fullmatch(head) or base == head:
        raise Refusal("invalid_or_identical_sha")
    if _git(root, "rev-parse", "HEAD").decode().strip() != head:
        raise Refusal("candidate_head_mismatch")
    if _git(root, "merge-base", base, head).decode().strip() != base:
        raise Refusal("base_not_ancestor")
    before, after = _snapshot(root, base), _snapshot(root, head)
    new = sum(max(0, count-before[key]) for key, count in after.items())
    return {
        "policy": "cross-run-artifact-download-no-new-debt-v1",
        "base": base, "head": head,
        "legacy_findings": sum(before.values()),
        "candidate_findings": sum(after.values()),
        "new_findings": new,
        "ok": new == 0,
        "mutation_performed": False,
        "production_mutation_authorized": False,
        "merge_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        report = evaluate(args.repo_root.resolve(), args.base, args.head)
    except (Refusal, OSError) as exc:
        print(json.dumps({"ok": False, "reason": str(exc) if isinstance(exc, Refusal) else "filesystem_error",
                          "mutation_performed": False, "merge_authorized": False,
                          "production_mutation_authorized": False}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
