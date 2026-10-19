#!/usr/bin/env python3
"""Fail-closed, opt-in recovery for offline zCloud/FTMO GitHub Actions listeners.

Does not stop or restart active runners, Firefox, workers, browser tabs, services
other than the exact inactive Actions unit, or the zCloud queue/database.
Default operation is observation only. Production use requires --apply.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time

UNITS = {
    "zcloud": ("actions.runner.Zennay-zCloud.zcloud-vps-1.service", "ubuntu"),
    "ftmo": ("actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service", "ftmo-runner"),
}

# A GitHub Actions listener can spawn memory-heavy worker processes as soon as
# it reconnects. Do not rearm a crashed service on a host with exhausted swap.
# These are admission floors, not a reclaim order or a guarantee of spare CPU.
MIN_AVAILABLE_MIB = 3072
MIN_SWAP_FREE_MIB = 512
MIN_AVAILABLE_WITHOUT_SWAP_MIB = 4096
RUNNER_ROOTS = {
    'zcloud': '/home/ubuntu/actions-runner-zcloud/',
    'ftmo': '/opt/ftmo-runner/',
}


def memory_headroom() -> dict:
    """Fail closed using live kernel MemAvailable + configured swap headroom.

    Do not infer free usable RAM from the 'free' field or assume that swap
    currently marked used can be reclaimed without killing live workers.
    """
    keys = ("MemAvailable", "SwapTotal", "SwapFree")
    values: dict[str, int] = {}
    with open("/proc/meminfo", encoding="ascii") as stream:
        for line in stream:
            name, _, raw = line.partition(":")
            if name not in keys:
                continue
            chunks = raw.split()
            if name in values or len(chunks) != 2 or chunks[1] != "kB" or not chunks[0].isdigit():
                raise ValueError("invalid_memory_source")
            values[name] = int(chunks[0]) // 1024
    if set(values) != set(keys):
        raise ValueError("incomplete_memory_source")
    ram = values["MemAvailable"]
    swap_total = values["SwapTotal"]
    swap_free = values["SwapFree"]
    if swap_free > swap_total:
        raise ValueError("invalid_swap_counters")
    minimum_ram = (MIN_AVAILABLE_MIB
                   if swap_total >= MIN_SWAP_FREE_MIB
                   else MIN_AVAILABLE_WITHOUT_SWAP_MIB)
    decision = (
        "deny_insufficient_ram_headroom" if ram < minimum_ram else
        "deny_configured_swap_exhausted" if swap_total >= MIN_SWAP_FREE_MIB
            and swap_free < MIN_SWAP_FREE_MIB else
        "eligible_memory_headroom"
    )
    return {
        "available_ram_mib": ram,
        "configured_swap_mib": swap_total,
        "free_swap_mib": swap_free,
        "required_ram_mib": minimum_ram,
        "required_swap_mib": MIN_SWAP_FREE_MIB if swap_total >= MIN_SWAP_FREE_MIB else 0,
        "decision": decision,
    }


def decide(load: str, active: str, matching_processes: int) -> str:
    """Never touch a live listener or an orphaned worker."""
    if load != "loaded":
        return "deny_missing_unit"
    if active in {"active", "activating", "reloading"}:
        return "preserve_existing_runner"
    if active not in {"inactive", "failed"}:
        return "deny_unknown_service_state"
    if matching_processes != 0:
        return "deny_residual_runner_processes"
    return "eligible_start_inactive_listener"


def call(*argv: str) -> str:
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=15).stdout.strip()


def runner_process_count(lane: str) -> int:
    """Resolve a listener to its exact installation path, never just a user.

    Other runners can share the ubuntu account; an orphaned target process
    can conversely survive under another Unix owner. Both cases must be
    treated accurately before any systemd start command is permitted.
    """
    root = RUNNER_ROOTS[lane]
    output = call("ps", "-eo", "user:32=,args=")
    count = 0
    for line in output.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        user, command = parts
        owned = any(root + suffix in command for suffix in (
            "bin/Runner.Listener", "bin/Runner.Worker", "runsvc.sh", "run.sh",
        ))
        if owned:
            count += 1
        elif (user == UNITS[lane][1]
              and ("Runner.Listener" in command or "Runner.Worker" in command)
              and not re.search(r"/[A-Za-z0-9_./-]+/bin/Runner\\.(?:Listener|Worker)", command)):
            # A relative/unknown runner command cannot be attributed safely.
            count += 1
    return count


def service_unit_matches(unit: str, lane: str, expected_owner: str) -> bool:
    """Bind systemd User and ExecStart to the known installed service root."""
    output = call("systemctl", "show", unit, "--property=ExecStart",
                  "--property=User", "--no-pager")
    props = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    starts = re.findall(r"(?:^|\\s)path=([^\\s;]+)", props.get("ExecStart", ""))
    return props.get("User") == expected_owner and starts == [RUNNER_ROOTS[lane] + "runsvc.sh"]

def service_state(unit: str) -> tuple[str, str]:
    output = call("systemctl", "show", unit, "--property=LoadState", "--property=ActiveState", "--no-pager")
    data = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    return data.get("LoadState", ""), data.get("ActiveState", "")


def rescue(lane: str, apply: bool) -> dict:
    unit, owner = UNITS[lane]
    load, active = service_state(unit)
    processes = runner_process_count(lane)
    identity_ok = service_unit_matches(unit, lane, owner)
    decision = decide(load, active, processes)
    if not identity_ok:
        decision = "deny_runner_unit_identity_mismatch"
    memory = memory_headroom()
    if decision == "eligible_start_inactive_listener" and memory["decision"] != "eligible_memory_headroom":
        decision = memory["decision"]
    evidence = {
        "lane": lane, "unit": unit, "load_state": load,
        "active_state_before": active, "process_count_for_target_root": processes,
        "exact_system_unit_identity": identity_ok,
        "decision": decision, "mutation_performed": False,
        "mutation_attempted": False, "memory": memory,
    }
    if apply and decision == "eligible_start_inactive_listener":
        # A second independent admission read guards slow command/service races.
        if service_state(unit) != (load, active) or not service_unit_matches(unit, lane, owner) or runner_process_count(lane):
            evidence["decision"] = "deny_state_changed"
            return evidence
        latest = memory_headroom()
        evidence["memory_before_start"] = latest
        if latest["decision"] != "eligible_memory_headroom":
            evidence["decision"] = "deny_memory_headroom_changed"
            return evidence
        if active == "failed":
            evidence["mutation_attempted"] = True
            try:
                subprocess.run(["sudo", "-n", "systemctl", "reset-failed", unit], check=True, timeout=15)
            except (OSError, subprocess.SubprocessError) as exc:
                evidence["decision"] = "deny_reset_failed"
                evidence["error_type"] = type(exc).__name__
                return evidence
            evidence["mutation_performed"] = True
        # Re-check one more time after reset-failed before a job can start.
        latest = memory_headroom()
        evidence["memory_before_start"] = latest
        if latest["decision"] != "eligible_memory_headroom":
            evidence["decision"] = "deny_memory_headroom_changed"
            return evidence
        if runner_process_count(lane) or not service_unit_matches(unit, lane, owner):
            evidence["decision"] = "deny_residual_processes_or_changed_unit"
            return evidence
        evidence["mutation_attempted"] = True
        try:
            subprocess.run(["sudo", "-n", "systemctl", "start", unit], check=True, timeout=25)
        except (OSError, subprocess.SubprocessError) as exc:
            evidence["decision"] = "deny_listener_start_failed"
            evidence["error_type"] = type(exc).__name__
            # A rejected start can still have affected systemd; the result
            # must not claim an untouched service or verified recovery.
            evidence["mutation_effect_unknown"] = True
            return evidence
        evidence["mutation_performed"] = True
        for _ in range(5):
            _load, post = service_state(unit)
            if _load == "loaded" and post == "active":
                evidence["active_state_after"] = post
                evidence["decision"] = "started_inactive_listener"
                return evidence
            time.sleep(1)
        evidence["active_state_after"] = post
        evidence["decision"] = "start_not_confirmed"
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=[*UNITS, "both"], default="zcloud",
                        help="Explicit target; defaults to zcloud, never starts FTMO implicitly")
    parser.add_argument("--apply", action="store_true", help="Start only verifiably inactive runners")
    args = parser.parse_args()
    if socket.gethostname() != "vps-bb300bba" or call("id", "-un") != "ubuntu":
        print(json.dumps({"error": "deny_untrusted_host_or_user", "mutation_performed": False}))
        return 2
    lanes = list(UNITS) if args.lane == "both" else [args.lane]
    reports = []
    for lane in lanes:
        try:
            reports.append(rescue(lane, args.apply))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            reports.append({"lane": lane, "decision": "error", "error_type": type(exc).__name__, "mutation_performed": False})
    print(json.dumps({"policy": "zcloud-idle-actions-listener-recovery-v1", "apply": args.apply,
                      "reports": reports}, sort_keys=True))
    return 1 if any(r["decision"].startswith(("deny", "error", "start_not_confirmed")) for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
