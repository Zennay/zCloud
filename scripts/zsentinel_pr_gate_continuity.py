#!/usr/bin/env python3
"""Compare two saved exact-head PR gate observations for human follow-up.

Source-only snapshots; no polling, background retry, claims or release authority.
A repeated blocker at two timestamps is NOT continuous proof between them.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from html import escape
import re

from zsentinel_pr_scope_delta import _clock, MAX_AGE_SECONDS

SCHEMA = "zsentinel-pr-gate-episode-comparison-v1"
BOARD = "zsentinel-portfolio-pr-collision-board-v1"
SHA = re.compile(r"^[a-f0-9]{40}$")
PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
REPO = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")
MAX_ITEMS = 4
MAX_CHECKS = 12

GATE_KEYS = (
    "status", "human_gate_blocker", "draft_at_read", "base_sha_observed",
    "observed_checks_pending", "observed_checks_unknown",
    "observed_checks_not_successful", "observed_legacy_pending",
    "observed_legacy_failure_or_error",
    "observed_independent_exact_head_approvals",
    "observed_exact_head_changes_requested",
    "github_mergeable_boolean_observed", "check_page_complete",
    "check_runs_sample_complete_and_terminal",
    "review_page_may_be_truncated", "legacy_status_source_may_be_truncated",
    "observed_actionable_check_runs_hidden",
    "observed_actionable_legacy_contexts_hidden",
    "check_runs_total_observed", "check_runs_latest_attempts_observed",
    "check_runs_superseded_attempts_observed",
    "legacy_contexts_observed", "legacy_status_superseded_attempts_observed",
    "human_next_gate_step", "check_runs_evidence_sha256",
    "legacy_status_evidence_sha256", "exact_head_review_evidence_sha256",
)


def _project_repositories(board):
    if (type(board) is not dict or board.get("schema") != BOARD
            or any(board.get(k) is not False for k in
                   ("claim_authorized", "merge_authorized",
                    "deploy_authorized", "writes_performed"))
            or type(board.get("projects")) is not list
            or type(board.get("project_views")) is not list
            or not 1 <= len(board["projects"]) <= 3
            or len(board["project_views"]) != len(board["projects"])
            or any(type(project) is not str for project in board["projects"])
            or len(set(board["projects"])) != len(board["projects"])):
        raise ValueError("not a bounded non-authorizing PR board")
    repos = {}
    for project, view in zip(board["projects"], board["project_views"]):
        if (type(project) is not str or not PROJECT.fullmatch(project)
                or type(view) is not dict or view.get("project") != project
                or type(view.get("repository")) is not str
                or not REPO.fullmatch(view["repository"])):
            raise ValueError("invalid project/repository identity")
        repos[project] = view["repository"]
    return repos


def _gate_rows(board, repos):
    gates = board.get("current_live_pr_gate_observation")
    if gates is None:
        return None
    if (type(gates) is not dict or type(gates.get("items")) is not list
            or len(gates["items"]) > MAX_ITEMS
            or any(gates.get(k) is not False for k in
                   ("merge_authorized", "deploy_authorized",
                    "writes_performed"))):
        raise ValueError("invalid bounded exact-head gate source")
    board_time = _clock(board.get("observed_at_utc"))
    gate_time = _clock(gates.get("observed_at_utc"))
    if (gate_time < board_time or gate_time - board_time > timedelta(minutes=15)
            or gate_time > datetime.now(timezone.utc)):
        raise ValueError("gate receipts too old, future, or detached from PR snapshot")
    rows = {}
    for row in gates["items"]:
        if type(row) is not dict:
            raise ValueError("invalid exact-head row")
        project, url = row.get("project"), row.get("url")
        if type(project) is not str or project not in repos or type(url) is not str:
            raise ValueError("gate outside canonical project")
        prefix = "https://github.com/" + repos[project] + "/pull/"
        if not url.startswith(prefix) or not re.fullmatch(r"[1-9][0-9]{0,8}", url[len(prefix):]):
            raise ValueError("noncanonical PR URL")
        if url in rows or row.get("merge_authorized") is not False:
            raise ValueError("duplicate or authorizing gate")
        status = row.get("status")
        if status not in (
                "current_exact_pr_head_and_sample_match",
                "pr_source_unavailable_or_changed_between_observations",
                "direct_selected_pr_unavailable_or_not_product"):
            raise ValueError("unrecognized gate receipt status")
        if type(row.get("human_gate_blocker")) is not str or not 1 <= len(row["human_gate_blocker"]) <= 120:
            raise ValueError("invalid gate blocker")
        if status == "current_exact_pr_head_and_sample_match":
            head = row.get("head_sha_observed")
            base = row.get("base_sha_observed")
            if (type(head) is not str or not SHA.fullmatch(head)
                    or type(base) is not str or not SHA.fullmatch(base)):
                raise ValueError("missing pinned exact HEAD or base")
            for key in ("observed_actionable_check_runs",
                        "observed_actionable_legacy_contexts"):
                val = row.get(key)
                if type(val) is not list or len(val) > MAX_CHECKS:
                    raise ValueError("invalid bounded check records")
            check_ids = set()
            for check in row["observed_actionable_check_runs"]:
                if (type(check) is not dict
                        or type(check.get("check_run_id")) is not int
                        or check["check_run_id"] <= 0
                        or check["check_run_id"] in check_ids
                        or type(check.get("name")) is not str
                        or not 1 <= len(check["name"]) <= 180
                        or any(ord(char) < 32 or ord(char) == 127
                               for char in check["name"])
                        or check.get("state") not in (
                            "completed", "queued", "in_progress",
                            "pending", "waiting", "requested", "unknown")
                        or (check.get("conclusion") is not None and
                            (type(check["conclusion"]) is not str
                             or len(check["conclusion"]) > 40))
                        or check.get("url") != "https://github.com/" +
                            repos[project] + "/runs/" + str(check["check_run_id"])):
                    raise ValueError("invalid pinned GitHub check identity")
                check_ids.add(check["check_run_id"])
            seen_contexts = set()
            for status_row in row["observed_actionable_legacy_contexts"]:
                if (type(status_row) is not dict
                        or type(status_row.get("context")) is not str
                        or not 1 <= len(status_row["context"]) <= 200
                        or any(ord(char) < 32 or ord(char) == 127
                               for char in status_row["context"])
                        or status_row["context"] in seen_contexts
                        or status_row.get("state") not in
                            ("pending", "failure", "error")
                        or status_row.get("url") !=
                            "https://github.com/" + repos[project] +
                            "/commit/" + head + "/checks"):
                    raise ValueError("invalid pinned legacy status context")
                seen_contexts.add(status_row["context"])
            for key in ("observed_actionable_check_runs_hidden",
                        "observed_actionable_legacy_contexts_hidden",
                        "observed_checks_pending",
                        "observed_checks_unknown",
                        "observed_checks_not_successful",
                        "observed_legacy_pending",
                        "observed_legacy_failure_or_error",
                        "observed_independent_exact_head_approvals",
                        "observed_exact_head_changes_requested"):
                val = row.get(key)
                if type(val) is not int or not 0 <= val <= 1000000:
                    raise ValueError("invalid check/review count")
            # Existing old snapshots can lack source identities. They are
            # allowed for comparison, but must NEVER qualify as unchanged.
            for field in ("check_runs_evidence_sha256",
                          "legacy_status_evidence_sha256",
                          "exact_head_review_evidence_sha256"):
                val = row.get(field)
                if val is not None and (type(val) is not str or
                                        SHA.fullmatch(val) is None):
                    raise ValueError("malformed pinned source fingerprint")
            for key in ("check_page_complete", "review_page_may_be_truncated",
                        "legacy_status_source_may_be_truncated",
                        "check_runs_sample_complete_and_terminal"):
                if type(row.get(key)) is not bool:
                    raise ValueError("invalid check source completeness flag")
        rows[url] = row
    return rows


def _proof(row):
    """Canonical mutable observation, not an assurance of required checks."""
    result = {key: row.get(key) for key in GATE_KEYS}
    result["checks"] = sorted(
        (c.get("check_run_id"), c.get("name"), c.get("state"), c.get("conclusion"))
        for c in row["observed_actionable_check_runs"])
    result["legacy"] = sorted(
        (c.get("context"), c.get("state"))
        for c in row["observed_actionable_legacy_contexts"])
    return result


def _changed_non_green(past, now):
    """Only positive differences in the sampled non-green check evidence."""
    if not past or past["status"] != now["status"] or now["status"] != (
            "current_exact_pr_head_and_sample_match"):
        return [], 0
    changes = []
    old_jobs = {c["check_run_id"]: c for c in past["observed_actionable_check_runs"]}
    new_jobs = {c["check_run_id"]: c for c in now["observed_actionable_check_runs"]}
    for job_id in sorted(new_jobs.keys() - old_jobs.keys()):
        job = new_jobs[job_id]
        changes.append({"kind": "non_green_job_newly_observed",
                        "label": job["name"], "url": job["url"]})
    for job_id in sorted(old_jobs.keys() & new_jobs.keys()):
        old_job, new_job = old_jobs[job_id], new_jobs[job_id]
        if old_job != new_job:
            changes.append({"kind": "non_green_job_state_or_conclusion_changed",
                            "label": new_job["name"], "url": new_job["url"]})
    for job_id in sorted(old_jobs.keys() - new_jobs.keys()):
        job = old_jobs[job_id]
        changes.append({"kind": "previous_non_green_job_not_in_current_sample",
                        "label": job["name"], "url": job["url"]})
    old_legacy = {s["context"]: s for s in past["observed_actionable_legacy_contexts"]}
    new_legacy = {s["context"]: s for s in now["observed_actionable_legacy_contexts"]}
    for context in sorted(old_legacy.keys() | new_legacy.keys()):
        old = old_legacy.get(context)
        new = new_legacy.get(context)
        if old != new:
            entry = new or old
            changes.append({
                "kind": ("non_green_legacy_now_observed" if new else
                         "previous_non_green_legacy_not_in_current_sample"),
                "label": context, "url": entry["url"]})
    return changes[:16], max(0, len(changes) - 16)


def compare_gate_episodes(previous, current):
    old_repos = _project_repositories(previous)
    new_repos = _project_repositories(current)
    if old_repos != new_repos:
        raise ValueError("different project set or registry repository")
    old_time = _clock(previous["observed_at_utc"])
    new_time = _clock(current["observed_at_utc"])
    seconds = (new_time - old_time).total_seconds()
    if (seconds < 0 or seconds > MAX_AGE_SECONDS
            or new_time > datetime.now(timezone.utc)):
        raise ValueError("gate observations not ordered or stale beyond seven days")
    old_rows = _gate_rows(previous, old_repos)
    new_rows = _gate_rows(current, new_repos)
    if old_rows is None or new_rows is None:
        return {
            "schema": SCHEMA, "comparison_status": "one_or_both_gate_observations_absent",
            "previous_observed_at_utc": old_time.isoformat(),
            "current_observed_at_utc": new_time.isoformat(), "elapsed_seconds": int(seconds),
            "items": [], "unchanged_observed_holds": 0,
            "attention_needed": 0, "automatic_retry_authorized": False,
            "claim_authorized": False, "merge_authorized": False,
            "writes_performed": False,
        }
    rows = []
    for url, now in new_rows.items():
        past = old_rows.get(url)
        old_head = past.get("head_sha_observed") if past else None
        old_base = past.get("base_sha_observed") if past else None
        head = now.get("head_sha_observed")
        base = now.get("base_sha_observed")
        same_ref = (
            type(head) is str and SHA.fullmatch(head) is not None
            and type(base) is str and SHA.fullmatch(base) is not None
            and head == old_head and base == old_base)
        exact = (past is not None and
                 past["status"] == now["status"] ==
                 "current_exact_pr_head_and_sample_match" and same_ref)
        fully_sampled = exact and all(
            row.get("check_page_complete") is True
            and row.get("review_page_may_be_truncated") is False
            and row.get("legacy_status_source_may_be_truncated") is False
            and row.get("observed_actionable_check_runs_hidden") == 0
            and row.get("observed_actionable_legacy_contexts_hidden") == 0
            and all(type(row.get(key)) is str
                    and SHA.fullmatch(row[key]) is not None for key in (
                        "check_runs_evidence_sha256",
                        "legacy_status_evidence_sha256",
                        "exact_head_review_evidence_sha256"))
            for row in (past, now))
        if past is None:
            change = "newly_selected_in_current_sample_not_new_pr"
        elif not same_ref:
            change = "head_changed_or_source_uncertain"
        elif not fully_sampled:
            change = "gate_source_incomplete_no_stability_claim"
        elif _proof(past) == _proof(now):
            change = "same_exact_head_and_gate_receipts_at_two_observations"
        else:
            change = "gate_receipt_changed_same_head"
        movements, hidden = _changed_non_green(past, now) if exact else ([], 0)
        source_evidence_changes = []
        if exact and fully_sampled:
            for field, human_label in (
                    ("check_runs_evidence_sha256", "Nieuwe Check Run poging of conclusie"),
                    ("legacy_status_evidence_sha256", "Legacy context of statuspoging gewijzigd"),
                    ("exact_head_review_evidence_sha256", "Individuele reviewbon gewijzigd")):
                if past[field] != now[field]:
                    source_evidence_changes.append(human_label)
        rows.append({
            "url": url, "project": now["project"],
            "observed_changed_non_green_checks": movements,
            "observed_changed_non_green_checks_hidden": hidden,
            "observed_changed_gate_evidence_categories": source_evidence_changes,
            "previous_head": old_head if type(old_head) is str and SHA.fullmatch(old_head) else None,
            "current_head": head if type(head) is str and SHA.fullmatch(head) else None,
            "previous_base": old_base if type(old_base) is str and SHA.fullmatch(old_base) else None,
            "current_base": base if type(base) is str and SHA.fullmatch(base) else None,
            "change": change,
            "previous_blocker": past.get("human_gate_blocker") if past else None,
            "current_blocker": now["human_gate_blocker"],
            "safe_to_defer_repeat_only_when_no_new_evidence":
                change == "same_exact_head_and_gate_receipts_at_two_observations",
            "exact_head_required_checks_verified": False,
            "active_owner_claim_verified": False,
            "merge_authorized": False,
        })
    for url in old_rows.keys() - new_rows.keys():
        old = old_rows[url]
        rows.append({
            "url": url, "project": old["project"],
            "previous_head": old.get("head_sha_observed"),
            "current_head": None,
            "previous_base": old.get("base_sha_observed"),
            "current_base": None,
            "observed_changed_non_green_checks": [],
            "observed_changed_non_green_checks_hidden": 0,
            "observed_changed_gate_evidence_categories": [],
            "change": "not_selected_now_not_evidence_of_merge_or_closure",
            "previous_blocker": old["human_gate_blocker"],
            "current_blocker": None,
            "safe_to_defer_repeat_only_when_no_new_evidence": False,
            "exact_head_required_checks_verified": False,
            "active_owner_claim_verified": False,
            "merge_authorized": False,
        })
    rows.sort(key=lambda row: (
        row["safe_to_defer_repeat_only_when_no_new_evidence"],
        row["project"], row["url"]))
    return {
        "schema": SCHEMA, "comparison_status": "two_bounded_observations",
        "previous_observed_at_utc": old_time.isoformat(),
        "current_observed_at_utc": new_time.isoformat(),
        "elapsed_seconds": int(seconds), "items": rows,
        "unchanged_observed_holds": sum(
            row["safe_to_defer_repeat_only_when_no_new_evidence"] for row in rows),
        "attention_needed": sum(
            not row["safe_to_defer_repeat_only_when_no_new_evidence"] for row in rows),
        "never_proves_intermediate_continuity_or_recovery": True,
        "automatic_retry_authorized": False,
        "claim_authorized": False, "merge_authorized": False,
        "writes_performed": False,
    }


LABELS = {
    "newly_selected_in_current_sample_not_new_pr": "Nieuw in selectie; geen bewijs van nieuwe PR",
    "head_changed_or_source_uncertain": "PR-HEAD/doelbasis gewijzigd of bron onzeker; huidige eigenaar controleren",
    "gate_source_incomplete_no_stability_claim": "Onvolledige gatebron; geen stabiel-hold bewijs",
    "same_exact_head_and_gate_receipts_at_two_observations":
        "Zelfde HEAD en gatebewijs op beide bronmomenten; geen ongewijzigde tussenperiode bewezen",
    "gate_receipt_changed_same_head": "Gatebewijs veranderd bij dezelfde HEAD; inspecteer ontwikkeling",
    "not_selected_now_not_evidence_of_merge_or_closure":
        "Niet opnieuw geselecteerd; geen bewijs van merge of afsluiting",
}


def render_gate_episodes(report, style="text"):
    if report is None:
        return ""
    if (type(report) is not dict or report.get("schema") != SCHEMA or
            any(report.get(k) is not False for k in (
                "automatic_retry_authorized", "claim_authorized",
                "merge_authorized", "writes_performed"))):
        raise ValueError("invalid gate episode handoff")
    if style not in ("html", "text", "markdown"):
        raise ValueError("invalid handoff format")
    rows = report["items"]
    if style == "html":
        items = "".join(
            "<li><a rel='noopener noreferrer' href='" +
            escape(row["url"], quote=True) + "'>" + escape(row["url"]) +
            "</a>: " + escape(LABELS[row["change"]]) +
            ("<p>Gewijzigd bronbewijs: " +
             escape("; ".join(row["observed_changed_gate_evidence_categories"])) +
             "</p>" if row["observed_changed_gate_evidence_categories"] else "") +
            ("<ul>" + "".join(
                "<li>" + escape(item["kind"]) + ": <a rel='noopener noreferrer' href='" +
                escape(item["url"], quote=True) + "'>" + escape(item["label"]) +
                "</a></li>" for item in row["observed_changed_non_green_checks"]) +
             "</ul>" if row["observed_changed_non_green_checks"] else "") +
            ("<p>Extra veranderingen buiten voorvertoning: " +
             str(row["observed_changed_non_green_checks_hidden"]) + "</p>"
             if row["observed_changed_non_green_checks_hidden"] else "") +
            "</li>"
            for row in rows)
        return ("<section><h2>PR-gateverandering tussen twee opnamen</h2><p>" +
                str(report["unchanged_observed_holds"]) + " ongewijzigde observaties; " +
                str(report["attention_needed"]) +
                " opnieuw te bekijken; niet bewezen wat daartussen gebeurde.</p>" +
                ("<p>Gatebewijs ontbreekt bij een van beide opnamen; geen vergelijking mogelijk.</p>"
                 if report["comparison_status"] != "two_bounded_observations" else "") +
                "<ul>" +
                (items or "<li>Geen vergelijkbaar gatebewijs.</li>") +
                "</ul><p>GEEN automatische retrigger, claim-, review- of mergevrijgave.</p></section>")
    lines = [
        ("## PR-gateverandering tussen twee opnamen" if style == "markdown"
         else "PR-gateverandering tussen twee opnamen"),
        ("Geen twee vergelijkbare gate-opnamen aanwezig." if
         report["comparison_status"] != "two_bounded_observations" else
         "Twee gate-opnamen aanwezig; uitsluitend bronmomentvergelijking."),
        "Ongewijzigd op twee meetmomenten: " + str(report["unchanged_observed_holds"]) +
        "; opnieuw te bekijken: " + str(report["attention_needed"]) +
        ". Geen bewijs voor de tussenliggende periode.",
    ]
    for row in rows:
        link = ("[Open PR](" + row["url"] + ")" if style == "markdown" else row["url"])
        lines.append("- " + link + " · " + LABELS[row["change"]] +
                     (" · HEAD " + row["current_head"][:12] if row["current_head"] else ""))
        if row["observed_changed_gate_evidence_categories"]:
            lines.append("  Gewijzigd bronbewijs: " +
                         "; ".join(row["observed_changed_gate_evidence_categories"]))
        for detail in row["observed_changed_non_green_checks"]:
            label = detail["label"].replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
            detail_link = ("[" + label + "](" + detail["url"] + ")"
                           if style == "markdown" else detail["label"] + " · " + detail["url"])
            lines.append("  " + detail["kind"] + " · " + detail_link)
        if row["observed_changed_non_green_checks_hidden"]:
            lines.append("  Extra veranderingen niet getoond: " +
                         str(row["observed_changed_non_green_checks_hidden"]))
    lines.append("Geen automatische claim, workflow-herstart, merge of deploy.")
    return "\n".join(lines) + "\n"
