#!/usr/bin/env python3
"""Git-only advisory ratchet: prevent growth of scheduled Actions start-time bursts.

No network, API tokens, workflow execution, VPS access, or production authority.
This is a conservative weekly *potential* overlap model, not actual run telemetry.
"""
import argparse
from collections import Counter
import json
import re
import subprocess
import sys

POLICY = "zcloud-scheduled-start-burst-v1"
MAX_BLOB = 180_000
MAX_WORKFLOWS = 1000
CAP = 4
FIELDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def git(*args):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, timeout=20
    ).stdout


def fields(expr):
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("expected exactly five cron fields")
    return tuple(expand(p, *bounds, dow=(i == 4))
                 for i, (p, bounds) in enumerate(zip(parts, FIELDS)))


def expand(source, lo, hi, dow=False):
    values = set()
    if not re.fullmatch(r"[0-9*/,-]+", source):
        raise ValueError("unsupported cron expression")
    for item in source.split(","):
        if not item:
            raise ValueError("empty cron list entry")
        chunks = item.split("/")
        if len(chunks) > 2:
            raise ValueError("invalid cron step")
        step = int(chunks[1]) if len(chunks) == 2 and chunks[1].isdigit() else 1
        if step < 1 or (len(chunks) == 2 and not chunks[1].isdigit()):
            raise ValueError("invalid cron step")
        first = chunks[0]
        if first == "*":
            start, end = lo, hi
        elif "-" in first:
            bounds = first.split("-")
            if len(bounds) != 2 or not all(s.isdigit() for s in bounds):
                raise ValueError("invalid cron range")
            start, end = map(int, bounds)
        elif first.isdigit():
            start = int(first)
            end = hi if len(chunks) == 2 else start
        else:
            raise ValueError("invalid cron item")
        if start < lo or end > hi or start > end:
            raise ValueError("cron item out of range")
        values.update(range(start, end + 1, step))
    if dow and 7 in values:
        values.remove(7)
        values.add(0)
    return frozenset(values)


def schedules(document):
    """Read only canonical top-level on.schedule sequence; fail on ambiguity."""
    lines = document.splitlines()
    in_on, in_schedule, seen_on, seen_schedule = False, False, False, False
    result = []
    for original in lines:
        if not original.strip() or original.lstrip().startswith("#"):
            continue
        indentation = len(original) - len(original.lstrip(" "))
        if "\t" in original[:len(original) - len(original.lstrip())]:
            raise ValueError("tab-indented YAML cannot be classified")
        line = original.strip()
        if indentation == 0:
            if re.match(r"^<<\\s*:", line):
                raise ValueError("top-level YAML merge may conceal scheduled triggers")
            in_schedule = False
            in_on = False
            if re.match(r"^(['\"]?on['\"]?)\s*:", line):
                if seen_on:
                    raise ValueError("duplicate on trigger mapping")
                seen_on = True
                if not re.fullmatch(r"(['\"]?on['\"]?)\s*:\s*(#.*)?", line):
                    raise ValueError("inline or aliased on trigger mapping")
                in_on = True
            continue
        if not in_on:
            continue
        if indentation == 2:
            if re.match(r"^<<\\s*:", line):
                raise ValueError("trigger YAML merge may conceal schedule")
            in_schedule = False
            if re.match(r"^schedule\s*:", line):
                if seen_schedule:
                    raise ValueError("duplicate schedule trigger")
                seen_schedule = True
                if not re.fullmatch(r"schedule\s*:\s*(#.*)?", line):
                    raise ValueError("unsupported inline schedule mapping")
                in_schedule = True
            continue
        if in_schedule:
            if indentation != 4:
                raise ValueError("unrecognized schedule nesting")
            match = re.fullmatch(r"-\s+cron:\s*(.+)", line)
            if not match:
                raise ValueError("unsupported scheduled event entry")
            token = match.group(1).split(" #", 1)[0].strip()
            if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
                token = token[1:-1]
            fields(token)
            result.append(token)
    if seen_schedule and not result:
        raise ValueError("schedule trigger has no literal cron entries")
    return result


def read_tree(revision):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be an immutable lowercase commit SHA")
    raw = git("ls-tree", "-r", "-z", revision, "--", ".github/workflows")
    result = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, path = record.split(b"\t", 1)
        mode, objtype, sha = header.decode("ascii").split(" ")
        name = path.decode("utf-8")
        if not name.startswith(".github/workflows/") or not name.endswith((".yml", ".yaml")):
            continue
        if len(result) >= MAX_WORKFLOWS:
            raise ValueError("workflow count exceeds bounded scanner budget")
        if mode != "100644" or objtype != "blob":
            raise ValueError(f"unsupported workflow git mode: {name}")
        blob = git("cat-file", "blob", sha)
        if len(blob) > MAX_BLOB:
            raise ValueError(f"workflow source exceeds byte budget: {name}")
        result[name] = schedules(blob.decode("utf-8"))
    return result


def weekly_slots(cron):
    minute, hour, day, month, weekday = fields(cron)
    # Dates/months are conservatively overapproximated: every chosen day of
    # month could coincide with any weekday in a later year. Does not claim
    # actual execution timing or GitHub's scheduler reliability.
    days = range(7) if len(day) != 31 else weekday
    return {(d, h, m) for d in days for h in hour for m in minute}


def load(manifest):
    density = Counter()
    events = 0
    for path, crons in manifest.items():
        for expr in crons:
            events += 1
            # Each scheduled entry is counted even within the same workflow.
            density.update(weekly_slots(expr))
    return density, events


def compare(baseline, candidate, cap=CAP):
    before, before_events = load(baseline)
    after, after_events = load(candidate)
    growth = [(d, h, m, before[d, h, m], after[d, h, m])
              for (d, h, m), count in after.items()
              if count > max(cap, before[d, h, m])]
    growth.sort(key=lambda x: (-x[4], x[0], x[1], x[2]))
    return {
        "policy": POLICY,
        "baseline_workflows": len(baseline),
        "candidate_workflows": len(candidate),
        "baseline_crons": before_events,
        "candidate_crons": after_events,
        "baseline_peak": max(before.values(), default=0),
        "candidate_peak": max(after.values(), default=0),
        "burst_limit": cap,
        "new_over_budget_slots": len(growth),
        "sample": [{"weekday_sunday_zero": d, "hour_utc": h, "minute_utc": m,
                    "baseline": old, "candidate": new}
                   for d, h, m, old, new in growth[:20]],
        "observation_only": True,
        "mutation_performed": False,
        "deploy_authorized": False,
        "model": "potential-minute-of-week; day-of-month/month conservatively overapproximated",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-sha", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--burst-limit", type=int, default=CAP)
    args = parser.parse_args()
    try:
        if not 1 <= args.burst_limit <= 50:
            raise ValueError("burst limit outside 1..50")
        result = compare(
            read_tree(args.baseline_sha), read_tree(args.candidate_sha),
            args.burst_limit)
        result.update(baseline_sha=args.baseline_sha, candidate_sha=args.candidate_sha)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as error:
        print(json.dumps({"policy": POLICY, "error": str(error),
                          "observation_only": True, "mutation_performed": False,
                          "deploy_authorized": False}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 1 if result["new_over_budget_slots"] else 0


if __name__ == "__main__":
    sys.exit(main())
