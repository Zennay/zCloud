#!/usr/bin/env python3
"""Fail closed on newly introduced sub-15-minute scheduled GitHub Actions jobs.

This is a source-only, no-new-debt audit. Existing schedules are grandfathered
by their exact (workflow path, cron expression) identity. Nothing runs on VPS.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

WORKFLOW_DIR = ".github/workflows"
LIMIT_BYTES = 256_000
CRON_RE = re.compile(r"^\s*-\s*cron\s*:\s*(.+?)\s*$")
SCHEDULE_RE = re.compile(r"^\s{2}schedule\s*:\s*(.*?)\s*$")
TOP_LEVEL_RE = re.compile(r"^[^\s#][^:]*\s*:")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MIN_GAP = 15


class AdmissionError(ValueError):
    """Untrusted or ambiguous workflow schedule source."""


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if result.returncode:
        raise AdmissionError("git_source_unavailable")
    return result.stdout.strip()


def field_values(text: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            raise AdmissionError("invalid_cron_field")
        if "/" in token:
            stem, step_text = token.split("/", 1)
            if not step_text.isdecimal():
                raise AdmissionError("invalid_cron_step")
            step = int(step_text)
            if not (1 <= step <= high - low + 1):
                raise AdmissionError("invalid_cron_step")
        else:
            stem, step = token, 1
        if stem == "*":
            start, end = low, high
        elif "-" in stem:
            bounds = stem.split("-")
            if len(bounds) != 2 or not all(x.isdecimal() for x in bounds):
                raise AdmissionError("invalid_cron_range")
            start, end = map(int, bounds)
        elif stem.isdecimal():
            start = int(stem)
            # GitHub cron 5/10 means 5,15,25,... (from 5 to end).
            end = high if "/" in token else start
        else:
            raise AdmissionError("unsupported_cron_field")
        if not (low <= start <= end <= high):
            raise AdmissionError("cron_field_out_of_range")
        values.update(range(start, end + 1, step))
    return values


def is_high_frequency(cron: str) -> bool:
    parts = cron.split()
    if len(parts) != 5:
        raise AdmissionError("cron_requires_five_fields")
    minutes = field_values(parts[0], 0, 59)
    hours = field_values(parts[1], 0, 23)
    # Validate remaining fields enough to reject nonliteral or macro syntax.
    field_values(parts[2], 1, 31)
    field_values(parts[3], 1, 12)
    field_values(parts[4], 0, 6)
    times = sorted(60 * hour + minute for hour in hours for minute in minutes)
    if not times:
        raise AdmissionError("empty_cron_times")
    circular = times + [times[0] + 1440]
    return any(b - a < MIN_GAP for a, b in zip(circular, circular[1:]))


def schedules(source: str) -> tuple[str, ...]:
    if len(source.encode("utf-8")) > LIMIT_BYTES:
        raise AdmissionError("workflow_source_oversized")
    entries: list[str] = []
    in_on = False
    in_schedule = False
    found_schedule = False
    for line in source.splitlines():
        if TOP_LEVEL_RE.match(line):
            key = line.partition(":")[0].strip().strip("'\"")
            in_on = key == "on"
            in_schedule = False
            if in_on and line.partition(":")[2].strip() not in ("", "# workflow events"):
                # Simple inline triggers are valid but have no cron settings.
                # Inline YAML mentioning schedule cannot be safely parsed.
                if "schedule" in line.partition(":")[2]:
                    raise AdmissionError("ambiguous_inline_schedule")
                in_on = False
            continue
        if not in_on or not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  ") and not line.startswith("    "):
            match = SCHEDULE_RE.match(line)
            if match:
                if found_schedule or match.group(1):
                    raise AdmissionError("ambiguous_schedule_block")
                found_schedule = True
                in_schedule = True
            else:
                in_schedule = False
            continue
        if not in_schedule:
            continue
        match = CRON_RE.match(line)
        if match:
            scalar = match.group(1).strip()
            if scalar[0:1] in ("'", '"'):
                quote = scalar[0]
                if len(scalar) < 2 or scalar[-1] != quote:
                    raise AdmissionError("ambiguous_cron_scalar")
                scalar = scalar[1:-1]
            else:
                scalar = scalar.split(" #", 1)[0].strip()
            if not scalar or "${{" in scalar:
                raise AdmissionError("dynamic_cron_forbidden")
            entries.append(scalar)
        elif line.strip().startswith("-") or "cron:" in line:
            raise AdmissionError("unsupported_schedule_entry")
    if found_schedule and not entries:
        raise AdmissionError("empty_or_ambiguous_schedule")
    return tuple(entries)


def workflow_paths(root: Path) -> list[str]:
    directory = root / WORKFLOW_DIR
    if not directory.is_dir():
        raise AdmissionError("workflow_directory_missing")
    paths = []
    for entry in sorted(directory.iterdir()):
        if entry.suffix not in (".yml", ".yaml"):
            continue
        if entry.is_symlink() or not entry.is_file():
            raise AdmissionError("nonregular_workflow_source")
        paths.append(entry.relative_to(root).as_posix())
    return paths


def audit(repo: Path, baseline_sha: str) -> dict:
    if not SHA_RE.fullmatch(baseline_sha):
        raise AdmissionError("invalid_baseline_sha")
    paths = workflow_paths(repo)
    old = set(git(repo, "ls-tree", "-r", "--name-only", baseline_sha, "--", WORKFLOW_DIR).splitlines())
    added: list[dict[str, str]] = []
    existing: list[dict[str, str]] = []
    count = 0
    for path in paths:
        if path not in old:
            before: set[str] = set()
        else:
            before = set(schedules(git(repo, "show", f"{baseline_sha}:{path}")))
        after = schedules((repo / path).read_text(encoding="utf-8"))
        for cron in after:
            if is_high_frequency(cron):
                count += 1
                finding = {"workflow": path, "cron": cron}
                (existing if cron in before else added).append(finding)
    return {
        "policy": "zcloud-scheduled-workflow-no-new-debt-v1",
        "baseline_sha": baseline_sha,
        "workflow_count": len(paths),
        "existing_high_frequency": sorted(existing, key=lambda x: (x["workflow"], x["cron"])),
        "new_high_frequency": sorted(added, key=lambda x: (x["workflow"], x["cron"])),
        "total_high_frequency": count,
        "merge_authorized": False,
        "production_mutation_authorized": False,
        "mutation_performed": False,
        "accepted": not added,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--baseline-sha", required=True)
    args = parser.parse_args()
    try:
        report = audit(args.repo_root.resolve(), args.baseline_sha)
    except (AdmissionError, OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({
            "policy": "zcloud-scheduled-workflow-no-new-debt-v1",
            "accepted": False, "reason": type(exc).__name__,
            "merge_authorized": False, "production_mutation_authorized": False,
            "mutation_performed": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
