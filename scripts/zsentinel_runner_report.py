#!/usr/bin/env python3
"""Create an owner-private, one-shot zSentinel runner recovery workbench.

Observes host capacity and user/system GitHub runner services without starting,
stopping, retrying, claiming, or modifying any workloads. Does not fetch secrets.
"""
import argparse
from datetime import datetime, timezone
from html import escape
import os
from pathlib import Path
import subprocess
import stat
import time

from zsentinel_background_priority import (
    PROMOTION_SECONDS, _admission_context, _boot_token, admission, installed_cgroup,
    matches_cgroup, pressure_reasons, promotion_policy, read_private_state,
    runner_inventory, snapshot,
)

REASON_NL = {
    "incomplete-capacity-source": "Onvolledige systeemmetingen",
    "zcloud-github-job-running": "zCloud GitHub-job waargenomen",
    "other-github-job-running": "Andere GitHub-job waargenomen",
    "high-system-load": "Hoge systeemload",
    "elevated-system-load": "Verhoogde systeemload",
    "low-available-memory": "Weinig beschikbaar werkgeheugen",
    "limited-available-memory": "Beperkt beschikbaar werkgeheugen",
    "high-swap-use": "Hoog swapgebruik",
    "elevated-swap-use": "Verhoogd swapgebruik",
    "low-root-disk-headroom": "Weinig vrije root-schijfruimte",
    "limited-root-disk-headroom": "Beperkte root-schijfruimte",
    "critical-root-free-gib": "Minder dan 2 GiB vrije rootruimte",
    "limited-root-free-gib": "Minder dan 5 GiB vrije rootruimte",
    "critical-root-inodes": "Minder dan 1% root-inodes beschikbaar",
    "limited-root-inodes": "Minder dan 5% root-inodes beschikbaar",
}


def _value(value, suffix=""):
    return "Onbekend" if value is None else f"{escape(str(value))}{suffix}"


def classify(unit):
    """Present service evidence without inventing a successful GitHub job."""
    state = unit.get("state", "unknown")
    reason = unit.get("reason")
    if state == "failed":
        return ("urgent", "Mislukt", "Controleer service-eigenaar, OOM-log en RAM/swap vóór herstel."
                if reason == "oom-kill" else
                "Laat de bevoegde eigenaar de systemd-fout bekijken vóór herstart.")
    if state == "active":
        return "ok", "Service actief", "GitHub-toewijzing en uitvoerende job niet bewezen."
    if state == "inactive":
        return "hold", "Service gestopt", "Controleer eerst of stoppen bewust is; niet automatisch starten."
    return "unknown", "Status onzeker", "Controleer bron en eigenaarschap; geen automatische ingreep."


def collect():
    host = snapshot()
    quota, weight, mode = admission(host)
    urgent, moderate = pressure_reasons(host)
    units = runner_inventory()
    try:
        current = installed_cgroup()
        clock = time.monotonic()
        effective_quota, effective_weight, promotion_status, promotion = promotion_policy(
            quota, weight, current, read_private_state(), clock, _boot_token(),
            _admission_context(host)
        )
        coach = {
            "observed": True,
            "effective_quota": effective_quota,
            "effective_weight": effective_weight,
            "promotion_status": promotion_status,
            "promotion_observations": promotion["observations"] if promotion else None,
            "promotion_remaining_seconds": (
                max(0, round(PROMOTION_SECONDS - (clock - promotion["started_mono"])))
                if promotion else None
            ),
            "active": current["active"],
            "weight": current["weight"],
            "quota": round(current["quota_seconds"] * 100, 2)
            if current["quota_seconds"] is not None else None,
            "recommendation_matches": matches_cgroup(current, quota, weight),
        }
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        # Deliberately redact systemd error text (may include private paths).
        coach = {"observed": False, "active": None, "weight": None,
                 "quota": None, "recommendation_matches": None,
                 "effective_quota": None, "effective_weight": None,
                 "promotion_status": "source-unavailable",
                 "promotion_observations": None,
                 "promotion_remaining_seconds": None}
    return {
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "host": host, "units": units, "coach": coach,
        "quota": quota, "weight": weight, "mode": mode,
        "reasons": urgent + moderate,
    }


def recovery_priorities(data):
    """Conservative operator decisions, never executable recovery instructions."""
    host = data["host"]
    unit_source = data["units"]
    from zsentinel_runner_snapshot import _unit_states, _user_unit_states
    system_observed = unit_source.get("runner_units_observed") is True
    user_observed = unit_source.get("user_runner_units_observed") is True
    # Validate both managers separately. A same-named service in two managers
    # is two distinct observations and must never overwrite another one.
    system_rows = _unit_states({"units": unit_source}) if system_observed else {}
    user_rows = _user_unit_states({"units": unit_source})
    entries = [("system", row) for row in system_rows.values()] + [
        ("user", row) for row in user_rows.values()] if user_observed else [
        ("system", row) for row in system_rows.values()]
    failed = [scope for scope, row in entries if row["state"] == "failed"]
    stopped = [scope for scope, row in entries if row["state"] == "inactive"]
    active = [scope for scope, row in entries if row["state"] == "active"]
    # Partial/malformed capacity is UNKNOWN, never falsely healthy and
    # never a TypeError that prevents an owner handoff.
    import math
    def bounded(value, *, positive=False):
        return (type(value) in (int, float) and math.isfinite(value)
                and (value > 0 if positive else value >= 0))
    load, cores = host.get("host_load"), host.get("cores")
    swap, disk = host.get("swap_used_percent"), host.get("disk_unavailable_percent")
    inode, free = host.get("root_inode_available_percent"), host.get("root_available_gib")
    under_pressure = (
        host.get("source_complete") is not True
        or not bounded(load) or not bounded(cores, positive=True)
        or not bounded(swap) or swap > 100
        or not bounded(disk) or disk > 100
        or (inode is not None and (not bounded(inode) or inode > 100))
        or (free is not None and not bounded(free))
    )
    if not under_pressure:
        under_pressure = (
            swap >= 80 or disk >= 90
            or (inode is not None and inode < 1)
            or (free is not None and free < 2)
            or load > cores * 1.35
        )
    actions = []
    if under_pressure:
        actions.append(("P0 · capaciteit veiligstellen",
                        "Geheugen-, swap-, load- of schijfdruk is hoog of onvoldoende "
                        "geobserveerd. Start geen extra workers of OOM-gefaalde "
                        "runner-services zolang de bevoegde beheerder niet "
                        "onafhankelijk heeft vastgesteld dat er voldoende ruimte is."))
    oom_system = sum(1 for row in system_rows.values()
                     if row["state"] == "failed" and row.get("reason") == "oom-kill")
    oom_user = sum(1 for row in user_rows.values()
                   if row["state"] == "failed" and row.get("reason") == "oom-kill")
    if oom_system or oom_user:
        actions.append((
            "P0 · OOM-kill bij GitHub-runner-service",
            str(oom_system) + " systeemrunner(s) en " + str(oom_user) +
            " gebruikersrunner(s) melden systemd oom-kill. De bevoegde "
            "runtime-eigenaar onderzoekt MemAvailable, swap, geheugen-PSI "
            "en kernel-OOM-bron vóór elke herstart. Linux OOM-tellers "
            "bewijzen op zichzelf niet welke runner werd beëindigd."
        ))
    if failed:
        actions.append(("P1 · gefaalde runner-services",
                        f"{len(failed)} service(s) hebben een systemd-fout "
                        f"(system={failed.count('system')}, user={failed.count('user')}). "
                        "De bevoegde service-eigenaar onderzoekt oorzaak en "
                        "huidige GitHub-labelroute; dit rapport voert geen herstart uit."))
    if stopped:
        actions.append(("P1 · gestopte runner-services",
                        f"{len(stopped)} service(s) staan uit "
                        f"(system={stopped.count('system')}, user={stopped.count('user')}). Een geslaagde "
                        "stop kan bewust zijn; eerst eigenaarsintentie controleren."))
    if active:
        actions.append(("P2 · actieve services",
                        f"{len(active)} service(s) zijn actief "
                        f"(system={active.count('system')}, user={active.count('user')}). GitHub-jobtoewijzing, "
                        "actuele workflowstatus en CI blijven afzonderlijk te bevestigen."))
    if (not system_observed or unit_source.get("runner_units_truncated")
            or not user_observed or unit_source.get("user_runner_units_truncated")):
        actions.append(("Brondekking onvolledig",
                        "Systeem- en/of gebruikersrunnerinventaris is onvolledig. Ontbrekende services of jobs "
                        "mogen niet als gezond, hersteld of verwijderd worden beschouwd."))
    if not actions:
        actions.append(("Geen aantoonbare herstelactie",
                        "De beperkte lokale observatie bewijst geen afwezigheid van "
                        "GitHub- of workflowblokkades."))
    return actions


