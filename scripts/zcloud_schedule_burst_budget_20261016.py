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
import unicodedata

POLICY = "zcloud-scheduled-start-burst-v1"
MAX_BLOB = 180_000
MAX_WORKFLOWS = 1000
MAX_SCHEDULE_ENTRIES = 512
MAX_CRON_CHARACTERS = 512
CAP = 4
FIELDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def git(*args):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, timeout=20
    ).stdout


def fields(expr):
    # A single schedule can otherwise contain thousands of comma-list tokens
    # while still counting as only one of the 512 allowed cron entries.
    if len(expr) > MAX_CRON_CHARACTERS:
        raise ValueError("cron expression exceeds bounded lexical budget")
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
    """Parse canonical on.schedule YAML without assuming a fixed indentation.

    Accepted forms are simple literal block mappings. Unknown/aliased/
    flow-style schedule declarations fail closed; non-schedule event payloads
    are not misclassified as schedule entries.
    """
    if document.startswith("\ufeff") or any(
        ord(ch) < 32 and ch not in "\n\r\t" for ch in document
    ):
        raise ValueError("workflow source contains unsafe control characters or BOM")
    lines = document.splitlines()
    in_on, in_schedule, seen_on, seen_schedule = False, False, False, False
    trigger_indent = None
    cron_indent = None
    result = []
    schedule_key = r"""(?:schedule|'schedule'|"schedule")"""
    for original in lines:
        if not original.strip() or original.lstrip().startswith("#"):
            continue
        indentation = len(original) - len(original.lstrip(" "))
        if "\t" in original[:len(original) - len(original.lstrip())]:
            raise ValueError("tab-indented YAML cannot be classified")
        line = original.strip()
        # Double-quoted YAML mapping keys can decode Unicode escapes. Never
        # count an escaped event alias as an unrelated harmless mapping.
        if (
            re.match(r"""^["'][^"']*\\[^"']*["']\s*:""", line)
            or re.match(r"""^-\s+["'][^"']*\\[^"']*["']\s*:""", line)
        ):
            raise ValueError("escaped YAML mapping keys require explicit review")
        if indentation == 0:
            # Explicit mapping forms can spell on without a plain "on:".
            # They cannot be reliably classified by this lexical scanner.
            if line.startswith(("? ", ": ", "!!", "!<")):
                raise ValueError("explicit or tagged top-level YAML key cannot be classified")
            if re.match(r"^<<\s*:", line):
                raise ValueError("top-level YAML merge may conceal scheduled triggers")
            in_schedule = False
            in_on = False
            trigger_indent = None
            cron_indent = None
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
        if trigger_indent is None:
            # YAML permits one, two, four or more spaces for a mapping's
            # first child; subsequent siblings must share its indent.
            trigger_indent = indentation
        if indentation < trigger_indent:
            raise ValueError("inconsistent on trigger indentation")
        if indentation == trigger_indent:
            # A YAML "? schedule" / ":" form can conceal an event key.
            if line.startswith(("? ", ": ", "!!", "!<")):
                raise ValueError("explicit or tagged trigger YAML key cannot be classified")
            if re.match(r"^<<\s*:", line):
                raise ValueError("trigger YAML merge may conceal schedule")
            in_schedule = False
            cron_indent = None
            if re.match(schedule_key + r"\s*:", line):
                if seen_schedule:
                    raise ValueError("duplicate schedule trigger")
                seen_schedule = True
                if not re.fullmatch(schedule_key + r"\s*:\s*(#.*)?", line):
                    raise ValueError("unsupported inline schedule mapping")
                in_schedule = True
            continue
        if in_schedule:
            # GitHub supports per-schedule IANA timezones. This admission
            # model counts UTC minute-of-week only; accepting timezone entries
            # as if they were UTC could hide DST-shifted launch collisions.
            # Deny explicitly until the model can safely normalize zones.
            if re.match(r"""^(?:timezone|'timezone'|"timezone")\s*:""", line):
                raise ValueError(
                    "timezone-aware cron entries require a timezone-safe budget model"
                )
            if cron_indent is None:
                cron_indent = indentation
            if indentation != cron_indent:
                raise ValueError("inconsistent schedule entry indentation")
            match = re.fullmatch(
                r"""-\s+(?:cron|'cron'|"cron")\s*:\s*(.+)""", line
            )
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
    # A 40-hex Git tree or blob object is not a commit. Without object-type
    # admission, a locally supplied tree could be mislabeled as a commit SHA.
    if git("cat-file", "-t", revision).strip() != b"commit":
        raise ValueError("revision object must be an immutable Git commit")
    raw = git("ls-tree", "-r", "-z", revision, "--", ".github/workflows")
    result = {}
    schedule_count = 0
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, path = record.split(b"\t", 1)
        mode, objtype, sha = header.decode("ascii").split(" ")
        name = path.decode("utf-8")
        if not name.startswith(".github/workflows/"):
            continue
        # A Git host may treat workflow file extensions case-insensitively.
        # Never silently skip a file with an ambiguous YAML suffix.
        if name.lower().endswith((".yml", ".yaml")) and not name.endswith((".yml", ".yaml")):
            raise ValueError(f"ambiguous workflow filename extension: {name}")
        if not name.endswith((".yml", ".yaml")):
            continue
        # Filenames are printed in review receipts. Unicode bidi controls
        # or line/paragraph separators can spoof the actual changed source.
        if any(
            unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"}
            for ch in name
        ):
            raise ValueError("workflow filename contains control or formatting characters")
        if name in result:
            raise ValueError("duplicate workflow path in immutable Git source tree")
        if len(result) >= MAX_WORKFLOWS:
            raise ValueError("workflow count exceeds bounded scanner budget")
        if mode != "100644" or objtype != "blob":
            raise ValueError(f"unsupported workflow git mode: {name}")
        # Enforce the immutable Git object's size BEFORE materializing the
        # blob. A large attacker-controlled PR source must not be loaded into
        # the runner's memory merely so it can be rejected afterwards.
        size = int(git("cat-file", "-s", sha).strip())
        if size > MAX_BLOB:
            raise ValueError(f"workflow source exceeds byte budget: {name}")
        blob = git("cat-file", "blob", sha)
        if len(blob) != size:
            raise ValueError(f"workflow immutable blob size mismatch: {name}")
        entries = schedules(blob.decode("utf-8"))
        schedule_count += len(entries)
        if schedule_count > MAX_SCHEDULE_ENTRIES:
            raise ValueError("scheduled cron entry count exceeds bounded scanner budget")
        result[name] = entries
    return result


