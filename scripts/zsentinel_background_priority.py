#!/usr/bin/env python3
"""Yield background Fuckball CPU to zSentinel and other GitHub runner jobs.
Only adjusts the dedicated user training cgroup; never stops jobs or changes a model.
"""
import json
import os
from pathlib import Path
import subprocess

UNIT = "fuckball-ai-coach.service"
STATE = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")) / "zcloud-background-priority.json"

def workers():
    any_runner = sentinel = False
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            if (directory / "comm").read_text().strip() != "Runner.Worker":
                continue
            cmd = (directory / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            any_runner = True
            sentinel |= "/actions-runner-zcloud/" in cmd
        except (OSError, PermissionError):
            continue
    return any_runner, sentinel

def available_memory_gib():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024**2
    return 0.0

def main():
    if os.geteuid() != 1000:
        raise SystemExit("Refusing to alter units outside ubuntu user scope")
    any_runner, sentinel = workers()
    cores = max(1, len(os.sched_getaffinity(0)))
    load = os.getloadavg()[0]
    mem = available_memory_gib()
    if sentinel or load > cores * 1.35 or mem < 2:
        quota, weight, mode = 60, 25, "yield-to-sentinel"
    elif any_runner or load > cores * 0.85 or mem < 3:
        quota, weight, mode = 100, 40, "runner-active"
    else:
        quota, weight, mode = 175, 70, "idle-capacity"
    current = {}
    try:
        current = json.loads(STATE.read_text())
    except (OSError, ValueError):
        pass
    if current.get("quota") != quota or current.get("weight") != weight:
        subprocess.run(["systemctl", "--user", "set-property", "--runtime",
                        UNIT, f"CPUQuota={quota}%", f"CPUWeight={weight}"],
                       check=True, timeout=12, stdout=subprocess.DEVNULL)
    STATE.write_text(json.dumps({"quota": quota, "weight": weight, "mode": mode,
                                 "runner_active": any_runner, "sentinel_active": sentinel,
                                 "host_load": round(load, 2), "memory_available_gib": round(mem, 2)}) + "\n")
    print(f"{mode}: Fuckball CPUQuota={quota}%; CPUWeight={weight}; active runner={any_runner}")

if __name__ == "__main__":
    main()