def render_comparison(comparison):
    """Visible T0/T1 changes without implying a healed CI workflow."""
    if comparison is None:
        return ""
    observed_complete = comparison.get("complete_service_coverage") is True
    reboot = comparison.get("host_reboot_observed")
    if reboot is True:
        boot_note = ('<p class="warning"><strong>VPS opnieuw opgestart tussen T0 en T1.</strong> '
                     'Een service die nu actief is, bewijst geen doorlopende worker of '
                     'geslaagde GitHub-job. Laat de eigenaar opdrachten en claims afzonderlijk bevestigen.</p>')
    elif comparison.get("same_boot_verified") is True:
        boot_note = ('<p class="muted">Dezelfde Linux-opstartsessie bevestigd '
                     'voor beide bronmomenten. Dit is geen job- of CI-bewijs.</p>')
    else:
        boot_note = ('<p class="warning">Opstartsessie niet op beide bronmomenten '
                     'geverifieerd (bijvoorbeeld oud T0 zonder bootvingerafdruk). '
                     'Continuïteit van runners blijft onbekend.</p>')
    heading = "Beide servicelijsten volledig binnen de bronlimiet" if observed_complete else (
        "Onvolledige bron: ontbrekende runner mag niet als hersteld tellen"
    )
    labels = {
        "newly_observed": "Nieuw waargenomen",
        "no_longer_observed": "Niet meer waargenomen",
        "service_state_changed": "Systemd-toestand gewijzigd",
        "previous_source_gap": "Alleen nu gezien; vorige bron onvolledig",
        "current_source_gap": "Nu niet gezien; huidige bron onvolledig",
    }
    rows = []
    for entry in comparison.get("transitions", [])[:40]:
        before = entry.get("previous") or {}
        after = entry.get("current") or {}
        first = before.get("state", "niet gezien")
        second = after.get("state", "niet gezien")
        label = labels.get(entry.get("change"), "Onzekere wijziging")
        rows.append(
            '<li class="transition"><strong>' + escape(label) + '</strong> · '
            + escape(str(entry.get("unit", "")))
            + '<br><span class="muted">' + escape(str(first)) + ' → '
            + escape(str(second)) + '</span><br>'
            + '<small>Incidentcode: ' + escape(str(entry.get("incident_key", "")))
            + '</small></li>'
        )
    items = "".join(rows) if rows else "<li>Geen verandering waargenomen in het beperkte bronbereik.</li>"
    capacity = comparison.get("capacity_comparison") or {}
    cap_labels = {
        "host_load": ("Systeemload", ""),
        "memory_available_gib": ("Beschikbaar RAM", " GiB"),
        "swap_used_percent": ("Swapgebruik", " procentpunt"),
        "disk_unavailable_percent": ("Rootruimte niet beschikbaar", " procentpunt"),
        "root_available_gib": ("Rootruimte beschikbaar", " GiB"),
        "root_inode_available_percent": ("Root-inodes beschikbaar", " procentpunt"),
    }
    metric_rows = []
    for metric in capacity.get("metrics", [])[:6]:
        name, unit = cap_labels.get(metric.get("metric"), ("Onbekende meting", ""))
        if metric.get("comparable") is True:
            delta = metric.get("difference")
            fmt = f"{delta:+g}" if isinstance(delta, (float, int)) else "onbekend"
            metric_rows.append(
                "<li><strong>" + escape(name) + "</strong>: "
                + _value(metric.get("previous")) + " → "
                + _value(metric.get("current")) + " (" + escape(fmt + unit) + ")</li>"
            )
        else:
            metric_rows.append(
                "<li><strong>" + escape(name)
                + "</strong>: niet vergelijkbaar (bron ontbreekt of rootvolume verschilt)</li>"
            )
    capacity_html = (
        '<h3>Capaciteit tussen bronmomenten</h3><ul>'
        + ("".join(metric_rows) or "<li>Geen brongebonden capaciteit beschikbaar.</li>")
        + '</ul><p class="muted">Verschillen zijn momentopnames, geen bewijs van '
        'vrijgemaakte bestanden, herstelde runners of prestaties.</p>'
    )
    if not capacity.get("root_comparable"):
        capacity_html += ('<p class="warning">Geen identieke root-mount en '
                          'volumeomvang vastgesteld; schijfdelta is daarom onbekend.</p>')
    oom = comparison.get("memory_oom_comparison")
    if isinstance(oom, dict):
        if oom.get("oom_counter_same_boot_comparable") is True:
            count = oom.get("kernel_oom_kills_delta")
            kill_text = (
                "Kernel-OOM-toename in hetzelfde geverifieerde Linux-bootvenster: "
                + str(count) + "."
                if type(count) is int and 0 <= count < 2 ** 63
                else "Ongeldige kernel-OOM-bron: ONBEKEND."
            )
        else:
            kill_text = ("Kernel-OOM-verschil ONBEKEND (bron ontbreekt, "
                         "tegenstrijdig of opnieuw opgestart).")
        psi_text = (
            "Hostbrede PSI full avg10: "
            + _value(oom.get("previous_psi_full_avg10_percent"), "%") +
            " → " + _value(oom.get("current_psi_full_avg10_percent"), "%") +
            "; some avg10: "
            + _value(oom.get("previous_psi_some_avg10_percent"), "%") +
            " → " + _value(oom.get("current_psi_some_avg10_percent"), "%") + "."
            if oom.get("memory_psi_source_comparable") is True
            else "Geheugen-PSI-bron op een of beide momenten ONBEKEND."
        )
    else:
        kill_text = "Geen kerneltellervergelijking in dit oude bronformaat."
        psi_text = "Geen geheugen-PSI-vergelijking in dit oude bronformaat."
    memory_html = (
        '<h3>Hostbreed OOM- en geheugenstagnatiebewijs</h3><p>'
        + escape(kill_text) + '</p><p>' + escape(psi_text) + '</p>'
        '<p class="muted">Een positieve kernel-OOM-delta bewijst geen '
        'oorzaak bij een bepaalde runner of voltooide herstelactie.</p>'
    )
    coach = comparison.get("coach_comparison")
    if isinstance(coach, dict) and isinstance(coach.get("previous"), dict) and isinstance(coach.get("current"), dict):
        p, q = coach["previous"], coach["current"]
        if coach.get("same_boot_policy_comparable") is True:
            cpu_text = (
                "Gemeten CPUQuota " + _value(p.get("quota"), "%") + " → "
                + _value(q.get("quota"), "%") + "; CPUWeight "
                + _value(p.get("weight")) + " → " + _value(q.get("weight"))
                + (" (verschil waargenomen)" if coach.get("observed_settings_changed") is True
                   else " (geen verschil waargenomen)")
            )
        else:
            cpu_text = ("Niet vergelijkbaar: de coach was niet op beide momenten "
                        "actief en uitleesbaar, of dezelfde Linux-opstartsessie "
                        "kon niet worden bevestigd.")
        matched = coach.get("applied_recommendation_now")
        match_text = ("actueel advies aanwezig in gemeten cgroup"
                      if matched is True else
                      "actueel advies verschilt van gemeten cgroup"
                      if matched is False else
                      "niet vast te stellen")
        coach_html = (
            '<h3>Coach-CPU-beleid tussen bronmomenten</h3><p>'
            + cpu_text + '</p><p>Advies ten opzichte van actuele cgroup: '
            + escape(match_text) + '.</p><p class="muted">Geen bewijs dat '
            'modeltraining draait, werk is hervat of een runner is hersteld.</p>'
        )
    else:
        coach_html = ('<h3>Coach-CPU-beleid tussen bronmomenten</h3>'
                      '<p class="muted">Geen vergelijkbare privé cgroup-bron beschikbaar.</p>')
    return (
        '<section class="panel"><h2>Verandering sinds privé T0</h2>'
        '<p><strong>Van:</strong> ' + escape(comparison["previous_utc"])
        + ' · <strong>Tot:</strong> ' + escape(comparison["current_utc"])
        + '</p><p><strong>Brontoestand:</strong> ' + escape(heading)
        + ' · ' + escape(str(comparison["elapsed_seconds"])) + ' seconden</p>'
        + boot_note
        + '<ul class="transitions">' + items + '</ul>' + capacity_html + memory_html + coach_html +
        '<p class="warning">Dit toont alleen waargenomen systemd-statusverschillen. '
        'Ook failed→active bewijst geen toegewezen GitHub-job, geslaagde CI, merge '
        'of live herstel. Niet gezien ≠ opgelost; geen automatische herstart.</p></section>'
    )



def render_user_timeline(pairs):
    """A separate bounded user-manager historical panel; never merge scopes."""
    from zsentinel_runner_snapshot import RUNNER_UNIT
    if pairs is None:
        return ""
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 11:
        raise ValueError("Unbounded user-manager history")
    rows = []
    source_gaps = 0
    omitted = 0
    for index, pair in enumerate(pairs, start=1):
        if not isinstance(pair, dict):
            raise ValueError("Invalid user history source")
        observed = pair.get("user_transitions", [])
        if not isinstance(observed, list) or len(observed) > 40:
            raise ValueError("Too many user-manager changes")
        if pair.get("complete_user_service_coverage") is not True:
            source_gaps += 1
        for item in observed:
            name, kind = item.get("unit"), item.get("change")
            if (item.get("scope") != "user" or not isinstance(name, str)
                    or len(name) > 160 or not RUNNER_UNIT.fullmatch(name)
                    or kind not in {"newly_observed", "no_longer_observed",
                                    "service_state_changed", "previous_source_gap",
                                    "current_source_gap"}):
                raise ValueError("Invalid user-manager observation")
            if len(rows) >= 40:
                omitted += 1
                continue
            rows.append(
                "<li>Venster " + str(index) + " · <code>" + escape(name) +
                "</code> · " + escape(kind) + " · geen GitHub-jobherstelbewijs</li>"
            )
    return (
        '<section class="panel"><h2>Gebruikersservicehistorie (systemd --user)</h2>'
        '<p>Onafhankelijke namespace: ' + str(source_gaps) + '/' +
        str(len(pairs)) + ' bronvensters GEDEELTELIJK of ONBEKEND. '
        'Geen automatische herstart, GitHub-toewijzing of herstelconclusie.</p>'
        '<ul>' + ("".join(rows) or '<li>Geen vastgestelde overgang. '
                'Niet gelijk aan bewezen gezonde runners.</li>') +
        '</ul><p>Extra oudere waarnemingen niet getoond: ' +
        str(omitted) + '.</p></section>'
    )


