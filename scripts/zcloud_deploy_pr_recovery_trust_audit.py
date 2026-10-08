#!/usr/bin/env python3
"""Read-only fail-closed PR-to-recovery privilege contract for #1135.

No imports of project runtime, no network calls and no subprocess execution.
This is a static admission signal, not permission to run any recovery job.
"""
import argparse
import json
import pathlib
import re


MUTATING = re.compile(
    r"\b(?:sudo|systemctl\s+(?:stop|restart|start|reset-failed|daemon-reload)|"
    r"chown|chmod|chattr|install\s+-[dm]|BEGIN\s+IMMEDIATE)\b",
    re.IGNORECASE,
)


def audit(text):
    # Deliberately fail closed: unexpected formatting cannot prove isolation.
    lines = text.splitlines()
    triggers = []
    jobs = {}
    section = None
    job = None
    for line in lines:
        if re.match(r"^on:\s*$", line):
            section, job = "on", None
            continue
        if re.match(r"^jobs:\s*$", line):
            section, job = "jobs", None
            continue
        if re.match(r"^[a-zA-Z_][\w-]*:\s*", line):
            section, job = None, None
        if section == "on" and re.match(r"^  (pull_request|pull_request_target):\s*", line):
            triggers.append(line.strip().split(":")[0])
        if section == "jobs":
            m = re.match(r"^  ([\w-]+):\s*$", line)
            if m:
                job = m.group(1)
                jobs[job] = {"hosted": False, "guarded": False, "mutation": False}
            elif job:
                if re.search(r"runs-on:\s*(?:\[?\s*)?ubuntu-latest", line):
                    jobs[job]["hosted"] = True
                if re.search(r"^    if:\s*", line):
                    # Guard alone cannot authorize mutation; only hosted PR checks are accepted.
                    jobs[job]["guarded"] = True
                if MUTATING.search(line) and not line.lstrip().startswith("#"):
                    jobs[job]["mutation"] = True
    problems = []
    if triggers:
        if not jobs:
            problems.append("PR trigger present but job inventory unavailable")
        for name, detail in jobs.items():
            if detail["mutation"] and not detail["hosted"]:
                problems.append("PR-accessible privileged mutation job: " + name)
            elif not detail["hosted"]:
                problems.append("PR-accessible non-hosted job cannot be proven read-only: " + name)
    return {"status": "unsafe" if problems else "clear",
            "pr_triggers": sorted(set(triggers)),
            "jobs": jobs, "violations": problems,
            "mutation_authorized": False, "deploy_authorized": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", default=".github/workflows/zcloud-dashboard-access-recovery.yml")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero if unsafe")
    args = parser.parse_args()
    path = pathlib.Path(args.workflow)
    try:
        report = audit(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        report = {"status": "unknown", "violations": ["workflow unreadable: " + type(exc).__name__],
                  "mutation_authorized": False, "deploy_authorized": False}
    print(json.dumps(report, sort_keys=True))
    return 1 if args.strict and report["status"] != "clear" else 0


if __name__ == "__main__":
    raise SystemExit(main())
