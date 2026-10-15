#!/usr/bin/env python3
"""Read-only zCloud production evidence cockpit; it never grants release permission.

Produces sanitized JSON and an offline HTML operator view. This is an advisory
snapshot, not a replacement for #580/PWQ-41, #1089 or runner/runtime approval.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "Zennay/zCloud"
API = "https://api.github.com"
EXPECTED_RUNNER = "zcloud-vps-1"
EXPECTED_LABELS = {"self-hosted", "zcloud", "vps"}
SHA = re.compile(r"^[a-f0-9]{40}$")
MAX_BYTES = 2_000_000


class EvidenceError(Exception):
    """Invalid, incomplete, or unstable GitHub source evidence."""


def require_sha(value: object) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise EvidenceError("invalid_commit_sha")
    return value


def integer(value: object) -> int:
    if type(value) is not int or value < 1:
        raise EvidenceError("invalid_positive_integer")
    return value


def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate_json_key")
        result[key] = value
    return result


def nonfinite(_: str) -> object:
    raise EvidenceError("nonfinite_json_number")


def reject_unpaired_unicode(value: object) -> object:
    """Reject escaped surrogate codepoints in bounded GitHub JSON evidence.

    The iterative walk avoids recursive decoding limits while keeping all
    error codes static and therefore safe for an operator artifact.
    """
    pending = [value]
    visited = 0
    while pending:
        item = pending.pop()
        visited += 1
        if visited > MAX_BYTES:
            raise EvidenceError("oversized_api_structure")
        if isinstance(item, str):
            if any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                raise EvidenceError("invalid_api_unicode")
        elif isinstance(item, dict):
            for key, val in item.items():
                pending.extend((key, val))
        elif isinstance(item, list):
            pending.extend(item)
    return value


class GitHubReadOnly:
    def __init__(self, token: str | None = None):
        self.token = token

    def get(self, path: str) -> object:
        if not path.startswith("/repos/Zennay/zCloud/"):
            raise EvidenceError("noncanonical_api_path")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "zcloud-read-only-release-evidence",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(API + path, headers=headers, method="GET")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as r:
                if r.status != 200:
                    raise EvidenceError("unexpected_http_status")
                raw = r.read(MAX_BYTES + 1)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise EvidenceError("github_transport_unavailable") from exc
        if len(raw) > MAX_BYTES:
            raise EvidenceError("oversized_api_response")
        try:
            parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs,
                                parse_constant=nonfinite)
            return reject_unpaired_unicode(parsed)
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise EvidenceError("invalid_api_json") from exc


def main_sha(api) -> str:
    item = api.get("/repos/Zennay/zCloud/git/ref/heads/main")
    if not isinstance(item, dict) or not isinstance(item.get("object"), dict):
        raise EvidenceError("missing_main_ref")
    if item["object"].get("type") != "commit":
        raise EvidenceError("main_ref_not_commit")
    return require_sha(item["object"].get("sha"))


def production_status(api, sha: str) -> dict:
    latest = None
    count = 0
    seen_status_ids = set()
    status_events = []
    for page in range(1, 6):
        payload = api.get(
            f"/repos/Zennay/zCloud/commits/{sha}/statuses?per_page=100&page={page}"
        )
        if not isinstance(payload, list) or len(payload) > 100:
            raise EvidenceError("incomplete_production_status_page")
        for row in payload:
            if not isinstance(row, dict):
                raise EvidenceError("malformed_production_status")
            if row.get("context") != "zcloud/vps-production":
                continue
            state = row.get("state")
            if state not in ("pending", "success", "failure", "error"):
                raise EvidenceError("unknown_production_state")
            stamp = row.get("created_at")
            if not isinstance(stamp, str) or not re.fullmatch(
                r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp
            ):
                raise EvidenceError("invalid_production_timestamp")
            status_id = integer(row.get("id"))
            if status_id in seen_status_ids:
                raise EvidenceError("duplicate_production_status_id")
            seen_status_ids.add(status_id)
            status_events.append((status_id, stamp, state))
            try:
                timestamp = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError as exc:
                raise EvidenceError("invalid_production_timestamp") from exc
            age = (dt.datetime.now(dt.timezone.utc) - timestamp).total_seconds()
            if age < -60:
                raise EvidenceError("future_production_evidence")
            key = (timestamp, status_id)
            if latest is None or key > latest[0]:
                latest = (key, state, max(0, int(age)))
            count += 1
        if len(payload) < 100:
            break
    else:
        raise EvidenceError("production_status_pagination_cap")
    return {
        "state": latest[1] if latest else "missing",
        "matching_events": count,
        "last_status_age_seconds": latest[2] if latest else None,
        "latest_status_id": latest[0][1] if latest else None,
        "latest_status_at_utc": latest[0][0].isoformat() if latest else None,
        # Bind the entire observed context set, not only the newest event:
        # older failures/pending statuses may shift without changing "latest".
        "status_events_digest": hashlib.sha256(
            json.dumps(sorted(status_events), separators=(",", ":"))
            .encode("utf-8")
        ).hexdigest(),
    }


def current_deploy(api, sha: str) -> dict:
    run_url = (
        "/repos/Zennay/zCloud/actions/workflows/zcloud-vps-deploy.yml/runs"
        "?branch=main&per_page=100&page="
    )
    runs = []
    seen_page_run_ids = set()
    page_snapshots = []
    reported_total = None
    total_count_field_present = None
    for page in range(1, 6):
        payload = api.get(f"{run_url}{page}")
        if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
            raise EvidenceError("invalid_workflow_runs")
        batch = payload["workflow_runs"]
        has_total = "total_count" in payload
        if total_count_field_present is None:
            total_count_field_present = has_total
        elif has_total != total_count_field_present:
            raise EvidenceError("inconsistent_deploy_total_count_presence")
        if has_total:
            count_value = payload["total_count"]
            if type(count_value) is not int or count_value < 0:
                raise EvidenceError("invalid_deploy_total_count")
            if reported_total is not None and reported_total != count_value:
                raise EvidenceError("changing_deploy_total_count")
            reported_total = count_value
        elif reported_total is not None:
            raise EvidenceError("missing_deploy_total_count")
        if len(batch) > 100:
            raise EvidenceError("oversized_workflow_page")
        page_snapshots.append(payload)
        for row in batch:
            if not isinstance(row, dict):
                raise EvidenceError("invalid_workflow_run")
            run_id = integer(row.get("id"))
            if run_id in seen_page_run_ids:
                raise EvidenceError("duplicate_paginated_deploy_run")
            seen_page_run_ids.add(run_id)
            runs.append(row)
        if len(batch) < 100:
            inventory_complete = True
            break
    else:
        # The repository can legitimately contain thousands of deploy runs.
        # Keep a bounded advisory window with an explicit incomplete marker;
        # do not misrepresent it as exhaustive or turn it into release authority.
        inventory_complete = False
    pages_scanned = page
    if reported_total is not None and (
        reported_total < len(runs) or
        (inventory_complete and reported_total != len(runs))
    ):
        raise EvidenceError("inconsistent_deploy_total_count")

    def verify_paginated_run_snapshots():
        # Offset pagination can shift as runs arrive: require every page to
        # re-read identically, not only page 1. This is still not an atomic
        # GitHub snapshot, so no release authority can follow from equality.
        for number, original in enumerate(page_snapshots, 1):
            if api.get(f"{run_url}{number}") != original:
                raise EvidenceError("deploy_run_page_drift")

    matching = []
    seen_matching_runs = set()
    for row in runs:
        if not isinstance(row, dict):
            raise EvidenceError("invalid_workflow_run")
        if row.get("head_sha") != sha:
            continue
        if row.get("head_branch") != "main" or row.get("event") != "workflow_run":
            raise EvidenceError("unexpected_deploy_provenance")
        run_id = integer(row.get("id"))
        attempt = integer(row.get("run_attempt"))
        if (run_id, attempt) in seen_matching_runs:
            raise EvidenceError("duplicate_deploy_run_identity")
        seen_matching_runs.add((run_id, attempt))
        status = row.get("status")
        conclusion = row.get("conclusion")
        if status not in ("queued", "in_progress", "completed", "waiting", "pending", "requested"):
            raise EvidenceError("unrecognized_deploy_run_status")
        if conclusion is not None and conclusion not in (
            "success", "failure", "cancelled", "skipped", "timed_out",
            "action_required", "neutral", "stale", "startup_failure",
        ):
            raise EvidenceError("unrecognized_deploy_conclusion")
        if status == "completed" and conclusion is None:
            raise EvidenceError("completed_deploy_missing_conclusion")
        if status != "completed" and conclusion is not None:
            raise EvidenceError("nonterminal_deploy_has_conclusion")
        updated = row.get("updated_at")
        if not isinstance(updated, str) or not re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", updated
        ):
            raise EvidenceError("invalid_deploy_updated_at")
        try:
            updated_time = dt.datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EvidenceError("invalid_deploy_updated_at") from exc
        age = (dt.datetime.now(dt.timezone.utc) - updated_time).total_seconds()
        if age < -60:
            raise EvidenceError("future_deploy_evidence")
        matching.append((run_id, attempt, status, conclusion, max(0, int(age)), updated))
    if not matching:
        verify_paginated_run_snapshots()
        return {
            "run_id": None, "attempt": None, "status": "not_observed",
            "conclusion": None, "runner_route": "UNVERIFIED",
            "matching_deploys_in_bounded_inventory": 0, "run_age_seconds": None,
            "runner_job_result": "NOT_OBSERVED",
            "run_pages_scanned": pages_scanned,
            "runs_scanned": len(runs),
            "run_inventory_count_verified": reported_total is not None and inventory_complete,
            "run_inventory_complete": inventory_complete,
        }
    run_id, attempt, status, conclusion, run_age_seconds, updated_at = max(matching, key=lambda x: (x[0], x[1]))
    job_url = f"/repos/Zennay/zCloud/actions/runs/{run_id}/jobs?per_page=100&page=1"
    jobs_payload = api.get(job_url)
    if not isinstance(jobs_payload, dict) or not isinstance(jobs_payload.get("jobs"), list):
        raise EvidenceError("invalid_deploy_jobs")
    total = jobs_payload.get("total_count")
    if type(total) is not int or total < 0 or total > 100:
        raise EvidenceError("incomplete_deploy_jobs")
    jobs = jobs_payload["jobs"]
    if len(jobs) != total:
        raise EvidenceError("deploy_job_count_mismatch")
    found = []
    outcomes = []
    seen_job_ids = set()
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("labels"), list):
            raise EvidenceError("malformed_job")
        job_id = integer(job.get("id"))
        if job_id in seen_job_ids:
            raise EvidenceError("duplicate_deploy_job")
        seen_job_ids.add(job_id)
        if (type(job.get("run_id")) is not int or job["run_id"] != run_id or
            type(job.get("run_attempt")) is not int or job["run_attempt"] != attempt or
            job.get("head_sha") != sha):
            raise EvidenceError("mismatched_deploy_job_identity")
        labels = job["labels"]
        if not all(isinstance(label, str) for label in labels):
            raise EvidenceError("malformed_runner_labels")
        if not EXPECTED_LABELS.issubset(set(labels)):
            continue
        job_status = job.get("status")
        if job_status not in ("queued", "in_progress", "completed", "waiting", "pending"):
            raise EvidenceError("unknown_job_status")
        job_conclusion = job.get("conclusion")
        if job_status == "completed":
            if job_conclusion not in (
                "success", "failure", "cancelled", "skipped", "timed_out",
                "action_required", "neutral", "stale", "startup_failure",
            ):
                raise EvidenceError("invalid_terminal_job_conclusion")
            outcomes.append("SUCCESS" if job_conclusion == "success" else "FAILED")
        else:
            if job_conclusion is not None:
                raise EvidenceError("nonterminal_job_has_conclusion")
            outcomes.append("NOT_TERMINAL")
        runner_id = job.get("runner_id")
        runner_name = job.get("runner_name")
        if runner_id is not None and (type(runner_id) is not int or runner_id < 0):
            raise EvidenceError("malformed_runner_id")
        if runner_name is not None and not isinstance(runner_name, str):
            raise EvidenceError("malformed_runner_name")
        if runner_id and runner_name == EXPECTED_RUNNER:
            found.append("ASSIGNED_TO_EXPECTED")
        elif runner_id or runner_name:
            found.append("UNEXPECTED_RUNNER")
        elif job_status == "queued":
            found.append("QUEUED_UNASSIGNED")
        else:
            found.append("UNVERIFIED")
    # Any unexpected route or unassigned queue prevents an optimistic summary.
    priority = ("UNEXPECTED_RUNNER", "QUEUED_UNASSIGNED",
                "UNVERIFIED", "ASSIGNED_TO_EXPECTED")
    runner_route = next((name for name in priority if name in found),
                        "NO_SELF_HOSTED_JOB")
    if "FAILED" in outcomes:
        runner_job_result = "FAILED"
    elif "NOT_TERMINAL" in outcomes:
        runner_job_result = "NOT_TERMINAL"
    elif outcomes and all(x == "SUCCESS" for x in outcomes):
        runner_job_result = "SUCCESS"
    else:
        runner_job_result = "NOT_OBSERVED"
    # A job-list GET can race with a new attempt or with the workflow finishing.
    # Re-fetch the exact run after every job has been inspected, never silently
    # combine a stale list conclusion with newer job state.
    post = api.get(f"/repos/Zennay/zCloud/actions/runs/{run_id}")
    if not isinstance(post, dict) or (
        type(post.get("id")) is not int or post["id"] != run_id or
        type(post.get("run_attempt")) is not int or
        post["run_attempt"] != attempt or
        post.get("head_sha") != sha or
        post.get("head_branch") != "main" or
        post.get("event") != "workflow_run" or
        post.get("status") != status or
        post.get("conclusion") != conclusion or
        post.get("updated_at") != updated_at
    ):
        raise EvidenceError("deploy_run_changed_during_job_read")
    # The run may remain unchanged even while its jobs acquire a runner or
    # finish. Re-read the exact selected job page after the run read; a mixed
    # pre/post job snapshot must never appear as stable job evidence.
    if api.get(job_url) != jobs_payload:
        raise EvidenceError("deploy_job_changed_during_observation")
    # Revalidate every page before reporting one combined run snapshot.
    verify_paginated_run_snapshots()
    return {
        "run_id": run_id, "attempt": attempt, "status": status,
        "conclusion": conclusion, "runner_route": runner_route,
        "runner_job_result": runner_job_result,
        "matching_deploys_in_bounded_inventory": len(matching),
        "run_pages_scanned": pages_scanned,
        "runs_scanned": len(runs),
        "run_inventory_count_verified": reported_total is not None and inventory_complete,
        "run_inventory_complete": inventory_complete,
        "run_age_seconds": run_age_seconds,
    }


def operator_next_checks(warnings: list[str]) -> list[str]:
    """Bounded safe next observations; guidance never authorizes mutation."""
    checks = {
        "production_not_success":
            "Controleer de exacte main-SHA productiestatus en de bijbehorende "
            "deploylog; herstart of herbewapen niets automatisch.",
        "production_observation_stale":
            "De laatste productie-statusmelding is oud of ontbreekt; vergelijk de "
            "huidige main-SHA met een nieuwe onafhankelijke bronobservatie.",
        "deploy_run_inventory_incomplete":
            "De deployrunhistorie bevat meer dan 500 vermeldingen; dit rapport "
            "toont slechts een begrensde, onvolledige periode. Interpreteer "
            "een ontbrekende run niet als bewijs dat er geen deploy was.",
        "runner_route_not_proven":
            "Controleer in GitHub de repository-toegang, runnerlabels en "
            "jobtoewijzing; een oude of wachtrij-job bewijst geen actieve listener.",
        "deploy_observation_stale_or_missing":
            "Ververs eerst de alleen-lezen GitHub-observatie en vergelijk dezelfde "
            "main-SHA voordat je conclusies trekt.",
        "runner_job_not_success":
            "Controleer de afzonderlijke VPS-jobuitkomst op dezelfde run, poging "
            "en commit; eerdere toewijzing alleen betekent geen geslaagde uitvoering.",
        "deploy_run_not_success":
            "Bekijk de exacte workflow-run en de afzonderlijke safe-idle- en "
            "database-logmarkers; leid geen oorzaak af uit één mislukte jobnaam.",
    }
    result = [checks[key] for key in checks if key in warnings]
    if not result:
        result = [
            "Laat een onafhankelijke eigenaar runtime, bronconflicten en de "
            "geserialiseerde releasepoort beoordelen; dit is geen deploygoedkeuring."
        ]
    return result


def collect(api) -> dict:
    sha = main_sha(api)
    prod = production_status(api, sha)
    deploy = current_deploy(api, sha)
    # Never combine an older green status with a newer failed deployment.
    # A second GET is not an atomic transaction, but changing status-event
    # identities or counts must invalidate the entire evidence snapshot.
    prod_final = production_status(api, sha)
    stable = ("state", "matching_events", "latest_status_id", "latest_status_at_utc",
              "status_events_digest")
    if any(prod[key] != prod_final[key] for key in stable):
        raise EvidenceError("production_status_changed_during_observation")
    if main_sha(api) != sha:
        raise EvidenceError("main_moved_during_observation")
    healthy_production_observed = prod["state"] == "success"
    warnings = []
    if not healthy_production_observed:
        warnings.append("production_not_success")
    if prod["last_status_age_seconds"] is None or prod["last_status_age_seconds"] > 900:
        warnings.append("production_observation_stale")
    if not deploy["run_inventory_complete"]:
        warnings.append("deploy_run_inventory_incomplete")
    if deploy["runner_route"] != "ASSIGNED_TO_EXPECTED":
        warnings.append("runner_route_not_proven")
    if deploy["run_age_seconds"] is None or deploy["run_age_seconds"] > 900:
        warnings.append("deploy_observation_stale_or_missing")
    if deploy["runner_job_result"] != "SUCCESS":
        warnings.append("runner_job_not_success")
    if deploy["conclusion"] != "success":
        warnings.append("deploy_run_not_success")
    return {
        "schema": "zcloud-deploy-ops-evidence-v1",
        "repository": REPO, "main_sha": sha,
        "observed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "production": prod, "deploy": deploy, "warnings": warnings,
        "next_checks": operator_next_checks(warnings),
        "observation": "GREEN_STATUS_IS_NOT_RELEASE_PERMISSION",
        "release_authorized": False, "deployment_authorized": False,
        "mutations_performed": False,
    }


WARNING_LABELS_NL = {
    "production_not_success": "Geen bevestigde geslaagde productiestatus voor deze commit",
    "production_observation_stale": "Laatste productiestatus is te oud of ontbreekt",
    "deploy_run_inventory_incomplete": "Deploygeschiedenis is onvolledig (maximaal 500 runs)",
    "runner_route_not_proven": "Toewijzing aan de vaste VPS-runner niet aangetoond",
    "deploy_observation_stale_or_missing": "Deploybewijs is verouderd of ontbreekt",
    "runner_job_not_success": "VPS-job is niet aantoonbaar geslaagd",
    "deploy_run_not_success": "Deployworkflow is niet aantoonbaar geslaagd",
}


def render_html(snapshot: dict) -> str:
    def safe(value: object) -> str:
        return html.escape(str(value), quote=True)
    p, d = snapshot["production"], snapshot["deploy"]
    source_links = []
    # Source navigation is derived solely from canonical immutable identifiers.
    # Never create links from remote workflow/job user-controlled strings.
    sha = snapshot.get("main_sha")
    if isinstance(sha, str) and SHA.fullmatch(sha):
        source_links.append(
            '<a href="https://github.com/Zennay/zCloud/commit/'
            + safe(sha) + '">Geobserveerde main-commit</a>'
        )
    run_id = d.get("run_id")
    if type(run_id) is int and run_id > 0:
        source_links.append(
            '<a href="https://github.com/Zennay/zCloud/actions/runs/'
            + str(run_id) + '">GitHub-deployrun</a>'
        )
    evidence_links = " · ".join(source_links) or "Geen geverifieerde bronlink"
    warnings = "".join(
        f"<li>{safe(WARNING_LABELS_NL.get(w, w))}</li>"
        for w in snapshot["warnings"]
    )
    next_checks = "".join(f"<li>{safe(c)}</li>" for c in snapshot["next_checks"])
    return """<!doctype html><html lang="nl"><meta charset="utf-8">
