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
MAX_WORKFLOW_FILES = 512
MAX_WORKFLOW_FILE_BYTES = 1_048_576
JOB_RE = re.compile(r"^(?P<indent> +)(?P<job>[A-Za-z0-9_-]+):[ \t]*(?:#.*)?$")
SELF_HOSTED_LABEL_RE = re.compile(r"(?<![\w-])self-hosted(?![\w-])", re.IGNORECASE)
# An expression can resolve to a self-hosted runner at execution time.
DYNAMIC_RUNNER_RE = re.compile(r"\$\{\{|\*[A-Za-z_][\w-]*")
# Only canonical hosted runner labels may be excluded from the self-hosted
# capacity audit. Custom labels and group selectors can reach private runners
# even when the explicit "self-hosted" label is omitted.
KNOWN_HOSTED_LABEL_RE = re.compile(
    r"(?i)^(?:ubuntu-(?:latest|slim|[0-9]{2}\.[0-9]{2}(?:-arm)?)"
    r"|windows-(?:latest|[0-9]{4}(?:-arm)?)"
    r"|macos-(?:latest|[0-9]{2}(?:-large|-[a-z0-9]+)?))$"
)


def _definitely_hosted_runner(runs_on: str) -> bool:
    selector = runs_on.strip()
    if selector.startswith("[") and selector.endswith("]"):
        labels = selector[1:-1].split(",")
        if len(labels) != 1:
            return False
        selector = labels[0].strip()
    selector = selector.strip("'\"").strip()
    return bool(KNOWN_HOSTED_LABEL_RE.fullmatch(selector))



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
    job_indent: int | None = None

    for index, line in enumerate(lines):
        if not in_jobs:
            if re.fullmatch(r"jobs:\s*(?:#.*)?", line):
                in_jobs = True
            continue

        if line and not line[0].isspace():
            break

        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = JOB_RE.match(line)
        if match:
            indent = len(match.group("indent"))
            if job_indent is None:
                job_indent = indent
            if indent == job_indent:
                starts.append((index, match.group("job")))

    for pos, (start, job) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        yield job, "\n".join(lines[start:end])


def _job_key_indent(block: str) -> int:
    """Find the job's immediate child indentation, not nested step/script keys."""
    lines = block.splitlines()
    job_indent = len(lines[0]) - len(lines[0].lstrip(" "))
    indents = [
        len(line) - len(line.lstrip(" "))
        for line in lines[1:]
        if line.strip() and not line.lstrip().startswith("#")
        and len(line) - len(line.lstrip(" ")) > job_indent
    ]
    return min(indents, default=job_indent + 2)


def _runs_on_text(block: str, *, key_indent: int) -> str | None:
    match = re.search(
        rf"(?m)^ {{{key_indent}}}runs-on:[ \t]*(?P<value>[^#\n]*)",
        block,
    )
    if match is None:
        return None
    value = match.group("value").strip()
    if value and value not in ("|", "|-", "|+", ">", ">-", ">+"):
        return value

    lines = block.splitlines()
    for index, line in enumerate(lines):
        if re.match(
            rf"^ {{{key_indent}}}runs-on:[ \t]*(?:[>|][+-]?)?(?:#.*)?$",
            line,
        ):
            continuation: list[str] = []
            for following in lines[index + 1 :]:
                stripped = following.lstrip()
                if not stripped:
                    continue
                indent = len(following) - len(stripped)
                if indent <= key_indent:
                    break
                continuation.append(stripped)
            return " ".join(continuation)
    return ""


