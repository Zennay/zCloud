#!/usr/bin/env python3
"""Read-only GitHub PR/merge/Actions activity for zCloud issue #1518.

Run locally or in an approved backend process. No SQLite, queue, deploy, browser,
credential persistence, HTML scraping, or runtime/owner mutation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"
OWNER = "Zennay"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
PROJECT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
SHA_RE = re.compile(r"^[a-f0-9]{40}$")
MAX_REGISTRY_BYTES = 512 * 1024
MAX_REQUESTS = 30
MAX_PROJECTS = 3
MAX_EVENTS = 200
MAX_PR_PAGES = 5
MAX_CI_PAGES = 2
MAX_COMMIT_PAGES = 2


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def instant(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None or when.utcoffset() is None:
        return None
    return when.astimezone(dt.timezone.utc)


def stamp(value: dt.datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def read_registry(path: Path) -> dict[str, str]:
    """Allowlist from canonical projects.json, never accept client-supplied repo."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REGISTRY_BYTES:
        raise ValueError("canonical project registry unavailable or oversized")
    raw = path.read_bytes()
    if len(raw) > MAX_REGISTRY_BYTES:
        raise ValueError("project registry over limit")
    items = json.loads(raw)
    if not isinstance(items, list):
        raise ValueError("project registry is not an array")
    repositories: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        project, url = item.get("id"), item.get("repo_url")
        if not isinstance(project, str) or not PROJECT_RE.fullmatch(project):
            continue
        if not isinstance(url, str):
            continue
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username or parsed.password:
            continue
        if parsed.port not in (None, 443) or parsed.query or parsed.fragment:
            continue
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != OWNER or not REPO_RE.fullmatch(parts[1]):
            continue
        if project in repositories:
            raise ValueError("duplicate canonical project")
        repositories[project] = OWNER + "/" + parts[1]
    if not repositories:
        raise ValueError("no canonical GitHub project entries")
    return repositories


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a GitHub bearer token to an unexpected redirect.
        return None


class GitHubReader:
    def __init__(self, token: str | None, budget: int = MAX_REQUESTS) -> None:
        self.token = token
        self.remaining = budget
        # A 403 without a confirmed exhausted global quota can be local to a
        # single repository. Do not poison independent portfolio projects.
        self.denied = False
        self.repository_denials: set[str] = set()
        self.opener = urllib.request.build_opener(_NoRedirect)

    def get(self, repo: str, suffix: str, params: dict[str, object]) -> dict:
        if self.denied:
            raise ValueError("not_fetched_after_rate_or_auth_denial")
        if repo in self.repository_denials:
            raise ValueError("not_fetched_after_repository_denial")
        if self.remaining < 1:
            raise ValueError("request_budget_exhausted")
        self.remaining -= 1
        url = API + "/repos/" + urllib.parse.quote(repo, safe="/") + "/" + suffix
        url += "?" + urllib.parse.urlencode(params)
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "zCloud-private-activity-observer",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self.opener.open(request, timeout=8) as response:
                if response.status != 200:
                    raise ValueError("github_response_unavailable")
                # Bound memory regardless of HTTP Content-Length claims.
                payload = response.read(512 * 1024 + 1)
                if len(payload) > 512 * 1024:
                    raise ValueError("github_response_oversized")
                data = json.loads(payload)
        except urllib.error.HTTPError as error:
            # Never emit provider response bodies, authentication or headers.
            if error.code == 404:
                raise ValueError("repository_inaccessible") from None
            if error.code == 401:
                # An invalid bearer token affects all remaining requests.
                self.denied = True
                raise ValueError("github_authentication_required") from None
            if error.code == 429:
                self.denied = True
                raise ValueError("github_rate_limit_confirmed") from None
            if error.code == 403:
                # GitHub can signal primary rate exhaustion using 403.
                # Only its explicit zero remaining header justifies a
                # global stop; ordinary 403 may be repository-specific.
                remaining = (error.headers.get("X-RateLimit-Remaining")
                             if error.headers is not None else None)
                retry_after = (error.headers.get("Retry-After")
                               if error.headers is not None else None)
                # A server-supplied Retry-After is itself a global cooldown
                # instruction even with nonzero primary quota. Do not
                # fan out to other repositories during secondary throttling.
                if remaining == "0" or retry_after is not None:
                    self.denied = True
                    raise ValueError("github_rate_limit_confirmed") from None
                self.repository_denials.add(repo)
                raise ValueError("github_forbidden_or_rate_limit_unknown") from None
            if error.code in (301, 302, 303, 307, 308):
                raise ValueError("github_redirect_refused") from None
            raise ValueError("github_http_unavailable") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ValueError("github_network_unavailable") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("github_payload_invalid") from None
        if not isinstance(data, dict) and not isinstance(data, list):
            raise ValueError("github_payload_invalid")
        return {"data": data}


