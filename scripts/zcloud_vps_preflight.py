#!/usr/bin/env python3
"""Read-only VPS deployment preflight. No service, Git, API or queue mutations."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

RUNNER_UNIT = "actions.runner.Zennay-zCloud.zcloud-vps-1.service"


def command(*argv: str, cwd: Path | None = None, timeout: int = 8) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            env={**{key: value for key, value in os.environ.items() if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}}, "GIT_OPTIONAL_LOCKS": "0"},
            check=False,
        )
        return completed.returncode, completed.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def meminfo() -> dict[str, int]:
    result = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, sep, rest = line.partition(":")
            if sep and key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                result[key] = int(rest.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return {}
    return result


def listener_pids() -> list[int]:
    """Only detect the dedicated zCloud Listener; never expose raw process arguments."""
    result = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return result
    for proc in entries:
        if not proc.name.isdigit():
            continue
        try:
            if (proc / "comm").read_text().strip() != "Runner.Listener":
                continue
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            program = argv[0].decode("utf-8", "replace").lower() if argv else ""
            if ("zcloud" in program or "zcloud-vps-1" in program) and program.endswith("/runner.listener"):
                result.append(int(proc.name))
        except (OSError, ValueError):
            continue
    return sorted(result)


def service_state() -> str:
    for prefix in (("systemctl", "--user"), ("systemctl",)):
        rc, output = command(*prefix, "show", RUNNER_UNIT, "--property=ActiveState", "--value", timeout=4)
        if rc == 0 and output in {"active", "inactive", "failed", "activating", "deactivating"}:
            return output
    return "unavailable"


def actionable_recovery(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    """Human-readable, non-authorizing recovery ordering; never executes the actions."""
    codes = {entry["code"] for entry in findings}
    recommendations: list[dict[str, str]] = []

    def recommend(code: str, owner: str, why: str, action: str) -> None:
        recommendations.append({
            "code": code, "owner": owner, "reason": why, "next_action": action,
            "changes_host": "no", "authorization_required": "yes",
        })

    if codes & {"LOW_DISK", "SWAP_EXHAUSTED", "LOW_AVAILABLE_MEMORY", "MEMORY_PRESSURE"}:
        recommend("CAPACITY_FIRST", "VPS resource owner",
                  "Insufficient resource headroom can cause repeat OOM crashes or incomplete releases.",
                  "Inspect disk/cache ownership and memory pressure read-only; agree on safe cleanup or resizing before a controlled runner recovery. Never automatically delete or clear swap.")
    if codes & {"ZCLOUD_RUNNER_MISSING", "RUNNER_UNIT_NOT_ACTIVE", "RUNNER_UNIT_UNOBSERVABLE"}:
        recommend("RUNNER_OWNER", "zCloud Actions runner owner (#1326)",
                  "The dedicated listener/registered runner may be unavailable independently of other project runners.",
                  "Inspect the dedicated service and assigned jobs with the authorized runner owner. Restore only under the existing idle and account-identity gates; independently confirm a job was assigned.")
    if codes & {"TRACKED_CHANGES", "UNTRACKED_FILES", "NOT_MAIN", "CHECKOUT_STATUS_UNAVAILABLE"}:
        recommend("WORKTREE_RECONCILE", "zCloud production checkout owner",
                  "Uncommitted work and untracked owner files must not be replaced by a deployment or pull.",
                  "Identify current active edits and ownership; reconcile via the existing serialized release process. Never reset, clean or force-pull this checkout.")
    if codes & {"MAIN_SHA_MISMATCH", "REMOTE_MAIN_UNBOUND", "CHECKOUT_UNAVAILABLE"}:
        recommend("SOURCE_PROVENANCE", "zCloud release owner",
                  "Exact remote-main provenance has not been established for this host.",
                  "Obtain a fresh independent protected-main SHA and compare. Require existing exact-head CI, review and release admission before deployment.")
    if not recommendations:
        recommend("EXTERNAL_RELEASE_GATES", "zCloud release owner",
                  "A local read-only preflight cannot authorize a production release.",
                  "Verify GitHub branch protection, exact-head CI, independent review, serialized writer lock, runner assignment and live acceptance separately.")
    return recommendations


def measure(root: Path, expected_main_sha: str | None = None) -> dict:
    findings: list[dict[str, str]] = []
    def mark(code: str, severity: str, detail: str) -> None:
        findings.append({"code": code, "severity": severity, "detail": detail})

    git_rc, branch = command("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    sha_rc, sha = command("git", "rev-parse", "HEAD", cwd=root)
    if git_rc or sha_rc or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        mark("CHECKOUT_UNAVAILABLE", "block", "Cannot establish local Git checkout identity.")
        branch, sha = "unknown", None
    if expected_main_sha is None:
        mark("REMOTE_MAIN_UNBOUND", "warn", "No independently verified current-main SHA supplied.")
    elif sha != expected_main_sha:
        mark("MAIN_SHA_MISMATCH", "block", "Local checkout HEAD differs from supplied main SHA.")
    status_rc, tracked = command("git", "status", "--porcelain=v1", "--untracked-files=no", cwd=root)
    untracked_rc, untracked = command("git", "ls-files", "--others", "--exclude-standard", cwd=root)
    tracked_count = len(tracked.splitlines()) if status_rc == 0 and tracked else 0
    untracked_count = len(untracked.splitlines()) if untracked_rc == 0 and untracked else 0
    if status_rc != 0 or untracked_rc != 0:
        mark("CHECKOUT_STATUS_UNAVAILABLE", "block", "Cannot prove the checkout is clean.")
    if tracked_count:
        mark("TRACKED_CHANGES", "block", f"{tracked_count} tracked checkout entries changed; preserve owner edits.")
    if untracked_count:
        mark("UNTRACKED_FILES", "block", f"{untracked_count} untracked checkout entries present; do not overwrite.")
    if branch != "main":
        mark("NOT_MAIN", "block", "Live checkout is not on main.")

    pids = listener_pids()
    unit = service_state()
    if not pids:
        mark("ZCLOUD_RUNNER_MISSING", "block", "Dedicated zCloud Runner.Listener not observed.")
    if unit in {"failed", "inactive"}:
        mark("RUNNER_UNIT_NOT_ACTIVE", "block", f"zCloud runner unit state: {unit}.")
    elif unit == "unavailable":
        mark("RUNNER_UNIT_UNOBSERVABLE", "warn", "Unit identity not readable from this context.")

    memory = meminfo()
    if {"MemTotal", "MemAvailable"} <= memory.keys():
        available_pct = round(100 * memory["MemAvailable"] / max(memory["MemTotal"], 1), 1)
        if available_pct < 10:
            mark("LOW_AVAILABLE_MEMORY", "block", "Available RAM below 10%.")
        elif available_pct < 20:
            mark("MEMORY_PRESSURE", "warn", "Available RAM below 20%.")
    else:
        available_pct = None
        mark("MEMORY_UNOBSERVABLE", "warn", "Cannot read memory accounting.")

    if memory.get("SwapTotal", 0):
        swap_pct = round(100 * (1 - memory.get("SwapFree", 0) / memory["SwapTotal"]), 1)
        if swap_pct >= 95:
            mark("SWAP_EXHAUSTED", "block", "Swap utilisation >=95%.")
    else:
        swap_pct = None

    try:
        volume = os.statvfs(root)
        disk_free_pct = round(100 * volume.f_bavail / max(volume.f_blocks, 1), 1)
        if disk_free_pct < 10:
            mark("LOW_DISK", "block", "Filesystem free space below 10%.")
    except OSError:
        disk_free_pct = None
        mark("DISK_UNOBSERVABLE", "warn", "Cannot read filesystem space.")

    blocked = any(f["severity"] == "block" for f in findings)
    recovery = actionable_recovery(findings)
    return {
        "schema": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only", "release_authorized": False,
        "result": "blocked" if blocked else ("partial" if findings else "preflight_clear"),
        "checkout": {"branch": branch, "sha": sha, "expected_main_sha": expected_main_sha, "tracked_changes": tracked_count,
                     "untracked_entries": untracked_count},
        "runner": {"unit": RUNNER_UNIT, "unit_state": unit, "listener_pids": pids},
        "resources": {"available_ram_pct": available_pct, "swap_used_pct": swap_pct,
                      "disk_free_pct": disk_free_pct},
        "findings": findings,
        "recovery_priorities": recovery,
        "next_step": "Obtain existing owner/release approval; never use this report as deploy authorization.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/home/ubuntu/zennay-cloud"))
    parser.add_argument("--json", action="store_true", help="Print bounded machine-readable evidence")
    parser.add_argument("--expected-main-sha", default=None,
                        help="Exact independently fetched 40-character main commit SHA (no network requests)")
    args = parser.parse_args()
    if args.expected_main_sha and not re.fullmatch(r"[0-9a-f]{40}", args.expected_main_sha):
        parser.error("--expected-main-sha must be a lowercase 40-character Git SHA")
    report = measure(args.root.resolve(), args.expected_main_sha)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"zCloud VPS preflight: {report['result'].upper()} (read-only; NOT a release authorization)")
        c = report["checkout"]
        print(f"Checkout: {c['branch']} {c['sha'] or 'unknown'}; tracked={c['tracked_changes']} untracked={c['untracked_entries']}")
        r = report["runner"]
        print(f"Runner: {r['unit_state']}; dedicated listener PIDs: {r['listener_pids']}")
        for f in report["findings"]:
            print(f"[{f['severity'].upper()}] {f['code']}: {f['detail']}")
        print("Owner-gated recovery priorities:")
        for index, item in enumerate(report["recovery_priorities"], 1):
            print(f"  {index}. {item['code']} [{item['owner']}]: {item['next_action']}")
        print(report["next_step"])
    return 2 if report["result"] == "blocked" else (1 if report["result"] == "partial" else 0)


if __name__ == "__main__":
    sys.exit(main())
