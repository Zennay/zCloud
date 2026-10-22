#!/usr/bin/env python3
"""zSentinel offline operator cockpit from an already validated routing decision.

Only prints a private, self-contained HTML document. Never fetches sources,
trusts unvalidated markup, performs workflow actions or asserts live recovery.
"""
from __future__ import annotations

import math
import re
from html import escape
from urllib.parse import urlsplit
from zsentinel_selection_holds import html_panel as selection_hold_panel

MAX_INCIDENTS = 200
MAX_ISSUES = 8
MAX_TASKS = 5

INCIDENT_STATUS_LABELS_NL = {
    "human_action_required": "Menselijke actie vereist",
    "read_only_diagnosis_required": "Broninspectie nodig",
    "not_observed_needs_reconciliation": "Niet opnieuw waargenomen · reconciliatie nodig",
    "exact_job_completed_source_reconciled": "Exacte jobbron opnieuw waargenomen",
    "exact_job_route_reclassified_not_runner_recovered":
        "Jobroute heringedeeld · runnerherstel niet bewezen",
}

INCIDENT_PROBLEM_LABELS_NL = {
    "github_job_unstarted": "GitHub-job niet gestart · oorzaak nog onbekend",
    "github_job_startup_failure": "GitHub-job kon niet starten · runner/bootstrap onderzoeken",
    "github_job_stale_conclusion": "Jobconclusie verouderd · actuele HEAD opnieuw lezen",
    "github_job_neutral_or_skipped": "Job neutraal/overgeslagen · geen groen bewijs",
    "github_job_cancelled": "GitHub-job geannuleerd · reden onbekend",
    "github_workflow_failed": "GitHub-job mislukt · oorzaak nog niet bevestigd",
    "github_job_unassigned": "GitHub-job zonder runner-toewijzing",
    "github_job_pending_or_waiting": "Job wacht · geen runnerstoring bewezen",
    "github_hosted_billing_admission": "GitHub-hosted toelating vereist account-eigenaar",
    "github_required_owner_approval": "GitHub-ownergoedkeuring vereist",
    "stale_github_observation": "GitHub-waarneming verlopen · bron vernieuwen",
    "ci_green_runtime_not_verified": "CI groen · runtimeherstel nog niet bewezen",
    "runtime_progress_missing": "Runtimevoortgang ontbreekt · claim en heartbeat controleren",
    "active_owner_or_serialized_release": "Actieve eigenaar/releasegate · niet overnemen",
    "explicit_human_gate": "Expliciete menselijke gate · niet omzeilen",
    "notion_project_posture": "Project bewust gepauzeerd",
    "scheduled_wait_not_elapsed": "Wachttijd nog niet verstreken",
    "insufficient_progress_evidence": "Onvoldoende verse voortgangsgegevens",
}

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


def incident_card(item, registry=None):
    priority = phrase(item.get("severity"), 5)
    project = phrase(item.get("project_id"), 80)
    owner = phrase(item.get("owner"), 80)
    due = phrase(item.get("target_review_by"), 35)
    action = phrase(item.get("action"), 480)
    raw_status = item.get("status")
    status = phrase(
        INCIDENT_STATUS_LABELS_NL.get(raw_status, raw_status)
        if isinstance(raw_status, str) else raw_status, 50,
    )
    reason = item.get("problem")
    # A displayed reason is an operator hint, never proof of recovery, billing
    # denial or a broken VPS listener. Prefer clear Dutch labels for known routes.
    label = (INCIDENT_PROBLEM_LABELS_NL.get(reason, reason)
             if isinstance(reason, str) else reason)
    problem = phrase(label, 120)
    # Derive provenance links only from the canonical project registry and
    # a fresh exact run/job/SHA receipt; never link unpinned incidents.
    from zsentinel_runtime_handoff import _attach_verified_job
    source = _attach_verified_job(
        {"project": item.get("project_id")},
        item.get("exact_github_job"), registry, None)
    exact = source.get("exact_github_job")
    exact_html = ""
    if exact is not None:
        exact_html = ('<p class="muted small word">Exacte bron · run ' +
                      str(exact["run_id"]) + ' · job ' + str(exact["job_id"]) +
                      ' · HEAD ' + escape(exact["head_sha"]) + '</p>')
        for key, label in (("exact_github_job_url", "Bekijk exacte job"),
                           ("exact_github_commit_url", "Bekijk exacte commit")):
            url = source.get(key)
            if url:
                exact_html += (' <a class="small" href="' + escape(url, quote=True) +
                               '" target="_blank" rel="noopener noreferrer">' +
                               label + '</a>')
    return (f'<article class="panel warn"><span class="tag">{priority}</span>'
            f'<span class="tag">{project}</span>'
            f'<h3 class="word">Probleem · {problem}</h3>'
            f'<p class="row"><strong>Actie ({owner})</strong><br>{action}</p>'
            f'<p class="row"><strong>Status:</strong> {status}<br>'
            f'<span class="muted small">Herbeoordeling voor {due}; geen automatische retry.</span></p>'
            + exact_html + '</article>')


def historical_card(item, registry=None):
    """Historically unresolved only: deliberately distinct from fresh cards."""
    if (item.get("source_status") != "not_observed_needs_reconciliation"
            or item.get("current_source_observed") is not False
            or item.get("mutation_authorized") is not False):
        raise ValueError("untrusted historical incident authority")
    severity = phrase(item.get("severity"), 5)
    project = phrase(item.get("project_id"), 80)
    raw_reason = item.get("problem")
    reason_label = (INCIDENT_PROBLEM_LABELS_NL.get(raw_reason, raw_reason)
                    if isinstance(raw_reason, str) else raw_reason)
    reason = phrase(reason_label, 120)
    last = phrase(item.get("last_seen"), 45)
    age = item.get("minutes_since_last_observed")
    if type(age) is not int or age < 0:
        raise ValueError("invalid historical age")
    exact = item.get("exact_github_job")
    if exact is None:
        source = "Exacte bronidentiteit onbekend; overleg eerst met bevoegde eigenaar."
    elif (type(exact) is not dict or type(exact.get("run_id")) is not int
          or type(exact.get("job_id")) is not int
          or not isinstance(exact.get("head_sha"), str)
          or len(exact["head_sha"]) != 40):
        raise ValueError("invalid exact historical job reference")
    else:
        if (exact.get("project_id") != item.get("project_id")
                or type(exact.get("run_id")) is not int
                or type(exact.get("job_id")) is not int
                or not 1 <= exact["run_id"] <= 2**53-1
                or not 1 <= exact["job_id"] <= 2**53-1):
            raise ValueError("historical job project or identity mismatch")
        from zsentinel_runtime_handoff import _attach_verified_job, SAFE_SHA
        if not SAFE_SHA.fullmatch(exact["head_sha"]):
            raise ValueError("invalid historical exact SHA")
        source = (f"GitHub run {exact['run_id']} · job {exact['job_id']} · "
                  f"SHA {phrase(exact['head_sha'], 40)}")
        # Historical link is always a link to the OLD exact job, not to a
        # claimed current recovery. Missing project mapping stays unlinked.
        mapped_repo = (registry.get(item["project_id"])
                       if isinstance(registry, dict) else None)
        if isinstance(mapped_repo, str):
            pinned = _attach_verified_job(
                {"project": item["project_id"]},
                {key: exact[key] for key in ("run_id", "job_id", "head_sha")},
                registry, None)
            for field, title in (
                    ("exact_github_job_url", "Bekijk historische job"),
                    ("exact_github_commit_url", "Bekijk historische commit")):
                url = pinned.get(field)
                if url:
                    source += (' <a href="' + escape(url, quote=True) +
                               '" target="_blank" rel="noopener noreferrer">' +
                               title + '</a>')
    return (f'<article class="panel"><span class="tag">HISTORISCH · GEEN ACTUELE BRON</span>'
            f'<span class="tag">{severity}</span><span class="tag">{project}</span>'
            f'<h3 class="word">{reason}</h3>'
            f'<p>Laatste waarneming: {last} · {age} minuten geleden.</p>'
            f'<p>{source}</p><p>Actie: exacte bron/owner handmatig verifiëren. '
            f'Geen automatische herstelclaim, retry, merge of worker-claim.</p></article>')


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
    unknown_lease = item["owner_lease_unverifiable_observed"]
    impossible_lease = item["owner_lease_window_inconsistent_observed"]
    kind = "warn" if urgent or unknown_lease or impossible_lease else "read"
    owner = ("Claimstart/vervaltermijn inconsistent: controleer beide tijden bij de huidige eigenaar."
             if impossible_lease else
             "Mogelijk verlopen owner-lease; beheerder moet status beoordelen."
             if urgent else
             "Leaseklok ontbreekt of is ongeldig; eigenaar moet de bezette claim beoordelen."
             if unknown_lease else
             "Meerdere claimpogingen; niet hetzelfde als mislukte pogingen.")
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
                     "high_claim_attempts","expired_owner_lease_rows",
                      "unverifiable_owner_lease_rows",
                      "inconsistent_owner_lease_window_rows"):
            items.append(str(row[name]))
        rows.append("<tr>" + "".join("<td>" + cell + "</td>" for cell in items) + "</tr>")
    suffix = (" · meer groepen niet weergegeven" if truncated else "")
    sample = (" · risicovoorbeelden onvolledig" if not sample_complete else "")
    return ('<details><summary>Wachtrij per project en prioriteit (' +
            str(len(groups)) + ')' + suffix + sample + '</summary><div class="wrap">'
            '<table><thead><tr><th>Project</th><th>Prioriteit</th><th>Wachtend</th>'
            '<th>Geclaimd</th><th>Running</th><th>Verifying</th>'
            '<th>Veel claims</th><th>Lease verlopen?</th><th>Leaseklok onbekend?</th><th>Claimvenster inconsistent?</th></tr></thead><tbody>' +
            "".join(rows) + '</tbody></table></div></details>')


