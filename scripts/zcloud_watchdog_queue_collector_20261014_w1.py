#!/usr/bin/env python3
"""Bounded GET-only GitHub queue evidence collector; never cancels or restarts.

A complete GET inventory still does not establish identical dispatch intent or
grant mutation authority. Use with the watchdog admission policy (#1269).
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import signal
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler, HTTPRedirectHandler

try:
    from scripts import zcloud_watchdog_queue_admission_20261014_w1 as policy
except ModuleNotFoundError:
    import zcloud_watchdog_queue_admission_20261014_w1 as policy

REPO = "Zennay/zCloud"
API_ROOT = "https://api.github.com"
_MAX_RUNS = 500
_MAX_RUN_PAGES = 5
_MAX_JOBS_PER_RUN = 300
_MAX_JOB_PAGES = 3
_PAGE_SIZE = 100
_SHA = re.compile(r"^[0-9a-f]{40}$")
# Fixed API route family: this token may NEVER be redirected to other repo
# endpoints, downloaded artifacts, arbitrary URLs, or mutation endpoints.
_ALLOWED_GET_PATH = re.compile(
    r"^/repos/Zennay/zCloud/(?:"
    r"branches/main"
    r"|actions/runs\?status=queued&per_page=100&page=[1-9][0-9]*"
    r"|actions/runs/[1-9][0-9]*/jobs\?per_page=100&page=[1-9][0-9]*"
    r")$"
)


# Only published stable categories are exposed. No exception text, URLs,
# labels, tokens, worker names or raw GitHub data may reach operator output.
_ERROR_CATEGORIES = {
    "invalid_total_count": "incomplete_queue_pages",
    "unstable_or_invalid_page": "incomplete_queue_pages",
    "empty_nonterminal_page": "incomplete_queue_pages",
    "truncated_inventory": "incomplete_queue_pages",
    "pagination_cap_reached": "incomplete_queue_pages",
    "untrusted_next_cursor": "incomplete_queue_pages",
    "duplicate_next_link": "incomplete_queue_pages",
    "duplicate_or_invalid_paged_identity": "invalid_job_identity",
    "cross_run_job_evidence": "invalid_job_identity",
    "invalid_run_provenance": "invalid_job_identity",
    "run_without_valid_job_evidence": "invalid_job_identity",
    "candidate_job_changed_during_inventory": "job_state_changed",
    "candidate_recheck_cap_exceeded": "queue_too_large",
    "excess_inventory": "queue_too_large",
    "queue_changed_during_inventory": "job_state_changed",
    "main_moved_during_inventory": "main_changed",
    "snapshot_acquisition_too_slow": "observation_too_slow",
    "collector_budget_exhausted": "observation_too_slow",
    "collector_wallclock_exceeded": "observation_too_slow",
}


def safe_error_category(error):
    """Return only a fixed public reason; never reflect untrusted exceptions."""
    code = error.args[0] if isinstance(error, InventoryError) and error.args else None
    # An exception originating from malformed API data may itself contain an
    # unhashable list/dict; do not crash while trying to deny a prior error.
    if type(code) is not str or len(code) > 100:
        return "github_inventory_untrusted"
    return _ERROR_CATEGORIES.get(code, "github_inventory_untrusted")


class InventoryError(ValueError):
    pass


def _integer(value):
    return type(value) is int and value > 0


def _unique_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise InventoryError("duplicate_key")
        obj[key] = value
    return obj


def _no_constant(_):
    raise InventoryError("nonfinite_number")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise InventoryError("api_redirect_denied")


def _next_link(header, *, expected_path, expected_page, required_query):
    if not header:
        return False
    if type(header) is not str or len(header) > 4096:
        raise InventoryError("invalid_link_header")
    matches = []
    for component in header.split(","):
        if "rel=" not in component:
            raise InventoryError("malformed_link")
        if 'rel="next"' not in component and "rel=next" not in component:
            continue
        match = re.fullmatch(r"\s*<([^<>]+)>\s*;\s*rel=(?:\"next\"|next)\s*", component)
        if match is None:
            raise InventoryError("invalid_next_link")
        matches.append(match.group(1))
    if len(matches) > 1:
        raise InventoryError("duplicate_next_link")
    if not matches:
        return False
    url = urlsplit(matches[0])
    if (url.scheme, url.netloc, url.path, url.fragment) != ("https", "api.github.com", expected_path, ""):
        raise InventoryError("cross_origin_next_link")
    values = parse_qs(url.query, keep_blank_values=True, strict_parsing=True)
    permitted = {**required_query, "page": str(expected_page + 1)}
    if values != {key: [value] for key, value in permitted.items()}:
        raise InventoryError("untrusted_next_cursor")
    return True


class ReadOnlyAPI:
    def __init__(self, token):
        # GitHub-issued bearer credentials consist of bounded ASCII token
        # characters. Never let whitespace, Unicode, controls or delimiters
        # enter an HTTP Authorization header; reject before building a Request.
        if (
            type(token) is not str or not 1 <= len(token) <= 4096
            or re.fullmatch(r"[A-Za-z0-9_.-]+", token, flags=re.ASCII) is None
        ):
            raise InventoryError("invalid_token")
        self.token = token
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(), _NoRedirect())

    def get(self, path, *, max_bytes=4 * 1024 * 1024):
        if type(path) is not str or _ALLOWED_GET_PATH.fullmatch(path) is None:
            raise InventoryError("noncanonical_api_path")
        req = Request(API_ROOT + path, method="GET", headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "zcloud-queue-readonly-admission/1",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with self.opener.open(req, timeout=12) as response:
            if response.status != 200:
                raise InventoryError("unexpected_http_status")
            data = response.read(max_bytes + 1)
            link = response.headers.get("Link", "")
        if len(data) > max_bytes:
            raise InventoryError("oversized_api_response")
        try:
            obj = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs,
                             parse_constant=_no_constant)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise InventoryError("invalid_api_json") from exc
        if type(obj) is not dict:
            raise InventoryError("invalid_api_object")
        return obj, link


def _pages(api_get, route, collection, *, max_pages, max_count, query):
    """Page through a bounded trusted route and reject incomplete count or links."""
    items = []
    seen_ids: set[int] = set()
    expected_total = None
    for index in range(1, max_pages + 1):
        path = route + ("&" if "?" in route else "?") + "page=" + str(index)
        payload, link = api_get(path)
        page_items = payload.get(collection)
        total = payload.get("total_count")
        if type(total) is not int or total < 0 or total > max_count:
            raise InventoryError("invalid_total_count")
        if expected_total is None:
            expected_total = total
        if total != expected_total or type(page_items) is not list or len(page_items) > _PAGE_SIZE:
            raise InventoryError("unstable_or_invalid_page")
        more = _next_link(link, expected_path=route.split("?")[0],
                          expected_page=index, required_query=query)
        if more and not page_items:
            raise InventoryError("empty_nonterminal_page")
        # Catch a duplicated or malformed run/job ID before requesting
        # downstream resources. Page overlap is not a complete inventory.
        for record in page_items:
            if (
                type(record) is not dict
                or not _integer(record.get("id"))
                or record["id"] in seen_ids
            ):
                raise InventoryError("duplicate_or_invalid_paged_identity")
            seen_ids.add(record["id"])
        items.extend(page_items)
        if len(items) > max_count:
            raise InventoryError("excess_inventory")
        if not more:
            if len(items) != expected_total:
                raise InventoryError("truncated_inventory")
            return items
    raise InventoryError("pagination_cap_reached")


def _main(api_get):
    item, link = api_get("/repos/Zennay/zCloud/branches/main")
    sha = (item.get("commit") or {}).get("sha")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha) or link:
        raise InventoryError("invalid_main_commit_evidence")
    return sha


def _run_identity(run):
    """Strictly typed run identity; bools must never compare equal to ints."""
    if type(run) is not dict or type(run.get("head_repository")) is not dict:
        raise InventoryError("invalid_run_identity")
    if not _integer(run.get("id")):
        raise InventoryError("invalid_run_id")
    if (
        not _integer(run.get("run_attempt"))
        or not _integer(run.get("workflow_id"))
        or type(run.get("head_sha")) is not str
        or _SHA.fullmatch(run["head_sha"]) is None
        or type(run.get("head_branch")) is not str
        or not run["head_branch"]
        or type(run.get("event")) is not str
        or not run["event"]
        or type(run.get("status")) is not str
        or not run["status"]
        or type(run["head_repository"].get("full_name")) is not str
    ):
        raise InventoryError("invalid_run_identity")
    return (
        run["id"], run["run_attempt"], run["workflow_id"],
        run["head_sha"], run["head_branch"], run["event"],
        run["status"], run["head_repository"]["full_name"],
    )


def _job_signature(job):
    """Strictly typed job identity; omit private logs and display names."""
    if type(job) is not dict:
        raise InventoryError("invalid_candidate_job")
    labels = job.get("labels")
    if type(labels) is not list or any(type(x) is not str for x in labels):
        raise InventoryError("invalid_candidate_labels")
    if not _integer(job.get("id")):
        raise InventoryError("invalid_candidate_job_id")
    runner_id = job.get("runner_id")
    if (
        not _integer(job.get("run_id"))
        or not _integer(job.get("run_attempt"))
        or type(job.get("head_sha")) is not str
        or _SHA.fullmatch(job["head_sha"]) is None
        or type(job.get("head_branch")) is not str
        or not job["head_branch"]
        or type(job.get("status")) is not str
        or not job["status"]
        or not (runner_id is None or (type(runner_id) is int and runner_id >= 0))
    ):
        raise InventoryError("invalid_candidate_job")
    return (
        job["id"], job["run_id"], job["run_attempt"],
        job["head_sha"], job["head_branch"], job["status"],
        tuple(sorted(labels)), runner_id,
    )


def collect(api_get, *, clock=None, monotonic_clock=None):
    """Verify stable queue census and bounded acquisition time; GET only."""
    monotonic = monotonic_clock or time.monotonic
    started_tick = monotonic()
    observed_at = clock if clock is not None else datetime.now(timezone.utc)
    if (
        type(started_tick) not in (int, float) or
        not isinstance(observed_at, datetime) or observed_at.tzinfo is None or
        observed_at.utcoffset().total_seconds() != 0
    ):
        raise InventoryError("invalid_observation_clock")
    before = _main(api_get)
    run_route = "/repos/Zennay/zCloud/actions/runs?status=queued&per_page=100"
    runs = _pages(api_get, run_route, "workflow_runs", max_pages=_MAX_RUN_PAGES,
                  max_count=_MAX_RUNS, query={"status": "queued", "per_page": "100"})
    mapped = []
    # Only runs with queued dedicated-VPS jobs can ever appear in a
    # duplicate-looking advisory group. Retain *bounded identifiers*, not
    # untrusted body/log data, for a second independent job-state read.
    candidate_groups = {}
    for run in runs:
        if type(run) is not dict or not _integer(run.get("id")):
            raise InventoryError("invalid_run_id")
        job_route = "/repos/Zennay/zCloud/actions/runs/" + str(run["id"]) + "/jobs?per_page=100"
        jobs = _pages(api_get, job_route, "jobs", max_pages=_MAX_JOB_PAGES,
                      max_count=_MAX_JOBS_PER_RUN, query={"per_page": "100"})
        if not jobs or any(type(job) is not dict for job in jobs):
            raise InventoryError("run_without_valid_job_evidence")
        # Bind the job to the *same attempt* and source revision as the run.
        # Matching run IDs alone are insufficient for a retried workflow.
        attempt = run.get("run_attempt")
        if (
            not _integer(attempt) or not _integer(run.get("workflow_id"))
            or type(run.get("head_sha")) is not str
            or not _SHA.fullmatch(run["head_sha"])
            or type(run.get("head_branch")) is not str
            or not run["head_branch"]
        ):
            raise InventoryError("invalid_run_provenance")
        for job in jobs:
            if (
                not _integer(job.get("run_id"))
                or job["run_id"] != run["id"]
                or not _integer(job.get("run_attempt"))
                or job["run_attempt"] != attempt
                or job.get("head_sha") != run["head_sha"]
                or job.get("head_branch") != run["head_branch"]
            ):
                raise InventoryError("cross_run_job_evidence")
        if (
            run.get("status") == "queued"
            and all(
                job.get("status") == "queued"
                and type(job.get("labels")) is list
                and {"self-hosted", "zcloud", "vps"}.issubset(set(
                    label for label in job["labels"] if type(label) is str
                ))
                for job in jobs
            )
        ):
            key = (run.get("workflow_id"), run.get("event"),
                   run.get("head_branch"), run.get("head_sha"))
            candidate_groups.setdefault(key, []).append(
                (run["id"], tuple(_job_signature(job) for job in jobs))
            )
        mapped.append({
            "id": run["id"], "repository": (run.get("head_repository") or {}).get("full_name"),
            "head_sha": run.get("head_sha"), "head_branch": run.get("head_branch"),
            "workflow_id": run.get("workflow_id"), "event": run.get("event"),
            "status": run.get("status"), "jobs_complete": True,
            "jobs": [{"id": j.get("id"), "status": j.get("status"), "labels": j.get("labels")}
                     for j in jobs if type(j) is dict],
        })
    # Re-read the COMPLETE run census, not only the first page. A later-page
    # replacement or identical IDs with different claim metadata must deny.
    verified_runs = _pages(api_get, run_route, "workflow_runs",
                           max_pages=_MAX_RUN_PAGES, max_count=_MAX_RUNS,
                           query={"status": "queued", "per_page": "100"})
    if [_run_identity(item) for item in verified_runs] != [
        _run_identity(item) for item in runs
    ]:
        raise InventoryError("queue_changed_during_inventory")
    # The earlier job response can go stale while the run census remains
    # unchanged. Recheck only plausible duplicate groups, still with GETs.
    # No cancellation is authorized even when every comparison succeeds.
    candidate_members = [
        member
        for group in candidate_groups.values() if len(group) > 1
        for member in group
    ]
    if len(candidate_members) > 110:
        raise InventoryError("candidate_recheck_cap_exceeded")
    for run_id, original in candidate_members:
        route = f"/repos/Zennay/zCloud/actions/runs/{run_id}/jobs?per_page=100"
        fresh_jobs = _pages(api_get, route, "jobs", max_pages=_MAX_JOB_PAGES,
                            max_count=_MAX_JOBS_PER_RUN, query={"per_page": "100"})
        if tuple(_job_signature(job) for job in fresh_jobs) != original:
            raise InventoryError("candidate_job_changed_during_inventory")
    after = _main(api_get)
    if after != before:
        raise InventoryError("main_moved_during_inventory")
    span = monotonic() - started_tick
    if type(span) not in (int, float) or not 0 <= span <= 45:
        raise InventoryError("snapshot_acquisition_too_slow")
    # The timestamp is the BEGINNING of the observation, never refreshed at
    # the end to make a slow or partially stale scan appear fresh.
    when = observed_at
    chunks = [mapped[i:i + _PAGE_SIZE] for i in range(0, len(mapped), _PAGE_SIZE)] or [[]]
    return {
        "repository": REPO, "main_sha_before": before, "main_sha_after": after,
        "observed_at": when.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "run_pages": [
            {"runs": chunk, "has_next": index < len(chunks) - 1}
            for index, chunk in enumerate(chunks)
        ],
    }


def main():
    token = os.environ.get("GITHUB_TOKEN", "")
    deadline = time.monotonic() + 115
    requests = 0

    def timeout_handler(_number, _frame):
        raise InventoryError("collector_wallclock_exceeded")

    if hasattr(signal, "setitimer") and hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, 115)

    try:
        reader = ReadOnlyAPI(token)
        def bounded_get(path):
            nonlocal requests
            requests += 1
            if requests > 640 or time.monotonic() >= deadline:
                raise InventoryError("collector_budget_exhausted")
            result = reader.get(path)
            if time.monotonic() >= deadline:
                raise InventoryError("collector_budget_exhausted")
            return result

        snapshot = collect(bounded_get)
        answer = policy.assess(snapshot, expected_main_sha=snapshot["main_sha_before"],
                               now=datetime.now(timezone.utc))
    except InventoryError as exc:
        answer = policy._fail(safe_error_category(exc))
    except (OSError, TypeError, AttributeError, ValueError, RecursionError):
        answer = policy._fail("github_inventory_untrusted")
    finally:
        if hasattr(signal, "setitimer") and hasattr(signal, "SIGALRM"):
            signal.setitimer(signal.ITIMER_REAL, 0)
    print(json.dumps(answer, sort_keys=True, separators=(",", ":")))
    # Even observational review output is not a privileged-workflow admission.
    return 0 if answer["decision"] == "NOOP" else 1


if __name__ == "__main__":
    raise SystemExit(main())
