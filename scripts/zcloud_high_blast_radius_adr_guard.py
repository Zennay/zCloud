#!/usr/bin/env python3
"""Require an ADR when a zCloud change touches high-blast-radius surfaces.

This guard is intentionally read-only. It evaluates the candidate diff and,
when a high-blast-radius surface changes, requires a bounded Architecture
Decision Record under docs/adr/. It does not mutate GitHub, runtime state,
SQLite, services, browser automation, or deployment state.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Iterable, NamedTuple

_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_ADR_NAME_RE = re.compile(r"^(?:\d{4,8}|[a-z0-9][a-z0-9-]{2,63})-[a-z0-9][a-z0-9-]{2,80}\.md$")
_REQUIRED_SECTIONS = ("Context", "Decision", "Blast Radius", "Rollback", "Validation")
_PLACEHOLDER_RE = re.compile(r"\b(?:tbd|todo|placeholder|fill me|n/?a)\b", re.IGNORECASE)

_EXACT_HIGH_BLAST = {
    "server.py": "runtime_core",
    "projects.json": "project_registry",
    "project-contracts.json": "project_contract",
    "resource-policy.json": "resource_policy",
    "autonomy-policy.json": "autonomy_policy",
}

_MUTATING_TOKENS = {
    "activate",
    "activation",
    "allocator",
    "deploy",
    "emergency",
    "heal",
    "migration",
    "promotion",
    "reactivation",
    "reconcile",
    "recover",
    "recovery",
    "restart",
    "retry",
    "rollback",
    "scheduler",
    "sweep",
    "watchdog",
}


class AdrGuardError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Risk(NamedTuple):
    path: str
    category: str


def _require_sha(value: str, code: str) -> str:
    value = str(value or "").strip().lower()
    if not _SHA_RE.fullmatch(value):
        raise AdrGuardError(code)
    return value


def _safe_repo_path(raw: str) -> str:
    path = str(raw or "").strip().replace("\\", "/")
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise AdrGuardError("unsafe_changed_path")
    if len(path) > 320:
        raise AdrGuardError("changed_path_too_long")
    return path


def classify_path(raw: str) -> Risk | None:
    path = _safe_repo_path(raw)
    if path in _EXACT_HIGH_BLAST:
        return Risk(path=path, category=_EXACT_HIGH_BLAST[path])

    lower = path.lower()
    name = Path(lower).name.replace("_", "-")
    tokens = {piece for piece in re.split(r"[^a-z0-9]+", name) if piece}

    if lower.startswith(".github/workflows/") and tokens & _MUTATING_TOKENS:
        return Risk(path=path, category="mutating_workflow")
    if lower.startswith("scripts/") and tokens & _MUTATING_TOKENS:
        return Risk(path=path, category="mutating_script")
    return None


def changed_risks(changed_files: Iterable[str]) -> list[Risk]:
    risks: list[Risk] = []
    seen: set[str] = set()
    for raw in changed_files:
        path = _safe_repo_path(raw)
        if path in seen:
            continue
        seen.add(path)
        if len(seen) > 600:
            raise AdrGuardError("too_many_changed_files")
        risk = classify_path(path)
        if risk is not None:
            risks.append(risk)
    return risks


def changed_adr_paths(changed_files: Iterable[str]) -> list[str]:
    result: list[str] = []
    for raw in changed_files:
        path = _safe_repo_path(raw)
        lower = path.lower()
        if not lower.startswith("docs/adr/") or not lower.endswith(".md"):
            continue
        name = Path(lower).name
        if name in {"readme.md", "template.md"}:
            continue
        if not _ADR_NAME_RE.fullmatch(name):
            raise AdrGuardError("invalid_adr_filename")
        if path not in result:
            result.append(path)
    if len(result) > 20:
        raise AdrGuardError("too_many_adr_files")
    return result


def _section_body(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        raise AdrGuardError(f"adr_missing_{heading.lower().replace(' ', '_')}")
    body = match.group("body").strip()
    if len(body) < 20:
        raise AdrGuardError(f"adr_section_too_short_{heading.lower().replace(' ', '_')}")
    if _PLACEHOLDER_RE.search(body):
        raise AdrGuardError(f"adr_placeholder_{heading.lower().replace(' ', '_')}")
    return body


def validate_adr(root: Path, relative_path: str) -> None:
    relative_path = _safe_repo_path(relative_path)
    target = root / relative_path
    if target.is_symlink():
        raise AdrGuardError("adr_symlink_rejected")
    if not target.is_file():
        raise AdrGuardError("adr_file_missing")
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise AdrGuardError("adr_unreadable")
    if len(text) > 60_000:
        raise AdrGuardError("adr_too_large")
    if not text.lstrip().startswith("# ADR"):
        raise AdrGuardError("adr_title_required")
    for heading in _REQUIRED_SECTIONS:
        _section_body(text, heading)


def evaluate(*, root: Path, changed_files: Iterable[str]) -> dict[str, object]:
    files = [_safe_repo_path(item) for item in changed_files]
    risks = changed_risks(files)
    if not risks:
        return {
            "schema_version": 1,
            "policy": "high-blast-radius-adr-v1",
            "decision": "ADR_NOT_REQUIRED",
            "risk_count": 0,
            "risk_categories": [],
            "adr_count": 0,
        }

    adrs = changed_adr_paths(files)
    if not adrs:
        return {
            "schema_version": 1,
            "policy": "high-blast-radius-adr-v1",
            "decision": "ADR_REQUIRED",
            "risk_count": len(risks),
            "risk_categories": sorted({risk.category for risk in risks}),
            "adr_count": 0,
        }

    for path in adrs:
        validate_adr(root, path)

    return {
        "schema_version": 1,
        "policy": "high-blast-radius-adr-v1",
        "decision": "ADR_PRESENT",
        "risk_count": len(risks),
        "risk_categories": sorted({risk.category for risk in risks}),
        "adr_count": len(adrs),
    }


def git_changed_files(base_sha: str, head_sha: str) -> list[str]:
    base_sha = _require_sha(base_sha, "invalid_base_sha")
    head_sha = _require_sha(head_sha, "invalid_head_sha")
    if base_sha == head_sha:
        raise AdrGuardError("head_equals_base")
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRD", f"{base_sha}...{head_sha}"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise AdrGuardError("git_diff_failed")
    files = completed.stdout.splitlines()
    if not files:
        raise AdrGuardError("empty_change_set")
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-covered", action="store_true")
    args = parser.parse_args(argv)

    try:
        files = git_changed_files(args.base_sha, args.head_sha)
        result = evaluate(root=args.root.resolve(), changed_files=files)
    except AdrGuardError as exc:
        result = {"ok": False, "code": exc.code}
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "ZCLOUD_ADR_GUARD "
            f"decision={result['decision']} "
            f"risks={result['risk_count']} "
            f"adrs={result['adr_count']}"
        )

    if args.require_covered and result["decision"] == "ADR_REQUIRED":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
