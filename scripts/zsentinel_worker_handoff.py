#!/usr/bin/env python3
"""Standalone offline operator handoff for already observed worker metadata.

No external source reads, app GETs, imports from the control-plane writer, or
claims/retry/restarts. This module renders only the bounded observer receipt.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import os
from pathlib import Path
import stat

from zsentinel_worker_progress_observer import (
    MAX_PROJECTS, MAX_SLOTS, PROJECT, _operator_worker_priority,
    worker_progress_html,
)

STEP = {
    "owner_check_paused_worker_with_current_queue_lease":
        "Controleer met de eigenaar waarom een toegewezen worker met queuelease gepauzeerd is.",
    "owner_inspect_current_assignment_and_recent_browser_block":
        "Controleer de huidige toewijzing en het recente browserblokkadesignaal bij de eigenaar.",
    "owner_verify_product_continuation_after_block_signal":
        "Na de blokkade kwam een browser-event; controleer de echte productcontinuïteit bij de eigenaar.",
    "owner_verify_current_lease_and_real_worker_progress":
        "Controleer actuele claimlease en echte voortgang via de bevoegde eigenaar.",
    "continue_read_only_observation_without_takeover":
        "Observeer verder, zonder claimovername.",
    "owner_verify_actual_product_commit_or_handoff":
        "Laat de eigenaar de werkelijke productcommit of overdracht controleren.",
    "respect_draining_owner_hold":
        "Respecteer het drain-besluit van de eigenaar; niet herstarten.",
    "no_stalled_worker_recovery_from_this_evidence":
        "Deze bron bewijst geen vastgelopen worker; geen recovery uitvoeren.",
    "owner_recheck_current_source_without_recovery":
        "Controleer eerst de actuele bron opnieuw bij de bevoegde eigenaar.",
}

HEADER = (
    "Observatie alleen: GEEN bewezen claimidentiteit, herstel, uitvoerautorisatie, "
    "productcommit of complete workerinventaris. Geen runner of queue wordt gewijzigd."
)


def _admit(receipt):
    """Fail closed on unsafe or contradictory archived/evolving source fields."""
    # The existing worker HTML validator is the single source contract for
    # bounded card identities, claims, lease signals, timelines, owner inbox.
    # It never reads a DB or performs network I/O.
    fragment = worker_progress_html(receipt)
    if (type(receipt) is not dict
            or type(receipt.get("entries")) is not list
            or len(receipt["entries"]) > MAX_PROJECTS * MAX_SLOTS
            or receipt.get("actions_performed") is not False
            or receipt.get("worker_resumed_verified") is not False
            or receipt.get("live_worker_claims_verified") is not False
            or receipt.get("runner_restart_authorized") is not False
            or receipt.get("database_write_authorized") is not False):
        raise ValueError("worker handoff source is not read-only evidence")
    at = receipt.get("observed_at_utc")
    if type(at) is not str or len(at) > 55:
        raise ValueError("worker observation date missing")
    try:
        observed = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid worker observation date") from exc
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("worker observation missing timezone")
    if observed > datetime.now(timezone.utc):
        # A future observer clock is not fresh proof of a healthy worker.
        raise ValueError("future worker observation not trustworthy")
    for row in receipt["entries"]:
        if (type(row) is not dict
                or type(row.get("project_id")) is not str
                or not PROJECT.fullmatch(row["project_id"])
                or type(row.get("worker_slot")) is not int
                or not 1 <= row["worker_slot"] <= 16
                or row.get("safe_followup", "owner_recheck_current_source_without_recovery")
                not in STEP):
            raise ValueError("invalid worker operator handoff row")
    return observed.astimezone(timezone.utc), fragment


def render_html(receipt):
    observed, body = _admit(receipt)
    entries = receipt["entries"]
    inbox = receipt.get("owner_followup_candidates")
    actionable = len(inbox) if isinstance(inbox, list) else None
    freshness = "OUD BRONMOMENT — VERNIEUW READ-ONLY BRON" if (
        datetime.now(timezone.utc) - observed).total_seconds() > 600 else (
        "Laatste bronmoment maximaal tien minuten oud — geen live-garantie")
    action_label = (str(actionable) if actionable is not None
                    else "ONBEKEND · historisch bronmoment")
    return """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy"
