#!/usr/bin/env python3
"""Standalone offline operator handoff for already observed worker metadata.

No external source reads, app GETs, imports from the control-plane writer, or
claims/retry/restarts. This module renders only the bounded observer receipt.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
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
    "owner_reconcile_stale_queue_claim_without_claim_transfer":
        "Een claimregel bestaat maar de lease is verlopen/ongeldig. Laat de bevoegde eigenaar de originele claimstatus handmatig beoordelen; reset of herverdeel niets.",
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


def _unique_worker_rows(receipt):
    rows = {}
    for item in receipt["entries"]:
        key = (item["project_id"], item["worker_slot"])
        if key in rows:
            raise ValueError("duplicate worker slot in continuity source")
        rows[key] = item
    return rows


def compare_worker_snapshots(prior, current):
    """Conservative bounded before/after signals; never infer task recovery.

    Reused project/window identities are mandatory. A missing current card
    means 'not in sample', not closed, healthy, released or available.
    """
    previous_at, _ = _admit(prior)
    current_at, _ = _admit(current)
    if (current_at <= previous_at
            or (current_at - previous_at).total_seconds() > 30 * 86400
            or prior.get("selected_projects") != current.get("selected_projects")
            or type(prior.get("selected_projects")) is not list
            or not 1 <= len(prior["selected_projects"]) <= MAX_PROJECTS):
        raise ValueError("worker comparison needs chronological matching project windows")
    old, new = _unique_worker_rows(prior), _unique_worker_rows(current)
    combined = sorted(set(old) | set(new))
    if len(combined) > 2 * MAX_PROJECTS * MAX_SLOTS:
        raise ValueError("worker comparison exceeds the bounded source window")
    changes = []
    for key in combined:
        before = old.get(key)
        after = new.get(key)
        state_before = before["classification"] if before is not None else None
        state_now = after["classification"] if after is not None else None
        if before is None:
            label = "nieuw_in_beperkte_steekproef_niet_nieuwe_claim"
            next_step = "Controleer actuele toewijzing en eigenaar voordat er iets verandert."
        elif after is None:
            label = "ontbreekt_in_recente_steekproef_niet_opgelost"
            next_step = "Verifieer met eigenaar of slot nog bestaat; GEEN herstelbewijs."
        elif state_before != state_now:
            label = "bronclassificatie_gewijzigd_herstel_niet_bewezen"
            next_step = "Controleer actuele claim-epoch en echte productvoortgang bij eigenaar."
        else:
            label = "opnieuw_waargenomen_geen_continuiteitsbewijs"
            next_step = "Observeer eigenaarclaim en productoutput apart; geen automatische overname."
        # A browser heartbeat can change recency classifications without
        # creating a new progress event. Compare stable indexed event cursors
        # only within the same selected worker slot. They remain unbound to
        # any queue claim epoch and are NEVER proof of delivered code.
        cursor_signal = "historic_event_cursor_unavailable"
        progress_signal = "historic_progress_cursor_unavailable"
        if before is not None and after is not None:
            previous_id = before.get("latest_indexed_event_id")
            current_id = after.get("latest_indexed_event_id")
            if type(previous_id) is int and type(current_id) is int:
                if current_id > previous_id:
                    cursor_signal = "new_indexed_browser_events_observed"
                elif current_id == previous_id:
                    cursor_signal = "same_last_event_no_new_indexed_browser_activity"
                else:
                    cursor_signal = "event_cursor_regressed_owner_source_recheck"
            previous_progress = before.get("latest_indexed_progress_event_id")
            current_progress = after.get("latest_indexed_progress_event_id")
            if type(previous_progress) is int and type(current_progress) is int:
                if current_progress > previous_progress:
                    progress_signal = "new_browser_progress_event_not_product_proof"
                elif current_progress == previous_progress:
                    progress_signal = "no_new_indexed_progress_event_observed"
                else:
                    progress_signal = "progress_cursor_regressed_owner_source_recheck"
        changes.append({
            "project_id": key[0],
            "worker_slot": key[1],
            "previous_classification": state_before,
            "current_classification": state_now,
            "interpretation": label,
            "event_cursor_observation": cursor_signal,
            "progress_cursor_observation": progress_signal,
            "safe_next_step": next_step,
            "owner_verified": False,
            "claim_epoch_bound": False,
            "recovered_verified": False,
            "mutations_performed": False,
        })
    return {
        "schema": "zsentinel-worker-handoff-continuity-v1",
        "prior_observed_at_utc": previous_at.isoformat(timespec="seconds"),
        "current_observed_at_utc": current_at.isoformat(timespec="seconds"),
        "same_selected_project_window": True,
        "history_complete": False,
        "recovery_verified": False,
        "worker_claim_identity_verified": False,
        "writes_performed": False,
        "observations": changes,
    }


def load_private_previous(path: Path):
    """Bounded no-symlink JSON input, with duplicate-key rejection."""
    if not isinstance(path, Path) or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("secure previous operator receipt path required")
    fd = os.open(
        path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
        getattr(os, "O_CLOEXEC", 0))
    with os.fdopen(fd, "rb") as source:
        st = os.fstat(source.fileno())
        if not stat.S_ISREG(st.st_mode) or not 2 <= st.st_size <= 512 * 1024:
            raise ValueError("prior operator receipt must be bounded regular JSON")
        raw = source.read(512 * 1024 + 1)
        if len(raw) != st.st_size or len(raw) > 512 * 1024:
            raise ValueError("prior operator receipt unstable or exceeds limit")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key in prior worker source")
            result[key] = value
        return result
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("prior worker JSON invalid") from exc
    _admit(parsed)
    return parsed


def _continuity_html(transition):
    rows = []
    for row in transition["observations"]:
        rows.append(
            "<li><strong>" + escape(row["project_id"]) + " · worker " +
            str(row["worker_slot"]) + "</strong> — " +
            escape(row["interpretation"]) + " · eerder: " +
            escape(row["previous_classification"] or "ONBEKEND") +
            " → nu: " + escape(row["current_classification"] or "ONBEKEND") +
            ". Eventcursor: " + escape(row["event_cursor_observation"]) +
            "; voortgangscursor: " + escape(row["progress_cursor_observation"]) +
            ". " + escape(row["safe_next_step"]) + "</li>"
        )
    return (
        '<section class="panel warn"><h2>Continuïteit sinds vorige waarneming</h2>'
        '<p class="banner">Dit is geen herstelbewijs. Eerder niet getoonde'
        ' of verdwenen workers zijn GEEN nieuwe/vrije/afgeronde claims.'
        ' Er is geen claimepoch- of productoutput-verificatie.</p>'
        '<p>Vorige bron (UTC): ' + escape(transition["prior_observed_at_utc"]) +
        ' · huidige bron: ' + escape(transition["current_observed_at_utc"]) +
        '</p><ol>' + "".join(rows) + '</ol></section>'
    )


def _continuity_markdown(transition):
    out = ["## Verschillen sinds vorige bronwaarneming",
           "Vorige bron (UTC): " + transition["prior_observed_at_utc"],
           "Nieuwste bron (UTC): " + transition["current_observed_at_utc"],
           "**NOOIT herstelbewijs:** bronvensters zijn begrensd; claimepoch en productoutput onbekend."]
    for row in transition["observations"]:
        out.append(
            "- " + row["project_id"] + "/worker " + str(row["worker_slot"]) +
            ": " + row["interpretation"] + " (" +
            (row["previous_classification"] or "ONBEKEND") + " → " +
            (row["current_classification"] or "ONBEKEND") +
            "). Eventcursor: " + row["event_cursor_observation"] +
            "; voortgangscursor: " + row["progress_cursor_observation"] +
            ". " + row["safe_next_step"])
    return "\n".join(out)


def render_html(receipt, previous=None):
    observed, body = _admit(receipt)
    if previous is not None:
        body = _continuity_html(compare_worker_snapshots(previous, receipt)) + body
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


def render_markdown(receipt, previous=None):
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
    if previous is not None:
        lines.extend(["", _continuity_markdown(compare_worker_snapshots(previous, receipt))])
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
            "- Indexed eventcursor: " +
            (str(row["latest_indexed_event_id"])
             if type(row.get("latest_indexed_event_id")) is int else "ONBEKEND") +
            "; expliciete progress-eventcursor: " +
            (str(row["latest_indexed_progress_event_id"])
             if type(row.get("latest_indexed_progress_event_id")) is int else
             "ONBEKEND") + " (geen claim-epoch of productbewijs).",
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
    if not isinstance(path, Path) or format not in ("html", "markdown", "json"):
        raise ValueError("handoff output must be an explicit private path and format")
    ext = {"html": ".html", "markdown": ".md", "json": ".json"}[format]
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
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as dest:
                dest.write(payload)
                dest.flush()
                os.fsync(dest.fileno())
        except BaseException:
            # Only the exclusive newly created inode belongs to this call.
            os.unlink(path.name, dir_fd=dirfd)
            raise
    finally:
        os.close(dirfd)
