#!/usr/bin/env python3
"""Deny NEW scheduled GitHub Actions workflow-dispatch sites.

Source-only, monotone debt guard. Existing recovery workflows are neither
modified nor approved by a passing result. Never mutates VPS or GitHub state.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import os
import re
import selectors
import subprocess
import time
from collections import Counter
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[^/\0]+\.(?:yml|yaml)\Z")
ACTION = re.compile(r"\.github/actions/(?:[^/\0]+/)+action\.ya?ml\Z")
ON = re.compile(r"^(?:on|'on'|\"on\")\s*:(.*)$")
SCHEDULE = re.compile(r"(?i)(?:^|[\s,{\[])['\"]?schedule['\"]?\s*(?:[:,}\]]|$)")
DISPATCH = re.compile(
    r"(?i)\bgh(?:\s+(?:-R|--repo)(?:=|\s+)\S+)?\s+workflow"
    r"(?:\s+(?:-R|--repo)(?:=|\s+)\S+)?\s+run\b"
    # A dynamic workflow ID can be passed as a shell/environment variable.
    r"|/actions/workflows/[^\s'\"\n]{1,256}/dispatches\b"
    # POST repos/owner/repo/dispatches invokes repository_dispatch.
    r"|\brepos/[^\s'\"\n]{1,256}/dispatches\b"
    r"|\bcreateDispatchEvent\b"
    r"|\bcreateRepositoryDispatch\b"
    r"|\b[\w.-]+\.ya?ml/dispatches\b"
    r"|\bcreateWorkflowDispatch\b"
    r"|\bcreate_workflow_dispatch\b"
)
# Recognise job-level reusable-workflow dependencies without executing YAML.
# References are review-requiring in scheduled ancestry even if the helper is
# currently inert: a later helper edit could activate another dispatch path.
REUSABLE = re.compile(
    r"""(?m)^\s+(?:-\s*)?uses:\s*['"]?"""
    r"""((?:\./\.github/workflows/|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/)"""
    r"""[A-Za-z0-9_.-]+\.ya?ml(?:@[^\s'"#]+)?)['"]?\s*(?:#.*)?$"""
)
LOCAL_ACTION = re.compile(
    r"""(?m)^\s+(?:-\s*)?uses:\s*['"]?"""
    r"""(\./\.github/actions/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)"""
    r"""['"]?\s*(?:#.*)?$"""
)
MAX_MANIFESTS = 2048
MAX_BLOB = 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024


def git(repo: Path, *args: str) -> bytes:
    """Read Git stdout with a hard streaming cap and an overall deadline.

    Silencing Git stderr keeps repository-local path/credential details out of
    CI errors; any nonzero return is a non-authorizing failure.
    """
    command = ["git", "-C", str(repo), *args]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    output = bytearray()
    deadline = time.monotonic() + 20
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as reader:
            reader.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not reader.select(remaining):
                    raise subprocess.TimeoutExpired(command, 20)
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > MAX_TOTAL:
                    raise ValueError("Git output exceeds streaming bounds")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, 20)
        if process.wait(timeout=remaining) != 0:
            raise ValueError("Git source inventory failed closed")
        return bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()


def manifest_map(repo: Path, sha: str) -> dict[str, str]:
    if not SHA.fullmatch(sha):
        raise ValueError("exact 40-character lowercase Git SHA required")
    entries = git(repo, "ls-tree", "-r", "-z", sha, "--", ".github/workflows", ".github/actions").split(b"\0")
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
        if not (WORKFLOW.fullmatch(path) or ACTION.fullmatch(path)):
            continue
        # A newline, terminal escape or delimiter hidden in a Git pathname
        # must never be folded into apparently ordinary evidence filenames.
        if any(ord(char) < 32 or ord(char) == 127 for char in path):
            raise ValueError("unsafe manifest path")
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


