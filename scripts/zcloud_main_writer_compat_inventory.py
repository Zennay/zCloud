#!/usr/bin/env python3
"""Bounded read-only inventory of zCloud workflows that may write directly to main."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

POLICY = "zcloud-main-writer-compat-v1"
MAX_FILES = 400
MAX_FILE_BYTES = 512_000
MAX_RESULTS = 200

WRITE_PERMISSION_RE = re.compile(r"(?m)^\s*contents\s*:\s*write\s*(?:#.*)?$")
MAIN_PUSH_RE = re.compile(
    r"(?i)(?:git\s+push[^\n]*(?:refs/heads/main|\bmain\b)|"
    r"git\s+push[^\n]*HEAD:main)"
)
REFS_API_RE = re.compile(
    r"(?i)(?:/git/refs/(?:heads/)?main|/git/refs/heads/main|"
    r"refs/heads/main)"
)
WRITE_API_RE = re.compile(
    r"(?i)(?:\b(?:POST|PUT|PATCH|DELETE)\b|"
    r"gh\s+api[^\n]*(?:--method|-X)\s+(?:POST|PUT|PATCH|DELETE)|"
    r"curl[^\n]*\s-X\s*(?:POST|PUT|PATCH|DELETE))"
)
CHECKOUT_CREDS_RE = re.compile(r"(?m)^\s*persist-credentials\s*:\s*true\s*(?:#.*)?$")


@dataclass(frozen=True)
class Finding:
    path: str
    signals: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {"path": self.path, "signals": list(self.signals)}


def _is_regular_safe_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


def _has_main_refs_write_api(text: str) -> bool:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not REFS_API_RE.search(line):
            continue
        start = max(0, index - 6)
        end = min(len(lines), index + 7)
        if WRITE_API_RE.search("\n".join(lines[start:end])):
            return True
    return False


def classify_text(text: str) -> tuple[str, ...]:
    signals: list[str] = []
    if WRITE_PERMISSION_RE.search(text):
        signals.append("contents_write")
    if MAIN_PUSH_RE.search(text):
        signals.append("git_push_main")
    if _has_main_refs_write_api(text):
        signals.append("main_refs_write_api")
    if CHECKOUT_CREDS_RE.search(text):
        signals.append("checkout_persists_credentials")
    return tuple(signals)


def iter_workflow_files(root: Path) -> Iterable[Path]:
    workflow_root = root / ".github" / "workflows"
    if not workflow_root.is_dir() or workflow_root.is_symlink():
        return ()
    files = sorted(
        p
        for p in workflow_root.iterdir()
        if p.suffix.lower() in {".yml", ".yaml"} and _is_regular_safe_file(p)
    )
    if len(files) > MAX_FILES:
        raise SystemExit(f"workflow inventory exceeds MAX_FILES={MAX_FILES}")
    return files


def build_inventory(root: Path) -> dict[str, object]:
    findings: list[Finding] = []
    scanned = 0
    skipped = 0

    workflow_root = root / ".github" / "workflows"
    if not workflow_root.exists():
        raise SystemExit("missing .github/workflows")
    if workflow_root.is_symlink() or not workflow_root.is_dir():
        raise SystemExit(".github/workflows must be a real directory")

    candidates = sorted(
        p for p in workflow_root.iterdir() if p.suffix.lower() in {".yml", ".yaml"}
    )
    if len(candidates) > MAX_FILES:
        raise SystemExit(f"workflow inventory exceeds MAX_FILES={MAX_FILES}")

    for path in candidates:
        if not _is_regular_safe_file(path):
            skipped += 1
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            skipped += 1
            continue
        signals = classify_text(text)
        if signals:
            findings.append(Finding(path.relative_to(root).as_posix(), signals))
        if len(findings) > MAX_RESULTS:
            raise SystemExit(f"writer inventory exceeds MAX_RESULTS={MAX_RESULTS}")

    direct_main = [
        f for f in findings if "git_push_main" in f.signals or "main_refs_write_api" in f.signals
    ]
    permission_only = [
        f
        for f in findings
        if "contents_write" in f.signals
        and "git_push_main" not in f.signals
        and "main_refs_write_api" not in f.signals
    ]

    return {
        "policy": POLICY,
        "status": "inventory_complete",
        "mutation_performed": False,
        "workflow_files_scanned": scanned,
        "workflow_files_skipped": skipped,
        "findings_count": len(findings),
        "direct_main_writer_count": len(direct_main),
        "contents_write_only_count": len(permission_only),
        "direct_main_writers": [f.to_json() for f in direct_main],
        "contents_write_only": [f.to_json() for f in permission_only],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--require-zero-direct-main-writers",
        action="store_true",
        help="fail closed when any direct-main writer remains",
    )
    args = parser.parse_args()

    result = build_inventory(args.root.resolve())
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(
            f"{POLICY}: scanned={result['workflow_files_scanned']} "
            f"direct_main={result['direct_main_writer_count']} "
            f"contents_write_only={result['contents_write_only_count']} "
            "mutation_performed=false"
        )

    if args.require_zero_direct_main_writers and result["direct_main_writer_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
