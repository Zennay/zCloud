#!/usr/bin/env python3
"""Fail only when a PR increases self-hosted workflow trust debt.

Historical debt is tolerated so remediation can be incremental. For every
changed workflow, candidate finding counts must be <= the exact base counts.
New workflows therefore have a zero-debt baseline.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
from pathlib import Path
from typing import Iterable

JOB_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
RUNS_ON_RE = re.compile(r"^    runs-on:\s*(.+?)\s*$")
CHECKOUT_RE = re.compile(r"actions/checkout@([^\s#]+)")
SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
WORKFLOW_PREFIX = ".github/workflows/"


def _pull_request_trigger(lines: list[str]) -> bool:
    in_on = False
    on_indent = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("on:") and stripped != "on:":
            return bool(re.search(r"\bpull_request(?:_target)?\b", stripped))
        if stripped == "on:":
            in_on = True
            on_indent = indent
            continue
        if in_on and indent <= on_indent:
            in_on = False
        if in_on and stripped.startswith(("pull_request:", "pull_request_target:")):
            return True
    return False


def _job_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    try:
        start = next(
            i for i, line in enumerate(lines)
            if line.strip() == "jobs:" and not line.startswith(" ")
        )
    except StopIteration:
        return []

    result: list[tuple[str, list[str]]] = []
    name: str | None = None
    block: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        match = JOB_RE.match(line)
        if match:
            if name is not None:
                result.append((name, block))
            name = match.group(1)
            block = [line]
        elif name is not None:
            block.append(line)
    if name is not None:
        result.append((name, block))
    return result


def _checkout_findings(block: list[str]) -> list[str]:
    findings: list[str] = []
    for index, line in enumerate(block):
        match = CHECKOUT_RE.search(line)
        if not match:
            continue
        action_ref = match.group(1)
        step_lines = [line]
        base_indent = len(line) - len(line.lstrip())
        for following in block[index + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= base_indent:
                break
            step_lines.append(following)
        step = "\n".join(step_lines)

        if not SHA40_RE.fullmatch(action_ref):
            findings.append("floating_checkout_action")
        if "persist-credentials: false" not in step:
            findings.append("checkout_credentials_not_explicitly_disabled")
        exact = any(
            token in step
            for token in (
                "github.event.pull_request.head.sha",
                "github.event.workflow_run.head_sha",
                "github.sha",
                "EXPECTED_SHA",
                "TARGET_SHA",
                "HEAD_SHA",
            )
        ) or bool(re.search(r"ref:\s*[0-9a-fA-F]{40}\b", step))
        if not exact:
            findings.append("checkout_exact_ref_not_evident")
    return findings


def findings_for_text(text: str) -> collections.Counter[str]:
    lines = text.splitlines()
    pr_trigger = _pull_request_trigger(lines)
    findings: list[str] = []

    for _job_name, block in _job_blocks(lines):
        runs_on = ""
        for line in block:
            match = RUNS_ON_RE.match(line)
            if match:
                runs_on = match.group(1).strip().strip("'\"")
                break
        if "self-hosted" not in runs_on:
            continue

        block_text = "\n".join(block)
        if runs_on == "self-hosted":
            findings.append("generic_self_hosted_runner")
        if pr_trigger and not (
            "github.actor == 'Zennay'" in block_text
            and "github.event.pull_request.head.repo.full_name == github.repository"
            in block_text
        ):
            findings.append("pr_self_hosted_without_owner_same_repo_guard")
        findings.extend(_checkout_findings(block))
        if (
            "zcloud" in runs_on
            and "vps" in runs_on
            and "scripts/zcloud_vps_runner_guard.py --json" not in block_text
        ):
            findings.append("zcloud_vps_runner_guard_not_evident")

    return collections.Counter(findings)


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        text=True,
        capture_output=True,
    )


def _validate_sha(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not SHA40_RE.fullmatch(value):
        raise ValueError(f"{label} must be an exact 40-character commit SHA")
    return value.lower()


def changed_workflows(base_sha: str, head_sha: str) -> list[str]:
    base_sha = _validate_sha(base_sha, "base_sha")
    head_sha = _validate_sha(head_sha, "head_sha")
    proc = _git(
        "diff",
        "--name-only",
        "--diff-filter=ACMR",
        base_sha,
        head_sha,
        "--",
        ".github/workflows",
    )
    return sorted(
        path
        for path in proc.stdout.splitlines()
        if path.startswith(WORKFLOW_PREFIX)
        and path.endswith((".yml", ".yaml"))
    )


def _text_at(revision: str, path: str) -> str:
    proc = _git("show", f"{revision}:{path}", check=False)
    if proc.returncode != 0:
        return ""
    return proc.stdout


def audit(base_sha: str, head_sha: str) -> dict:
    base_sha = _validate_sha(base_sha, "base_sha")
    head_sha = _validate_sha(head_sha, "head_sha")
    paths = changed_workflows(base_sha, head_sha)
    files: list[dict] = []
    violations: list[dict] = []

    for path in paths:
        baseline = findings_for_text(_text_at(base_sha, path))
        candidate = findings_for_text(_text_at(head_sha, path))
        increased = {
            key: candidate[key] - baseline[key]
            for key in sorted(set(baseline) | set(candidate))
            if candidate[key] > baseline[key]
        }
        item = {
            "path": path,
            "base_findings": dict(sorted(baseline.items())),
            "head_findings": dict(sorted(candidate.items())),
            "increased": increased,
        }
        files.append(item)
        if increased:
            violations.append({"path": path, "increased": increased})

    return {
        "schema_version": 1,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "changed_workflow_count": len(paths),
        "ok": not violations,
        "files": files,
        "violations": violations,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = audit(args.base_sha, args.head_sha)
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            "ZCLOUD_SELF_HOSTED_TRUST_NO_REGRESSION="
            + ("GREEN" if report["ok"] else "FAIL")
        )
        for violation in report["violations"]:
            print(
                f"{violation['path']} "
                + ",".join(
                    f"{key}=+{value}"
                    for key, value in violation["increased"].items()
                )
            )
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
