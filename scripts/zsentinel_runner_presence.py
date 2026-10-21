"""Local runner process presence for the zSentinel recovery desk.

Read-only /proc observation; a listener PID is NOT proof of GitHub job
assignment, runner labels, registration, health or actual CI completion.
Do not expose PIDs, arguments, environment variables or executable paths.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os

MAX_PROC_ENTRIES = 20000
KNOWN_RUNNER_DIRS = {
    "actions-runner-zcloud": "zCloud",
    "actions-runner-ftmo": "FTMO",
    "ftmo-runner": "FTMO",
    "actions-runner-haxlab": "HaxLab",
    "actions-runner-supa": "Supa",
    "actions-runner-lightup": "LightUp",
    "actions-runner-zssh": "zSSH",
    "actions-runner-raiseai": "RaiseAI",
    "actions-runner-pool-fuckball": "Fuckball",
    "actions-runner-elmo": "Elmo",
}


def _classify_executable(executable):
    """Return only allowlisted family and executable role, never a raw path."""
    parts = Path(executable).parts
    if len(parts) < 3 or parts[-2] != "bin":
        return None
    role = parts[-1].removesuffix(" (deleted)")
    if role not in ("Runner.Listener", "Runner.Worker"):
        return None
    family = KNOWN_RUNNER_DIRS.get(parts[-3])
    return (family, role) if family else None


def observe_local_runners(proc_root="/proc"):
    """Observe limited local process state without querying GitHub or systemd.

    The result is source-limited: absence of a matching listener is UNKNOWN
    if /proc access is absent, denied, or traversal was truncated.
    """
    seen = {
        family: {"listeners": 0, "workers": 0, "argv0_only": 0,
                 "listener_argv0_only": 0, "worker_argv0_only": 0}
        for family in sorted(set(KNOWN_RUNNER_DIRS.values()))
    }
    observation = {
        "observed_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "local_procfs_executable_symlinks",
        "source_available": False,
        "source_complete": False,
        "github_assignment": "NOT_OBSERVED",
        "github_runner_registration": "NOT_OBSERVED",
        "runner_health": "NOT_INFERABLE",
        "restart_authorized": False,
        "source_warnings": [],
        "families": seen,
    }
    try:
        root = Path(proc_root)
        if not root.is_dir():
            observation["source_warnings"].append("procfs_unavailable")
            return observation
        observation["source_available"] = True
        encountered = 0
        denied = 0
        with os.scandir(root) as iterator:
            for item in iterator:
                if not item.name.isdecimal():
                    continue
                encountered += 1
                if encountered > MAX_PROC_ENTRIES:
                    observation["source_warnings"].append("procfs_entry_budget_exceeded")
                    break
                lower_assurance = False
                process_dir = root / item.name
                try:
                    executable = os.readlink(process_dir / "exe")
                    signature = _classify_executable(executable)
                except FileNotFoundError:
                    # Process may have exited during this bounded enumeration.
                    continue
                except OSError:
                    # Several hardened VPS installs deny /proc/PID/exe even to
                    # a readable, same-user process. Only consider argv[0]
                    # after first matching the *kernel* process name.
                    try:
                        with (process_dir / "comm").open("r", encoding="utf-8") as fh:
                            name = fh.read(32).strip()
                        if name not in ("Runner.Listener", "Runner.Worker"):
                            # Unreadable executable links for unrelated processes
                            # do not make this runner-only source incomplete.
                            continue
                        with (process_dir / "cmdline").open("rb") as fh:
                            prefix = fh.read(512)
                        argv0, separator, _ = prefix.partition(bytes((0,)))
                        if not separator:
                            denied += 1
                            continue
                        signature = _classify_executable(
                            argv0.decode("utf-8", errors="replace")
                        )
                        if signature is not None and signature[1] != name:
                            signature = None
                        lower_assurance = signature is not None
                    except (OSError, UnicodeError):
                        denied += 1
                        continue
                if signature is None:
                    continue
                family, role = signature
                seen[family]["listeners" if role == "Runner.Listener" else "workers"] += 1
                if lower_assurance:
                    seen[family]["argv0_only"] += 1
                    seen[family][
                        "listener_argv0_only" if role == "Runner.Listener"
                        else "worker_argv0_only"
                    ] += 1
        if denied:
            observation["source_warnings"].append("procfs_processes_inaccessible")
        observation["source_complete"] = (
            encountered <= MAX_PROC_ENTRIES and denied == 0
        )
    except OSError:
        observation["source_warnings"].append("procfs_enumeration_failed")
    for family, values in seen.items():
        # Only a process presence signal. Never label it "online" or "healthy".
        values["local_observation"] = (
            "PROCESS_ARGV0_SEEN_NOT_VERIFIED"
            if values["listeners"] and values["listener_argv0_only"]
            else "EXECUTABLE_VERIFIED" if values["listeners"]
            else "WORKER_SEEN_LISTENER_NOT_OBSERVED" if values["workers"]
            else "ABSENCE_NOT_VERIFIED" if not observation["source_complete"]
            else "NOT_SEEN_IN_SNAPSHOT"
        )
    return observation


def text_report(data):
    lines = ["Lokale GitHub-runnerprocessen (alleen /proc):"]
    for family, item in sorted(data["families"].items()):
        if item["listeners"] or item["workers"]:
            lines.append(
                f"  {family}: listener {item['listeners']}; "
                f"worker {item['workers']}; {item['local_observation']}"
            )
    if any(v["workers"] and not v["listeners"]
           for v in data["families"].values()):
        lines.append("  Worker zonder waargenomen listener: behoud bestaande job; "
                     "controleer de listener onafhankelijk.")
    if not any(v["listeners"] or v["workers"] for v in data["families"].values()):
        lines.append("  Geen herkenbare runnerprocessen waargenomen.")
    if not data["source_complete"]:
        lines.append("  Beperkte procesbron; afwezigheid is niet bewezen.")
    lines.append("  Aanwezig proces != GitHub runner_id, jobtoewijzing of CI-succes.")
    return "\n".join(lines)