def render(data):
    host = data["host"]
    units = data["units"]
    coach = data["coach"]
    history = render_comparison(data.get("comparison"))
    timeline_html = render_timeline(data.get("timeline"), data.get("timeline_observations", 0))
    footprint_html = render_timeline_footprint(data.get("timeline_footprint"))
    user_timeline_html = render_user_timeline(data.get("timeline"))
    cards = []
    if not units["runner_units_observed"]:
        cards.append('<p class="warning">Runner-services konden niet worden uitgelezen. Geen bewijs van herstel.</p>')
    elif not units["runner_units"]:
        cards.append('<p class="warning">Geen runner-services waargenomen in de begrensde bron. Niet hetzelfde als geen runners.</p>')
    else:
        for unit in sorted(units["runner_units"], key=lambda u: (
            0 if u["state"] == "failed" else 1 if u["state"] == "inactive" else 2,
            u["unit"],
        )):
            style, title, advice = classify(unit)
            reason = f" · {escape(unit['reason'])}" if unit["reason"] else ""
            cards.append(
                f'<article class="card {style}"><h3>{escape(title)}</h3>'
                f'<p class="unit">{escape(unit["unit"])}</p>'
                f'<p>systemd: {escape(unit["state"])}/{escape(unit["detail"])}{reason}</p>'
                f'<p class="advice">{escape(advice)}</p></article>'
            )
    if units.get("runner_units_truncated") or not units.get("runner_units_observed"):
        cards.insert(0, '<p class="warning">Brontoegang/overzicht is onvolledig. '
                        'Niet-waargenomen betekent niet gezond of verwijderd.</p>')
    # User-manager runner units are a second, independent read-only source.
    # Older private receipts did not capture this field: unknown, not empty.
    from zsentinel_runner_snapshot import _user_unit_states
    user_observed = units.get("user_runner_units_observed") is True
    user_entries = _user_unit_states({"units": units})
    user_cards = []
    if not user_observed:
        user_cards.append('<p class="warning">User-systemd runnerbron ONBEKEND. '
                          'Deze bron was mogelijk niet beschikbaar in de opgenomen versie.</p>')
    elif not user_entries:
        user_cards.append('<p class="warning">Geen user-systemd runners waargenomen '
                          '(geen bewijs dat er geen GitHub runners zijn).</p>')
    else:
        for name, row in sorted(user_entries.items(), key=lambda entry: (
                0 if entry[1]["state"] == "failed" else
                1 if entry[1]["state"] == "inactive" else 2,
                entry[0])):
            style, title, advice = classify(row)
            cause = (" · " + escape(row["reason"]) if row.get("reason") else "")
            user_cards.append(
                '<article class="card ' + style + '"><h3>' + escape(title) +
                ' · user-systemd</h3><p class="unit">' + escape(name) +
                '</p><p>systemd --user: ' + escape(row["state"]) +
                '/' + escape(row["detail"]) + cause +
                '</p><p class="advice">' + escape(advice) + '</p></article>'
            )
    if units.get("user_runner_units_truncated") or not user_observed:
        user_cards.insert(0, '<p class="warning">User-systemd bronbereik '
                          'onvolledig of onbekend; niet-waargenomen is geen herstelbewijs.</p>')
    user_previous = data.get("comparison")
    user_deltas = user_previous.get("user_transitions", []) if isinstance(
        user_previous, dict) else []
    user_transition_items = "".join(
        '<li><code>' + escape(row["unit"]) + '</code> · ' +
        escape(row["change"]) + ' (geen bevestigde GitHub-job)</li>'
        for row in user_deltas[:20]
    )
    user_transition_panel = (
        '<section class="panel"><h2>Gebruikersservicebron T0/T1</h2><p>' +
        ('Begrensde waargenomen verschillen, geen herstelbewijs.'
         if user_previous.get("complete_user_service_coverage") is True
         else 'Een of beide bronmomenten zijn onvolledig/onbekend.')
         + '</p><ul>' + (user_transition_items or
         '<li>Geen vergelijkbare wijzigingen binnen de bron aangetoond.</li>') +
        '</ul></section>'
        if isinstance(user_previous, dict) else ''
    )

    reasons = ", ".join(REASON_NL.get(r, "Onbekende observatie")
                        for r in data["reasons"]) or "Geen druk gemeten (uitsluitend momentopname)"
    plans = "".join(
        f"<li><strong>{escape(title)}</strong> — {escape(advice)}</li>"
        for title, advice in recovery_priorities(data)
    )
    counts = {
        "failed": sum(u["state"] == "failed" for u in units["runner_units"]),
        "inactive": sum(u["state"] == "inactive" for u in units["runner_units"]),
        "active": sum(u["state"] == "active" for u in units["runner_units"]),
    }
    overview = (f'{counts["failed"]} gefaald · {counts["inactive"]} gestopt · '
                f'{counts["active"]} actief · '
                + ("gedeeltelijke bron" if units.get("runner_units_truncated")
                   or not units["runner_units_observed"] else "begrensde lokale bron"))

    coach_state = ("Bestaande coach-instellingen niet uitleesbaar"
                   if not coach["observed"] else
                   "Coach inactief (geen herstart)"
                   if not coach["active"] else
                   f'Bestaande coach: {_value(coach["quota"], "%")} CPU, '
                   f'gewicht {_value(coach["weight"])}')
    coach_match = ("Gelijk aan huidig beleid" if coach["recommendation_matches"] is True
                   else "Niet gelijk of niet vastgesteld")

    promotion_status = coach.get("promotion_status")
    if promotion_status in {
        "promotion-waiting", "promotion-reset-context",
        "promotion-reset-policy", "promotion-reset-gap",
    }:
        wait_reason = {
            "promotion-reset-context": "nieuwe runner, andere drukcategorie of reboot",
            "promotion-reset-policy": "gewijzigde cgroup of nieuwe CPU-hersteltrap",
            "promotion-reset-gap": "te veel tijd zonder betrouwbare observatie",
        }.get(promotion_status, "nieuwe observatieperiode")
        cpu_policy = (
            "Opschalen tijdelijk uitgesteld (" + wait_reason + "): "
            + _value(coach.get("promotion_remaining_seconds"), " seconden")
            + " resterend; gescheiden vrije bronmetingen: "
            + _value(coach.get("promotion_observations"))
            + " (minimaal drie vóór verhoging)."
        )
    elif promotion_status == "sustained-headroom":
        cpu_policy = "Stabiele vrije capaciteit: opschalen aanbevolen, niet door dit rapport uitgevoerd."
    elif promotion_status in {"immediate-cpu-yield", "immediate-weight-yield"}:
        cpu_policy = "Direct ruimte voor runners: lagere cgroup-toewijzing aanbevolen."
    elif promotion_status == "unreadable-current-cgroup":
        cpu_policy = "Huidige cgroup niet leesbaar; geen veilig CPU-advies toe te passen."
    elif promotion_status in {"no-promotion-needed", "inactive-no-action"}:
        cpu_policy = "Geen extra CPU-verhoging voorgesteld."
    else:
        cpu_policy = "Opschaalbeslissing niet in deze bron aangetroffen (mogelijk oudere privébon)."
    applied_policy = (
        "Effectief veilig doel: CPU " + _value(coach.get("effective_quota"), "%")
        + ", gewicht " + _value(coach.get("effective_weight"))
        if coach.get("effective_quota") is not None else
        "Effectief veilig doel onbekend"
    )
    css = """
    :root {color-scheme:light; font-family:system-ui,-apple-system,sans-serif}
    body {margin:0;background:#f5f6f8;color:#171c25;line-height:1.55}
    header {background:#18263c;color:white;padding:2.5rem max(1rem,calc((100vw - 1120px)/2))}
    main {max-width:1120px;margin:0 auto;padding:1.3rem}
    h1 {font-size:2rem;margin:0 0 .3rem} h2 {margin:1.5rem 0 .8rem}
    h3 {font-size:1rem;margin:.1rem 0} p {margin:.35rem 0 .7rem}
    .muted {opacity:.8}.grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem}
    .card,.panel {background:white;border:1px solid #d8dfe7;border-radius:12px;padding:1rem}
    .card.urgent {border-left:6px solid #bb3939}.card.hold {border-left:6px solid #ba831b}
    .card.ok {border-left:6px solid #337e65}.card.unknown {border-left:6px solid #738399}
    .warning {padding:1rem;background:#fff5e1;border:1px solid #deb76f;border-radius:8px}
    .unit {font-family:ui-monospace,monospace;overflow-wrap:anywhere;font-size:.86rem}
    .advice {font-weight:600}.metric {font-size:1.15rem}
    .transitions {padding-left:1.3rem}.transition {margin:.6rem 0;overflow-wrap:anywhere}
    .timeline-entry {border:1px solid #d8dfe7;border-radius:8px;padding:.7rem;margin:.65rem 0}
    .timeline-entry summary {cursor:pointer;font-weight:600;overflow-wrap:anywhere}
    footer {margin-top:2rem;border-top:1px solid #cdd5df;padding:1.5rem 0}
    """
    def box(name, value):
        return f'<div class="panel"><strong>{escape(name)}</strong><p class="metric">{value}</p></div>'
    metrics = "".join([
        box("Load / CPU", f'{_value(host["host_load"])} / {_value(host["cores"])} cores'),
        box("MemAvailable", _value(host["memory_available_gib"], " GiB")),
        box("Swap gebruikt", _value(host["swap_used_percent"], "%")),
        box("Geheugen-PSI some avg10 (host)", _value(
            host.get("memory_psi_some_avg10_percent")
            if host.get("memory_psi_observed") is True else None, "%")),
        box("Geheugen-PSI full avg10 (host)", _value(
            host.get("memory_psi_full_avg10_percent")
            if host.get("memory_psi_observed") is True else None, "%")),
        box("Kernel-OOM sinds huidige boot (alle processen)", _value(
            host.get("kernel_oom_kills_since_boot")
            if host.get("kernel_oom_counter_observed") is True else None)),
        box("Root niet beschikbaar", _value(host["disk_unavailable_percent"], "%")),
        box("Root beschikbaar", _value(host.get("root_available_gib"), " GiB")),
        box("Root-inodes beschikbaar", _value(host.get("root_inode_available_percent"), "%")),
    ])
    return f"""<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>zSentinel · VPS herstelwerkbank</title><style>{css}</style></head><body>
<header><h1>zSentinel · VPS herstelwerkbank</h1>
<p>Privé momentopname · {escape(data["time"])} · alleen observatie</p>
<p class="muted">Geen services, runners, modeltraining of wachtrij gewijzigd. {("Historische privébron, niet opnieuw geobserveerd." if data.get("offline_replay") else "Niet live gesynchroniseerd.")}</p></header>
<main><h2>Hostcapaciteit</h2><div class="grid">{metrics}</div>
<p class="muted">Geheugen-PSI toont hostbrede wachttijd gedurende een venster
van 10 seconden. De OOM-teller bevat alle processen sinds de huidige boot,
niet alleen GitHub-runners. Onbekende bronnen worden nooit als nul gezien.</p>
<section class="panel"><h2>Vrijblijvend CPU-advies voor Fuckball-training</h2>
<p><strong>Advies:</strong> CPUQuota {_value(data["quota"], "%")}, CPUWeight {_value(data["weight"])}</p>
<p><strong>Oorzaken:</strong> {escape(reasons)}</p>
<p>{escape(coach_state)} · {escape(coach_match)}</p>
<p><strong>Afgewogen CPU-regeling:</strong> {escape(applied_policy)}</p>
<p class="muted">{escape(cpu_policy)}</p>
<p>De voorbeeldinstellingen zijn niet door dit rapport toegepast.</p></section>
{history}
{footprint_html}
{timeline_html}
{user_timeline_html}
<h2>GitHub-runner-servicestatus</h2><p class="muted">{escape(overview)}</p>
<div class="grid">{"".join(cards)}</div>
<h2>Gebruikersservices (aparte systemd --user manager)</h2>
<p class="muted">Deze bron wordt onafhankelijk van systeemservices gelezen;
gelijke servicenamen uit verschillende managers worden niet samengevoegd.</p>
<div class="grid">{"".join(user_cards)}</div>
{user_transition_panel}
<section class="panel"><h2>Prioriteit en veilige herstelvolgorde</h2><ol>{plans}</ol>
<p>Dit zijn leesadviezen, geen bewijs van toestemming tot herstart, dispatch of merge.</p></section>
<section class="panel"><h2>Bevoegde vervolgactie</h2>
<p>Geef voorrang aan gefaalde OOM-services wanneer RAM/swap voldoende ruimte hebben en
de service-eigenaar herstel toestaat. Controleer voor gestopte services eerst of dat bewust is.
Een actieve service bewijst geen GitHub job, succesvolle CI, main-merge of productieherstel.</p>
<p>Onbekende bronnen en gedeeltelijke observaties blijven onbekend. Geen automatische
retry, servicewijziging, GitHub-actie of portfolio-claim is uitgevoerd.</p></section>
<footer>Bron: {("vooraf opgeslagen privéwaarneming; niet opnieuw live gelezen" if data.get("offline_replay") else "lokale Linux host- en systemd-observatie")}. Geen netwerkverzoek of credentialopslag.
Alleen bedoeld als privé operatoroverzicht, niet als productie- of releasebewijs.</footer></main></body></html>"""


def offline_receipt_data(current):
    """Admit one owner-private historical receipt with NO live host/provider read."""
    from zsentinel_runner_snapshot import _parsed, _unit_states, SCHEMA
    if type(current) is not dict or current.get("schema") != SCHEMA:
        raise ValueError("Unknown stored source schema")
    # The single-observation path must not silently skip validation normally
    # performed by changes(T0,T1) when a second observation is available.
    _parsed(current.get("observation_utc"))
    if not isinstance(current.get("host"), str) or not current["host"]:
        raise ValueError("Stored source has no canonical host")
    _unit_states(current)
    rec = current.get("cpu_recommendation")
    capacity = current.get("capacity")
    if not isinstance(rec, dict) or not isinstance(capacity, dict):
        raise ValueError("Incomplete stored capacity receipt")
    coach = current.get("coach_state")
    if not isinstance(coach, dict):
        # Older receipts have no verified coach details.
        coach = {"observed": False, "active": None, "quota": None,
                 "weight": None, "recommendation_matches": None}
    quota, weight = rec.get("quota_percent"), rec.get("weight")
    if type(quota) is not int or quota not in {60, 100, 175} or (
            type(weight) is not int or weight not in {25, 40, 70}):
        raise ValueError("Untrusted stored CPU recommendation")
    reasons = rec.get("reasons")
    if not isinstance(reasons, list) or len(reasons) > 20 or not all(
            isinstance(r, str) and r in REASON_NL for r in reasons):
        raise ValueError("Untrusted stored pressure reasons")
    for key in ("host_load", "cores", "memory_available_gib",
                "swap_used_percent", "disk_unavailable_percent", "source_complete"):
        if key not in capacity:
            raise ValueError("Missing saved host measurement")
    return {
        "time": current["observation_utc"].replace("T", " ").replace("Z", " UTC"),
        "host": capacity, "units": current["units"], "coach": coach,
        "quota": quota, "weight": weight, "reasons": reasons,
        "offline_replay": True,
        "comparison": None,
    }


def offline_comparison_data(previous, current):
    """Render a saved T0/T1 comparison, reusing the strict single source."""
    from zsentinel_runner_snapshot import changes
    comparison = changes(previous, current)
    result = offline_receipt_data(current)
    result["comparison"] = comparison
    return result


def offline_receipt_report(path):
    """Owner-private historical replay: zero systemd, process or GitHub reads."""
    from zsentinel_runner_snapshot import load_private
    return offline_receipt_data(load_private(path))


def offline_comparison_report(t0_path, t1_path):
    """Open each private T0/T1 receipt exactly once; never probe live state."""
    from zsentinel_runner_snapshot import load_private
    return offline_comparison_data(load_private(t0_path), load_private(t1_path))