def require_current_baseline_ancestry(baseline_sha, candidate_sha):
    """Fail closed when the PR head could omit schedules added to main.

    Comparing separate stale trees is not proof of their future merge result.
    The candidate must contain the exact trusted main commit as an ancestor.
    """
    merge_bases = git("merge-base", baseline_sha, candidate_sha).decode("ascii").split()
    if merge_bases != [baseline_sha]:
        raise ValueError(
            "candidate lacks exact main baseline ancestry; refresh branch before admission"
        )


def weekly_slots(cron):
    minute, hour, day, month, weekday = fields(cron)
    # POSIX cron matches either day-of-month OR day-of-week when both are
    # restricted. Even "1-31" or "*/1" are syntactically restricted DOM
    # fields, despite expanding to all 31 numbers. Looking only at the size
    # of expanded DOM values incorrectly narrows such runs to one weekday.
    #
    # Model any explicit DOM constraint as potentially matching EVERY weekday
    # across months/years. Only a literal "*" DOM delegates to the DOW
    # selection. This deliberately overapproximates irregular dates, months,
    # and daylight transitions; it never asserts actual job start telemetry.
    dom_is_literal_wildcard = cron.split()[2] == "*"
    days = weekday if dom_is_literal_wildcard else range(7)
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


def attribute_hotspots(baseline, candidate, slots):
    """Bounded filenames/cron attribution; never return workflow source or tokens.

    Candidate entries absent from the same path/cron multiset on main are
    flagged as changed, including extra duplicate entries in one workflow.
    """
    sampled = set(slots)
    if not sampled:
        return {}
    before_counts = Counter(
        (path, cron)
        for path, crons in baseline.items()
        for cron in crons
    )
    seen = Counter()
    evidence = {
        slot: {"candidate_source_count": 0, "changed_source_count": 0,
               "candidate_sources": [], "new_or_changed_sources": []}
        for slot in slots
    }
    for path in sorted(candidate):
        for cron in candidate[path]:
            identity = (path, cron)
            seen[identity] += 1
            changed = seen[identity] > before_counts[identity]
            for slot in weekly_slots(cron) & sampled:
                item = evidence[slot]
                item["candidate_source_count"] += 1
                if len(item["candidate_sources"]) < 8:
                    item["candidate_sources"].append({"workflow": path, "cron": cron})
                if changed:
                    item["changed_source_count"] += 1
                    if len(item["new_or_changed_sources"]) < 8:
                        item["new_or_changed_sources"].append(
                            {"workflow": path, "cron": cron}
                        )
    return evidence


def compare(baseline, candidate, cap=CAP):
    before, before_events = load(baseline)
    after, after_events = load(candidate)
    growth = [(d, h, m, before[d, h, m], after[d, h, m])
              for (d, h, m), count in after.items()
              if count > max(cap, before[d, h, m])]
    growth.sort(key=lambda x: (-x[4], x[0], x[1], x[2]))
    hotspots = growth[:20]
    evidence = attribute_hotspots(
        baseline, candidate, [(d, h, m) for d, h, m, _, _ in hotspots]
    )
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
                    "baseline": old, "candidate": new,
                    **evidence[(d, h, m)]}
                   for d, h, m, old, new in hotspots],
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
        baseline = read_tree(args.baseline_sha)
        candidate = read_tree(args.candidate_sha)
        require_current_baseline_ancestry(args.baseline_sha, args.candidate_sha)
        result = compare(baseline, candidate, args.burst_limit)
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
