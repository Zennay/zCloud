#!/usr/bin/env python3
"""zSentinel offline operator cockpit from an already validated routing decision.

Only prints a private, self-contained HTML document. Never fetches sources,
trusts unvalidated markup, performs workflow actions or asserts live recovery.
"""
from __future__ import annotations

import re
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


def incident_card(item, registry=None):
    priority = phrase(item.get("severity"), 5)
    project = phrase(item.get("project_id"), 80)
    owner = phrase(item.get("owner"), 80)
    due = phrase(item.get("target_review_by"), 35)
    action = phrase(item.get("action"), 480)
    status = phrase(item.get("status"), 50)
    reason = item.get("problem")
    # A zero-step completed job requires account/platform inspection. Never
    # tell the operator that billing denial or a VPS listener is proven.
    problem = ("GitHub-job niet gestart · oorzaak nog onbekend"
               if reason == "github_job_unstarted" else phrase(reason, 120))
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
            f'<h3 class="word">Problem · {problem}</h3>'
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
    reason = phrase(item.get("problem"), 120)
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
                runner_route_cards.append(
                    '<article class="panel warn"><h3>' + phrase(row.get("project_id"), 80) +
                    ' · bron onbereikbaar</h3><p>De exacte joblabels en runnerroute zijn '
                    'ONBEKEND; geen diagnose of runnerherstart toegestaan.</p></article>'
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
                or windows != ["queued", "in_progress", "failure"]
                or discovery.get("full_repo_ci_inventory") is not False):
            raise ValueError("noncanonical bounded GitHub workflow window receipt")
        labels = {
            "queued": "Wachtende workflow",
            "in_progress": "Al lopende workflow, maar job nog wachtend",
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
            if (not isinstance(incomplete, list) or len(incomplete) > 3
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
            if (type(not_scanned) is not list or len(not_scanned) > 3
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
                        or row.get("primary_job_status") not in ("queued", "completed")
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
                            or job.get("job_status") not in ("queued", "completed")
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
        provenance = ("Notion en runtime zijn niet uitgelezen; actieve eigenaren zijn onbekend. "
                      "Dit dashboard biedt observatie, geen automatische taakclaim.")
    else:
        provenance = ("Dit dashboard is gebaseerd op een apart aangeleverd bronpakket; "
                      "claims en runtimeherstel moeten onafhankelijk worden bevestigd.")
    notion = decision.get("notion_claims_observation")
    notion_panel = '<p class="empty">Niet opgevraagd; actuele eigendom onbekend.</p>'
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
        rows = []
        for item in notion["items"]:
            if (not isinstance(item, dict)
                    or item.get("status") not in ("Claimed", "Running", "Verifying")):
                raise ValueError("invalid Notion active/verification status")
            rows.append('<li class="word"><strong>' + phrase(item.get("work"), 35) +
                        ' · ' + phrase(item.get("status"), 12) +
                        ' · ' + phrase(item.get("project"), 80) +
                        '</strong>' +
                        (': ' + phrase(item.get("title"), 120) if item.get("title") else
                         ' · titel bewust weggelaten') + '</li>')
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
        notion_panel = (
            '<p>' + phrase(caveat, 80) + ' · onafgeronde werkregels: ' +
            str(notion["active_count"]) + ' · geen bestandsscope, claimvrijgave of dispatch.</p>' +
            window_html +
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
        "thresholds_met_not_release_authorization":
            "Beperkte drempels niet overschreden; geen start- of deployrecht",
        "old_snapshot_disk_occupancy_unknown_not_release_authorization":
            "Schijfbezetting onbekend in oudere meting; geen startrecht",
    }
    raw_reason = capacity.get("reason")
    if type(raw_reason) is not str or len(raw_reason) > 240:
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
            + branch_panel
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