def bounded_events(
    reader: GitHubReader,
    project: str,
    repo: str,
    since: dt.datetime,
    observed: dt.datetime,
) -> tuple[list[dict], dict]:
    events: list[dict] = []
    errors: list[str] = []
    bounded_main_commit_shas: set[str] = set()
    coverage = {
        "project": project,
        "repository": repo,
        "synced_at": None,
        "observed_at": stamp(observed),
        "status": "partial",
        "github_pr": "unavailable",
        "github_ci": "unavailable",
        "github_branches": "not_synchronized",
        "github_main_commits": "unavailable",
        "local_vps": "not_observed",
        "pages": {"pulls": 0, "actions": 0, "commits": 0},
        "page_limit_reached": {"pulls": False, "actions": False, "commits": False},
        "errors": errors,
    }

    def add(kind: str, identifier: str, source_time: object, **fields: object) -> None:
        when = instant(source_time)
        if when is None:
            raise ValueError("github_timestamp_invalid")
        if when > observed + dt.timedelta(minutes=2):
            raise ValueError("github_future_timestamp")
        if when < since:
            return
        events.append({
            "id": "github:" + repo + ":" + kind + ":" + identifier,
            "project": project,
            "source": "github",
            "kind": kind,
            "source_at": stamp(when),
            "observed_at": stamp(observed),
            **fields,
        })

    try:
        for page in range(1, MAX_PR_PAGES + 1):
            rows = reader.get(repo, "pulls", {
                "state": "all", "sort": "updated", "direction": "desc",
                "per_page": 10, "page": page,
            })["data"]
            if not isinstance(rows, list):
                raise ValueError("github_pulls_invalid")
            for pr in rows:
                if not isinstance(pr, dict) or not isinstance(pr.get("number"), int):
                    raise ValueError("github_pulls_invalid")
                number = pr["number"]
                if isinstance(number, bool) or not 1 <= number <= 100000000:
                    raise ValueError("github_pulls_invalid")
                draft = pr.get("draft")
                if not isinstance(draft, bool):
                    raise ValueError("github_pulls_invalid")
                merged_at = pr.get("merged_at")
                if merged_at is not None and instant(merged_at) is None:
                    raise ValueError("github_timestamp_invalid")
                current_state = pr.get("state")
                closed_at = pr.get("closed_at")
                if current_state not in ("open", "closed"):
                    raise ValueError("github_pr_state_invalid")
                if closed_at is not None and instant(closed_at) is None:
                    raise ValueError("github_timestamp_invalid")
                if (current_state == "open" and
                        (closed_at is not None or merged_at is not None)):
                    raise ValueError("github_pr_state_inconsistent")
                if current_state == "closed" and closed_at is None:
                    raise ValueError("github_pr_closed_time_missing")
                lifecycle = ("merged" if merged_at is not None
                             else "closed_without_merge" if current_state == "closed"
                             else "open")
                merge_sha = pr.get("merge_commit_sha")
                if merged_at is not None:
                    if not isinstance(merge_sha, str) or not SHA_RE.fullmatch(merge_sha):
                        raise ValueError("github_pr_merge_sha_invalid")
                else:
                    merge_sha = None
                # GitHub's PR base ref is the reported target, not proof that
                # its merge commit is reachable from the current canonical main.
                base = pr.get("base")
                target = base.get("ref") if isinstance(base, dict) else None
                if (not isinstance(target, str) or len(target) > 180 or not re.fullmatch(r"[A-Za-z0-9_./-]+", target) or target.startswith("/") or target.endswith("/") or "//" in target or ".." in target):
                    target = None
                head = pr.get("head")
                head_sha = head.get("sha") if isinstance(head, dict) else None
                if not isinstance(head_sha, str) or not SHA_RE.fullmatch(head_sha):
                    raise ValueError("github_pr_head_sha_invalid")
                # Branch refs may contain Unicode, slashes or upstream user text.
                # Export only the immutable SHA, never untrusted branch text.
                ref = f"https://github.com/{repo}/pull/{number}"
                fields = {"pr": number, "url": ref, "draft": draft,
                          "pr_head_sha": head_sha,
                          # This is the current state at observation time,
                          # not a retroactive claim about when PR was created.
                          "pr_lifecycle": lifecycle,
                          "pr_closed_at": closed_at,
                          "merged": merged_at is not None, "main_merged": False,
                          "main_merge_evidence": "not_proven_in_bounded_history",
                          "merge_commit_sha": merge_sha,
                          "reported_target": target,
                          "reported_main_merge": bool(merged_at is not None and target == "main")}
                add("pr_created", str(number), pr.get("created_at"), **fields)
                add("pr_updated", str(number), pr.get("updated_at"), **fields)
                if lifecycle == "closed_without_merge":
                    # Exact provider close time, not a later generic PR update.
                    # A closed PR has no merged-main or deploy implication.
                    add("pr_closed", str(number), closed_at, **fields)
                elif merged_at is not None:
                    # GitHub merged != proven merged into canonical main.
                    add("pr_merged", str(number), merged_at, **fields)
            coverage["pages"]["pulls"] = page
            if len(rows) < 10:
                break
            if page == MAX_PR_PAGES:
                coverage["page_limit_reached"]["pulls"] = True
        coverage["github_pr"] = ("bounded_truncated" if coverage["page_limit_reached"]["pulls"]
                                 else "bounded_window")
        coverage["github_branches"] = "observed_pr_heads_only"
    except ValueError as error:
        errors.append(str(error))
        # Partial pages cannot be treated as complete coverage.
        if coverage["pages"]["pulls"]:
            coverage["github_pr"] = "partial"

    try:
        for page in range(1, MAX_CI_PAGES + 1):
            rows = reader.get(repo, "actions/runs", {
                "per_page": 40, "page": page, "exclude_pull_requests": "false",
            })["data"]
            if not isinstance(rows, dict) or not isinstance(rows.get("workflow_runs"), list):
                raise ValueError("github_actions_invalid")
            runs = rows["workflow_runs"]
            for run in runs:
                if not isinstance(run, dict) or not isinstance(run.get("id"), int):
                    raise ValueError("github_actions_invalid")
                run_id = run["id"]
                status = run.get("status")
                conclusion = run.get("conclusion")
                if status not in {"queued", "in_progress", "waiting", "pending", "completed", "requested"}:
                    raise ValueError("github_actions_invalid")
                if status != "completed" and conclusion is not None:
                    raise ValueError("github_actions_invalid")
                if conclusion is not None and conclusion not in {
                    "success", "failure", "cancelled", "skipped", "timed_out",
                    "action_required", "neutral", "stale", "startup_failure",
                }:
                    raise ValueError("github_actions_invalid")
                sha = run.get("head_sha")
                if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
                    raise ValueError("github_actions_invalid")
                add("ci_run", str(run_id), run.get("updated_at"), run_id=run_id,
                    sha=sha, status=status, conclusion=conclusion,
                    url=f"https://github.com/{repo}/actions/runs/{run_id}",
                    deploy_verified=False, worker_resumed=False)
            coverage["pages"]["actions"] = page
            if len(runs) < 40:
                break
            if page == MAX_CI_PAGES:
                coverage["page_limit_reached"]["actions"] = True
        coverage["github_ci"] = ("bounded_truncated" if coverage["page_limit_reached"]["actions"]
                                 else "bounded_window")
    except ValueError as error:
        errors.append(str(error))
        if coverage["pages"]["actions"]:
            coverage["github_ci"] = "partial"

    try:
        for page in range(1, MAX_COMMIT_PAGES + 1):
            rows = reader.get(repo, "commits", {
                "sha": "main", "per_page": 20, "page": page,
            })["data"]
            if not isinstance(rows, list):
                raise ValueError("github_commits_invalid")
            for commit in rows:
                if not isinstance(commit, dict):
                    raise ValueError("github_commits_invalid")
                sha = commit.get("sha")
                details = commit.get("commit")
                if not isinstance(sha, str) or not SHA_RE.fullmatch(sha) or not isinstance(details, dict):
                    raise ValueError("github_commits_invalid")
                committer = details.get("committer")
                if not isinstance(committer, dict):
                    raise ValueError("github_commits_invalid")
                bounded_main_commit_shas.add(sha)
                add("main_commit", sha, committer.get("date"),
                    sha=sha, main_merged=True,
                    url=f"https://github.com/{repo}/commit/{sha}",
                    deploy_verified=False, worker_resumed=False)
            coverage["pages"]["commits"] = page
            if len(rows) < 20:
                break
            if page == MAX_COMMIT_PAGES:
                coverage["page_limit_reached"]["commits"] = True
        coverage["github_main_commits"] = (
            "bounded_truncated" if coverage["page_limit_reached"]["commits"]
            else "bounded_window")
    except ValueError as error:
        errors.append(str(error))
        if coverage["pages"]["commits"]:
            coverage["github_main_commits"] = "partial"

    # A GitHub-reported PR merge is not proof that its exact merge commit is
    # reachable from canonical main. A matching SHA in the bounded main
    # commit history is positive reachability evidence, NOT live deployment.
    for entry in events:
        if entry["kind"] not in {"pr_created", "pr_updated", "pr_merged"}:
            continue
        if (entry.get("merged") is True
                and entry.get("reported_target") == "main"
                and entry.get("merge_commit_sha") in bounded_main_commit_shas):
            entry["main_merged"] = True
            entry["main_merge_evidence"] = "canonical_main_history_exact_sha"
        else:
            entry["main_merge_evidence"] = "not_proven_in_bounded_history"
    coverage["main_merge_absence_is_unverified"] = True
    # A source denial is an operator action, not evidence of zero activity.
    # No provider response body, raw URL or authorization header is retained.
    if "github_authentication_required" in errors:
        coverage["provider_admission"] = "authentication_required"
    elif "github_rate_limit_confirmed" in errors:
        coverage["provider_admission"] = "rate_limit_confirmed"
    elif "github_forbidden_or_rate_limit_unknown" in errors:
        coverage["provider_admission"] = "repository_or_secondary_limit_unknown"
    elif "not_fetched_after_rate_or_auth_denial" in errors:
        coverage["provider_admission"] = "global_admission_denied_prior_project"
    else:
        coverage["provider_admission"] = "none"
    # Distinguish a GitHub-reported merge target from a main-history commit.
    # These are different observations and must never count twice as shipped.
    coverage["github_pr_target_is_reported"] = True

    if not errors:
        coverage["status"] = "partial_sources"  # GitHub branch coverage absent.
        coverage["synced_at"] = stamp(observed)
    elif any(coverage[source] != "unavailable" for source in ("github_pr", "github_ci", "github_main_commits")):
        coverage["status"] = "partial_error"
        coverage["synced_at"] = stamp(observed)
    else:
        coverage["status"] = "unavailable"
    return events, coverage


