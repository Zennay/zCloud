#!/usr/bin/env python3
"""Audit GitHub Actions artifact retention without mutating repository or runtime state."""

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


def scan_git_ref(root: Path, ref: str, *, max_days: int) -> list[ArtifactUpload]:
    """Read workflow blobs from an existing git commit without checking it out."""
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", ref, "--", WORKFLOW_DIR.as_posix()],
        check=True,
        capture_output=True,
        text=True,
    )
    uploads: list[ArtifactUpload] = []
    for row in proc.stdout.splitlines():
        metadata, sep, relative = row.partition("\t")
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
        text = subprocess.run(
            ["git", "-C", str(root), "show", f"{ref}:{relative}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        uploads.extend(inspect_workflow(relative, text, max_days=max_days))
    return uploads


def _upload_identities(rows: list[ArtifactUpload]) -> list[tuple[tuple[str, int], ArtifactUpload]]:
    """Give each upload a stable per-workflow ordinal for base-vs-head comparison."""
    ordinals: dict[str, int] = {}
    indexed: list[tuple[tuple[str, int], ArtifactUpload]] = []
    for row in rows:
        ordinal = ordinals.get(row.path, 0)
        indexed.append(((row.path, ordinal), row))
        ordinals[row.path] = ordinal + 1
    return indexed


def new_debt(
    base_rows: list[ArtifactUpload],
    head_rows: list[ArtifactUpload],
) -> list[ArtifactUpload]:
    """Return unbounded uploads whose path/ordinal identity was not debt in base."""
    base_debt = {
        identity
        for identity, row in _upload_identities(base_rows)
        if row.status != "bounded"
    }
    return [
        row
        for identity, row in _upload_identities(head_rows)
        if row.status != "bounded" and identity not in base_debt
    ]


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
    parser.add_argument(
        "--base-ref",
        help="Git ref to compare against for monotonic no-new-debt enforcement.",
    )
    parser.add_argument("--require-no-new-debt", action="store_true")
    args = parser.parse_args(argv)

    if not 1 <= args.max_days <= 90:
        parser.error("--max-days must be between 1 and 90")
    if args.require_no_new_debt and not args.base_ref:
        parser.error("--require-no-new-debt requires --base-ref")

    root = args.repo_root.resolve()
    try:
        uploads = scan_repository(root, max_days=args.max_days)
    except (OSError, UnicodeError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 2

    payload = summarize(uploads, max_days=args.max_days)
    payload["ok"] = payload["finding_count"] == 0

    if args.base_ref:
        try:
            base_rows = scan_git_ref(root, args.base_ref, max_days=args.max_days)
        except (OSError, UnicodeError, RuntimeError, subprocess.SubprocessError):
            print(json.dumps({"ok": False, "error": "base_ref_unreadable"}, sort_keys=True))
            return 2
        introduced = new_debt(base_rows, uploads)
        payload["base_ref"] = args.base_ref
        payload["base_upload_count"] = len(base_rows)
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
