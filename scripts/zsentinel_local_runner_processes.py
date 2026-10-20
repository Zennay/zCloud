#!/usr/bin/env python3
"""Read-only, bounded local Linux GitHub Actions runner-process inventory.

No systemctl, signals, runner credentials, git operations, service actions or
workflow dispatch. This CANNOT establish GitHub registration, labels, access,
job assignment, safe-idle readiness or permission to restart a runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path

MAX_PROCESSES = 8192
MAX_CANDIDATES = 128
MAX_GROUPS = 32
TARGETS = {"Runner.Listener": "listener", "Runner.Worker": "worker",
           "runsvc.sh": "service_wrapper"}
PID = re.compile(r"^[1-9][0-9]{0,9}$")


def observe(proc: Path = Path("/proc")):
    if not isinstance(proc, Path) or not proc.is_dir():
        raise ValueError("Linux /proc must be mounted")
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    groups = defaultdict(lambda: {"listeners": 0, "workers": 0,
                                  "service_wrappers": 0})
    counts = {"listeners": 0, "workers": 0, "service_wrappers": 0}
    examined = 0
    candidates = 0
    inaccessible = 0
    unknown_cwd = 0
    process_parent_links = []
    cutoff = False
    for entry in os.scandir(proc):
        if not PID.fullmatch(entry.name):
            continue
        examined += 1
        if examined > MAX_PROCESSES:
            cutoff = True
            break
        try:
            with open(proc / entry.name / "comm", "r", encoding="ascii") as stream:
                comm = stream.readline(100).strip()
        except (OSError, UnicodeError):
            inaccessible += 1
            continue
        kind = TARGETS.get(comm)
        if kind is None:
            continue
        candidates += 1
        if candidates > MAX_CANDIDATES:
            cutoff = True
            break
        field = {"listener": "listeners", "worker": "workers",
                 "service_wrapper": "service_wrappers"}[kind]
        counts[field] += 1
        parent_id = None
        try:
            with open(proc / entry.name / "status", "r", encoding="ascii") as status:
                for line in status:
                    if line.startswith("PPid:"):
                        raw = line.partition(":")[2].strip()
                        if raw.isascii() and raw.isdecimal():
                            parent_id = raw
                        break
        except (OSError, UnicodeError):
            pass
        # Keep raw /proc IDs exclusively in this ephemeral local scope.
        # None of these values is serialized or disclosed in the report.
        process_parent_links.append((entry.name, kind, parent_id))
        try:
            cwd = os.readlink(proc / entry.name / "cwd")
            if not cwd.startswith("/") or len(cwd) > 2048:
                raise ValueError("untrusted runner cwd")
            # A stable local association hint, not a repository, runner
            # label, access entitlement or proof of worker ownership.
            group = "local-" + hashlib.sha256(
                cwd.encode("utf-8", "surrogateescape")).hexdigest()[:16]
        except (OSError, ValueError):
            group = "unknown-cwd"
            unknown_cwd += 1
        groups[group][field] += 1
    listener_ids = {pid for pid, kind, _ in process_parent_links
                    if kind == "listener"}
    worker_parent_links = sum(
        kind == "worker" and parent in listener_ids
        for _, kind, parent in process_parent_links)
    ordered = sorted(groups.items(), key=lambda item: (
        -item[1]["workers"], -item[1]["listeners"], item[0]))
    return {
        "schema": "zsentinel-local-runner-processes-v1",
        "source": "bounded_linux_proc_comm_cwd_readonly",
        "observed_at_utc": observed,
        "coverage": "partial" if cutoff or inaccessible or unknown_cwd else "bounded_observed",
        "numeric_proc_entries_seen": min(examined, MAX_PROCESSES),
        "candidate_processes_seen": min(candidates, MAX_CANDIDATES),
        "inaccessible_proc_entries": min(inaccessible, MAX_PROCESSES),
        "unknown_cwd_processes_seen": unknown_cwd,
        "listener_processes_seen": counts["listeners"],
        "worker_processes_seen": counts["workers"],
        "worker_parent_listener_links_seen": worker_parent_links,
        "workers_without_verified_parent_listener_link": (
            counts["workers"] - worker_parent_links),
        "process_tree_is_non_atomic_snapshot": True,
        "service_wrapper_processes_seen": counts["service_wrappers"],
        "groups_truncated": len(ordered) > MAX_GROUPS,
        "cwd_association_groups": [
            {"local_reference": name, **values}
            for name, values in ordered[:MAX_GROUPS]],
        "github_repo_binding_verified": False,
        "runner_labels_verified": False,
        "runner_online_verified": False,
        "active_job_ownership_verified": False,
        "safe_to_restart_verified": False,
        "production_deployed_verified": False,
        "actions_performed": False,
        "owner_action": (
            "An active Runner.Worker process was observed. Do not interrupt "
            "work; owner must bind listeners to GitHub registration, labels, "
            "repository permission and exact job receipts."
            if counts["workers"] else
            "No Runner.Worker was observed in this bounded process sample. "
            "This is NOT safe-idle proof or restart authority. Owner must "
            "verify actual GitHub job assignment and service permissions."),
    }


def _admit(report):
    if (not isinstance(report, dict)
            or report.get("schema") != "zsentinel-local-runner-processes-v1"
            or report.get("source") != "bounded_linux_proc_comm_cwd_readonly"
            or report.get("coverage") not in ("partial", "bounded_observed")
            or any(report.get(key) is not False for key in (
                "github_repo_binding_verified", "runner_labels_verified",
                "runner_online_verified", "active_job_ownership_verified",
                "safe_to_restart_verified", "production_deployed_verified",
                "actions_performed"))
            or not isinstance(report.get("cwd_association_groups"), list)
            or len(report["cwd_association_groups"]) > MAX_GROUPS):
        raise ValueError("untrusted runner-process inventory")
    for key in ("listener_processes_seen", "worker_processes_seen",
                "service_wrapper_processes_seen", "candidate_processes_seen",
                "numeric_proc_entries_seen", "inaccessible_proc_entries",
                "unknown_cwd_processes_seen",
                "worker_parent_listener_links_seen",
                "workers_without_verified_parent_listener_link"):
        value = report.get(key)
        if type(value) is not int or not 0 <= value <= MAX_PROCESSES:
            raise ValueError("unbounded runner process counter")
    if (report.get("process_tree_is_non_atomic_snapshot") is not True
            or report["worker_parent_listener_links_seen"] +
                report["workers_without_verified_parent_listener_link"] !=
                report["worker_processes_seen"]):
        raise ValueError("untrusted or inconsistent worker parent observations")
    seen = set()
    for item in report["cwd_association_groups"]:
        if not isinstance(item, dict) or any(
                type(item.get(k)) is not int or not 0 <= item[k] <= MAX_CANDIDATES
                for k in ("listeners", "workers", "service_wrappers")):
            raise ValueError("invalid process grouping")
        ref = item.get("local_reference")
        if (type(ref) is not str
                or not re.fullmatch(r"local-[a-f0-9]{16}|unknown-cwd", ref)
                or ref in seen):
            raise ValueError("invalid source-local runner group")
        seen.add(ref)
    return report


def render_text(report):
    r = _admit(report)
    lines = [
        "zSentinel · LIVE LOKALE /proc-RUNNEROBSERVATIE · GEEN GITHUB STATUS",
        "Bron UTC: " + r["observed_at_utc"] + " · coverage " + r["coverage"],
        "Runner.Listener %d · Runner.Worker %d · runsvc.sh %d" % (
            r["listener_processes_seen"], r["worker_processes_seen"],
            r["service_wrapper_processes_seen"]),
        "Worker->Listener parentrelaties in eigen /proc-steekproef: %d; andere worker-parentrelaties %d." %
            (r["worker_parent_listener_links_seen"],
             r["workers_without_verified_parent_listener_link"]),
        "Lokale runner-root niet toegankelijk: %d processen; die groep is NIET één bewezen installatie." %
            r["unknown_cwd_processes_seen"],
        "Repo/labels/registratie/job-toewijzing: ONBEKEND; geen herstel of restartautorisatie.",
        "Menselijk vervolg: " + r["owner_action"],
    ]
    for group in r["cwd_association_groups"]:
        lines.append("%s: listener=%d worker=%d wrapper=%d" % (
            group["local_reference"], group["listeners"],
            group["workers"], group["service_wrappers"]))
    if r["groups_truncated"]:
        lines.append("Niet alle bronassociaties zijn getoond; geen nulclaim.")
    return "\n".join(lines) + "\n"


def render_html(report):
    r = _admit(report)
    rows = "".join(
        "<li><code>%s</code>: listeners %d · workers %d · wrappers %d</li>" % (
            escape(group["local_reference"]), group["listeners"],
            group["workers"], group["service_wrappers"])
        for group in r["cwd_association_groups"])
    return (
        '<section class="panel warn">'
        '<h2>Lokale VPS-runnerprocessen · niet hetzelfde als GitHub-runnerstatus</h2>'
        '<p>Snapshot UTC ' + escape(r["observed_at_utc"]) + '; coverage ' +
        escape(r["coverage"]) + '</p>'
        '<p><strong>Runner.Listener ' + str(r["listener_processes_seen"]) +
        ' · Runner.Worker ' + str(r["worker_processes_seen"]) +
        ' · runsvc.sh ' + str(r["service_wrapper_processes_seen"]) + '</strong></p>'
        '<p>Lokale Runner.Worker met Runner.Listener als procesouder: ' +
        str(r["worker_parent_listener_links_seen"]) +
        '. Geen bewijs van GitHub job/label of continue werking.</p>'
        '<p>Niet-verifieerbare lokale runner-mappen: ' +
        str(r["unknown_cwd_processes_seen"]) +
        '; deze procesgroep is geen bewijs van één installatie.</p>'
        '<p>Geen vastgestelde repositoryregistratie, labels, GitHub-jobtoewijzing,'
        ' veilige idle-status of herstelde productie. Nooit automatisch herstarten.</p>'
        '<p>' + escape(r["owner_action"]) + '</p>'
        '<details><summary>Lokale procesgroep-associaties, geen repo-ID</summary>'
        '<ol>' + rows + '</ol></details>'
        + ('<p>Afgekapt bronoverzicht: resterende groepen onbekend.</p>'
           if r["groups_truncated"] else '') + '</section>'
    )


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Read only Linux runner process metadata; no GitHub or service actions")
    p.add_argument("--format", choices=("text", "json", "html"), default="text")
    p.add_argument("--output", type=Path, help="new private 0600 report; no overwrite")
    args = p.parse_args(argv)
    try:
        result = observe()
        data = (render_text(result) if args.format == "text"
                else render_html(result) if args.format == "html"
                else json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
        if args.output is not None:
            from zsentinel_private_output import write_private_output
            fmt = "txt" if args.format == "text" else args.format
            write_private_output(args.output, data, fmt)
        else:
            print(data)
    except (OSError, ValueError, UnicodeError) as exc:
        p.exit(2, "zSentinel local runner observation unavailable: " + str(exc)[:140] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
