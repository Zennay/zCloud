#!/usr/bin/env python3
"""zSentinel private incident follow-up desk from an existing read-only decision.

Generate a self-contained offline operator view; local human dispositions are
neither recovery receipts nor claim/merge/dispatch authority. No network I/O.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from html import escape

IDENT = re.compile(r"^[a-f0-9]{32}$")
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
PROBLEM = re.compile(r"^[A-Za-z0-9_]{1,120}$")
STATUSES = frozenset(("human_action_required", "read_only_diagnosis_required"))
MAX_INCIDENTS = 200
MAX_TEXT = 600


def _stamp(value):
    if type(value) is not str or len(value) > 45:
        raise ValueError("untrusted incident datetime")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("untrusted incident datetime") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("incident datetime lacks zone")
    if dt > datetime.now(timezone.utc) + timedelta(minutes=2):
        raise ValueError("incident timestamp beyond tolerated source clock skew")
    return dt.astimezone(timezone.utc).isoformat()


def _text(value, limit=MAX_TEXT):
    if (type(value) is not str or not 1 <= len(value) <= limit or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("unsafe incident field")
    return value


def _source(decision):
    if (type(decision) is not dict
            or type(decision.get("schema_version")) is not int
            or decision["schema_version"] != 1
            or any(decision.get(k) is not False for k in (
                "mutations_performed", "queue_claims_changed", "ci_retried",
                "deployment_authorized", "watch_delivery_performed"))):
        raise ValueError("incident desk requires non-authorizing decision")
    raw = decision.get("incidents")
    if type(raw) is not list or len(raw) > MAX_INCIDENTS:
        raise ValueError("unbounded incident inventory")
    source_time = _stamp(decision.get("observed_at_utc"))
    items = []
    seen = set()
    for entry in raw:
        if type(entry) is not dict:
            raise ValueError("malformed incident")
        key = entry.get("incident_id")
        pid = entry.get("project_id")
        kind = entry.get("problem")
        if (type(key) is not str or not IDENT.fullmatch(key) or key in seen or
                type(pid) is not str or not SLUG.fullmatch(pid) or
                type(kind) is not str or not PROBLEM.fullmatch(kind) or
                entry.get("severity") not in ("P0", "P1", "P2", "P3") or
                type(entry.get("human_escalation")) is not bool or
                entry.get("status") not in STATUSES):
            raise ValueError("noncanonical incident identity")
        seen.add(key)
        first = _stamp(entry.get("first_seen"))
        last = _stamp(entry.get("last_seen"))
        # Review target may be in the future; parse it separately.
        due = entry.get("target_review_by")
        if type(due) is not str or len(due) > 45:
            raise ValueError("invalid review target")
        try:
            due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid review target") from exc
        if due_dt.tzinfo is None or due_dt.utcoffset() is None:
            raise ValueError("review target missing timezone")
        if first > last or last > datetime.fromisoformat(source_time):
            raise ValueError("incident receipt time inconsistent")
        items.append({
            "id": key, "project": pid, "priority": entry["severity"],
            "problem": kind, "owner": _text(entry.get("owner"), 100),
            "action": _text(entry.get("action"), 600),
            "status": entry["status"],
            "human": entry["human_escalation"],
            "first": first, "last": last,
            "review_by": due_dt.astimezone(timezone.utc).isoformat(),
        })
    items.sort(key=lambda x: (
        ("P0", "P1", "P2", "P3").index(x["priority"]),
        not x["human"], x["review_by"], x["id"]))
    canonical_inputs = {"time": source_time, "incidents": items}
    owner_source = decision.get("recent_branch_ownership")
    # Preserve the original handoff identity for reports without a new branch
    # observation. A supplied owner snapshot changes the handoff identity,
    # preventing a private status from opening under a different branch scope.
    if isinstance(owner_source, dict) and owner_source.get("supplied") is True:
        canonical_inputs["branch_owner_source"] = owner_source
    canonical = json.dumps(canonical_inputs, ensure_ascii=True, sort_keys=True,
                           separators=(",", ":"))
    historical = decision.get("historical_unresolved", {
        "schema": "zsentinel-historical-backlog-v1",
        "total_unresolved": 0, "shown": [], "truncated": False,
        "source_partial": True, "action_authorized": False,
    })
    total = decision.get("unresolved_not_observed", 0)
    if (type(total) is not int or total < 0 or type(historical) is not dict
            or historical.get("schema") != "zsentinel-historical-backlog-v1"
            or historical.get("action_authorized") is not False
            or historical.get("source_partial") is not True
            or type(historical.get("total_unresolved")) is not int
            or historical["total_unresolved"] != total
            or type(historical.get("shown")) is not list
            or len(historical["shown"]) > 30
            or len(historical["shown"]) > total
            or historical.get("truncated") is not (total > len(historical["shown"]))):
        raise ValueError("untrusted historical desk inventory")
    historical_rows = []
    historical_seen = set()
    for row in historical["shown"]:
        if type(row) is not dict or any(row.get(k) is not v for k, v in (
                ("current_source_observed", False), ("mutation_authorized", False))):
            raise ValueError("history cannot become current or privileged")
        ident, pid = row.get("incident_id"), row.get("project_id")
        if (type(ident) is not str or not IDENT.fullmatch(ident)
                or ident in historical_seen or ident in seen
                or type(pid) is not str or not SLUG.fullmatch(pid)
                or row.get("source_status") != "not_observed_needs_reconciliation"
                or row.get("severity") not in ("P0", "P1")
                or type(row.get("human_escalation_historical")) is not bool):
            raise ValueError("untrusted historical row")
        historical_seen.add(ident)
        last = _stamp(row.get("last_seen"))
        reason = row.get("problem")
        if type(reason) is not str or not PROBLEM.fullmatch(reason):
            raise ValueError("untrusted historical cause")
        source_job = row.get("exact_github_job")
        if source_job is not None:
            if (type(source_job) is not dict
                    or type(source_job.get("run_id")) is not int
                    or type(source_job.get("job_id")) is not int
                    or not 1 <= source_job["run_id"] < 2**53
                    or not 1 <= source_job["job_id"] < 2**53
                    or type(source_job.get("head_sha")) is not str
                    or not re.fullmatch(r"[0-9a-f]{40}", source_job["head_sha"])):
                raise ValueError("untrusted historical exact job")
        historical_rows.append({
            "id": ident, "project": pid, "priority": row["severity"],
            "problem": reason, "last": last, "exact_github_job": source_job,
            "human_historical": row["human_escalation_historical"],
            "current_source_observed": False, "source_status": "historical_unresolved",
            "action_authorized": False,
        })
    raw_rechecks = decision.get("historical_owner_recheck_candidates", [])
    if type(raw_rechecks) is not list or len(raw_rechecks) > 5:
        raise ValueError("unbounded historical owner recheck source")
    by_old = {row["id"]: row for row in historical_rows}
    rechecks = []
    recheck_ids = set()
    for row in raw_rechecks:
        if type(row) is not dict:
            raise ValueError("untrusted historical owner recheck")
        ident = row.get("incident_id")
        original = by_old.get(ident)
        if (type(ident) is not str or not IDENT.fullmatch(ident)
                or ident in recheck_ids or original is None
                or original["human_historical"] is not True
                or row.get("project_id") != original["project"]
                or row.get("severity_at_last_observation") != original["priority"]
                or row.get("problem_at_last_observation") != original["problem"]
                or _stamp(row.get("source_last_seen")) != original["last"]
                or row.get("exact_github_job") != original["exact_github_job"]
                or row.get("source_status") != "historical_not_observed_needs_owner_recheck"
                or row.get("current_source_observed") is not False
                or row.get("notifier_delivery_authorized") is not False
                or row.get("recovery_action_authorized") is not False
                or row.get("owner_confirmation_required") is not True):
            raise ValueError("historical human follow-up contradicts original ledger")
        recheck_ids.add(ident)
        rechecks.append({
            "id": ident, "project": original["project"],
            "priority": original["priority"], "problem": original["problem"],
            "last": original["last"],
            "exact_github_job": original["exact_github_job"],
            "current_source_observed": False, "action_authorized": False,
        })
    recheck_partial = decision.get("historical_recheck_source_truncated", False)
    if type(recheck_partial) is not bool:
        raise ValueError("invalid historical human shortlist coverage")
    owner_source_sha = hashlib.sha256(json.dumps(
        {"observed_at": source_time, "source_rows": rechecks,
         "truncated": recheck_partial}, ensure_ascii=True,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"historical": historical_rows,
            "historical_count": total,
            "historical_truncated": historical["truncated"],
            "owner_rechecks": rechecks,
            "owner_rechecks_truncated": recheck_partial,
            "owner_rechecks_sha256": owner_source_sha,
            "observed_at": source_time, "incidents": items,
            "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "source_partial": True, "authorizes_actions": False}


def write_private_html(path, rendered):
    """Create-only private static operator artifact; never overwrite an export."""
    target = Path(path)
    parent = target.parent
    if (not isinstance(rendered, str) or not rendered.startswith("<!doctype html>")
            or target.suffix.lower() != ".html"
            or not parent.is_dir() or parent.is_symlink()
            or not hasattr(os, "O_NOFOLLOW")):
        raise ValueError("private desk requires an existing safe directory and .html output")
    info = parent.stat()
    if (info.st_uid != os.geteuid() or
            stat.S_IMODE(info.st_mode) & 0o022):
        raise ValueError("private desk directory must be owned and not group/world writable")
    raw = rendered.encode("utf-8")
    if len(raw) > 1_000_000:
        raise ValueError("bounded private desk HTML required")
    fd, temporary = tempfile.mkstemp(prefix=".zsentinel-desk-", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link existing safe temporary into a new *absent* target.
        # Fails if target already exists (regular file or symlink).
        os.link(temporary, target)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)



def branch_work_review_panel(decision):
    """Static non-authorizing duplicate-work disclosure in the human desk."""
    source = decision.get("recent_branch_ownership")
    if source is None:
        return ""
    if (type(source) is not dict
            or source.get("project_id") != "cloud"
            or type(source.get("supplied")) is not bool
            or source.get("coverage") not in (
                "not_supplied", "bounded_complete", "stale", "incomplete", "invalid")
            or source.get("ownership_verified") is not False
            or source.get("claim_authorized") is not False
            or type(source.get("observed_branches")) is not int
            or not 0 <= source["observed_branches"] <= 100
            or type(source.get("suppressed_task_count")) is not int
            or not 0 <= source["suppressed_task_count"] <= 250
            or type(source.get("suppressed_task_examples")) is not list
            or len(source["suppressed_task_examples"]) > 8
            or len(source["suppressed_task_examples"]) > source["suppressed_task_count"]
            or source.get("samples_truncated") is not (
                source["suppressed_task_count"] > len(source["suppressed_task_examples"]))):
        raise ValueError("invalid read-only branch routing source")
    reason_labels = {
        "prless_branch_source_stale_or_incomplete":
            "Bron verouderd of onvolledig. Controleer de bestaande branches opnieuw.",
        "overlaps_existing_recent_unpr_branch":
            "Er wordt al aan een overlappend bestand gewerkt op een branch zonder PR.",
        "task_file_scope_unavailable":
            "Bestandsscope van de taak is onvoldoende bekend.",
    }
    rows = []
    for row in source["suppressed_task_examples"]:
        if (type(row) is not dict or row.get("project_id") != "cloud"
                or row.get("priority") not in ("P0", "P1", "P2", "P3")
                or type(row.get("id")) is not str
                or SLUG.fullmatch(row["id"]) is None
                or row.get("reason") not in reason_labels
                or ("branch_ref" in row) != ("conflicting_path" in row)):
            raise ValueError("invalid omitted recovery task")
        details = ""
        if "branch_ref" in row:
            reference, path = row["branch_ref"], row["conflicting_path"]
            if (type(reference) is not str or len(reference) > 161
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,160}", reference) is None
                    or type(path) is not str or not 1 <= len(path) <= 220
                    or any(ord(c) < 32 or ord(c) == 127 for c in path)):
                raise ValueError("invalid observed branch/file reference")
            details = ('<p class="meta">Bestaande branch: <code>' +
                       escape(reference, quote=True) +
                       '</code> · bestand: <code>' + escape(path, quote=True) + '</code></p>')
        rows.append(
            '<li class="panel wait"><strong>' +
            escape(row["priority"], quote=True) + ' cloud / ' +
            escape(row["id"], quote=True) + '</strong><p>' +
            escape(reason_labels[row["reason"]], quote=True) +
            '</p>' + details +
            '<p class="meta">Voorstel overgeslagen, niet geclaimd. Controleer de '
            'oorspronkelijke branch en actieve eigenaar.</p></li>'
        )
    coverage = {
        "not_supplied": "Geen branchbron meegestuurd. Dat betekent NIET dat scope vrij is.",
        "bounded_complete": "Bestaande recente PR-loze branchpaden gecontroleerd binnen het bronvenster.",
        "stale": "Bron ouder dan vijf minuten; cloud-voorstellen niet vrijgegeven.",
        "incomplete": "Brondekking of bestandspaden onvolledig; cloud-voorstellen onderdrukt.",
        "invalid": "Optionele branchbron ongeldig; alleen cloud-voorstellen onderdrukt.",
    }[source["coverage"]]
    return (
        '<section class="panel" aria-label="Voorkom dubbel productwerk">'
        '<h2>Dubbel werk voorkomen · recente branches zonder PR</h2><p>' +
        escape(coverage, quote=True) + '</p><p class="meta">' +
        str(source["suppressed_task_count"]) +
        ' potentiële cloud-taken niet voorgesteld · ' +
        str(source["observed_branches"]) +
        ' recente branches in bron. Andere projecten blijven onafhankelijk.</p>' +
        ('<ol>' + "".join(rows) + '</ol>' if rows else
         '<p class="meta">Geen individuele conflicttaken in dit bronvenster getoond.</p>') +
        ('<p class="meta">Extra conflicttaken zijn niet weergegeven.</p>'
         if source["samples_truncated"] else '') +
        '<p class="meta">Geen claims, branches, werkitems of GitHub-reviews gewijzigd.</p></section>'
    )



def runtime_source_panel(decision):
    """Private, read-only worker/Notion context for the interactive incident desk.

    The source remains observation-only and cannot affect human dispositions,
    incident identity, queue leases, work claims or the recovery router.
    """
    live = decision.get("live_sources")
    if live is None:
        return ""
    if (type(live) is not dict
            or live.get("runtime") != "not_observed_no_worker_recovery_claim"
            or live.get("current_claim_inventory") != "unknown_and_not_used_for_routing"
            or live.get("notion") not in (
                "not_observed_no_ownership_claim",
                "claim_status_observed_no_file_ownership",
                "private_connector_snapshot_unverified_origin",
                "degraded_unavailable_no_claim_authority")
            or live.get("worker_progress") not in (
                None, "not_requested", "read_only_indexed_events",
                "selected_project_event_slots_only",
                "bounded_partial_or_no_events",
                "degraded_unavailable_no_recovery_proof",
                "unavailable_no_worker_recovery_proof")):
        raise ValueError("invalid private incident desk source authority")
    worker_status = live.get("worker_progress")
    worker = decision.get("worker_progress_observation")
    notion_status = live["notion"]
    notion = decision.get("notion_claims_observation")
    if worker_status in (None, "not_requested") and worker is not None:
        raise ValueError("unexpected private worker source")
    if worker_status in ("degraded_unavailable_no_recovery_proof",
                         "unavailable_no_worker_recovery_proof") and worker is not None:
        raise ValueError("degraded worker observation cannot carry a source")
    if notion_status in ("not_observed_no_ownership_claim",
                         "degraded_unavailable_no_claim_authority") and notion is not None:
        raise ValueError("unverified Notion source contradiction")
    if worker_status in (None, "not_requested") and notion_status == "not_observed_no_ownership_claim":
        return ""
    panels = ['<section class="panel" aria-label="Privé herstelcontext">'
              '<h2>Werkers en Notion · bronobservaties, géén uitvoerrecht</h2>'
              '<p class="banner">Alleen privé lokaal; een tijdstempel, Notion status'
              ' of browserevent bewijst geen actuele lease, claimeigenaar,'
              ' voortgang, merge- of hersteltoestemming.</p>']
    if worker_status in ("read_only_indexed_events",
                         "selected_project_event_slots_only",
                         "bounded_partial_or_no_events"):
        if worker is None or (worker_status in (
                "selected_project_event_slots_only",
                "bounded_partial_or_no_events") and
                worker.get("coverage") != worker_status):
            raise ValueError("worker source receipt without matching observation")
        from zsentinel_worker_progress_observer import worker_progress_html
        # Reuse the existing strict source validator and script-free renderer.
        panels.append('<details><summary>Browserworker-bron · uitgeklapte'
                      ' eventtijdlijn en veilige eigenaarvervolgstappen</summary>'
                      + worker_progress_html(worker) + '</details>')
    elif worker_status in ("degraded_unavailable_no_recovery_proof",
                           "unavailable_no_worker_recovery_proof"):
        panels.append('<p class="banner">Browserworker-bron aangevraagd maar'
                      ' onbeschikbaar. Niet interpreteren als vrije worker.</p>')
    if notion is not None:
        if (type(notion) is not dict
                or notion.get("source") not in (
                    "notion_readonly_data_source_pages",
                    "user_supplied_notion_connector_export_unverified_origin")
                or notion.get("coverage") not in (
                    "current_pages_only", "incomplete",
                    "saved_single_page_not_live_current_claims_unknown")
                or notion.get("claim_scope_verified") is not False
                or notion.get("task_inventory_verified") is not False
                or notion.get("can_dispatch") is not False
                or notion.get("can_release_claim") is not False
                or type(notion.get("active_count")) is not int
                or not 0 <= notion["active_count"] <= 400
                or type(notion.get("items")) is not list
                or len(notion["items"]) > 30
                or type(notion.get("items_truncated")) is not bool):
            raise ValueError("untrusted private Notion observation")
        expected = ("claim_status_observed_no_file_ownership" if
                    notion["source"] == "notion_readonly_data_source_pages" else
                    "private_connector_snapshot_unverified_origin")
        if notion_status != expected:
            raise ValueError("Notion source identity and coverage disagree")
        if (notion["source"] == "user_supplied_notion_connector_export_unverified_origin"
                and (notion.get("origin_authenticity_verified") is not False
                     or notion.get("live_claim_ownership_verified") is not False)):
            raise ValueError("saved Notion snapshot lacks unverified-origin guard")
        labels = []
        for item in notion["items"]:
            if type(item) is not dict or item.get("status") not in (
                    "Claimed", "Running", "Verifying"):
                raise ValueError("invalid Notion work status sample")
            parts = []
            for field, limit in (("work", 35), ("status", 12),
                                 ("project", 80), ("title", 120)):
                value = item.get(field)
                if value is None and field == "title":
                    value = "Titel niet meegestuurd"
                if (type(value) is not str or len(value) > limit
                        or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                    raise ValueError("unsafe private Notion status label")
                parts.append(escape(value))
            labels.append("<li>" + " · ".join(parts) + "</li>")
        caveat = ("HANDMATIG OPGESLAGEN · oorsprong en huidige claims onbekend"
                  if expected == "private_connector_snapshot_unverified_origin"
                  else ("ONVOLLEDIGE BRON · niet alle statuspagina's gezien"
                        if notion["coverage"] == "incomplete" else
                        "Live opgevraagde Notion werkstatus · geen bestands- of leaseclaim"))
        panels.append('<details><summary>Notion statusbron · ' +
                      str(notion["active_count"]) + ' waargenomen werkregels' +
                      (' · samples afgekapt' if notion["items_truncated"] else '') +
                      '</summary><p>' + escape(caveat) +
                      '</p><ul>' + ''.join(labels) +
                      '</ul><p>Niet bevoegd om werk over te nemen, te hervatten of'
                      ' als voltooid te markeren.</p></details>')
    elif notion_status == "degraded_unavailable_no_claim_authority":
        panels.append('<p class="banner">Notionbron aangevraagd maar'
                      ' onbeschikbaar. Claimeigenaarschap ONBEKEND.</p>')
    panels.append('</section>')
    return ''.join(panels)


def review_html(decision):
    """One offline HTML view + bounded private operator handoff in browser."""
    # Validate optional branch data before using it in the source fingerprint.
    branch_panel = branch_work_review_panel(decision)
    runtime_panel = runtime_source_panel(decision)
    source = _source(decision)
    payload = json.dumps(source, ensure_ascii=True, separators=(",", ":"))
    payload = (payload.replace("<", "\\u003c").replace(">", "\\u003e")
               .replace("&", "\\u0026"))
    html = r"""<!doctype html><html lang="nl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none';script-src 'unsafe-inline';style-src 'unsafe-inline';
 connect-src 'none';img-src 'none';form-action 'none';object-src 'none';base-uri 'none'">
