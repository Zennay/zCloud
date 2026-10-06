#!/usr/bin/env python3
"""Read-only inventory of GitHub Actions dependency references.

This tool intentionally does not gate existing workflow debt. It makes the
current dependency surface measurable so remediation can be prioritized
without overlapping the PR-diff security gate.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable

USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^'\"\s#]+)")
SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def _workflow_files(root: Path) -> list[Path]:
    if root.is_symlink():
        raise ValueError(f"workflow root must not be a symlink: {root}")
    if not root.exists() or not root.is_dir():
        raise ValueError(f"workflow root is not a directory: {root}")

    files: list[Path] = []
    for path in sorted(root.glob("*.y*ml")):
        if path.is_symlink():
            raise ValueError(f"workflow file must not be a symlink: {path}")
        if path.is_file():
            files.append(path)
    return files


def _classify(spec: str) -> tuple[str, str | None]:
    if spec.startswith("./"):
        return "local", None
    if spec.startswith("docker://"):
        return "docker", None
    if "@" not in spec:
        return "invalid_external", None

    action, ref = spec.rsplit("@", 1)
    if not action or not ref:
        return "invalid_external", None
    if SHA40_RE.fullmatch(ref):
        return "pinned_sha", ref.lower()
    return "floating", ref


def inventory(root: Path) -> dict:
    workflows = _workflow_files(root)
    entries: list[dict] = []
    counts = {
        "workflows": len(workflows),
        "uses_total": 0,
        "external_total": 0,
        "pinned_sha": 0,
        "floating": 0,
        "local": 0,
        "docker": 0,
        "invalid_external": 0,
    }

    for path in workflows:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = USES_RE.match(line)
            if not match:
                continue
            spec = match.group(1)
            kind, ref = _classify(spec)
            counts["uses_total"] += 1
            counts[kind] += 1
            if kind in {"pinned_sha", "floating", "invalid_external"}:
                counts["external_total"] += 1

            action = spec.rsplit("@", 1)[0] if "@" in spec else spec
            entries.append(
                {
                    "workflow": path.name,
                    "line": line_no,
                    "action": action,
                    "kind": kind,
                    "ref": ref,
                }
            )

    floating = [
        entry
        for entry in entries
        if entry["kind"] in {"floating", "invalid_external"}
    ]
    floating.sort(key=lambda item: (item["workflow"], item["line"], item["action"]))

    return {
        "schema_version": 1,
        "root": str(root),
        "counts": counts,
        "floating_dependencies": floating,
    }


def _render_text(report: dict) -> str:
    counts = report["counts"]
    lines = [
        "zCloud workflow dependency inventory",
        f"workflows={counts['workflows']}",
        f"uses_total={counts['uses_total']}",
        f"external_total={counts['external_total']}",
        f"pinned_sha={counts['pinned_sha']}",
        f"floating={counts['floating']}",
        f"local={counts['local']}",
        f"docker={counts['docker']}",
        f"invalid_external={counts['invalid_external']}",
    ]
    for item in report["floating_dependencies"]:
        ref = item["ref"] or "<missing>"
        lines.append(
            f"{item['workflow']}:{item['line']} {item['action']}@{ref} [{item['kind']}]"
        )
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(".github/workflows"),
        help="Workflow directory to scan (default: .github/workflows)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = inventory(args.root)
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(_render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
