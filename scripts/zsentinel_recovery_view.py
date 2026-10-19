#!/usr/bin/env python3
"""zSentinel offline operator cockpit from an already validated routing decision.

Only prints a private, self-contained HTML document. Never fetches sources,
trusts unvalidated markup, performs workflow actions or asserts live recovery.
"""
from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

MAX_INCIDENTS = 200
MAX_ISSUES = 8
MAX_TASKS = 5

CSS = """
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif;background:#0c1422;color:#edf4ff}
*{box-sizing:border-box}body{max-width:1200px;margin:auto;padding:clamp(1rem,3vw,2.5rem)}
header{padding:1.2rem 0 1.6rem}h1{font-size:clamp(1.6rem,4vw,2.4rem);margin:.2rem 0}
h2{font-size:1.25rem}h3{font-size:1.05rem;margin:.3rem 0}
p,li{line-height:1.5}.muted{color:#b6c7d9}.grid{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.panel{border:1px solid #40516a;background:#172538;border-radius:12px;padding:1.1rem;margin:.8rem 0}
.panel.warn{border-left:5px solid #ffbd6a}.panel.read{border-left:5px solid #7cbeeb}
.tag{display:inline-block;font-size:.72rem;font-weight:700;border-radius:30px;border:1px solid #a3b4c9;padding:.2rem .55rem;margin-right:.4rem}
.word{overflow-wrap:anywhere}small,.small{font-size:.85rem}a{color:#bcebf6}a:focus-visible{outline:3px solid #c5f285;outline-offset:3px}
.row{margin:.6rem 0}.row strong{color:#f7faff}.empty{border:1px dashed #58657a;border-radius:12px;padding:1rem}
.banner{background:#302720;border-left:4px solid #ffc078;padding:.8rem 1rem;border-radius:6px}
ul{padding-left:1.2rem}footer{margin:2rem 0;border-top:1px solid #44556b;padding-top:1rem}
"""


def safe_link(value):
    if not isinstance(value, str) or len(value) > 220:
        return None
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query
            or parsed.fragment or parsed.username or parsed.password):
        return None
    bits = parsed.path.strip("/").split("/")
    if len(bits) != 4 or bits[2] not in ("pull", "issues"):
        return None
    if not bits[3].isdigit() or not 0 < int(bits[3]) < 10**9:
        return None
    if any(not part.replace("-", "").replace("_", "").replace(".", "").isalnum()
           for part in bits[:2]):
        return None
    return value


def phrase(value, max_chars=600):
    if not isinstance(value, str):
        return "Unknown"
    cleaned = " ".join(value.split())
    return escape(cleaned[:max_chars]) if cleaned else "Unknown"


def linked_item(url, title):
    valid = safe_link(url)
    label = phrase(title, 180)
    if valid is None:
        return label
    return '<a href="' + escape(valid, quote=True) + '" target="_blank" rel="noopener noreferrer">' + label + "</a>"


def incident_card(item):
    priority = phrase(item.get("severity"), 5)
    project = phrase(item.get("project_id"), 80)
    owner = phrase(item.get("owner"), 80)
    due = phrase(item.get("target_review_by"), 35)
    action = phrase(item.get("action"), 480)
    status = phrase(item.get("status"), 50)
    problem = phrase(item.get("problem"), 120)
    return (f'<article class="panel warn"><span class="tag">{priority}</span>'
            f'<span class="tag">{project}</span>'
            f'<h3 class="word">Problem · {problem}</h3>'
            f'<p class="row"><strong>Actie ({owner})</strong><br>{action}</p>'
            f'<p class="row"><strong>Status:</strong> {status}<br>'
            f'<span class="muted small">Herbeoordeling voor {due}; geen automatische retry.</span></p></article>')


def issue_card(item):
    project = phrase(item.get("project_id"), 80)
    pri = phrase(item.get("priority"), 5)
    linked = item.get("open_pr_candidates") or []
    links = " · ".join(linked_item(url, "Open PR") for url in linked[:3])
    return (f'<article class="panel read"><span class="tag">{pri}</span>'
            f'<span class="tag">{project}</span>'
            f'<h3 class="word">{linked_item(item.get("issue_url"), item.get("title"))}</h3>'
            f'<p class="small">{phrase(item.get("action"), 200)}</p>'
            + (f'<p class="small">Gerelateerde PR-kandidaten: {links}</p>' if links else '')
            + '<p class="muted small">Actieve eigenaar niet geverifieerd; geen claim.</p></article>')


def task_card(item):
    paths = ", ".join(phrase(p, 160) for p in (item.get("paths") or [])[:25])
    return (f'<article class="panel"><span class="tag">{phrase(item.get("priority"), 5)}</span>'
            f'<h3>{phrase(item.get("project_id"), 80)} / {phrase(item.get("id"), 80)}</h3>'
            f'<p class="muted small word">Scope: {paths}</p>'
            '<p>Alleen kandidaat. Herbeoordeel live claims en vereiste gates vóór wijzigingen.</p></article>')