def timeline_footprint(receipts):
    """Bounded counts of observed failed services and capacity pressure windows.

    Repeated sampled failures are not separate crashes or proof of a retry.
    """
    from zsentinel_runner_snapshot import _unit_states, _user_unit_states
    failed = {}
    observed = {}
    failure_results = {}
    user_failed = {}
    user_observed = {}
    user_failure_results = {}
    incomplete_user = 0
    latest_user_rows = {}
    latest_user_coverage = False
    safe_results = {"oom-kill", "exit-code", "signal", "timeout",
                    "watchdog", "core-dump", "resources"}
    partial = 0
    high_swap = 0
    high_disk = 0
    high_load = 0
    unknown_capacity = 0
    latest_pressure_high = None
    latest_service_rows = {}
    latest_service_coverage = False
    for index, receipt in enumerate(receipts):
        units = receipt.get("units") or {}
        rows = _unit_states(receipt)
        source_complete = (units.get("runner_units_observed") is True
                           and units.get("runner_units_truncated") is False)
        if not source_complete:
            partial += 1
        if index == len(receipts) - 1:
            latest_service_rows = rows
            latest_service_coverage = source_complete
        for name, state in rows.items():
            observed[name] = observed.get(name, 0) + 1
            if state["state"] == "failed":
                failed[name] = failed.get(name, 0) + 1
                result = state.get("reason")
                if result not in safe_results:
                    result = "unknown"
                causes = failure_results.setdefault(name, {})
                causes[result] = causes.get(result, 0) + 1
        user_rows = _user_unit_states(receipt)
        user_complete = (units.get("user_runner_units_observed") is True
                         and units.get("user_runner_units_truncated") is False)
        if not user_complete:
            incomplete_user += 1
        if index == len(receipts) - 1:
            latest_user_rows = user_rows
            latest_user_coverage = user_complete
        for name, row in user_rows.items():
            user_observed[name] = user_observed.get(name, 0) + 1
            if row["state"] == "failed":
                user_failed[name] = user_failed.get(name, 0) + 1
                reason = row.get("reason")
                if reason not in safe_results:
                    reason = "unknown"
                results = user_failure_results.setdefault(name, {})
                results[reason] = results.get(reason, 0) + 1
        cap = receipt.get("capacity")
        if not isinstance(cap, dict) or cap.get("source_complete") is not True:
            unknown_capacity += 1
            continue
        swap, disk = cap.get("swap_used_percent"), cap.get("disk_unavailable_percent")
        load, cores = cap.get("host_load"), cap.get("cores")
        if not (type(swap) in (int, float) and 0 <= swap <= 100
                and type(disk) in (int, float) and 0 <= disk <= 100
                and type(load) in (int, float) and 0 <= load < 1e6
                and type(cores) is int and 1 <= cores <= 4096):
            unknown_capacity += 1
            continue
        high_swap += swap >= 80
        high_disk += disk >= 90
        high_load += load > 1.35 * cores
        if index == len(receipts) - 1:
            latest_pressure_high = (swap >= 80 or disk >= 90 or
                                    load > 1.35 * cores)
    latest_failed = sorted(name for name, row in latest_service_rows.items()
                           if row["state"] == "failed")
    latest_user_failed = sorted(
        name for name, row in latest_user_rows.items() if row["state"] == "failed")
    user_failures = [
        {"scope": "user", "unit": name,
         "failed_samples": count,
         "seen_samples": user_observed[name],
         "last_observed_state": (
             latest_user_rows[name]["state"] if name in latest_user_rows
             else "unknown_partial" if not latest_user_coverage
             else "not_in_latest_sample"),
         "last_systemd_result": (
             latest_user_rows[name].get("reason")
             if name in latest_user_rows
             and latest_user_rows[name]["state"] == "failed"
             and latest_user_rows[name].get("reason") in safe_results
             else "unknown"),
         "failure_results": [
             {"result": reason, "sample_count": amount}
             for reason, amount in sorted(user_failure_results.get(name, {}).items(),
                                          key=lambda item: (-item[1], item[0]))
         ]}
        for name, count in sorted(user_failed.items(),
                                  key=lambda pair: (-pair[1], pair[0]))[:20]
    ]
    return {
        "samples": len(receipts), "incomplete_service_samples": partial,
        "incomplete_user_service_samples": incomplete_user,
        "unknown_capacity_samples": unknown_capacity,
        "swap_high_samples": high_swap,
        "root_high_samples": high_disk,
        "load_high_samples": high_load,
        "latest_failed_services": latest_failed[:20],
        "latest_user_failed_services": latest_user_failed[:20],
        "latest_pressure_high": latest_pressure_high,
        "latest_service_coverage_complete": latest_service_coverage,
        "latest_user_service_coverage_complete": latest_user_coverage,
        "user_failed_observations": user_failures,
        "failed_observations": [
            {"unit": name, "failed_samples": count,
             "seen_samples": observed[name],
             "last_observed_state": (
                 latest_service_rows[name]["state"]
                 if name in latest_service_rows else
                 "unknown_partial" if not latest_service_coverage else
                 "not_in_latest_sample"),
             "last_systemd_result": (
                 latest_service_rows[name].get("reason")
                 if name in latest_service_rows
                 and latest_service_rows[name]["state"] == "failed"
                 and latest_service_rows[name].get("reason") in safe_results
                 else "unknown"),
             "failure_results": [
                 {"result": result, "sample_count": number}
                 for result, number in sorted(failure_results.get(name, {}).items(),
                                              key=lambda item: (-item[1], item[0]))
             ]}
            for name, count in sorted(failed.items(),
                                      key=lambda pair: (-pair[1], pair[0]))[:20]
        ],
        "restart_or_job_count_inferable": False,
    }


def timeline_owner_actions(summary, comparisons):
    """At most three evidence-bound human follow-ups; no execution or dispatch."""
    if not isinstance(summary, dict) or comparisons is None:
        return []
    failed_history = (
        [{"scope": "system", **row}
         for row in (summary.get("failed_observations") or [])]
        + (summary.get("user_failed_observations") or [])
    )
    failed_latest = (
        [("system", name) for name in (summary.get("latest_failed_services") or [])]
        + [("user", name) for name in (summary.get("latest_user_failed_services") or [])]
    )
    stressed_latest = summary.get("latest_pressure_high") is True
    reboot = any(pair.get("host_reboot_observed") is True for pair in comparisons)
    unknown = (summary.get("incomplete_user_service_samples", 0) > 0
               or summary.get("incomplete_service_samples", 0) > 0
               or summary.get("unknown_capacity_samples", 0) > 0
               or summary.get("latest_pressure_high") is None
               or any(pair.get("same_boot_verified") is not True
                      for pair in comparisons))
    # OOM increments apply to every process on this Linux host. They can flag
    # current capacity risk, but cannot prove a particular runner crashed.
    oom_rising = any(
        isinstance(pair, dict)
        and isinstance(pair.get("memory_oom_comparison"), dict)
        and pair["memory_oom_comparison"].get(
            "oom_counter_same_boot_comparable") is True
        and type(pair["memory_oom_comparison"].get(
            "kernel_oom_kills_delta")) is int
        and pair["memory_oom_comparison"]["kernel_oom_kills_delta"] > 0
        for pair in comparisons[:11]
    )
    tasks = []
    if oom_rising and stressed_latest and not failed_latest:
        tasks.append((
            "P0 · kernelbrede OOM-toename bij actuele VPS-druk",
            "De Linux-kernel telt extra OOM-kills binnen een bevestigd "
            "zelfde-bootvenster en de laatste bron toont hostdruk. Laat "
            "de bevoegde eigenaar geheugen/swap en betrokken processen "
            "eerst beoordelen; dit bewijst GEEN GitHub-runnercrash en "
            "staat geen herstart toe.",
            "HOST OOM-DRUK · EIGENAAR NODIG",
        ))
    if failed_latest and stressed_latest:
        oom_recent = any(
            (entry.get("scope"), entry.get("unit")) in failed_latest
            and entry.get("last_systemd_result") == "oom-kill"
            for entry in failed_history
        )
        actions_hint = (
            "De systemd-bron meldt oom-kill: eerst geheugen/swap en "
            "cgroup-limieten door de eigenaar laten inspecteren. "
            if oom_recent else
            "Laat de runtime-eigenaar oorzaak en RAM/swap/disk-headroom bevestigen. "
        )
        tasks.append((
            "P0 · actuele failed-services bij VPS-druk",
            "De laatste bron toont failed-services EN hoge huidige hostdruk. "
            + actions_hint + "Geen herstart of nieuwe jobs.",
            "LAATSTE BRON FAILED · EIGENAAR NODIG",
        ))
    elif failed_latest:
        tasks.append((
            "P1 · failed-service in laatste bron",
            "Een of meer systeem- of gebruikersrunner-services zijn bij de laatste "
            "observatie failed. Laat de eigenaar per systemd-manager de oorzaak "
            "en GitHub-toewijzing onafhankelijk vaststellen.",
            "LAATSTE BRON FAILED · GEEN JOBBEWIJS",
        ))
    elif failed_history:
        tasks.append((
            "P1 · historische failed-services opvolgen",
            "Eerdere systeem-/gebruikersbronnen meldden failed, maar de laatste bron toont die "
            "status niet. Controleer werkelijke GitHub-toewijzing en eigenaarschap; "
            "een andere systemd-status is geen herstelbewijs.",
            "HISTORISCH FAILED · HUIDIG JOBHERSTEL ONBEKEND",
        ))
    if reboot:
        tasks.append((
            "P1 · bootwissel in herstelperiode",
            "Verifieer oorspronkelijke GitHub-run en workerlease opnieuw "
            "na herstart; systemd active is onvoldoende.",
            "HERVATTING ONBEKEND",
        ))
    if unknown:
        tasks.append((
            "P1 · ontbrekend betrouwbaar bronbereik",
            "Vraag volledige actuele owner-private systeem- en user-bronmomenten en "
            "claim-/jobbewijs; lege records geven geen vrij eigenaarschap.",
            "BRON ONVOLLEDIG",
        ))
    if not tasks:
        tasks.append((
            "P1 · service-waarneming zonder GitHub-bevestiging",
            "Controleer via bestaande GitHub-bronnen de exacte queued/running "
            "job en actuele claim; geen queue/runneractie geautoriseerd.",
            "ASSIGNMENT ONBEKEND",
        ))
    return tasks[:3]


def render_timeline_footprint(summary):
    if not isinstance(summary, dict):
        return ""
    samples = summary["samples"]
    rows = []
    for entry in summary["failed_observations"][:20]:
        rows.append("<li><strong>" + escape(entry["unit"]) + "</strong>: "
                    + str(entry["failed_samples"]) + " van "
                    + str(entry["seen_samples"]) + " waarnemingen met "
                    "systemd-status failed; laatst waargenomen: "
                    + escape(entry["last_observed_state"]) +
                    "; geobserveerde systemd-resultaten: "
                    + escape(", ".join(
                        item["result"] + " " + str(item["sample_count"]) + "×"
                        for item in entry["failure_results"]
                    )) + " (geen aantal afzonderlijke crashes).</li>")
    for entry in summary.get("user_failed_observations", [])[:20]:
        rows.append("<li><strong>user-systemd / " + escape(entry["unit"]) +
                    "</strong>: " + str(entry["failed_samples"]) + " van " +
                    str(entry["seen_samples"]) + " waarnemingen met failed; " +
                    "laatst gezien: " + escape(entry["last_observed_state"]) +
                    "; systemd-oorzaak: " + escape(", ".join(
                        item["result"] + " " + str(item["sample_count"]) + "×"
                        for item in entry["failure_results"]
                    )) + ". Dit zijn geen afzonderlijke crashes.</li>")
    failed_html = "".join(rows) or (
        "<li>Geen failed-status in waargenomen runnerregels; dit is geen "
        "bewijs dat alle runners gezond zijn.</li>")
    actions = timeline_owner_actions(summary, summary.get("comparisons", []))
    action_rows = "".join(
        "<li><strong>" + escape(title) + "</strong><br>"
        + escape(advice) + "<br><small>Status: " + escape(status)
        + " · geen geautoriseerde systeemactie</small></li>"
        for title, advice, status in actions
    )
    return (
        "<section class='panel'><h2>Aanhoudende blokkades in het bronvenster</h2>"
        "<p><strong>" + str(samples) + " privémetingen</strong> · "
        + str(summary["incomplete_service_samples"]) + " gedeeltelijke "
        "systeemlijsten · " + str(summary.get("incomplete_user_service_samples", samples))
        + " gedeeltelijke/onbekende user-lijsten · "
        + str(summary["unknown_capacity_samples"])
        + " onvolledige capaciteitsmetingen</p>"
        "<p><strong>Druk waargenomen:</strong> hoge swap "
        + str(summary["swap_high_samples"]) + "× · weinig rootruimte "
        + str(summary["root_high_samples"]) + "× · hoge CPU-load "
        + str(summary["load_high_samples"]) + "×. "
        "Dit zijn meetmomenten, geen minuten onder druk.</p>"
        "<p><strong>Laatste bron:</strong> " +
        str(len(summary.get("latest_failed_services", []))) +
        " system en " + str(len(summary.get("latest_user_failed_services", []))) +
        " user failed-service(s) waargenomen; hostdruk " +
        ("hoog" if summary.get("latest_pressure_high") is True else
         "niet hoog in waargenomen bronnen" if summary.get("latest_pressure_high") is False
         else "onbekend") + ". Historisch failed is niet automatisch actueel P0.</p>"
        "<h3>Als failed waargenomen services</h3><ul>" + failed_html + "</ul>"
        "<h3>Vervolg voor bevoegde operator</h3><ol>" + action_rows + "</ol>"
        "<p class='warning'>Een herhaald failed-meetmoment bewijst geen nieuwe "
        "crash; ontbrekende services of bronnen bewijzen geen herstel. "
        "Geen automatische herstart, claim of workflowactie.</p></section>"
    )

