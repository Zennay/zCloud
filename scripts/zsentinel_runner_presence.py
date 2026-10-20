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
        family: {"listeners": 0, "workers": 0}
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
                try:
                    executable = os.readlink(root / item.name / "exe")
                except FileNotFoundError:
                    # Process naturally exited during observation: absence cannot
                    # conclusively prove a listener failed or GitHub rejected it.
                    continue
                except (PermissionError, OSError):
                    denied += 1
                    continue
                signature = _classify_executable(executable)
                if not signature:
                    continue
                family, role = signature
                seen[family]["listeners" if role == "Runner.Listener" else "workers"] += 1
        else:
            pass
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
            "PRESENT" if values["listeners"] else
            "ABSENCE_NOT_VERIFIED" if not observation["source_complete"]
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
    if not any(v["listeners"] or v["workers"] for v in data["families"].values()):
        lines.append("  Geen herkenbare runnerprocessen waargenomen.")
    if not data["source_complete"]:
        lines.append("  Beperkte procesbron; afwezigheid is niet bewezen.")
    lines.append("  Aanwezig proces != GitHub runner_id, jobtoewijzing of CI-succes.")
    return "\n".join(lines)
