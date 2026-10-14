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
        if not isinstance(token, str) or not 1 <= len(token) <= 4096 or any(c in token for c in "\r\n\x00"):
            raise InventoryError("invalid_token")
        self.token = token
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(), _NoRedirect())

    def get(self, path, *, max_bytes=4 * 1024 * 1024):
        if not isinstance(path, str) or not path.startswith("/repos/Zennay/zCloud/"):
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


def collect(api_get, *, clock=None):
    """Fetch main before/after a complete queued-run + job snapshot, GET only."""
    before = _main(api_get)
    run_route = "/repos/Zennay/zCloud/actions/runs?status=queued&per_page=100"
    runs = _pages(api_get, run_route, "workflow_runs", max_pages=_MAX_RUN_PAGES,
                  max_count=_MAX_RUNS, query={"status": "queued", "per_page": "100"})
    mapped = []
    for run in runs:
        if type(run) is not dict or not _integer(run.get("id")):
            raise InventoryError("invalid_run_id")
        job_route = "/repos/Zennay/zCloud/actions/runs/" + str(run["id"]) + "/jobs?per_page=100"
        jobs = _pages(api_get, job_route, "jobs", max_pages=_MAX_JOB_PAGES,
                      max_count=_MAX_JOBS_PER_RUN, query={"per_page": "100"})
        if not jobs or any(type(job) is not dict for job in jobs):
            raise InventoryError("run_without_valid_job_evidence")
        mapped.append({
            "id": run["id"], "repository": (run.get("head_repository") or {}).get("full_name"),
            "head_sha": run.get("head_sha"), "head_branch": run.get("head_branch"),
            "workflow_id": run.get("workflow_id"), "event": run.get("event"),
            "status": run.get("status"), "jobs_complete": True,
            "jobs": [{"id": j.get("id"), "status": j.get("status"), "labels": j.get("labels")}
                     for j in jobs if type(j) is dict],
        })
    # Catch a queue replacement on the leading page, not merely Git main drift.
    latest, latest_link = api_get(run_route + "&page=1")
    initial_ids = [r.get("id") for r in runs[:_PAGE_SIZE] if type(r) is dict]
    latest_ids = [r.get("id") for r in latest.get("workflow_runs", [])
                  if type(r) is dict] if type(latest.get("workflow_runs")) is list else None
    if (latest.get("total_count") != len(runs) or latest_ids != initial_ids
            or _next_link(latest_link, expected_path=run_route.split("?")[0], expected_page=1,
                          required_query={"status": "queued", "per_page": "100"})
            != (len(runs) > _PAGE_SIZE)):
        raise InventoryError("queue_changed_during_inventory")
    after = _main(api_get)
    if after != before:
        raise InventoryError("main_moved_during_inventory")
    when = clock or datetime.now(timezone.utc)
    if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset().total_seconds() != 0:
        raise InventoryError("invalid_clock")
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
    try:
        snapshot = collect(ReadOnlyAPI(token).get)
        answer = policy.assess(snapshot, expected_main_sha=snapshot["main_sha_before"],
                               now=datetime.now(timezone.utc))
    except (InventoryError, OSError, TypeError, AttributeError, ValueError, RecursionError):
        answer = policy._fail("github_inventory_untrusted")
    print(json.dumps(answer, sort_keys=True, separators=(",", ":")))
    # Even observational review output is not a privileged-workflow admission.
    return 0 if answer["decision"] == "NOOP" else 1


if __name__ == "__main__":
    raise SystemExit(main())