def offline_timeline_report(paths):
    """Replay 3–12 owner-private snapshots; never sample current host or services."""
    from zsentinel_runner_snapshot import changes, load_private, _parsed, MAX_AGE_HOURS
    if not 3 <= len(paths) <= 12:
        raise ValueError("Use three to twelve private observations")
    receipts = [load_private(path) for path in paths]
    first = _parsed(receipts[0].get("observation_utc"))
    last = _parsed(receipts[-1].get("observation_utc"))
    if not 0 < (last - first).total_seconds() <= MAX_AGE_HOURS * 3600:
        raise ValueError("Timeline must be ordered within 72 hours")
    pairs = [changes(left, right) for left, right in zip(receipts, receipts[1:])]
    # Reuse the existing v1 owner-private offline render contract for the
    # newest observation. This does not read systemd or a GitHub API.
    data = offline_comparison_data(receipts[-2], receipts[-1])
    data["timeline"] = pairs
    data["timeline_observations"] = len(receipts)
    data["timeline_footprint"] = timeline_footprint(receipts)
    data["timeline_footprint"]["comparisons"] = pairs
    return data


def render_timeline(pairs, observations):
    """Show incident continuity and host pressure without claiming job recovery."""
    if pairs is None:
        return ""
    headings = []
    reboot_count = 0
    uncertain_count = 0
    for index, pair in enumerate(pairs, start=1):
        reboot = pair.get("host_reboot_observed")
        same_boot = pair.get("same_boot_verified") is True
        complete = pair.get("complete_service_coverage") is True
        if reboot is True:
            reboot_count += 1
            continuity = "VPS-herstart geobserveerd"
        elif not same_boot:
            uncertain_count += 1
            continuity = "Bootcontinuïteit onbekend"
        elif not complete:
            uncertain_count += 1
            continuity = "Gedeeltelijke runnerbron"
        else:
            continuity = "Zelfde boot; servicelijsten volledig"
        transitions = pair.get("transitions", [])
        # _unit_states bounds each snapshot to 20 runner services: maximum
        # 40 transitions in the union. Explicitly flag any display truncation.
        status_rows = []
        for transition in transitions[:40]:
            prior = transition.get("previous") or {}
            current = transition.get("current") or {}
            desc = {
                "service_state_changed": "systemd-status gewijzigd",
                "newly_observed": "nieuw waargenomen",
                "no_longer_observed": "niet meer waargenomen",
                "previous_source_gap": "vorige bron onvolledig",
                "current_source_gap": "huidige bron onvolledig",
            }.get(transition.get("change"), "onzekere observatie")
            status_rows.append(
                "<li><strong>" + escape(str(transition.get("unit", ""))) + "</strong>: "
                + escape(desc) + " (" + escape(str(prior.get("state", "onbekend")))
                + " → " + escape(str(current.get("state", "onbekend"))) + ")</li>"
            )
        items = "".join(status_rows) or "<li>Geen geobserveerde servicestatuswijziging.</li>"
        capacity = pair.get("capacity_comparison") or {}
        metric_rows = []
        labels = {
            "host_load": ("Load", ""),
            "memory_available_gib": ("RAM beschikbaar", " GiB"),
            "swap_used_percent": ("Swap", " procentpunt"),
            "disk_unavailable_percent": ("Root niet beschikbaar", " procentpunt"),
            "root_available_gib": ("Root beschikbaar", " GiB"),
            "root_inode_available_percent": ("Root-inodes beschikbaar", " procentpunt"),
        }
        for item in capacity.get("metrics", [])[:6]:
            name, unit = labels.get(item.get("metric"), ("Andere metric", ""))
            amount = item.get("difference")
            if item.get("comparable") is True and type(amount) in (float, int):
                metric_rows.append(escape(name) + ": " + escape(f"{amount:+g}{unit}"))
            else:
                metric_rows.append(escape(name) + ": niet vergelijkbaar")
        coach = pair.get("coach_comparison") or {}
        if coach.get("same_boot_policy_comparable") is True:
            before, after = coach.get("previous") or {}, coach.get("current") or {}
            cpu = ("Coach CPUQuota " + _value(before.get("quota"), "%") + " → "
                   + _value(after.get("quota"), "%") + ", CPUWeight "
                   + _value(before.get("weight")) + " → "
                   + _value(after.get("weight")))
        else:
            cpu = "Coach-CPU niet vergelijkbaar (bron, status of boot onzeker)"
        headings.append(
            "<details class='timeline-entry'><summary>Stap " + str(index)
            + " · " + escape(str(pair.get("previous_utc", ""))) + " → "
            + escape(str(pair.get("current_utc", ""))) + " · "
            + escape(continuity) + " · "
            + str(len(transitions)) + " waargenomen serviceverschillen</summary>"
            + "<p class='muted'>Capaciteit: " + "; ".join(metric_rows) + "</p>"
            + "<p>" + cpu + "</p><ul>" + items + "</ul>"
            + "<p class='muted'>Waarneming, geen bewijs van herstel, "
            "toewijzing, geslaagde CI of doorlopende modeltraining.</p></details>"
        )
    return (
        "<section class='panel'><h2>Historisch runnerverloop (alleen privébonnen)</h2>"
        "<p><strong>" + str(observations) + " meetmomenten</strong> · "
        + str(len(pairs)) + " opeenvolgende vergelijkingen · "
        + str(reboot_count) + " waargenomen bootwisselingen · "
        + str(uncertain_count) + " onzekere vergelijkingen</p>"
        "<p class='warning'>Bronmomenten blijven afzonderlijke systemd-/hostmetingen. "
        "Ook een actieve runner-service of verbeterde capaciteit bewijst niet "
        "dat een GitHub-job opnieuw is toegewezen.</p>"
        + "".join(headings) + "</section>"
    )



def _handoff_text(value, maximum=192):
    """Restrict the printable operator handoff to bounded, inert plain text."""
    if value is None:
        return "onbekend"
    raw = str(value)[:maximum]
    return "".join(ch if (ch.isalnum() or ch in " .,:@+%/()_-→") else "?"
                   for ch in raw)


def _handoff_number(value, suffix=""):
    """No untrusted JSON value can masquerade as a measured number."""
    import math
    if type(value) in (int, float) and math.isfinite(value) and 0 <= value < 1e10:
        return f"{value:g}{suffix}"
    return "onbekend"


def render_private_handoff(data):
    """One self-contained markdown handoff from the admitted offline timeline.

    Source-bound operator instructions only, never claim, merge, dispatch or
    resume authority. Do not include raw process args or stored host tokens.
    """
    summary = data.get("timeline_footprint")
    comparisons = data.get("timeline")
    if not isinstance(summary, dict) or not isinstance(comparisons, list):
        raise ValueError("Markdown handoff requires complete offline timeline")
    if not 2 <= len(comparisons) <= 11 or summary.get("samples") != len(comparisons) + 1:
        raise ValueError("Incompatible timeline evidence")
    beginning = _handoff_text(comparisons[0].get("previous_utc"))
    ending = _handoff_text(comparisons[-1].get("current_utc"))
    actions = timeline_owner_actions(summary, comparisons)
    rows = [
        "# zSentinel | privé runner-hersteloverdracht",
        "",
        f"Bronvenster: {beginning} → {ending} (UTC; uitsluitend lokaal opgeslagen bronmomenten).",
        f"Observaties: {summary['samples']} | Vergelijkingen: {len(comparisons)}.",
        "Status: GEEN GECONFIRMEERD HERSTEL. Alleen systemd-/hostobservaties; "
        "GitHub-jobtoewijzing, CI, claims en productie moeten afzonderlijk bevestigd worden.",
        "",
        "## Veilige vervolgacties voor bevoegde eigenaar",
    ]
    for title, advice, status in actions[:3]:
        rows.extend([
            f"### {_handoff_text(title)}",
            f"Probleem/actie: {_handoff_text(advice, 500)}",
            f"Status: {_handoff_text(status)}; geen service-, worker-, queue- of GitHub-actie uitgevoerd.",
            "",
        ])
    rows.extend([
        "## Bronbereik en capaciteitsdruk",
        f"- Onvolledige runnerinventaris: {summary['incomplete_service_samples']} meetmomenten.",
        f"- Ontbrekende capaciteitsbron: {summary['unknown_capacity_samples']} meetmomenten.",
        f"- Swap >=80%: {summary['swap_high_samples']} meetmomenten.",
        f"- Rootruimte >=90% niet beschikbaar: {summary['root_high_samples']} meetmomenten.",
        f"- Load >1,35× beschikbare CPU-cores: {summary['load_high_samples']} meetmomenten.",
        f"- Laatste meetmoment: {len(summary['latest_failed_services'])} als failed "
        "waargenomen services; huidige hostdruk: "
        + ("hoog" if summary["latest_pressure_high"] is True else
           "niet hoog in beschikbare laatste bron" if summary["latest_pressure_high"] is False
           else "onbekend") + ".",
        "Historische failed-waarnemingen zijn niet automatisch actuele P0-storingen.",
        "Dit zijn aantallen momentopnames, geen tijdsduur, crashfrequentie of vrijgegeven resourcebudget.",
        "",
        "## Herhaald geobserveerde gefaalde runner-services",
    ])
    failed = summary.get("failed_observations", [])
    if failed:
        for entry in failed[:20]:
            rows.append("- " + _handoff_text(entry["unit"]) + ": "
                        + str(entry["failed_samples"]) + " × failed in "
                        + str(entry["seen_samples"]) + " bronobservaties; "
                        + "laatst gezien: " + _handoff_text(entry["last_observed_state"])
                        + "; systemd-resultaten: "
                        + _handoff_text(", ".join(
                            item["result"] + " " + str(item["sample_count"]) + "x"
                            for item in entry["failure_results"]), 250)
                        + "; niet gelijk aan evenveel crashes.")
    else:
        rows.append("- Geen failed-bronregel aangetroffen; geen bewijs dat alle runners gezond zijn.")
    rows.extend(["", "## Historische overgangen"])
    for index, pair in enumerate(comparisons, start=1):
        reboot = pair.get("host_reboot_observed") is True
        same_boot = pair.get("same_boot_verified") is True
        coverage = pair.get("complete_service_coverage") is True
        continuity = ("VPS boot gewijzigd" if reboot else
                      "Zelfde Linux-boot" if same_boot else "Bootidentiteit ONBEKEND")
        rows.append(f"### Stap {index}: {_handoff_text(pair.get('previous_utc'))} → "
                    f"{_handoff_text(pair.get('current_utc'))}")
        rows.append("Bron: " + continuity + " | runnerinventaris: "
                    + ("volledig binnen limieten" if coverage else "GEDEELTELIJK/ONBEKEND") + ".")
        metrics = (pair.get("capacity_comparison") or {}).get("metrics", [])
        for m in metrics[:6]:
            name = m.get("metric")
            if name not in {"host_load", "memory_available_gib",
                            "swap_used_percent", "disk_unavailable_percent",
                            "root_available_gib", "root_inode_available_percent"}:
                continue
            diff = m.get("difference")
            if m.get("comparable") is True and type(diff) in (int, float):
                import math
                detail = f"{diff:+g}" if math.isfinite(diff) else "onbekend"
            else:
                detail = "niet vergelijkbaar"
            rows.append("- Capaciteit " + name + ": " + detail + ".")
        oom = pair.get("memory_oom_comparison")
        if isinstance(oom, dict):
            amount = oom.get("kernel_oom_kills_delta")
            rows.append("- Hostbrede kernel-OOM-toename (geen runner-attributie): " +
                        (str(amount) if oom.get(
                            "oom_counter_same_boot_comparable") is True
                         and type(amount) is int and 0 <= amount < 2 ** 63
                         else "ONBEKEND (boot/bron)") + ".")
            rows.append("- PSI full avg10: " + _handoff_number(
                oom.get("previous_psi_full_avg10_percent"), "%") + " → " +
                _handoff_number(oom.get("current_psi_full_avg10_percent"), "%")
                + "; geen jobbevestiging.")
        else:
            rows.append("- Kernelteller/PSI in deze historische bon ONBEKEND.")
        coach = pair.get("coach_comparison") or {}
        if coach.get("same_boot_policy_comparable") is True:
            before, after = coach.get("previous") or {}, coach.get("current") or {}
            rows.append("- Trainer CPUQuota: "
                        + _handoff_number(before.get("quota"), "%") + " → "
                        + _handoff_number(after.get("quota"), "%")
                        + "; CPUWeight: " + _handoff_number(before.get("weight"))
                        + " → " + _handoff_number(after.get("weight")) + ".")
        else:
            rows.append("- Trainer CPU-instellingen: niet betrouwbaar vergelijkbaar.")
        transitions = pair.get("transitions") or []
        if transitions:
            for event in transitions[:40]:
                label = {
                    "newly_observed": "nieuw waargenomen",
                    "no_longer_observed": "niet meer waargenomen",
                    "service_state_changed": "servicestatus gewijzigd",
                    "previous_source_gap": "vorige bron gedeeltelijk",
                    "current_source_gap": "huidige bron gedeeltelijk",
                }.get(event.get("change"), "onzekere overgang")
                rows.append("- " + _handoff_text(event.get("unit")) + ": " + label
                            + "; GitHub-job/herstel ONBEKEND.")
        else:
            rows.append("- Geen servicestatusverschil waargenomen binnen de bronlimieten.")
        user_transitions = pair.get("user_transitions", [])
        if not isinstance(user_transitions, list) or len(user_transitions) > 40:
            raise ValueError("Unbounded user-manager history")
        rows.append("- Gebruikersmanager bron: " +
                    ("volledig binnen limiet" if pair.get(
                        "complete_user_service_coverage") is True else
                     "GEDEELTELIJK/ONBEKEND") + ".")
        for event in user_transitions[:40]:
            if event.get("scope") != "user":
                raise ValueError("Incorrect user-manager source label")
            rows.append("- user-systemd / " +
                        _handoff_text(event.get("unit")) + ": " +
                        _handoff_text(event.get("change")) +
                        "; geen GitHub-job- of herstelbewijs.")
        rows.append("")
    rows.extend([
        "## Vertrouwen en autorisatie",
        "- Alle data komen uit door de operator eerder gemaakte eigenaar-private JSON-bonnen.",
        "- Geen actuele GitHub-API-, systemd- of hostopvraag uitgevoerd tijdens offline replay.",
        "- De bron kan gedeeltelijk zijn; geen impliciete claim-, herstart- of releasebevoegdheid.",
        "- Een actieve runner-service bewijst geen toegewezen job, success CI, merge of productieherstel.",
        "- Vereiste onafhankelijke review, exacte-head checks, eigenaarclaims en releasegates blijven gelden.",
        "",
    ])
    content = "\n".join(rows)
    if len(content.encode("utf-8")) > 128 * 1024:
        raise ValueError("Private handoff exceeds the artifact limit")
    return content



