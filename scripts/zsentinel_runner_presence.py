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
MAX_CGROUP_BYTES = 4096
MAX_PROC_STAT_BYTES = 4096
ZCLOUD_SERVICE_UNIT = "actions.runner.Zennay-zCloud.zcloud-vps-1.service"

NEXT_STEPS = {
    "PROCESS_ARGV0_SEEN_NOT_VERIFIED": (
        "Verifieer executable-metadata en GitHub job/runner-bewijs met de eigenaar; "
        "herstart niet op basis van argv0 alleen."
    ),
    "EXECUTABLE_VERIFIED": (
        "Koppel het proces aan GitHub-registratie en job-ID; aanwezigheid bewijst "
        "geen online status of CI-succes."
    ),
    "WORKER_SEEN_LISTENER_NOT_OBSERVED": (
        "Behoud de bestaande job en laat de bevoegde eigenaar de listener apart "
        "controleren voordat herstel wordt overwogen."
    ),
    "ABSENCE_NOT_VERIFIED": (
        "De procfs-bron is onvolledig; trek geen afwezigheidsconclusie en herstel "
        "niets op basis van deze snapshot."
    ),
    "NOT_SEEN_IN_SNAPSHOT": (
        "Controleer GitHub-registratie en jobtoewijzing onafhankelijk; lokale "
        "afwezigheid alleen autoriseert geen herstart."
    ),
}


def next_step_for(observation):
    """Return bounded operator guidance, never recovery authorization."""
    return NEXT_STEPS.get(
        observation,
        "Bronstatus onbekend; behoud bestaande toestand en vraag de bevoegde eigenaar om onafhankelijke observatie.",
    )
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



def _process_start_ticks(process_dir, expected_pid):
    """Read bounded /proc stat identity; return starttime ticks, never the PID."""
    fd = None
    try:
        fd = os.open(
            str(process_dir / "stat"),
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
        )
        raw = os.read(fd, MAX_PROC_STAT_BYTES + 1)
    except FileNotFoundError:
        return None, "vanished"
    except PermissionError:
        return None, "unreadable"
    except OSError:
        return None, "unavailable"
    finally:
        if fd is not None:
            os.close(fd)
    if not raw or len(raw) > MAX_PROC_STAT_BYTES:
        return None, "oversized"
    try:
        value = raw.decode("ascii")
    except UnicodeError:
        return None, "malformed"
    left, right = value.find("("), value.rfind(")")
    if left < 0 or right <= left:
        return None, "malformed"
    try:
        observed_pid = int(value[:left].strip())
        fields = value[right + 2:].split()
        if observed_pid != expected_pid or len(fields) < 20:
            return None, "identity_mismatch"
        start_ticks = int(fields[19])
    except (ValueError, IndexError):
        return None, "malformed"
    if start_ticks < 0:
        return None, "malformed"
    return start_ticks, None


