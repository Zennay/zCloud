#!/usr/bin/env python3
"""Fail closed on newly added high-confidence secrets and unpinned workflow actions."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable


PINNED_ACTION_REF = re.compile(r"^[0-9a-fA-F]{40}$")
USES_LINE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)")
SECRET_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----")),
    ("github-token", re.compile(r"\bgh" + r"[pousr]_[A-Za-z0-9]{32,}\b")),
    ("openai-key", re.compile(r"\bsk-" + r"[A-Za-z0-9_-]{20,}\b")),
    ("aws-access-key", re.compile(r"\bA" + r"KIA[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox" + r"[baprs]-[A-Za-z0-9-]{20,}\b")),
    (
        "literal-secret-assignment",
        re.compile(
            r"""(?ix)
            \b(?:api[_-]?key|client[_-]?secret|password|token)\s*[:=]\s*
            ["'][A-Za-z0-9/+_=.-]{20,}["']
            """
        ),
    ),
)
PLACEHOLDER_MARKERS = (
    "example",
    "placeholder",
    "changeme",
    "not-a-real",
    "dummy",
    "redacted",
)


@dataclass(frozen=True)
class AddedLine:
    path: str
    line_no: int
    text: str


@dataclass(frozen=True)
class Finding:
    path: str
    line_no: int
    code: str
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line_no,
            "code": self.code,
            "detail": self.detail,
        }


def parse_added_lines(patch: str) -> list[AddedLine]:
    current_path: str | None = None
    new_line = 0
    added: list[AddedLine] = []

    for raw in patch.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            current_path = None if target == "/dev/null" else target.removeprefix("b/")
            continue
        if raw.startswith("@@ "):
            match = re.search(r"\+(\d+)(?:,(\d+))?", raw)
            if not match:
                raise ValueError(f"invalid hunk header: {raw!r}")
            new_line = int(match.group(1))
            continue
        if current_path is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            added.append(AddedLine(current_path, new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            continue
        elif raw.startswith("\\ No newline at end of file"):
            continue
        else:
            new_line += 1
    return added


def _looks_like_placeholder(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _scan_secret(line: AddedLine) -> Iterable[Finding]:
    if _looks_like_placeholder(line.text):
        return
    for code, pattern in SECRET_PATTERNS:
        if pattern.search(line.text):
            yield Finding(
                line.path,
                line.line_no,
                f"secret:{code}",
                "new high-confidence secret material detected; use repository/environment secrets instead",
            )


def _scan_workflow_action(line: AddedLine) -> Iterable[Finding]:
    path = PurePosixPath(line.path)
    if path.parent != PurePosixPath(".github/workflows") or path.suffix not in {".yml", ".yaml"}:
        return
    match = USES_LINE.match(line.text)
    if not match:
        return
    target = match.group(1)
    if target.startswith("./") or target.startswith("docker://"):
        return
    if "@" not in target:
        yield Finding(line.path, line.line_no, "dependency:action-unpinned", "external action has no @ref")
        return
    _, ref = target.rsplit("@", 1)
    if not PINNED_ACTION_REF.fullmatch(ref):
        yield Finding(
            line.path,
            line.line_no,
            "dependency:action-unpinned",
            "external action must be pinned to an immutable 40-character commit SHA",
        )


def scan_patch(patch: str) -> list[Finding]:
    findings: list[Finding] = []
    for line in parse_added_lines(patch):
        findings.extend(_scan_secret(line))
        findings.extend(_scan_workflow_action(line))
    return findings


def git_patch(base_ref: str, head_ref: str) -> str:
    completed = subprocess.run(
        ["git", "diff", "--unified=0", "--no-ext-diff", "--no-renames", f"{base_ref}...{head_ref}"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", help="trusted merge-base ref, for example origin/main")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--stdin", action="store_true", help="read a unified diff from stdin")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.stdin == bool(args.base_ref):
        raise SystemExit("choose exactly one of --stdin or --base-ref")
    patch = sys.stdin.read() if args.stdin else git_patch(args.base_ref, args.head_ref)
    findings = scan_patch(patch)
    payload = {
        "ok": not findings,
        "added_lines_scanned": len(parse_added_lines(patch)),
        "finding_count": len(findings),
        "findings": [finding.as_dict() for finding in findings],
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        if findings:
            for finding in findings:
                print(
                    f"{finding.path}:{finding.line_no}: {finding.code}: {finding.detail}",
                    file=sys.stderr,
                )
        print(
            "ZCLOUD_PR_SECURITY_SCAN_GREEN"
            if not findings
            else "ZCLOUD_PR_SECURITY_SCAN_BLOCKED",
            file=sys.stderr,
        )
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