def dispatch_sites(text: str, *, assume_scheduled: bool = False) -> Counter[str]:
    """Count stable site fingerprints, never disclose source or credentials."""
    if not assume_scheduled and not scheduled(text):
        return Counter()
    active = "\n".join(line for line in text.splitlines()
                       if not line.lstrip().startswith("#"))
    # Join shell continuations before looking for a command.
    active = active.replace("\\\n", "")
    result: Counter[str] = Counter()
    for match in DISPATCH.finditer(active):
        start = active.rfind("\n", 0, match.start()) + 1
        end = active.find("\n", match.end())
        if end == -1:
            end = len(active)
        # A changed target, method or argument is new risk, even if the old
        # invocation disappeared and the total number of sites did not grow.
        site = active[start:end].strip()
        signature = hashlib.sha256(site.encode("utf-8")).hexdigest()
        result[signature] += 1
    return result


def dispatch_count(text: str) -> int:
    return sum(dispatch_sites(text).values())


def findings(workflows: dict[str, str]) -> dict[str, Counter[str]]:
    """Trace scheduled callers into local reusable-workflow definitions.

    Each reachable workflow is visited once, so local cycles are bounded. The
    site identity also includes the providing workflow path, distinguishing
    otherwise identical dispatch commands in different helper workflows.
    """
    report: dict[str, Counter[str]] = {}
    for root, source in workflows.items():
        if not scheduled(source):
            continue
        sites: Counter[str] = Counter()
        visiting = [root]
        seen: set[str] = set()
        while visiting:
            path = visiting.pop()
            if path in seen:
                continue
            seen.add(path)
            text = workflows.get(path)
            if text is None:
                # Missing reusable definitions cannot be independently trusted.
                fingerprint = hashlib.sha256(("missing:" + path).encode()).hexdigest()
                sites[fingerprint] += 1
                continue
            for signature, count in dispatch_sites(
                text, assume_scheduled=True
            ).items():
                fingerprint = hashlib.sha256(
                    ("site:" + path + ":" + signature).encode()
                ).hexdigest()
                sites[fingerprint] += count
            for match in REUSABLE.finditer(text):
                target = match.group(1)
                # Every newly introduced/replaced delegation is risky even if
                # its target currently has no explicit dispatch command.
                edge = hashlib.sha256(
                    ("uses:" + path + ":" + target).encode()
                ).hexdigest()
                sites[edge] += 1
                if target.startswith("./.github/workflows/"):
                    visiting.append(target[2:])
            for match in LOCAL_ACTION.finditer(text):
                action_root = match.group(1)[2:]
                edge = hashlib.sha256(
                    ("action:" + path + ":" + action_root).encode()
                ).hexdigest()
                sites[edge] += 1
                found = False
                for suffix in ("/action.yml", "/action.yaml"):
                    manifest = action_root + suffix
                    if manifest in workflows:
                        visiting.append(manifest)
                        found = True
                if not found:
                    fingerprint = hashlib.sha256(
                        ("missing-action:" + action_root).encode()
                    ).hexdigest()
                    sites[fingerprint] += 1
        if sites:
            report[root] = sites
    return report


def evaluate(repo: Path, base: str, candidate: str) -> dict[str, object]:
    before = findings(manifest_map(repo, base))
    after = findings(manifest_map(repo, candidate))
    # Multiset subtraction prevents offsetting newly introduced dispatches
    # with a deletion or an unrelated existing occurrence in the same file.
    growth = {
        path: sum((sites - before.get(path, Counter())).values())
        for path, sites in sorted(after.items())
    }
    growth = {path: count for path, count in growth.items() if count > 0}
    return {
        "policy": "zcloud-scheduled-dispatch-no-new-debt-v1",
        "base_sha": base, "candidate_sha": candidate,
        "baseline_sites": sum(sum(x.values()) for x in before.values()),
        "candidate_sites": sum(sum(x.values()) for x in after.values()),
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
