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


def _structural_lines(text: str) -> list[tuple[int, str]]:
    """Return YAML-shaped lines, excluding comments and block scalar bodies.

    This is deliberately a bounded, read-only structural scanner, not a full
    YAML loader. A shell example containing 'steps:' or 'uses:' must never
    become evidence of an upload action.
    """
    result: list[tuple[int, str]] = []
    scalar_indent: int | None = None
    scalar_start = re.compile(
        r"^\s*(?:-\s+)?[A-Za-z_][A-Za-z0-9_-]*:\s*[|>][+-]?\s*(?:#.*)?$"
    )
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if scalar_indent is not None:
            if not stripped or indent > scalar_indent:
                continue
            scalar_indent = None
        if not stripped or stripped.startswith("#"):
            continue
        result.append((number, line))
        if scalar_start.match(line):
            scalar_indent = indent
    return result


def _step_blocks(text: str) -> Iterable[tuple[int, list[tuple[int, str]], int]]:
    """Yield only actual items under a steps: mapping, never shell examples."""
    lines = _structural_lines(text)
    steps_key = re.compile(r"^([ ]*)steps:\s*(?:#.*)?$")
    item_key = re.compile(r"^([ ]*)-\s+")
    i = 0
    while i < len(lines):
        match = steps_key.match(lines[i][1])
        if match is None:
            i += 1
            continue
        section_indent = len(match.group(1))
        i += 1
        item_indent: int | None = None
        while i < len(lines):
            indent = len(lines[i][1]) - len(lines[i][1].lstrip(" "))
            if indent <= section_indent:
                break
            item = item_key.match(lines[i][1])
            if item and item_indent is None:
                item_indent = len(item.group(1))
            if not item or indent != item_indent:
                i += 1
                continue
            first = i
            i += 1
            while i < len(lines):
                following = lines[i][1]
                depth = len(following) - len(following.lstrip(" "))
                if depth <= section_indent or depth < item_indent:
                    break
                if depth == item_indent and item_key.match(following):
                    break
                i += 1
            yield lines[first][0], lines[first:i], item_indent


def inspect_workflow(path: str, text: str, *, max_days: int) -> list[ArtifactUpload]:
    uploads: list[ArtifactUpload] = []
    for first_line, step, indent in _step_blocks(text):
        # The first entry may be '- uses:', subsequent mapping keys must
        # belong to the step itself, not an env/with/run child mapping.
        uses_line: int | None = None
        action: str | None = None
        with_pos: int | None = None
        field_indent = indent + 2
        for position, (number, line) in enumerate(step):
            if position == 0:
                candidate = re.match(r"^\s*-\s+uses:\s*(.+?)\s*$", line)
            else:
                candidate = re.match(r"^ {" + str(field_indent) + r"}uses:\s*(.+?)\s*$", line)
            if candidate:
                uses_line = number
                action = candidate.group(1).split("#", 1)[0].strip().strip("\'\\"")
            if re.match(r"^ {" + str(field_indent) + r"}with:\s*(?:#.*)?$", line):
                with_pos = position
        if not action or not re.match(r"^actions/upload-artifact@[^\s]+$", action):
            continue

        retention: int | None = None
        status = "missing"
        if with_pos is not None:
            for _, line in step[with_pos + 1:]:
                depth = len(line) - len(line.lstrip(" "))
                if depth <= field_indent:
                    break
                # Only direct inputs in the with: mapping count. A nested
                # demonstration or a sibling env value is not an input.
                if depth != field_indent + 2:
                    continue
                match = re.match(r"^\s*retention-days:\s*(.*?)\s*$", line)
                if not match:
                    continue
                raw = match.group(1).split("#", 1)[0].strip().strip("\'\\"")
                try:
                    retention = int(raw)
                except ValueError:
                    status = "dynamic_or_invalid"
                else:
                    if not 1 <= retention <= 90:
                        status = "invalid"
                    elif retention > max_days:
                        status = "over_limit"
                    else:
                        status = "bounded"
                break
        uploads.append(
            ArtifactUpload(
                path=path,
                line=uses_line or first_line,
                retention_days=retention,
                status=status,
            )
        )
    return uploads


def scan_repository(root: Path, *, max_days: int) -> list[ArtifactUpload]:
    workflows = root / WORKFLOW_DIR
    if workflows.is_symlink():
        raise RuntimeError("symlink workflow directory rejected")
    if not workflows.is_dir():
        raise FileNotFoundError(f"workflow directory not found: {workflows}")

    uploads: list[ArtifactUpload] = []
    for path in sorted(workflows.iterdir()):
        if path.suffix.lower() not in WORKFLOW_SUFFIXES:
            continue
        if path.is_symlink():
            raise RuntimeError(f"symlink workflow rejected: {path}")
        if not path.is_file():
            continue
        if path.stat().st_size > 1024 * 1024:
            raise RuntimeError("workflow too large to audit")
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