def render_private_json_handoff(data):
    """Bounded machine-readable owner inbox, never a recovery instruction.

    Use only already collected live evidence or admitted owner-private receipts.
    No provider requests, process arguments, paths, claims or service actions.
    """
    import json
    import math

    if not isinstance(data, dict) or not isinstance(data.get("host"), dict):
        raise ValueError("Missing bounded runner source")
    host, units = data["host"], data.get("units")
    if not isinstance(units, dict):
        raise ValueError("Missing runner service source")

    timeline = data.get("timeline")
    if timeline is not None:
        summary = data.get("timeline_footprint")
        if (not isinstance(summary, dict) or not isinstance(timeline, list)
                or not 2 <= len(timeline) <= 11
                or summary.get("samples") != len(timeline) + 1):
            raise ValueError("Unverified historical timeline")
        work = timeline_owner_actions(summary, timeline)
        window = {
            "first_utc": _handoff_text(timeline[0].get("previous_utc")),
            "last_utc": _handoff_text(timeline[-1].get("current_utc")),
            "observations": summary["samples"],
            "comparison_count": len(timeline),
            "incomplete_service_samples": summary["incomplete_service_samples"],
            "unknown_capacity_samples": summary["unknown_capacity_samples"],
            "failed_services_at_last_observation": len(summary["latest_failed_services"]),
        }
        source_kind = "private-offline-timeline"
    else:
        # The live/offline point-in-time decision intentionally returns
        # (title, advice) pairs; only timeline_owner_actions returns triples.
        # Adapt at the presentation boundary. Do not invent a completed
        # intervention or infer GitHub job status from a service observation.
        work = [
            (title, advice, "EIGENAAR NODIG · NIET UITGEVOERD")
            for title, advice in recovery_priorities(data)[:3]
        ]
        window = {
            "last_utc": _handoff_text(data.get("time")),
            "observations": 1 + int(data.get("comparison") is not None),
            "comparison_count": int(data.get("comparison") is not None),
        }
        source_kind = (
            "private-offline-pair" if data.get("offline_replay") is True
            and data.get("comparison") is not None else
            "private-offline-receipt" if data.get("offline_replay") is True else
            "local-current-with-saved-previous"
            if data.get("comparison") is not None else
            "local-read-only-current"
        )

    # A missing observation is UNKNOWN, never zero healthy/failed runners.
    observed = units.get("runner_units_observed") is True
    rows = units.get("runner_units")
    if observed and (not isinstance(rows, list) or len(rows) > 20):
        raise ValueError("Unbounded runner inventory")
    counts = {state: 0 for state in ("active", "failed", "inactive", "unknown")}
    services = None
    if observed:
        # Reuse the existing receipt's strict canonical unit-name, bounded-row,
        # state and duplicate validator. Never forward arbitrary process names.
        from zsentinel_runner_snapshot import _unit_states
        valid = _unit_states({"units": units})
        services = []
        for name, service in sorted(valid.items()):
            state = service["state"]
            counts[state if state in counts else "unknown"] += 1
            services.append({
                "unit": name, "systemd_state": state,
                "github_job_assignment_verified": False,
                "recovery_authorized": False,
            })

    from zsentinel_runner_snapshot import _user_unit_states
    user_observed = units.get("user_runner_units_observed") is True
    user_services = None
    user_counts = None
    if user_observed:
        valid_user = _user_unit_states({"units": units})
        user_counts = {state: 0 for state in ("active", "failed", "inactive", "unknown")}
        user_services = []
        for name, row in sorted(valid_user.items()):
            state = row["state"]
            user_counts[state if state in user_counts else "unknown"] += 1
            user_services.append({
                "unit": name, "systemd_state": state, "scope": "user",
                "github_job_assignment_verified": False,
                "recovery_authorized": False,
            })
    else:
        # Validate present saved data even if the user manager was inaccessible.
        _user_unit_states({"units": units})

    pressure = {}
    for key in ("host_load", "cores", "memory_available_gib",
                "swap_used_percent", "disk_unavailable_percent",
                "root_available_gib", "root_inode_available_percent"):
        value = host.get(key)
        pressure[key] = (value if type(value) in (int, float)
                         and math.isfinite(value) and 0 <= value < 1e10
                         else None)
    # Kernel OOM counters apply to ALL host processes, not any named runner.
    # A missing/newer-source signal remains null, not a healthy zero.
    psi_available = host.get("memory_psi_observed") is True
    for key in ("memory_psi_full_avg10_percent",
                "memory_psi_some_avg10_percent"):
        value = host.get(key)
        pressure[key] = (value if psi_available
                         and type(value) in (int, float)
                         and math.isfinite(value) and 0 <= value <= 100
                         else None)
    kills = host.get("kernel_oom_kills_since_boot")
    pressure["kernel_oom_kills_since_boot"] = (
        kills if host.get("kernel_oom_counter_observed") is True
        and type(kills) is int and 0 <= kills < 2 ** 63 else None)
    pressure["memory_psi_observed"] = all(
        pressure[key] is not None
        for key in ("memory_psi_full_avg10_percent",
                    "memory_psi_some_avg10_percent"))
    pressure["kernel_oom_counter_observed"] = (
        pressure["kernel_oom_kills_since_boot"] is not None)
    # An offline T0/T1 or multi-snapshot inbox must include the observed
    # service *changes*, not just the last inventory. Keep all window
    # summaries and at most 40 canonical transitions, favouring newest
    # evidence when older windows exceed the cap.
    pairs = timeline if timeline is not None else (
        [data["comparison"]] if isinstance(data.get("comparison"), dict) else []
    )
    steps = []
    for pair in pairs:
        if not isinstance(pair, dict) or not isinstance(pair.get("transitions"), list):
            raise ValueError("Malformed admitted service transition window")
        changes = pair["transitions"]
        if len(changes) > 40:
            raise ValueError("Unbounded transition source window")
        steps.append({
            "from_utc": _handoff_text(pair.get("previous_utc")),
            "to_utc": _handoff_text(pair.get("current_utc")),
            "same_boot_verified": pair.get("same_boot_verified") is True,
            "source_complete": pair.get("complete_service_coverage") is True,
            "observed_transition_count": len(changes),
            "host_oom_counter_comparable": (
                pair.get("memory_oom_comparison", {}).get(
                    "oom_counter_same_boot_comparable") is True
                if isinstance(pair.get("memory_oom_comparison"), dict) else False),
            "host_kernel_oom_kills_delta": (
                pair["memory_oom_comparison"].get("kernel_oom_kills_delta")
                if isinstance(pair.get("memory_oom_comparison"), dict)
                and pair["memory_oom_comparison"].get(
                    "oom_counter_same_boot_comparable") is True
                and type(pair["memory_oom_comparison"].get(
                    "kernel_oom_kills_delta")) is int
                and 0 <= pair["memory_oom_comparison"]["kernel_oom_kills_delta"] < 2 ** 63
                else None),
        })
    from zsentinel_runner_snapshot import RUNNER_UNIT
    selected = []
    for index in range(len(pairs) - 1, -1, -1):
        for item in reversed(pairs[index]["transitions"]):
            name = item.get("unit")
            event_kind = item.get("change")
            if (not isinstance(name, str) or len(name) > 160
                    or not RUNNER_UNIT.fullmatch(name)
                    or event_kind not in {
                        "newly_observed", "no_longer_observed", "service_state_changed",
                        "previous_source_gap", "current_source_gap"
                    }):
                raise ValueError("Invalid canonical source transition")
            if len(selected) >= 40:
                break
            selected.append({
                "window_index": index,
                "unit": name,
                "change": event_kind,
                "same_boot_verified": pairs[index].get("same_boot_verified") is True,
                "source_complete": pairs[index].get("complete_service_coverage") is True,
                "github_job_recovered": None,
                "action_authorized": False,
            })
        if len(selected) >= 40:
            break
    selected.reverse()
    total_transitions = sum(x["observed_transition_count"] for x in steps)
    user_selected = []
    user_total = 0
    for index in range(len(pairs) - 1, -1, -1):
        observed_users = pairs[index].get("user_transitions", [])
        if not isinstance(observed_users, list) or len(observed_users) > 40:
            raise ValueError("Unbounded user-systemd transition window")
        user_total += len(observed_users)
        for row in reversed(observed_users):
            name, kind = row.get("unit"), row.get("change")
            if (row.get("scope") != "user" or
                    not isinstance(name, str) or len(name) > 160
                    or not RUNNER_UNIT.fullmatch(name)
                    or kind not in {"newly_observed", "no_longer_observed",
                                    "service_state_changed", "previous_source_gap",
                                    "current_source_gap"}):
                raise ValueError("Invalid user manager transition receipt")
            if len(user_selected) < 40:
                user_selected.append({
                    "window_index": index, "scope": "user", "unit": name,
                    "change": kind,
                    "source_complete": pairs[index].get(
                        "complete_user_service_coverage") is True,
                    "same_boot_verified": pairs[index].get("same_boot_verified") is True,
                    "github_job_recovered": None, "action_authorized": False,
                })
    user_selected.reverse()
    payload = {
        "schema": "zsentinel.runner-owner-inbox.v1",
        "source": source_kind,
        "window": window,
        "evidence": {
            "capacity_complete": host.get("source_complete") is True,
            "runner_inventory_observed": observed,
            "runner_inventory_complete": observed and units.get("runner_units_truncated") is False,
            "runner_service_counts": counts if observed else None,
            "runner_services": services,
            "user_runner_inventory_observed": user_observed,
            "user_runner_inventory_complete": (
                user_observed and units.get("user_runner_units_truncated") is False),
            "user_runner_service_counts": user_counts,
            "user_runner_services": user_services,
            "capacity": pressure,
            "host_oom_events_attributed_to_runner": False,
            "runner_restart_authorized": False,
            "coach_quota_recommended_percent": data.get("quota")
                if type(data.get("quota")) is int and data["quota"] in (60, 100, 175) else None,
            "coach_weight_recommended": data.get("weight")
                if type(data.get("weight")) is int and data["weight"] in (25, 40, 70) else None,
        },
        "observation_steps": steps,
        "observed_transitions": selected,
        "transition_items_omitted": total_transitions - len(selected),
        "observed_user_transitions": user_selected,
        "user_transition_items_omitted": user_total - len(user_selected),
        "owner_actions": [
            {"problem": _handoff_text(title, 160),
             "owner_action": _handoff_text(advice, 500),
             "status": _handoff_text(status, 160),
             "action_authorized": False}
            for title, advice, status in work[:3]
        ],
        "authority": {"claim": False, "restart": False, "dispatch": False,
                      "merge": False, "deploy": False},
        "github_job_recovered": None,
        "production_restored": None,
        "limitations": ["systemd is not GitHub job assignment",
                        "offline history is not current release or claim evidence",
                        "source gaps must not be interpreted as healthy capacity"],
    }
    result = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(result.encode("utf-8")) > 128 * 1024:
        raise ValueError("Private JSON inbox exceeds size limit")
    return result



