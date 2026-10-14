#!/usr/bin/env python3
"""Bounded, no-mutation VPS Worker-v2 service metadata inventory.

Never serialize process identities, command lines, unit descriptions, raw
exceptions, private paths, or Firefox tab data to public Actions logs.
This is observation only: it does not attest worker health or authorize deploy.
"""
import json
import os
import re
import selectors
import signal
import time
import stat
import subprocess
from pathlib import Path

ROOT = Path("/home/ubuntu")
WORKER_SCRIPT = ROOT / "zennay-cloud/public/zcloud-worker.user.js"
EXTENSION_MANIFEST = ROOT / "snap/firefox/common/chatgpt-project-extension/manifest.json"
COMMANDS = {
    "process": ("/usr/bin/ps", "-eo", "comm="),
    "user_services": ("/usr/bin/systemctl", "--user", "list-units", "--all", "--type=service", "--no-legend", "--plain", "--no-pager"),
    "system_services": ("/usr/bin/systemctl", "list-units", "--all", "--type=service", "--no-legend", "--plain", "--no-pager"),
    "user_timers": ("/usr/bin/systemctl", "--user", "list-units", "--all", "--type=timer", "--no-legend", "--plain", "--no-pager"),
}
PREFIXES = ("zcloud", "zennay-cloud", "chatgpt", "firefox", "worker", "watchdog", "self-heal")
UNIT_RE = re.compile(r"^[a-zA-Z0-9_.@:-]{1,150}\.(service|timer)$")
VERSION_RE = re.compile(r"^// @version[ \t]+([0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4})[ \t]*$")
PROCESS_RE = re.compile(r"^[A-Za-z0-9_./:@+\[\]-]{1,80}$")
STATES = {"active", "inactive", "failed", "activating", "deactivating", "reloading"}
MAX_LINES = 4000
MAX_OUTPUT = 65536


def _safe_subprocess_env():
    """Never forward the runner's tokens or untrusted host environment."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/ubuntu",
        "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "SYSTEMD_COLORS": "0",
        "SYSTEMD_PAGER": "cat",
    }


def _bounded_process_output(argv, *, timeout_seconds=5):
    """Stream subprocess output with a hard memory and wall-time ceiling.

    subprocess.run(capture_output=True) buffered arbitrary output before
    our old post-capture size check, so it could still exhaust VPS memory.
    """
    process = None
    selector = None
    completed = False
    try:
        if timeout_seconds <= 0 or timeout_seconds > 5:
            return None
        process = subprocess.Popen(
            list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True,
            env=_safe_subprocess_env(),
        )
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout_seconds
        bounded = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                return None
            # Never read more than the remaining budget plus one sentinel.
            chunk = os.read(process.stdout.fileno(), min(4096, MAX_OUTPUT + 1 - len(bounded)))
            if not chunk:
                break
            bounded.extend(chunk)
            if len(bounded) > MAX_OUTPUT:
                return None
        if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
            return None
        decoded = bytes(bounded).decode("utf-8", "strict")
        completed = True
        return decoded
    except (OSError, UnicodeError, ValueError, TypeError, subprocess.SubprocessError):
        return None
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            # start_new_session=True isolates this invocation from every other
            # runner/Firefox process. Abort the whole private process group on
            # timeout, oversize or malformed output; killing only the parent
            # can leave a child writing after the five-second deadline.
            if not completed:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
            try:
                process.wait(timeout=1)
            except subprocess.SubprocessError:
                pass
            if process.stdout is not None:
                process.stdout.close()


def command_lines(argv, *, execute=None):
    try:
        if execute is None:
            raw = _bounded_process_output(argv)
            if raw is None:
                return None
        else:
            # Offline tests inject deterministic command outcomes without
            # spawning any VPS service or changing runtime state.
            done = execute(list(argv), capture_output=True, text=True, timeout=5, check=False)
            raw = done.stdout
            if done.returncode != 0:
                return None
        if not isinstance(raw, str) or len(raw) > MAX_OUTPUT or "\x00" in raw:
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
        # systemctl columns are UNIT LOAD ACTIVE SUB DESCRIPTION. A status
        # like "not-found active running" is contradictory: never call it
        # active merely because the third column contains that string.
        if (len(fields) < 4 or fields[1] != "loaded" or
                fields[2] not in STATES or unit in observed):
            return None
        if not re.fullmatch(r"[a-z][a-z-]{0,39}", fields[3]):
            return None
        observed.add(unit)
        count += 1
        active += fields[2] == "active"
        failed += fields[2] == "failed"
    return {"matching_units": count, "active_units": active, "failed_units": failed}


def _open_no_symlink_ancestors(path):
    """Open via directory descriptors so no parent symlink can be swapped in."""
    parts = Path(path).parts
    if len(parts) < 2 or parts[0] != "/" or any(part in ("", ".", "..") for part in parts[1:]):
        raise ValueError("non-absolute or ambiguous source path")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open("/", directory_flags)
    try:
        for component in parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                       dir_fd=directory_fd)
    finally:
        os.close(directory_fd)


def read_regular(path, maximum):
    """Read a stable regular inode with no symlinks in any ancestor."""
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 0 <= maximum <= 65536:
        return None
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")):
        return None
    descriptor = None
    try:
        # Resolve every directory as its own nofollow fd to close ancestor
        # symlink races; finally validate the actual file before/after read.
        descriptor = _open_no_symlink_ancestors(path)
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
    except (TypeError, ValueError, RecursionError):
        return None


def collect(*, execute=None, source_path=WORKER_SCRIPT, manifest_path=EXTENSION_MANIFEST):
    lines = {key: command_lines(argv, execute=execute) for key, argv in COMMANDS.items()}
    processes = summarize_processes(lines["process"])
    user = summarize_units(lines["user_services"], kind="service")
    system = summarize_units(lines["system_services"], kind="service")
    timers = summarize_units(lines["user_timers"], kind="timer")
    version = get_source_version(source_path)
    background = get_background_count(manifest_path)
    components = {
        "process": processes,
        "user_services": user,
        "system_services": system,
        "user_timers": timers,
        "source_version": version,
        "extension_background_script_count": background,
    }
    # Stable enum only: report unavailable component *types*, never paths,
    # raw exceptions, service names, process identities or credentials.
    missing = sorted(key for key, value in components.items() if value is None)
    return {
        "policy": "zcloud-v2-legacy-readonly-inventory-v1",
        "observation_complete": all(
            components[key] is not None
            for key in ("process", "user_services", "system_services", "user_timers")
        ),
        "inventory_complete": not missing,
        "missing_components": missing,
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
    report = collect()
    print("ZCLOUD_V2_LEGACY_SAFE_METADATA=" + json.dumps(report, sort_keys=True, separators=(",", ":")))
    print("NO_PROCESS_OR_TAB_MUTATION=1")
    # A successful Actions job is only a complete *inventory*, not a release
    # admission. Incomplete observation must never silently look green.
    if not report["inventory_complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