def _zcloud_service_cgroup_membership(process_dir):
    """Return only whether a zCloud process belongs to its expected service cgroup.

    Never export the raw cgroup path. Unknown/oversized/malformed procfs data
    stays UNKNOWN and must not be interpreted as a service failure.
    """
    fd = None
    try:
        fd = os.open(
            str(process_dir / "cgroup"),
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
        )
        raw = os.read(fd, MAX_CGROUP_BYTES + 1)
    except OSError:
        return "UNKNOWN"
    finally:
        if fd is not None:
            os.close(fd)
    if not raw or len(raw) > MAX_CGROUP_BYTES:
        return "UNKNOWN"
    try:
        text = raw.decode("ascii")
    except UnicodeError:
        return "UNKNOWN"
    paths = []
    for line in text.splitlines():
        fields = line.split(":", 2)
        if (len(fields) != 3 or not fields[0].isdigit()
                or not fields[2].startswith("/")):
            continue
        paths.append(fields[2])
    if not paths:
        return "UNKNOWN"
    if any(ZCLOUD_SERVICE_UNIT in path.strip("/").split("/") for path in paths):
        return "EXPECTED_UNIT"
    return "OTHER_UNIT"


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
        "source": "local_procfs_executable_and_starttime_identity",
        "source_available": False,
        "source_complete": False,
        "github_assignment": "NOT_OBSERVED",
        "github_runner_registration": "NOT_OBSERVED",
        "runner_health": "NOT_INFERABLE",
        "restart_authorized": False,
        "source_warnings": [],
        "unclassified_runner_processes_observed": 0,
        "zcloud_service_cgroup": {
            "expected_unit": ZCLOUD_SERVICE_UNIT,
            "status": "NOT_OBSERVED",
            "processes_observed": 0,
            "listener_in_expected_unit": 0,
            "worker_in_expected_unit": 0,
            "listener_outside_expected_unit": 0,
            "worker_outside_expected_unit": 0,
            "membership_unknown": 0,
            "process_identity_unverified": 0,
            "source_complete": False,
            "restart_authorized": False,
        },
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
        unclassified_runner_processes = 0
        with os.scandir(root) as iterator:
            for item in iterator:
                if not item.name.isdecimal():
                    continue
                encountered += 1
                if encountered > MAX_PROC_ENTRIES:
                    observation["source_warnings"].append("procfs_entry_budget_exceeded")
                    break
                lower_assurance = False
                process_id = int(item.name)
                process_dir = root / item.name
                start_before, start_error = _process_start_ticks(process_dir, process_id)
                if start_error == "vanished":
                    continue
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
                        if signature is None or signature[1] != name:
                            # The kernel identifies a runner process, but the
                            # bounded argv0 fallback cannot bind it to a known
                            # runner family/role. Keep the source incomplete;
                            # otherwise an unknown zCloud listener could be
                            # misreported as absent.
                            unclassified_runner_processes += 1
                            continue
                        lower_assurance = True
                    except (OSError, UnicodeError):
                        denied += 1
                        continue
                if signature is None:
                    # The executable symlink can be readable while its path
                    # belongs to a runner installation not in our allowlist.
                    # If either the executable basename or kernel comm still
                    # identifies a runner, absence is not a complete sample.
                    process_name = Path(executable).name.removesuffix(" (deleted)")
                    is_runner = process_name in ("Runner.Listener", "Runner.Worker")
                    if not is_runner:
                        try:
                            with (process_dir / "comm").open("r", encoding="utf-8") as fh:
                                is_runner = fh.read(32).strip() in (
                                    "Runner.Listener", "Runner.Worker")
                        except (OSError, UnicodeError):
                            is_runner = False
                    if is_runner:
                        unclassified_runner_processes += 1
                    continue
                family, role = signature
                if start_before is None:
                    # A candidate runner without a start-time baseline cannot
                    # be bound to one process lifetime; keep absence UNKNOWN.
                    unclassified_runner_processes += 1
                    continue
                membership = (
                    _zcloud_service_cgroup_membership(process_dir)
                    if family == "zCloud" else None
                )
                start_after, after_error = _process_start_ticks(process_dir, process_id)
                if after_error == "vanished":
                    # A runner that exited during this snapshot is not a current
                    # occupancy signal; the remaining source can still be complete.
                    continue
                if start_after is None or start_after != start_before:
                    # PID reuse or unavailable identity means the executable and
                    # cgroup reads may refer to different process lifetimes.
                    unclassified_runner_processes += 1
                    continue
                seen[family]["listeners" if role == "Runner.Listener" else "workers"] += 1
                if family == "zCloud":
                    service = observation["zcloud_service_cgroup"]
                    service["processes_observed"] += 1
                    if lower_assurance:
                        # argv[0] is only a fallback identity, never enough to
                        # certify membership in the expected systemd unit.
                        service["process_identity_unverified"] += 1
                    role_key = "listener" if role == "Runner.Listener" else "worker"
                    if membership == "EXPECTED_UNIT":
                        service[f"{role_key}_in_expected_unit"] += 1
                    elif membership == "OTHER_UNIT":
                        service[f"{role_key}_outside_expected_unit"] += 1
                    else:
                        service["membership_unknown"] += 1
                if lower_assurance:
                    seen[family]["argv0_only"] += 1
                    seen[family][
                        "listener_argv0_only" if role == "Runner.Listener"
                        else "worker_argv0_only"
                    ] += 1
        if denied:
            observation["source_warnings"].append("procfs_processes_inaccessible")
        if unclassified_runner_processes:
            observation["source_warnings"].append("procfs_runner_process_unclassified")
        observation["unclassified_runner_processes_observed"] = unclassified_runner_processes
        observation["source_complete"] = (
            encountered <= MAX_PROC_ENTRIES and denied == 0
            and unclassified_runner_processes == 0
        )
    except OSError:
        observation["source_warnings"].append("procfs_enumeration_failed")
    service = observation["zcloud_service_cgroup"]
    if service["processes_observed"]:
        service["source_complete"] = (
            observation["source_complete"]
            and service["membership_unknown"] == 0
            and service["process_identity_unverified"] == 0
        )
        service["status"] = (
            "OBSERVED_COMPLETE" if service["source_complete"] else "PARTIAL"
        )
    if service["status"] == "OBSERVED_COMPLETE":
        listener_in = service["listener_in_expected_unit"]
        listener_out = service["listener_outside_expected_unit"]
        if listener_out and not listener_in and listener_out == seen["zCloud"]["listeners"]:
            service["observation"] = "LISTENER_OUTSIDE_EXPECTED_SERVICE_CGROUP"
        elif listener_out and listener_in:
            service["observation"] = "MIXED_LISTENER_SERVICE_CGROUP_MEMBERSHIP"
        elif listener_in:
            service["observation"] = "EXPECTED_SERVICE_CGROUP_MATCHED"
        else:
            service["observation"] = "NO_LISTENER_IN_SNAPSHOT"
    else:
        service["observation"] = service["status"]
    if service["processes_observed"] and not service["source_complete"]:
        observation["source_warnings"].append("zcloud_service_cgroup_incomplete")
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
        values["recommended_next_step"] = next_step_for(values["local_observation"])
    return observation