<title>zSentinel | Privé incidentenbalie</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif}
*{box-sizing:border-box}body{background:#0d1420;color:#eff4fb;max-width:1160px;margin:auto;padding:clamp(16px,3vw,36px)}
a{color:#bde7ff}h1{font-size:clamp(28px,4vw,42px);letter-spacing:-.03em}h2{font-size:21px}
p{line-height:1.55;color:#d2dce8}.banner{background:#2a2924;border-left:4px solid #ffcc85;padding:12px 16px;border-radius:8px}
.meta{color:#adc2d6;font-size:13px}.row{display:flex;align-items:end;gap:12px;flex-wrap:wrap}
label{display:grid;gap:6px;margin:12px 0}select,input,textarea,button{font:inherit}
select,input,textarea{border:1px solid #66809d;background:#18283d;color:white;border-radius:7px;padding:9px}
textarea{min-height:96px;width:100%;resize:vertical}button{border:1px solid #94c7ea;background:#b9e4ff;color:#10243b;padding:10px 16px;border-radius:8px;font-weight:700;cursor:pointer}
button:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #fff0a1;outline-offset:2px}
.panel{background:#172538;border:1px solid #415c79;border-radius:12px;margin:13px 0;padding:16px}
.panel.human{border-left:5px solid #ffcb8e}.panel.wait{border-left:5px solid #90bddf}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.scroll{max-height:70vh;overflow:auto}
.tag{display:inline-block;border:1px solid #9eafc6;border-radius:16px;padding:2px 7px;font-size:12px;margin-right:5px}
.line{margin:9px 0}.line strong{display:inline-block;min-width:84px}
small{color:#bbcbdc}code{overflow-wrap:anywhere}#detail{min-height:320px}
@media(max-width:760px){.grid{grid-template-columns:1fr}.scroll{max-height:320px}}
@media print{body{color:black;background:white}.controls,.grid>div:first-child{display:none}.grid{display:block}}
</style>
<header><small>zCLOUD / zSENTINEL / READ-ONLY SOURCE</small>
<h1>Vliegende keep · incidentenbalie</h1>
<p>Een lokaal overzicht om concrete blokkades menselijk op te volgen. De bron is één begrensde momentopname;
de actieknoppen veranderen geen echte incidenten of claims.</p>
<p class="banner"><strong>GEEN herstel- of uitvoerrechten.</strong>
Statuskeuzes en notities bestaan alleen in dit browsertabblad totdat je een privé-overdracht downloadt.
Geen workflow, GitHub-write, calendar push, Notion-write, lease of deploy.</p>
<p class="meta" id="freshness"></p></header>
<div class="controls row">
<label>Project<select id="project"><option value="">Alle projecten</option></select></label>
<label>Eigenaar<select id="owner"><option value="">Alle eigenaren</option></select></label>
<label>Ernst<select id="priority"><option value="">Alle prioriteiten</option>
<option>P0</option><option>P1</option><option>P2</option><option>P3</option></select></label>
<label>Soort<select id="human"><option value="">Alle blokkades</option>
<option value="yes">Menselijke actie nodig</option><option value="no">Alleen diagnose</option></select></label>
<label>Reviewdoel<select id="reviewTarget"><option value="">Alle deadlines</option>
<option value="overdue">Reviewdoel verstreken</option>
<option value="soon">Verstreken of binnen 2 uur</option></select></label>
<label>Zoeken<input id="query" type="search" maxlength="80" placeholder="Project of probleem"></label>
</div>
<p id="count" aria-live="polite"></p>
<section class="panel" aria-label="Historische niet-meer-geobserveerde incidenten">
<h2>Historisch verdwenen uit beperkte bron · NIET opgelost</h2>
<p>Dit zijn oude privé-ledgerincidenten die een latere bron niet opnieuw zag. Ze zijn
geen huidige incidenten, watch-alerts of nieuwe taakclaims. Controleer exact bronbewijs
met een bevoegde eigenaar; er worden nooit retries of herstelacties gestart.</p>
<p id="historyCount" class="meta"></p><div id="historical" class="scroll"></div>
<div class="controls"><h3>Historische P0/P1 · alleen menselijke bronherbeoordeling</h3>
<p>Dit zijn geen actuele notificaties. Bekijk de privé-overdracht met de
oorspronkelijke jobidentiteit en bronhash vóór je zelf de bevoegde eigenaar benadert.</p>
<p id="ownerRecheckCount" class="meta"></p>
<div class="row"><button id="previewOwnerRecheck" type="button">Historische owner-briefing bekijken</button>
<button id="downloadOwnerRecheck" type="button" disabled>Download privébriefing (.md)</button></div>
<pre id="ownerRecheckPreview" style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto"></pre>
<p id="ownerRecheckStatus" class="meta" aria-live="polite">Nog geen historische briefing voorbereid.</p>
</div>
</section>
__BRANCH_PANEL__
__RUNTIME_PANEL__
<section class="panel" aria-label="Human escalation overview"><h2>Wie heeft actie nodig?</h2>
<p class="meta">Alleen huidige gedeeltelijke bron: eigenaarverdeling is geen eigenaarsbewijs of werktoewijzing.</p>
<div id="ownerSummary"></div></section>
<div class="grid"><div><h2>Actuele bronincidenten</h2><div class="scroll" id="items"></div></div>
<div><h2>Menselijke opvolging</h2><section id="detail" class="panel">
<p>Selecteer een incident. Geen incident geselecteerd betekent niet dat het portfolio gezond is.</p></section>
<section class="panel controls"><h2>Privé-overdracht</h2>
<p>Download alleen je eigen statuskeuzes en notities, exact gekoppeld aan deze momentopname.
Import van een ander bronmoment verandert niets.</p>
<div class="row"><button id="export" type="button">Download overdracht</button>
<label>Heropen exact dezelfde bron <input id="import" type="file" accept=".json,application/json"></label>
<label>Herbruik oude notities (opnieuw beoordelen) <input id="carry" type="file" accept=".json,application/json"></label></div>
<p id="feedback" aria-live="polite"></p></section>
<section class="panel controls" aria-label="Source-bound human briefing"><h2>Privé briefing per eigenaar</h2>
<p>Alleen gefilterde incidenten die menselijke actie nodig hebben. Voorvertoning bevat mogelijk privénotities; deel uitsluitend bewust.</p>
<div class="row"><button id="previewBrief" type="button">Voorvertoning maken</button>
<button id="downloadBrief" type="button" disabled>Download deze briefing (.md)</button></div>
<pre id="briefText" style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:340px;overflow:auto"></pre>
<p id="briefStatus" class="meta" aria-live="polite">Geen briefing voorbereid.</p></section></div></div>
<footer><p class="meta">Een ontbrekend incident kan door incomplete GitHub-bron, rate-limit,
scope of tijdvenster komen. Een lokale opvolgstatus bewijst geen eigenaargoedkeuring
of systeemherstel. Bronstatus blijft ongewijzigd.</p></footer>
<script>
"use strict";
const source=__SOURCE__;
const $=id=>document.getElementById(id);
const choices=["nog-niet-beoordeeld","eigenaar-benaderen","in-behandeling-bij-eigenaar",
               "extern-geparkeerd","bron-opnieuw-verifieren"];
const notes=Object.create(null);
let selected=null;
const el=(tag,txt,cls)=>{const n=document.createElement(tag);if(txt!==undefined)n.textContent=txt;if(cls)n.className=cls;return n};
let lastHistoricalBrief=null;
function historicalOwnerBrief(){
 const rows=source.owner_rechecks;
 if(!Array.isArray(rows)||rows.length>5||!rows.length)
  throw Error("Geen brongebonden historische owner-rechecks beschikbaar");
 if(!/^[0-9a-f]{64}$/.test(source.owner_rechecks_sha256))
  throw Error("Historische bronhash ontbreekt");
 const lines=["# zSentinel · historische menselijke eigenaarherbeoordeling","",
  "> Niet actueel. Geen bevestigde storing, ownerclaim, herstel, CI-rerun, merge of deploy.",
  "> Deze privéoverdracht is handmatig en doet geen notificatie.","",
  "- Bron UTC: "+source.observed_at,
  "- Historische bron SHA256: "+source.owner_rechecks_sha256,
  "- Actuele incidentbron SHA256: "+source.fingerprint,
  "- Historische ownerrechecks getoond: "+rows.length,
  "- Historische bronselectie afgekapt: "+(source.owner_rechecks_truncated?"JA":"nee"),""];
 for(const row of rows){
  if(row.current_source_observed!==false||row.action_authorized!==false)
   throw Error("Historische owner-briefing bevat autorisatie");
  lines.push("## HISTORISCH "+mdText(row.priority+" · "+row.project+" · "+row.problem),
   "- Incidentbron: "+row.id,
   "- Laatst gezien: "+mdText(row.last),
   "- Benodigde stap: bevoegde eigenaar controleert oorspronkelijke job/issue en huidige gates.",
   "- Actuele bron waargenomen: NEE; eigenaarschap en herstel NIET bevestigd.");
  const job=row.exact_github_job;
  if(job)lines.push("- Eerdere GitHub-run: "+job.run_id+
   " · job "+job.job_id+" · exact SHA "+job.head_sha);
  else lines.push("- Eerdere exacte GitHub-job: ONBEKEND.");
  lines.push("");
 }
 lines.push("---","Geen automatische melding, claimovername, runneractie of productierelease.","");
 const data=lines.join("\n");
 if(new TextEncoder().encode(data).byteLength>120000)
  throw Error("Historische privébriefing te groot");
 return data;
}
$("ownerRecheckCount").textContent=source.owner_rechecks.length+
 " historische ownerrechecks in de begrensde bron; geen nieuwe alarmen."+
 (source.owner_rechecks_truncated?" Er zijn meer verborgen historische bronnen.":"");
$("previewOwnerRecheck").onclick=()=>{
 try{
  const body=historicalOwnerBrief();lastHistoricalBrief=body;
  $("ownerRecheckPreview").textContent=body;
  $("downloadOwnerRecheck").disabled=false;
  $("ownerRecheckStatus").textContent="Privévoorvertoning gereed; niets verstuurd.";
 }catch(_){
  lastHistoricalBrief=null;$("ownerRecheckPreview").textContent="";
  $("downloadOwnerRecheck").disabled=true;
  $("ownerRecheckStatus").textContent="Geen veilige historische owner-briefing beschikbaar.";
 }
};
$("downloadOwnerRecheck").onclick=()=>{
 try{
  if(!lastHistoricalBrief||historicalOwnerBrief()!==lastHistoricalBrief)
   throw Error("Historische privévoorvertoning verlopen");
  const url=URL.createObjectURL(new Blob([lastHistoricalBrief],
    {type:"text/markdown;charset=utf-8"}));
  const a=el("a");a.href=url;a.download="zsentinel-historical-owner-rechecks.md";
  document.body.append(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  $("ownerRecheckStatus").textContent="Privébriefing aangeboden; geen melding verstuurd.";
 }catch(_){
  $("downloadOwnerRecheck").disabled=true;
  $("ownerRecheckStatus").textContent="De bron/voorvertoning is niet meer geldig.";
 }
};
function incidentLabel(x){return x.priority+" · "+x.project+" · "+x.problem}
function isFresh(){
 const delta=Date.now()-Date.parse(source.observed_at);
 const msg=(delta<0?"Systeemklok wijkt af;":delta>600000?"OUDER DAN 10 MINUTEN;":"Momentopname;");
 $("freshness").textContent=msg+" bron "+source.observed_at+"; dekking altijd gedeeltelijk. Bron-ID "+source.fingerprint.slice(0,12)+".";
}
for(const owner of [...new Set(source.incidents.map(i=>i.owner))].sort()){
 const opt=el("option",owner);opt.value=owner;$("owner").append(opt);
}
for(const p of [...new Set(source.incidents.map(i=>i.project))].sort()){
 const opt=el("option",p);opt.value=p;$("project").append(opt);
}
function filtered(){const q=$("query").value.trim().toLowerCase();return source.incidents.filter(x=>
 (!$("project").value||x.project===$("project").value)&&
 (!$("owner").value||x.owner===$("owner").value)&&
 (!$("priority").value||x.priority===$("priority").value)&&
 (!$("human").value||(x.human?"yes":"no")===$("human").value)&&
 (!$("reviewTarget").value||
   ($("reviewTarget").value==="overdue"&&Date.parse(x.review_by)<=Date.now())||
   ($("reviewTarget").value==="soon"&&Date.parse(x.review_by)<=Date.now()+7200000))&&
 (!q||(x.project+" "+x.problem+" "+x.owner).toLowerCase().includes(q)));
}
function render(){
 invalidateBrief();
 $("historical").replaceChildren();
 $("historyCount").textContent=source.historical_count+
  " historische incidenten niet gereconcilieerd; "+source.historical.length+
  " brongebonden voorbeelden, nooit als huidige alarmen gepresenteerd."+
  (source.historical_truncated?" Lijst is afgekapt.":"");
 if(!source.historical.length)
   $("historical").append(el("p","Geen historische details beschikbaar; een lege lijst is geen algemene herstelsignalering."));
 for(const old of source.historical){
   const block=el("article",undefined,"panel");
   block.append(el("h3",old.priority+" · "+old.project+" · "+old.problem));
   block.append(el("p","HISTORISCH, NIET ACTUEEL · laatste waarneming: "+old.last,"meta"));
   if(old.exact_github_job)
     block.append(el("p","Exact oud GitHub-run "+old.exact_github_job.run_id+
       " · job "+old.exact_github_job.job_id+" · commit "+old.exact_github_job.head_sha,"code"));
   else block.append(el("p","Exacte jobidentiteit onbekend; geen aannames over herstel.","meta"));
   block.append(el("p","Actie: vraag een bevoegde eigenaar de oorspronkelijke bron opnieuw te verifiëren."));
   $("historical").append(block);
 }
 $("items").replaceChildren();
 $("ownerSummary").replaceChildren();
 const assigned=new Map();
 for(const x of filtered().filter(x=>x.human)){
  const counts=assigned.get(x.owner)||{p0:0,p1:0,other:0,overdue:0};
  if(Date.parse(x.review_by)<=Date.now())counts.overdue++;
  if(x.priority==="P0")counts.p0++;
  else if(x.priority==="P1")counts.p1++;
  else counts.other++;
  assigned.set(x.owner,counts);
 }
 if(!assigned.size)$("ownerSummary").append(el("p","Geen geobserveerde mens-afhankelijke incidenten in dit filter. Dekking blijft onvolledig."));
 for(const [owner,counts] of [...assigned.entries()].sort((a,b)=>b[1].overdue-a[1].overdue||b[1].p0-a[1].p0||b[1].p1-a[1].p1||a[0].localeCompare(b[0]))){
  const p=el("p",owner+" · P0: "+counts.p0+" · P1: "+counts.p1+" · overige: "+counts.other+" · reviewdoel verstreken: "+counts.overdue);
  $("ownerSummary").append(p);
 }
 const rows=filtered();$("count").textContent=rows.length+" incidenten binnen huidig filter (bronmaximaal "+source.incidents.length+").";
 if(!rows.length)$("items").append(el("p","Geen bronincidenten in dit filter. Geen all-clear."));
 for(const x of rows){
   const b=el("button",incidentLabel(x));b.style.width="100%";b.style.marginBottom="8px";b.style.textAlign="left";
   b.style.background=selected===x.id?"#e6f5ff":"#c4e6fc";
   b.onclick=()=>{selected=x.id;render()};
   const wrapper=el("div");wrapper.append(b);
   wrapper.append(el("small",x.human?"Eigenaaractie vereist":"Read-only diagnose · geen automatische actie"));
   $("items").append(wrapper);
 }
 const x=source.incidents.find(v=>v.id===selected);
 const box=$("detail");box.replaceChildren();
 if(!x){box.append(el("p","Selecteer een bronincident om de menselijke opvolging te bekijken."));return}
 const hd=el("h3",incidentLabel(x));box.append(hd);
 box.append(el("p","MENS NODIG: "+(x.human?"JA":"NEE, alleen veilige diagnose")+" · Owner: "+x.owner,"meta"));
 for(const [label,value] of [["Probleem",x.problem],["Actie",x.action],["Bronstatus",x.status]]){
   const p=el("p",undefined,"line");p.append(el("strong",label));p.append(document.createTextNode(" "+value));box.append(p)
 }
 box.append(el("p","Reviewdoel "+x.review_by+
   (Date.parse(x.review_by)<=Date.now()?" · VERSTREKEN (adviserend)":" · nog niet verstreken (adviserend)")+
   ". Dit is geen kalenderafspraak of bevestigde eigenaaractie.","meta"));
 const sl=el("label","Mijn opvolgstatus (alleen privé)");const sel=el("select");
 for(const c of choices){const o=el("option",c.replaceAll("-"," "));o.value=c;sel.append(o)}
 sel.value=notes[x.id]?.status||choices[0];
 sel.onchange=()=>{if(!notes[x.id])notes[x.id]={status:choices[0],note:""};notes[x.id].status=sel.value;invalidateBrief();$("feedback").textContent="Lokale status aangepast, niet doorgestuurd."};
 sl.append(sel);box.append(sl);
 const nl=el("label","Mijn privénotitie (maximaal 800 tekens)");
 const ta=el("textarea");ta.maxLength=800;ta.value=notes[x.id]?.note||"";
 ta.oninput=()=>{if(!notes[x.id])notes[x.id]={status:choices[0],note:""};notes[x.id].note=ta.value;invalidateBrief();$("feedback").textContent="Notitie alleen in dit tabblad."};
 nl.append(ta);box.append(nl);
 box.append(el("small","Dit verandert NIET de incidentstatus, CI, PR, eigenaar of queue."));
}
for(const id of ["project","owner","priority","human","reviewTarget","query"])$(id).addEventListener(id==="query"?"input":"change",render);
function download(obj){
 const str=JSON.stringify(obj,null,2);if(new TextEncoder().encode(str).byteLength>1000000)throw Error("Overdracht te groot");
 const url=URL.createObjectURL(new Blob([str+"\n"],{type:"application/json"}));
 const a=el("a");a.href=url;a.download="zsentinel-private-human-handoff.json";
 document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$("export").onclick=()=>{try{
 const entries=Object.keys(notes).filter(k=>source.incidents.some(v=>v.id===k)).sort().map(id=>({id,status:notes[id].status,note:notes[id].note}));
 for(const r of entries)if(!choices.includes(r.status)||typeof r.note!=="string"||r.note.length>800||/[\u0000-\u0009\u000B-\u001F\u007F]/u.test(r.note))throw Error("Ongeldige notitie");
 download({schema:"zsentinel-human-followup-v1",source_sha256:source.fingerprint,source_at:source.observed_at,
         read_only:true,action_authorized:false,incident_count:source.incidents.length,entries});
 $("feedback").textContent="Privébestand aangeboden; er is niets verstuurd.";
 }catch(_){$("feedback").textContent="Export geweigerd; controleer lokale gegevens."}};
$("import").onchange=async event=>{
 try{
  const file=event.target.files?.[0];if(!file||file.size>1000000)throw Error("size");
  const raw=await file.text();const data=JSON.parse(raw);
  if(!data||data.schema!=="zsentinel-human-followup-v1"||data.source_sha256!==source.fingerprint||
     data.source_at!==source.observed_at||data.read_only!==true||data.action_authorized!==false||
     data.incident_count!==source.incidents.length||!Array.isArray(data.entries)||
     data.entries.length>source.incidents.length)throw Error("source");
  const fresh=Object.create(null);
  for(const row of data.entries){
    if(!row||typeof row.id!=="string"||!source.incidents.some(x=>x.id===row.id)||
       row.id in fresh||!choices.includes(row.status)||typeof row.note!=="string"||
       row.note.length>800||/[\u0000-\u0009\u000B-\u001F\u007F]/u.test(row.note))throw Error("entry");
    fresh[row.id]={status:row.status,note:row.note};
  }
  for(const key of Object.keys(notes))delete notes[key];
  for(const key of Object.keys(fresh))notes[key]=fresh[key];
  $("feedback").textContent="Privé-overdracht geopend; bron-ID exact gelijk. Geen externe actie.";
  render();
 }catch(_){$("feedback").textContent="Import geweigerd: geen exacte betrouwbare overdracht voor deze bron."}
 finally{event.target.value=""}
};
// Import notes from an older saved source ONLY with explicit user confirmation.
// Prior local statuses must never become current owner/recovery evidence.
$("carry").onchange=async event=>{
 try{
  const file=event.target.files?.[0];
  if(!file||file.size>1000000)throw Error("size");
  const data=JSON.parse(await file.text());
  const older=Date.parse(data?.source_at);
  const current=Date.parse(source.observed_at);
  if(!data||data.schema!=="zsentinel-human-followup-v1"||
     typeof data.source_sha256!=="string"||!/^[0-9a-f]{64}$/.test(data.source_sha256)||
     data.source_sha256===source.fingerprint||!Number.isFinite(older)||
     older>=current||!Array.isArray(data.entries)||data.entries.length>200||
     data.read_only!==true||data.action_authorized!==false||
     !Number.isSafeInteger(data.incident_count)||data.incident_count<0||data.incident_count>200)
    throw Error("source");
  const prior=Object.create(null),matching=[];
  for(const row of data.entries){
    if(!row||typeof row.id!=="string"||!/^[a-f0-9]{32}$/.test(row.id)||
       row.id in prior||!choices.includes(row.status)||
       typeof row.note!=="string"||row.note.length>800||
       /[\u0000-\u0009\u000B-\u001F\u007F]/u.test(row.note))throw Error("entry");
    prior[row.id]=row;
    if(row.note.trim()&&source.incidents.some(x=>x.id===row.id))
      matching.push(row);
  }
  if(!matching.length)throw Error("no-match");
  const message="Er zijn "+matching.length+" oude notities bij identieke incident-ID's. "+
   "Alleen de notities worden overgenomen, nooit eerdere eigenaar-/herstelstatus. "+
   "Iedere overgenomen status wordt 'bron opnieuw verifiëren'. Wil je dit bewust doen?";
  if(!window.confirm(message)){
    $("feedback").textContent="Oude notities niet overgenomen.";
    return;
  }
  const prefix="HISTORISCHE NOTITIE "+data.source_at+"\n";
  for(const row of matching){
    const note=(prefix+row.note).slice(0,800);
    notes[row.id]={status:"bron-opnieuw-verifieren",note};
  }
  $("feedback").textContent=matching.length+" historische notities overgenomen als opnieuw te verifiëren. Geen externe status gewijzigd.";
  render();
 }catch(_){$("feedback").textContent="Oude overdracht geweigerd: bron, tijd, incident-ID of notities niet bruikbaar.";}
 finally{event.target.value="";}
};
let lastBrief=null;
function invalidateBrief(){
 lastBrief=null;
 if($("downloadBrief"))$("downloadBrief").disabled=true;
 if($("briefStatus"))$("briefStatus").textContent="Bron, filter of notities gewijzigd: maak een nieuwe voorvertoning.";
 if($("briefText"))$("briefText").textContent="";
}
function mdText(text){
 // Render labels/notes as inert Markdown text; never turn source input into
 // active links, headings, code fences or raw HTML in the human handoff.
 let value=String(text).replace(/\r/g,"");
 for(const c of ["\\","<",">","[","]","#","*","_","|","`","!","{","}"]){
  value=value.split(c).join("\\"+c);
 }
 return value.replace(/\n/g,"\n> ");
}
function briefScope(){
 const rows=filtered().filter(x=>x.human);
 // Include source identity, each selected human incident and complete
 // local follow-up state in the preview binding. Never imply current claims.
 return JSON.stringify({source:source.fingerprint,time:source.observed_at,
  rows:rows.map(x=>({id:x.id,notes:notes[x.id]||null})),
  filter:[$("owner").value,$("project").value,$("priority").value,$("human").value,$("reviewTarget").value,$("query").value]});
}
function makeBrief(){
 const selectedRows=filtered().filter(x=>x.human);
 if(!selectedRows.length)throw Error("Geen menselijke incidenten in het filter");
 const head=[
 "# zSentinel · privé overdracht voor menselijke opvolging",
 "",
 "> Niet geautoriseerd voor claims, merges, betaling, CI-retries of productieherstel.",
 "> Bron is een gedeeltelijke momentopname; verdwenen/ontbrekende issues zijn niet opgelost.",
 "",
 "- Bron SHA-256: "+source.fingerprint,
 "- Bronmoment UTC: "+source.observed_at,
 "- Huidige filterselectie: "+selectedRows.length+" menselijke incidenten",
 "- Eigenaarfilter: "+mdText($("owner").value||"alle"),
 "- Projectfilter: "+mdText($("project").value||"alle"),
 "- Prioriteitfilter: "+mdText($("priority").value||"alle"),
 "- Reviewdoelfilter: "+mdText($("reviewTarget").value||"alle"),
 "",
 ];
 for(const x of selectedRows){
  const user=notes[x.id]||{status:choices[0],note:""};
  if(!choices.includes(user.status)||typeof user.note!=="string"||
      user.note.length>800||/[\u0000-\u0009\u000B-\u001F\u007F]/u.test(user.note))
    throw Error("Ongeldige privénotitie");
  head.push("## "+mdText(x.priority+" · "+x.project+" · "+x.problem),
    "- Bronincident: "+x.id,
    "- Eigenaar (bronlabel, niet geverifieerd): "+mdText(x.owner),
    "- Actie (bronadvies): "+mdText(x.action),
    "- Feitelijke bronstatus: "+mdText(x.status),
    "- Laatste bronwaarneming: "+x.last,
    "- Beoordelingsdoel (geen agenda): "+x.review_by,
    "- Lokale opvolgstatus (geen eigenaarsbewijs): "+mdText(user.status),
    "- Privénotitie: "+(user.note.trim()?"> "+mdText(user.note):"geen"),"");
 }
 head.push("---","Geen verzending, geautomatiseerde dispatch, autorisatie, ACK of herstelstatuswijziging.","");
 const result=head.join("\n");
 if(new TextEncoder().encode(result).byteLength>500000)throw Error("Briefing groter dan 500 KiB");
 return result;
}
$("previewBrief").onclick=()=>{
 try{
  const md=makeBrief();lastBrief={signature:briefScope(),content:md};
  $("briefText").textContent=md;
  $("briefStatus").textContent="Voorvertoning: "+filtered().filter(x=>x.human).length+
     " bronincidenten. Beoordeel de privé-inhoud voordat je downloadt.";
  $("downloadBrief").disabled=false;
 }catch(_){invalidateBrief();$("briefStatus").textContent="Briefing niet beschikbaar voor dit filter of ongeldige notities.";}
};
$("downloadBrief").onclick=()=>{
 try{
  if(!lastBrief||lastBrief.signature!==briefScope()||lastBrief.content!==makeBrief())
    throw Error("Preview is verouderd");
  const url=URL.createObjectURL(new Blob([lastBrief.content],{type:"text/markdown;charset=utf-8"}));
  const a=el("a");a.href=url;a.download="zsentinel-private-human-briefing.md";
  document.body.append(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  $("briefStatus").textContent="Privébriefing aangeboden. Niets verstuurd of goedgekeurd.";
 }catch(_){invalidateBrief();$("briefStatus").textContent="Voorvertoning verlopen. Controleer filters en maak opnieuw.";}
};
isFresh();render();
</script></html>"""
    # Split the ORIGINAL template before inserting either source-derived
    # value. Never scan an inserted incident/branch value for another marker.
    if (html.count("__BRANCH_PANEL__") != 1 or
            html.count("__RUNTIME_PANEL__") != 1 or
            html.count("__SOURCE__") != 1):
        raise ValueError("offline incident desk template has ambiguous placeholders")
    before_branch, rest = html.split("__BRANCH_PANEL__", 1)
    between_panels, rest = rest.split("__RUNTIME_PANEL__", 1)
    between, after_source = rest.split("__SOURCE__", 1)
    return (before_branch + branch_panel + between_panels + runtime_panel +
            between + payload + after_source)