def render_private_immediate_handoff(data):
    """Single-observation or T0/T1 owner handoff from the same JSON decision.

    No historical 3+ sample requirement for an urgent human-owner message.
    Preserve UNKNOWN assignment and source coverage rather than invent recovery.
    """
    import json
    evidence = json.loads(render_private_json_handoff(data))
    if evidence["source"] not in {"local-read-only-current",
                                  "local-current-with-saved-previous",
                                  "private-offline-pair", "private-offline-receipt"}:
        raise ValueError("Immediate handoff requires current or saved T0/T1 evidence")
    source = evidence["source"]
    window = evidence["window"]
    observed = evidence["evidence"]
    origin = (
        "Live eenmalig gelezen (alleen lokale host/systemd-bron)"
        if source == "local-read-only-current" else
        "Nieuwe lokale hostobservatie met eerdere opgeslagen T0-bon (T0 is historisch)"
        if source == "local-current-with-saved-previous" else
        "HISTORISCHE PRIVÉBON: één oude momentopname (niet opnieuw live bevestigd)"
        if source == "private-offline-receipt" else
        "HISTORISCHE T0/T1-bonnen (niet opnieuw live bevestigd)"
    )
    coverage = ("Volledig binnen de lokale runnerlimiet"
                if observed["runner_inventory_complete"] else
                "GEDEELTELIJK OF ONBEKEND")
    readings = observed["capacity"]
    rows = [
        "# zSentinel | privé onmiddellijke runner-eigenaaroverdracht",
        "",
        "Bron: " + origin + ".",
        "Laatste bronmoment: " + _handoff_text(window.get("last_utc")) + ".",
        "Waarnemingen: " + str(window["observations"]) + "; "
        "vergelijkingen: " + str(window["comparison_count"]) + ".",
        "**GEEN GECONFIRMEERD GITHUB-HERSTEL, CLAIMVRIJGAVE OF PRODUCTIEDEPLOY.**",
        "",
        "## Kort eigenaarwerk",
    ]
    for item in evidence["owner_actions"][:3]:
        rows += [
            "### " + _handoff_text(item["problem"]),
            "Probleem en bevoegde vervolgactie: "
            + _handoff_text(item["owner_action"], 500),
            "Status: " + _handoff_text(item["status"]) + ".",
            "",
        ]
    rows += [
        "## Brondekking en VPS-capaciteit",
        "- Runnerinventaris: " + coverage + ".",
        "- Capaciteit: " + ("volledig" if observed["capacity_complete"] else
                           "GEDEELTELIJK / ONBEKEND") + ".",
    ]
    service_counts = observed["runner_service_counts"]
    if service_counts is None:
        rows.append("- Servicetellingen: ONBEKEND (geen lege bron als nul geïnterpreteerd).")
    else:
        rows.append("- Servicetellingen binnen bronlimiet: " + ", ".join(
            key + "=" + str(service_counts[key])
            for key in ("active", "failed", "inactive", "unknown")
        ) + ".")
    for field, label, suffix in (
        ("host_load", "Load", ""), ("cores", "CPU-kernen", ""),
        ("memory_available_gib", "RAM beschikbaar", " GiB"),
        ("swap_used_percent", "Swap gebruikt", "%"),
        ("disk_unavailable_percent", "Root niet beschikbaar", "%"),
        ("root_available_gib", "Root beschikbaar", " GiB"),
        ("root_inode_available_percent", "Root-inodes vrij", "%"),
    ):
        rows.append("- " + label + ": " +
                    _handoff_number(readings.get(field), suffix) + ".")
    rows += ["", "## Hostbreed OOM/PSI-bronbewijs", ""]
    rows.append("- Memory PSI some avg10: " + _handoff_number(
        readings.get("memory_psi_some_avg10_percent"), "%") +
        "; full avg10: " + _handoff_number(
            readings.get("memory_psi_full_avg10_percent"), "%") + ".")
    rows.append("- Kernel-OOM-kills sinds boot (ALLE processen, geen runnerclaim): " +
                _handoff_number(readings.get("kernel_oom_kills_since_boot")) + ".")
    rows.append("- OOM-kernelbronnen bewijzen niet dat een bepaalde runner "
                "of GitHub-job is hersteld. Geen herstart geautoriseerd.")
    rows += ["", "## Waargenomen runner-services (alleen systemd)", ""]
    services = observed["runner_services"]
    if services is None:
        rows.append("- Geen verifieerbare bron; actuele runnerstatus ONBEKEND.")
    elif not services:
        rows.append("- Geen runner-service binnen deze bron gezien; geen bewijs dat GitHub gezond is.")
    else:
        for entry in services[:20]:
            rows.append("- " + _handoff_text(entry["unit"]) + ": "
                        + _handoff_text(entry["systemd_state"])
                        + "; GitHub-jobtoewijzing NIET BEWEZEN.")
    rows += ["", "## Gebruikersservices (aparte systemd --user bron)", ""]
    user_services = observed["user_runner_services"]
    if user_services is None:
        rows.append("- User-manager bron ONBEKEND: niet gezien of oud bronformaat.")
    elif not user_services:
        rows.append("- Geen user-runner waargenomen; geen GitHub-herstelbewijs.")
    else:
        for entry in user_services[:20]:
            rows.append("- user-systemd / " + _handoff_text(entry["unit"]) + ": "
                        + _handoff_text(entry["systemd_state"])
                        + "; jobtoewijzing NIET BEWEZEN.")
    if not observed["user_runner_inventory_complete"]:
        rows.append("- User-servicebron GEDEELTELIJK/ONBEKEND; niet gezien is niet opgelost.")
    steps = evidence["observation_steps"]
    if steps:
        rows += ["", "## T0/T1 bronverschillen"]
        for step in steps[:11]:
            rows.append("- " + _handoff_text(step["from_utc"]) + " → "
                        + _handoff_text(step["to_utc"])
                        + "; " + str(step["observed_transition_count"])
                        + " overgang(en), bron "
                        + ("volledig" if step["source_complete"] else "ONZEKER")
                        + ", zelfde boot "
                        + ("ja" if step["same_boot_verified"] else "niet bevestigd") + ".")
        for step in steps[:11]:
            count = step.get("host_kernel_oom_kills_delta")
            rows.append("- Hostbrede OOM-toename " + _handoff_text(step["from_utc"]) +
                        " → " + _handoff_text(step["to_utc"]) + ": " +
                        (str(count) if step.get("host_oom_counter_comparable") is True
                         and type(count) is int and 0 <= count < 2 ** 63
                         else "ONBEKEND (boot/bron)") +
                        "; dit is geen aantal runnercrashes.")
        for change in evidence["observed_transitions"][:40]:
            rows.append("  - " + _handoff_text(change["unit"]) + ": "
                        + _handoff_text(change["change"]) + "; geen herstelbewijs.")
        for change in evidence["observed_user_transitions"][:40]:
            rows.append("  - user-systemd / " + _handoff_text(change["unit"]) + ": "
                        + _handoff_text(change["change"]) + "; geen herstelbewijs.")
        if evidence["user_transition_items_omitted"]:
            rows.append("- Weggelaten oudere user-systemd overgangen: "
                        + str(evidence["user_transition_items_omitted"]) + ".")
        if evidence["transition_items_omitted"]:
            rows.append("- Weggelaten oudere overgangen: "
                        + str(evidence["transition_items_omitted"]) + ".")
    rows += [
        "",
        "## Veiligheid en vervolgstap",
        "- Eigenaar controleert echte GitHub-run/toewijzing en claim apart.",
        "- Alle claim-, serviceherstart-, workflow-, merge- en deployautorisaties: NEE.",
        "- Actieve systemd-service is geen bewijs van geslaagde job of modeltraining.",
        "- Bronvenster is niet bewijs van onafgebroken runner- of leasecontinuïteit.",
        "- Geen actie door deze overdracht uitgevoerd.",
        "",
    ]
    result = "\n".join(rows)
    if len(result.encode("utf-8")) > 128 * 1024:
        raise ValueError("Private immediate handoff exceeds artifact limit")
    return result



def render_owner_issue_draft(data):
    """Redacted, owner-editable GitHub issue text; never posts a GitHub issue.

    Build only from the admitted private inbox decision. No host name,
    service names, PIDs, command arguments, internal URLs, credentials or
    numeric capacity details are copied into the shareable draft.
    """
    import hashlib
    import json

    raw = render_private_json_handoff(data)
    inbox = json.loads(raw)
    if inbox.get("schema") != "zsentinel.runner-owner-inbox.v1":
        raise ValueError("Unknown owner decision")
    incident_ref = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    kind = inbox["source"]
    historical = kind.startswith("private-offline-")
    coverage = inbox["evidence"]
    action_items = inbox["owner_actions"][:3]
    if not action_items:
        raise ValueError("Owner action source is missing")
    lines = [
        "# [zSentinel] Runner / GitHub-toewijzing: eigenaarbeoordeling vereist",
        "",
        "**Status: NIET BEVESTIGD; geen runnerherstart, claimmutatie of deploy.**",
        "",
        "- Incidentreferentie (geen autorisatietoken): `" + incident_ref + "`",
        "- Bron: " + (
            "historische privéwaarneming" if historical else
            "nieuwe read-only waarneming met historisch T0"
            if kind == "local-current-with-saved-previous" else
            "eenmalige lokale read-only waarneming") + ".",
        "- GitHub-jobtoewijzing: ONBEKEND; apart door eigenaar controleren.",
        "- GitHub-workflow / CI-resultaat: NIET ONDERZOCHT DOOR DIT RAPPORT.",
        "- Runnerinventaris: " + (
            "begrensd lokaal waargenomen"
            if coverage.get("runner_inventory_complete") is True else
            "ONVOLLEDIG OF ONBEKEND") + ".",
        "- VPS-capaciteitsbron: " + (
            "lokaal waargenomen" if coverage.get("capacity_complete") is True else
            "ONVOLLEDIG OF ONBEKEND") + ".",
        "",
        "## Gevraagde actie voor bevoegde project-/runner-eigenaar",
    ]
    for index, item in enumerate(action_items, start=1):
        lines += [
            "",
            str(index) + ". **" + _handoff_text(item["problem"], 160) + "**",
            "   - " + _handoff_text(item["owner_action"], 500),
            "   - Uitvoeringsstatus: NIET UITGEVOERD; eerst bron en mandaat nagaan.",
        ]
    lines += [
        "",
        "## Vereist extern bewijs vóór sluiting",
        "",
        "- [ ] Project, actuele taakclaim en bevoegde eigenaar bevestigd.",
        "- [ ] Exacte GitHub-job/toewijzing en workflowstatus apart onderzocht.",
        "- [ ] Alleen veilige geautoriseerde herstelstappen uitgevoerd indien nodig.",
        "- [ ] Onafhankelijke bron bevestigt daadwerkelijk herstel of HOLD-oorzaak.",
        "- [ ] PR-review, exact-head verplichte CI en releasevoorwaarden intact.",
        "",
        "## Privébijlage",
        "",
        "Het originele runnerincidentpakket blijft uitsluitend bij de operator.",
        "Plaats GEEN private snapshots, servicelijsten, hostdetails, cgroupwaarden,",
        "tokens, padnamen of ruwe logs in een openbare GitHub-issue.",
        "Deze conceptmelding is geen bewijs van jobherstel of toestemming tot actie.",
        "",
    ]
    output = "\n".join(lines)
    if len(output.encode("utf-8")) > 128 * 1024:
        raise ValueError("Shareable issue draft exceeds size limit")
    return output


