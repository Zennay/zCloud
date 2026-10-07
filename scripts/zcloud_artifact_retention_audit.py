#!/usr/bin/env python3
"""Audit GitHub Actions artifact retention without mutating repository or runtime state."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

WORKFLOW_DIR = Path(".github/workflows")
WORKFLOW_SUFFIXES = {".yml", ".yaml"}
UPLOAD_RE = re.compile(
    r"^(?P<indent>\s*)(?:-\s+)?uses:\s+actions/upload-artifact@[^\s#]+",
    re.MULTILINE,
)
RETENTION_RE = re.compile(r"(?m)^\s+retention-days:\s*(?P<value>[^#\n]+?)\s*$")


@dataclass(frozen=True)
class ArtifactUpload:
    path: str
    line: int
    retention_days: int | None
    status: str

    def bounded(self) -> dict[str, object]:
        return asdict(self)


def _step_blocks(text: str) -> Iterable[tuple[int, str]]:
    """Yield upload-artifact step blocks using YAML list indentation as boundary."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(
            r"^(?P<indent>\s*)(?:-\s+)?uses:\s+actions/upload-artifact@[^\s#]+",
            line,
        )
        if not match:
            continue
        indent = len(match.group("indent"))
        block = [line]
        for following in lines[index + 1 :]:
            stripped = following.lstrip()
            if not stripped:
                block.append(following)
                continue
            following_indent = len(following) - len(stripped)
            if following_indent <= indent and stripped.startswith("-"):
                break
            if following_indent < indent:
                break
            block.append(following)
        yield index + 1, "\n".join(block)


def inspect_workflow(path: str, text: str, *, max_days: int) -> list[ArtifactUpload]:
    uploads: list[ArtifactUpload] = []
    for line, block in _step_blocks(text):
        match = RETENTION_RE.search(block)
        if match is None:
            retention = None
            status = "missing"
        else:
            raw = match.group("value").strip().strip("'\"")
            try:
                retention = int(raw)
            except ValueError:
                retention = None
                status = "dynamic_or_invalid"
            else:
                if retention < 1 or retention > 90:
                    status = "invalid"
                elif retention > max_days:
                    status = "over_limit"
                else:
                    status = "bounded"
        uploads.append(
            ArtifactUpload(
                path=path,
                line=line,
                retention_days=retention,
                status=status,
            )
        )
    return uploads


def scan_repository(root: Path, *, max_days: int) -> list[ArtifactUpload]:
    workflows = root / WORKFLOW_DIR
    if not workflows.is_dir():
        raise FileNotFoundError(f"workflow directory not found: {workflows}")

    uploads: list[ArtifactUpload] = []
    for path in sorted(workflows.iterdir()):
        if path.suffix.lower() not in WORKFLOW_SUFFIXES or not path.is_file():
            continue
        if path.is_symlink():
            raise RuntimeError(f"symlink workflow rejected: {path}")
        text = path.read_text(encoding="utf-8")
        uploads.extend(
            inspect_workflow(
                path.relative_to(root).as_posix(),
                text,
                max_days=max_days,
            )
        )
    return uploads


def summarize(uploads: list[ArtifactUpload], *, max_days: int) -> dict[str, object]:
    by_status = {
        status: sum(1 for item in uploads if item.status == status)
        for status in ("bounded", "missing", "over_limit", "dynamic_or_invalid", "invalid")
    }
    findings = [
        item.bounded()
        for item in uploads
        if item.status != "bounded"
    ]
    return {
        "policy": "github-actions-artifact-retention-audit-v1",
        "max_days": max_days,
        "upload_count": len(uploads),
        "by_status": by_status,
        "finding_count": len(findings),
        "findings": findings,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--max-days", type=int, default=30)
    parser.add_argument("--require-bounded", action="store_true")
    args = parser.parse_args(argv)

    if not 1 <= args.max_days <= 90:
        parser.error("--max-days must be between 1 and 90")

    try:
        uploads = scan_repository(args.repo_root.resolve(), max_days=args.max_days)
    except (OSError, UnicodeError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 2

    payload = summarize(uploads, max_days=args.max_days)
    payload["ok"] = payload["finding_count"] == 0
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if args.require_bounded and payload["finding_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
