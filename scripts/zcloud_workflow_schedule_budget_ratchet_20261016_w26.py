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
MONTHS = dict(zip("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), range(1, 13)))
WEEKDAYS = dict(zip("SUN MON TUE WED THU FRI SAT".split(), range(7)))
SAFE_INLINE_EVENTS = re.compile(r"^(?:push|pull_request|workflow_dispatch|workflow_call|workflow_run|issues|issue_comment|release)$")


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


def field_values(text: str, low: int, high: int, names: dict[str, int] | None = None) -> set[int]:
    if names:
        text = re.sub(r"[A-Za-z]{3}", lambda m: str(names[m.group(0).upper()])
                      if m.group(0).upper() in names else m.group(0), text)
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
    field_values(parts[3], 1, 12, MONTHS)
    field_values(parts[4], 0, 7, WEEKDAYS)
    times = sorted(60 * hour + minute for hour in hours for minute in minutes)
    if not times:
        raise AdmissionError("empty_cron_times")
    circular = times + [times[0] + 1440]
    return any(b - a < MIN_GAP for a, b in zip(circular, circular[1:]))


def schedules(source: str, *, timezone_metadata: list[str] | None = None) -> tuple[str, ...]:
    if len(source.encode("utf-8")) > LIMIT_BYTES:
        raise AdmissionError("workflow_source_oversized")
    entries: list[str] = []
    in_on = False
    in_schedule = False
    found_schedule = False
    event_indent: int | None = None
    for line in source.splitlines():
        if TOP_LEVEL_RE.match(line):
            key = line.partition(":")[0].strip().strip("'\"")
            in_on = key == "on"
            in_schedule = False
            event_indent = None
            if in_on and line.partition(":")[2].strip() not in ("", "# workflow events"):
                # Inline aliases, maps, YAML tags or schedule directives could
                # hide a cron job. Only simple event names/lists are exempt.
                raw_event = line.partition(":")[2].split(" #", 1)[0].strip()
                if raw_event.startswith("[") and raw_event.endswith("]"):
                    events = [event.strip().strip("'\\\"") for event in raw_event[1:-1].split(",")]
                else:
                    events = [raw_event.strip("'\\\"")]
                if not events or any(not SAFE_INLINE_EVENTS.fullmatch(event) for event in events):
                    raise AdmissionError("ambiguous_inline_on")
                in_on = False
            continue
        if not in_on or not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0 or "\t" in line[:indent]:
            raise AdmissionError("ambiguous_event_indentation")
        if event_indent is None:
            event_indent = indent
        if indent < event_indent:
            raise AdmissionError("ambiguous_event_indentation")
        if indent == event_indent:
            # YAML merge keys can inject a scheduled trigger from an alias
            # without spelling out schedule here; refuse this trust bypass.
            if line.lstrip().startswith("<<:") or line.lstrip().startswith("&"):
                raise AdmissionError("yaml_event_merge_forbidden")
            match = re.fullmatch(r"\s*schedule\s*:\s*(.*?)\s*", line)
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
        # GitHub supports IANA timezone selectors, including DST. Never infer
        # UTC proximity from them when a schedule is added or changed.
        if re.fullmatch(r"\s*timezone\s*:\s*.+", line):
            if timezone_metadata is not None:
                timezone_metadata.append(line.strip())
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
    if (root / ".github").is_symlink() or directory.is_symlink():
        raise AdmissionError("symlinked_workflow_directory")
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



def _compiled_cron(cron: str) -> dict:
    """Normalized, bounded POSIX cron fields without invoking repository code."""
    parts = cron.split()
    if len(parts) != 5:
        raise AdmissionError("cron_requires_five_fields")
    minute, hour, dom, month, weekday = parts
    minutes = field_values(minute, 0, 59)
    hours = field_values(hour, 0, 23)
    weekdays = {day % 7 for day in field_values(weekday, 0, 7, WEEKDAYS)}
    return {
        "times": sorted(hour * 60 + minute for hour in hours for minute in minutes),
        "dom": field_values(dom, 1, 31),
        "month": field_values(month, 1, 12, MONTHS),
        "dow": weekdays,
        "dom_wild": dom.startswith("*"),
        "dow_wild": weekday.startswith("*"),
    }


def _calendar_matches(spec: dict, month: int, day: int, weekday: int) -> bool:
    if month not in spec["month"]:
        return False
    dom = day in spec["dom"]
    dow = weekday in spec["dow"]
    # POSIX cron: if both day fields are restricted, either can match.
    if not spec["dom_wild"] and not spec["dow_wild"]:
        return dom or dow
    return dom and dow


def _calendar_overlap(a: dict, b: dict, *, next_day: bool) -> bool:
    """Exact existence check over the 400-year Gregorian weekday cycle.

    Every valid month/day appears on all seven weekdays within the cycle.
    February 28 has two legal successors (leap / ordinary year).
    """
    month_days = (0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    for month in range(1, 13):
        if month not in a["month"]:
            continue
        for day in range(1, month_days[month] + 1):
            if next_day:
                if month == 2 and day == 28:
                    successors = ((2, 29), (3, 1))
                elif day == month_days[month]:
                    successors = (((month % 12) + 1, 1),)
                else:
                    successors = ((month, day + 1),)
            else:
                successors = ((month, day),)
            for weekday in range(7):
                if not _calendar_matches(a, month, day, weekday):
                    continue
                for follow_month, follow_day in successors:
                    follow_weekday = (weekday + int(next_day)) % 7
                    if _calendar_matches(b, follow_month, follow_day, follow_weekday):
                        return True
    return False


def _times_near(a: list[int], b: list[int], *, offset: int = 0) -> bool:
    """Linear scan detects strictly sub-15-minute gaps (including ties)."""
    i = j = 0
    while i < len(a) and j < len(b):
        gap = a[i] - (b[j] + offset)
        if abs(gap) < MIN_GAP:
            return True
        if gap < 0:
            i += 1
        else:
            j += 1
    return False


def cross_schedule_bursts(expressions: tuple[str, ...]) -> set[tuple[str, str]]:
    """Pairs of individually acceptable crons that together oversubscribe a workflow."""
    if len(expressions) > 32:
        raise AdmissionError("schedule_entry_budget_exceeded")
    compiled = [_compiled_cron(cron) for cron in expressions]
    findings: set[tuple[str, str]] = set()
    for i, left in enumerate(compiled):
        for j in range(i + 1, len(compiled)):
            right = compiled[j]
            adjacent = (
                _times_near(left["times"], right["times"])
                and _calendar_overlap(left, right, next_day=False)
            )
            # A 23:55 run on day D + 00:05 on D+1 is also a 10-min burst.
            forward = (
                _times_near(left["times"], right["times"], offset=1440)
                and _calendar_overlap(left, right, next_day=True)
            )
            reverse = (
                _times_near(right["times"], left["times"], offset=1440)
                and _calendar_overlap(right, left, next_day=True)
            )
            if adjacent or forward or reverse:
                findings.add(tuple(sorted((expressions[i], expressions[j]))))
    return findings


def audit(repo: Path, baseline_sha: str) -> dict:
    if not SHA_RE.fullmatch(baseline_sha):
        raise AdmissionError("invalid_baseline_sha")
    paths = workflow_paths(repo)
    old = set(git(repo, "ls-tree", "-r", "--name-only", baseline_sha, "--", WORKFLOW_DIR).splitlines())
    added: list[dict[str, str]] = []
    existing: list[dict[str, str]] = []
    count = 0
    for path in paths:
        old_timezones: list[str] = []
        new_timezones: list[str] = []
        if path not in old:
            old_entries: tuple[str, ...] = ()
        else:
            old_entries = schedules(
                git(repo, "show", f"{baseline_sha}:{path}"),
                timezone_metadata=old_timezones,
            )
        before = set(old_entries)
        after = schedules(
            (repo / path).read_text(encoding="utf-8"),
            timezone_metadata=new_timezones,
        )
        # An unchanged legacy timezone is observationally grandfathered.
        # Altered cron/timezone combinations require a separate DST-aware
        # review: nominal wall-clock matches do not prove UTC spacing.
        if new_timezones and (old_timezones != new_timezones or old_entries != after):
            count += 1
            added.append({"workflow": path, "cron": "<timezone schedule change>",
                          "reason": "timezone_requires_independent_review"})
        # A workflow can schedule the same job from multiple cron entries:
        # independently hourly 10:00 and 10:05 still creates a burst.
        prior_pairs = cross_schedule_bursts(old_entries)
        candidate_pairs = cross_schedule_bursts(after)
        for pair in sorted(candidate_pairs):
            count += 1
            finding = {"workflow": path, "cron": " | ".join(pair),
                       "reason": "cross_schedule_burst"}
            (existing if pair in prior_pairs else added).append(finding)
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
