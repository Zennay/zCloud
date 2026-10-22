#!/usr/bin/env python3
"""Bounded, read-only local zCloud runner and systemd-unit observation.

Only the intended Linux host is sampled. A listener process or active unit is
NOT proof that GitHub registered the runner, assigned a job or completed it.
Never reads registration credentials, service journals or the runner .env.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PROC = Path("/proc")
HOST = "vps-bb300bba"
RUNNER_ROOT = "/home/ubuntu/actions-runner-zcloud"
LISTENER = (RUNNER_ROOT + "/bin/Runner.Listener").encode("ascii")
WORKER = (RUNNER_ROOT + "/bin/Runner.Worker").encode("ascii")
SYSTEMCTL = Path("/usr/bin/systemctl")
SERVICE_UNIT = "actions.runner.Zennay-zCloud.zcloud-vps-1.service"
SERVICE_PROPERTIES = ("LoadState", "ActiveState", "SubState", "UnitFileState", "Result")
MAX_SYSTEMD_OUTPUT = 4096
MAX_PIDS = 20000
MAX_CMDLINE_BYTES = 4096
MAX_STAT_BYTES = 4096


def observe_systemd_service():
    """Read only five fixed systemd properties for the canonical zCloud unit.

    Never invokes start/stop/restart/enable/disable, never reads a journal or
    environment file, and never exposes PID, token, argv or unit-file contents.
    Missing systemd permissions or a timeout is UNKNOWN, not service failure.
    """
    observed_at = datetime.now(timezone.utc).isoformat()

    def unknown(reason):
        return {
            "schema": "zsentinel-systemd-service-observation-v1",
            "source": "fixed_systemctl_show_readonly",
            "service_unit": SERVICE_UNIT,
            "status": "unknown",
            "reason": reason,
            "properties": None,
            "observed_at_utc": observed_at,
            "mutations_performed": False,
            "restart_authorized": False,
        }

    if not SYSTEMCTL.is_file() or not os.access(SYSTEMCTL, os.X_OK):
        return unknown("systemctl_unavailable")
    env = os.environ.copy()
    env.update({"LC_ALL": "C", "SYSTEMD_PAGER": "cat", "SYSTEMD_COLORS": "0"})
    command = [str(SYSTEMCTL), "show", "--no-pager"]
    command.extend("--property=" + name for name in SERVICE_PROPERTIES)
    command.append(SERVICE_UNIT)
    try:
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=3, check=False, env=env)
    except subprocess.TimeoutExpired:
        return unknown("query_timeout")
    except OSError:
        return unknown("query_unavailable")
    if completed.returncode != 0:
        return unknown("query_failed")
    raw = completed.stdout
    if not isinstance(raw, bytes) or len(raw) > MAX_SYSTEMD_OUTPUT:
        return unknown("malformed_output")
    try:
        lines = raw.decode("utf-8", "strict").splitlines()
    except UnicodeError:
        return unknown("malformed_output")
    properties = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if not sep or key not in SERVICE_PROPERTIES or key in properties:
            return unknown("malformed_output")
        if len(value) > 64 or not re.fullmatch(r"[A-Za-z0-9_.:-]*", value):
            return unknown("malformed_output")
        properties[key] = value
    if set(properties) != set(SERVICE_PROPERTIES):
        return unknown("incomplete_output")
    return {
        "schema": "zsentinel-systemd-service-observation-v1",
        "source": "fixed_systemctl_show_readonly",
        "service_unit": SERVICE_UNIT,
        "status": "observed",
        "reason": None,
        "properties": properties,
        "observed_at_utc": observed_at,
        "mutations_performed": False,
        "restart_authorized": False,
    }


def _systemd_summary(report):
    service = report.get("systemd_service_observation")
    if (type(service) is not dict
            or service.get("schema") != "zsentinel-systemd-service-observation-v1"
            or service.get("status") != "observed"
            or type(service.get("properties")) is not dict):
        reason = service.get("reason") if type(service) is dict else "not_observed"
        if reason not in ("systemctl_unavailable", "query_timeout",
                          "query_unavailable", "query_failed",
                          "malformed_output", "incomplete_output", "not_observed"):
            reason = "not_observed"
        return "ONBEKEND (" + reason + ")"
    props = service["properties"]
    active = props.get("ActiveState") or "unknown"
    sub = props.get("SubState") or "unknown"
    load = props.get("LoadState") or "unknown"
    unit = props.get("UnitFileState") or "unknown"
    result = props.get("Result") or "unknown"
    return ("ActiveState=" + active + "/" + sub + "; LoadState=" + load
            + "; UnitFileState=" + unit + "; Result=" + result)


def _systemd_process_relationship(report):
    service = report.get("systemd_service_observation")
    if (type(service) is not dict or service.get("status") != "observed"
            or type(service.get("properties")) is not dict):
        return "unknown_service_state"
    if report.get("process_inventory_complete") is not True:
        return "unknown_partial_process_sample"
    listeners = report.get("listener_processes_observed")
    if type(listeners) is not int or not 0 <= listeners <= MAX_PIDS:
        return "unknown_partial_process_sample"
    active = service["properties"].get("ActiveState")
    if active == "active" and listeners == 0:
        return "unit_active_listener_not_observed"
    if active in ("inactive", "failed") and listeners > 0:
        return "listener_observed_unit_not_active"
    if active == "active" and listeners > 0:
        return "unit_active_listener_observed"
    if active in ("inactive", "failed") and listeners == 0:
        return "unit_not_active_listener_not_observed"
    return "unit_state_not_comparable"


def _runner_binary(first, process):
    """Identify only this configured runner with relative or absolute argv[0].

    runsvc.sh often starts ./bin/Runner.Listener. Never attribute a different
    repository's listener merely because its executable shares a basename.
    Only argv[0] and the /proc cwd symlink are read; secrets are not copied.
    """
    if first in (LISTENER, WORKER):
        return first
    if first not in (b"Runner.Listener", b"Runner.Worker",
                     b"./Runner.Listener", b"./Runner.Worker",
                     b"bin/Runner.Listener", b"bin/Runner.Worker",
                     b"./bin/Runner.Listener", b"./bin/Runner.Worker"):
        return None
    cwd = os.readlink(process / "cwd")
    candidate = os.fsencode(os.path.normpath(
        os.path.join(cwd, os.fsdecode(first))))
    return candidate if candidate in (LISTENER, WORKER) else None


def observe_local_runner():
    """Read exactly bounded /proc cmdline+stat, no ps subprocess or mutation."""
    if os.name != "posix" or not PROC.is_dir():
        raise ValueError("Linux /proc required")
    if socket.gethostname().split(".", 1)[0].lower() != HOST:
        raise ValueError("different host; never infer the zCloud VPS runner")
    scanned = unreadable = 0
    listeners = workers = 0
    listener_zombies = worker_zombies = 0
    # Listing /proc is non-atomic; a disappearing PID is a coverage gap, not
    # evidence of a cleanly stopped worker or authorized safe-idle.
    with os.scandir(PROC) as entries:
        for entry in entries:
            if not entry.name.isascii() or not entry.name.isdecimal():
                continue
            scanned += 1
            if scanned > MAX_PIDS:
                raise ValueError("process inventory exceeds bounded budget")
            process = PROC / entry.name
            try:
                # Read at most 4097 bytes; never dump arguments or secrets.
                with (process / "cmdline").open("rb", buffering=0) as cmd:
                    command = cmd.read(MAX_CMDLINE_BYTES + 1)
                if len(command) > MAX_CMDLINE_BYTES:
                    unreadable += 1
                    continue
                first = command.split(b"\0", 1)[0]
                # Relative argv[0] is normal for GitHub service listeners.
                # Resolve only known executable names within this root.
                target = _runner_binary(first, process)
                if target is None:
                    continue
                with (process / "stat").open("rb", buffering=0) as status:
                    raw = status.read(MAX_STAT_BYTES + 1)
                if len(raw) > MAX_STAT_BYTES:
                    unreadable += 1
                    continue
                # The process name is enclosed in parentheses and can contain
                # spaces; find the FINAL ')' before the separate state field.
                closing = raw.rfind(b")")
                if closing < 0 or raw[closing + 1:closing + 4] not in (
                        b" S ", b" R ", b" D ", b" T ", b" I ", b" Z ", b" X "):
                    unreadable += 1
                    continue
                state = raw[closing + 2:closing + 3]
                if target == LISTENER:
                    if state in (b"Z", b"X"):
                        listener_zombies += 1
                    else:
                        listeners += 1
                else:
                    if state in (b"Z", b"X"):
                        worker_zombies += 1
                    else:
                        workers += 1
            except (OSError, ValueError):
                unreadable += 1
    # Service-unit state is an independent read-only source. A denied or
    # unavailable systemd query must not invalidate the bounded /proc sample.
    service_observation = observe_systemd_service()
    # A zero count is explicitly never converted to "offline" or "idle".
    complete = unreadable == 0
    return {
        "schema": "zsentinel-local-runner-observation-v1",
        "source": "local_proc_bounded_no_credentials",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": HOST,
        "runner_scope": "Zennay/zCloud:zcloud-vps-1",
        "scanned_pid_count": scanned,
        "unreadable_or_raced_pid_count": unreadable,
        "process_inventory_complete": complete,
        "listener_processes_observed": listeners,
        "worker_processes_observed": workers,
        "zombie_listener_processes": listener_zombies,
        "zombie_worker_processes": worker_zombies,
        "listener_seen_in_process_sample": listeners > 0,
        "worker_seen_in_process_sample": workers > 0,
        "systemd_service_observation": service_observation,
        "github_registration_verified": False,
        "github_job_assignment_verified": False,
        "github_job_completed_verified": False,
        "safe_idle_authorized": False,
        "runner_restart_authorized": False,
        "worker_resumed_verified": False,
        "source_sample_not_proof_of_continuous_health": True,
        "mutations_performed": False,
    }


def runner_text(report):
    """Clear operator-language boundary between /proc and GitHub scheduling."""
    if not isinstance(report, dict) or report.get("schema") != "zsentinel-local-runner-observation-v1":
        raise ValueError("invalid local runner observation")
    return (
        "zSentinel runner op VPS: listenerprocessen=" +
        str(report["listener_processes_observed"]) +
        ", workerprocessen=" + str(report["worker_processes_observed"]) +
        ", processdekking=" + ("volledig" if report["process_inventory_complete"] else
                               "ONVOLLEDIG") +
        "; systemd-unit: " + _systemd_summary(report) +
        "; unit/proces-relatie: " + _systemd_process_relationship(report) +
        "; GitHub toegewezen job: NIET BEWEZEN; safe idle/herstel: NIET BEWEZEN;"
        " herstart of workerwijziging: NIET TOEGESTAAN."
    )


def runner_html(report):
    """Static escaped status card, no buttons to restart or claim."""
    from html import escape
    note = escape(runner_text(report))
    return ('<section aria-label="zCloud self-hosted runnerprocessen">'
            '<h2>VPS-runner · alleen lokale proceswaarneming</h2>'
            '<p>' + note + '</p>'
            '<p>Een Runner.Listener of Runner.Worker in /proc bewijst geen '
            'GitHub-runnerregistratie, toegewezen workflow of geslaagde release. '
            'Controleer de exacte job op GitHub voordat een eigenaar herstel bevestigt.</p>'
            '</section>')


def main():
    parser = argparse.ArgumentParser(description="Read local runner process presence without restart")
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args()
    try:
        result = observe_local_runner()
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        else:
            print("zSentinel VPS listenerprocessen: " + str(result["listener_processes_observed"]))
            print("zSentinel VPS workerprocessen: " + str(result["worker_processes_observed"]))
            print("Bron volledig: " + str(result["process_inventory_complete"]))
            print("Systemd zCloud-runnerunit: " + _systemd_summary(result))
            print("Unit/proces-relatie: " + _systemd_process_relationship(result))
            print("GitHub jobtoewijzing: ONBEKEND; idle, herstel en herstart NIET geautoriseerd.")
    except (OSError, ValueError) as exc:
        parser.exit(2, "zSentinel runner source unavailable: " + str(exc)[:160] + "\\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