<title>zCloud deploy-ops evidence</title>
<style>body{max-width:850px;margin:2rem auto;padding:1rem;font:16px system-ui,sans-serif;
line-height:1.6;background:#f8f8fa;color:#171c25}article{background:white;padding:1.5rem;
border:1px solid #d3d6dd;border-radius:12px}dt{font-weight:bold;margin-top:1rem}
dd{margin-left:0;overflow-wrap:anywhere}.warning{font-weight:700}</style>
<article><h1>zCloud · releasebewijs</h1>
<p class="warning">Alleen lezen. Geen toestemming voor merge, restart of productie-deploy.</p>
<dl>
<dt>Exact geobserveerde main</dt><dd>""" + safe(snapshot["main_sha"]) + """</dd>
<dt>Bronnen (alleen lezen)</dt><dd>""" + evidence_links + """</dd>
<dt>Productie-status</dt><dd>""" + safe(p["state"]) + """</dd>
<dt>Leeftijd productiestatus (seconden)</dt><dd>""" + safe(p["last_status_age_seconds"]) + """</dd>
<dt>Laatste deploy (huidige SHA, begrensd volledig opgehaalde pagina's)</dt><dd>run """ + safe(d["run_id"]) + """ · poging """ + safe(d["attempt"]) + """ · """ + safe(d["status"]) + """ · """ + safe(d["conclusion"]) + """</dd>
<dt>Doorgenomen deployruns</dt><dd>""" + safe(d["runs_scanned"]) + """ runs in """ + safe(d["run_pages_scanned"]) + """ pagina's · volledige historie: """ + safe("JA" if d["run_inventory_complete"] else "NEE (gedeeltelijk)") + """</dd>
<dt>VPS-runnerbewijs</dt><dd>""" + safe(d["runner_route"]) + """</dd>
<dt>VPS-jobuitkomst</dt><dd>""" + safe(d["runner_job_result"]) + """</dd>
<dt>Leeftijd laatste deploybewijs (seconden)</dt><dd>""" + safe(d["run_age_seconds"]) + """</dd></dl>
<h2>Ontbrekend of afwijkend bewijs</h2><ul>""" + (warnings or "<li>Geen lokale waarschuwingen; onafhankelijke releasepoort blijft verplicht.</li>") + """</ul>
<h2>Veilige volgende controles</h2><ol>""" + next_checks + """</ol>
<p>Geen live healthcheck van Firefox, SQLite, workers of VPS verricht. 
Groene GitHub-status is geen bewijs van actuele runtimegezondheid.</p>
<p>Bevoegd tot deploy: <strong>NEE</strong></p></article></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default="deploy-ops-evidence.json")
    parser.add_argument("--html", default="deploy-ops-evidence.html")
    args = parser.parse_args(argv)
    try:
        data = collect(GitHubReadOnly(os.getenv("GITHUB_TOKEN")))
    except EvidenceError as exc:
        # No exception details, tokens, URLs, headers, or arbitrary responses.
        data = {
            "schema": "zcloud-deploy-ops-evidence-v1", "repository": REPO,
            "main_sha": "UNVERIFIED", "observed_utc": dt.datetime.now(
                dt.timezone.utc).isoformat(),
            "production": {"state": "unknown", "matching_events": 0,
                           "last_status_age_seconds": None,
                           "latest_status_id": None, "latest_status_at_utc": None,
                           "status_events_digest": None},
            "deploy": {"run_id": None, "attempt": None, "status": "not_observed",
                       "conclusion": None, "runner_route": "UNVERIFIED",
                       "run_age_seconds": None, "runner_job_result": "NOT_OBSERVED",
                       "run_pages_scanned": 0, "runs_scanned": 0,
                       "run_inventory_count_verified": False,
                       "run_inventory_complete": False},
            "warnings": [str(exc)],
            "next_checks": [
                "GitHub-bewijs is onvolledig of onbereikbaar: onderzoek uitsluitend "
                "met leesrechten en vraag eigenaarstoestemming voor verdere stappen."
            ],
            "observation": "INCOMPLETE_EVIDENCE",
            "release_authorized": False, "deployment_authorized": False,
            "mutations_performed": False,
        }
        status = 2
    else:
        status = 0 if not data["warnings"] else 1
    Path(args.json).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    Path(args.html).write_text(render_html(data), encoding="utf-8")
    # Only fixed internal error codes are emitted, never raw API payloads,
    # authenticated URLs or HTTP exception text.
    reason = data["warnings"][0] if status == 2 else "none"
    print(f"zCloud deploy-ops evidence: {data['main_sha']} "
          f"warnings={len(data['warnings'])} reason={reason} authorized=false")
    return status


if __name__ == "__main__":
    sys.exit(main())
