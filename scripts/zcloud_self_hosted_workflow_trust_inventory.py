#!/usr/bin/env python3
"""Inventory trust/provenance signals for self-hosted GitHub Actions jobs.

The report is intentionally read-only and descriptive. It does not decide
whether a workflow is safe to merge or execute; it surfaces legacy debt so
hardening can be staged without taking ownership of those workflows.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable

JOB_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
RUNS_ON_RE = re.compile(r"^    runs-on:\s*(.+?)\s*$")
CHECKOUT_RE = re.compile(r"actions/checkout@([^\s#]+)")
SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def _workflow_files(root: Path) -> list[Path]:
    if root.is_symlink():
        raise ValueError(f"workflow root must not be a symlink: {root}")
    if not root.exists() or not root.is_dir():
        raise ValueError(f"workflow root is not a directory: {root}")

    files: list[Path] = []
    for path in sorted(root.glob("*.y*ml")):
        if path.is_symlink():
            raise ValueError(f"workflow file must not be a symlink: {path}")
        if path.is_file():
            files.append(path)
    return files


def _has_pr_trigger(lines: list[str]) -> bool:
    in_on = False
    on_indent = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if stripped == "on:":
            in_on = True
            on_indent = indent
            continue
        if in_on and indent <= (on_indent or 0):
            in_on = False
        if in_on and stripped.startswith(("pull_request:", "pull_request_target:")):
            return True
    return False


def _job_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    jobs_index = None
    for index, line in enumerate(lines):
        if line.strip() == "jobs:" and not line.startswith(" "):
            jobs_index = index
            break
    if jobs_index is None:
        return []

    jobs: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []
    for line in lines[jobs_index + 1 :]:
        if line and not line.startswith(" "):
            break
        match = JOB_RE.match(line)
        if match:
            if current_name is not None:
                jobs.append((current_name, current_lines))
            current_name = match.group(1)
            current_lines = [line]
        elif current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        jobs.append((current_name, current_lines))
    return jobs


def _runner_kind(value: str) -> str | None:
    normalized = value.strip().strip("'\"")
    if normalized == "self-hosted":
        return "generic_self_hosted"
    if "self-hosted" in normalized:
        return "labeled_self_hosted"
    return None


def _checkout_signals(block: list[str]) -> tuple[list[dict], bool, bool]:
    checkouts: list[dict] = []
    persist_false = False
    exact_ref = False

    for index, line in enumerate(block):
        match = CHECKOUT_RE.search(line)
        if not match:
            continue
        ref = match.group(1)
        pinned = bool(SHA40_RE.fullmatch(ref))
        step_lines = [line]
        base_indent = len(line) - len(line.lstrip())
        for following in block[index + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= base_indent:
                break
            step_lines.append(following)
        step_text = "\n".join(step_lines)
        step_persist_false = "persist-credentials: false" in step_text
        step_exact_ref = any(
            token in step_text
            for token in (
                "github.event.pull_request.head.sha",
                "github.event.workflow_run.head_sha",
                "github.sha",
                "EXPECTED_SHA",
                "TARGET_SHA",
                "HEAD_SHA",
            )
        ) or bool(re.search(r"ref:\s*[0-9a-fA-F]{40}\b", step_text))
        persist_false = persist_false or step_persist_false
        exact_ref = exact_ref or step_exact_ref
        checkouts.append(
            {
                "ref": ref,
                "immutable_sha": pinned,
                "persist_credentials_false": step_persist_false,
                "exact_ref_signal": step_exact_ref,
            }
        )

    all_persist_false = bool(checkouts) and all(persist_results)\n    all_exact_ref = bool(checkouts) and all(exact_ref_results)\n    return checkouts, all_persist_false, all_exact_ref


def inventory(root: Path) -> dict:
    entries: list[dict] = []
    workflows = _workflow_files(root)

    for path in workflows:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        pr_trigger = _has_pr_trigger(lines)
        for job_name, block in _job_blocks(lines):
            runs_on = None
            for line in block:
                match = RUNS_ON_RE.match(line)
                if match:
                    runs_on = match.group(1)
                    break
            if runs_on is None:
                continue
            kind = _runner_kind(runs_on)
            if kind is None:
                continue

            block_text = "\n".join(block)
            checkouts, persist_false, exact_ref = _checkout_signals(block)
            owner_same_repo_guard = (
                "github.actor == 'Zennay'" in block_text
                and "github.event.pull_request.head.repo.full_name == github.repository"
                in block_text
            )
            runner_guard = "scripts/zcloud_vps_runner_guard.py --json" in block_text

            findings: list[str] = []
            if kind == "generic_self_hosted":
                findings.append("generic_self_hosted_runner")
            if pr_trigger and not owner_same_repo_guard:
                findings.append("pr_self_hosted_without_owner_same_repo_guard")
            if checkouts and not all(item["immutable_sha"] for item in checkouts):
                findings.append("floating_checkout_action")
            if checkouts and not persist_false:
                findings.append("checkout_credentials_not_explicitly_disabled")
            if checkouts and not exact_ref:
                findings.append("checkout_exact_ref_not_evident")
            if "zcloud" in runs_on and "vps" in runs_on and not runner_guard:
                findings.append("zcloud_vps_runner_guard_not_evident")

            entries.append(
                {
                    "workflow": path.name,
                    "job": job_name,
                    "runner": runs_on,
                    "runner_kind": kind,
                    "pull_request_trigger": pr_trigger,
                    "owner_same_repo_guard": owner_same_repo_guard,
                    "runner_guard": runner_guard,
                    "checkout": checkouts,
                    "findings": sorted(findings),
                }
            )

    entries.sort(key=lambda item: (item["workflow"], item["job"]))
    counts = {
        "workflows_scanned": len(workflows),
        "self_hosted_jobs": len(entries),
        "generic_self_hosted_jobs": sum(
            item["runner_kind"] == "generic_self_hosted" for item in entries
        ),
        "labeled_self_hosted_jobs": sum(
            item["runner_kind"] == "labeled_self_hosted" for item in entries
        ),
        "jobs_with_findings": sum(bool(item["findings"]) for item in entries),
        "pr_triggered_self_hosted_jobs": sum(
            item["pull_request_trigger"] for item in entries
        ),
    }
    finding_counts: dict[str, int] = {}
    for item in entries:
        for finding in item["findings"]:
            finding_counts[finding] = finding_counts.get(finding, 0) + 1

    return {
        "schema_version": 1,
        "root": str(root),
        "counts": counts,
        "finding_counts": dict(sorted(finding_counts.items())),
        "jobs": entries,
    }


def _render_text(report: dict) -> str:
    counts = report["counts"]
    lines = [
        "zCloud self-hosted workflow trust inventory",
        *(f"{key}={value}" for key, value in counts.items()),
    ]
    for finding, count in report["finding_counts"].items():
        lines.append(f"finding.{finding}={count}")
    for item in report["jobs"]:
        if not item["findings"]:
            continue
        lines.append(
            f"{item['workflow']}::{item['job']} "
            + ",".join(item["findings"])
        )
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(".github/workflows"),
        help="Workflow directory to scan",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = inventory(args.root)
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(_render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
