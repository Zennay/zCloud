#!/usr/bin/env python3
"""Source-only deny gate for modified GitHub Actions workflows.

This gate DOES NOT authorize a live deployment or prove runner health.
It never mutates workflow, VPS, queue or runtime state.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}\Z")
PATH = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
EVENTS = {"push", "pull_request", "pull_request_target", "workflow_run",
          "schedule", "repository_dispatch", "issue_comment", "workflow_call",
          "release", "create", "delete", "workflow_dispatch"}
HOSTED = re.compile(r"(?:ubuntu|windows|macos)-[A-Za-z0-9_.-]+\Z")


def changed_workflows(base, head, root=ROOT):
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise ValueError("invalid_exact_sha")
    output = subprocess.check_output(
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACMRT",
         f"{base}...{head}", "--", ".github/workflows/"],
        cwd=root, timeout=15)
    paths = [p.decode("utf-8", "strict") for p in output.split(b"\0") if p]
    if len(paths) > 250 or len(set(paths)) != len(paths):
        raise ValueError("invalid_inventory")
    if any(not PATH.fullmatch(p) for p in paths):
        raise ValueError("invalid_workflow_path")
    return paths


def events_from(source):
    anchor = re.search(r"(?m)^on:[ \t]*(.*)$", source)
    if not anchor:
        raise ValueError("missing_literal_on")
    value = anchor.group(1).split("#", 1)[0].strip()
    if value.startswith("[") and value.endswith("]"):
        events = {v.strip(" '\"") for v in value[1:-1].split(",")}
    elif value.startswith("{") and value.endswith("}"):
        events = set(re.findall(r"([A-Za-z_]+)\s*:", value))
    elif not value:
        region = re.split(r"(?m)^[A-Za-z_][\w-]*:\s*", source[anchor.end():], maxsplit=1)[0]
        events = set(re.findall(r"(?m)^  ([A-Za-z_]+):(?:\s|$)", region))
    else:
        raise ValueError("unsupported_on")
    if not events or not events <= EVENTS:
        raise ValueError("unknown_event")
    return events


def judge(source):
    """Conservative syntactic admission for a *changed* workflow only."""
    try:
        events = events_from(source)
        root = re.search(r"(?m)^jobs:\s*$", source)
        if not root:
            raise ValueError("missing_jobs")
        region = re.split(r"(?m)^[A-Za-z_][\w-]*:\s*", source[root.end():], maxsplit=1)[0]
        blocks = re.split(r"(?m)(?=^  [A-Za-z_][\w-]*:[ \t]*$)", region)
        jobs = [b for b in blocks if re.match(r"(?m)^  [A-Za-z_][\w-]*:[ \t]*$", b)]
        if not jobs or len(jobs) > 100:
            raise ValueError("invalid_jobs")
    except ValueError as exc:
        return [str(exc)]

    problems = []
    for job in jobs:
        name = re.match(r"^  ([A-Za-z_][\w-]*):", job).group(1)
        runner = re.search(r"(?m)^    runs-on:\s*(.*)$", job)
        if not runner:
            problems.append(f"{name}:runner_unknown")
            continue
        runner_value = runner.group(1).strip()
        if HOSTED.fullmatch(runner_value):
            continue
        # Dynamic and self-hosted runners both require explicit manual gates.
        if "workflow_dispatch" not in events:
            problems.append(f"{name}:nonhosted_without_manual_event")
            continue
        conditional = re.search(r"(?m)^    if:\s*(.*)$", job)
        if not conditional:
            problems.append(f"{name}:missing_manual_if")
            continue
        lines = [conditional.group(1)]
        for line in job[conditional.end():].splitlines():
            if line.strip() and not line.startswith("      "):
                break
            lines.append(line.strip())
        expression = " ".join(lines)
        required = ("github.event_name", "workflow_dispatch",
                    "github.repository", "github.ref", "github.actor")
        if any(word not in expression for word in required) or "||" in expression:
            problems.append(f"{name}:incomplete_manual_if")
        if "confirm_sha" not in source or "GITHUB_SHA" not in job or "git ls-remote" not in job:
            problems.append(f"{name}:no_exact_main_preflight")
    return problems


def evaluate(paths, root=ROOT):
    problems = []
    for relative in paths:
        if not PATH.fullmatch(relative):
            problems.append("invalid_workflow_path")
            continue
        path = root / relative
        if (root.is_symlink() or (root / '.github').is_symlink() or
                (root / '.github/workflows').is_symlink()):
            problems.append(f"{relative}:symlink_ancestor")
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 100000:
            problems.append(f"{relative}:invalid_source_file")
            continue
        try:
            source = path.read_text("utf-8", "strict")
        except (UnicodeError, OSError):
            problems.append(f"{relative}:unreadable_source_file")
            continue
        problems.extend(f"{relative}:{reason}" for reason in judge(source))
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        if args.root.is_symlink() or not args.root.is_dir():
            raise ValueError('invalid_source_root')
        paths = changed_workflows(args.base, args.head, args.root)
        problems = evaluate(paths, args.root)
    except (ValueError, UnicodeError, OSError, subprocess.SubprocessError):
        print("WORKFLOW_CHANGE_GATE=DENY reason=unverifiable_source")
        return 1
    print(f"WORKFLOW_CHANGE_GATE={'DENY' if problems else 'PASS'} scanned={len(paths)}")
    for item in problems[:25]:
        print(f"DENY {item}")
    if len(problems) > 25:
        print("DENY additional_failures_redacted")
    print("PRODUCTION_MUTATION_AUTHORIZED=false")
    return bool(problems)


if __name__ == "__main__":
    sys.exit(main())
