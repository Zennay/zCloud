#!/usr/bin/env python3
"""Fail closed when a DB/config migration lacks forward + rollback evidence."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
MAX_PATCH_BYTES = 2 * 1024 * 1024
MIGRATION_DOC_DIR = Path("docs/migrations")
REQUIRED_SECTIONS = ("Scope", "Forward", "Rollback", "Validation")
CANONICAL_CONFIGS = {
    "project-contracts.json",
    "autonomy-policy.json",
    "config-schema-versions.json",
    "projects.json",
    "project-layout.json",
    "resource-policy.json",
}
DDL_PATTERNS = {
    "alter_table": re.compile(r"\bALTER\s+TABLE\b", re.IGNORECASE),
    "create_table": re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE),
    "drop_table": re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
    "rename_table": re.compile(r"\bRENAME\s+TABLE\b", re.IGNORECASE),
    "pragma_user_version": re.compile(r"\bPRAGMA\s+USER_VERSION\b", re.IGNORECASE),
}
MIGRATION_PATH_RE = re.compile(r"(?:^|[._/-])migrat(?:e|ion|ions)(?:[._/-]|$)", re.IGNORECASE)
SCHEMA_VERSION_RE = re.compile(r"""["']schema_version["']\s*[:=]""", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(
    r"^\s*(?:n/?a|none|not applicable|tbd|todo|same as forward|automatic)\s*[.!]?\s*$",
    re.IGNORECASE,
)


class GuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    patch: str


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise GuardError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:500]}")
    return proc.stdout


def _changed_paths(base: str, head: str) -> list[tuple[str, str]]:
    raw = _git("diff", "--name-status", "--no-renames", f"{base}...{head}")
    result: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            raise GuardError("unexpected git name-status output")
        status, path = parts
        result.append((status.strip(), path.strip()))
    return result


def _patch(base: str, head: str, path: str) -> str:
    raw = _git("diff", "--unified=0", "--no-ext-diff", "--no-renames", f"{base}...{head}", "--", path)
    if len(raw.encode("utf-8", errors="replace")) > MAX_PATCH_BYTES:
        raise GuardError(f"diff too large for bounded migration guard: {path}")
    return raw


def collect_changes(base: str, head: str) -> list[Change]:
    return [Change(status, path, _patch(base, head, path)) for status, path in _changed_paths(base, head)]


def _changed_payload_lines(patch: str) -> str:
    lines: list[str] = []
    for line in patch.splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            lines.append(line[1:])
    return "\n".join(lines)


def migration_signals(change: Change) -> list[str]:
    path = Path(change.path)
    if path.parts and path.parts[0] in {"tests", "docs", ".github"}:
        return []

    signals: list[str] = []
    payload = _changed_payload_lines(change.patch)

    if MIGRATION_PATH_RE.search(change.path):
        signals.append("migration_path")

    if path.name in CANONICAL_CONFIGS:
        if path.name == "config-schema-versions.json":
            signals.append("config_version_registry")
        if SCHEMA_VERSION_RE.search(payload):
            signals.append("config_schema_version")

    if path.suffix.lower() in {".py", ".sql"}:
        for name, pattern in DDL_PATTERNS.items():
            if pattern.search(payload):
                signals.append(name)

    return sorted(set(signals))


def _section_body(text: str, heading: str) -> str | None:
    match = re.search(
        rf"(?ms)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)",
        text,
    )
    return match.group(1).strip() if match else None


def validate_migration_record(path: Path) -> list[str]:
    errors: list[str] = []
    if path.is_symlink():
        return [f"{path}: symlink migration record refused"]
    if not path.is_file():
        return [f"{path}: migration record must be a regular file"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: cannot read migration record: {exc}"]

    for section in REQUIRED_SECTIONS:
        body = _section_body(text, section)
        if body is None:
            errors.append(f"{path}: missing ## {section}")
            continue
        if len(body) < 20:
            errors.append(f"{path}: ## {section} must contain substantive evidence")
        if section in {"Forward", "Rollback"} and PLACEHOLDER_RE.match(body):
            errors.append(f"{path}: ## {section} cannot be a placeholder")
    return errors


def assess(changes: Iterable[Change], root: Path = ROOT) -> dict[str, object]:
    changes = list(changes)
    risky: list[dict[str, object]] = []
    records: list[str] = []

    for change in changes:
        signals = migration_signals(change)
        if signals:
            risky.append({"path": change.path, "signals": signals})
        p = Path(change.path)
        if (
            len(p.parts) >= 3
            and Path(*p.parts[:2]) == MIGRATION_DOC_DIR
            and p.suffix.lower() == ".md"
            and p.name.lower() != "readme.md"
        ):
            records.append(change.path)

    errors: list[str] = []
    if risky and not records:
        errors.append(
            "migration-risk change detected without a changed docs/migrations/*.md record"
        )

    for record in records:
        errors.extend(validate_migration_record(root / record))

    return {
        "ok": not errors,
        "migration_risk": bool(risky),
        "risky_changes": risky,
        "migration_records": sorted(records),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = assess(collect_changes(args.base, args.head))
    except GuardError as exc:
        result = {
            "ok": False,
            "migration_risk": None,
            "risky_changes": [],
            "migration_records": [],
            "errors": [str(exc)],
        }

    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["ok"]:
        print("ZCLOUD_MIGRATION_SAFETY_GREEN=1")
        print(f"migration_risk={str(result['migration_risk']).lower()}")
        print(f"records={len(result['migration_records'])}")
    else:
        for error in result["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