def load_previous(path: Path, repositories: dict[str, str],
                  observed: dt.datetime) -> dict[str, dt.datetime]:
    """Use a prior local receipt for earliest-seen facts, never as authority."""
    import stat as _stat
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            if not _stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
                raise ValueError("previous_receipt_invalid")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                blob = stream.read(1024 * 1024 + 1)
            if len(blob) > 1024 * 1024:
                raise ValueError("previous_receipt_oversized")
        finally:
            os.close(fd)
    except OSError:
        raise ValueError("previous_receipt_unavailable") from None
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError("previous_receipt_duplicate_keys")
            result[k] = v
        return result
    payload = json.loads(blob, object_pairs_hook=unique)
    if not isinstance(payload, dict) or payload.get("schema") != "zcloud-github-activity-readonly-v1":
        raise ValueError("previous_receipt_invalid")
    if payload.get("feed_complete") is not False or any(
        payload.get(k) is not False for k in ("authorizes_repair", "authorizes_merge", "authorizes_deploy")
    ):
        raise ValueError("previous_receipt_authority_invalid")
    when = instant(payload.get("observed_at"))
    if when is None or when > observed or observed - when > dt.timedelta(days=30):
        raise ValueError("previous_receipt_time_invalid")
    events = payload.get("events")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("previous_receipt_events_invalid")
    earliest = {}
    for row in events:
        if not isinstance(row, dict):
            raise ValueError("previous_receipt_event_invalid")
        project = row.get("project")
        identifier = row.get("id")
        repo = repositories.get(project) if isinstance(project, str) else None
        if not repo or not isinstance(identifier, str) or len(identifier) > 256:
            raise ValueError("previous_receipt_scope_invalid")
        if not identifier.startswith("github:" + repo + ":"):
            raise ValueError("previous_receipt_source_invalid")
        first = instant(row.get("first_observed_at") or row.get("observed_at"))
        if first is None or first > when or first > observed:
            raise ValueError("previous_receipt_event_time_invalid")
        if identifier in earliest:
            raise ValueError("previous_receipt_duplicate_event")
        earliest[identifier] = first
    return earliest


