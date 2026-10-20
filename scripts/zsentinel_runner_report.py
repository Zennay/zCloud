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

from zsentinel_background_priority import (
    admission, installed_cgroup, matches_cgroup, pressure_reasons,
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
        coach = {
            "observed": True,
            "active": current["active"],
            "weight": current["weight"],
            "quota": round(current["quota_seconds"] * 100, 2)
            if current["quota_seconds"] is not None else None,
            "recommendation_matches": matches_cgroup(current, quota, weight),
        }
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        # Deliberately redact systemd error text (may include private paths).
        coach = {"observed": False, "active": None, "weight": None,
                 "quota": None, "recommendation_matches": None}
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
    entries = unit_source["runner_units"] if unit_source["runner_units_observed"] else []
    failed = [u for u in entries if u["state"] == "failed"]
    stopped = [u for u in entries if u["state"] == "inactive"]
    active = [u for u in entries if u["state"] == "active"]
    load = host["host_load"]
    cores = host["cores"]
    under_pressure = (not host["source_complete"] or
                      host["swap_used_percent"] is None or
                      host["disk_unavailable_percent"] is None or
                      load is None or
                      host["swap_used_percent"] >= 80 or
                      host["disk_unavailable_percent"] >= 90 or
                      load > cores * 1.35)
    actions = []
    if under_pressure:
        actions.append(("P0 · capaciteit veiligstellen",
                        "Geheugen-, swap-, load- of schijfdruk is hoog of onvoldoende "
                        "geobserveerd. Start geen extra workers of OOM-gefaalde "
                        "runner-services zolang de bevoegde beheerder niet "
                        "onafhankelijk heeft vastgesteld dat er voldoende ruimte is."))
    if failed:
        actions.append(("P1 · gefaalde runner-services",
                        f"{len(failed)} service(s) hebben een systemd-fout. "
                        "De bevoegde service-eigenaar onderzoekt oorzaak en "
                        "huidige GitHub-labelroute; dit rapport voert geen herstart uit."))
    if stopped:
        actions.append(("P1 · gestopte runner-services",
                        f"{len(stopped)} service(s) staan uit. Een geslaagde "
                        "stop kan bewust zijn; eerst eigenaarsintentie controleren."))
    if active:
        actions.append(("P2 · actieve services",
                        f"{len(active)} service(s) zijn actief. GitHub-jobtoewijzing, "
                        "actuele workflowstatus en CI blijven afzonderlijk te bevestigen."))
    if not unit_source["runner_units_observed"] or unit_source.get("runner_units_truncated"):
        actions.append(("Brondekking onvolledig",
                        "Runnerinventaris is onvolledig. Ontbrekende services of jobs "
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
    }
    metric_rows = []
    for metric in capacity.get("metrics", [])[:4]:
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
        + '<ul class="transitions">' + items + '</ul>' + capacity_html + coach_html +
        '<p class="warning">Dit toont alleen waargenomen systemd-statusverschillen. '
        'Ook failed→active bewijst geen toegewezen GitHub-job, geslaagde CI, merge '
        'of live herstel. Niet gezien ≠ opgelost; geen automatische herstart.</p></section>'
    )


def render(data):
    host = data["host"]
    units = data["units"]
    coach = data["coach"]
    history = render_comparison(data.get("comparison"))
    timeline_html = render_timeline(data.get("timeline"), data.get("timeline_observations", 0))
    footprint_html = render_timeline_footprint(data.get("timeline_footprint"))
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
        box("Root niet beschikbaar", _value(host["disk_unavailable_percent"], "%")),
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
<section class="panel"><h2>Vrijblijvend CPU-advies voor Fuckball-training</h2>
<p><strong>Advies:</strong> CPUQuota {_value(data["quota"], "%")}, CPUWeight {_value(data["weight"])}</p>
<p><strong>Oorzaken:</strong> {escape(reasons)}</p>
<p>{escape(coach_state)} · {escape(coach_match)}</p>
<p>De voorbeeldinstellingen zijn niet door dit rapport toegepast.</p></section>
{history}
{footprint_html}
{timeline_html}
<h2>GitHub-runner-servicestatus</h2><p class="muted">{escape(overview)}</p>
<div class="grid">{"".join(cards)}</div>
<section class="panel"><h2>Prioriteit en veilige herstelvolgorde</h2><ol>{plans}</ol>
<p>Dit zijn leesadviezen, geen bewijs van toestemming tot herstart, dispatch of merge.</p></section>
<section class="panel"><h2>Bevoegde vervolgactie</h2>
<p>Geef voorrang aan gefaalde OOM-services wanneer RAM/swap voldoende ruimte hebben en
de service-eigenaar herstel toestaat. Controleer voor gestopte services eerst of dat bewust is.
Een actieve service bewijst geen GitHub job, succesvolle CI, main-merge of productieherstel.</p>
<p>Onbekende bronnen en gedeeltelijke observaties blijven onbekend. Geen automatische
retry, servicewijziging, GitHub-actie of portfolio-claim is uitgevoerd.</p></section>
<footer>Bron: lokale Linux host- en systemd-observatie. Geen netwerkverzoek of credentialopslag.
Alleen bedoeld als privé operatoroverzicht, niet als productie- of releasebewijs.</footer></main></body></html>"""


def offline_comparison_data(previous, current):
    """Render from the same already-admitted private receipts, without re-reading."""
    from zsentinel_runner_snapshot import changes
    comparison = changes(previous, current)
    rec = current.get("cpu_recommendation")
    capacity = current.get("capacity")
    if not isinstance(rec, dict) or not isinstance(capacity, dict):
        raise ValueError("Incomplete stored capacity receipt")
    coach = current.get("coach_state")
    if not isinstance(coach, dict):
        # Older stored T1 may lack coach detail. Never infer live settings.
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
        "comparison": comparison,
        "offline_replay": True,
    }




def offline_comparison_report(t0_path, t1_path):
    """Open each private T0/T1 receipt exactly once; never probe live state."""
    from zsentinel_runner_snapshot import load_private
    return offline_comparison_data(load_private(t0_path), load_private(t1_path))


def timeline_footprint(receipts):
    """Bounded counts of observed failed services and capacity pressure windows.

    Repeated sampled failures are not separate crashes or proof of a retry.
    """
    from zsentinel_runner_snapshot import _unit_states
    failed = {}
    observed = {}
    partial = 0
    high_swap = 0
    high_disk = 0
    high_load = 0
    unknown_capacity = 0
    for receipt in receipts:
        units = receipt.get("units") or {}
        rows = _unit_states(receipt)
        if units.get("runner_units_observed") is not True or units.get("runner_units_truncated") is not False:
            partial += 1
        for name, state in rows.items():
            observed[name] = observed.get(name, 0) + 1
            if state["state"] == "failed":
                failed[name] = failed.get(name, 0) + 1
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
    return {
        "samples": len(receipts), "incomplete_service_samples": partial,
        "unknown_capacity_samples": unknown_capacity,
        "swap_high_samples": high_swap,
        "root_high_samples": high_disk,
        "load_high_samples": high_load,
        "failed_observations": [
            {"unit": name, "failed_samples": count,
             "seen_samples": observed[name]}
            for name, count in sorted(failed.items(),
                                      key=lambda pair: (-pair[1], pair[0]))[:20]
        ],
        "restart_or_job_count_inferable": False,
    }


def timeline_owner_actions(summary, comparisons):
    """At most three evidence-bound human follow-ups; no execution or dispatch."""
    if not isinstance(summary, dict) or comparisons is None:
        return []
    failed = summary.get("failed_observations") or []
    stressed = (summary.get("swap_high_samples", 0) > 0
                or summary.get("root_high_samples", 0) > 0
                or summary.get("load_high_samples", 0) > 0)
    reboot = any(pair.get("host_reboot_observed") is True for pair in comparisons)
    unknown = (summary.get("incomplete_service_samples", 0) > 0
               or summary.get("unknown_capacity_samples", 0) > 0
               or any(pair.get("same_boot_verified") is not True
                      for pair in comparisons))
    tasks = []
    if failed and stressed:
        tasks.append((
            "P0 · gefaalde services bij VPS-druk",
            "Herhaald als failed geobserveerde runners en hostdruk vereisen "
            "eerst eigenaarbevestigde RAM/swap/disk-capaciteit en oorzaakonderzoek; "
            "geen automatische herstart of extra jobs.",
            "WACHT OP EIGENAAR",
        ))
    elif failed:
        tasks.append((
            "P1 · gefaalde runner-service",
            "Laat de bevoegde runtime-eigenaar systemd-fout en exacte "
            "GitHub-runnerlabel/toewijzing onafhankelijk vaststellen.",
            "NIET HERSTELD BEWEZEN",
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
            "Vraag volledige actuele owner-private bronmomenten en "
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
                    "systemd-status failed (geen aantal afzonderlijke crashes).</li>")
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
        "servicelijsten · " + str(summary["unknown_capacity_samples"])
        + " onvolledige capaciteitsmetingen</p>"
        "<p><strong>Druk waargenomen:</strong> hoge swap "
        + str(summary["swap_high_samples"]) + "× · weinig rootruimte "
        + str(summary["root_high_samples"]) + "× · hoge CPU-load "
        + str(summary["load_high_samples"]) + "×. "
        "Dit zijn meetmomenten, geen minuten onder druk.</p>"
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
        }
        for item in capacity.get("metrics", [])[:4]:
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


def save_private(path, markup):
    target = Path(path)
    if not target.name or target.name in {".", ".."} or target.suffix != ".html":
        raise ValueError("Choose a new .html report filename")
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


def main():
    parser = argparse.ArgumentParser(description="Private one-shot zSentinel runner incident workbench")
    parser.add_argument("--output", required=True, help="NEW private 0600 .html report (never overwritten)")
    parser.add_argument("--previous", help="Optional owner-private T0 JSON, same-host and <=72 hours")
    parser.add_argument("--t0", help="Offline replay: first owner-private JSON receipt")
    parser.add_argument("--t1", help="Offline replay: later owner-private JSON receipt")
    parser.add_argument("--timeline", nargs="+", metavar="PRIVATE_JSON",
                        help="Offline historical view of 3–12 ordered private receipts")
    args = parser.parse_args()
    if (bool(args.t0) != bool(args.t1) or
            sum(bool(x) for x in (args.previous, args.t0, args.timeline)) > 1):
        parser.error("Choose one: --t0 and --t1, --previous, or --timeline")
    if args.timeline and not 3 <= len(args.timeline) <= 12:
        parser.error("--timeline requires 3–12 chronological private receipts")
    try:
        if args.timeline:
            data = offline_timeline_report(args.timeline)
        elif args.t0:
            data = offline_comparison_report(args.t0, args.t1)
        else:
            data = collect()
            if args.previous:
                # Import only for opt-in private previous-receipt comparison.
                from zsentinel_runner_snapshot import packet, changes, load_private
                current = packet(data)
                data["comparison"] = changes(load_private(args.previous), current)
        markup = render(data)
        save_private(args.output, markup)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        # Do not echo report paths, unit command lines or operator-private details.
        parser.exit(1, "Kon geen privé rapport maken; controleer veilige doelmap en rechten.\n")
    print("Privé herstelwerkbank gemaakt (0600, zonder live wijziging).")


if __name__ == "__main__":
    main()
