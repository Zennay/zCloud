#!/usr/bin/env python3
"""Bounded, read-only local zCloud runner process observation.

Only the live Linux host is sampled. A listener process is NOT proof that
GitHub has registered it, assigned it a job or completed the job. Never
reads the registration credentials, service journal or runner .env file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

PROC = Path("/proc")
HOST = "vps-bb300bba"
RUNNER_ROOT = "/home/ubuntu/actions-runner-zcloud"
LISTENER = (RUNNER_ROOT + "/bin/Runner.Listener").encode("ascii")
WORKER = (RUNNER_ROOT + "/bin/Runner.Worker").encode("ascii")
MAX_PIDS = 20000
MAX_CMDLINE_BYTES = 4096
MAX_STAT_BYTES = 4096


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
                first = command.split(b"\\0", 1)[0]
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
            print("GitHub jobtoewijzing: ONBEKEND; idle, herstel en herstart NIET geautoriseerd.")
    except (OSError, ValueError) as exc:
        parser.exit(2, "zSentinel runner source unavailable: " + str(exc)[:160] + "\\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
