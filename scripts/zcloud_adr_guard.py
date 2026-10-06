#!/usr/bin/env python3
"""Require a versioned ADR when a PR changes high-blast zCloud architecture."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from zcloud_transactional_promote import promotion_blast_radius


ADR_PATH_RE = re.compile(r"^docs/adr/(?P<number>\d{4})-[a-z0-9][a-z0-9-]*\.md$")
PRODUCTION_CONFIG_PATHS = {
    "projects.json",
    "project-layout.json",
    "resource-policy.json",
    "project-contracts.json",
    "autonomy-policy.json",
    "vps-execution-policy.json",
    "portfolio_queue.seed.json",
}
PRODUCTION_ROOT_PATHS = {
    "server.py",
    "enhancements.py",
    "project_runtime.py",
    "lane_generator.py",
}
PRODUCTION_PREFIXES = (
    "scripts/",
    "deploy/",
    "firefox-extension/",
    "public/",
)
REQUIRED_HEADINGS = (
    "## Status",
    "## Context",
    "## Decision",
    "## Consequences",
    "## Evidence",
    "## Rollback",
)
ALLOWED_STATUSES = {"Proposed", "Accepted", "Superseded", "Rejected"}


class AdrGuardError(RuntimeError):
    pass


def normalize_path(value: str) -> str:
    path = str(value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if not path or path.startswith("/") or ".." in Path(path).parts:
        raise AdrGuardError(f"invalid changed path {value!r}")
    return path


def production_paths(paths: list[str]) -> list[str]:
    selected = []
    for raw in paths:
        path = normalize_path(raw)
        if (
            path in PRODUCTION_CONFIG_PATHS
            or path in PRODUCTION_ROOT_PATHS
            or path.startswith(PRODUCTION_PREFIXES)
        ):
            selected.append(path)
    return selected


def adr_paths(paths: list[str]) -> list[str]:
    result = []
    for raw in paths:
        path = normalize_path(raw)
        if ADR_PATH_RE.fullmatch(path):
            result.append(path)
    return sorted(set(result))


def changed_paths(base_sha: str, head_sha: str, root: Path = ROOT) -> list[str]:
    if not str(base_sha or "").strip() or not str(head_sha or "").strip():
        raise AdrGuardError("base and head SHA are required")
    proc = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            str(base_sha).strip(),
            str(head_sha).strip(),
            "--",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git diff failed").strip()
        raise AdrGuardError(f"cannot determine changed paths: {detail[:1200]}")
    return [
        normalize_path(line)
        for line in proc.stdout.splitlines()
        if line.strip()
    ]


def _section_value(text: str, heading: str) -> str:
    marker = heading + "\n"
    if marker not in text:
        return ""
    tail = text.split(marker, 1)[1]
    section = tail.split("\n## ", 1)[0]
    return section.strip()


def validate_adr(path: Path, relpath: str) -> dict:
    match = ADR_PATH_RE.fullmatch(relpath)
    if not match:
        raise AdrGuardError(f"{relpath}: ADR filename must be docs/adr/NNNN-slug.md")
    if not path.is_file():
        raise AdrGuardError(f"{relpath}: ADR file missing from candidate")

    text = path.read_text(encoding="utf-8")
    number = match.group("number")
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    if not re.fullmatch(rf"# ADR-{number}: .+", first):
        raise AdrGuardError(
            f"{relpath}: first heading must be '# ADR-{number}: <decision title>'"
        )
    for heading in REQUIRED_HEADINGS:
        if heading not in text:
            raise AdrGuardError(f"{relpath}: missing required heading {heading!r}")
        if not _section_value(text, heading):
            raise AdrGuardError(f"{relpath}: empty required section {heading!r}")

    status = _section_value(text, "## Status").splitlines()[0].strip()
    if status not in ALLOWED_STATUSES:
        raise AdrGuardError(
            f"{relpath}: status must be one of {sorted(ALLOWED_STATUSES)}"
        )
    return {"path": relpath, "number": int(number), "status": status}


def evaluate(paths: list[str], root: Path = ROOT) -> dict:
    normalized = [normalize_path(path) for path in paths]
    production = production_paths(normalized)
    blast = promotion_blast_radius(production)
    adrs = adr_paths(normalized)
    validated = []

    if blast["high"]:
        if not adrs:
            reasons = ",".join(blast["reasons"]) or "high_blast_radius"
            raise AdrGuardError(
                f"high-blast architecture change requires a versioned ADR ({reasons})"
            )
        validated = [validate_adr(root / relpath, relpath) for relpath in adrs]

    return {
        "ok": True,
        "high_blast": bool(blast["high"]),
        "blast_radius": blast,
        "production_paths": production,
        "adr_paths": adrs,
        "validated_adrs": validated,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Require a versioned ADR for high-blast zCloud architecture changes"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--path", action="append", dest="paths")
    source.add_argument("--base-sha")
    parser.add_argument("--head-sha")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        root = args.root.resolve()
        if args.paths is not None:
            paths = args.paths
        else:
            if not args.head_sha:
                raise AdrGuardError("--head-sha is required with --base-sha")
            paths = changed_paths(args.base_sha, args.head_sha, root)
        result = evaluate(paths, root)
    except AdrGuardError as exc:
        payload = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"ADR_GUARD_BLOCKED: {exc}")
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        state = "high-blast-with-adr" if result["high_blast"] else "low-blast"
        print(f"ADR_GUARD_GREEN state={state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
