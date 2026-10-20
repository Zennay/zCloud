#!/usr/bin/env python3
"""Keep background Fuckball training alive while reserving capacity for runners.

Only rebalances an existing ubuntu user-scoped coach cgroup. Never starts,
stops, restarts, dispatches or modifies a portfolio queue or model.
"""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess

UNIT = "fuckball-ai-coach.service"
STATE = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")) / "zcloud-background-priority.json"
RUNNER_MARKER = "/actions-runner-zcloud/"


def workers():
    """Observe running job processes, treating denied process reads as unknown."""
    any_runner = sentinel = False
    complete = True
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
                    # Empty argv on an extant process cannot prove no active job.
                    complete = False
                    continue
                any_runner = True
                sentinel |= RUNNER_MARKER in cmd
            except FileNotFoundError:
                # A process exited between its directory listing and inspection.
                continue
            except (OSError, PermissionError):
                # A still-existing process was unreadable: fail closed.
                if directory.exists():
                    complete = False
    except OSError:
        # A broken /proc observation must never license a high CPU quota.
        return False, False, False
    return any_runner, sentinel, complete


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


def pressure_reasons(s):
    """Explain exactly which bounded signals shaped the CPU decision."""
    urgent = []
    moderate = []
    if not s["source_complete"]:
        urgent.append("incomplete-capacity-source")
    if s["zcloud_runner_active"]:
        urgent.append("zcloud-github-job-running")
    elif s["runner_active"]:
        moderate.append("other-github-job-running")
    load = s["host_load"]
    if load is not None:
        if load > s["cores"] * 1.35:
            urgent.append("high-system-load")
        elif load > s["cores"] * 0.85:
            moderate.append("elevated-system-load")
    memory = s["memory_available_gib"]
    if memory is not None:
        if memory < 2:
            urgent.append("low-available-memory")
        elif memory < 3:
            moderate.append("limited-available-memory")
    swap = s["swap_used_percent"]
    if swap is not None:
        if swap >= 80:
            urgent.append("high-swap-use")
        elif swap >= 60:
            moderate.append("elevated-swap-use")
    disk = s["disk_unavailable_percent"]
    if disk is not None:
        if disk >= 90:
            urgent.append("low-root-disk-headroom")
        elif disk >= 85:
            moderate.append("limited-root-disk-headroom")
    return urgent, moderate


def admission(s):
    """Fail closed: uncertainty or pressure reduces CPU, never stops training."""
    urgent, moderate = pressure_reasons(s)
    if urgent:
        return 60, 25, "yield-to-runners-or-pressure"
    if moderate:
        return 100, 40, "runner-or-moderate-pressure"
    return 175, 70, "idle-capacity"


def _cpu_period_seconds(raw):
    """Convert systemd's one-second quota to seconds without shell parsing."""
    raw = raw.strip()
    for suffix, scale in (("min", 60.0), ("ms", 0.001),
                          ("us", 0.000001), ("s", 1.0)):
        if raw.endswith(suffix):
            try:
                number = float(raw[:-len(suffix)])
                if number >= 0 and number < 3600:
                    return number * scale
            except ValueError:
                return None
    return None


def installed_cgroup():
    """Check real service state, not an old JSON guess about applied values."""
    result = subprocess.run(
        ["systemctl", "--user", "show", UNIT,
         "-p", "ActiveState", "-p", "CPUQuotaPerSecUSec", "-p", "CPUWeight"],
        check=True, timeout=8, capture_output=True, text=True,
    )
    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines()
        if "=" in line
    )
    try:
        weight = int(fields["CPUWeight"])
    except (KeyError, ValueError):
        weight = None
    return {
        "active": fields.get("ActiveState") == "active",
        "weight": weight,
        "quota_seconds": _cpu_period_seconds(fields.get("CPUQuotaPerSecUSec", "")),
    }


