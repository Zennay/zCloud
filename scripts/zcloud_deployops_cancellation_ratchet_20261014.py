#!/usr/bin/env python3
"""Read-only Git source ratchet: deny cancellation of possible VPS jobs.

This is not a deployment or release authorization; no HTTP/Actions/VPS/DB writes.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
HOSTED = re.compile(r"(?:ubuntu-(?:latest|22\.04|24\.04(?:-arm)?)|windows-(?:latest|2022|2025)|macos-(?:latest|13|14|15|26))\Z")
RUNNER = re.compile(r"(?m)^    runs-on:\s*([^\r\n#]*)")
JOB = re.compile(r"(?m)^  ([A-Za-z_][A-Za-z0-9_-]*):\s*$")


class Unverifiable(ValueError):
    pass


def git(root, *args):
    return subprocess.check_output(
        ["git", *args], cwd=root, timeout=15, stderr=subprocess.DEVNULL
    )


def changed(root, base, head):
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise Unverifiable("sha")
    if git(root, "merge-base", base, head).decode("ascii", "strict").strip() != base:
        raise Unverifiable("base_not_ancestor")
    raw = git(root, "diff", "--name-only", "-z", "--diff-filter=ACMRT",
              f"{base}...{head}", "--", ".github/workflows/")
    paths = [part.decode("utf-8", "strict") for part in raw.split(b"\0") if part]
    if len(paths) > 250 or len(paths) != len(set(paths)) or any(
        not WORKFLOW.fullmatch(path) for path in paths
    ):
        raise Unverifiable("paths")
    return paths


def source(root, sha, path, *, missing_ok=False):
    if not SHA.fullmatch(sha) or not WORKFLOW.fullmatch(path):
        raise Unverifiable("path")
    info = git(root, "ls-tree", "-z", sha, "--", path)
    if not info:
        if missing_ok:
            return None
        raise Unverifiable("missing")
    if info.count(b"\0") != 1 or not info.startswith(b"100644 blob "):
        raise Unverifiable("mode")
    raw = git(root, "show", f"{sha}:{path}")
    if len(raw) > 100000 or b"\0" in raw:
        raise Unverifiable("size")
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise Unverifiable("encoding") from exc


def _cancel_values(area, *, indent):
    matches = re.findall(
        r"(?m)^" + re.escape(indent) + r"cancel-in-progress:\s*([^\r\n#]*)",
        area,
    )
    return any(item.strip().lower() not in (
        "false", "'false'", '"false"'
    ) for item in matches)


def _inline_concurrency(area, indent):
    """Inline YAML concurrency maps are ambiguous: never silently ignore them."""
    vals = re.findall(
        r"(?m)^" + re.escape(indent) + r"concurrency:[ \t]*(\{[^\r\n]*\}|[^\r\n#]+)$",
        area,
    )
    return bool(vals)


def risks(text, *, details=False):
    """Conservative parser of jobs and root/job-level cancellation.

    Unknown runner expressions/reusable jobs are possibly self-hosted.
    Unexpected source layouts are denied rather than approved.
    """
    if (not re.search(r"(?m)^on:\s*(?:\[.*\]|\{.*\})?\s*$|^on:\s*$", text)
            or len(re.findall(r"(?m)^jobs:\s*$", text)) != 1
            or len(re.findall(r"(?m)^on:", text)) != 1):
        raise Unverifiable("structure")
    if re.search(r"(?m)^[ ]{0,6}(?:<<:|concurrency:\s*[&*]|runs-on:\s*[&*])", text):
        raise Unverifiable("yaml_alias")
    # Nonstandard valid YAML indentation or quoted policy keys are DENY, not PASS.
    if re.search(r"(?m)^[ \t]*[\x27\x22](?:concurrency|cancel-in-progress)[\x27\x22]\s*:", text):
        raise Unverifiable("quoted_key")
    for match in re.finditer(r"(?m)^([ \t]*)cancel-in-progress:", text):
        if match.group(1) not in ("  ", "      "):
            raise Unverifiable("cancel_indentation")
    for match in re.finditer(r"(?m)^([ \t]*)concurrency:", text):
        if match.group(1) not in ("", "    "):
            raise Unverifiable("concurrency_indentation")
    start = re.search(r"(?m)^jobs:\s*$", text)
    tail = text[start.end():]
    next_root = re.search(r"(?m)^[A-Za-z_-][\w-]*:\s*", tail)
    jobs_area = tail[:next_root.start()] if next_root else tail
    outside_jobs = text[:start.start()] + "\n" + (
        tail[next_root.start():] if next_root else ""
    )
    markers = list(JOB.finditer(jobs_area))
    if not markers or len(markers) > 100:
        raise Unverifiable("jobs")
    if len({m.group(1) for m in markers}) != len(markers):
        raise Unverifiable("duplicate_job")
    global_cancel = _cancel_values(outside_jobs, indent="  ") or _inline_concurrency(outside_jobs, "")
    findings = []
    possible_vps = False
    for index, marker in enumerate(markers):
        job = jobs_area[
            marker.end():markers[index + 1].start()
            if index + 1 < len(markers) else None
        ]
        runner = RUNNER.findall("  " + marker.group(1) + ":\n" + job)
        is_hosted = len(runner) == 1 and bool(HOSTED.fullmatch(runner[0].strip()))
        possible_vps = possible_vps or not is_hosted
        job_cancel = _cancel_values(job, indent="      ") or _inline_concurrency(job, "    ")
        if (global_cancel or job_cancel) and not is_hosted:
            findings.append("possible_vps_cancellation")
    findings = sorted(set(findings))
    if details:
        return findings, possible_vps, global_cancel
    return findings


def evaluate(root, base, head):
    result = []
    for path in changed(root, base, head):
        before = source(root, base, path, missing_ok=True)
        after = source(root, head, path)
        now, _, now_global_cancel = risks(after, details=True)
        prior_vps = False
        if before is not None:
            _, prior_vps, _ = risks(before, details=True)
        # GitHub concurrency may cancel a still-running job from the previous
        # workflow revision. Hosted-only new jobs do NOT prove older VPS
        # invocations with the same group have stopped.
        if now or (prior_vps and now_global_cancel):
            # Changed legacy risk needs owner review. Risk removal passes.
            result.append(path)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo-root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        risky = evaluate(args.repo_root, args.base, args.head)
    except (Unverifiable, subprocess.SubprocessError, OSError, UnicodeError):
        print("VPS_CANCELLATION_RATCHET=DENY reason=unverifiable_source")
        print("VPS_MUTATION_AUTHORIZED=false")
        return 2
    print(f"VPS_CANCELLATION_RATCHET={'DENY' if risky else 'PASS'} risky_changed={len(risky)}")
    for path in risky[:25]:
        print(f"DENY {path}")
    print("VPS_MUTATION_AUTHORIZED=false")
    return int(bool(risky))


if __name__ == "__main__":
    sys.exit(main())
