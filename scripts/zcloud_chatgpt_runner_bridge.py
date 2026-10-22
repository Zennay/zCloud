#!/usr/bin/env python3
"""Bounded GitHub issue-comment to VPS runner operations.

This is NOT a generic shell interpreter.  Extend reviewed recipes on main for
new operations.  Public issue comments and Actions logs must contain no secrets.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile

REPOSITORY = "Zennay/zCloud"
CONTROL_ISSUE = 1278
OWNER = "Zennay"
CHATGPT_APP_ID = 1144995
HOST = "vps-bb300bba"
USER = "ubuntu"
COMMAND = re.compile(
    r"/zssh (status|workers|project cloud|service (?:zennay-cloud|zssh|zssh-public)|run [a-z][a-z0-9-]{1,40})\Z"
)


MAX_PROC_ENTRIES = 8192
MAX_PROC_STAT_BYTES = 4096
MAX_PROC_ANCESTORS = 64


class RequestRejected(ValueError):
    pass


def parse_request(event: dict, actor: str) -> str:
    if event.get("action") != "created":
        raise RequestRejected("wrong_action")
    if (event.get("repository") or {}).get("full_name") != REPOSITORY:
        raise RequestRejected("wrong_repository")
    issue = event.get("issue") or {}
    if issue.get("number") != CONTROL_ISSUE or issue.get("pull_request"):
        raise RequestRejected("wrong_issue")
    comment = event.get("comment") or {}
    if actor != OWNER or (comment.get("user") or {}).get("login") != OWNER:
        raise RequestRejected("unauthorized_actor")
    if comment.get("author_association") != "OWNER":
        raise RequestRejected("unauthorized_author_association")
    # Manual owner comments are allowed. For connector-mediated requests,
    # require the exact observed GitHub App identity; never trust text claims.
    app = comment.get("performed_via_github_app")
    if app is not None and app.get("id") != CHATGPT_APP_ID:
        raise RequestRejected("untrusted_app")
    body = comment.get("body")
    if not isinstance(body, str) or len(body) > 120 or not COMMAND.fullmatch(body):
        raise RequestRejected("unsupported_operation")
    return body.removeprefix("/zssh ")


def ensure_runner() -> None:
    if socket.gethostname().split(".", 1)[0] != HOST:
        raise RuntimeError("wrong_runner_host")
    if os.environ.get("USER") != USER or os.geteuid() == 0:
        raise RuntimeError("wrong_runner_user")
    name = os.environ.get("RUNNER_NAME", "")
    if not name or "haxlab" in name.lower():
        raise RuntimeError("wrong_runner_identity")


def run_checked(argv: list[str], *, cwd: str | None = None) -> tuple[int, str]:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=20,
        check=False,
        env={k: v for k, v in os.environ.items() if k not in
             {"GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "AWS_SECRET_ACCESS_KEY"}},
    )
    # Only reviewed read-only commands may expose stdout; bounded and no stderr.
    return completed.returncode, completed.stdout[:800].strip()


def _process_stat(pid: int) -> tuple[dict[str, object] | None, str | None]:
    """Read bounded /proc identity fields only; never read cmdline or environ."""
    try:
        with (Path("/proc") / str(pid) / "stat").open("rb") as stream:
            raw = stream.read(MAX_PROC_STAT_BYTES + 1)
    except FileNotFoundError:
        return None, "vanished"
    except PermissionError:
        return None, "unreadable"
    except OSError:
        return None, "unavailable"
    if len(raw) > MAX_PROC_STAT_BYTES:
        return None, "oversized"
    try:
        value = raw.decode("ascii")
    except UnicodeError:
        return None, "malformed"
    left, right = value.find("("), value.rfind(")")
    if left < 0 or right <= left:
        return None, "malformed"
    comm = value[left + 1:right]
    fields = value[right + 2:].split()
    if len(fields) < 20:
        return None, "malformed"
    try:
        parent_pid, start_ticks = int(fields[1]), int(fields[19])
    except (ValueError, IndexError):
        return None, "malformed"
    if parent_pid < 0 or start_ticks < 0:
        return None, "malformed"
    return {"comm": comm, "parent_pid": parent_pid, "start_ticks": start_ticks}, None


def _occupancy_band(count: int, partial: bool = False) -> str:
    if count <= 0:
        return "none_observed_coverage_partial" if partial else "none_observed"
    if partial:
        return "one_or_more_observed" if count == 1 else "multiple_observed"
    return "one_observed" if count == 1 else "multiple_observed"


def _worker_age_bands(ages: list[int | None]) -> dict[str, int]:
    return {
        "under_5m": sum(age is not None and age < 300 for age in ages),
        "5m_to_30m": sum(age is not None and 300 <= age < 1800 for age in ages),
        "over_30m": sum(age is not None and age >= 1800 for age in ages),
        "unknown": sum(age is None for age in ages),
    }


def runner_worker_snapshot() -> dict:
    """Privacy-preserving local occupancy hint, not a task/claim inventory."""
    try:
        with Path("/proc/uptime").open("rb") as stream:
            uptime_raw = stream.read(128)
        uptime_seconds = float(uptime_raw.decode("ascii").split()[0])
        ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
        if (not math.isfinite(uptime_seconds) or uptime_seconds < 0
                or not 1 <= ticks_per_second <= 1_000_000):
            raise ValueError("invalid_proc_clock")
    except (OSError, UnicodeError, ValueError, IndexError, TypeError):
        return {
            "state": "unavailable", "scan_coverage": "unavailable",
            "observed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "worker_process_band_observed": "unknown",
            "other_worker_process_band": "unknown",
            "listener_process_band_observed": "unknown",
            "self_worker_excluded": False,
            "worker_age_bands_observed": None,
            "claim_ownership_verified": False,
            "project_attribution_verified": False,
            "safe_to_take_over": False,
            "release_authorized": False,
            "mutation_performed": False,
        }

    current_worker_pids = set()
    pid = os.getpid()
    for _ in range(MAX_PROC_ANCESTORS):
        info, _error = _process_stat(pid)
        if info is None:
            break
        if info["comm"] == "Runner.Worker":
            current_worker_pids.add(pid)
        parent_pid = info["parent_pid"]
        if parent_pid <= 1 or parent_pid == pid:
            break
        pid = parent_pid
    self_worker_excluded = bool(current_worker_pids)

    worker_ages: list[int | None] = []
    listener_count = 0
    scanned = 0
    partial = False
    try:
        with os.scandir("/proc") as entries:
            for entry in entries:
                if not entry.name.isdecimal():
                    continue
                scanned += 1
                if scanned > MAX_PROC_ENTRIES:
                    partial = True
                    break
                process_id = int(entry.name)
                info, error = _process_stat(process_id)
                if info is None:
                    if error not in (None, "vanished"):
                        partial = True
                    continue
                if info["comm"] == "Runner.Listener":
                    listener_count += 1
                    continue
                if info["comm"] != "Runner.Worker" or process_id in current_worker_pids:
                    continue
                age = uptime_seconds - info["start_ticks"] / ticks_per_second
                worker_ages.append(
                    int(age) if math.isfinite(age) and age >= 0 else None
                )
    except OSError:
        partial = True

    worker_band = _occupancy_band(len(worker_ages), partial)
    observed_band = _occupancy_band(
        len(worker_ages) + len(current_worker_pids), partial
    ) if self_worker_excluded else _occupancy_band(len(worker_ages), partial)
    state = (
        "partial_observation" if partial else
        "no_other_worker_observed_not_clearance" if self_worker_excluded and not worker_ages else
        "one_other_worker_observed" if self_worker_excluded and len(worker_ages) == 1 else
        "multiple_other_workers_observed" if self_worker_excluded else
        "current_worker_not_identified"
    )
    return {
        "state": state,
        "observed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scan_coverage": "partial" if partial else "bounded_proc_snapshot_complete",
        "self_worker_excluded": self_worker_excluded,
        "worker_process_band_observed": observed_band,
        "other_worker_process_band": worker_band if self_worker_excluded else "unknown_self_not_excluded",
        "listener_process_band_observed": _occupancy_band(listener_count, partial),
        "worker_age_bands_observed": _worker_age_bands(worker_ages),
        "claim_ownership_verified": False,
        "project_attribution_verified": False,
        "safe_to_take_over": False,
        "release_authorized": False,
        "mutation_performed": False,
    }


def execute(request: str) -> dict:
    ensure_runner()
    if request == "status":
        probes = [
            ("uptime", ["/usr/bin/uptime", "-p"]),
            ("disk", ["/usr/bin/df", "-h", "/"]),
            ("memory", ["/usr/bin/free", "-h"]),
        ]
        checks = {}
        for label, argv in probes:
            code, output = run_checked(argv)
            if code:
                raise RuntimeError("status_probe_failed:" + label)
            checks[label] = output
        return {"ok": True, "operation": "status", "host": HOST, "checks": checks}
    if request == "workers":
        return {"ok": True, "operation": "workers", "host": HOST,
                **runner_worker_snapshot()}
    if request == "project cloud":
        code, sha = run_checked(
            ["/usr/bin/git", "-C", "/home/ubuntu/zennay-cloud", "rev-parse", "--short", "HEAD"]
        )
        if code or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
            raise RuntimeError("cloud_git_revision_unavailable")
        return {"ok": True, "operation": "project cloud", "revision": sha}
    if request.startswith("service "):
        service = request.split(" ", 1)[1] + ".service"
        code, active = run_checked(["/usr/bin/systemctl", "is-active", "--", service])
        if code not in (0, 3):
            raise RuntimeError("service_inspection_failed")
        if active not in ("active", "inactive", "failed", "activating", "deactivating", "unknown"):
            raise RuntimeError("unexpected_service_state")
        return {"ok": True, "operation": request, "state": active}
    if request == "run write-probe":
        # Material, reversible proof of writing on the VPS without modifying services.
        temp_root = os.environ.get("RUNNER_TEMP")
        if not temp_root or not Path(temp_root).is_dir():
            raise RuntimeError("runner_temp_unavailable")
        with tempfile.TemporaryDirectory(prefix="zssh-chatgpt-", dir=temp_root) as directory:
            probe = Path(directory) / "proof.txt"
            probe.write_text("runner-local-write-proof\n", encoding="utf-8")
            if probe.read_text(encoding="utf-8") != "runner-local-write-proof\n":
                raise RuntimeError("write_probe_mismatch")
        return {"ok": True, "operation": request, "effect": "temporary_file_write_verified_and_cleaned"}
    if request == "run zcloud-guard-test":
        code, _ = run_checked(
            [sys.executable, "-m", "unittest", "-q", "tests.test_vps_runner_guard"],
            cwd=os.environ.get("GITHUB_WORKSPACE") or None,
        )
        if code:
            raise RuntimeError("zcloud_guard_test_failed")
        return {"ok": True, "operation": request, "effect": "unit_test_passed"}
    if request.startswith("run "):
        # Only execute a checked-in recipe from the exact main revision.
        # Issue comments never supply code, shell flags, or file paths.
        name = request.removeprefix("run ")
        root = Path(__file__).resolve().parents[1]
        recipes_dir = root / "ops" / "zssh-runner-recipes"
        recipe = recipes_dir / (name + ".py")
        if recipe.is_symlink() or not recipe.is_file():
            raise RequestRejected("reviewed_recipe_not_found")
        if recipe.resolve().parent != recipes_dir.resolve():
            raise RequestRejected("reviewed_recipe_outside_root")
        code, _ = run_checked([sys.executable, str(recipe)], cwd=str(root))
        if code:
            raise RuntimeError("reviewed_recipe_failed")
        return {"ok": True, "operation": request, "effect": "reviewed_recipe_passed"}
    raise RequestRejected("unsupported_operation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["validate", "execute"])
    parser.add_argument("--event", type=Path, default=Path(os.environ.get("GITHUB_EVENT_PATH", "")))
    args = parser.parse_args()
    if not args.event.is_file():
        print("zssh_bridge_rejected: missing_event", file=sys.stderr)
        return 2
    try:
        event = json.loads(args.event.read_text(encoding="utf-8"))
        request = parse_request(event, os.environ.get("GITHUB_ACTOR", ""))
        if args.mode == "validate":
            print("ZSSH_BRIDGE_AUTHORIZED " + request)
            return 0
        result = execute(request)
        # The only operational output is a JSON receipt: no environment, secrets,
        # stderr, arbitrary shell stdout, private files, or dynamic paths.
        print("ZSSH_BRIDGE_RECEIPT=" + json.dumps(result, sort_keys=True))
        return 0
    except (RequestRejected, RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # Error messages are machine-owned constants, not raw command stderr.
        print("zssh_bridge_rejected: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
