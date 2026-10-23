#!/usr/bin/env python3
"""Audit GitHub Actions artifact retention without mutating repository or runtime state."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

WORKFLOW_DIR = Path(".github/workflows")
WORKFLOW_SUFFIXES = {".yml", ".yaml"}
MAX_WORKFLOW_ENTRIES = 1024
MAX_WORKFLOW_BYTES = 1024 * 1024
MAX_TOTAL_WORKFLOW_BYTES = 32 * 1024 * 1024
MAX_ARTIFACT_UPLOADS = 4096

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
        r"^\s*(?:-\s+)?[A-Za-z_][A-Za-z0-9_-]*:\s*[|>](?:[1-9][+-]?|[+-][1-9]?)?\s*(?:#.*)?$"
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
        # A valid YAML key may have spaces before the colon, while a tabbed
        # indentation cannot be interpreted by this bounded source reader.
        if re.match(r"^ *\t", line) or re.match(
            r"^\s*(?:-\s+)?(?:jobs|steps|uses|with|retention-days)\s+:",
            line,
        ):
            raise RuntimeError("unresolved workflow key whitespace")
        # A flow-style jobs collection can hide an entire upload list.
        if re.match(r"^\s*jobs:\s*[\[{*&!]", line):
            raise RuntimeError("unresolved workflow jobs collection")
        # This bounded source scanner intentionally does not resolve YAML
        # quoted mapping keys. Counting a partial set would be fail-open.
        if re.match(
            r"""^\s*(?:-\s+)?(?:['"](?:steps|uses|with|retention-days)['"])\s*:""",
            line,
        ):
            raise RuntimeError("unresolved workflow mapping key")
        # YAML merge maps may inherit 'uses' or 'with' from a hidden anchor.
        # A lexical source audit cannot prove the effective merged identity.
        if re.match(r"""^\s*(?:-\s+)?(?:<<|['"]<<['"])\s*:""", line):
            raise RuntimeError("unresolved YAML merge mapping")
        # Flow-style or aliased step collections cannot be audited by the
        # block-sequence reader below. Reject rather than report zero uploads.
        steps_value = re.match(r"^\s*steps:\s*(.*)$", line)
        if steps_value:
            value = steps_value.group(1).strip()
            if value and not value.startswith("#") and not re.fullmatch(
                r"\[\]\s*(?:#.*)?", value
            ):
                raise RuntimeError("unresolved workflow steps collection")
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
                # YAML also allows indentless sequences at the same depth as
                # their mapping key; do not silently skip those upload steps.
                if indent == section_indent and item_key.match(lines[i][1]):
                    raise RuntimeError("unresolved indentless steps collection")
                break
            item = item_key.match(lines[i][1])
            if item and item_indent is None:
                item_indent = len(item.group(1))
            if not item or indent != item_indent:
                i += 1
                continue
            first = i
            if re.match(r"^\s*-\s*[\[{]", lines[first][1]):
                raise RuntimeError("unresolved flow-style workflow step")
            if re.match(r"^\s*-\s*[&*!]", lines[first][1]):
                raise RuntimeError("unresolved anchored workflow step")
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
    """Count only real upload steps; deny ambiguous YAML instead of guessing."""
    uploads: list[ArtifactUpload] = []
    literal_days = re.compile(
        r"""(?P<quote>['"]?)(?P<days>-?(?:0|[1-9][0-9]*))(?P=quote)(?:\s+#.*)?"""
    )
    for first_line, step, indent in _step_blocks(text):
        uses: list[tuple[int, str]] = []
        with_positions: list[int] = []
        field_indent = indent + 2
        for position, (number, line) in enumerate(step):
            if position == 0:
                candidate = re.match(r"^\s*-\s+uses:\s*(.+?)\s*$", line)
            else:
                candidate = re.match(
                    r"^ {" + str(field_indent) + r"}uses:\s*(.+?)\s*$", line
                )
            if candidate:
                raw_action = candidate.group(1).strip()
                if raw_action.startswith((chr(39), chr(34))):
                    # A YAML comment starts only outside the matched quotes.
                    # An unmatched quote may continue onto a later line.
                    quoted = re.fullmatch(
                        r"""(?P<q>[\x27"])(?P<action>.*)(?P=q)(?:\s+#.*)?""",
                        raw_action,
                    )
                    if quoted is None:
                        raise RuntimeError("unresolved quoted action identity")
                    action = quoted.group("action")
                else:
                    action = re.split(r"\s+#", raw_action, maxsplit=1)[0].strip()
                # A text-only audit cannot resolve YAML aliases, anchors, tags
                # or folded action references. Never certify a partial list.
                if action.startswith(("*", "&", "!", "|", ">", "${{")):
                    raise RuntimeError("unresolved action identity")
                uses.append((number, action))
            if re.match(r"^ {" + str(field_indent) + r"}with:\s*(?:#.*)?$", line):
                with_positions.append(position)

        upload_lines = [
            number for number, action in uses
            if re.fullmatch(r"actions/upload-artifact@[^\s#]+", action, re.IGNORECASE)
        ]
        if not upload_lines:
            continue

        retention: int | None = None
        status = "missing"
        # Duplicate uses/with keys have YAML-loader-dependent interpretation.
        # Neither a clean result nor a guessed last value is evidence.
        if len(uses) != 1 or len(with_positions) > 1:
            status = "dynamic_or_invalid"
        elif with_positions:
            values: list[str] = []
            for _, line in step[with_positions[0] + 1:]:
                depth = len(line) - len(line.lstrip(" "))
                if depth <= field_indent:
                    break
                if depth != field_indent + 2:
                    continue
                match = re.match(r"^\s*retention-days:\s*(.*?)\s*$", line)
                if match:
                    values.append(match.group(1).strip())
            if len(values) > 1:
                status = "dynamic_or_invalid"
            elif values:
                value = literal_days.fullmatch(values[0])
                if value is None:
                    status = "dynamic_or_invalid"
                else:
                    retention = int(value.group("days"))
                    if not 1 <= retention <= 90:
                        status = "invalid"
                    elif retention > max_days:
                        status = "over_limit"
                    else:
                        status = "bounded"
        if len(uploads) >= MAX_ARTIFACT_UPLOADS:
            raise RuntimeError("artifact upload inventory too large")
        uploads.append(
            ArtifactUpload(
                path=path,
                line=upload_lines[0] or first_line,
                retention_days=retention,
                status=status,
            )
        )
    return uploads


def scan_repository(root: Path, *, max_days: int) -> list[ArtifactUpload]:
    """Open workflow files relative to pinned directories, without following links.

    Preflight the complete bounded inventory before parsing. Directory-relative
    file descriptors also close the symlink-swap gap between stat() and read().
    """
    workflows = root / WORKFLOW_DIR
    if (root / ".github").is_symlink() or workflows.is_symlink():
        raise RuntimeError("symlink workflow directory rejected")
    if not workflows.is_dir():
        raise FileNotFoundError(f"workflow directory not found: {workflows}")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(root, directory_flags)
    try:
        github_fd = os.open(".github", directory_flags, dir_fd=root_fd)
        try:
            workflows_fd = os.open("workflows", directory_flags, dir_fd=github_fd)
            try:
                names: list[str] = []
                with os.scandir(workflows_fd) as entries:
                    for entry in entries:
                        names.append(entry.name)
                        if len(names) > MAX_WORKFLOW_ENTRIES:
                            raise RuntimeError("workflow inventory too large")
                selected: list[tuple[str, os.stat_result]] = []
                total_bytes = 0
                for name in sorted(names):
                    if Path(name).suffix.lower() not in WORKFLOW_SUFFIXES:
                        continue
                    identity = os.stat(
                        name, dir_fd=workflows_fd, follow_symlinks=False
                    )
                    if stat.S_ISLNK(identity.st_mode):
                        raise RuntimeError("symlink workflow rejected")
                    if not stat.S_ISREG(identity.st_mode):
                        continue
                    if identity.st_size > MAX_WORKFLOW_BYTES:
                        raise RuntimeError("workflow too large to audit")
                    total_bytes += identity.st_size
                    if total_bytes > MAX_TOTAL_WORKFLOW_BYTES:
                        raise RuntimeError("workflow inventory bytes exceeded")
                    selected.append((name, identity))

                uploads: list[ArtifactUpload] = []
                for name, identity in selected:
                    # O_NONBLOCK avoids hanging on a FIFO swapped into place;
                    # O_NOFOLLOW denies symlinks swapped after inventory.
                    file_fd = os.open(
                        name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                        dir_fd=workflows_fd,
                    )
                    with os.fdopen(file_fd, "r", encoding="utf-8") as stream:
                        before = os.fstat(stream.fileno())
                        if (
                            not stat.S_ISREG(before.st_mode)
                            or (before.st_dev, before.st_ino)
                            != (identity.st_dev, identity.st_ino)
                            or before.st_size > MAX_WORKFLOW_BYTES
                        ):
                            raise RuntimeError("workflow changed during audit")
                        text = stream.read(MAX_WORKFLOW_BYTES + 1)
                        after = os.fstat(stream.fileno())
                        if (
                            len(text.encode("utf-8")) > MAX_WORKFLOW_BYTES
                            or (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size)
                            != (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size)
                        ):
                            raise RuntimeError("workflow changed during audit")
                    uploads.extend(
                        inspect_workflow(
                            (WORKFLOW_DIR / name).as_posix(),
                            text,
                            max_days=max_days,
                        )
                    )
                    if len(uploads) > MAX_ARTIFACT_UPLOADS:
                        raise RuntimeError("artifact upload inventory too large")
                return uploads
            finally:
                os.close(workflows_fd)
        finally:
            os.close(github_fd)
    finally:
        os.close(root_fd)


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