def save_private(path, markup, extension=".html"):
    target = Path(path)
    if extension not in {".html", ".md", ".json"} or (
            not target.name or target.name in {".", ".."} or target.suffix != extension):
        raise ValueError("Choose a new private output filename with the correct suffix")
    raw = markup.encode("utf-8")
    if len(raw) > 128 * 1024:
        raise ValueError("Report size limit exceeded")
    # Refuse symlink final directory component and refuse file overwrites.
    directory = os.open(str(target.parent), os.O_RDONLY | os.O_DIRECTORY |
                        getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    created = False
    try:
        fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        created = True
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            os.unlink(target.name, dir_fd=directory)
            created = False
            raise
    finally:
        os.close(directory)
    return created



def save_private_incident_bundle(path, data):
    """Create one owner-private incident folder from one admitted observation.

    HTML, Markdown and JSON are rendered before the first filesystem write.
    The SHA256 manifest makes source equivalence verifiable without carrying
    process arguments, credentials or private source file paths.
    """
    import hashlib
    import json

    report_json = render_private_json_handoff(data)
    payload = json.loads(report_json)
    report_markdown = (render_private_handoff(data) if data.get("timeline") is not None
                       else render_private_immediate_handoff(data))
    issue_draft = render_owner_issue_draft(data)
    reference = hashlib.sha256(report_json.encode("utf-8")).hexdigest()[:24]
    # Static links only to the same private directory; never to an external
    # GitHub endpoint, nor to untrusted filenames or user-supplied URLs.
    navigation = (
        '<section class="panel"><h2>Bijbehorende privé overdracht</h2>'
        '<p>Incidentreferentie: <code>' + reference + '</code></p>'
        '<ul><li><a href="owner-handoff.md">Handmatige eigenaaroverdracht</a></li>'
        '<li><a href="github-owner-issue-draft.md">Gerédigeerd GitHub-issueconcept</a></li>'
        '<li><a href="owner-inbox.json">Machineleesbare incidentgegevens</a></li></ul>'
        '<p>Deze links openen alleen lokale bestanden. Geen upload of herstelactie.</p>'
        '</section>'
    )
    report_html = render(data)
    if "<main>" not in report_html:
        raise ValueError("Missing local report navigation insertion point")
    report_html = report_html.replace("<main>", "<main>" + navigation, 1)
    report_markdown = "Incidentreferentie: `" + reference + "`\n\n" + report_markdown
    rendered = {
        "incident.html": report_html.encode("utf-8"),
        "owner-handoff.md": report_markdown.encode("utf-8"),
        "owner-inbox.json": report_json.encode("utf-8"),
        "github-owner-issue-draft.md": issue_draft.encode("utf-8"),
    }
    if any(not body or len(body) > 128 * 1024 for body in rendered.values()):
        raise ValueError("Private incident artifact exceeds bounded output size")
    manifest = {
        "schema": "zsentinel.private-incident-bundle.v1",
        "incident_ref": hashlib.sha256(rendered["owner-inbox.json"]).hexdigest()[:24],
        "source": payload["source"],
        "observation_window": payload["window"],
        "artifacts": {
            name: {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
            for name, body in sorted(rendered.items())
        },
        "claim_authorized": False,
        "service_restart_authorized": False,
        "github_dispatch_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "github_job_recovered": None,
        "production_restored": None,
    }
    rendered["MANIFEST.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")

    target = Path(path)
    if not target.name or target.name in {".", ".."} or target.is_symlink():
        raise ValueError("Choose a fresh incident bundle folder")
    # The parent is already provisioned by the owner, not created in a shared
    # runtime tree. All entries are opened relative to pinned directory fds.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    parent_fd = os.open(str(target.parent), flags)
    bundle_fd = None
    made = False
    written = []
    try:
        info = os.fstat(parent_fd)
        if (info.st_uid != os.geteuid() or
                stat.S_IMODE(info.st_mode) & 0o077 or
                not stat.S_ISDIR(info.st_mode)):
            raise PermissionError("Private owner directory required")
        os.mkdir(target.name, mode=0o700, dir_fd=parent_fd)
        made = True
        bundle_fd = os.open(target.name, flags, dir_fd=parent_fd)
        os.fchmod(bundle_fd, 0o700)
        for name, body in rendered.items():
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                         0o600, dir_fd=bundle_fd)
            written.append(name)
            with os.fdopen(fd, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
        os.fsync(bundle_fd)
        os.fsync(parent_fd)
    except BaseException:
        if bundle_fd is not None:
            for name in reversed(written):
                try:
                    os.unlink(name, dir_fd=bundle_fd)
                except FileNotFoundError:
                    pass
        if made:
            try:
                os.rmdir(target.name, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    finally:
        if bundle_fd is not None:
            os.close(bundle_fd)
        os.close(parent_fd)
    return manifest



def verify_private_incident_bundle(path):
    """Read-only receipt consistency check, not an authenticity signature."""
    import hashlib
    import hmac
    import json

    directory = os.open(
        str(Path(path)),
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        info = os.fstat(directory)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError("Private owned bundle required")
        names = {"incident.html", "owner-handoff.md", "owner-inbox.json", "MANIFEST.json"}
        optional = "github-owner-issue-draft.md"
        found = set(os.listdir(directory))
        # Existing four-file bundles remain readable. New bundles include a
        # separately redacted GitHub issue draft, always manifest-covered.
        if found != names and found != names | {optional}:
            raise ValueError("Incomplete or unexpected incident bundle")
        names = found
        source = {}
        for name in sorted(names):
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory,
            )
            try:
                meta = os.fstat(fd)
                if (not stat.S_ISREG(meta.st_mode) or
                        meta.st_uid != os.geteuid() or
                        stat.S_IMODE(meta.st_mode) & 0o077 or
                        not 0 < meta.st_size <= 128 * 1024):
                    raise ValueError("Untrusted incident file")
                with os.fdopen(fd, "rb", closefd=False) as reader:
                    data = reader.read(128 * 1024 + 1)
                if len(data) != meta.st_size:
                    raise ValueError("Incomplete incident file")
                source[name] = data
            finally:
                os.close(fd)
    finally:
        os.close(directory)
    manifest = json.loads(source["MANIFEST.json"].decode("utf-8"))
    inbox = json.loads(source["owner-inbox.json"].decode("utf-8"))
    if (type(manifest) is not dict
            or manifest.get("schema") != "zsentinel.private-incident-bundle.v1"
            or type(inbox) is not dict
            or inbox.get("schema") != "zsentinel.runner-owner-inbox.v1"
            or manifest.get("source") != inbox.get("source")
            or manifest.get("observation_window") != inbox.get("window")):
        raise ValueError("Incident source and manifest disagree")
    identity = hashlib.sha256(source["owner-inbox.json"]).hexdigest()[:24]
    if (not isinstance(manifest.get("incident_ref"), str)
            or not hmac.compare_digest(manifest["incident_ref"], identity)):
        raise ValueError("Incident reference mismatch")
    for key in ("claim_authorized", "service_restart_authorized",
                "github_dispatch_authorized", "merge_authorized",
                "deploy_authorized"):
        if manifest.get(key) is not False:
            raise ValueError("Incident manifest grants unsupported authority")
    if manifest.get("github_job_recovered") is not None or manifest.get(
            "production_restored") is not None:
        raise ValueError("Manifest falsely attests live recovery")
    artifacts = manifest.get("artifacts")
    if type(artifacts) is not dict or set(artifacts) != set(source) - {"MANIFEST.json"}:
        raise ValueError("Incident manifest is incomplete")
    for name, expected in artifacts.items():
        if type(expected) is not dict or type(expected.get("bytes")) is not int:
            raise ValueError("Malformed manifest receipt")
        actual = source[name]
        digest = hashlib.sha256(actual).hexdigest()
        if (expected["bytes"] != len(actual) or
                not isinstance(expected.get("sha256"), str) or
                not hmac.compare_digest(expected["sha256"], digest)):
            raise ValueError("Incident source checksum mismatch")
    return {"schema": manifest["schema"], "source": manifest["source"],
            "incident_ref": identity, "verified_files": len(source), "content_consistent": True,
            "cryptographic_origin_attested": False,
            "github_job_recovered": None, "production_restored": None}


def main():
    parser = argparse.ArgumentParser(description="Private one-shot zSentinel runner incident workbench")
    parser.add_argument("--output", help="NEW private 0600 output (never overwritten)")
    parser.add_argument("--bundle", help="NEW owner-private folder for coherent HTML/MD/JSON incident package")
    parser.add_argument("--verify-bundle", help="Read-only checksum and private-file consistency inspection")
    parser.add_argument("--format", choices=("html", "markdown", "json", "issue"), default="html",
                        help="Private HTML, JSON or Markdown owner handoff from current, T0/T1 or offline timeline")
    parser.add_argument("--receipt", help="Replay one previously saved owner-private JSON (no current host read)")
    parser.add_argument("--previous", help="Optional owner-private T0 JSON, same-host and <=72 hours")
    parser.add_argument("--t0", help="Offline replay: first owner-private JSON receipt")
    parser.add_argument("--t1", help="Offline replay: later owner-private JSON receipt")
    parser.add_argument("--timeline", nargs="+", metavar="PRIVATE_JSON",
                        help="Offline historical view of 3–12 ordered private receipts")
    args = parser.parse_args()
    if args.verify_bundle:
        if (args.output or args.bundle or args.receipt or args.previous or
                args.t0 or args.t1 or args.timeline or args.format != "html"):
            parser.error("--verify-bundle is standalone and read-only")
        try:
            summary = verify_private_incident_bundle(args.verify_bundle)
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            parser.exit(1, "Privé incidentpakket niet verifieerbaar; geen bron aangepast.\\n")
        import json
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return
    if bool(args.output) == bool(args.bundle):
        parser.error("Choose exactly one: --output or --bundle")
    if args.bundle and args.format != "html":
        parser.error("--bundle creates all formats; omit --format")
    if (bool(args.t0) != bool(args.t1) or
            sum(bool(x) for x in (args.previous, args.t0, args.timeline, args.receipt)) > 1):
        parser.error("Choose one: --t0 and --t1, --previous, or --timeline")
    if args.timeline and not 3 <= len(args.timeline) <= 12:
        parser.error("--timeline requires 3–12 chronological private receipts")
    try:
        if args.timeline:
            data = offline_timeline_report(args.timeline)
        elif args.t0:
            data = offline_comparison_report(args.t0, args.t1)
        elif args.receipt:
            data = offline_receipt_report(args.receipt)
        else:
            data = collect()
            if args.previous:
                # Import only for opt-in private previous-receipt comparison.
                from zsentinel_runner_snapshot import packet, changes, load_private
                current = packet(data)
                data["comparison"] = changes(load_private(args.previous), current)
        if args.bundle:
            save_private_incident_bundle(args.bundle, data)
        elif args.format == "json":
            markup = render_private_json_handoff(data)
            save_private(args.output, markup, extension=".json")
        elif args.format == "issue":
            markup = render_owner_issue_draft(data)
            save_private(args.output, markup, extension=".md")
        elif args.format == "markdown":
            markup = (render_private_handoff(data) if args.timeline else
                      render_private_immediate_handoff(data))
            save_private(args.output, markup, extension=".md")
        else:
            markup = render(data)
            save_private(args.output, markup)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        # Do not echo report paths, unit command lines or operator-private details.
        parser.exit(1, "Kon geen privé rapport maken; controleer veilige doelmap en rechten.\n")
    print("Privé coherent incidentpakket gemaakt (0700/0600; geen servicewijziging)." if args.bundle
          else "Privé herstelwerkbank gemaakt (0600, zonder live wijziging).")


if __name__ == "__main__":
    main()