def blocked_queue_panel(source):
    """Render bounded status=blocked aggregates without treating them as failures."""
    if source is None:
        source = {
            "coverage": "not_supplied", "aggregate_coverage": "not_supplied",
            "source_coverage": "not_supplied", "observed_at": None,
            "total_items_observed": None, "project_groups": [],
            "sample_truncated": True, "invalid_group_rows": None,
            "work_items": [], "work_items_coverage": "not_observed",
            "work_items_truncated": True, "invalid_work_rows": None,
            "worker_slot_source_covered": False,
            "claim_ownership_verified": False, "queue_write_authorized": False,
            "automatic_retry_authorized": False,
        }
    allowed = ("not_supplied", "not_observed", "complete", "partial", "stale")
    if (type(source) is not dict or source.get("coverage") not in allowed
            or source.get("aggregate_coverage") not in
               ("not_supplied", "not_observed", "complete", "partial")
            or source.get("source_coverage") not in
               ("not_supplied", "current", "stale", "incomplete")
            or type(source.get("claim_ownership_verified")) is not bool
            or source["claim_ownership_verified"] is not False
            or type(source.get("queue_write_authorized")) is not bool
            or source["queue_write_authorized"] is not False
            or type(source.get("automatic_retry_authorized")) is not bool
            or source["automatic_retry_authorized"] is not False
            or type(source.get("sample_truncated")) is not bool
            or type(source.get("project_groups")) is not list
            or len(source["project_groups"]) > 250):
        raise ValueError("untrusted blocked-queue panel")
    coverage = source["coverage"]
    groups = source["project_groups"]
    total = source.get("total_items_observed")
    invalid = source.get("invalid_group_rows")
    if coverage in ("not_supplied", "not_observed"):
        if total is not None or groups or source["sample_truncated"] is not True:
            raise ValueError("unknown blocked-queue source cannot imply zero")
        if coverage == "not_supplied" and source["aggregate_coverage"] != "not_supplied":
            raise ValueError("missing blocked-queue source has contradictory coverage")
        if coverage == "not_observed" and source["aggregate_coverage"] != "not_observed":
            raise ValueError("unobserved blocked-queue source has contradictory coverage")
        summary = ("Status=blocked niet waargenomen; aantal ONBEKEND, niet nul."
                   if coverage == "not_supplied" else
                   "De wachtrijbron is niet gelezen; status=blocked blijft ONBEKEND, niet nul.")
    else:
        if (type(total) is not int or not 0 <= total <= 10**9
                or type(invalid) is not int or not 0 <= invalid <= 251
                or source.get("observed_at") is None
                or not isinstance(source.get("observed_at"), str)
                or len(source["observed_at"]) > 45):
            raise ValueError("invalid blocked-queue count or source moment")
        identities = set()
        group_sum = 0
        for row in groups:
            if (type(row) is not dict
                    or type(row.get("project_id")) is not str
                    or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", row["project_id"])
                    or row.get("priority") not in ("P0", "P1", "P2", "P3")
                    or type(row.get("items")) is not int
                    or not 1 <= row["items"] <= 10**9
                    or (row["project_id"], row["priority"]) in identities):
                raise ValueError("invalid blocked-queue project aggregate")
            identities.add((row["project_id"], row["priority"]))
            group_sum += row["items"]
        if coverage == "complete":
            if (source["aggregate_coverage"] != "complete"
                    or source["sample_truncated"] or invalid != 0 or group_sum != total
                    or source["source_coverage"] == "stale"):
                raise ValueError("blocked-queue complete coverage contradicts source")
        elif coverage == "partial":
            if (source["aggregate_coverage"] != "partial"
                    or not source["sample_truncated"] or group_sum > total):
                raise ValueError("blocked-queue partial coverage contradicts source")
        elif coverage == "stale":
            if (source["aggregate_coverage"] not in ("complete", "partial")
                    or source["source_coverage"] != "stale" or groups
                    or not source["sample_truncated"]):
                raise ValueError("stale blocked-queue source exposes current detail")
        if coverage == "stale":
            summary = ("VEROUDERD bronmoment " + source["observed_at"] +
                       " · " + str(total) +
                       " status=blocked-rijen toen waargenomen; geen actuele status. "
                       "Projectdetails verborgen.")
        elif coverage == "partial":
            summary = (str(total) + " status=blocked-rijen waargenomen; "
                       "projectverdeling onvolledig, menselijke broncontrole nodig.")
        elif total == 0:
            summary = ("0 status=blocked-rijen waargenomen op " +
                       source["observed_at"] +
                       ". Dit bewijst niet dat alle productblokkades zijn opgelost.")
        else:
            summary = (str(total) + " status=blocked-rijen waargenomen op " +
                       source["observed_at"] +
                       "; alleen handmatige eigenaarbeoordeling, geen automatische retry.")
    rows = "".join(
        "<tr><td>" + phrase(row["project_id"], 80) + "</td><td>" +
        phrase(row["priority"], 5) + "</td><td>" + str(row["items"]) + "</td></tr>"
        for row in groups
    )
    table = ('<div class="wrap"><table><thead><tr><th>Project</th><th>Prioriteit</th>'
             '<th>Rijen met status blocked</th></tr></thead><tbody>' +
             rows + '</tbody></table></div>' if rows else "")
    # This is bounded read-only evidence, NOT an operator 'resume' queue.
    work = source.get("work_items", [])
    work_coverage = source.get("work_items_coverage", "not_observed")
    work_truncated = source.get("work_items_truncated", True)
    invalid_work = source.get("invalid_work_rows")
    slot_source_covered = source.get("worker_slot_source_covered", False)
    if type(slot_source_covered) is not bool:
        raise ValueError("invalid worker-slot coverage source")
    if (type(work) is not list or len(work) > 24
            or work_coverage not in ("not_observed", "complete", "partial", "stale")
            or type(work_truncated) is not bool
            or (invalid_work is not None and
                (type(invalid_work) is not int or not 0 <= invalid_work <= 24))):
        raise ValueError("invalid blocked worklist source")
    if (coverage in ("not_supplied", "not_observed", "stale")
            or work_coverage in ("not_observed", "stale")):
        if work:
            raise ValueError("unknown/stale blocked worklist cannot show current items")
    elif (work_coverage == "complete" and
          (coverage != "complete" or work_truncated or invalid_work != 0
           or len(work) != total)):
        raise ValueError("complete individual queue list contradicts aggregate")
    seen_items = set()
    work_rows = []
    for row in work:
        if type(row) is not dict:
            raise ValueError("invalid blocked task sample")
        ref, title, pid, priority = (
            row.get("queue_ref"), row.get("title"),
            row.get("project_id"), row.get("priority"))
        count = row.get("claim_attempts")
        link = row.get("canonical_github_source_link_unverified")
        if (link is not None and (
                type(link) is not str or len(link) > 220
                or re.fullmatch(
                    r"https://github[.]com/Zennay/[A-Za-z0-9_.-]{1,100}/(?:issues|pull)/[1-9][0-9]{0,8}",
                    link) is None)):
            raise ValueError("untrusted canonical blocked-source link")
        if (type(ref) is not str or re.fullmatch(r"[0-9a-f]{16}", ref) is None
                or ref in seen_items or type(pid) is not str
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", pid) is None
                or priority not in ("P0", "P1", "P2", "P3")
                or type(title) is not str or len(title) > 180
                or any(ord(ch) < 32 for ch in title)
                or type(count) is not int or not 0 <= count <= 10**9
                or type(row.get("eligible_observed")) is not bool
                or type(row.get("claim_fields_present_observed")) is not bool
                or row.get("worker_slot_source_covered", False) is not slot_source_covered
                or (slot_source_covered and
                    type(row.get("worker_slot_present_observed")) is not bool)
                or (not slot_source_covered and
                    row.get("worker_slot_present_observed") is not None)
                or (row.get("worker_slot_present_observed") is True
                    and not row["claim_fields_present_observed"])
                or row.get("resume_authorized") is not False
                or row.get("current_owner_verified") is not False
                or row.get("automatic_retry_authorized") is not False):
            raise ValueError("untrusted individual blocked queue evidence")
        seen_items.add(ref)
        link_cell = (
            '<a target="_blank" rel="noopener noreferrer" href="' +
            escape(link, quote=True) + '">Open bron (niet geverifieerd)</a>'
            if link else "Geen canonieke GitHub-bron in deze snapshot")
        work_rows.append(
            "<tr><td>" + phrase(priority, 3) + "</td><td>" +
            phrase(pid, 65) + "</td><td><code>" + phrase(ref, 16) +
            "</code></td><td>" + phrase(title, 180) + "</td><td>" +
            str(count) + "</td><td>" +
            ("JA · eigenaar controleren" if row["claim_fields_present_observed"]
             else "Geen tijdstempels/slot gezien · eigenaar onbekend"
             if slot_source_covered else "Worker-slot ONBEKEND; eigenaar controleren") +
            "</td><td>" +
            ("Bezet gezien" if row["worker_slot_present_observed"] is True
             else "Leeg in bron" if row["worker_slot_present_observed"] is False
             else "ONBEKEND") +
            "</td><td>" + link_cell + "</td><td>" +
            ("Ja, bronvlag; GEEN hervatrecht" if row["eligible_observed"]
             else "Nee, bronvlag") + "</td></tr>")
    if total is not None and len(work_rows) > total:
        raise ValueError("blocked work items exceed counted blocked rows")
    work_html = (
        '<details><summary>Geblokkeerde taken · individuele P0/P1-eerst '
        'werkvolgorde (' + str(len(work_rows)) + ' getoond)</summary>' +
        ('<p>Volledige taaksteekproef op bronmoment.' if work_coverage == "complete"
         else '<p>ONVOLLEDIGE taaksteekproef: meer taken mogelijk.'
         if work_coverage == "partial"
         else '<p>Geen actuele individuele taakbron beschikbaar.') +
        ' P0 eerst, binnen een prioriteit gespreid over projecten. '
        'De steekproef garandeert GEEN volledigheid of chronologische volgorde. '
        'Queue-id is alleen een afgeleide referentie; bronlinks uit de queue '
        'zijn NIET onafhankelijk geverifieerd. Controleer de '
        'actuele echte taak via bevoegde beheertoegang.</p>' +
        ('<div class="wrap"><table><thead><tr><th>Prioriteit</th><th>Project</th>'
         '<th>Opaak kenmerk</th><th>Taak</th><th>Claims</th>'
         '<th>Claimvelden</th><th>Worker-slot aanwezig?</th>'
         '<th>Bronverwijzing</th><th>Eligible?</th></tr></thead><tbody>' +
         "".join(work_rows) + '</tbody></table></div>' if work_rows else '') +
        '<p class="muted">Een blocked status is geen mislukte job of '
        'automatische retry. Alleen bevoegde eigenaar kan na actuele '
        'lease-/broncontrole besluiten over herstel.</p></details>'
    )
    source_note = (" · broninventaris " + phrase(source.get("source_coverage"), 20)
                   if coverage not in ("not_supplied", "not_observed") else "")
    return ('<section aria-label="Geblokkeerde wachtrij-items"><h2>Parkeerstatus · blocked</h2>'
            '<p>' + phrase(summary, 360) + source_note + '</p>' +
            table + work_html +
            '<p class="muted">Dit zijn queue-rijen met status=blocked, geen bewezen fouten of '
            'retry-opdrachten. Geen claimvrijgave, automatische retry of dispatch.</p></section>')



def capacity_observation_panel(observation, admission):
    """Render only allowlisted scalar host-capacity data; never render raw paths."""
    if observation is None:
        return ""
    if (type(observation) is not dict
            or observation.get("source") != "trusted_readonly_vps_snapshot"
            or type(observation.get("observed_at")) is not str
            or len(observation["observed_at"]) > 45):
        raise ValueError("untrusted VPS capacity observation")
    metrics = observation.get("vps")
    if type(metrics) is not dict:
        raise ValueError("VPS capacity metrics missing")

    def number(key, maximum, optional=False):
        value = metrics.get(key)
        if value is None and optional:
            return None
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not 0 <= value <= maximum):
            raise ValueError("invalid VPS capacity field: " + key)
        return value

    cores = metrics.get("cores")
    if type(cores) is not int or not 1 <= cores <= 128:
        raise ValueError("invalid VPS capacity core count")
    load = number("load_5m", 5000)
    load_per_core = number("load_5m_per_core", 5000, optional=True)
    available_memory = number("available_memory_mib", 1048576)
    total_memory = number("total_memory_mib", 1048576)
    available_memory_percent = number("available_memory_percent", 100)
    available_swap = number("available_swap_mib", 1048576)
    total_swap = number("total_swap_mib", 1048576)
    swap_used = number("swap_used_percent", 100, optional=True)
    database_free = number("free_disk_gib", 100000)
    database_unavailable = number("disk_unavailable_percent", 100)
    root_free = number("root_free_disk_gib", 100000)
    root_unavailable = number("root_disk_unavailable_percent", 100)
    root_inode_percent = number("root_inode_unavailable_percent", 100, optional=True)
    database_inode_percent = number("inode_unavailable_percent", 100, optional=True)
    if available_memory > total_memory or available_swap > total_swap:
        raise ValueError("inconsistent VPS capacity totals")
    if (total_memory <= 0 or available_memory_percent > 100 or
            (total_swap == 0 and swap_used is not None) or
            (total_swap > 0 and swap_used is None)):
        raise ValueError("inconsistent VPS memory/swap percentages")
    root_band = metrics.get("root_capacity_band")
    database_band = metrics.get("database_capacity_band")
    severity = metrics.get("capacity_severity")
    if (root_band not in ("critical", "high", "normal")
            or database_band not in ("critical", "high", "normal")
            or severity not in ("critical", "high", "normal")):
        raise ValueError("unrecognized VPS capacity severity")
    warnings = metrics.get("capacity_warnings")
    allowed_warnings = {
        "root_filesystem_critical", "root_filesystem_high",
        "database_mount_critical", "database_mount_high",
        "swap_pressure_high", "memory_pressure_high", "load_pressure_high",
        "no_verified_external_archive_headroom",
    }
    if (type(warnings) is not list or len(warnings) > 8
            or any(type(item) is not str or item not in allowed_warnings for item in warnings)
            or len(set(warnings)) != len(warnings)):
        raise ValueError("invalid VPS capacity warning list")
    archive = metrics.get("archive_target")
    if (type(archive) is not dict
            or archive.get("offload_authorized") is not False
            or archive.get("mutation_performed") is not False
            or archive.get("source_files_inspected") is not False
            or archive.get("state") not in (
                "not_requested", "unavailable", "observed_external_mount",
                "same_as_root_filesystem", "mount_point_not_verified")):
        raise ValueError("untrusted archive capacity detail")
    archive_free = archive.get("free_disk_gib")
    if archive_free is not None and (
            type(archive_free) not in (int, float) or not math.isfinite(archive_free)
            or not 0 <= archive_free <= 100000):
        raise ValueError("invalid archive capacity value")
    if (archive.get("state") == "observed_external_mount"
            and (archive.get("mount_point_detected") is not True
                 or archive.get("same_device_as_root") is not False
                 or archive_free is None)):
        raise ValueError("archive mount classification lacks capacity evidence")

    def amount(value, unit):
        return "onbekend" if value is None else f"{value:,.1f} {unit}".strip()

    def percent(value):
        return "onbekend" if value is None else f"{value:.1f}%"

    archive_states = {
        "not_requested": "niet aangevraagd",
        "unavailable": "niet beschikbaar / bron onleesbaar",
        "observed_external_mount": "externe mount waargenomen",
        "same_as_root_filesystem": "zelfde filesystem als root; niet extern",
        "mount_point_not_verified": "mountpoint niet bewezen",
    }
    archive_text = archive_states[archive["state"]]
    if archive["state"] == "observed_external_mount":
        archive_text += "; " + amount(archive_free, "GiB") + " vrij"
        archive_text += "; zelfde device als databasemount: " + (
            "ja" if archive.get("same_device_as_database") is True else
            "nee" if archive.get("same_device_as_database") is False else "onbekend"
        )
    warning_labels = {
        "root_filesystem_critical": "Systeemschijf kritiek",
        "root_filesystem_high": "Systeemschijf krap",
        "database_mount_critical": "Databasemount kritiek",
        "database_mount_high": "Databasemount krap",
        "swap_pressure_high": "Swap >=90% gebruikt",
        "memory_pressure_high": "RAM beschikbaar <=10%",
        "load_pressure_high": "Load >=2x toegestane CPU-capaciteit",
        "no_verified_external_archive_headroom": "Geen externe archief-headroom bewezen",
    }
    warning_text = " · ".join(warning_labels[item] for item in warnings) or "Geen waarschuwingscodes"
    admission_state = admission.get("state") if type(admission) is dict else "unknown"
    admission_labels = {
        "unavailable": "bron onbekend", "stale": "meting verouderd",
        "overloaded": "geen nieuw werk toelaten",
        "advisory_headroom": "drempels niet overschreden; geen startrecht",
    }
    css = "warn" if severity in ("critical", "high") else "read"
    return (
        '<section class="panel ' + css + '" aria-label="VPS capaciteitsmeting">'
        '<h2>VPS-capaciteit · read-only meting</h2>'
        '<p><strong>' + phrase(severity.upper(), 20) + '</strong> · ' +
        phrase(admission_labels.get(admission_state, "toelating onbekend"), 70) +
        ' · waargenomen ' + phrase(observation["observed_at"], 45) + '</p>'
        '<div class="wrap"><table><thead><tr><th>Onderdeel</th><th>Waarneming</th><th>Band</th></tr></thead><tbody>'
        '<tr><th>Systeemschijf</th><td>' + amount(root_free, "GiB") + ' vrij; ' +
        percent(root_unavailable) + ' niet beschikbaar; inodes ' +
        percent(root_inode_percent) + '</td><td>' +
        phrase(root_band, 12) + '</td></tr>'
        '<tr><th>Databasemount</th><td>' + amount(database_free, "GiB") + ' vrij; ' +
        percent(database_unavailable) + ' niet beschikbaar; inodes ' +
        percent(database_inode_percent) + '</td><td>' +
        phrase(database_band, 12) + '</td></tr>'
        '<tr><th>RAM</th><td>' + amount(available_memory, "MiB") + ' / ' +
        amount(total_memory, "MiB") + ' beschikbaar (' +
        percent(available_memory_percent) + ')</td><td>—</td></tr>'
        '<tr><th>Swap</th><td>' + amount(available_swap, "MiB") + ' / ' +
        amount(total_swap, "MiB") + ' vrij; ' + percent(swap_used) + ' gebruikt</td><td>—</td></tr>'
        '<tr><th>Load (5 min)</th><td>' + amount(load, "") + ' totaal; ' +
        amount(load_per_core, "per CPU") + '; ' + str(cores) + ' CPU(s)</td><td>—</td></tr>'
        '<tr><th>Archiefdoel</th><td>' + phrase(archive_text, 220) + '</td><td>Geen offloadrecht</td></tr>'
        '</tbody></table></div>'
        '<p class="muted">Waarschuwingen: ' + phrase(warning_text, 420) + '</p>'
        '<p class="muted">Alleen counters; geen bestandsnamen of inhoud. Dit paneel bewijst niet dat een bestand veilig verplaatst kan worden en verleent geen opruim-, offload-, herstart-, queue- of claimrechten.</p>'
        '</section>'
    )



