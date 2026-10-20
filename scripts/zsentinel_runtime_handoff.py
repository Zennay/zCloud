#!/usr/bin/env python3
"""One-shot private owner handoff from an already assembled zSentinel decision.

No provider reads or control-plane writes. Handoff items are suggestions only:
even an apparent free slot, expired lease or green CI is NOT authorization.
"""
from __future__ import annotations

import json
import re
from html import escape
from datetime import datetime, timezone

SAFE_SHA = re.compile(r"^[a-f0-9]{40}$")
SAFE_REPOSITORY = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")
SCHEMA = "zsentinel-runtime-owner-handoff-v1"
MAX_ITEMS = 24
MAX_TEXT = 220
SAFE_PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
SAFE_ISSUE = re.compile(r"^https://github\.com/Zennay/[A-Za-z0-9_.-]+/issues/[1-9][0-9]{0,8}$")
SAFE_PR = re.compile(r"^https://github\.com/Zennay/[A-Za-z0-9_.-]+/pull/[1-9][0-9]{0,8}$")


def _short(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        return "ONBEKEND"
    return " ".join(value.split())[:limit] or "ONBEKEND"


def _project(value):
    if not isinstance(value, str) or not SAFE_PROJECT.fullmatch(value):
        raise ValueError("invalid handoff project identity")
    return value


def _card(priority, project, kind, action, *, source="observed_partial", ref=None):
    if priority not in ("P0", "P1", "P2") or not isinstance(kind, str):
        raise ValueError("invalid handoff priority or kind")
    card = {
        "priority": priority,
        "project": _project(project),
        "kind": kind,
        "next_step": _short(action),
        "evidence": source,
        "claim_authorized": False,
        "retry_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "owner_verification_required": True,
    }
    if ref is not None and isinstance(ref, str) and SAFE_ISSUE.fullmatch(ref):
        card["issue_url"] = ref
    return card


def _attach_verified_job(card, raw, registry, review_by):
    """Human navigation to one observed job; never authorizes a CI/release action."""
    if review_by is not None:
        if type(review_by) is not str or len(review_by) > 40:
            raise ValueError("invalid incident review deadline")
        try:
            deadline = datetime.fromisoformat(review_by.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid incident review deadline") from exc
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise ValueError("naive incident review deadline")
        due_utc = deadline.astimezone(timezone.utc)
        card["review_by_utc"] = due_utc.isoformat(timespec="seconds")
        card["review_overdue_at_export"] = due_utc < datetime.now(timezone.utc)
    if raw is None:
        return card
    if (type(raw) is not dict
            or set(raw) != {"run_id", "job_id", "head_sha"}
            or type(raw["run_id"]) is not int
            or type(raw["job_id"]) is not int
            or not 1 <= raw["run_id"] <= 2**53 - 1
            or not 1 <= raw["job_id"] <= 2**53 - 1
            or type(raw["head_sha"]) is not str
            or not SAFE_SHA.fullmatch(raw["head_sha"])):
        raise ValueError("invalid exact GitHub incident job")
    card["exact_github_job"] = dict(raw)
    if registry is not None:
        repo = registry.get(card["project"]) if type(registry) is dict else None
        if type(repo) is not str or not SAFE_REPOSITORY.fullmatch(repo):
            raise ValueError("no canonical repository for verified job")
        base = "https://github.com/" + repo
        card["exact_github_job_url"] = (base + "/actions/runs/" +
            str(raw["run_id"]) + "/job/" + str(raw["job_id"]))
        card["exact_github_commit_url"] = base + "/commit/" + raw["head_sha"]
    return card


def build_handoff(decision, registry=None):
    """Prioritize exact incidents, owner/lease conflicts, and open product work.

    The handoff does not infer absence of work from a missing/partial source.
    Limited source samples are not promoted into a complete project state.
    """
    if not isinstance(decision, dict):
        raise ValueError("missing source-bound recovery decision")
    sources = decision.get("live_sources")
    incidents = decision.get("incidents")
    if not isinstance(sources, dict) or not isinstance(incidents, list) or len(incidents) > 200:
        raise ValueError("handoff requires assembled live source provenance")
    cards = []
    for item in incidents:
        if not isinstance(item, dict) or item.get("severity") not in ("P0", "P1", "P2"):
            raise ValueError("malformed fresh incident")
        cards.append(_attach_verified_job(
            _card(item["severity"], item.get("project_id"),
                  "fresh_incident", item.get("action"),
                  source="bounded_fresh_incident"),
            item.get("exact_github_job"), registry, item.get("target_review_by")))
    worker = decision.get("worker_progress_observation")
    worker_coverage = "not_requested"
    if worker is not None:
        if (not isinstance(worker, dict)
                or worker.get("schema") != "zsentinel-worker-progress-observation-v1"
                or worker.get("source") != "existing_sqlite_mode_ro_query_only_indexed_runner_events"
                or worker.get("actions_performed") is not False
                or worker.get("live_worker_claims_verified") is not False
                or worker.get("worker_resumed_verified") is not False
                or worker.get("runner_restart_authorized") is not False):
            raise ValueError("untrusted worker progress source")
        worker_coverage = _short(worker.get("coverage"), 80)
        followups = worker.get("owner_followup_candidates")
        if not isinstance(followups, list) or len(followups) > 24:
            raise ValueError("unbounded worker followup list")
        seen = set()
        for item in followups:
            if (not isinstance(item, dict)
                    or item.get("owner_claim_verified") is not False
                    or item.get("recovery_authorized") is not False
                    or item.get("continuation_verified") is not False
                    or type(item.get("worker_slot")) is not int
                    or not 1 <= item["worker_slot"] <= 16):
                raise ValueError("untrusted worker owner followup")
            project = _project(item.get("project_id"))
            key = (project, item["worker_slot"])
            if key in seen:
                raise ValueError("duplicate worker owner followup")
            seen.add(key)
            cls = item.get("classification")
            priority = ("P0" if cls == "allocated_paused_with_claimed_queue_owner_gate"
                        else "P1")
            cards.append(_card(priority, project, "worker_owner_review",
                               "Worker " + str(item["worker_slot"]) + ": " +
                               _short(item.get("safe_followup"), 150),
                               source="indexed_browser_event_and_queue_sample"))
    pressure = decision.get("live_queue_pressure")
    queue_coverage = "not_requested_or_unavailable"
    if pressure is not None:
        if (not isinstance(pressure, dict)
                or pressure.get("claim_ownership_verified") is not False):
            raise ValueError("untrusted queue pressure contract")
        queue_coverage = _short(pressure.get("coverage", pressure.get("source")), 80)
        # An aggregate must never invent a new owner or translate attempts to failures.
        if pressure.get("queue_write_authorized") is True:
            raise ValueError("queue write authority cannot come from a read-only handoff")
    issue = decision.get("open_product_issue_triage")
    issue_coverage = "not_requested_or_unavailable"
    if issue is not None:
        if not isinstance(issue, dict):
            raise ValueError("invalid open issue triage")
        records = issue.get("issues", [])
        if not isinstance(records, list) or len(records) > 60:
            raise ValueError("unbounded issue sample")
        if issue.get("source") == "github_readonly_issue_inbox":
            issue_coverage = "bounded_" + _short(issue.get("coverage"), 30)
        elif records:
            raise ValueError("issues cannot appear without read-only GitHub source")
        seen_urls = set()
        for row in records:
            if not isinstance(row, dict):
                raise ValueError("invalid issue row")
            priority = row.get("priority")
            url = row.get("issue_url")
            if priority not in ("P0", "P1") or not isinstance(url, str):
                continue
            if url in seen_urls or not SAFE_ISSUE.fullmatch(url):
                continue
            seen_urls.add(url)
            card = _card(priority, row.get("project_id"), "open_product_issue",
                         row.get("action") or "Controleer bestaande open PR en eigenaar",
                         source="bounded_open_issue_sample", ref=url)
            related = row.get("open_pr_candidates", [])
            if not isinstance(related, list) or len(related) > 3:
                raise ValueError("invalid related product pull request inventory")
            origin = url.rsplit("/issues/", 1)[0] + "/pull/"
            if any(not isinstance(pr, str) or not SAFE_PR.fullmatch(pr)
                   or not pr.startswith(origin) for pr in related):
                raise ValueError("untrusted cross-repo product PR link")
            card["existing_pr_urls"] = sorted(set(related))
            card["review_existing_pr_first"] = bool(related)
            cards.append(card)
    # Historical backlog is a DIFFERENT source class. Never splice it into
    # fresh incidents, issue intake, live alerts or worker assignment.
    backlog = decision.get("historical_owner_recheck_candidates", [])
    old = decision.get("historical_unresolved")
    historical = []
    if (type(backlog) is not list or len(backlog) > 5):
        raise ValueError("invalid bounded historical owner recheck source")
    if backlog:
        if (type(old) is not dict
                or old.get("schema") != "zsentinel-historical-backlog-v1"
                or old.get("source_partial") is not True
                or old.get("action_authorized") is not False
                or type(old.get("shown")) is not list
                or len(old["shown"]) > 30):
            raise ValueError("historical handoff needs canonical unresolved ledger")
        historical_source = {row.get("incident_id"): row for row in old["shown"]
                             if type(row) is dict}
        if len(historical_source) != len(old["shown"]):
            raise ValueError("duplicate historical ledger incident ID")
        seen_history = set()
        for row in backlog:
            if (type(row) is not dict or type(row.get("incident_id")) is not str
                    or not re.fullmatch("[0-9a-f]{32}", row["incident_id"])
                    or row["incident_id"] in seen_history
                    or row.get("source_status") !=
                        "historical_not_observed_needs_owner_recheck"
                    or row.get("current_source_observed") is not False
                    or row.get("notifier_delivery_authorized") is not False
                    or row.get("recovery_action_authorized") is not False
                    or row.get("owner_confirmation_required") is not True
                    or row.get("severity_at_last_observation") not in ("P0", "P1")
                    or type(row.get("next_step")) is not str):
                raise ValueError("untrusted historical owner recheck")
            seen_history.add(row["incident_id"])
            prior = historical_source.get(row["incident_id"])
            if (prior is None
                    or prior.get("source_status") !=
                        "not_observed_needs_reconciliation"
                    or prior.get("current_source_observed") is not False
                    or prior.get("mutation_authorized") is not False
                    or prior.get("human_escalation_historical") is not True
                    or prior.get("project_id") != row.get("project_id")
                    or prior.get("severity") != row["severity_at_last_observation"]
                    or prior.get("problem") != row.get("problem_at_last_observation")
                    or prior.get("last_seen") != row.get("source_last_seen")
                    or prior.get("exact_github_job") != row.get("exact_github_job")):
                raise ValueError("historical owner source contradicts private ledger")
            original = row.get("exact_github_job")
            if original is not None:
                if (type(original) is not dict
                        or original.get("project_id") != row["project_id"]):
                    raise ValueError("cross-project historical GitHub job")
                fields = {key: original.get(key) for key in
                          ("run_id", "job_id", "head_sha")}
            else:
                fields = None
            card = _card(row["severity_at_last_observation"],
                         row["project_id"], "historical_owner_recheck",
                         row["next_step"], source="prior_private_ledger_not_current")
            card = _attach_verified_job(card, fields, registry, None)
            last = row["source_last_seen"]
            if type(last) is not str or len(last) > 40:
                raise ValueError("invalid historical last seen")
            try:
                parsed = datetime.fromisoformat(last.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("invalid historical last seen") from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("historical last seen needs timezone")
            card.update({"historical_source_last_seen_utc":
                         parsed.astimezone(timezone.utc).isoformat(timespec="seconds"),
                         "current_source_observed": False,
                         "historical_reconciliation_required": True,
                         "live_alert_authorized": False})
            historical.append(card)
    # Priority before kind: make owner/incident work visible before new intake.
    order = {"P0": 0, "P1": 1, "P2": 2}
    kind_order = {"fresh_incident": 0, "worker_owner_review": 1, "open_product_issue": 2}
    cards.sort(key=lambda c: (order[c["priority"]], kind_order[c["kind"]],
                              c.get("review_by_utc", "9999"),
                              0 if c.get("existing_pr_urls") else 1,
                              c["project"], c.get("issue_url", ""), c["next_step"]))
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "schema": SCHEMA,
        "created_at_utc": stamp,
        "coverage": {"github": _short(sources.get("github_jobs"), 80)
                     if isinstance(sources.get("github_jobs"), str)
                     else "bounded_exact_job_receipts_only",
                     "queue": queue_coverage, "workers": worker_coverage,
                     "issues": issue_coverage, "notion": _short(sources.get("notion"), 80)},
        "priority_items": cards[:MAX_ITEMS],
        "historical_owner_rechecks": historical,
        "historical_source_incomplete": (
            old.get("source_partial") is True if type(old) is dict else True),
        "historical_rechecks_truncated": (
            decision.get("historical_recheck_source_truncated") is True),
        "items_truncated": len(cards) > MAX_ITEMS,
        "all_sources_complete": False,
        "owner_and_current_claim_verified": False,
        "human_review_required": True,
        "actions_performed": False,
        "worker_resumed_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
    }


def _md(value, limit=MAX_TEXT):
    """Neutralize untrusted GitHub/worker text inside Markdown handoffs."""
    value = _short(value, limit)
    for marker in ("\\", chr(96), "*", "_", "{", "}", "[", "]",
                   "(", ")", "<", ">", "#", "!", "|"):
        value = value.replace(marker, "\\" + marker)
    return value


def handoff_html(report):
    """Script-free P0-first owner strip for the existing live cockpit."""
    if (not isinstance(report, dict) or report.get("schema") != SCHEMA
            or report.get("actions_performed") is not False
            or report.get("owner_and_current_claim_verified") is not False):
        raise ValueError("untrusted operator handoff")
    items = report.get("priority_items")
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError("unbounded operator handoff")
    cards = []
    for item in items:
        if (not isinstance(item, dict) or item.get("claim_authorized") is not False
                or item.get("retry_authorized") is not False
                or item.get("merge_authorized") is not False
                or item.get("deploy_authorized") is not False):
            raise ValueError("authorizing handoff item rejected")
        url = item.get("issue_url")
        link = (' <a href="' + escape(url, quote=True) +
                '" target="_blank" rel="noopener noreferrer">Bestaand issue</a>'
                if isinstance(url, str) and SAFE_ISSUE.fullmatch(url) else '')
        prs = item.get("existing_pr_urls", [])
        if not isinstance(prs, list) or len(prs) > 3 or any(
                not isinstance(p, str) or not SAFE_PR.fullmatch(p) for p in prs):
            raise ValueError("invalid issue-linked PR handoff")
        pr_links = "".join(' <a href="' + escape(p, quote=True) +
                           '" target="_blank" rel="noopener noreferrer">Bestaande PR</a>'
                           for p in prs)
        job = item.get("exact_github_job")
        job_url = item.get("exact_github_job_url")
        commit_url = item.get("exact_github_commit_url")
        if job is not None:
            if (type(job) is not dict
                    or set(job) != {"run_id", "job_id", "head_sha"}
                    or not SAFE_SHA.fullmatch(str(job.get("head_sha", "")))):
                raise ValueError("invalid private handoff job")
            if job_url is not None and (
                    type(job_url) is not str or not job_url.startswith("https://github.com/Zennay/")
                    or not job_url.endswith("/actions/runs/" + str(job["run_id"]) +
                                           "/job/" + str(job["job_id"]))):
                raise ValueError("untrusted private handoff job link")
            if commit_url is not None and (
                    type(commit_url) is not str or not commit_url.startswith("https://github.com/Zennay/")
                    or not commit_url.endswith("/commit/" + job["head_sha"])):
                raise ValueError("untrusted private handoff commit link")
        elif job_url is not None or commit_url is not None:
            raise ValueError("unbound private handoff link")
        verified_links = ""
        for target, label in ((job_url, "Exacte job"), (commit_url, "Exacte commit")):
            if target:
                verified_links += (' <a href="' + escape(target, quote=True) +
                                   '" target="_blank" rel="noopener noreferrer">' +
                                   label + '</a>')
        due = item.get("review_by_utc")
        if due is not None and (type(due) is not str or len(due) > 35):
            raise ValueError("untrusted review deadline")
        overdue = item.get("review_overdue_at_export")
        if overdue is not None and type(overdue) is not bool:
            raise ValueError("untrusted overdue classification")
        due_label = (" · DEADLINE VERSTREKEN" if overdue else "") + (
            " · Beoordelen vóór " + escape(due) if due else "")
        cards.append('<li><strong>' + escape(_short(item.get("priority"), 5)) +
                     ' · ' + escape(_short(item.get("project"))) + '</strong> — ' +
                     escape(_short(item.get("next_step"))) + due_label + link + pr_links + verified_links + '</li>')
    coverage = report.get("coverage", {})
    if not isinstance(coverage, dict) or len(coverage) > 8:
        raise ValueError("invalid handoff source coverage")
    sources = " · ".join(
        escape(_short(name, 20)) + ': ' + escape(_short(value, 75))
        for name, value in coverage.items())
    return ('<section class="panel warn" aria-label="Prioritaire eigenaaroverdracht">'
            '<h2>Nu opvolgen · P0 eerst</h2><p>Actuele, begrensde '
            'bronwaarneming. GEEN vrijgave voor workerclaim, retry, merge of deploy.</p>'
            '<p class="small muted">Bronbereik: ' + sources + '</p>' +
            ('<ol>' + ''.join(cards) + '</ol>' if cards else
             '<p>Geen actie in het onderzochte venster; geen compleet herstelbewijs.</p>') +
            ('<p>Werkbak afgekapt; extra items blijven buiten dit venster.</p>'
             if report.get("items_truncated") is True else '') +
            '</section>')


def markdown(report):
    if (not isinstance(report, dict) or report.get("schema") != SCHEMA
            or report.get("actions_performed") is not False
            or report.get("merge_authorized") is not False):
        raise ValueError("invalid handoff report")
    lines = ["# zSentinel · actuele eigenaaroverdracht",
             "Bronmoment: " + _md(report.get("created_at_utc"), 40),
             "",
             "**Alleen brongebonden advies.** Geen claim, CI-herstart, merge, "
             "VPS-herstel of deploy uitgevoerd of toegestaan.",
             "",
             "## Bronbereik (nooit volledig)"]
    for label, value in report["coverage"].items():
        lines.append("- " + _md(label, 24) + ": " + _md(value, 100))
    lines.append("")
    lines.append("## Prioritaire opvolging")
    for n, card in enumerate(report["priority_items"], 1):
        lines.append(str(n) + ". **" + card["priority"] + " " +
                     _md(card["project"]) + "** · " + _md(card["kind"]) +
                     " · " + _md(card["next_step"]))
        if card.get("issue_url"):
            lines.append("   Bestaand issue: " + card["issue_url"])
        if card.get("review_by_utc"):
            lines.append("   Uiterlijk beoordelen (UTC): " + _md(card["review_by_utc"], 40))
            if card.get("review_overdue_at_export") is True:
                lines.append("   DEADLINE VERSTREKEN volgens exportklok; eigenaar controleren.")
        job = card.get("exact_github_job")
        if job:
            lines.append("   Exacte bron (geen actierecht): run " +
                         str(job["run_id"]) + ", job " + str(job["job_id"]) +
                         ", commit " + job["head_sha"])
            if card.get("exact_github_job_url"):
                lines.append("   GitHub-job: " + card["exact_github_job_url"])
                lines.append("   GitHub-commit: " + card["exact_github_commit_url"])
        for pr in card.get("existing_pr_urls", []):
            lines.append("   Eerst bestaande PR beoordelen: " + pr)
    if not report["priority_items"]:
        lines.append("Geen actie in de beperkte steekproef; brongebrek is geen groen licht.")
    if report["items_truncated"]:
        lines.append("Meer items niet getoond; geen volledige werkvoorraad.")
    lines.extend(["", "Eigenaar moet actuele claims, exacte PR-head, CI, reviews "
                  "en releasebevoegdheid apart verifiëren.", ""])
    return "\n".join(lines)


def serialize(report, suffix):
    if suffix == ".md":
        return markdown(report)
    if suffix == ".json":
        return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    raise ValueError("private handoff output must be .md or .json")
