#!/usr/bin/env python3
"""Reject newly introduced unbounded GitHub Actions artifact retention debt.

A previously noncompliant upload is grandfathered ONLY when its entire step
source is byte-for-byte unchanged in the same workflow. This is an offline,
read-only monotonic guard; it never changes a workflow, artifact or VPS.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys

from zcloud_artifact_retention_audit import _step_blocks, inspect_workflow

SHA = re.compile(r"[0-9a-f]{40}\Z")
UPLOAD = "actions/upload-artifact@"
LIMIT_WORKFLOWS = 250
LIMIT_BYTES = 256_000
LIMIT_STEPS = 300


class AdmissionError(Exception):
    """An untrusted or noncompliant source snapshot must be denied."""


def git(root: Path, *args: str, max_bytes: int = LIMIT_BYTES) -> bytes:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, check=False, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdmissionError("git_unavailable_or_timeout") from exc
    if proc.returncode:
        raise AdmissionError("git_command_failed")
    if len(proc.stdout) > max_bytes:
        raise AdmissionError("git_output_limit")
    return proc.stdout


def checked_revision(root: Path, revision: str) -> str:
    if not SHA.fullmatch(revision):
        raise AdmissionError("invalid_commit_identity")
    actual = git(root, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
    if actual != revision:
        raise AdmissionError("commit_mismatch")
    return revision


def changed_workflows(root: Path, base: str, head: str) -> list[str]:
    if git(root, "merge-base", base, head).decode().strip() != base:
        raise AdmissionError("base_not_ancestor")
    raw = git(
        root, "diff", "--no-renames", "--name-only", "-z",
        "--diff-filter=ACMRT", base, head, "--", ".github/workflows",
    )
    if not raw.endswith(b"\0") and raw:
        raise AdmissionError("invalid_diff_output")
    paths = []
    for candidate in filter(None, raw.split(b"\0")):
        try:
            path = candidate.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise AdmissionError("invalid_workflow_path") from exc
        if (
            not path.startswith(".github/workflows/")
            or path.count("/") != 2
            or path.endswith("/")
            or Path(path).suffix not in (".yml", ".yaml")
        ):
            # Ignore unrelated assets but never traverse unexpected workflow paths.
            if ".." in Path(path).parts:
                raise AdmissionError("invalid_workflow_path")
            continue
        paths.append(path)
    if len(paths) > LIMIT_WORKFLOWS:
        raise AdmissionError("workflow_count_limit")
    return paths


def blob(root: Path, revision: str, path: str) -> str | None:
    tree = git(root, "ls-tree", revision, "--", path)
    if not tree:
        return None
    if len(tree.splitlines()) != 1:
        raise AdmissionError("ambiguous_tree")
    metadata, sep, recorded = tree.partition(b"\t")
    if sep != b"\t" or recorded.decode("utf-8", "strict").strip() != path:
        raise AdmissionError("invalid_tree_path")
    fields = metadata.split()
    if len(fields) != 3 or fields[:2] not in ([b"100644", b"blob"], [b"100755", b"blob"]):
        raise AdmissionError("unsafe_workflow_tree_mode")
    data = git(root, "show", f"{revision}:{path}")
    try:
        return data.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise AdmissionError("invalid_workflow_encoding") from exc


def strictly_bounded_step(step_source: str) -> bool:
    """Validate retention only under the upload step's own literal with: map.

    This intentionally denies aliases, duplicate/misplaced YAML keys,
    expression values, and ambiguous inline maps instead of trusting the
    first regex match made by the historical read-only inventory.
    """
    lines = step_source.splitlines()
    if not lines:
        return False
    action = re.match(
        r"^(?P<spaces> *)(?P<dash>- +)?uses: *actions/upload-artifact@[A-Za-z0-9._-]+",
        lines[0],
    )
    if action is None:
        return False
    use_column = len(action.group("spaces")) + len(action.group("dash") or "")
    seen_with = False
    retention: list[str] = []
    for line in lines[1:]:
        raw = line.split("#", 1)[0].rstrip()
        if re.fullmatch(r" *with:", raw):
            if seen_with or len(raw) - len(raw.lstrip(" ")) != use_column:
                return False
            seen_with = True
        match = re.match(r"^( *)(retention-days)\s*:\s*(.*?)\s*$", raw)
        if match:
            if not seen_with or len(match.group(1)) != use_column + 2:
                return False
            retention.append(match.group(3))
    if len(retention) != 1 or not re.fullmatch(r"[1-9][0-9]?", retention[0]):
        return False
    return 1 <= int(retention[0]) <= 30


def findings(path: str, content: str) -> Counter[str]:
    blocks = list(_step_blocks(content))
    if len(blocks) > LIMIT_STEPS:
        raise AdmissionError("upload_step_count_limit")
    # The legacy audit is line-based. Deny ambiguous action mentions rather
    # than silently treating unparsed upload steps as compliant.
    if content.count(UPLOAD) != len(blocks):
        raise AdmissionError("ambiguous_upload_source")
    audited = inspect_workflow(path, content, max_days=30)
    if len(audited) != len(blocks):
        raise AdmissionError("audit_step_count_mismatch")
    unsafe: Counter[str] = Counter()
    for (_, step_source), row in zip(blocks, audited):
        if row.status != "bounded" or not strictly_bounded_step(step_source):
            unsafe[step_source] += 1
    return unsafe


def assess(root: Path, base: str, head: str) -> dict[str, object]:
    base = checked_revision(root, base)
    head = checked_revision(root, head)
    if git(root, "rev-parse", "HEAD").decode().strip() != head:
        raise AdmissionError("wrong_checkout_head")
    paths = changed_workflows(root, base, head)
    debt_before = debt_after = newly_noncompliant = 0
    for path in paths:
        old_text = blob(root, base, path)
        new_text = blob(root, head, path)
        if new_text is None:
            raise AdmissionError("changed_workflow_missing")
        previous = findings(path, old_text) if old_text is not None else Counter()
        current = findings(path, new_text)
        debt_before += sum(previous.values())
        debt_after += sum(current.values())
        newly_noncompliant += sum((current - previous).values())
    return {
        "policy": "zcloud-artifact-retention-no-new-debt-v1",
        "base": base,
        "head": head,
        "changed_workflows": len(paths),
        "legacy_unsafe_steps_in_changed_files": debt_before,
        "unsafe_steps_after": debt_after,
        "new_unsafe_steps": newly_noncompliant,
        "max_days": 30,
        "mutation_performed": False,
        "ok": newly_noncompliant == 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        report = assess(args.repo_root.resolve(), args.base, args.head)
    except (AdmissionError, OSError, UnicodeError, ValueError) as exc:
        report = {
            "policy": "zcloud-artifact-retention-no-new-debt-v1",
            "ok": False,
            "reason": str(exc) if isinstance(exc, AdmissionError) else "invalid_source",
            "mutation_performed": False,
        }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
