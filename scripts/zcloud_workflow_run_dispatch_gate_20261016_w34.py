#!/usr/bin/env python3
"""No-new-debt gate for workflow_run-triggered recursive dispatch.

Source-only, read-only, and deliberately not a release/runner authority.
Existing exact source debt is inventoried, not certified safe.
"""
from __future__ import annotations

import argparse
from collections import Counter
import os
from pathlib import Path
import re
import subprocess
import sys

REPO = "Zennay/zCloud"
ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
ON = re.compile(r"(?m)^(?:on|'on'|\"on\"):[ \t]*(.*)$")
RUN = re.compile(r"(?m)^( {6,})(?:-[ \\t]+)?run:[ \\t]*(.*)$")
RISK = (
    re.compile(r"\bgh\s+workflow\s+run\b", re.I),
    re.compile(r"\bgh\s+run\s+rerun\b", re.I),
    re.compile(r"\bgh\s+api\b.{0,180}\b(?:-X\s+POST|--method(?:=|\s+)POST)\b.{0,200}/dispatches\b", re.I | re.S),
    re.compile(r"\bcurl\b.{0,180}(?:-X\s+POST|--request\s+POST)\b.{0,200}/dispatches\b", re.I | re.S),
    re.compile(r"\b(?:create_workflow_dispatch|createWorkflowDispatch|create_repository_dispatch|createRepositoryDispatch)\b"),
)
MAX_PATHS = 400
MAX_SOURCE = 200_000
MAX_TOTAL = 16_000_000


class EvidenceError(Exception):
    """Unverifiable immutable source: deny without printing untrusted text."""


def git(root: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_", "GITHUB_", "GH_"))}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
    try:
        cmd = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args],
                             cwd=root, env=env, capture_output=True, timeout=12,
                             check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("git_unavailable") from exc
    if cmd.returncode:
        raise EvidenceError("unverifiable_git_object")
    return cmd.stdout


def inventory(root: Path, sha: str) -> dict[str, str]:
    if not SHA.fullmatch(sha):
        raise EvidenceError("invalid_sha")
    raw = git(root, "ls-tree", "-r", "-z", sha, "--", ".github/workflows")
    if len(raw) > MAX_TOTAL:
        raise EvidenceError("oversized_inventory")
    sources: dict[str, str] = {}
    total = 0
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            info, path = record.split(b"\t", 1)
            mode, kind, oid = info.decode("ascii").split()
            name = path.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise EvidenceError("invalid_tree_entry") from exc
        if not WORKFLOW.fullmatch(name):
            continue
        if name in sources or len(sources) >= MAX_PATHS:
            raise EvidenceError("ambiguous_workflow_inventory")
        if mode not in ("100644", "100755") or kind != "blob" or not SHA.fullmatch(oid):
            raise EvidenceError("unsafe_workflow_object")
        raw_source = git(root, "cat-file", "blob", oid)
        if len(raw_source) > MAX_SOURCE:
            raise EvidenceError("oversized_workflow")
        total += len(raw_source)
        if total > MAX_TOTAL:
            raise EvidenceError("oversized_inventory")
        try:
            sources[name] = raw_source.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise EvidenceError("invalid_workflow_encoding") from exc
    return sources


def workflow_run_event(source: str) -> bool:
    headers = list(ON.finditer(source))
    # Missing or duplicated trigger mapping is not reliable evidence.
    if len(headers) != 1:
        raise EvidenceError("ambiguous_event_mapping")
    start = headers[0]
    same_line = start.group(1).split("#", 1)[0].strip()
    if same_line:
        if same_line.startswith("*") or same_line.startswith("&"):
            raise EvidenceError("aliased_event_mapping")
        if same_line.startswith("[") and same_line.endswith("]"):
            return "workflow_run" in {v.strip(" '\"") for v in same_line[1:-1].split(",")}
        if same_line.startswith("{") and same_line.endswith("}"):
            return bool(re.search(r"(?:^|[, {])['\"]?workflow_run['\"]?\s*:", same_line))
        if same_line in ("workflow_run", "'workflow_run'", '"workflow_run"'):
            return True
        if re.fullmatch(r"['\"]?[A-Za-z_]+['\"]?", same_line):
            return False
        raise EvidenceError("unsupported_event_mapping")
    rest = source[start.end():]
    end = re.search(r"(?m)^[A-Za-z_][A-Za-z0-9_-]*\s*:", rest)
    block = rest[:end.start()] if end else rest
    if re.search(r"(?m)^  <<\s*:|^  [^\n]*[&*][A-Za-z0-9_-]+", block):
        raise EvidenceError("aliased_event_mapping")
    return bool(re.search(r"(?m)^  (?:workflow_run|'workflow_run'|\"workflow_run\")\s*:", block))


def executable_steps(source: str) -> list[str]:
    steps: list[str] = []
    for match in RUN.finditer(source):
        indent = len(match.group(1))
        lines = [match.group(2)]
        after = source[match.end():]
        for line in after.splitlines():
            if not line.strip():
                lines.append("")
                continue
            depth = len(line) - len(line.lstrip(" "))
            if depth <= indent:
                break
            if not line.lstrip().startswith("#"):
                lines.append(line.strip())
        steps.append("\n".join(lines))
    return steps


def classify(source: str) -> bool:
    if not workflow_run_event(source):
        return False
    # Only executable run-block bodies are inspected; documentation text is ignored.
    for body in executable_steps(source):
        normalized = re.sub(r"\\\s*\n", " ", body)
        if any(pattern.search(normalized) for pattern in RISK):
            return True
    return False


def evaluate(root: Path, base: str, head: str) -> dict:
    if not root.is_dir() or root.is_symlink() or not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise EvidenceError("invalid_source_identity")
    git(root, "merge-base", "--is-ancestor", base, head)
    old = inventory(root, base)
    new = inventory(root, head)
    previous = Counter(path for path, content in old.items() if classify(content))
    current = Counter(path for path, content in new.items() if classify(content))
    new_findings = sorted((current - previous).elements())
    return {
        "base": base, "head": head,
        "scanned_base": len(old), "scanned_head": len(new),
        "previous_findings": sum(previous.values()),
        "current_findings": sum(current.values()),
        "new_findings": new_findings,
        "production_mutation_authorized": False,
        "merge_authorized": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.root, args.base, args.head)
    except EvidenceError as exc:
        print("WORKFLOW_RUN_DISPATCH_GATE=DENY reason=" + str(exc))
        print("PRODUCTION_MUTATION_AUTHORIZED=false MERGE_AUTHORIZED=false")
        return 2
    except (OSError, UnicodeError, ValueError):
        print("WORKFLOW_RUN_DISPATCH_GATE=DENY reason=unverifiable_source")
        print("PRODUCTION_MUTATION_AUTHORIZED=false MERGE_AUTHORIZED=false")
        return 2
    print("WORKFLOW_RUN_DISPATCH_GATE=" + ("DENY" if result["new_findings"] else "PASS"))
    print("SCANNED_BASE=" + str(result["scanned_base"]) +
          " SCANNED_HEAD=" + str(result["scanned_head"]) +
          " LEGACY=" + str(result["previous_findings"]))
    for path in result["new_findings"][:30]:
        print("NEW_RECURSIVE_DISPATCH=" + path)
    print("PRODUCTION_MUTATION_AUTHORIZED=false MERGE_AUTHORIZED=false")
    return 1 if result["new_findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