content="default-src 'none'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; script-src 'none'; form-action 'none'; base-uri 'none'">
<title>zSentinel · Worker Continuïteit · privé</title>
<style>
:root{color-scheme:dark;font:16px/1.55 system-ui,sans-serif}
body{margin:0 auto;max-width:1080px;padding:clamp(18px,3vw,40px);background:#111b2a;color:#eaf0f9}
h1{font-size:clamp(1.8rem,4vw,2.7rem);line-height:1.15}
h2{font-size:1.4rem}h3{font-size:1.05rem}
header{border-bottom:1px solid #3c5774;padding-bottom:1.2rem}
.panel,aside{background:#1a293b;border:1px solid #415b78;border-radius:12px;padding:1rem;margin:1rem 0}
.warn{border-left:5px solid #f4c17c}.banner{color:#f4d29e}
.read{border-left:4px solid #87a7dd}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}
.grid .panel{min-width:0;margin:0}.small,small{color:#bbcede;font-size:.9rem}
details{margin-top:.8rem;border-top:1px solid #415b78;padding-top:.75rem}
summary{cursor:pointer;font-weight:600}
ol{padding-left:1.5rem;overflow-wrap:anywhere}
strong{color:#f6ddad}p{overflow-wrap:anywhere}
section{margin-top:1.5rem}
.badges{display:flex;gap:.7rem;flex-wrap:wrap}
.badges span{padding:.7rem 1rem;background:#21364d;border-radius:8px}
</style></head><body><header><small>PRIVÉ · READ-ONLY · GEEN EXTERNE REQUESTS</small>
<h1>zSentinel · Workercontinuïteit</h1><p>""" + escape(HEADER) + """</p>
<div class="badges"><span>Geobserveerd (UTC): <b>""" + escape(observed.isoformat(timespec="seconds")) + """</b></span>
<span>Geselecteerde workerkaarten: <b>""" + str(len(entries)) + """</b></span>
<span>Eigenaarcontrole in sample: <b>""" + escape(action_label) + """</b></span></div>
<p class="banner">""" + escape(freshness) + """</p></header><main>""" + body + """</main>
<footer><p class="small">Bron: beperkte geïndexeerde SQLite-metadata. Geen browserherstel
of automatische taakovername. Voor echte actie zijn actuele eigenaarclaims,
vereiste CI, toestemming en deploymentgates verplicht.</p></footer></body></html>"""


def render_markdown(receipt):
    observed, _ = _admit(receipt)
    entries = sorted(receipt["entries"], key=_operator_worker_priority)
    inbox = receipt.get("owner_followup_candidates")
    lines = [
        "# zSentinel · privé workercontinuïteit",
        "",
        "**Observatie UTC:** " + observed.isoformat(timespec="seconds"),
        "**Status:** " + HEADER,
        "**Brondekking:** geselecteerde worker-slots; niet volledig; onbekende workers zijn niet bewezen gezond.",
        "",
        "## Eerst aan de bevoegde eigenaar",
    ]
    if inbox is None:
        lines.append("Historische bron: actuele eigenaarinbox ONBEKEND; lees de bron opnieuw.")
    elif not inbox:
        lines.append("Geen concrete eigenaarcontrole in deze beperkte sample; dit bewijst geen herstel.")
    else:
        for row in inbox:
            step = STEP[row["safe_followup"]]
            lines.append(
                "- **" + row["project_id"] + " / worker " +
                str(row["worker_slot"]) + "** — " + step +
                " Blokkades in beperkte sample na prompt: " +
                str(row["recent_block_events_after_prompt_in_sample"]) +
                "; voortgangssignalen: " +
                str(row["recent_progress_events_after_prompt_in_sample"]) +
                ". Claim/recovery niet geverifieerd."
            )
    lines.extend(["", "## Context per geselecteerde worker"])
    for row in entries:
        age = row.get("event_age_seconds")
        progress = row.get("progress_age_seconds")
        timeline = row.get("timeline")
        followup = row.get("safe_followup", "owner_recheck_current_source_without_recovery")
        lines.extend([
            "",
            "### " + row["project_id"] + " · worker " + str(row["worker_slot"]),
            "- Signaalcategorie: `" + row["classification"] + "`.",
            "- Configuratie: `" + row["desired_state"] + "`; globale toewijzing: " +
            (str(row["allocated_global_slot"]) if row["allocated_global_slot"] is not None
             else "ONBEKEND/GEEN") + ".",
            "- Queueclaimstatus waargenomen: " +
            (row["queue_claim_status_observed"] or "ONBEKEND/GEEN") +
            "; lease nog geldig volgens bron: " +
            ("ja (geen overnamerecht)" if row["queue_lease_current_from_source"]
             else "onbekend/nee") + ".",
            "- Laatste event/leeftijd: " + (
                str(age) + "s" if type(age) is int else "ONBEKEND") +
            "; progress-tijdsignaal: " + (
                str(progress) + "s" if type(progress) is int else "ONBEKEND") + ".",
            "- Veilige volgende actie: " + STEP[followup],
        ])
        if type(timeline) is dict:
            recent = timeline.get("recent_events_newest_first")
            if type(recent) is list:
                lines.append("- Recentste (maximaal twaalf) eventtypen: " +
                    (", ".join(str(ev["event"]) for ev in recent) if recent
                     else "geen in deze beperkte bron") + ".")
        else:
            lines.append("- Historische bron: tijdlijn ONBEKEND.")
    lines.extend(["", "## Niet toegestaan op grond van dit rapport",
        "Geen claimovername, queue-reset, CI-retry, runnerstart, merge, deploy of "
        "product-geslaagdverklaring. Actuele claims en gates apart verifiëren.",
        ""])
    return "\n".join(lines)


def write_private_handoff(path: Path, content: str, *, format: str) -> None:
    """Create-only, owner-private operator report under an existing private directory.

    Never overwrite a ledger, DB, symlink or another operator's report.
    Uses O_EXCL against existing destination and O_NOFOLLOW for final path.
    Does not touch production service/queue or GitHub/Notion state.
    """
    if type(path) is not Path or format not in ("html", "markdown"):
        raise ValueError("handoff output must be an explicit private path and format")
    ext = ".html" if format == "html" else ".md"
    if path.name in ("", ".", "..") or not path.name.endswith(ext):
        raise ValueError("operator report must use matching .html or .md suffix")
    payload = content.encode("utf-8")
    if not 0 < len(payload) <= 512 * 1024:
        raise ValueError("operator report exceeds 512 KiB")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("operator report directory must already exist without symlink")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("secure nofollow file opening required")
    dirfd = os.open(
        parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
        getattr(os, "O_CLOEXEC", 0))
    created = False
    try:
        folder = os.fstat(dirfd)
        if (not stat.S_ISDIR(folder.st_mode) or folder.st_uid != os.geteuid()
                or stat.S_IMODE(folder.st_mode) & 0o077):
            raise ValueError("operator directory must be private and owned (0700)")
        # Do not allow path traversal or an accidental replacement. Resolve
        # the file relative to the already-opened private directory fd.
        if "/" in path.name or "\\" in path.name:
            raise ValueError("unsafe operator report basename")
        fd = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
            getattr(os, "O_CLOEXEC", 0),
            0o600, dir_fd=dirfd)
        created = True
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as dest:
                dest.write(payload)
                dest.flush()
                os.fsync(dest.fileno())
        except BaseException:
            # Only the exclusive newly created inode belongs to this call.
            os.unlink(path.name, dir_fd=dirfd)
            created = False
            raise
    finally:
        os.close(dirfd)