def collect(registry: Path, selected: list[str], hours: int, max_events: int,
            token: str | None, now: dt.datetime | None = None,
            previous: Path | None = None) -> dict:
    canonical = read_registry(registry)
    if not selected:
        raise ValueError("select at least one --project")
    if len(selected) > MAX_PROJECTS or len(set(selected)) != len(selected):
        raise ValueError("invalid project selection count")
    if any(p not in canonical for p in selected):
        raise ValueError("unknown canonical project")
    if not 1 <= hours <= 168 or not 1 <= max_events <= MAX_EVENTS:
        raise ValueError("invalid bounded window")
    observed = now or utc_now()
    prior = load_previous(previous, canonical, observed) if previous is not None else None
    since = observed - dt.timedelta(hours=hours)
    reader = GitHubReader(token)
    results: list[dict] = []
    coverage: list[dict] = []
    for project in selected:
        rows, status = bounded_events(reader, project, canonical[project], since, observed)
        results.extend(rows)
        coverage.append(status)
    deduped = {row["id"]: row for row in results}
    for item in deduped.values():
        first_seen = prior.get(item["id"]) if prior is not None else None
        item["first_observed_at"] = stamp(first_seen or observed)
        item["new_since_previous"] = (item["id"] not in prior) if prior is not None else None
    events = sorted(deduped.values(), key=lambda x: (x["source_at"], x["id"]), reverse=True)
    # Interleave four bounded source categories within each project. A noisy
    # PR-update or CI stream cannot consume every slot before a genuine
    # GitHub close-without-merge is made visible to the operator.
    buckets = {(project, kind): [] for project in selected
               for kind in ("pr", "closed", "ci", "commit")}
    for item in events:
        category = ("ci" if item["kind"] == "ci_run" else
                    "commit" if item["kind"] == "main_commit" else
                    "closed" if item["kind"] == "pr_closed" else "pr")
        buckets[(item["project"], category)].append(item)
    fair = []
    positions = {key: 0 for key in buckets}
    while len(fair) < max_events:
        advanced = False
        for project in selected:
            for category in ("pr", "closed", "ci", "commit"):
                key = (project, category)
                index = positions[key]
                if index >= len(buckets[key]):
                    continue
                fair.append(buckets[key][index])
                positions[key] += 1
                advanced = True
                if len(fair) >= max_events:
                    break
            if len(fair) >= max_events:
                break
        if not advanced:
            break
    # The browser treats imported receipts as untrusted. Only carry a
    # positive recent-main indicator when the exact canonical main commit
    # event survives this *same bounded exported receipt*. Without it the
    # UI must display "not proven" instead of a bare true boolean.
    included_main = {
        (item["project"], item["sha"]) for item in fair
        if item["kind"] == "main_commit"
    }
    for item in fair:
        if item["kind"] not in {"pr_created", "pr_updated", "pr_merged"}:
            continue
        if item.get("main_merged") is True and (
            item["project"], item.get("merge_commit_sha")
        ) not in included_main:
            item["main_merged"] = False
            item["main_merge_evidence"] = "main_sha_not_in_exported_window"
    for item in coverage:
        item["verified_pr_main_merges_in_window"] = len({
            row.get("pr") for row in fair
            if row["project"] == item["project"] and row["kind"] == "pr_merged"
            and row.get("main_merged") is True
        })
        item["main_merge_absence_is_unverified"] = True
    return {
        "schema": "zcloud-github-activity-readonly-v1",
        "observed_at": stamp(observed),
        "hours": hours,
        "requested_projects": selected,
        "requests_used": MAX_REQUESTS - reader.remaining,
        "events": fair,
        "previous_snapshot_compared": prior is not None,
        "truncated": len(events) > max_events,
        "coverage": coverage,
        "feed_complete": False,  # no branch/backfill/runtime proof
        "authorizes_repair": False,
        "authorizes_merge": False,
        "authorizes_deploy": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only, bounded GitHub activity, no credentials in output")
    parser.add_argument("--registry", type=Path,
                        default=Path(__file__).resolve().parents[1] / "projects.json")
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--max-events", type=int, default=120)
    parser.add_argument("--previous", type=Path, help="Read-only prior JSON activity receipt, up to 30 days old")
    args = parser.parse_args(argv)
    try:
        result = collect(args.registry, args.project, args.hours, args.max_events,
                         os.environ.get("GITHUB_TOKEN") or None, previous=args.previous)
    except (ValueError, OSError, json.JSONDecodeError):
        # File names / exception text must not leak paths or secrets.
        print('{"error":"invalid_registry_or_request","feed_complete":false}', file=sys.stdout)
        return 2
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0 if all(x["status"] == "partial_sources" for x in result["coverage"]) else 3


if __name__ == "__main__":
    raise SystemExit(main())
