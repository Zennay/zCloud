#!/usr/bin/env python3
"""Audit workflow compatibility with preventive main-branch protection."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

WORKFLOW_DIR = Path(".github/workflows")
WORKFLOW_SUFFIXES = {".yml", ".yaml"}
MAX_WORKFLOW_BYTES = 512_000

CONTENTS_WRITE_RE = re.compile(r"(?m)^\s*contents:\s*write\s*(?:#.*)?$")
GIT_PUSH_RE = re.compile(r"(?im)\bgit\s+push\b[^\n]*")
GH_REFS_WRITE_RE = re.compile(
    r"(?im)\bgh\s+api\b[^\n]*(?:-X|--method)\s*(?:POST|PATCH|PUT|DELETE)\b[^\n]*"
    r"(?:/git/refs|/git/commits|/git/trees|/contents(?:/|\b))"
)
CURL_REFS_WRITE_RE = re.compile(
    r"(?im)\bcurl\b[^\n]*(?:-X|--request)\s*(?:POST|PATCH|PUT|DELETE)\b[^\n]*"
    r"(?:/git/refs|/git/commits|/git/trees|/contents(?:/|\b))"
)

MAIN_DESTINATION_PATTERNS = (
    re.compile(r"(?i)(?:^|\s)(?:HEAD|[0-9a-f]{7,40})?:?refs/heads/main(?:\s|$)"),
    re.compile(r"(?i)(?:^|\s)(?:HEAD|[0-9a-f]{7,40})?:?main(?:\s|$)"),
    re.compile(r"(?i)(?:^|\s)main:main(?:\s|$)"),
    re.compile(r"(?i)(?:^|\s)HEAD:main(?:\s|$)"),
)


@dataclass(frozen=True)
class Finding:
    path: str
    contents_write: bool
    direct_main_push: bool
    refs_api_write: bool
    status: str

    def bounded(self) -> dict[str, object]:
        return asdict(self)


def _push_targets_main(command: str) -> bool:
    normalized = re.sub(r"\s+", " ", command.strip())
    if "--delete" in normalized and re.search(r"(?i)(?:\s|:)main(?:\s|$)", normalized):
        return True
    return any(pattern.search(normalized) for pattern in MAIN_DESTINATION_PATTERNS)


def inspect_workflow(path: str, text: str) -> Finding | None:
    contents_write = bool(CONTENTS_WRITE_RE.search(text))
    direct_main_push = any(
        _push_targets_main(match.group(0)) for match in GIT_PUSH_RE.finditer(text)
    )
    refs_api_write = bool(GH_REFS_WRITE_RE.search(text) or CURL_REFS_WRITE_RE.search(text))

    if not (contents_write or direct_main_push or refs_api_write):
        return None

    status = (
        "incompatible"
        if direct_main_push or refs_api_write
        else "review_required"
        if contents_write
        else "compatible"
    )
    return Finding(path, contents_write, direct_main_push, refs_api_write, status)


def scan_repository(root: Path) -> list[Finding]:
    workflows = root / WORKFLOW_DIR
    if not workflows.is_dir():
        raise FileNotFoundError(f"workflow directory not found: {workflows}")

    findings: list[Finding] = []
    for path in sorted(workflows.iterdir()):
        if path.suffix.lower() not in WORKFLOW_SUFFIXES:
            continue
        if path.is_symlink():
            raise RuntimeError(f"symlink workflow rejected: {path}")
        if not path.is_file():
            continue
        if path.stat().st_size > MAX_WORKFLOW_BYTES:
            raise RuntimeError(f"oversized workflow rejected: {path}")
        finding = inspect_workflow(
            path.relative_to(root).as_posix(),
            path.read_text(encoding="utf-8"),
        )
        if finding is not None:
            findings.append(finding)
    return findings


def summarize(findings: list[Finding]) -> dict[str, object]:
    incompatible = [row for row in findings if row.status == "incompatible"]
    review_required = [row for row in findings if row.status == "review_required"]
    compatible = [row for row in findings if row.status == "compatible"]
    return {
        "policy": "zcloud-main-writer-compatibility-audit-v1",
        "workflow_count_with_write_signal": len(findings),
        "incompatible_count": len(incompatible),
        "review_required_count": len(review_required),
        "compatible_count": len(compatible),
        "branch_protection_ready": not incompatible and not review_required,
        "findings": [row.bounded() for row in findings],
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--require-compatible", action="store_true")
    args = parser.parse_args(argv)

    try:
        findings = scan_repository(args.repo_root.resolve())
    except (OSError, UnicodeError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 2

    payload = summarize(findings)
    payload["ok"] = bool(payload["branch_protection_ready"])
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if args.require_compatible and not payload["branch_protection_ready"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