def text_report(data):
    lines = ["Lokale GitHub-runnerprocessen (alleen /proc):"]
    unclassified = data.get("unclassified_runner_processes_observed", 0)
    if type(unclassified) is int and unclassified > 0:
        lines.append(
            "  Niet aan een bekende runnerfamilie te koppelen runnerprocessen: "
            + str(min(unclassified, MAX_PROC_ENTRIES))
            + " · bron onvolledig; afwezigheid van een listener niet bewezen."
        )
    for family, item in sorted(data["families"].items()):
        if item["listeners"] or item["workers"]:
            lines.append(
                f"  {family}: listener {item['listeners']}; "
                f"worker {item['workers']}; {item['local_observation']}; "
                f"vervolgstap: {item.get('recommended_next_step') or next_step_for(item['local_observation'])}"
            )
    service = data.get("zcloud_service_cgroup")
    if isinstance(service, dict):
        if service.get("status") == "OBSERVED_COMPLETE":
            lines.append(
                "  zCloud service-cgroup (lokale proceskoppeling): "
                f"verwachte unit {service['expected_unit']}; "
                f"listener in unit {service['listener_in_expected_unit']}; "
                f"buiten unit {service['listener_outside_expected_unit']}; "
                f"worker in unit {service['worker_in_expected_unit']}; "
                f"buiten unit {service['worker_outside_expected_unit']}."
            )
            if service.get("observation") == "LISTENER_OUTSIDE_EXPECTED_SERVICE_CGROUP":
                lines.append(
                    "  HOLD: de waargenomen zCloud-listener valt buiten de verwachte "
                    "systemd-service-cgroup; eigenaar moet unit/procesidentiteit en GitHub-job "
                    "onafhankelijk vergelijken. Geen herstartautorisatie."
                )
            elif service.get("observation") == "MIXED_LISTENER_SERVICE_CGROUP_MEMBERSHIP":
                lines.append(
                    "  HOLD: zCloud-listeners hebben gemengde service-cgroup-lidmaatschappen; "
                    "geen listener opruimen of herstarten op basis van deze observatie."
                )
            zcloud = data.get("families", {}).get("zCloud", {})
            worker_count = zcloud.get("workers", 0) if isinstance(zcloud, dict) else 0
            worker_in = service.get("worker_in_expected_unit", 0)
            worker_out = service.get("worker_outside_expected_unit", 0)
            if (type(worker_count) is int and worker_count > 0
                    and type(worker_in) is int and type(worker_out) is int
                    and worker_in == 0 and worker_out == worker_count):
                lines.append(
                    "  HOLD: de waargenomen zCloud-worker valt buiten de verwachte "
                    "systemd-service-cgroup; behoud de actieve worker en vraag owner-reconciliatie."
                )
            elif (type(worker_in) is int and type(worker_out) is int
                    and worker_in > 0 and worker_out > 0):
                lines.append(
                    "  HOLD: zCloud-workers hebben gemengde service-cgroup-lidmaatschappen; "
                    "beëindig geen proces en herstart niet."
                )
        elif service.get("status") == "PARTIAL":
            lines.append(
                "  zCloud service-cgroup-koppeling gedeeltelijk/UNKNOWN; "
                "geen conclusie over service-eigenaarschap."
            )
        else:
            lines.append("  zCloud service-cgroup-koppeling niet waargenomen; status UNKNOWN.")
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