def html_view(decision, registry=None):
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
    # GitHub admission is an operator-owned access/budget incident, not a
    # claim that the VPS listener, product job or billing account failed.
    # Saved operator snapshots are untrusted; only fixed denial categories
    # and bounded nonnegative request counts may enter the rendered UI.
    github_access_panel = ""
    if isinstance(source, dict):
        denial = source.get("provider_admission_denial")
        if denial not in (None, "authentication_required", "rate_limit_confirmed"):
            raise ValueError("untrusted GitHub provider admission evidence")
        attempted = source.get("requests_attempted_actual")
        skipped = source.get("skipped_due_to_provider_denial")
        if attempted is not None or skipped is not None:
            if (type(attempted) is not int or not 0 <= attempted <= 16
                    or type(skipped) is not int or not 0 <= skipped <= 8):
                raise ValueError("untrusted GitHub refresh budget evidence")
        if denial is not None:
            instruction = (
                "GitHub-authenticatie geweigerd: een bevoegde eigenaar moet "
                "de tokeninstellingen herstellen; geen automatische retry."
                if denial == "authentication_required" else
                "GitHub API-verzoekbudget uitgeput: later opnieuw lezen na "
                "providerherstel; geen Actions-rerun of betaalde runner starten."
            )
            github_access_panel = (
                '<section aria-label="GitHub toegang">'
                '<h2>GitHub-bron deels niet beschikbaar</h2><p>' +
                phrase(instruction, 240) +
                ' Gemeten GET-verzoeken: ' + str(attempted) +
                '; niet opnieuw bevraagde jobs: ' + str(skipped) +
                '. Alle eerdere resultaten zonder verse bron blijven ONBEKEND.'
                '</p></section>'
            )
    # Reuse the same exact-HEAD PR evidence as the private desk. HTML keeps
    # merge-first decisions visible even when the operator chooses the
    # standalone recovery cockpit rather than its editable owner desk.
    pr_panel = ""
    if decision.get("pr_delivery") is not None:
        from zsentinel_pr_delivery import pr_delivery_html
        pr_panel = pr_delivery_html(decision["pr_delivery"])
    pr_continuity_panel = ""
    if decision.get("pr_gate_continuity") is not None:
        from zsentinel_pr_hold_continuity import html_panel
        pr_continuity_panel = html_panel(decision["pr_gate_continuity"])
    claims = decision.get("claim_inventory_complete") is True
    source_issues = phrase(triage.get("coverage"), 40)
    live = decision.get("live_sources")
    if live is not None and (not isinstance(live, dict)
            or live.get("notion") not in (
                    "not_observed_no_ownership_claim",
                    "claim_status_observed_no_file_ownership",
                    "private_connector_snapshot_unverified_origin",
                    "degraded_unavailable_no_claim_authority")
            or live.get("runtime") != "not_observed_no_worker_recovery_claim"
            or live.get("current_claim_inventory") != "unknown_and_not_used_for_routing"):
        raise ValueError("invalid live source authority disclosure")
    # Explain *why* a queued job was classified as a self-hosted route,
    # while retaining explicit unknown for degraded, missing or hosted labels.
    runner_route_cards = []
    job_source = (live.get("github_jobs") if isinstance(live, dict)
                  else source if isinstance(source, dict) else None)
    route_rows = job_source.get("refreshed_projects") if isinstance(job_source, dict) else None
    if route_rows is not None:
        if not isinstance(route_rows, list) or len(route_rows) > 8:
            raise ValueError("runner label evidence outside bounded GitHub refresh")
        for row in route_rows:
            if not isinstance(row, dict) or row.get("status") not in ("refreshed", "degraded"):
                raise ValueError("invalid route evidence")
            if row["status"] == "degraded":
                category = row.get("admission_category")
                if category not in (None, "authentication_required",
                                    "rate_limit_confirmed",
                                    "forbidden_or_rate_limit_unknown"):
                    raise ValueError("untrusted GitHub admission category")
                next_step = {
                    "authentication_required":
                        "GitHub-token of aanmeldrechten: bevoegde eigenaar herstelt toegang.",
                    "rate_limit_confirmed":
                        "GitHub API-budget uitgeput: later met een nieuw bronmoment herbeoordelen.",
                    "forbidden_or_rate_limit_unknown":
                        "Repositorytoegang of providerlimiet ONBEKEND: eigenaar bekijkt "
                        "GitHub-toegang, geen betaalde capaciteit veronderstellen.",
                }.get(category, "Bron onbereikbaar: exacte job opnieuw door eigenaar laten lezen.")
                runner_route_cards.append(
                    '<article class="panel warn"><h3>' + phrase(row.get("project_id"), 80) +
                    ' · bron onbereikbaar</h3><p>' + phrase(next_step, 220) +
                    ' Exacte joblabels en runnerroute zijn ONBEKEND. '
                    'Geen diagnose, CI-rerun of runnerherstart toegestaan.</p></article>'
                )
                continue
            if (row.get("source") != "github_exact_run_job"
                    or row.get("may_restart_runner") is not False
                    or row.get("runner_route") not in ("self_hosted", "hosted_or_custom", "unknown")
                    or row.get("job_status") not in ("queued", "in_progress", "completed", "waiting", "pending")
                    or type(row.get("runner_assigned")) is not bool
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
            started = row.get("job_start_evidence", "unknown_not_proven")
            if (started not in ("completed_without_steps", "completed_with_steps",
                                "unknown_not_proven")
                    or (row.get("no_job_steps") is not None
                        and type(row.get("no_job_steps")) is not bool)
                    or (started == "unknown_not_proven"
                        and row.get("no_job_steps") is not None)
                    or (started == "completed_without_steps"
                        and (row["job_status"] != "completed"
                             or row.get("no_job_steps") is not True))
                    or (started == "completed_with_steps"
                        and (row["job_status"] != "completed"
                             or row.get("no_job_steps") is not False))):
                raise ValueError("contradictory exact GitHub job start evidence")
            # A job may finish before a runner is ever assigned.
            stage = row.get("job_assignment_stage", "runner_assignment_missing_from_source")
            assignment_complete = row.get("assignment_source_complete", False)
            assignment_labels = {
                "runner_assignment_missing_from_source": "GitHub-toewijzing ONBEKEND.",
                "queued_without_runner_assignment": "Job in wachtrij zonder toegewezen runner.",
                "terminal_without_runner_assignment": "Job geëindigd zonder runner; billing niet bewezen.",
                "no_runner_assignment_observed": "Geen runner toegewezen bij deze bronwaarneming.",
                "job_with_runner_assignment": "Runner-ID ontvangen; listenergezondheid ONBEKEND.",
                "terminal_with_runner_assignment": "Afgeronde job had een runner-ID.",
            }
            if (type(stage) is not str or stage not in assignment_labels
                    or type(assignment_complete) is not bool
                    or (stage == "runner_assignment_missing_from_source") != (not assignment_complete)
                    or (stage == "queued_without_runner_assignment"
                        and (row["job_status"] != "queued" or row["runner_assigned"]))
                    or (stage == "terminal_without_runner_assignment"
                        and (row["job_status"] != "completed" or row["runner_assigned"]))
                    or (stage == "terminal_with_runner_assignment"
                        and (row["job_status"] != "completed" or not row["runner_assigned"]))):
                raise ValueError("contradictory GitHub job assignment stage")
            canonical_assignment = row.get(
                "canonical_zcloud_runner_assignment_observed")
            exact_runner_id = row.get("runner_id_observed")
            if (canonical_assignment is not None
                    and type(canonical_assignment) is not bool):
                raise ValueError("untrusted canonical runner assignment claim")
            if exact_runner_id is not None and (
                    type(exact_runner_id) is not int
                    or not 1 <= exact_runner_id <= 2**53-1):
                raise ValueError("untrusted GitHub runner ID")
            if canonical_assignment is True and (
                    not row["runner_assigned"]
                    or row["runner_route"] != "self_hosted"
                    or exact_runner_id is None):
                raise ValueError("canonical runner source receipt is contradictory")
            canonical_details = (
                "Exacte job toegewezen aan zcloud-vps-1 (runner_id=" +
                str(exact_runner_id) +
                "); dit is géén bewijs van huidige runnergezondheid of workerhervatting."
                if canonical_assignment is True else
                "Deze job bevestigt geen toewijzing aan zcloud-vps-1."
                if canonical_assignment is False else
                "Canonieke zCloud-runneridentiteit niet afzonderlijk waargenomen."
            )
            start_message = {
                "completed_without_steps":
                    "Afgerond zonder één uitgevoerde jobstap: mogelijke platformadmissie. "
                    "Oorzaak ONBEKEND; eigenaar moet exact job-/check-runannotaties controleren.",
                "completed_with_steps": "Uitgevoerde stappen aanwezig: beoordeel de werkelijke buildfout.",
                "unknown_not_proven": "Jobstart niet geverifieerd; geen billing- of VPS-conclusie.",
            }[started]
            href = ("https://github.com/" + repository + "/actions/runs/" +
                    str(run_id) + "/job/" + str(job_id))
            route_description = {
                "self_hosted": "Self-hosted jobroute bevestigd; runnergezondheid niet bewezen.",
                "hosted_or_custom": "Geen self-hosted-label op deze job; geen bewijs van VPS-runnerstoring.",
                "unknown": "Vereiste runnerlabels onbekend; geen bewijs van VPS-runnerstoring.",
            }[row["runner_route"]]
            if (row["runner_route"] == "self_hosted"
                    and stage == "queued_without_runner_assignment"):
                route_description = (
                    "Self-hosted job wacht zonder toegewezen runner. "
                    "Bevoegde eigenaar controleert online status, labels en repositorytoegang; "
                    "geen automatische serviceherstart.")
            elif (row["runner_route"] == "self_hosted"
                  and stage in ("job_with_runner_assignment",
                                "terminal_with_runner_assignment")):
                route_description = (
                    "Runner-ID waargenomen voor deze specifieke self-hosted job. "
                    "Geen bewijs van actuele listenergezondheid, veilige idle-status of herstel.")
            runner_route_cards.append(
                '<article class="panel read"><h3><a target="_blank" rel="noopener noreferrer" '
                'href="' + escape(href, quote=True) + '">' + phrase(row.get("project_id"), 80) +
                ' · exacte GitHub-job</a></h3><p>' +
                phrase(route_description, 180) + '</p><p class="small">Jobstatus: ' +
                phrase(row["job_status"], 30) + ' · runner toegewezen: ' +
                ('ja' if row["runner_assigned"] else 'nee') +
                '</p><p class="small">Toewijzingsfase: ' +
                phrase(assignment_labels[stage], 180) +
                '</p><p class="small">zCloud-toewijzingsbewijs: ' +
                phrase(canonical_details, 230) +
                '</p><p class="small">Startbewijs: ' + phrase(start_message, 250) +
                '</p><p class="small">Vereiste labels: ' +
                (", ".join(phrase(v, 64) for v in row["route_labels"])
                 if row["route_labels"] else "ONBEKEND") +
                ' · alleen read-only bronbewijs, geen runnerstartrecht.</p></article>'
            )
    runner_route_panel = ('<section><h2>GitHub-jobroute · exacte runnerlabels</h2>' +
                          ('<div class="grid">' + ''.join(runner_route_cards) + '</div>'
                           if runner_route_cards else
                           '<p class="empty">Geen exacte runnerlabels uitgelezen; '
                           'runnerstatus niet geverifieerd.</p>') + '</section>')
    discovery_panel = ""
    discovery = live.get("ci_discovery") if isinstance(live, dict) else None
    if isinstance(discovery, dict) and discovery.get("source") == "github_canonical_readonly_bounded_discovery":
        rows = discovery.get("coverage")
        windows = discovery.get("run_windows")
        if (not isinstance(rows, list) or len(rows) > 3
                or windows != ["queued", "in_progress", "waiting", "failure"]
                or discovery.get("full_repo_ci_inventory") is not False):
            raise ValueError("noncanonical bounded GitHub workflow window receipt")
        labels = {
            "queued": "Wachtende workflow",
            "in_progress": "Al lopende workflow, maar job nog wachtend",
            "waiting": "Goedkeuring/concurrency-wacht (geen runnerstoring bewezen)",
            "failure": "Afgeronde mislukte workflow",
        }
        cards = []
        for row in rows:
            if (not isinstance(row, dict)
                    or row.get("status") not in (
                        "selected_one_source_bound_job",
                        "selected_one_source_bound_job_partial_window",
                        "no_actionable_job_in_sampled_windows",
                        "degraded_unavailable")):
                raise ValueError("invalid GitHub workflow window coverage")
            pid = phrase(row.get("project_id"), 80)
            incomplete = row.get("incomplete_windows", [])
            if (not isinstance(incomplete, list) or len(incomplete) > 4
                    or any(x not in labels for x in incomplete)
                    or len(set(incomplete)) != len(incomplete)
                    or (row["status"] == "selected_one_source_bound_job_partial_window"
                        and not incomplete)):
                raise ValueError("invalid incomplete workflow window receipt")
            not_scanned = row.get("not_scanned_windows", [])
            stop_reason = row.get("repository_stop_reason")
            stop_descriptions = {
                None: None,
                "github_permission_or_rate_limit":
                    "GitHub weigert toegang of heeft de limiet bereikt; verdere verzoeken aan deze repository overgeslagen.",
                "github_authentication_required":
                    "GitHub API gaf HTTP 401: bevoegde eigenaar controleert authenticatie. Geen credential wordt weergegeven.",
                "github_rate_limit_confirmed":
                    "GitHub heeft rate limiting bevestigd; verzoeken hier gestopt. Herstel niet forceren.",
                "github_forbidden_or_rate_limit_unknown":
                    "GitHub API gaf HTTP 403: toegang of limiet onduidelijk. Bevoegde eigenaar controleert bronrechten.",
                "repository_inaccessible":
                    "Repository onbereikbaar; verdere verzoeken aan deze repository overgeslagen.",
                "github_network_unavailable":
                    "Netwerkbron onbereikbaar; verdere verzoeken aan deze repository overgeslagen.",
            }
            if (type(not_scanned) is not list or len(not_scanned) > 4
                    or any(x not in labels for x in not_scanned)
                    or len(set(not_scanned)) != len(not_scanned)
                    or any(x in incomplete for x in not_scanned)
                    or stop_reason not in stop_descriptions):
                raise ValueError("invalid repository stop evidence")
            if row["status"] in ("selected_one_source_bound_job",
                                  "selected_one_source_bound_job_partial_window"):
                window = row.get("workflow_status_window")
                if window not in labels:
                    raise ValueError("unknown discovered workflow status window")
                description = labels[window] + "; één exacte job geselecteerd. Geen volledig CI-overzicht."
                if incomplete:
                    description += (" Onvolledige bronsteekproef (begrensde pagina, "
                                    "ongeldige rij of bronfout): " +
                                    ", ".join(labels[x] for x in incomplete) + ".")
            elif row["status"] == "degraded_unavailable":
                description = ("GitHub-vensters niet voldoende onderzocht of bereikbaar; "
                               "andere jobs en runnergezondheid ONBEKEND.")
            else:
                description = "Geen kandidaat in dit kleine venster; overige jobs ONBEKEND."
            if stop_reason is not None:
                description += " " + stop_descriptions[stop_reason]
            if not_scanned:
                description += " Niet doorzocht: " + ", ".join(
                    labels[x] for x in not_scanned) + "."
            # Other exact jobs observed in this very same bounded workflow
            # sample are useful for unblock triage even when the recovery
            # router follows only one selected job. They carry no authority.
            extras = row.get("additional_job_links", [])
            extra_total = row.get("sampled_actionable_jobs")
            extra_cut = row.get("additional_jobs_truncated", False)
            linked = ""
            if (type(extras) is not list or len(extras) > 5
                    or type(extra_cut) is not bool):
                raise ValueError("untrusted extra GitHub job sample")
            if extra_total is not None:
                if (type(extra_total) is not int or not 1 <= extra_total <= 150
                        or extra_total < len(extras) + 1
                        or extra_cut != (extra_total > len(extras) + 1)
                        or row["status"] not in (
                            "selected_one_source_bound_job",
                            "selected_one_source_bound_job_partial_window")):
                    raise ValueError("contradictory additional job count")
                project_slug = row.get("project_id")
                if (type(project_slug) is not str
                        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", project_slug)):
                    raise ValueError("untrusted canonical job project")
                repo = row.get("repository")
                if (type(repo) is not str or
                        not re.fullmatch(r"Zennay/[A-Za-z0-9_.-]{1,100}", repo)):
                    raise ValueError("invalid exact job source repository")
                primary_run = row.get("run_id")
                primary_job = row.get("job_id")
                primary_sha = row.get("primary_head_sha")
                if (type(primary_run) is not int or type(primary_job) is not int
                        or not 0 < primary_run <= 2**53 - 1
                        or not 0 < primary_job <= 2**53 - 1
                        or type(primary_sha) is not str
                        or not re.fullmatch(r"[a-f0-9]{40}", primary_sha)
                        or row.get("primary_job_status") not in ("queued", "waiting", "pending", "completed")
                        or row.get("primary_runner_route") not in (
                            "self_hosted", "hosted_or_custom", "unknown")):
                    raise ValueError("untrusted selected GitHub job identity")
                primary_href = ("https://github.com/" + repo +
                                "/actions/runs/" + str(primary_run) +
                                "/job/" + str(primary_job))
                seen_links = {(primary_run, primary_job)}
                additional = [
                    '<li><strong>Primaire job:</strong> <a target="_blank"'
                    ' rel="noopener noreferrer" href="' +
                    escape(primary_href, quote=True) + '">GitHub-job ' +
                    str(primary_job) + '</a> · ' +
                    phrase(row.get("primary_runner_route"), 24) + ' · ' +
                    phrase(row.get("primary_job_status"), 24) +
                    ' · HEAD ' + primary_sha[:12] +
                    '<br><small>Opnieuw exact volgen (één job per project):'
                    ' <code style="overflow-wrap:anywhere">--job ' +
                    escape(project_slug + ":" + str(primary_run) + ":" +
                           str(primary_job) + ":" + primary_sha) +
                    '</code></small></li>'
                ]
                for job in extras:
                    if (type(job) is not dict
                            or job.get("observed_only") is not True
                            or job.get("work_resumed_verified") is not False
                            or job.get("action_authorized") is not False
                            or job.get("runner_route") not in (
                                "self_hosted", "hosted_or_custom", "unknown")
                            or job.get("job_status") not in ("queued", "waiting", "pending", "completed")
                            or job.get("source_window") not in labels):
                        raise ValueError("invalid extra GitHub job proof")
                    run_id, job_id = job.get("run_id"), job.get("job_id")
                    if (type(run_id) is not int or type(job_id) is not int
                            or not 0 < run_id <= 2**53-1
                            or not 0 < job_id <= 2**53-1
                            or (run_id, job_id) in seen_links):
                        raise ValueError("duplicate or invalid exact job link")
                    seen_links.add((run_id, job_id))
                    sha = job.get("head_sha")
                    href = ("https://github.com/" + repo + "/actions/runs/" +
                            str(run_id) + "/job/" + str(job_id))
                    if (type(sha) is not str
                            or not re.fullmatch(r"[a-f0-9]{40}", sha)
                            or job.get("url") != href):
                        raise ValueError("job link not bound to source repository")
                    additional.append(
                        '<li><a target="_blank" rel="noopener noreferrer" href="' +
                        escape(href, quote=True) + '">GitHub-job ' +
                        str(job_id) + '</a> · ' +
                        phrase(job["runner_route"], 24) + ' · ' +
                        phrase(job["job_status"], 24) + ' · HEAD ' +
                        sha[:12] +
                        '<br><small>Exact volgen: <code style="overflow-wrap:anywhere">--job ' +
                        escape(project_slug + ":" + str(run_id) + ":" +
                               str(job_id) + ":" + sha) +
                        '</code></small></li>')
                linked = (
                    '<details><summary>Geobserveerde GitHub-jobs (' +
                    str(len(extras) + 1) + ' getoond van ≥' + str(extra_total) +
                    ' gevonden)</summary><ul>' + ''.join(additional) +
                    '</ul><p class="small">Niet alle workflows of jobpagina’s'
                    ' zijn gelezen. De primaire job wordt afzonderlijk'
                    ' opnieuw geverifieerd; deze bronlinks geven GEEN'
                    ' herstart-, claim- of releasebevoegdheid.</p></details>')
                if extra_cut:
                    description += " Extra gevonden jobs buiten deze vijf zijn niet getoond."
            elif extras or extra_cut:
                raise ValueError("additional job links lack a bounded count")
            cards.append('<article class="panel read"><h3>' + pid +
                         '</h3><p>' + phrase(description, 750) +
                         '</p>' + linked + '</article>')
        discovery_panel = (
            '<section><h2>GitHub-zoekvensters · alleen een beperkte steekproef</h2>'
            '<p class="muted">Volgorde: wachtende workflows, lopende workflows met '
            'wachtende jobs, daarna mislukte workflows. Van de eerste vijf workflowruns '
            'per venster wordt maximaal de nieuwste en de verst terugliggende '
            'geldige run onderzocht; per run maximaal 25 jobs uit één GitHub-pagina. '
            'Oudere of tussenliggende runs zijn ONBEKEND. '
            'Geen GitHub-job wordt gestart.</p>'
            '<div class="grid">' + ''.join(cards) + '</div></section>'
        )
    notion_mode = live.get("notion") if isinstance(live, dict) else None
    if notion_mode == "claim_status_observed_no_file_ownership":
        provenance = ("Notion actieve werkstatus is alleen read-only geobserveerd. "
                      "Bestandsscope, lease en worker zijn NIET geverifieerd; geen automatische taakclaim.")
    elif notion_mode == "private_connector_snapshot_unverified_origin":
        provenance = ("Bewust opgeslagen Notion-query, NIET live: herkomst niet geauthenticeerd. "
                      "Alleen positieve statusaanwijzingen; claim, lease, bestandsscope en eigenaar ONBEKEND.")
    elif notion_mode == "degraded_unavailable_no_claim_authority":
        provenance = ("Notion claimstatus kon niet worden gelezen; nul actieve claims "
                      "mag hier niet uit worden afgeleid. Geen automatische taakclaim.")
    elif live is not None:
        provenance = ("Geen live Notion-claimstatus; runtime-claim/lease is niet geverifieerd. "
                      "Eventmetadata, indien apart waargenomen, bewijst geen eigenaar of herstel. "
                      "Dit dashboard biedt observatie, geen automatische taakclaim.")
    else:
        provenance = ("Dit dashboard is gebaseerd op een apart aangeleverd bronpakket; "
                      "claims en runtimeherstel moeten onafhankelijk worden bevestigd.")
    notion = decision.get("notion_claims_observation")
    notion_panel = '<p class="empty">Niet opgevraagd; actuele eigendom onbekend.</p>'
    if notion is None and isinstance(live, dict) and live.get("notion") == "degraded_unavailable_no_claim_authority":
        notion_panel = (
            '<p class="banner">NOTIONBRON ONBEKEND — de expliciet gevraagde live- of '
            'snapshotbron was niet beschikbaar of onvolledig. Er is geen nulclaim, '
            'geen eigenaarbewijs en geen toestemming tot claim, dispatch of herstel.</p>')
    if notion is not None:
        if (not isinstance(notion, dict)
                or notion.get("source") not in (
                    "notion_readonly_data_source_pages",
                    "user_supplied_notion_connector_export_unverified_origin")
                or notion.get("coverage") not in (
                    "current_pages_only", "incomplete",
                    "saved_single_page_not_live_current_claims_unknown")
                or (notion.get("source") ==
                    "user_supplied_notion_connector_export_unverified_origin" and
                    (notion.get("origin_authenticity_verified") is not False or
                     notion.get("live_claim_ownership_verified") is not False))
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
        claim_windows = {
            "missing_worker_or_lease_fields": "Geen volledige worker-/leasevelden",
            "date_only_not_a_lease": "Datum zonder tijd is geen lease",
            "invalid_lease_timestamp": "Ongeldige leaseklok",
            "invalid_lease_interval": "Ongeldig of te lang leasevenster",
            "future_claim_start": "Claimstart ligt in de toekomst",
            "expired_claim_window": "Leasevenster volgens Notion verlopen",
            "time_window_present_not_owner_verified": "Tijdvenster aanwezig; eigenaar NIET geverifieerd",
        }
        window_totals = notion.get("claim_window_observation_counts")
        live_notion = notion["source"] == "notion_readonly_data_source_pages"
        if live_notion:
            if (type(window_totals) is not dict or notion.get("claim_window_verified") is not False
                    or any(key not in claim_windows or type(count) is not int
                           or count < 1 for key, count in window_totals.items())
                    or sum(window_totals.values()) != notion["active_count"]):
                raise ValueError("invalid Notion observed lease window totals")
        elif window_totals is not None:
            raise ValueError("saved Notion snapshot cannot claim live lease windows")
        project_totals = notion.get("active_project_counts")
        project_windows = notion.get("active_project_claim_window_counts")
        if live_notion:
            if (type(project_totals) is not dict or len(project_totals) > 50
                    or type(project_windows) is not dict
                    or set(project_windows) != set(project_totals)):
                raise ValueError("missing Notion project claim window breakdown")
            combined = {}
            for project, buckets in project_windows.items():
                if (type(project) is not str or not 1 <= len(project) <= 100
                        or type(buckets) is not dict or len(buckets) > 7
                        or any(key not in claim_windows or type(count) is not int
                               or count < 1 for key, count in buckets.items())
                        or sum(buckets.values()) != project_totals[project]):
                    raise ValueError("untrusted per-project Notion lease metadata")
                for key, count in buckets.items():
                    combined[key] = combined.get(key, 0) + count
            if combined != window_totals:
                raise ValueError("Notion lease portfolio and project totals disagree")
            running_clocks = notion.get("active_project_in_progress_claim_clock_rechecks")
            verifying = notion.get("verifying_project_counts")
            if (type(running_clocks) is not dict
                    or set(running_clocks) != set(project_totals)
                    or type(verifying) is not dict
                    or any(type(n) is not int or n < 0
                           or n > project_totals[project] - verifying.get(project, 0)
                           or n > sum(value for key, value in project_windows[project].items()
                                      if key != "time_window_present_not_owner_verified")
                           for project, n in running_clocks.items())):
                raise ValueError("Notion running/claimed clock observations inconsistent")
        elif project_windows is not None or (
                notion.get("active_project_in_progress_claim_clock_rechecks") is not None):
            raise ValueError("saved Notion snapshot cannot claim live project lease totals")
        rows = []
        for item in notion["items"]:
            if (not isinstance(item, dict)
                    or item.get("status") not in ("Claimed", "Running", "Verifying")):
                raise ValueError("invalid Notion active/verification status")
            metadata_fields = ("worker", "claimed_at", "claim_expires",
                               "claim_metadata_status", "claim_window_observation",
                               "claim_authorized")
            if live_notion and any(field not in item for field in metadata_fields):
                raise ValueError("live Notion row lost claim-clock provenance")
            if any(field in item for field in metadata_fields):
                worker = item.get("worker")
                claimed_at = item.get("claimed_at")
                claim_expires = item.get("claim_expires")
                metadata_status = item.get("claim_metadata_status")
                window = item.get("claim_window_observation")
                if (live_notion and
                        (window not in claim_windows or item.get("claim_authorized") is not False)):
                    raise ValueError("Notion lease status cannot authorize a claim")
                if (worker is not None and worker not in ("Worker 1", "Worker 2")
                        or any(value is not None and
                               (type(value) is not str or not 1 <= len(value) <= 45
                                or any(ord(char) < 32 for char in value))
                               for value in (claimed_at, claim_expires))
                        or metadata_status not in ("observed_fields_present", "partial_or_missing")
                        or (metadata_status == "observed_fields_present") !=
                           bool(worker and claimed_at and claim_expires)):
                    raise ValueError("invalid observed Notion worker/lease metadata")
                details = []
                if worker:
                    details.append("worker " + worker)
                if claimed_at:
                    details.append("Claimed At " + claimed_at)
                if claim_expires:
                    details.append("Claim Expires " + claim_expires)
                details.append(
                    "velden waargenomen; lease niet geverifieerd"
                    if metadata_status == "observed_fields_present"
                    else "worker-/leasevelden gedeeltelijk of ontbrekend"
                )
                if live_notion:
                    details.append(claim_windows[window])
                metadata_html = '<br><span class="muted small">' + phrase(" · ".join(details), 300) + '</span>'
            else:
                metadata_html = '<br><span class="muted small">Worker-/leasemetadata niet opgenomen in deze momentopname.</span>'
            rows.append('<li class="word"><strong>' + phrase(item.get("work"), 35) +
                        ' · ' + phrase(item.get("status"), 12) +
                        ' · ' + phrase(item.get("project"), 80) +
                        '</strong>' +
                        (': ' + phrase(item.get("title"), 120) if item.get("title") else
                         ' · titel bewust weggelaten') + metadata_html + '</li>')
        caveat = ("Handmatig opgeslagen query, NIET live; huidige eigenaar en herkomst ONBEKEND"
                  if notion["source"] ==
                  "user_supplied_notion_connector_export_unverified_origin" else
                  ("Onvolledige Notion-paginering" if notion["coverage"] == "incomplete"
                   else "Notion werkstatuspagina's opgevraagd"))
        # The live reader can now distinguish partial status windows. Never
        # reuse that claim for a manually saved, unauthenticated snapshot.
        status_windows = notion.get("status_source_windows")
        window_html = (
            '<p class="muted">Geen afzonderlijk live bronvensterbewijs; '
            'geen vrije-werkconclusie uit een opgeslagen momentopname.</p>'
        )
        if status_windows is not None:
            expected = (("Claimed", 1), ("Running", 1), ("Verifying", 2))
            if (notion["source"] != "notion_readonly_data_source_pages"
                    or type(status_windows) is not list
                    or len(status_windows) != 3):
                raise ValueError("untrusted Notion status source windows")
            total_pages = 0
            total_rows = 0
            any_partial = False
            descriptions = []
            for window, (expected_status, budget) in zip(status_windows, expected):
                if (type(window) is not dict
                        or window.get("status") != expected_status
                        or type(window.get("pages_read")) is not int
                        or not 1 <= window["pages_read"] <= budget
                        or type(window.get("page_budget")) is not int
                        or window["page_budget"] != budget
                        or type(window.get("observed_rows")) is not int
                        or not 0 <= window["observed_rows"] <= window["pages_read"] * 100
                        or window.get("coverage") not in ("partial", "complete_within_source")
                        or window.get("read_only") is not True
                        or window.get("claim_scope_verified") is not False):
                    raise ValueError("untrusted Notion window evidence")
                total_pages += window["pages_read"]
                total_rows += window["observed_rows"]
                any_partial |= window["coverage"] == "partial"
                descriptions.append(
                    '<li>' + phrase(expected_status, 14) +
                    ': ≥' + str(window["observed_rows"]) + ' werkitems, ' +
                    str(window["pages_read"]) + '/' + str(budget) + ' pagina’s · ' +
                    ('ONVOLLEDIG · bevoegde eigenaar hercontroleert bron'
                     if window["coverage"] == "partial" else
                     'gelezen binnen opgegeven venster') + '</li>'
                )
            if (type(notion.get("total_pages_read")) is not int
                    or notion["total_pages_read"] != total_pages
                    or notion["active_count"] != total_rows
                    or (notion["coverage"] == "incomplete") != any_partial):
                raise ValueError("Notion status windows disagree with source")
            window_html = ('<details><summary>Notion brondekking per werkstatus'
                           '</summary><ul>' + ''.join(descriptions) +
                           '</ul><p>Geen autorisatie voor herstel, taakclaim, '
                           'runner of release.</p></details>')
        window_counts_html = ""
        if live_notion:
            observed_parts = [
                '<li>' + phrase(claim_windows[status], 90) +
                ': ' + str(window_totals[status]) + ' werkregels</li>'
                for status in claim_windows if status in window_totals
            ]
            ranked_projects = sorted(
                project_windows.items(),
                key=lambda row: (
                    -running_clocks[row[0]],
                    -sum(n for k, n in row[1].items()
                         if k != "time_window_present_not_owner_verified"),
                    row[0]))
            project_rows = []
            for project, buckets in ranked_projects[:12]:
                irregular = sum(n for k, n in buckets.items()
                                if k != "time_window_present_not_owner_verified")
                project_rows.append(
                    '<li>' + phrase(project, 100) + ': ' + str(irregular) +
                    ' ontbrekende/verlopen/ongeldige klokken, waarvan ' +
                    str(running_clocks[project]) +
                    ' bij Claimed/Running-statusregels; ' +
                    str(buckets.get("time_window_present_not_owner_verified", 0)) +
                    ' tijdvensters alleen als metadata</li>')
            window_counts_html = (
                '<details><summary>Notion-leasevelden · géén bewezen actuele claims</summary>'
                '<ul>' + ''.join(observed_parts) +
                '</ul><p>Ook een actueel ogend tijdvenster is geen eigenaarbewijs, '
                'queueclaim of toestemming tot herverdeling.</p>'
                '<h3>Per project · eigenaarcontrole</h3><ul>' +
                ''.join(project_rows) + '</ul>' +
                ('<p>Projectverdeling afgekapt na 12; overige projecten ONBEKEND '
                 'binnen de zichtbare werkbak.</p>' if len(ranked_projects) > 12 else '') +
                '</details>')
        notion_panel = (
            '<p>' + phrase(caveat, 80) + ' · onafgeronde werkregels: ' +
            str(notion["active_count"]) + ' · geen bestandsscope, claimvrijgave of dispatch.</p>' +
            window_html + window_counts_html +
            ('<ul>' + ''.join(rows) + '</ul>' if rows else
             '<p class="empty">Geen actieve regels in deze waarneming; geen bewijs van vrije scope.</p>') +
            ('<p>Voorbeelden afgekapt na 30; zichtbaar per waargenomen '
             'Claimed/Running/Verifying-status afgewisseld. De volledige '
             'bron blijft mogelijk onvolledig; geen vrije-werkconclusie.</p>'
             if notion["items_truncated"] else '')
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
    blocked_queue_panel_html = blocked_queue_panel(queue.get("blocked_queue"))
    queue_summary = (
        "Wachtend: " + str(queue_totals.get("queued_items", 0)) +
        " · Actief: " + str(queue_totals.get("owned_items", 0)) +
        " · Veel claims: " + str(queue_totals.get("high_claim_attempt_items", 0)) +
        " · Mogelijk verlopen leases: " +
        str(queue_totals.get("expired_owner_lease_rows", 0)) +
        " · Onverifieerbare leaseklokken: " +
        str(queue_totals.get("unverifiable_owner_lease_rows", 0)) +
        " · Inconsistente claimvensters: " +
        str(queue_totals.get("inconsistent_owner_lease_window_rows", 0))
        if queue_coverage == "current" else
        "Bron ontbreekt, is onvolledig of verouderd: geen betrouwbare actuele wachtrijclaim."
    )
    retry_contract = queue.get("native_retry_contract_boundary")
    required_retry_semantics = [
        "explicit_failure_disposition", "durable_retry_not_before_backoff",
        "finite_retry_budget", "claim_identity_continuity",
    ]
    if (type(retry_contract) is not dict
            or retry_contract.get("schema") != "zsentinel-native-retry-contract-boundary-v1"
            or retry_contract.get("status") != "not_verified_by_read_only_queue_view"
            or retry_contract.get("source_issue_url") != "https://github.com/Zennay/zCloud/issues/677"
            or retry_contract.get("source_issue_link_is_navigation_only") is not True
            or retry_contract.get("attempts_are_claims_not_failures") is not True
            or retry_contract.get("required_native_semantics") != required_retry_semantics
            or retry_contract.get("owner_route") != "authorized_queue_writer_owner_after_serialized_write_window"
            or retry_contract.get("automatic_retry_authorized") is not False
            or retry_contract.get("queue_write_authorized") is not False
            or retry_contract.get("historical_attempt_reset_authorized") is not False):
        raise ValueError("untrusted native retry-contract boundary")
    retry_contract_panel = (
        '<section aria-label="Native queue retry-contract boundary">'
        '<h2>Native queue-retrycontract · NIET GEVERIFIEERD</h2>'
        '<p>Deze read-only wachtrijweergave bewijst niet of het native retrycontract '
        'al is geïmplementeerd. Claimpogingen zijn geen mislukte uitvoeringen.</p>'
        '<ul><li>Expliciete failure-dispositie: FAILED_RETRYABLE / NEEDS_AI / FAILED_FINAL</li>'
        '<li>Duurzame retry-not-before/backoffgrens</li>'
        '<li>Begrensd retrybudget met expliciete uitputting</li>'
        '<li>Correlatie-/claimidentiteit behouden over retries</li></ul>'
        '<p>Vervolg: bevoegde queue-writereigenaar stemt dit af binnen het bestaande '
        'serialized control-plane write window. Historische attempt-counters niet resetten.</p>'
        '<p><a href="https://github.com/Zennay/zCloud/issues/677" rel="noreferrer">'
        'Issue #677 · navigatie, niet live geverifieerd</a></p>'
        '<p class="muted">Geen automatische retry, queue-write, claimvrijgave of dispatch.</p>'
        '</section>'
    )
    capacity = decision.get("vps_resource_admission")
    if (not isinstance(capacity, dict)
            or capacity.get("state") not in
            ("unavailable", "stale", "overloaded", "advisory_headroom")):
        raise ValueError("unverified VPS capacity summary")
    vps_state = phrase({
        "unavailable": "BRON ONBEKEND",
        "stale": "BRON VEROUDERD",
        "overloaded": "OVERBELAST: GEEN NIEUW WERK",
        "advisory_headroom": "GEEN GEMETEN GRENSOVERSCHRIJDING",
    }[capacity["state"]], 70)
    explanations = {
        "no_vps_observation": "Geen lokale VPS-bron",
        "vps_observation_outdated": "VPS-meting niet actueel",
        "load_above_conservative_capacity": "CPU-belasting hoger dan veilige capaciteit",
        "memory_headroom_low": "Te weinig beschikbare RAM",
        "swap_headroom_low": "Te weinig vrije swap",
        "disk_headroom_low": "Minder dan 2 GiB schijfruimte beschikbaar",
        "disk_unavailable_at_least_90_percent": "Schijf minstens 90% niet beschikbaar",
        "database_mount_inodes_unavailable_at_least_90_percent": "Minstens 90% inodes op databasemount niet beschikbaar",
        "root_filesystem_headroom_below_2_gib": "Systeemschijf heeft minder dan 2 GiB ruimte",
        "root_filesystem_headroom_below_5_gib": "Systeemschijf heeft minder dan 5 GiB vrije ruimte",
        "database_mount_headroom_below_5_gib": "Databasemount heeft minder dan 5 GiB vrije ruimte",
        "swap_used_percent_at_least_90": "Swap is minstens 90% gebruikt",
        "available_memory_percent_at_most_10": "Hoogstens 10% RAM beschikbaar",
        "capacity_severity_high": "VPS-meting meldt hoge capaciteitsdruk",
        "capacity_severity_critical": "VPS-meting meldt kritieke capaciteitsdruk",
        "root_filesystem_critical": "Systeemschijf kritiek",
        "root_filesystem_high": "Systeemschijf krap",
        "database_mount_critical": "Databasemount kritiek",
        "database_mount_high": "Databasemount krap",
        "swap_pressure_high": "Swap minstens 90% gebruikt",
        "memory_pressure_high": "RAM beschikbaar hoogstens 10%",
        "load_pressure_high": "Load minstens 2x toegestane CPU-capaciteit",
        "no_verified_external_archive_headroom": "Geen externe archief-headroom bewezen",
        "root_filesystem_unavailable_at_least_90_percent": "Systeemschijf minstens 90% niet beschikbaar",
        "root_filesystem_inodes_unavailable_at_least_90_percent": "Systeemschijf minstens 90% inodes niet beschikbaar",
        "root_filesystem_inode_capacity_unknown": "Inodes op systeemschijf niet betrouwbaar bekend; geen nieuw werk",
        "legacy_root_filesystem_capacity_not_observed": "Oude VPS-meting zonder aparte systeemschijfdekking; geen nieuw werk",
        "legacy_vps_inode_capacity_not_observed": "Oude VPS-meting zonder inodegegevens; geen nieuw werk toelaten",
        "database_mount_inode_capacity_unknown": "Databasemount rapporteert geen bruikbare inodecapaciteit",
        "thresholds_met_not_release_authorization":
            "Beperkte drempels niet overschreden; geen start- of deployrecht",
        "old_snapshot_disk_occupancy_unknown_not_release_authorization":
            "Schijfbezetting onbekend in oudere meting; geen startrecht",
    }
    raw_reason = capacity.get("reason")
    if type(raw_reason) is not str or len(raw_reason) > 512:
        raise ValueError("invalid VPS admission reason")
    vps_reason = phrase(" · ".join(
        explanations.get(name, name) for name in raw_reason.split(",")
    ), 350)
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
    vps_resource_panel = capacity_observation_panel(
        decision.get("vps_resource_observation"), capacity)
    watch = decision.get("watch_alert_candidates", [])
    if not isinstance(watch, list) or len(watch) > 5:
        raise ValueError("unbounded watch candidate feed")
    missing = decision.get("unresolved_not_observed", 0)
    historic = decision.get("historical_unresolved")
    if historic is None:
        historic = {"schema": "zsentinel-historical-backlog-v1",
                    "total_unresolved": missing, "shown": [], "truncated": missing > 0,
                    "source_partial": True, "action_authorized": False}
    if (type(historic) is not dict
            or historic.get("schema") != "zsentinel-historical-backlog-v1"
            or historic.get("action_authorized") is not False
            or historic.get("source_partial") is not True
            or type(historic.get("total_unresolved")) is not int
            or historic["total_unresolved"] != missing
            or type(historic.get("shown")) is not list
            or len(historic["shown"]) > 30
            or len(historic["shown"]) > missing
            or type(historic.get("truncated")) is not bool
            or historic["truncated"] != (missing > len(historic["shown"]))):
        raise ValueError("untrusted historical backlog receipt")
    historical_owner = decision.get("historical_owner_recheck_candidates", [])
    historical_owner_partial = decision.get("historical_recheck_source_truncated", False)
    if (type(historical_owner) is not list or len(historical_owner) > 5
            or type(historical_owner_partial) is not bool):
        raise ValueError("unbounded historical owner recheck feed")
    historical_by_id = {row["incident_id"]: row for row in historic["shown"]}
    historical_owner_cards = []
    seen_owner_rechecks = set()
    for row in historical_owner:
        if type(row) is not dict:
            raise ValueError("untrusted historical recheck source")
        key = row.get("incident_id")
        source = historical_by_id.get(key)
        if (type(key) is not str or len(key) != 32 or key in seen_owner_rechecks
                or source is None or source.get("human_escalation_historical") is not True
                or row.get("source_status") != "historical_not_observed_needs_owner_recheck"
                or row.get("current_source_observed") is not False
                or row.get("notifier_delivery_authorized") is not False
                or row.get("recovery_action_authorized") is not False
                or row.get("owner_confirmation_required") is not True
                or row.get("project_id") != source["project_id"]
                or row.get("severity_at_last_observation") != source["severity"]
                or row.get("problem_at_last_observation") != source["problem"]
                or row.get("source_last_seen") != source["last_seen"]
                or row.get("exact_github_job") != source["exact_github_job"]):
            raise ValueError("historical recheck not backed by private ledger")
        seen_owner_rechecks.add(key)
        historical_owner_cards.append(
            '<article class="panel warn"><span class="tag">HISTORISCH · OWNER-RECHECK</span>'
            '<span class="tag">' + phrase(row["severity_at_last_observation"], 5) +
            '</span><span class="tag">' + phrase(row["project_id"], 80) +
            '</span><h3>' + phrase(row["problem_at_last_observation"], 120) +
            '</h3><p>Laatste bron: ' + phrase(row["source_last_seen"], 45) +
            '</p><p>Bevoegde eigenaar: controleer oorspronkelijke job/issue en '
            'huidige gates. Geen actuele storing bewezen, geen notificatie verzonden, '
            'geen automatische recoveryactie.</p></article>'
        )
    if historical_owner_partial and not (
            historic["truncated"] or
            sum(x.get("human_escalation_historical") is True
                for x in historic["shown"]) > len(historical_owner)):
        raise ValueError("unexplained historical recheck truncation")
    if (not historical_owner_partial and
            len(historical_owner) != sum(
                x.get("human_escalation_historical") is True for x in historic["shown"])):
        raise ValueError("historical owner rechecks missing from complete sample")
    unattributed = decision.get("unattributed_progress_or_jobs", 0)
    if type(unattributed) is not int or unattributed < 0:
        raise ValueError("invalid unattributed evidence count")
    reconciled = decision.get("reconciled_exact_jobs", [])
    reclassified = decision.get("reclassified_exact_jobs", [])
    if (not isinstance(reconciled, list) or len(reconciled) > MAX_INCIDENTS
            or not isinstance(reclassified, list) or len(reclassified) > MAX_INCIDENTS):
        raise ValueError("invalid exact-job reconciliation")
    if type(missing) is not int or missing < 0:
        raise ValueError("invalid unresolved incident count")
    def section(cards, empty):
        return '<div class="grid">' + ("".join(cards) if cards else '<p class="empty">' + empty + "</p>") + "</div>"
    branch = decision.get("recent_branch_ownership")
    if branch is None:
        branch = {
            "supplied": False, "project_id": "cloud", "coverage": "not_supplied",
            "reason": "optional_source_not_supplied", "observed_branches": 0,
            "suppressed_task_count": 0, "suppressed_task_examples": [],
            "samples_truncated": False,
            "ownership_verified": False, "claim_authorized": False,
        }
    examples = branch.get("suppressed_task_examples")
    if (type(branch) is not dict
            or branch.get("project_id") != "cloud"
            or type(branch.get("supplied")) is not bool
            or branch.get("coverage") not in (
                "not_supplied", "bounded_complete", "stale", "incomplete", "invalid")
            or branch.get("ownership_verified") is not False
            or branch.get("claim_authorized") is not False
            or type(branch.get("observed_branches")) is not int
            or not 0 <= branch["observed_branches"] <= 100
            or type(branch.get("suppressed_task_count")) is not int
            or not 0 <= branch["suppressed_task_count"] <= 250
            or type(examples) is not list or len(examples) > 8
            or len(examples) > branch["suppressed_task_count"]
            or branch.get("samples_truncated") is not (
                branch["suppressed_task_count"] > len(examples))):
        raise ValueError("invalid branch scope routing receipt")
    branch_cards = []
    for row in examples:
        if (type(row) is not dict or row.get("project_id") != "cloud"
                or row.get("reason") not in (
                    "prless_branch_source_stale_or_incomplete",
                    "overlaps_existing_recent_unpr_branch")
                or row.get("priority") not in ("P0", "P1", "P2", "P3")
                or type(row.get("id")) is not str or len(row["id"]) > 80):
            raise ValueError("invalid branch collision candidate")
        if ("branch_ref" in row) != ("conflicting_path" in row):
            raise ValueError("incomplete branch conflict identity")
        if "branch_ref" in row and (
                type(row["branch_ref"]) is not str or len(row["branch_ref"]) > 161
                or type(row["conflicting_path"]) is not str
                or len(row["conflicting_path"]) > 220):
            raise ValueError("invalid branch reference in collision receipt")
        source_details = (
            '<p class="small word">Bestaande branch: ' +
            phrase(row["branch_ref"], 161) + ' · bestand: ' +
            phrase(row["conflicting_path"], 220) + '</p>'
            if "branch_ref" in row else ''
        )
        branch_cards.append(
            '<article class="panel warn"><span class="tag">' +
            phrase(row["priority"], 5) +
            '</span><h3>' + phrase(row["project_id"], 80) + ' / ' +
            phrase(row["id"], 80) + '</h3><p>Al bestaand branchwerk of ' +
            'onvolledige branchscope; voorstel overgeslagen.</p><p class="small">' +
            phrase(row["reason"], 70) +
            '</p>' + source_details +
            '<p class="muted small">Controleer de originele branch, huidige ' +
            'eigenaar en live claims. Geen claim of merge toegestaan.</p></article>'
        )
    branch_status = (
        "Geen branch-eigenaarbron geladen; de voorstellen blijven uitsluitend advies."
        if not branch["supplied"] else
        "Bron onvolledig, ongeldig of ouder dan vijf minuten: nieuwe cloud-voorstellen overgeslagen."
        if branch["coverage"] in ("stale", "incomplete", "invalid") else
        "Bestaande PR-loze branchpaden geobserveerd; alleen overlappende cloud-voorstellen weggelaten."
    )
    branch_panel = (
        '<section><h2>Dubbel werk voorkomen · bestaande branches zonder PR (' +
        str(branch["suppressed_task_count"]) +
        ' voorgestelde taken overgeslagen)</h2><p class="muted">' +
        phrase(branch_status, 400) + ' Bronstatus: ' +
        phrase(branch["coverage"], 30) + ' · ' +
        str(branch["observed_branches"]) +
        ' recente branches in de waargenomen bron. Andere projecten blijven beschikbaar.</p>' +
        section(branch_cards, "Geen brongebonden conflicttaken om te tonen.") +
        ('<p class="muted">Meer conflicttaken niet weergegeven; deze worden niet voorgesteld.</p>'
         if branch["samples_truncated"] else '') +
        '</section>'
    )
    # Incident dependencies are not satisfied merely because an incident
    # disappeared from the bounded current sample. Display source-bound holds
    # separately from both active branch conflicts and routable work.
    held = decision.get("incident_dependency_holds")
    if held is None:
        # Older saved decisions did not carry dependency coverage. Treat them
        # as unknown, never as evidence that no dependent work is held.
        held = {
            "coverage": "incomplete_or_stale", "count": 0, "examples": [],
            "samples_truncated": False, "claim_authorized": False,
            "exact_reconciliation_required": True,
        }
    held_panel = ""
    if held is not None:
        examples = held.get("examples") if type(held) is dict else None
        coverage = held.get("coverage", "incomplete_or_stale") if type(held) is dict else None
        allowed_coverage_reasons = {
            "claim_inventory_not_complete", "task_inventory_not_complete",
            "claim_observation_missing", "task_observation_missing",
            "claim_observation_stale_or_future", "task_observation_stale_or_future",
            "active_claim_scope_unknown",
        }
        coverage_reasons = (held.get("coverage_reasons", [])
                            if type(held) is dict else None)
        if (type(held) is not dict or coverage not in ("complete", "incomplete_or_stale")
                or type(coverage_reasons) is not list or len(coverage_reasons) > 7
                or any(reason not in allowed_coverage_reasons for reason in coverage_reasons)
                or (coverage == "complete" and coverage_reasons)
                or type(held.get("count")) is not int
                or not 0 <= held["count"] <= 250
                or type(examples) is not list or len(examples) > 8
                or len(examples) > held["count"]
                or held.get("samples_truncated") is not (
                    held["count"] > len(examples))
                or held.get("claim_authorized") is not False
                or held.get("exact_reconciliation_required") is not True):
            raise ValueError("untrusted incident dependency hold coverage")
        hold_cards = []
        for item in examples:
            if (type(item) is not dict
                    or item.get("reason") != "incident_dependency_reconciliation_required"
                    or item.get("claim_authorized") is not False
                    or item.get("priority") not in ("P0", "P1", "P2", "P3")
                    or type(item.get("id")) is not str
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", item["id"])
                    or type(item.get("project_id")) is not str
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",
                                        item["project_id"])
                    or type(item.get("unresolved_count")) is not int
                    or not 1 <= item["unresolved_count"] <= 30
                    or type(item.get("observed_blocked_count")) is not int
                    or not 0 <= item["observed_blocked_count"] <= item["unresolved_count"]):
                raise ValueError("invalid dependency hold example")
            hold_cards.append(
                '<article class="panel warn"><span class="tag">HOLD · ' +
                phrase(item["priority"], 5) + '</span><h3>' +
                phrase(item["project_id"], 80) + ' / ' + phrase(item["id"], 80) +
                '</h3><p>' + str(item["unresolved_count"]) +
                ' incidentafhankelijkheden niet exact gereconcilieerd; daarvan ' +
                str(item["observed_blocked_count"]) +
                ' als huidige/historische blokkade waargenomen.</p>' +
                '<p class="muted">Bron kan ontbreken of buiten het tijdvenster vallen. '
                'Eigenaar herbeoordeelt de originele job/claim voordat werk verdergaat. '
                'Geen claim, retry of automatische dispatch.</p></article>'
            )
        if coverage == "complete":
            hold_heading = "Routeerbare kandidaat-taken op incident-HOLD (" + str(held["count"]) + ")"
            hold_status = (
                '<p class="muted">Deze telling omvat alleen verder routeerbare wachtrijtaken die '
                'door onopgeloste incidentafhankelijkheden worden tegengehouden. Verdwenen '
                'incidenten tellen niet als opgelost. Alleen exacte succesvolle job-reconciliatie '
                'uit het bestaande incidentledger kan een afhankelijkheid opheffen; geen bewijs '
                'van algeheel runtime-herstel.</p>'
            )
            empty_holds = "Geen verder routeerbare kandidaat-taken met onopgeloste incidentafhankelijkheden in de complete, actuele taak- en claimbron."
        else:
            hold_heading = "Incidentafhankelijkheden — STATUS ONBEKEND"
            hold_status = (
                '<p class="muted">Taak-/claiminventaris ontbreekt, is onvolledig of is verouderd. '
                'Een getoond aantal is geen totaal; ook nul waargenomen HOLDs is geen all-clear. '
                'Geen afhankelijkheid is automatisch vrijgegeven.</p>'
            )
            empty_holds = "Geen voorbeelden in de onvolledige/verouderde bron; totaal onbekend."
        reason_labels = {
            "claim_inventory_not_complete": "Claiminventaris is niet expliciet compleet.",
            "task_inventory_not_complete": "Taakinventaris is niet expliciet compleet.",
            "claim_observation_missing": "Waarneming van actieve claims ontbreekt.",
            "task_observation_missing": "Waarneming van de taakinventaris ontbreekt.",
            "claim_observation_stale_or_future": "Claimwaarneming is verouderd of heeft een ongeldige toekomsttijd.",
            "task_observation_stale_or_future": "Taakwaarneming is verouderd of heeft een ongeldige toekomsttijd.",
            "active_claim_scope_unknown": "Bestandsscope van minstens één actieve claim is onbekend.",
        }
        coverage_notes = (
            '<ul class="muted">' + ''.join(
                '<li>' + phrase(reason_labels[reason], 180) + '</li>'
                for reason in coverage_reasons) + '</ul>'
            if coverage_reasons else ''
        )
        held_panel = (
            '<section><h2>' + hold_heading + '</h2>' + hold_status + coverage_notes +
            section(hold_cards, empty_holds) +
            ('<p class="muted">Niet alle HOLD-voorbeelden getoond.</p>'
             if held["samples_truncated"] else '') + '</section>'
        )
    # Keep otherwise-eligible tasks visible when an execution gate, VPS
    # capacity receipt, or explicit destination prevents safe suggestion.
    # Owner-guarded queued work stays visible as a non-actionable handoff.
    owner_report = decision.get("routing_owner_holds")
    if owner_report is None:
        owner_report = {
            "coverage": "incomplete_or_stale", "coverage_reasons": [],
            "count": 0, "examples": [], "samples_truncated": False,
            "claim_authorized": False, "dispatch_authorized": False,
        }
    owner_examples = owner_report.get("examples") if type(owner_report) is dict else None
    owner_coverage = owner_report.get("coverage") if type(owner_report) is dict else None
    owner_reasons = owner_report.get("coverage_reasons") if type(owner_report) is dict else None
    if (type(owner_report) is not dict
            or owner_coverage not in ("complete", "incomplete_or_stale")
            or type(owner_reasons) is not list or len(owner_reasons) > 7
            or any(reason not in allowed_coverage_reasons for reason in owner_reasons)
            or (owner_coverage == "complete" and owner_reasons)
            or type(owner_report.get("count")) is not int
            or not 0 <= owner_report["count"] <= 250
            or type(owner_examples) is not list or len(owner_examples) > 8
            or len(owner_examples) > owner_report["count"]
            or owner_report.get("samples_truncated") is not (
                owner_report["count"] > len(owner_examples))
            or owner_report.get("claim_authorized") is not False
            or owner_report.get("dispatch_authorized") is not False):
        raise ValueError("untrusted active-owner hold report")
    owner_cards = []
    for item in owner_examples:
        if (type(item) is not dict
                or item.get("reason") != "active_owner_protection"
                or item.get("claim_authorized") is not False
                or item.get("dispatch_authorized") is not False
                or item.get("priority") not in ("P0", "P1", "P2", "P3")
                or type(item.get("id")) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", item["id"])
                or type(item.get("project_id")) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", item["project_id"])):
            raise ValueError("invalid active-owner hold example")
        owner_cards.append(
            '<article class="panel warn"><span class="tag">OWNER-HOLD · ' +
            phrase(item["priority"], 5) + '</span><h3>' +
            phrase(item["project_id"], 80) + ' / ' + phrase(item["id"], 80) +
            '</h3><p>De bron meldt een actieve eigenaar. Dit is geen bewijs van een actuele claim of toestemming tot overname.</p>' +
            '<p>Verifieer de actuele eigenaar/claim en stem een overdracht af. Niet overnemen, geen claim of dispatch.</p></article>'
        )
    if owner_coverage == "complete":
        owner_heading = "Taken beschermd door een actieve eigenaar (" + str(owner_report["count"]) + ")"
        owner_status = (
            '<p class="muted">Deze wachtrijtaken zijn door de bron gemarkeerd met een actieve eigenaar en worden bewust niet als vrij werk voorgesteld. Eventuele andere blokkades kunnen daarnaast bestaan. '
            'Controleer de actuele eigenaar/claim en stem overdracht af; deze weergave verleent geen eigendom of uitvoerrechten.</p>'
        )
        owner_empty = "Geen wachtrijtaak met een expliciet actieve eigenaar waargenomen."
    else:
        owner_heading = "Bescherming door actieve eigenaar — STATUS ONBEKEND"
        owner_status = (
            '<p class="muted">Taak-/claiminventaris is onvolledig of verouderd. '
            'Het aantal is geen totaal en nul bewijst niet dat een taak vrij is.</p>'
        )
        owner_empty = "Geen voorbeelden in de onvolledige/verouderde bron; totaal onbekend."
    owner_reason_labels = {
        "claim_inventory_not_complete": "Claiminventaris is niet expliciet compleet.",
        "task_inventory_not_complete": "Taakinventaris is niet expliciet compleet.",
        "claim_observation_missing": "Waarneming van actieve claims ontbreekt.",
        "task_observation_missing": "Waarneming van de taakinventaris ontbreekt.",
        "claim_observation_stale_or_future": "Claimwaarneming is ouder dan vijf minuten of heeft een ongeldige toekomsttijd.",
        "task_observation_stale_or_future": "Taakwaarneming is ouder dan vijf minuten of heeft een ongeldige toekomsttijd.",
        "active_claim_scope_unknown": "Bestandsscope van minstens één actieve claim is onbekend.",
    }
    owner_coverage_notes = (
        '<ul class="muted">' + ''.join(
            '<li>' + phrase(owner_reason_labels[reason], 180) + '</li>'
            for reason in owner_reasons) + '</ul>'
        if owner_reasons else ''
    )
    owner_hold_panel = (
        '<section><h2>' + owner_heading + '</h2>' + owner_status +
        owner_coverage_notes + section(owner_cards, owner_empty) +
        ('<p class="muted">Niet alle eigenaar-HOLD-voorbeelden getoond.</p>'
         if owner_report["samples_truncated"] else '') + '</section>'
    )
    gate_report = decision.get("routing_gate_holds")
    if gate_report is None:
        gate_report = {
            "coverage": "incomplete_or_stale", "coverage_reasons": [],
            "count": 0, "examples": [], "samples_truncated": False,
            "exact_gate_recheck_required": True,
            "claim_authorized": False, "dispatch_authorized": False,
        }
    gate_examples = gate_report.get("examples") if type(gate_report) is dict else None
    gate_coverage = gate_report.get("coverage") if type(gate_report) is dict else None
    gate_reasons = gate_report.get("coverage_reasons") if type(gate_report) is dict else None
    if (type(gate_report) is not dict
            or gate_coverage not in ("complete", "incomplete_or_stale")
            or type(gate_reasons) is not list or len(gate_reasons) > 7
            or any(reason not in allowed_coverage_reasons for reason in gate_reasons)
            or (gate_coverage == "complete" and gate_reasons)
            or type(gate_report.get("count")) is not int
            or not 0 <= gate_report["count"] <= 250
            or type(gate_examples) is not list or len(gate_examples) > 8
            or len(gate_examples) > gate_report["count"]
            or gate_report.get("samples_truncated") is not (
                gate_report["count"] > len(gate_examples))
            or gate_report.get("exact_gate_recheck_required") is not True
            or gate_report.get("claim_authorized") is not False
            or gate_report.get("dispatch_authorized") is not False):
        raise ValueError("untrusted routing gate-hold report")
    gate_cards = []
    gate_reason_labels = {
        "required_gate_blocked": "Vereiste uitvoeringsgate is expliciet geblokkeerd.",
        "self_hosted_capacity_not_advisory_headroom":
            "Self-hosted werk is niet routeerbaar zonder verse advisory headroom.",
        "compute_destination_not_explicitly_routable":
            "De uitvoeringsbestemming is niet expliciet veilig routeerbaar.",
    }
    gate_next_steps = {
        "required_gate_blocked":
            "Bevoegde eigenaar herstelt/beoordeelt de vereiste gate; controleer daarna de exacte bron opnieuw. Geen retry of claim.",
        "self_hosted_capacity_not_advisory_headroom":
            "Vernieuw de read-only capaciteitsbron en laat de bevoegde eigenaar beslissen. Geen runner-herstart of dispatch.",
        "compute_destination_not_explicitly_routable":
            "Bevestig de uitvoeringsbestemming bij de taakeigenaar voordat de taak opnieuw wordt beoordeeld. Geen claim of dispatch.",
    }
    for item in gate_examples:
        if (type(item) is not dict
                or item.get("reason") not in gate_reason_labels
                or item.get("claim_authorized") is not False
                or item.get("dispatch_authorized") is not False
                or item.get("priority") not in ("P0", "P1", "P2", "P3")
                or type(item.get("id")) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", item["id"])
                or type(item.get("project_id")) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", item["project_id"])):
            raise ValueError("invalid routing gate-hold example")
        required = item.get("required_gates")
        blocked_for_task = item.get("blocked_required_gates")
        cap_state = item.get("capacity_state")
        if (type(required) is not list or not 1 <= len(required) <= 3
                or any(gate not in ("github_hosted", "self_hosted", "none") for gate in required)
                or len(set(required)) != len(required)
                or ("none" in required and len(required) != 1)
                or type(blocked_for_task) is not list or len(blocked_for_task) > 2
                or len(set(blocked_for_task)) != len(blocked_for_task)
                or any(gate not in ("github_hosted", "self_hosted") for gate in blocked_for_task)
                or not set(blocked_for_task).issubset(required)):
            raise ValueError("invalid routing gate-hold gate list")
        reason = item["reason"]
        if (reason == "required_gate_blocked"
                and (not blocked_for_task or cap_state is not None)
                or reason == "self_hosted_capacity_not_advisory_headroom"
                and ("self_hosted" not in required or blocked_for_task
                     or cap_state not in ("unavailable", "stale", "overloaded"))
                or reason == "compute_destination_not_explicitly_routable"
                and ("self_hosted" in required or blocked_for_task or cap_state is not None)):
            raise ValueError("contradictory routing gate-hold evidence")
        gate_cards.append(
            '<article class="panel warn"><span class="tag">GATE-HOLD · ' +
            phrase(item["priority"], 5) + '</span><h3>' +
            phrase(item["project_id"], 80) + ' / ' + phrase(item["id"], 80) +
            '</h3><p>' + gate_reason_labels[reason] + '</p><p class="muted">Vereiste gates: ' +
            phrase(", ".join(required), 100) +
            (' · geblokkeerd: ' + phrase(", ".join(blocked_for_task), 80)
             if blocked_for_task else '') +
            (' · VPS-capaciteit: ' + phrase(cap_state, 30) if cap_state else '') +
            '</p><p>' + gate_next_steps[reason] +
            '</p><p class="muted">Alleen anderszins routeerbare wachtrij-kandidaat; geen claim, retry of dispatch.</p></article>'
        )
    if gate_coverage == "complete":
        gate_heading = "Werk vastgehouden door uitvoeringsgate/capaciteit (" + str(gate_report["count"]) + ")"
        gate_status = (
            '<p class="muted">Alleen taken die de huidige claim-, dependency- en branch-scope-admission passeerden, '
            'maar niet veilig konden worden voorgesteld vanwege een vereiste gate, self-hosted capaciteitsstatus '
            'of niet-expliciete uitvoeringsbestemming. Los dit op bij de bevoegde eigenaar en herlees de exacte bron.</p>'
        )
        gate_empty = "Geen anderszins routeerbare wachtrijtaak waargenomen die uitsluitend door gate/capaciteit/bestemming wordt tegengehouden."
    else:
        gate_heading = "Uitvoeringsgates/capaciteit — STATUS ONBEKEND"
        gate_status = (
            '<p class="muted">Taak-/claiminventaris ontbreekt, is onvolledig of verouderd. '
            'Het getoonde aantal is geen totaal; nul voorbeelden bewijst niet dat uitvoering vrij is.</p>'
        )
        gate_empty = "Geen voorbeelden in de onvolledige/verouderde bron; totaal onbekend."
    gate_coverage_notes = (
        '<ul class="muted">' + ''.join(
            '<li>' + phrase(reason_labels[reason], 180) + '</li>'
            for reason in gate_reasons) + '</ul>'
        if gate_reasons else ''
    )
    gate_hold_panel = (
        '<section><h2>' + gate_heading + '</h2>' + gate_status +
        gate_coverage_notes + section(gate_cards, gate_empty) +
        ('<p class="muted">Niet alle gate-HOLD-voorbeelden getoond.</p>'
         if gate_report["samples_truncated"] else '') + '</section>'
    )
    # Urgent first; large portfolios keep the first five visible and make
    # remaining cards available under a native details disclosure (no JS).
    ordered_incidents = sorted(incidents, key=lambda i: (
        0 if i.get("severity") == "P0" else 1,
        str(i.get("target_review_by", "")),
        str(i.get("project_id", "")),
    ))
    primary_incidents = [incident_card(row, registry=registry) for row in ordered_incidents[:5]]
    overflow_incidents = [incident_card(row, registry=registry) for row in ordered_incidents[5:]]
    extra_incidents = (
        '<details><summary>Overige ' + str(len(overflow_incidents)) +
        ' incidenten bekijken</summary>' + section(overflow_incidents, "") + '</details>'
        if overflow_incidents else ""
    )
    worker_source = decision.get("worker_progress_observation")
    worker_status = source.get("worker_progress") if isinstance(source, dict) else None
    worker_projects = source.get("worker_projects") if isinstance(source, dict) else None
    worker_panel = ""
    if worker_status == "read_only_indexed_events":
        if (type(worker_projects) is not list or not 1 <= len(worker_projects) <= 3
                or len(set(worker_projects)) != len(worker_projects)
                or worker_source is None
                or type(worker_source) is not dict
                or worker_source.get("selected_projects") != worker_projects):
            raise ValueError("worker progress source does not match its selected project receipt")
        from zsentinel_worker_progress_observer import worker_progress_html
        worker_panel = worker_progress_html(worker_source)
    elif worker_status == "degraded_unavailable_no_recovery_proof":
        if (type(worker_projects) is not list or not 1 <= len(worker_projects) <= 3
                or len(set(worker_projects)) != len(worker_projects)
                or worker_source is not None):
            raise ValueError("invalid degraded worker progress source receipt")
        worker_panel = (
            '<section aria-label="Read-only browserworker voortgang">'
            '<h2>Browserworkers · bron onbeschikbaar</h2>'
            '<p class="banner">WORKERBRON ONBEKEND — de expliciet gekozen read-only '
            'eventbron ontbreekt of het schema is incompatibel. Dit bewijst niet dat '
            'workers stil staan of gezond zijn. Geen claim, hervatting of herstart '
            'is geautoriseerd.</p></section>')
    elif worker_status is not None or worker_source is not None:
        raise ValueError("unrecognized worker progress source status")
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
            + vps_resource_panel
            + f'<section><h2>Actuele blokkades ({len(incidents)})</h2>'
            + section(primary_incidents,
                      "Geen vers geverifieerde blokkades. Dat bewijst geen herstel.")
            + extra_incidents + '</section>'
            + '<section><h2>Menselijke bronherbeoordeling · historisch (' +
              str(len(historical_owner_cards)) +
              ')</h2><p class="muted">Afzonderlijke opvolging van eerdere menselijke blokkades. '
              'GEEN actuele incident- of notificatiefeed en geen claim-/merge-/herstartrecht.</p>'
            + section(historical_owner_cards,
                      "Geen historische menselijke blokkades om afzonderlijk te herbeoordelen.")
            + ('<p class="muted">Er zijn meer historische eigenaarrechecks dan de zichtbare shortlist.</p>'
               if historical_owner_partial else '') + '</section>'
            + f'<section><h2>Historisch niet meer waargenomen · nog te reconciliëren ({missing})</h2>'
            + '<p class="muted">Deze incidenten zijn uitsluitend uit het lokale incidentledger bekend. '
              'Een niet-bekeken projectvenster of ontbrekende GitHub-bron is GEEN herstelbewijs. '
              'Ze staan niet in actuele watch-alerts of automatische werkvoorstellen.</p>'
            + section([historical_card(row, registry=registry) for row in historic["shown"]],
                      "Geen historische bronrijen beschikbaar; onbekend blijft onbekend.")
            + ('<p class="muted">Historische lijst afgekapt tot 30 items; overige incidenten blijven '
               'onopgelost in het privéledger.</p>' if historic["truncated"] else "")
            + '</section>'
            + github_access_panel
            + pr_panel
            + pr_continuity_panel
            + discovery_panel
            + runner_route_panel
            + f'<section><h2>Open productissues · P0/P1 eerst, daarna P2 en zonder label ({len(triage["issues"])})</h2>'
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
            + retry_contract_panel
            + blocked_queue_panel_html
            + worker_panel
            + branch_panel
            + held_panel
            + owner_hold_panel
            + gate_hold_panel
            + selection_hold_panel(decision.get("routing_selection_holds"))
            + f'<section><h2>Onafhankelijke werkvoorstellen ({len(tasks)})</h2>'
            + section([task_card(row) for row in tasks],
                      "Geen claim- en gate-veilig onafhankelijk werk aangetoond.")
            + '</section>'
            + f'<p class="muted">Niet meer waargenomen, nog te reconciliëren incidenten: {missing}. '
              f'Exact succesvol afgeronde GitHub-jobs (geen bewijs van algeheel herstel): '
              f'{len(reconciled)}. Exact heringedeelde oude runnerincidenten (GEEN herstelbewijs): '
              f'{len(reclassified)}. Zonder geverifieerde claim/job-identiteit: '
              f'{unattributed}.</p>'
            + '</main><footer class="muted">Offline HTML, geen netwerk, geen scripts, '
            'geen worker-, runner-, queue-, claim-, CI-, of VPS-mutaties. '
            'Huidige bronweergave is geen bewijs van productieherstel.</footer></body></html>')


__all__ = ["html_view"]