def inspect_workflow(path: str, text: str, *, max_minutes: int) -> list[TimeoutFinding]:
    findings: list[TimeoutFinding] = []
    for job, block in iter_job_blocks(text):
        key_indent = _job_key_indent(block)
        runs_on = _runs_on_text(block, key_indent=key_indent)
        if runs_on is None:
            continue
        runner_is_dynamic = bool(DYNAMIC_RUNNER_RE.search(runs_on))
        runner_has_self_hosted_label = bool(SELF_HOSTED_LABEL_RE.search(runs_on))
        runner_is_unknown = (
            not runner_is_dynamic
            and not runner_has_self_hosted_label
            and not _definitely_hosted_runner(runs_on)
        )
        if not runner_is_dynamic and not runner_has_self_hosted_label and not runner_is_unknown:
            continue

        timeout_match = re.search(
            rf"(?m)^ {{{key_indent}}}timeout-minutes:[ \t]*(?P<value>[^#\n]+?)[ \t]*$",
            block,
        )
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

        if runner_is_dynamic:
            # Even a short timeout is not proof the unknown runner is hosted.
            status = "dynamic_runner_unknown"
        elif runner_is_unknown:
            # Custom labels and runner groups may resolve to self-hosted agents.
            status = "runner_target_unknown"

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
    if (root / ".github").is_symlink() or workflows.is_symlink():
        raise RuntimeError("symlink workflow directory rejected")
    if not workflows.is_dir():
        raise FileNotFoundError(f"workflow directory not found: {workflows}")

    paths = [
        path for path in sorted(workflows.iterdir())
        if path.suffix.lower() in WORKFLOW_SUFFIXES
    ]
    if len(paths) > MAX_WORKFLOW_FILES:
        raise RuntimeError("workflow inventory file count exceeds limit")
    findings: list[TimeoutFinding] = []
    for path in paths:
        if path.is_symlink():
            raise RuntimeError(f"symlink workflow rejected: {path}")
        if not path.is_file():
            raise RuntimeError("non-file workflow entry rejected")
        if path.stat().st_size > MAX_WORKFLOW_FILE_BYTES:
            raise RuntimeError("workflow file size exceeds limit")
        data = path.read_bytes()
        if len(data) > MAX_WORKFLOW_FILE_BYTES:
            raise RuntimeError("workflow file size changed during read")
        text = data.decode("utf-8")
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
    # A Git tree may replace .github or workflows with a symlink, causing the
    # recursive inventory to appear empty. Such snapshots cannot prove safety.
    for treeish, entry in ((ref, ".github"), (f"{ref}:.github", "workflows")):
        ancestor = subprocess.run(
            ["git", "-C", str(root), "ls-tree", treeish, "--", entry],
            check=True,
            capture_output=True,
            text=True,
        )
        rows = [line for line in ancestor.stdout.splitlines() if line.endswith("\t" + entry)]
        if len(rows) != 1:
            raise RuntimeError("workflow Git tree ancestry missing or ambiguous")
        mode, object_type, _sha = rows[0].split("\t", 1)[0].split(" ", 2)
        if mode == "120000" or object_type != "tree":
            raise RuntimeError("symlink or non-tree workflow Git ancestor rejected")
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", ref, "--", WORKFLOW_DIR.as_posix()],
        check=True,
        capture_output=True,
        text=True,
    )
    rows: list[TimeoutFinding] = []
    workflow_count = 0
    for line in proc.stdout.splitlines():
        metadata, sep, relative = line.partition("\t")
        if not sep:
            raise RuntimeError("unexpected git ls-tree output")
        mode, object_type, _object_id = metadata.split(" ", 2)
        path = Path(relative)
        if path.suffix.lower() not in WORKFLOW_SUFFIXES:
            continue
        workflow_count += 1
        if workflow_count > MAX_WORKFLOW_FILES:
            raise RuntimeError("Git workflow inventory file count exceeds limit")
        if mode == "120000":
            raise RuntimeError(f"symlink workflow rejected at git ref: {relative}")
        if object_type != "blob":
            raise RuntimeError("non-blob Git workflow entry rejected")
        size = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-s", _object_id],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if not size.isdigit() or int(size) > MAX_WORKFLOW_FILE_BYTES:
            raise RuntimeError("Git workflow file size exceeds limit")
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
    *,
    deny_worsening: bool = False,
) -> list[TimeoutFinding]:
    """Return debt introduced for job identities that were not already debt."""
    base_debt = {
        (row.path, row.job): row
        for row in base_rows
        if row.status != "bounded"
    }
    introduced: list[TimeoutFinding] = []
    for row in head_rows:
        if row.status == "bounded":
            continue
        previous = base_debt.get((row.path, row.job))
        if previous is None:
            introduced.append(row)
        elif deny_worsening and previous.status in (
            "over_limit", "dynamic_runner_unknown", "runner_target_unknown"
        ):
            # An existing untrusted runner or over-limit job is grandfathered
            # only while its timeout and provenance do not become less safe.
            if (
                row.status != previous.status
                or (
                    previous.timeout_minutes is not None
                    and (
                        row.timeout_minutes is None
                        or row.timeout_minutes > previous.timeout_minutes
                    )
                )
            ):
                introduced.append(row)
    return introduced


def summarize(rows: list[TimeoutFinding], *, max_minutes: int) -> dict[str, object]:
    by_status = {
        status: sum(1 for row in rows if row.status == status)
        for status in ("bounded", "missing", "over_limit", "dynamic_or_invalid", "invalid", "dynamic_runner_unknown", "runner_target_unknown")
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
        introduced = new_debt(base_rows, rows, deny_worsening=args.require_no_new_debt)
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
