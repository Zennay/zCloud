#!/usr/bin/env python3
"""Keep background Fuckball training alive while reserving capacity for runners.

Only rebalances an existing ubuntu user-scoped coach cgroup. Never starts,
stops, restarts, dispatches or modifies a portfolio queue or model.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

UNIT = "fuckball-ai-coach.service"
STATE = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")) / "zcloud-background-priority.json"
RUNNER_MARKER = "/actions-runner-zcloud/"


def workers():
    """Observe running GitHub jobs, never infer workers from idle listeners."""
    any_runner = sentinel = False
    try:
        entries = Path("/proc").iterdir()
        for directory in entries:
            if not directory.name.isdigit():
                continue
            try:
                if (directory / "comm").read_text().strip() != "Runner.Worker":
                    continue
                cmd = (directory / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
                if not cmd.strip():
                    continue
                any_runner = True
                sentinel |= RUNNER_MARKER in cmd
            except (OSError, PermissionError):
                # Processes may exit mid-observation; do not kill/retry them.
                continue
    except OSError:
        # A broken /proc observation must never license a high CPU quota.
        return False, False, False
    return any_runner, sentinel, True


def memory_pressure():
    """Return available memory in GiB and used swap fraction, or unknown."""
    try:
        fields = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, sep, value = line.partition(":")
            if sep and key in {"MemAvailable", "SwapTotal", "SwapFree"}:
                fields[key] = int(value.split()[0])
        available = fields["MemAvailable"] / (1024 ** 2)
        total = fields["SwapTotal"]
        free = fields["SwapFree"]
        if available < 0 or total < 0 or free < 0 or free > total:
            return None, None
        used = (total - free) / total if total else 0.0
        return available, used
    except (OSError, ValueError, IndexError, KeyError):
        return None, None


def disk_used_fraction():
    """Check root filesystem pressure without traversing or reading files."""
    try:
        st = os.statvfs("/")
        if st.f_blocks <= 0 or st.f_bavail < 0 or st.f_bavail > st.f_blocks:
            return None
        return 1.0 - st.f_bavail / st.f_blocks
    except OSError:
        return None


def snapshot():
    runner, sentinel, proc_ok = workers()
    memory, swap_used = memory_pressure()
    disk_used = disk_used_fraction()
    try:
        cores = max(1, len(os.sched_getaffinity(0)))
        load = os.getloadavg()[0]
    except (OSError, AttributeError):
        cores, load = 1, None
    unknown = not proc_ok or memory is None or swap_used is None or disk_used is None or load is None
    return {
        "runner_active": runner, "zcloud_runner_active": sentinel,
        "host_load": round(load, 2) if load is not None else None,
        "cores": cores, "memory_available_gib": round(memory, 2) if memory is not None else None,
        "swap_used_percent": round(swap_used * 100, 1) if swap_used is not None else None,
        "disk_unavailable_percent": round(disk_used * 100, 1) if disk_used is not None else None,
        "source_complete": not unknown,
    }


def admission(s):
    """Fail closed: uncertainty or pressure reduces CPU, never stops training."""
    if (not s["source_complete"] or s["zcloud_runner_active"] or
            s["host_load"] > s["cores"] * 1.35 or
            s["memory_available_gib"] < 2 or
            s["swap_used_percent"] >= 80 or
            s["disk_unavailable_percent"] >= 90):
        return 60, 25, "yield-to-runners-or-pressure"
    if (s["runner_active"] or s["host_load"] > s["cores"] * 0.85 or
            s["memory_available_gib"] < 3 or s["swap_used_percent"] >= 60 or
            s["disk_unavailable_percent"] >= 85):
        return 100, 40, "runner-or-moderate-pressure"
    return 175, 70, "idle-capacity"


def read_previous():
    try:
        # Do not follow an attacker-created state symlink.
        if STATE.is_symlink():
            return {}
        data = json.loads(STATE.read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def write_private_state(data):
    """Atomic 0600 state; no partially written decision exposed to timer runs."""
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = STATE.with_name(STATE.name + "." + str(os.getpid()) + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, STATE)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description="Bounded user training CPU priority")
    parser.add_argument("--preview", action="store_true",
                        help="Show policy decision in JSON without changing CPU controls or state")
    args = parser.parse_args()
    if not args.preview and os.geteuid() != 1000:
        raise SystemExit("Refusing to alter units outside ubuntu user scope")
    s = snapshot()
    quota, weight, mode = admission(s)
    if args.preview:
        print(json.dumps({**s, "mode": mode, "quota": quota, "weight": weight,
                          "applied": False}, sort_keys=True))
        return
    previous = read_previous()
    if previous.get("quota") != quota or previous.get("weight") != weight:
        # No command shell, no privileged unit and no service restart.
        subprocess.run(
            ["systemctl", "--user", "set-property", "--runtime",
             UNIT, f"CPUQuota={quota}%", f"CPUWeight={weight}"],
            check=True, timeout=12, stdout=subprocess.DEVNULL,
        )
    write_private_state({**s, "quota": quota, "weight": weight, "mode": mode})
    print(f"{mode}: Fuckball CPUQuota={quota}%; CPUWeight={weight}; "
          f"active runner={s['runner_active']}; sources complete={s['source_complete']}")


if __name__ == "__main__":
    main()
