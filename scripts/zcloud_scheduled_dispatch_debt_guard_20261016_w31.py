#!/usr/bin/env python3
"""Deny NEW scheduled GitHub Actions workflow-dispatch sites.

Source-only, monotone debt guard. Existing recovery workflows are neither
modified nor approved by a passing result. Never mutates VPS or GitHub state.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[^/\0]+\.(?:yml|yaml)\Z")
ON = re.compile(r"^(?:on|'on'|\"on\")\s*:(.*)$")
SCHEDULE = re.compile(r"(?i)(?:^|[\s,{])['\"]?schedule['\"]?\s*(?:[:,}])")
DISPATCH = re.compile(
    r"(?i)\bgh\s+workflow\s+run\b"
    r"|/actions/workflows/[a-zA-Z0-9_./{}-]+/dispatches\b"
    r"|\bcreateWorkflowDispatch\b"
    r"|\bcreate_workflow_dispatch\b"
)
MAX_MANIFESTS = 2048
MAX_BLOB = 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024


def git(repo: Path, *args: str) -> bytes:
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
        check=False,
    )
    if p.returncode:
        raise ValueError("Git source inventory failed closed")
    if len(p.stdout) > MAX_TOTAL:
        raise ValueError("Git inventory output exceeds bounds")
    return p.stdout


def manifest_map(repo: Path, sha: str) -> dict[str, str]:
    if not SHA.fullmatch(sha):
        raise ValueError("exact 40-character lowercase Git SHA required")
    entries = git(repo, "ls-tree", "-r", "-z", sha, "--", ".github/workflows").split(b"\0")
    result: dict[str, str] = {}
    total = 0
    for record in entries:
        if not record:
            continue
        try:
            meta, path_bytes = record.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split(" ")
            path = path_bytes.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("invalid Git tree entry") from exc
        if not WORKFLOW.fullmatch(path):
            continue
        if path in result or len(result) >= MAX_MANIFESTS:
            raise ValueError("duplicate or excessive workflow entry")
        if mode not in {"100644", "100755"}:
            raise ValueError("non-regular workflow source rejected")
        if kind != "blob" or not SHA.fullmatch(oid):
            raise ValueError("invalid workflow blob reference")
        size = int(git(repo, "cat-file", "-s", oid).decode("ascii").strip())
        total += size
        if size < 0 or size > MAX_BLOB or total > MAX_TOTAL:
            raise ValueError("workflow blob bound exceeded")
        blob = git(repo, "cat-file", "blob", oid)
        if len(blob) != size:
            raise ValueError("workflow blob length drift")
        try:
            result[path] = blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("non-UTF8 workflow source") from exc
    return result


def scheduled(text: str) -> bool:
    """Detect actual top-level schedule trigger, including opaque aliases."""
    lines = text.splitlines()
    for pos, line in enumerate(lines):
        match = ON.match(line)
        if not match:
            continue
        inline = match.group(1).strip()
        if inline:
            if SCHEDULE.search(inline):
                return True
            if inline.startswith(("*", "!", "&")):
                return True
        for next_line in lines[pos + 1:]:
            if next_line and not next_line[0].isspace() and not next_line.startswith("#"):
                break
            if next_line.lstrip().startswith("#"):
                continue
            if SCHEDULE.search(next_line) and re.match(r"^\s{2,}", next_line):
                return True
    return False


def dispatch_count(text: str) -> int:
    if not scheduled(text):
        return 0
    active = "\n".join(line for line in text.splitlines()
                       if not line.lstrip().startswith("#"))
    return len(DISPATCH.findall(active))


def findings(workflows: dict[str, str]) -> Counter[str]:
    return Counter({path: count for path, text in workflows.items()
                    if (count := dispatch_count(text)) > 0})


def evaluate(repo: Path, base: str, candidate: str) -> dict[str, object]:
    before = findings(manifest_map(repo, base))
    after = findings(manifest_map(repo, candidate))
    growth = {path: count - before.get(path, 0)
              for path, count in sorted(after.items())
              if count > before.get(path, 0)}
    return {
        "policy": "zcloud-scheduled-dispatch-no-new-debt-v1",
        "base_sha": base, "candidate_sha": candidate,
        "baseline_sites": sum(before.values()),
        "candidate_sites": sum(after.values()),
        "new_sites": sum(growth.values()),
        "changed_workflows_with_new_sites": sorted(growth),
        "status": "reject" if growth else "pass",
        "mutation_performed": False, "deploy_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base", required=True)
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args()
    try:
        result = evaluate(args.repo, args.base, args.candidate)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({
            "policy": "zcloud-scheduled-dispatch-no-new-debt-v1",
            "status": "error", "reason": type(exc).__name__,
            "mutation_performed": False, "deploy_authorized": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
