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
table{width:100%;border-collapse:collapse;margin:1rem 0}td,th{padding:.55rem;text-align:left;border-bottom:1px solid #40516a;white-space:nowrap}.wrap{overflow-x:auto}details{padding:.6rem 0}summary{cursor:pointer}.banner{background:#302720;border-left:4px solid #ffc078;padding:.8rem 1rem;border-radius:6px}
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


def queue_card(item):
    """A source-bound queue observation is not a safe automatic retry."""
    urgent = item["owner_lease_expired_observed"]
    kind = "warn" if urgent else "read"
    owner = ("Mogelijk verlopen owner-lease; beheerder moet status beoordelen."
             if urgent else "Meerdere claimpogingen; niet hetzelfde als mislukte pogingen.")
    return (
        f'<article class="panel {kind}">'
        f'<span class="tag">{phrase(item["priority"], 5)}</span>'
        f'<span class="tag">{phrase(item["project_id"], 80)}</span>'
        f'<h3 class="word">{phrase(item["title"], 180)}</h3>'
        f'<p>{phrase(owner, 250)}</p>'
        f'<p class="muted small">Status: {phrase(item["status"], 20)} · '
        f'Claimpogingen: {item["claim_attempts"]} · '
        f'Veilige referentie: {phrase(item["queue_ref"], 16)}</p>'
        f'<p class="small">Actie: {phrase(item["action"], 250)}</p>'
        '</article>'
    )


def queue_group_table(groups, truncated, sample_complete):
    if not groups:
        return ""
    rows=[]
    for row in groups:
        items = [phrase(row["project_id"],80), phrase(row["priority"],5)]
        for name in ("queued","claimed","running","verifying",
                     "high_claim_attempts","expired_owner_lease_rows"):
            items.append(str(row[name]))
        rows.append("<tr>" + "".join("<td>" + cell + "</td>" for cell in items) + "</tr>")
    suffix = (" · meer groepen niet weergegeven" if truncated else "")
    sample = (" · risicovoorbeelden onvolledig" if not sample_complete else "")
    return ('<details><summary>Wachtrij per project en prioriteit (' +
            str(len(groups)) + ')' + suffix + sample + '</summary><div class="wrap">'
            '<table><thead><tr><th>Project</th><th>Prioriteit</th><th>Wachtend</th>'
            '<th>Geclaimd</th><th>Running</th><th>Verifying</th>'
            '<th>Veel claims</th><th>Lease verlopen?</th></tr></thead><tbody>' +
            "".join(rows) + '</tbody></table></div></details>')


def html_view(decision):
    if not isinstance(decision, dict) or decision.get("schema_version") != 1:
        raise ValueError("not a canonical zSentinel decision")
    incidents = decision.get("incidents")
    tasks = decision.get("independent_task_suggestions")
    triage = decision.get("open_product_issue_triage")
    queue = decision.get("live_queue_pressure")
    if (not isinstance(queue, dict) or queue.get("coverage") not in
            ("not_supplied", "current", "stale", "incomplete")
            or not isinstance(queue.get("totals"), dict)
            or not isinstance(queue.get("samples"), list)
            or len(queue["samples"]) > 20
            or queue.get("claim_ownership_verified") is not False):
        raise ValueError("untrusted queue pressure evidence")
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
    live = decision.get("live_sources")
    if live is not None and (not isinstance(live, dict)
            or live.get("notion") not in (
                    "not_observed_no_ownership_claim",
                    "claim_status_observed_no_file_ownership",
                    "degraded_unavailable_no_claim_authority")
            or live.get("runtime") != "not_observed_no_worker_recovery_claim"
            or live.get("current_claim_inventory") != "unknown_and_not_used_for_routing"):
        raise ValueError("invalid live source authority disclosure")
    # Explain *why* a queued job was classified as a self-hosted route,
    # while retaining explicit unknown for degraded, missing or hosted labels.
    runner_route_cards = []
    job_source = live.get("github_jobs") if isinstance(live, dict) else None
    route_rows = job_source.get("refreshed_projects") if isinstance(job_source, dict) else None
    if route_rows is not None:
        if not isinstance(route_rows, list) or len(route_rows) > 8:
            raise ValueError("runner label evidence outside bounded GitHub refresh")
        for row in route_rows:
            if not isinstance(row, dict) or row.get("status") not in ("refreshed", "degraded"):
                raise ValueError("invalid route evidence")
            if row["status"] == "degraded":
                runner_route_cards.append(
                    '<article class="panel warn"><h3>' + phrase(row.get("project_id"), 80) +
                    ' · bron onbereikbaar</h3><p>De exacte joblabels en runnerroute zijn '
                    'ONBEKEND; geen diagnose of runnerherstart toegestaan.</p></article>'
                )
                continue
            if (row.get("source") != "github_exact_run_job"
                    or row.get("may_restart_runner") is not False
                    or row.get("runner_route") not in ("self_hosted", "hosted_or_custom", "unknown")
                    or not isinstance(row.get("route_labels"), list)
                    or len(row["route_labels"]) > 16
                    or any(not isinstance(v, str) or len(v) > 64
                           for v in row["route_labels"])):
                raise ValueError("invalid exact job route receipt")
            repository = row.get("repository")
            run_id, job_id = row.get("run_id"), row.get("job_id")
            if (not isinstance(repository, str) or not repository.startswith("Zennay/")
                    or repository.count("/") != 1 or
                    any(not c.isascii() or not (c.isalnum() or c in "._-")
                        for c in repository.split("/")[1])
                    or type(run_id) is not int or type(job_id) is not int
                    or not 0 < run_id <= 2**53-1 or not 0 < job_id <= 2**53-1):
                raise ValueError("invalid exact GitHub job route identity")
            href = ("https://github.com/" + repository + "/actions/runs/" +
                    str(run_id) + "/job/" + str(job_id))
            route_description = {
                "self_hosted": "Self-hosted jobroute bevestigd; oningedeelde queue kan runner/label-toegang vereisen.",
                "hosted_or_custom": "Geen self-hosted-label op deze job; geen bewijs van VPS-runnerstoring.",
                "unknown": "Vereiste runnerlabels onbekend; geen bewijs van VPS-runnerstoring.",
            }[row["runner_route"]]
            runner_route_cards.append(
                '<article class="panel read"><h3><a target="_blank" rel="noopener noreferrer" '
                'href="' + escape(href, quote=True) + '">' + phrase(row.get("project_id"), 80) +
                ' · exacte GitHub-job</a></h3><p>' +
                phrase(route_description, 180) + '</p><p class="small">Vereiste labels: ' +
                (", ".join(phrase(v, 64) for v in row["route_labels"])
                 if row["route_labels"] else "ONBEKEND") +
                ' · alleen read-only bronbewijs, geen runnerstartrecht.</p></article>'
            )
    runner_route_panel = ('<section><h2>GitHub-jobroute · exacte runnerlabels</h2>' +
                          ('<div class="grid">' + ''.join(runner_route_cards) + '</div>'
                           if runner_route_cards else
                           '<p class="empty">Geen exacte runnerlabels uitgelezen; '
                           'runnerstatus niet geverifieerd.</p>') + '</section>')
    notion_mode = live.get("notion") if isinstance(live, dict) else None
    if notion_mode == "claim_status_observed_no_file_ownership":
        provenance = ("Notion actieve werkstatus is alleen read-only geobserveerd. "
                      "Bestandsscope, lease en worker zijn NIET geverifieerd; geen automatische taakclaim.")
    elif notion_mode == "degraded_unavailable_no_claim_authority":
        provenance = ("Notion claimstatus kon niet worden gelezen; nul actieve claims "
                      "mag hier niet uit worden afgeleid. Geen automatische taakclaim.")
    elif live is not None:
        provenance = ("Notion en runtime zijn niet uitgelezen; actieve eigenaren zijn onbekend. "
                      "Dit dashboard biedt observatie, geen automatische taakclaim.")
    else:
        provenance = ("Dit dashboard is gebaseerd op een apart aangeleverd bronpakket; "
                      "claims en runtimeherstel moeten onafhankelijk worden bevestigd.")
    notion = decision.get("notion_claims_observation")
    notion_panel = '<p class="empty">Niet opgevraagd; actuele eigendom onbekend.</p>'
    if notion is not None:
        if (not isinstance(notion, dict) or notion.get("source") != "notion_readonly_data_source_pages"
                or notion.get("coverage") not in ("current_pages_only", "incomplete")
                or notion.get("claim_scope_verified") is not False
                or notion.get("task_inventory_verified") is not False
                or notion.get("can_dispatch") is not False
                or notion.get("can_release_claim") is not False
                or type(notion.get("active_count")) is not int
                or not 0 <= notion["active_count"] <= 400
                or not isinstance(notion.get("items"), list)
                or len(notion["items"]) > 30
                or type(notion.get("items_truncated")) is not bool):
            raise ValueError("invalid Notion claim visibility snapshot")
        rows = []
        for item in notion["items"]:
            if (not isinstance(item, dict) or item.get("status") not in ("Claimed", "Running")):
                raise ValueError("invalid Notion active claim")
            rows.append('<li class="word"><strong>' + phrase(item.get("work"), 35) +
                        ' · ' + phrase(item.get("status"), 12) +
                        ' · ' + phrase(item.get("project"), 80) +
                        '</strong>: ' + phrase(item.get("title"), 120) + '</li>')
        caveat = ("Onvolledige Notion-paginering" if notion["coverage"] == "incomplete"
                  else "Notion werkstatuspagina's opgevraagd")
        notion_panel = (
            '<p>' + phrase(caveat, 80) + ' · actieve regels: ' +
            str(notion["active_count"]) + ' · geen bestandsscope, claimvrijgave of dispatch.</p>' +
            ('<ul>' + ''.join(rows) + '</ul>' if rows else
             '<p class="empty">Geen actieve regels in deze waarneming; geen bewijs van vrije scope.</p>') +
            ('<p>Voorbeelden afgekapt na 30.</p>' if notion["items_truncated"] else '')
        )
    elif notion_mode == "degraded_unavailable_no_claim_authority":
        notion_panel = '<p class="empty">Notion-bron niet bereikbaar; status onbekend (niet nul).</p>'
    queue_coverage = phrase(queue["coverage"], 30)
    queue_totals = queue["totals"]
    queue_cards = [queue_card(item) for item in queue["samples"]]
    queue_groups = queue.get("project_groups", [])
    if (not isinstance(queue_groups,list) or len(queue_groups)>18
            or type(queue.get("project_groups_truncated")) is not bool
            or type(queue.get("sample_is_complete")) is not bool):
        raise ValueError("invalid queue pressure detail contract")
    group_html = queue_group_table(queue_groups, queue["project_groups_truncated"],
                                   queue["sample_is_complete"])
    queue_summary = (
        "Wachtend: " + str(queue_totals.get("queued_items", 0)) +
        " · Actief: " + str(queue_totals.get("owned_items", 0)) +
        " · Veel claims: " + str(queue_totals.get("high_claim_attempt_items", 0)) +
        " · Mogelijk verlopen leases: " +
        str(queue_totals.get("expired_owner_lease_rows", 0))
        if queue_coverage == "current" else
        "Bron ontbreekt, is onvolledig of verouderd: geen betrouwbare actuele wachtrijclaim."
    )
    capacity = decision.get("vps_resource_admission")
    if (not isinstance(capacity, dict)
            or capacity.get("state") not in
            ("unavailable", "stale", "overloaded", "advisory_headroom")):
        raise ValueError("unverified VPS capacity summary")
    vps_state = phrase(capacity["state"], 32)
    vps_reason = phrase(capacity.get("reason"), 100)
    vps_origin = (live.get("vps_capacity", "unknown_not_sampled")
                  if isinstance(live, dict) else "external_observation")
    if vps_origin not in ("unknown_not_sampled", "same_host_readonly_opt_in",
                          "external_observation"):
        raise ValueError("invalid VPS source label")
    vps_disclosure = (
        "Expliciet lokaal op deze Linux-host gemeten; alleen VPS-evidence als dit de bedoelde VPS is."
        if vps_origin == "same_host_readonly_opt_in" else
        "Geen lokale VPS-meting." if vps_origin == "unknown_not_sampled"
        else "Capaciteit komt uit apart aangeleverd bronbewijs."
    )
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
            f'<p>VPS-capaciteitsadvies: <strong>{vps_state}</strong> · {vps_reason}. '
            f'{phrase(vps_disclosure, 180)} Geen VPS-startrecht.</p>'
            f'<p class="banner">{phrase(provenance, 420)} {phrase(warning, 600)}</p></header><main>'
            f'<section><h2>Actuele blokkades ({len(incidents)})</h2>'
            + section(primary_incidents,
                      "Geen vers geverifieerde blokkades. Dat bewijst geen herstel.")
            + extra_incidents + '</section>'
            + runner_route_panel
            + f'<section><h2>Open P0/P1-productissues ({len(triage["issues"])})</h2>'
            + section([issue_card(row) for row in triage["issues"]],
                      "Geen issuewaarnemingen beschikbaar. Raadpleeg de brondekking.")
            + '</section>'
            + '<section><h2>Notion · actieve werkstatus (geen eigendomsbewijs)</h2>'
            + notion_panel + '</section>'
            + '<section><h2>Werkelijke wachtrijdruk · ' + queue_coverage + '</h2>'
            + '<p class="muted">' + phrase(queue_summary, 300) + '</p>'
            + (group_html if queue_coverage == "current" else "")
            + section(queue_cards if queue_coverage == "current" else [],
                      "Geen actuele individuele wachtrijincidenten. Claimherstel blijft handmatig geautoriseerd.")
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
