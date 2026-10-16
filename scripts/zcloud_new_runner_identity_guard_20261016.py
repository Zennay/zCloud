#!/usr/bin/env python3
"""Source-only ratchet: deny newly unguarded permanent zCloud VPS runner jobs (#633).

Historical findings are grandfathered per workflow/job ID, not marked resolved.
No hosted result from this tool may authorize a deployment or runner restart.
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys

SHA = re.compile(r"[0-9a-f]{40}\Z")
PATH = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
HOSTED = re.compile(r"(?:ubuntu|windows|macos)-[A-Za-z0-9_.-]+\Z")
JOB = re.compile(r"^  ([A-Za-z_][A-Za-z0-9_-]*):(?:\s*(?:#.*)?)?$")
CHECKOUT = re.compile(r"actions/checkout@[0-9a-f]{40}(?:\s*(?:#.*)?)?$")
GUARD = re.compile(r"python3\s+(?:\./)?scripts/zcloud_vps_runner_guard\.py(?:\s+--json)?\s*(?:#.*)?$")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], timeout=20, stderr=subprocess.DEVNULL)


def inventory(base: str, head: str) -> list[str]:
    for sha in (base, head):
        if not SHA.fullmatch(sha) or git("cat-file", "-t", sha).strip() != b"commit":
            raise ValueError("not_immutable_commit")
    if subprocess.call(["git", "merge-base", "--is-ancestor", base, head],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20):
        raise ValueError("base_not_ancestor")
    paths = [p.decode("utf-8") for p in
             git("diff", "--name-only", "-z", "--diff-filter=ACMRT",
                 base, head, "--", ".github/workflows").split(b"\0") if p]
    if len(paths) > 256 or len(paths) != len(set(paths)) or any(not PATH.fullmatch(p) for p in paths):
        raise ValueError("invalid_diff_inventory")
    return paths


def source(sha: str, path: str) -> str | None:
    if not PATH.fullmatch(path):
        raise ValueError("invalid_source_path")
    try:
        obj = git("ls-tree", sha, "--", path).decode("ascii").strip()
        if not obj:
            return None
        metadata, treepath = obj.split("\t", 1)
        mode, kind, blob = metadata.split(" ")
        if treepath != path or mode not in {"100644", "100755"} or kind != "blob" or not SHA.fullmatch(blob):
            raise ValueError("unsafe_source_object")
        data = git("cat-file", "blob", blob)
        if len(data) > 256_000 or b"\0" in data:
            raise ValueError("oversized_or_binary_workflow")
        return data.decode("utf-8", "strict")
    except subprocess.CalledProcessError as exc:
        raise ValueError("unreadable_git_object") from exc


def _runner(value: str) -> str:
    val = value.split("#", 1)[0].strip().strip("'\"")
    if HOSTED.fullmatch(val):
        return "hosted"
    if "zcloud" in val.lower() or "vps" in val.lower():
        return "needs_guard"
    if val == "self-hosted" or not val or "${{" in val or val.startswith("{"):
        return "needs_guard"
    if "self-hosted" in val.lower():
        return "other_self_hosted"  # dedicated FTMO/other runner has its own policy
    return "needs_guard"


def _early_guard(job: list[str]) -> bool:
    # Only a pinned checkout and shell 'set' may precede the actual guard command.
    if any(re.match(r"^    (?:defaults|container):", line) for line in job):
        return False
    headings = [n for n, line in enumerate(job)
                if re.fullmatch(r"    steps:\s*(?:#.*)?", line)]
    if len(headings) != 1:
        return False
    steps = []
    for line in job[headings[0] + 1:]:
        if re.match(r"^      -\s+", line):
            steps.append([line[8:]])
        elif steps:
            steps[-1].append(line)
    checkout_seen = False
    for lines in steps:
        text = "\n".join(lines)
        uses = re.findall(r"(?m)^\s*uses:\s*([^\n]+)", text)
        runs = re.findall(r"(?m)^\s*run:\s*([^\n]*)", text)
        if len(uses) > 1 or len(runs) > 1 or (uses and runs):
            return False
        if uses:
            if re.search(r"(?m)^\s*(?:if|continue-on-error):", text):
                return False
            if checkout_seen or not CHECKOUT.fullmatch(uses[0].strip()):
                return False
            checkout_seen = True
            continue
        if runs:
            # The identity check may not be skipped or demoted to nonfatal.
            if re.search(r"(?m)^\s*(?:if|continue-on-error):", text):
                return False
            # Folded YAML scripts join lines and do not prove a standalone
            # early executable guard command.
            if not checkout_seen or runs[0].strip() not in {"|", "|-"}:
                return False
            shells = re.findall(r"(?m)^\s*shell:\s*([^\n#]+)", text)
            if any(value.strip().strip("'\"") != "bash" for value in shells):
                return False
            seen_run = False
            for line in lines:
                stripped = line.strip()
                if not seen_run:
                    if re.match(r"^run:\s*", stripped):
                        seen_run = True
                    continue
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped in {"set -euo pipefail", "set -eu"}:
                    continue
                return bool(GUARD.fullmatch(stripped))
            return False
        return False
    return False


def unsafe_jobs(doc: str | None) -> dict[str, str]:
    if doc is None:
        return {}
    # Quoted/tagged/merge root keys can shadow or synthesize jobs invisibly.
    if re.search(r"(?m)^(?:[\"']jobs[\"']|[?!]\s*jobs|<<)\s*:", doc):
        raise ValueError("unsupported_jobs_mapping")
    lines = doc.splitlines()
    # Root defaults.run.shell can override the trusted first-run interpreter.
    workflow_defaults = bool(re.search(r"(?m)^defaults:\s*(?:#.*)?$", doc))
    roots = [i for i, l in enumerate(lines) if re.fullmatch(r"jobs:\s*(?:#.*)?", l)]
    if len(roots) != 1:
        raise ValueError("ambiguous_jobs_root")
    start = roots[0] + 1
    stop = next((i for i in range(start, len(lines)) if lines[i] and not lines[i][0].isspace()), len(lines))
    region = lines[start:stop]
    for line in region:
        # Inline mapping, aliases and quoted job keys cannot vanish from inventory.
        if (line.startswith("  ") and not line.startswith("   ")
                and line.strip() and not line.lstrip().startswith("#")
                and not JOB.fullmatch(line)):
            raise ValueError("unsupported_job_mapping")
    indices = [(i, JOB.fullmatch(l).group(1)) for i, l in enumerate(region) if JOB.fullmatch(l)]
    if not indices or len(indices) > 150 or len({x[1] for x in indices}) != len(indices):
        raise ValueError("ambiguous_job_ids")
    risks = {}
    for n, (index, name) in enumerate(indices):
        body = region[index + 1:indices[n + 1][0] if n + 1 < len(indices) else len(region)]
        runners = [f.group(1) for l in body if (f := re.fullmatch(r"    runs-on:\s*(.*)", l))]
        if not runners:
            if not any(re.match(r"^    uses:\s*", l) for l in body):
                risks[name] = "unknown_runner"
            continue
        if len(runners) != 1:
            risks[name] = "ambiguous_runner"
        elif _runner(runners[0]) not in {"hosted", "other_self_hosted"} and (
            workflow_defaults or not _early_guard(body)
        ):
            risks[name] = "missing_early_runner_guard"
    return risks


def evaluate(base: str, head: str) -> dict:
    paths = inventory(base, head)
    findings, old_count, new_count = [], 0, 0
    for path in paths:
        old = unsafe_jobs(source(base, path))
        new = unsafe_jobs(source(head, path))
        old_count += len(old)
        new_count += len(new)
        for job, reason in sorted(new.items()):
            if job not in old or old[job] != reason:
                findings.append({"path": path, "job": job, "reason": reason})
    return {"source_only": True, "mutation_performed": False, "release_authorized": False,
            "changed_workflows": len(paths), "legacy_debt_in_changed_files": old_count,
            "remaining_debt_in_changed_files": new_count, "new_findings": findings}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    args = p.parse_args(argv)
    try:
        report = evaluate(args.base, args.head)
    except (ValueError, OSError, UnicodeError, subprocess.SubprocessError) as error:
        report = {"source_only": True, "mutation_performed": False, "release_authorized": False,
                  "new_findings": [{"reason": "unverifiable_source", "detail": type(error).__name__}]}
    print(json.dumps(report, sort_keys=True))
    return 1 if report["new_findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
