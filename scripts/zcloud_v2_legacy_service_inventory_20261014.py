#!/usr/bin/env python3
"""Bounded, no-mutation VPS Worker-v2 service metadata inventory.

Never serialize process identities, command lines, unit descriptions, raw
exceptions, private paths, or Firefox tab data to public Actions logs.
This is observation only: it does not attest worker health or authorize deploy.
"""
import json
import os
import re
import stat
import subprocess
from pathlib import Path

ROOT = Path("/home/ubuntu")
WORKER_SCRIPT = ROOT / "zennay-cloud/public/zcloud-worker.user.js"
EXTENSION_MANIFEST = ROOT / "snap/firefox/common/chatgpt-project-extension/manifest.json"
COMMANDS = {
    "process": ("ps", "-eo", "comm="),
    "user_services": ("systemctl", "--user", "list-units", "--all", "--type=service", "--no-legend", "--plain", "--no-pager"),
    "system_services": ("systemctl", "list-units", "--all", "--type=service", "--no-legend", "--plain", "--no-pager"),
    "user_timers": ("systemctl", "--user", "list-units", "--all", "--type=timer", "--no-legend", "--plain", "--no-pager"),
}
PREFIXES = ("zcloud", "zennay-cloud", "chatgpt", "firefox", "worker", "watchdog", "self-heal")
UNIT_RE = re.compile(r"^[a-zA-Z0-9_.@:-]{1,150}\.(service|timer)$")
VERSION_RE = re.compile(r"^// @version[ \t]+([0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4})[ \t]*$")
PROCESS_RE = re.compile(r"^[A-Za-z0-9_./:@+\[\]-]{1,80}$")
STATES = {"active", "inactive", "failed", "activating", "deactivating", "reloading"}
MAX_LINES = 4000
MAX_OUTPUT = 65536


def command_lines(argv, *, execute=subprocess.run):
    try:
        done = execute(list(argv), capture_output=True, text=True, timeout=5, check=False)
        raw = done.stdout
        if (done.returncode != 0 or not isinstance(raw, str)
                or len(raw) > MAX_OUTPUT or "\x00" in raw):
            return None
        lines = raw.splitlines()
        return lines if len(lines) <= MAX_LINES else None
    except (OSError, ValueError, TypeError, UnicodeError, subprocess.SubprocessError):
        return None


def summarize_processes(lines):
    if lines is None:
        return None
    count = 0
    for line in lines:
        # ps "comm" is a command *name*, never command-line arguments.
        word = line.strip().lower()
        if not PROCESS_RE.fullmatch(word):
            return None
        if word in ("firefox", "firefox-bin", "web-ext", "web-ext-run"):
            count += 1
    return count


def summarize_units(lines, *, kind):
    if lines is None:
        return None
    observed = set()
    count = active = failed = 0
    for line in lines:
        fields = line.split()
        if fields and fields[0] in ("●", "○"):
            fields = fields[1:]
        if not fields:
            continue
        unit = fields[0]
        # Only the first column is a unit identity; descriptions are ignored.
        if not UNIT_RE.fullmatch(unit):
            # Malformed source is unknown, not proof of zero services.
            return None
        if not unit.endswith("." + kind):
            continue
        if not unit.lower().startswith(PREFIXES):
            continue
        if len(fields) < 4 or fields[2] not in STATES or unit in observed:
            return None
        observed.add(unit)
        count += 1
        active += fields[2] == "active"
        failed += fields[2] == "failed"
    return {"matching_units": count, "active_units": active, "failed_units": failed}


def read_regular(path, maximum):
    """Read only the regular inode opened; reject symlink swaps and concurrent edits."""
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 0 <= maximum <= 65536:
        return None
    if not hasattr(os, "O_NOFOLLOW"):
        return None
    descriptor = None
    try:
        # A prior Path.is_symlink()/stat() check races with a hostile replace.
        # O_NOFOLLOW validates the final component at the actual open syscall.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= maximum:
            return None
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            raw = stream.read(maximum + 1)
        after = os.fstat(descriptor)
        if (not stat.S_ISREG(after.st_mode) or
                (before.st_dev, before.st_ino, before.st_size,
                 before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size,
                 after.st_mtime_ns, after.st_ctime_ns) or
                len(raw) != after.st_size):
            return None
        return raw.decode("utf-8", "strict")
    except (OSError, UnicodeError, ValueError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def get_source_version(path=WORKER_SCRIPT):
    text = read_regular(path, 65536)
    if text is None:
        return None
    versions = [match.group(1) for line in text.splitlines()[:8]
                if (match := VERSION_RE.fullmatch(line))]
    return versions[0] if len(versions) == 1 else None


def get_background_count(path=EXTENSION_MANIFEST):
    raw = read_regular(path, 65536)
    if raw is None:
        return None
    try:
        def unique_keys(pairs):
            values = {}
            for key, value in pairs:
                if key in values:
                    raise ValueError("ambiguous manifest key")
                values[key] = value
            return values

        manifest = json.loads(raw, object_pairs_hook=unique_keys)
        if not isinstance(manifest, dict):
            return None
        background = manifest.get("background")
        if not isinstance(background, dict):
            return None
        scripts = background.get("scripts")
        if not isinstance(scripts, list) or len(scripts) > 1000:
            return None
        if any(not isinstance(item, str) for item in scripts):
            return None
        return len(scripts)
    except (TypeError, ValueError):
        return None


def collect(*, execute=subprocess.run, source_path=WORKER_SCRIPT, manifest_path=EXTENSION_MANIFEST):
    lines = {key: command_lines(argv, execute=execute) for key, argv in COMMANDS.items()}
    processes = summarize_processes(lines["process"])
    user = summarize_units(lines["user_services"], kind="service")
    system = summarize_units(lines["system_services"], kind="service")
    timers = summarize_units(lines["user_timers"], kind="timer")
    version = get_source_version(source_path)
    background = get_background_count(manifest_path)
    return {
        "policy": "zcloud-v2-legacy-readonly-inventory-v1",
        "observation_complete": all(value is not None for value in (processes, user, system, timers)),
        "firefox_process_count": processes,
        "user_services": user,
        "system_services": system,
        "user_timers": timers,
        "source_version": version,
        "extension_background_script_count": background,
        "mutation_performed": False,
        "production_verified": False,
        "worker_generation_verified": False,
        "identities_exposed": False,
    }


def main():
    print("ZCLOUD_V2_LEGACY_SAFE_METADATA=" + json.dumps(collect(), sort_keys=True, separators=(",", ":")))
    print("NO_PROCESS_OR_TAB_MUTATION=1")


if __name__ == "__main__":
    main()
