#!/usr/bin/env python3
"""Audit self-hosted GitHub Actions jobs for explicit bounded timeouts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

WORKFLOW_DIR = Path(".github/workflows")
WORKFLOW_SUFFIXES = {".yml", ".yaml"}
JOB_RE = re.compile(r"^  (?P<job>[A-Za-z0-9_-]+):\s*(?:#.*)?$")
RUNS_ON_RE = re.compile(r"(?m)^ {4}runs-on:\s*(?P<value>[^#\n]*)")
TIMEOUT_RE = re.compile(r"(?m)^ {4}timeout-minutes:\s*(?P<value>[^#\n]+?)\s*$")


@dataclass(frozen=True)
class TimeoutFinding:
    path: str
    job: str
    timeout_minutes: int | None
    status: str

    def bounded(self) -> dict[str, object]:
        return asdict(self)


def iter_job_blocks(text: str) -> Iterable[tuple[str, str]]:
    lines = text.splitlines()
    in_jobs = False
    starts: list[tuple[int, str]] = []

    for index, line in enumerate(lines):
        if not in_jobs:
            if re.fullmatch(r"jobs:\s*(?:#.*)?", line):
                in_jobs = True
            continue

        if line and not line[0].isspace():
            break

        match = JOB_RE.match(line)
        if match:
            starts.append((index, match.group("job")))

    for pos, (start, job) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        yield job, "\n".join(lines[start:end])


def _runs_on_text(block: str) -> str | None:
    match = RUNS_ON_RE.search(block)
    if match is None:
        return None
    value = match.group("value").strip()
    if value:
        return value

    lines = block.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^ {4}runs-on:\s*(?:#.*)?$", line):
            continuation: list[str] = []
            for following in lines[index + 1 :]:
                stripped = following.lstrip()
                if not stripped:
                    continue
                indent = len(following) - len(stripped)
                if indent <= 4:
                    break
                continuation.append(stripped)
            return " ".join(continuation)
    return ""


def inspect_workflow(path: str, text: str, *, max_minutes: int) -> list[TimeoutFinding]:
    findings: list[TimeoutFinding] = []
    for job, block in iter_job_blocks(text):
        runs_on = _runs_on_text(block)
        if runs_on is None or "self-hosted" not in runs_on.lower():
            continue

        timeout_match = TIMEOUT_RE.search(block)
        if timeout_match is None:
            timeout = None
            status = "missing"
        else:
            raw = timeout_match.group("value").strip().strip("'\"")
            try:
                timeout = int(raw)
            except ValueError:
                timeout = None
                status = "dynamic_or_invalid"
            else:
                if timeout < 1:
                    status = "invalid"
                elif timeout > max_minutes:
                    status = "over_limit"
                else:
                    status = "bounded"

        findings.append(
            TimeoutFinding(
                path=path,
                job=job,
                timeout_minutes=timeout,
                status=status,
            )
        )
    return findings


def scan_repository(root: Path, *, max_minutes: int) -> list[TimeoutFinding]:
    workflows = root / WORKFLOW_DIR
    if not workflows.is_dir():
        raise FileNotFoundError(f"workflow directory not found: {workflows}")

    findings: list[TimeoutFinding] = []
    for path in sorted(workflows.iterdir()):
        if path.suffix.lower() not in WORKFLOW_SUFFIXES:
            continue
        if path.is_symlink():
            raise RuntimeError(f"symlink workflow rejected: {path}")
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        findings.extend(
            inspect_workflow(
                path.relative_to(root).as_posix(),
                text,
                max_minutes=max_minutes,
            )
        )
    return findings


def scan_git_ref(root: Path, ref: str, *, max_minutes: int) -> list[TimeoutFinding]:
    """Read workflow blobs from an existing git commit without checking it out."""
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", ref, "--", WORKFLOW_DIR.as_posix()],
        check=True,
        capture_output=True,
        text=True,
    )
    rows: list[TimeoutFinding] = []
    for line in proc.stdout.splitlines():
        metadata, sep, relative = line.partition("\t")
        if not sep:
            raise RuntimeError("unexpected git ls-tree output")
        mode, object_type, _object_id = metadata.split(" ", 2)
        path = Path(relative)
        if path.suffix.lower() not in WORKFLOW_SUFFIXES:
            continue
        if mode == "120000":
            raise RuntimeError(f"symlink workflow rejected at git ref: {relative}")
        if object_type != "blob":
            continue
        blob = subprocess.run(
            ["git", "-C", str(root), "show", f"{ref}:{relative}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        rows.extend(inspect_workflow(relative, blob, max_minutes=max_minutes))
    return rows


def new_debt(
    base_rows: list[TimeoutFinding],
    head_rows: list[TimeoutFinding],
) -> list[TimeoutFinding]:
    """Return debt introduced for job identities that were not already debt."""
    base_debt = {
        (row.path, row.job)
        for row in base_rows
        if row.status != "bounded"
    }
    return [
        row
        for row in head_rows
        if row.status != "bounded" and (row.path, row.job) not in base_debt
    ]


def summarize(rows: list[TimeoutFinding], *, max_minutes: int) -> dict[str, object]:
    by_status = {
        status: sum(1 for row in rows if row.status == status)
        for status in ("bounded", "missing", "over_limit", "dynamic_or_invalid", "invalid")
    }
    debt = [row.bounded() for row in rows if row.status != "bounded"]
    return {
        "policy": "self-hosted-job-timeout-audit-v1",
        "max_minutes": max_minutes,
        "self_hosted_job_count": len(rows),
        "by_status": by_status,
        "finding_count": len(debt),
        "findings": debt,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--max-minutes", type=int, default=30)
    parser.add_argument("--require-bounded", action="store_true")
    parser.add_argument(
        "--base-ref",
        help="Git ref to compare against for monotonic no-new-debt enforcement.",
    )
    parser.add_argument("--require-no-new-debt", action="store_true")
    args = parser.parse_args(argv)

    if not 1 <= args.max_minutes <= 360:
        parser.error("--max-minutes must be between 1 and 360")

    try:
        rows = scan_repository(args.repo_root.resolve(), max_minutes=args.max_minutes)
    except (OSError, UnicodeError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 2

    payload = summarize(rows, max_minutes=args.max_minutes)
    payload["ok"] = payload["finding_count"] == 0

    if args.require_no_new_debt and not args.base_ref:
        parser.error("--require-no-new-debt requires --base-ref")

    if args.base_ref:
        try:
            base_rows = scan_git_ref(
                args.repo_root.resolve(),
                args.base_ref,
                max_minutes=args.max_minutes,
            )
        except (OSError, UnicodeError, RuntimeError, subprocess.SubprocessError):
            print(json.dumps({"ok": False, "error": "base_ref_unreadable"}, sort_keys=True))
            return 2
        introduced = new_debt(base_rows, rows)
        payload["base_ref"] = args.base_ref
        payload["base_self_hosted_job_count"] = len(base_rows)
        payload["base_finding_count"] = sum(
            1 for row in base_rows if row.status != "bounded"
        )
        payload["new_debt_count"] = len(introduced)
        payload["new_debt"] = [row.bounded() for row in introduced]

    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if args.require_bounded and payload["finding_count"]:
        return 1
    if args.require_no_new_debt and payload.get("new_debt_count", 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