def matches_cgroup(observed, quota, weight):
    seconds = observed["quota_seconds"]
    return (observed["active"] and observed["weight"] == weight and
            seconds is not None and abs(seconds - quota / 100) < 0.002)


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


RUNNER_UNIT = re.compile(r"^actions\.runner\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.service$")


def runner_inventory():
    """Optional one-shot local unit inventory: no permission or service changes."""
    try:
        result = subprocess.run(
            ["systemctl", "--no-pager", "--plain", "--no-legend",
             "list-units", "--all", "--type=service", "actions.runner.*"],
            check=True, timeout=8, capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return {"runner_units_observed": False, "runner_units": [],
                "runner_units_truncated": None}
    units = []
    seen = set()
    partial = len(result.stdout) > 20000
    for line in result.stdout[:20000].splitlines():
        fields = line.strip().split()
        if len(fields) < 4 or not RUNNER_UNIT.fullmatch(fields[0]):
            continue
        name, state, detail = fields[0], fields[2], fields[3]
        if name in seen:
            continue
        seen.add(name)
        if len(units) >= 20:
            partial = True
            break
        if state not in {"active", "failed", "inactive", "activating", "deactivating"}:
            state = "unknown"
        if detail not in {"running", "dead", "failed", "exited", "start", "stop",
                          "auto-restart", "waiting", "listening"}:
            detail = "unknown"
        entry = {"unit": name, "state": state, "detail": detail,
                 "reason": None}
        if state == "failed":
            try:
                receipt = subprocess.run(
                    ["systemctl", "--no-pager", "show", name, "-p", "Result"],
                    check=True, timeout=3, capture_output=True, text=True,
                )
                value = receipt.stdout.strip().removeprefix("Result=")
                if value in {"oom-kill", "exit-code", "signal", "timeout", "watchdog",
                             "core-dump", "resources", "success"}:
                    entry["reason"] = value
            except (OSError, subprocess.SubprocessError):
                pass
        units.append(entry)
    return {"runner_units_observed": True, "runner_units": units,
            "runner_units_truncated": partial}


REASON_LABELS = {
    "incomplete-capacity-source": "een benodigde systeemmeting ontbreekt",
    "zcloud-github-job-running": "een zCloud GitHub-job is actief",
    "other-github-job-running": "een andere GitHub-job is actief",
    "high-system-load": "de systeemload is hoog",
    "elevated-system-load": "de systeemload is verhoogd",
    "low-available-memory": "er is weinig vrij beschikbaar RAM",
    "limited-available-memory": "het beschikbare RAM is beperkt",
    "high-swap-use": "het swapgebruik is hoog",
    "elevated-swap-use": "het swapgebruik is verhoogd",
    "low-root-disk-headroom": "de root-schijf heeft weinig vrije ruimte",
    "limited-root-disk-headroom": "de vrije ruimte op de root-schijf is beperkt",
}


def human_preview(data):
    """Readable operator explanation; only received numeric and enum fields."""
    print("zSentinel | Capaciteitsadvies (alleen lezen)")
    print(f"Advies voor Fuckball: CPU {data['quota']}%, gewicht {data['weight']}")
    print("Oorzaken: " + ("; ".join(REASON_LABELS.get(x, x)
                                    for x in data["reasons"]) or
                         "geen kritieke druk of actieve job gemeten"))
    print(f"VPS: load {data['host_load'] if data['host_load'] is not None else 'onbekend'}"
          f" op {data['cores']} cores; swapgebruik "
          f"{data['swap_used_percent'] if data['swap_used_percent'] is not None else 'onbekend'}%;"
          f" root niet beschikbaar "
          f"{data['disk_unavailable_percent'] if data['disk_unavailable_percent'] is not None else 'onbekend'}%")
    if not data["unit_observed"]:
        print("Bestaande trainerinstellingen: niet uitleesbaar via user-systemd")
    elif not data["unit_active"]:
        print("Trainer: inactief; geen automatische herstart")
    else:
        print(f"Bestaande trainer: CPU "
              f"{data['current_quota_percent'] if data['current_quota_percent'] is not None else 'onbekend'}%,"
              f" gewicht {data['current_weight'] if data['current_weight'] is not None else 'onbekend'}")
        print("Advies al toegepast: " +
              ("ja" if data["recommendation_currently_applied"] else "nee of niet verifieerbaar"))
    if data.get("runner_units_observed") is True:
        print("GitHub runner-services (service actief is geen bewijs van een lopende job):")
        for entry in data["runner_units"]:
            reason = f" ({entry['reason']})" if entry["reason"] else ""
            print(f"  {entry['unit']}: {entry['state']}/{entry['detail']}{reason}")
        if data.get("runner_units_truncated"):
            print("  Let op: gedeeltelijk overzicht; niet alle services zijn gezien.")
    elif data.get("runner_units_observed") is False:
        print("GitHub runner-services: niet uitleesbaar.")
    print("Er zijn geen systeeminstellingen of taken gewijzigd.")


def main():
    parser = argparse.ArgumentParser(description="Bounded user training CPU priority")
    parser.add_argument("--preview", action="store_true",
                        help="Show policy decision in JSON without changing CPU controls or state")
    parser.add_argument("--human", action="store_true",
                        help="Explain a read-only preview in Dutch")
    parser.add_argument("--runners", action="store_true",
                        help="Include read-only GitHub runner service states in preview")
    args = parser.parse_args()
    if (args.human or args.runners) and not args.preview:
        parser.error("--human and --runners require --preview")
    if not args.preview and os.geteuid() != 1000:
        raise SystemExit("Refusing to alter units outside ubuntu user scope")
    s = snapshot()
    quota, weight, mode = admission(s)
    urgent, moderate = pressure_reasons(s)
    reasons = urgent + moderate
    if args.preview:
        try:
            current = installed_cgroup()
            observed = {
                "unit_observed": True,
                "unit_active": current["active"],
                "current_quota_percent": (round(current["quota_seconds"] * 100, 2)
                                          if current["quota_seconds"] is not None else None),
                "current_weight": current["weight"],
                "recommendation_currently_applied": matches_cgroup(current, quota, weight),
            }
        except (OSError, subprocess.SubprocessError, ValueError):
            # Read-only preview still works outside an available user systemd bus.
            observed = {"unit_observed": False, "unit_active": None,
                        "current_quota_percent": None, "current_weight": None,
                        "recommendation_currently_applied": None}
        result = {**s, **observed, "mode": mode, "quota": quota,
                  "weight": weight, "reasons": reasons, "applied": False}
        if args.runners:
            result.update(runner_inventory())
        if args.human:
            human_preview(result)
        else:
            print(json.dumps(result, sort_keys=True))
        return
    actual = installed_cgroup()
    if not actual["active"]:
        write_private_state({**s, "quota": quota, "weight": weight, "mode": mode,
                             "unit_active": False, "applied_verified": False,
                             "reasons": reasons})
        print("coach-not-active: capacity observed, CPU controls left unchanged")
        return
    if not matches_cgroup(actual, quota, weight):
        # No command shell, no privileged unit and no service restart.
        subprocess.run(
            ["systemctl", "--user", "set-property", "--runtime",
             UNIT, f"CPUQuota={quota}%", f"CPUWeight={weight}"],
            check=True, timeout=12, stdout=subprocess.DEVNULL,
        )
        actual = installed_cgroup()
        if not matches_cgroup(actual, quota, weight):
            raise SystemExit("Coach policy not verified after systemd update")
    write_private_state({**s, "quota": quota, "weight": weight,
                         "mode": mode, "unit_active": True, "applied_verified": True,
                         "reasons": reasons})
    print(f"{mode}: Fuckball CPUQuota={quota}%; CPUWeight={weight}; "
          f"active runner={s['runner_active']}; sources complete={s['source_complete']}")


if __name__ == "__main__":
    main()