def html_view(decision):
    if not isinstance(decision, dict) or decision.get("schema_version") != 1:
        raise ValueError("not a canonical zSentinel decision")
    incidents = decision.get("incidents")
    tasks = decision.get("independent_task_suggestions")
    triage = decision.get("open_product_issue_triage")
    if (not isinstance(incidents, list) or len(incidents) > MAX_INCIDENTS
            or not isinstance(tasks, list) or len(tasks) > MAX_TASKS
            or not isinstance(triage, dict) or not isinstance(triage.get("issues"), list)
            or len(triage["issues"]) > MAX_ISSUES):
        raise ValueError("routing decision is malformed or unbounded")
    for record in incidents + tasks + triage["issues"]:
        if not isinstance(record, dict):
            raise ValueError("routing card must be an object")
    if (decision.get("mutations_performed") is not False
            or decision.get("queue_claims_changed") is not False
            or decision.get("deployment_authorized") is not False):
        raise ValueError("operator cockpit refuses authorizing/mutating report")
    source = decision.get("live_github_read")
    coverage = (source.get("github_coverage") if isinstance(source, dict)
                else "not refreshed")
    coverage = phrase(coverage, 60)
    claims = decision.get("claim_inventory_complete") is True
    source_issues = phrase(triage.get("coverage"), 40)
    watch = decision.get("watch_alert_candidates", [])
    if not isinstance(watch, list) or len(watch) > 5:
        raise ValueError("unbounded watch candidate feed")
    missing = decision.get("unresolved_not_observed", 0)
    unattributed = decision.get("unattributed_progress_or_jobs", 0)
    if type(unattributed) is not int or unattributed < 0:
        raise ValueError("invalid unattributed evidence count")
    reconciled = decision.get("reconciled_exact_jobs", [])
    if not isinstance(reconciled, list) or len(reconciled) > MAX_INCIDENTS:
        raise ValueError("invalid exact-job reconciliations")
    if type(missing) is not int or missing < 0:
        raise ValueError("invalid unresolved incident count")
    def section(cards, empty):
        return '<div class="grid">' + ("".join(cards) if cards else '<p class="empty">' + empty + "</p>") + "</div>"
    # Urgent first; large portfolios keep the first five visible and make
    # remaining cards available under a native details disclosure (no JS).
    ordered_incidents = sorted(incidents, key=lambda i: (
        0 if i.get("severity") == "P0" else 1,
        str(i.get("target_review_by", "")),
        str(i.get("project_id", "")),
    ))
    primary_incidents = [incident_card(row) for row in ordered_incidents[:5]]
    overflow_incidents = [incident_card(row) for row in ordered_incidents[5:]]
    extra_incidents = (
        '<details><summary>Overige ' + str(len(overflow_incidents)) +
        ' incidenten bekijken</summary>' + section(overflow_incidents, "") + '</details>'
        if overflow_incidents else ""
    )
    warning = ("De bronnen zijn deels onbekend of niet actueel; afwezig betekent niet opgelost. "
               "Geen aanbeveling is een autorisatie voor claims, merges, financiële actie of deploy.")
    return ('<!doctype html><html lang="nl"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="referrer" content="no-referrer">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'; '
            'connect-src \'none\'; img-src \'none\'">'
            '<title>zSentinel · Herstelcockpit</title><style>' + CSS + '</style></head><body>'
            '<header><span class="tag">ZCLOUD · LOKAAL</span><h1>zSentinel herstelcockpit</h1>'
            f'<p class="muted">Bronobservatie: {phrase(decision.get("observed_at_utc"), 50)}</p>'
            f'<p>GitHub: {coverage} · GitHub-issues: {source_issues} · '
            f'Actieve claims: {"volledig expliciet" if claims else "ONVOLLEDIG"} · '
            f'Menselijke horloge-alertkandidaten: {len(watch)} (niet verstuurd)</p>'
            f'<p class="banner">{phrase(warning, 600)}</p></header><main>'
            f'<section><h2>Actuele blokkades ({len(incidents)})</h2>'
            + section(primary_incidents,
                      "Geen vers geverifieerde blokkades. Dat bewijst geen herstel.")
            + extra_incidents + '</section>'
            + f'<section><h2>Open P0/P1-productissues ({len(triage["issues"])})</h2>'
            + section([issue_card(row) for row in triage["issues"]],
                      "Geen issuewaarnemingen beschikbaar. Raadpleeg de brondekking.")
            + '</section>'
            + f'<section><h2>Onafhankelijke werkvoorstellen ({len(tasks)})</h2>'
            + section([task_card(row) for row in tasks],
                      "Geen claim- en gate-veilig onafhankelijk werk aangetoond.")
            + '</section>'
            + f'<p class="muted">Niet meer waargenomen, nog te reconciliëren incidenten: {missing}. '
              f'Exact succesvol afgeronde GitHub-jobs (geen bewijs van algeheel herstel): '
              f'{len(reconciled)}. Zonder geverifieerde claim/job-identiteit: '
              f'{unattributed}.</p>'
            + '</main><footer class="muted">Offline HTML, geen netwerk, geen scripts, '
            'geen worker-, runner-, queue-, claim-, CI-, of VPS-mutaties. '
            'Huidige bronweergave is geen bewijs van productieherstel.</footer></body></html>')


__all__ = ["html_view"]
