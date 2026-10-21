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
import stat
import time
import math
import hashlib

UNIT = "fuckball-ai-coach.service"
STATE = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")) / "zcloud-background-priority.json"
RUNNER_MARKER = "/actions-runner-zcloud/"

# Increase coach capacity only after recurring low-pressure observations.
# Immediate reductions remain unaffected; this must never delay yielding CPU.
PROMOTION_SECONDS = 300
PROMOTION_MIN_OBSERVATIONS = 3
PROMOTION_MIN_GAP = 45
PROMOTION_MAX_SAMPLE_GAP = 240
MAX_PRIVATE_STATE = 16 * 1024


def _boot_token():
    """Opaque process lifetime scope for conservative promotion admission."""
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
                            boot.lower()):
            return None
        return hashlib.sha256(boot.encode("ascii")).hexdigest()[:24]
    except (OSError, UnicodeError):
        return None


def _private_state_directory(create=False):
    """Owner-only open directory, never follow a substituted final symlink."""
    if create:
        STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = os.open(
        STATE.parent, os.O_RDONLY | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        info = os.fstat(directory)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise PermissionError("CPU state parent must be private to runtime owner")
        return directory
    except BaseException:
        os.close(directory)
        raise


def read_private_state():
    """Read an owner-private regular receipt; missing proof forbids promotion."""
    try:
        directory = _private_state_directory()
    except OSError:
        return None
    try:
        fd = os.open(STATE.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_CLOEXEC", 0), dir_fd=directory)
        try:
            meta = os.fstat(fd)
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
                    or stat.S_IMODE(meta.st_mode) & 0o077
                    or meta.st_size <= 0 or meta.st_size > MAX_PRIVATE_STATE):
                return None
            with os.fdopen(fd, "rb", closefd=False) as reader:
                blob = reader.read(MAX_PRIVATE_STATE + 1)
            if len(blob) > MAX_PRIVATE_STATE:
                return None
            receipt = json.loads(blob.decode("utf-8"))
            return receipt if type(receipt) is dict else None
        finally:
            os.close(fd)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    finally:
        os.close(directory)


def promotion_policy(recommended_quota, recommended_weight, observed, previous,
                     now, boot, context=None):
    """Read-only policy decision; never trust JSON over actual systemd.

    A lower recommendation is applied immediately. An increase requires an
    unchanged current cgroup, repeated samples and five minutes of headroom.
    Missing/modified state or reboot restarts the observation period.
    """
    actual_quota = observed.get("quota_seconds")
    actual_weight = observed.get("weight")
    if not observed.get("active"):
        return recommended_quota, recommended_weight, "inactive-no-action", None
    if (type(actual_quota) not in (int, float) or
            not math.isfinite(actual_quota) or actual_quota <= 0 or
            type(actual_weight) is not int or not 1 <= actual_weight <= 10000):
        # A currently active cgroup with unknown settings may NEVER be
        # silently boosted because we cannot prove its present limits.
        return None, None, "unreadable-current-cgroup", None
    current_quota = actual_quota * 100
    # Never jump from a throttled 60% coach directly to 175% on
    # one approval window: recover via 100%/40 weight, then require
    # another independent five-minute low-pressure window for 175%.
    if recommended_quota > 100 and current_quota < 99.8:
        recommended_quota = 100
        recommended_weight = min(recommended_weight, 40)
    if recommended_quota < current_quota - 0.2:
        return recommended_quota, min(recommended_weight, actual_weight), "immediate-cpu-yield", None
    if recommended_weight < actual_weight:
        return min(recommended_quota, current_quota), recommended_weight, "immediate-weight-yield", None
    if (recommended_quota <= current_quota + 0.2
            and recommended_weight <= actual_weight):
        return recommended_quota, recommended_weight, "no-promotion-needed", None
    candidate = {
        "boot": boot, "context": context, "target_quota": recommended_quota,
        "target_weight": recommended_weight,
        "current_quota": round(current_quota, 2),
        "current_weight": actual_weight,
        "started_mono": now, "last_mono": now, "observations": 1,
    }
    old = previous.get("promotion") if type(previous) is dict else None
    wait_state = "promotion-waiting"
    if type(old) is dict:
        if (old.get("boot") != boot or old.get("context") != context
                or boot is None or context is None):
            wait_state = "promotion-reset-context"
        elif (old.get("target_quota") != recommended_quota
              or old.get("target_weight") != recommended_weight
              or old.get("current_weight") != actual_weight
              or type(old.get("current_quota")) not in (int, float)
              or not math.isfinite(old["current_quota"])
              or abs(old["current_quota"] - current_quota) >= 0.01):
            wait_state = "promotion-reset-policy"
        elif (type(old.get("last_mono")) in (int, float)
              and math.isfinite(old["last_mono"])
              and now - old["last_mono"] > PROMOTION_MAX_SAMPLE_GAP):
            wait_state = "promotion-reset-gap"
    if (boot is not None and isinstance(context, str)
            and re.fullmatch(r"[a-f0-9]{24}", context)
            and type(old) is dict and old.get("boot") == boot
            and old.get("context") == context
            and type(old.get("target_quota")) is int
            and old["target_quota"] == recommended_quota
            and type(old.get("target_weight")) is int
            and old["target_weight"] == recommended_weight
            and type(old.get("current_quota")) in (int, float)
            and abs(old["current_quota"] - current_quota) < 0.01
            and type(old.get("current_weight")) is int
            and old["current_weight"] == actual_weight
            and type(old.get("started_mono")) in (int, float)
            and type(old.get("last_mono")) in (int, float)
            and type(old.get("observations")) is int
            and 1 <= old["observations"] <= 1000
            and math.isfinite(old["started_mono"])
            and math.isfinite(old["last_mono"])
            and 0 <= old["started_mono"] <= old["last_mono"] <= now
            and 0 <= now - old["last_mono"] <= PROMOTION_MAX_SAMPLE_GAP):
        candidate["started_mono"] = old["started_mono"]
        if now - old["last_mono"] >= PROMOTION_MIN_GAP:
            candidate["observations"] = old["observations"] + 1
        else:
            candidate["observations"] = old["observations"]
        if (now - candidate["started_mono"] >= PROMOTION_SECONDS
                and candidate["observations"] >= PROMOTION_MIN_OBSERVATIONS):
            return recommended_quota, recommended_weight, "sustained-headroom", None
    # Stay at the currently verified cgroup; never increase on a single sample.
    return current_quota, actual_weight, wait_state, candidate



def _admission_context(s):
    """Stable safety epoch for upward CPU decisions; no process arguments stored.

    A newly active runner, a zCloud job or a changed pressure tier invalidates
    a historical promotion window even if the numeric CPU advice is unchanged.
    """
    urgent, moderate = pressure_reasons(s)
    safe = {"runner": s.get("runner_active") is True,
            "zcloud": s.get("zcloud_runner_active") is True,
            "pressure": sorted(urgent + moderate),
            "complete": s.get("source_complete") is True}
    return hashlib.sha256(json.dumps(safe, sort_keys=True).encode("ascii")).hexdigest()[:24]


def _same_cgroup(before, after):
    """Conservative readback guard against overlapping external CPU writers."""
    if before.get("active") is not True or after.get("active") is not True:
        return False
    qa, qb = before.get("quota_seconds"), after.get("quota_seconds")
    return (type(qa) in (int, float) and type(qb) in (int, float)
            and math.isfinite(qa) and math.isfinite(qb)
            and qa > 0 and qb > 0 and abs(qa - qb) < 0.000001
            and type(before.get("weight")) is int
            and before["weight"] == after.get("weight"))

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



def memory_oom_observation():
    """Bounded read-only host PSI/OOM signals, never proof of a runner failure.

    PSI percentages describe recent global memory stalls. The kernel oom_kill
    counter is cumulative for this boot, for every workload on the host.
    Missing procfs support stays unknown and NEVER becomes a healthy zero.
    """
    from math import isfinite
    psi_some = psi_full = kills = None
    try:
        with open("/proc/pressure/memory", "rb") as src:
            blob = src.read(4097)
        if len(blob) <= 4096:
            rows = blob.decode("ascii").splitlines()
            for kind in ("some", "full"):
                matching = [row for row in rows if row.startswith(kind + " ")]
                if len(matching) != 1:
                    continue
                fields = dict(token.split("=", 1)
                              for token in matching[0].split()[1:]
                              if token.count("=") == 1)
                raw = float(fields["avg10"])
                if isfinite(raw) and 0 <= raw <= 100:
                    if kind == "some":
                        psi_some = round(raw, 2)
                    else:
                        psi_full = round(raw, 2)
    except (OSError, UnicodeError, ValueError, KeyError):
        pass
    try:
        with open("/proc/vmstat", "rb") as src:
            blob = src.read(65537)
        if len(blob) <= 65536:
            rows = blob.decode("ascii").splitlines()
            matches = [row.split() for row in rows
                       if row.startswith("oom_kill ")]
            if len(matches) == 1 and len(matches[0]) == 2:
                raw = int(matches[0][1])
                if 0 <= raw < 2 ** 63:
                    kills = raw
    except (OSError, UnicodeError, ValueError):
        pass
    return {
        "memory_psi_some_avg10_percent": psi_some,
        "memory_psi_full_avg10_percent": psi_full,
        "kernel_oom_kills_since_boot": kills,
        "memory_psi_observed": psi_some is not None and psi_full is not None,
        "kernel_oom_counter_observed": kills is not None,
    }


def disk_used_fraction():
    """Check root filesystem pressure without traversing or reading files."""
    try:
        st = os.statvfs("/")
        if st.f_blocks <= 0 or st.f_bavail < 0 or st.f_bavail > st.f_blocks:
            return None
        return 1.0 - st.f_bavail / st.f_blocks
    except OSError:
        return None


def root_free_gib():
    """Available root capacity, not proof that cleanup or recovery occurred."""
    try:
        fs = os.statvfs("/")
        total = fs.f_blocks * fs.f_frsize
        free = fs.f_bavail * fs.f_frsize
        if total <= 0 or free < 0 or free > total:
            return None
        return round(free / (1024 ** 3), 2)
    except (OSError, ValueError, OverflowError):
        return None


def root_inode_availability():
    """Optional available inode percentage/total; no filesystem traversal."""
    try:
        fs = os.statvfs("/")
        if fs.f_files <= 0 or fs.f_favail < 0 or fs.f_favail > fs.f_files:
            return None, None
        return round(100 * fs.f_favail / fs.f_files, 2), int(fs.f_files)
    except (OSError, ValueError, OverflowError):
        return None, None


def root_volume_identity():
    """Observe Linux root mount identity/capacity without traversing files."""
    try:
        filesystem = os.statvfs("/")
        device = os.stat("/").st_dev
        size = filesystem.f_blocks * filesystem.f_frsize
        if device < 0 or size <= 0:
            return None, None
        return str(device), size
    except (OSError, ValueError, OverflowError):
        return None, None


def snapshot():
    runner, sentinel, proc_ok = workers()
    memory, swap_used = memory_pressure()
    oom = memory_oom_observation()
    disk_used = disk_used_fraction()
    root_dev, root_total = root_volume_identity()
    root_free = root_free_gib()
    inode_available, inode_total = root_inode_availability()
    try:
        cores = max(1, len(os.sched_getaffinity(0)))
        load = os.getloadavg()[0]
    except (OSError, AttributeError):
        cores, load = 1, None
    unknown = (not proc_ok or memory is None or swap_used is None or
               disk_used is None or load is None or root_free is None)
    return {
        "runner_active": runner, "zcloud_runner_active": sentinel,
        "host_load": round(load, 2) if load is not None else None,
        "cores": cores, "memory_available_gib": round(memory, 2) if memory is not None else None,
        "swap_used_percent": round(swap_used * 100, 1) if swap_used is not None else None,
        "disk_unavailable_percent": round(disk_used * 100, 1) if disk_used is not None else None,
        "root_mount_device": root_dev, "root_volume_bytes": root_total,
        "root_available_gib": root_free,
        "root_inode_available_percent": inode_available,
        "root_inode_total": inode_total,
        "source_complete": not unknown,
        **oom,
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
    free = s.get("root_available_gib")
    if type(free) in (int, float) and math.isfinite(free):
        if free < 2:
            urgent.append("critical-root-free-gib")
        elif free < 5:
            moderate.append("limited-root-free-gib")
    inode = s.get("root_inode_available_percent")
    if type(inode) in (int, float) and math.isfinite(inode) and 0 <= inode <= 100:
        if inode < 1:
            urgent.append("critical-root-inodes")
        elif inode < 5:
            moderate.append("limited-root-inodes")
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
    """Create-only private temp and atomic receipt replacement in one open dir."""
    raw = (json.dumps(data, sort_keys=True, ensure_ascii=False)
           + "\n").encode("utf-8")
    if len(raw) > MAX_PRIVATE_STATE:
        raise ValueError("CPU decision receipt exceeds the private size bound")
    directory = _private_state_directory(create=True)
    temp_name = STATE.name + "." + str(os.getpid()) + ".tmp"
    created = False
    try:
        fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        created = True
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, STATE.name,
                   src_dir_fd=directory, dst_dir_fd=directory)
        created = False
        os.fsync(directory)
    finally:
        if created:
            try:
                os.unlink(temp_name, dir_fd=directory)
            except OSError:
                pass
        os.close(directory)


RUNNER_UNIT = re.compile(r"^actions\.runner\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.service$")


def _scoped_runner_inventory(user_scope=False):
    """Read only the selected systemd manager; don't conflate source gaps."""
    scope = ["--user"] if user_scope else []
    try:
        result = subprocess.run(
            ["systemctl", *scope, "--no-pager", "--plain", "--no-legend",
             "list-units", "--all", "--type=service", "actions.runner.*"],
            check=True, timeout=8, capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return {"observed": False, "units": [], "truncated": None}
    units, seen = [], set()
    partial = len(result.stdout) > 20000
    for line in result.stdout[:20000].splitlines():
        fields = line.strip().split()
        # systemctl prepends a marker ("●") for failed/errored units in
        # otherwise plain --no-legend output. Never silently hide P0 failures.
        if fields and fields[0] == "●":
            fields = fields[1:]
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
        entry = {"unit": name, "state": state, "detail": detail, "reason": None}
        if state == "failed":
            try:
                receipt = subprocess.run(
                    ["systemctl", *scope, "--no-pager", "show", name,
                     "-p", "Result"],
                    check=True, timeout=3, capture_output=True, text=True,
                )
                value = receipt.stdout.strip().removeprefix("Result=")
                if value in {"oom-kill", "exit-code", "signal", "timeout",
                             "watchdog", "core-dump", "resources", "success"}:
                    entry["reason"] = value
            except (OSError, subprocess.SubprocessError):
                pass
        units.append(entry)
    return {"observed": True, "units": units, "truncated": partial}


def runner_inventory():
    """One-shot separate system and user service sources, no service mutation.

    Existing system-scope fields retain their historical meanings for saved
    receipts. User units have separate fields, so identical unit names in
    different managers are never silently deduplicated or conflated.
    """
    system = _scoped_runner_inventory(user_scope=False)
    user = _scoped_runner_inventory(user_scope=True)
    return {
        "runner_units_observed": system["observed"],
        "runner_units": system["units"],
        "runner_units_truncated": system["truncated"],
        "user_runner_units_observed": user["observed"],
        "user_runner_units": user["units"],
        "user_runner_units_truncated": user["truncated"],
    }


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
    "critical-root-free-gib": "minder dan 2 GiB root-schijfruimte beschikbaar",
    "limited-root-free-gib": "minder dan 5 GiB root-schijfruimte beschikbaar",
    "critical-root-inodes": "minder dan 1% vrije root-inodes beschikbaar",
    "limited-root-inodes": "minder dan 5% vrije root-inodes beschikbaar",
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
    print("Vrije root-schijfruimte: " +
          (str(data.get("root_available_gib")) + " GiB" if
           data.get("root_available_gib") is not None else "onbekend"))
    print("Vrije root-inodes: " +
          (str(data.get("root_inode_available_percent")) + "%" if
           data.get("root_inode_available_percent") is not None else "onbekend"))
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
        print("GitHub system runner-services: niet uitleesbaar.")
    if "user_runner_units_observed" in data and data.get("user_runner_units_observed") is True:
        print("GitHub user runner-services (geen bewijs van toegewezen GitHub-job):")
        for entry in data.get("user_runner_units", []):
            reason = f" ({entry['reason']})" if entry["reason"] else ""
            print(f"  {entry['unit']}: {entry['state']}/{entry['detail']}{reason}")
        if data.get("user_runner_units_truncated"):
            print("  User systemd: gedeeltelijk overzicht.")
    elif "user_runner_units_observed" in data:
        print("GitHub user runner-services: bron onbekend/onleesbaar.")
    if "promotion_status" in data:
        print("Effectieve CPU-regeling: "
              + (str(data.get("effective_quota")) + "% / gewicht "
                 + str(data.get("effective_weight"))
                 if data.get("effective_quota") is not None else "niet veilig te bepalen"))
        if data["promotion_status"] in {
            "promotion-waiting", "promotion-reset-context",
            "promotion-reset-policy", "promotion-reset-gap",
        }:
            reason = {
                "promotion-reset-context": "nieuwe runner/drukcategorie/boot",
                "promotion-reset-policy": "gewijzigde cgroup of herstelstap",
                "promotion-reset-gap": "te grote pauze tussen bronmomenten",
            }.get(data["promotion_status"], "nieuwe vrije-capaciteitsperiode")
            print("Opschalen uitgesteld (" + reason + "): minimaal 5 minuten "
                  "en drie gescheiden betrouwbare bronmomenten vereist.")
        elif data["promotion_status"] == "sustained-headroom":
            print("Opschalen toegestaan door herhaald stabiel bronbewijs; "
                  "preview heeft niets toegepast.")
    print("Er zijn geen systeeminstellingen of taken gewijzigd.")


def main():
    parser = argparse.ArgumentParser(description="Bounded user training CPU priority")
    parser.add_argument("--preview", action="store_true",
                        help="Show policy decision in JSON without changing CPU controls or state")
    parser.add_argument("--human", action="store_true",
                        help="Explain a read-only preview in Dutch")
    parser.add_argument("--runners", action="store_true",
                        help="Include read-only GitHub runner service states in preview")
    parser.add_argument("--report", metavar="NEW_PRIVATE_HTML",
                        help="Create a private offline runner recovery report; implies --runners and --preview")
    parser.add_argument("--snapshot", metavar="NEW_PRIVATE_JSON",
                        help="Create a private runner evidence receipt, optionally comparing a previous receipt")
    parser.add_argument("--previous", metavar="PRIVATE_JSON",
                        help="Previous owner-private snapshot, valid only with --snapshot")
    args = parser.parse_args()
    if (args.human or args.runners or args.report or args.snapshot or args.previous) and not args.preview:
        parser.error("read-only options require --preview")
    if args.report and args.snapshot:
        parser.error("choose --report or --snapshot (one new private artifact per invocation)")
    if args.previous and not (args.snapshot or args.report):
        parser.error("--previous requires --snapshot or --report")
    if not args.preview and os.geteuid() != 1000:
        raise SystemExit("Refusing to alter units outside ubuntu user scope")
    s = snapshot()
    quota, weight, mode = admission(s)
    urgent, moderate = pressure_reasons(s)
    reasons = urgent + moderate
    if args.preview:
        try:
            current = installed_cgroup()
            clock = time.monotonic()
            effective_quota, effective_weight, promotion_status, candidate = promotion_policy(
                quota, weight, current, read_private_state(), clock, _boot_token(),
                _admission_context(s)
            )
            observed = {
                "effective_quota": effective_quota,
                "effective_weight": effective_weight,
                "promotion_status": promotion_status,
                "promotion_observations": candidate["observations"] if candidate else None,
                "promotion_elapsed_seconds": (
                    round(clock - candidate["started_mono"])
                    if candidate else None
                ),
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
                        "recommendation_currently_applied": None,
                        "effective_quota": None, "effective_weight": None,
                        "promotion_status": "current-cgroup-unavailable",
                        "promotion_observations": None,
                        "promotion_elapsed_seconds": None}
        result = {**s, **observed, "mode": mode, "quota": quota,
                  "weight": weight, "reasons": reasons, "applied": False}
        if args.runners or args.report or args.snapshot:
            result.update(runner_inventory())
        if args.report or args.snapshot:
            # Import only for explicit private artifact creation, never in the timer.
            from datetime import datetime, timezone
            report_data = {
                "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "host": s,
                "units": {key: result[key] for key in
                          ("runner_units_observed", "runner_units", "runner_units_truncated",
                            "user_runner_units_observed", "user_runner_units",
                            "user_runner_units_truncated")},
                "coach": {
                    "observed": result["unit_observed"],
                    "active": result["unit_active"],
                    "quota": result["current_quota_percent"],
                    "weight": result["current_weight"],
                    "recommendation_matches": result["recommendation_currently_applied"],
                    "effective_quota": result["effective_quota"],
                    "effective_weight": result["effective_weight"],
                    "promotion_status": result["promotion_status"],
                    "promotion_observations": result["promotion_observations"],
                    "promotion_remaining_seconds": (
                        max(0, PROMOTION_SECONDS - result["promotion_elapsed_seconds"])
                        if result["promotion_elapsed_seconds"] is not None else None
                    ),
                },
                "quota": quota, "weight": weight, "mode": mode, "reasons": reasons,
            }
            try:
                if args.report:
                    from zsentinel_runner_report import render, save_private
                    if args.previous:
                        from zsentinel_runner_snapshot import (
                            packet, changes, load_private,
                        )
                        report_data["comparison"] = changes(
                            load_private(args.previous), packet(report_data),
                        )
                    save_private(args.report, render(report_data))
                else:
                    from zsentinel_runner_snapshot import (
                        packet, changes, load_private, save_private_json,
                    )
                    receipt = packet(report_data)
                    if args.previous:
                        receipt["comparison"] = changes(
                            load_private(args.previous), receipt,
                        )
                    save_private_json(args.snapshot, receipt)
            except (OSError, ValueError, TypeError, KeyError, UnicodeError):
                parser.exit(1, "Privé bronmoment niet gemaakt; controleer nieuw doel, bron en rechten.\n")
        if args.human:
            human_preview(result)
        else:
            print(json.dumps(result, sort_keys=True))
        if args.report:
            print("Privé offline herstelrapport gemaakt (0600, geen systeemwijziging).")
        if args.snapshot:
            print("Privé runnerbronmoment gemaakt (0600, geen serviceactie).")
        return
    actual = installed_cgroup()
    if not actual["active"]:
        write_private_state({**s, "quota": quota, "weight": weight, "mode": mode,
                             "unit_active": False, "applied_verified": False,
                             "promotion": None, "reasons": reasons})
        print("coach-not-active: capacity observed, CPU controls left unchanged")
        return
    effective_quota, effective_weight, promotion_status, candidate = promotion_policy(
        quota, weight, actual, read_private_state(), time.monotonic(), _boot_token(),
        _admission_context(s)
    )
    if effective_quota is None or effective_weight is None:
        raise SystemExit("Current active cgroup unreadable: refusing CPU change")
    if not matches_cgroup(actual, effective_quota, effective_weight):
        # Two-phase read-only admission: avoid overwriting another operator's
        # changed cgroup or boosting the coach after a runner appears.
        newer_cgroup = installed_cgroup()
        if not _same_cgroup(actual, newer_cgroup):
            raise SystemExit("Concurrent cgroup change: not applying stale CPU advice")
        newer_host = snapshot()
        newer_quota, newer_weight, newer_mode = admission(newer_host)
        if (_admission_context(newer_host) != _admission_context(s)
                or newer_quota != quota or newer_weight != weight):
            # A new pressure/runner epoch invalidates the old promotion clock.
            # Applying fresh, lower quota is safe; never boost on stale data.
            effective_quota, effective_weight, promotion_status, candidate = promotion_policy(
                newer_quota, newer_weight, newer_cgroup, None,
                time.monotonic(), _boot_token(), _admission_context(newer_host)
            )
            if effective_quota is None or effective_weight is None:
                raise SystemExit("Runner/capacity change: current policy unreadable")
            if (effective_quota > actual["quota_seconds"] * 100 + 0.2
                    or effective_weight > actual["weight"]):
                raise SystemExit("Runner/capacity changed: refusing unproven CPU increase")
            s = newer_host
            quota, weight, mode = newer_quota, newer_weight, newer_mode
            urgent, moderate = pressure_reasons(s)
            reasons = urgent + moderate
        if not matches_cgroup(newer_cgroup, effective_quota, effective_weight):
            # No command shell, no privileged unit and no service restart.
            subprocess.run(
                ["systemctl", "--user", "set-property", "--runtime",
                 UNIT, f"CPUQuota={float(effective_quota):g}%",
                 f"CPUWeight={effective_weight}"],
                check=True, timeout=12, stdout=subprocess.DEVNULL,
            )
    # Never persist 'applied_verified' on a stale initial readback, even
    # when the earlier policy required no systemd write.
    actual = installed_cgroup()
    if not matches_cgroup(actual, effective_quota, effective_weight):
        raise SystemExit("Final cgroup changed or could not be verified; state not marked applied")
    write_private_state({**s, "quota": effective_quota, "weight": effective_weight,
                         "recommended_quota": quota, "recommended_weight": weight,
                         "promotion_status": promotion_status, "promotion": candidate,
                         "mode": mode, "unit_active": True, "applied_verified": True,
                         "reasons": reasons})
    print(f"{mode}: Fuckball CPUQuota={float(effective_quota):g}%; "
          f"CPUWeight={effective_weight}; promotion={promotion_status}; "
          f"active runner={s['runner_active']}; sources complete={s['source_complete']}")


if __name__ == "__main__":
    main()
