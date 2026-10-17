"""Bounded, read-only GitHub activity receipts for the zCloud portfolio.

No runtime imports: source collection is opt-in, outside the live queue/SQLite
writer and without deployment, worker dispatch or GitHub mutation privileges.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

MAX_RESPONSE = 2 * 1024 * 1024
MAX_PROJECTS = 12
MAX_REQUESTS = 24
MAX_EVENTS = 480
SLUG = re.compile(r"^https://github\.com/(Zennay)/([A-Za-z0-9_.-]+?)/?$")
SHA = re.compile(r"^[a-fA-F0-9]{40}$")
ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


class ActivitySourceError(Exception):
    """A source failed. Never include an HTTP response body or secret."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ActivitySourceError("github_redirect_refused")


def strict_json(raw):
    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("duplicate JSON field")
            data[key] = value
        return data
    return json.loads(raw, object_pairs_hook=unique)


def stamp(value):
    if not isinstance(value, str) or not ISO.fullmatch(value):
        return None
    try:
        date = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return date.isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def safe_sha(value):
    return value.lower() if isinstance(value, str) and SHA.fullmatch(value) else None


def portfolio_registry(path):
    # Read only the canonical registry, never an unreviewed user-supplied API host.
    try:
        data = strict_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ActivitySourceError("project_registry_unavailable") from None
    if not isinstance(data, list) or len(data) > 100:
        raise ActivitySourceError("project_registry_invalid")
    found = {}
    for entry in data:
        if not isinstance(entry, dict):
            raise ActivitySourceError("project_registry_invalid")
        pid, url = entry.get("id"), entry.get("repo_url")
        if not isinstance(pid, str) or not ID.fullmatch(pid) or pid in found:
            raise ActivitySourceError("project_registry_invalid")
        if url is None:
            continue
        match = SLUG.fullmatch(url) if isinstance(url, str) else None
        if not match:
            # Noncanonical repos cannot silently be made an API credential target.
            raise ActivitySourceError("project_repo_not_canonical")
        found[pid] = match.group(1) + "/" + match.group(2)
    return found


class GithubReader:
    def __init__(self, token=None, budget=MAX_REQUESTS, transport=None):
        self.token = token
        self.remaining = min(MAX_REQUESTS, max(0, budget))
        self.opener = transport or build_opener(NoRedirect())

    def fetch(self, repo, endpoint, page=1):
        if self.remaining < 1:
            raise ActivitySourceError("request_budget_exhausted")
        if not re.fullmatch(r"Zennay/[A-Za-z0-9_.-]+", repo):
            raise ActivitySourceError("unapproved_repository")
        if endpoint not in {"pulls", "commits", "actions/runs", "branches"}:
            raise ActivitySourceError("unapproved_endpoint")
        if not isinstance(page, int) or not 1 <= page <= 2:
            raise ActivitySourceError("page_out_of_range")
        self.remaining -= 1
        params = {
            "pulls": "state=all&sort=updated&direction=desc",
            "commits": "sha=main",
            "actions/runs": "",
            "branches": "",
        }[endpoint]
        query = ("?" + params + "&" if params else "?") + "per_page=30&page=" + str(page)
        url = "https://api.github.com/repos/" + repo + "/" + endpoint + query
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "zcloud-activity-readonly/1"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = Request(url, headers=headers, method="GET")
        try:
            with self.opener.open(request, timeout=8) as response:
                if response.status != 200:
                    raise ActivitySourceError("github_http_non_success")
                if urlsplit(response.url).hostname != "api.github.com":
                    raise ActivitySourceError("github_untrusted_response_origin")
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ActivitySourceError("github_response_too_large")
                return strict_json(raw.decode("utf-8"))
        except HTTPError as exc:
            if exc.code in (401, 403, 429):
                raise ActivitySourceError("github_unavailable_or_rate_limited") from None
            if exc.code == 404:
                raise ActivitySourceError("github_repository_not_accessible") from None
            raise ActivitySourceError("github_http_error") from None
        except (URLError, OSError, TimeoutError, UnicodeError, ValueError):
            raise ActivitySourceError("github_transport_or_payload_failure") from None


def event(repo, project, kind, identity, occurred, **extra):
    return {
        "id": "github:" + repo + ":" + kind + ":" + str(identity),
        "project": project,
        "repository": repo,
        "source": "github",
        "kind": kind,
        "source_timestamp": occurred,
        **extra,
    }


def parse_pulls(repo, project, data):
    if not isinstance(data, list):
        raise ActivitySourceError("github_pulls_invalid")
    result = []
    for item in data:
        if not isinstance(item, dict):
            raise ActivitySourceError("github_pulls_invalid")
        number, created = item.get("number"), stamp(item.get("created_at"))
        if type(number) is not int or number < 1 or not created:
            raise ActivitySourceError("github_pulls_invalid")
        merged = stamp(item.get("merged_at")) if item.get("merged_at") is not None else None
        updated = stamp(item.get("updated_at"))
        head = item.get("head") or {}
        sha = safe_sha(head.get("sha")) if isinstance(head, dict) else None
        if not sha or not updated or (item.get("merged_at") is not None and not merged):
            raise ActivitySourceError("github_pulls_invalid")
        common = {"number": number, "head_sha": sha, "draft": item.get("draft") is True}
        result.append(event(repo, project, "pr_created", number, created, **common))
        # A closed PR is never a merge unless GitHub provides merged_at.
        if merged:
            result.append(event(repo, project, "pr_merged", number, merged, **common,
                                merged_main=isinstance(item.get("base"), dict) and item["base"].get("ref") == "main"))
        else:
            result.append(event(repo, project, "pr_updated", str(number) + ":" + updated,
                                updated, **common, merged_main=False,
                                state=item.get("state") if item.get("state") in ("open", "closed") else "unknown"))
    return result


def parse_commits(repo, project, data):
    if not isinstance(data, list):
        raise ActivitySourceError("github_commits_invalid")
    result = []
    for item in data:
        if not isinstance(item, dict):
            raise ActivitySourceError("github_commits_invalid")
        sha = safe_sha(item.get("sha"))
        commit = item.get("commit")
        detail = commit.get("committer") if isinstance(commit, dict) else None
        when = stamp(detail.get("date")) if isinstance(detail, dict) else None
        if not sha or not when:
            raise ActivitySourceError("github_commits_invalid")
        result.append(event(repo, project, "main_commit", sha, when, sha=sha, merged_main=True))
    return result


def parse_runs(repo, project, data):
    if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
        raise ActivitySourceError("github_ci_invalid")
    result = []
    for item in data["workflow_runs"]:
        if not isinstance(item, dict):
            raise ActivitySourceError("github_ci_invalid")
        rid, attempt = item.get("id"), item.get("run_attempt")
        updated = stamp(item.get("updated_at"))
        sha = safe_sha(item.get("head_sha"))
        if type(rid) is not int or rid <= 0 or type(attempt) is not int or attempt < 1 or not sha or not updated:
            raise ActivitySourceError("github_ci_invalid")
        status = item.get("status")
        conclusion = item.get("conclusion")
        # Fetch repository-wide runs, not only main: PR/feature branches carry
        # real work. Head SHA joins to a PR without leaking branch names.
        result.append(event(repo, project, "ci_run", str(rid) + ":" + str(attempt), updated,
                            sha=sha, state=status if status in ("queued", "in_progress", "completed", "waiting", "requested", "pending") else "unknown",
                            conclusion=conclusion if conclusion in ("success", "failure", "cancelled", "skipped", "timed_out", "neutral", "action_required") else None,
                            merged_main=False))
    return result


def parse_branches(repo, project, data, observed):
    if not isinstance(data, list):
        raise ActivitySourceError("github_branches_invalid")
    result = []
    for item in data:
        if not isinstance(item, dict):
            raise ActivitySourceError("github_branches_invalid")
        name, commit = item.get("name"), item.get("commit")
        sha = safe_sha(commit.get("sha")) if isinstance(commit, dict) else None
        if not isinstance(name, str) or len(name) > 180 or not name or not sha:
            raise ActivitySourceError("github_branches_invalid")
        # Branch API has no push timestamp: do not invent one or count a
        # newly seen branch tip as freshly created source activity.
        result.append(event(repo, project, "branch_tip_observed", hashlib.sha256(name.encode("utf-8")).hexdigest()[:20] + ":" + sha, None,
                            sha=sha, branch_id=hashlib.sha256(name.encode("utf-8")).hexdigest()[:20],
                            first_observed_at=observed, merged_main=False))
    return result


def collect(registry, projects, *, reader, now=None):
    observed = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not isinstance(projects, list) or len(projects) < 1 or len(projects) > MAX_PROJECTS:
        raise ActivitySourceError("project_selection_out_of_bounds")
    if len(set(projects)) != len(projects) or any(pid not in registry for pid in projects):
        raise ActivitySourceError("unknown_or_duplicate_project")
    result = {"schema_version": 1, "source": "github", "observed_at": observed,
              "coverage": {}, "events": [], "feed_complete": False,
              "local_vps_events_synchronized": False}
    for pid in projects:
        repo = registry[pid]
        coverage = {"repository": repo, "status": "partial", "last_successful_sync": None,
                    "last_successful_source_fetch": None, "sources": {}, "github_pr_branch_ci": "partial"}
        result["coverage"][pid] = coverage
        staged = []
        endpoints = (("pulls", parse_pulls), ("commits", parse_commits),
                     ("actions/runs", parse_runs), ("branches", parse_branches))
        for endpoint, parse in endpoints:
            try:
                data = reader.fetch(repo, endpoint)
                parsed = parse(repo, pid, data, observed) if endpoint == "branches" else parse(repo, pid, data)
                # With a full first page, further pages are needed for complete coverage.
                size = len(data["workflow_runs"]) if endpoint == "actions/runs" else len(data)
                if size == 30:
                    try:
                        second = reader.fetch(repo, endpoint, 2)
                        parsed += parse(repo, pid, second, observed) if endpoint == "branches" else parse(repo, pid, second)
                        second_size = len(second["workflow_runs"]) if endpoint == "actions/runs" else len(second)
                        if second_size == 30:
                            coverage["sources"][endpoint] = "truncated_after_two_pages"
                        else:
                            coverage["sources"][endpoint] = "recent_window"
                    except ActivitySourceError as exc:
                        coverage["sources"][endpoint] = str(exc)
                else:
                    coverage["sources"][endpoint] = "recent_window"
                staged.extend(parsed)
            except ActivitySourceError as exc:
                coverage["sources"][endpoint] = str(exc)
        if all(value == "recent_window" for value in coverage["sources"].values()) and len(coverage["sources"]) == 4:
            coverage["status"] = "recent_window"
            coverage["github_pr_branch_ci"] = "recent_window"
            coverage["last_successful_sync"] = observed
        if any(value in ("recent_window", "truncated_after_two_pages") for value in coverage["sources"].values()):
            coverage["last_successful_source_fetch"] = observed
        result["events"].extend(staged)
    # Deduplicate stable source identities before any cache or UI adapter uses them.
    deduped = {}
    for item in result["events"]:
        deduped[item["id"]] = item
    events = list(deduped.values())
    # Fair interleaving: a noisy repository never starves a quiet project.
    groups = {pid: [] for pid in projects}
    for item in events:
        groups[item["project"]].append(item)
    for bucket in groups.values():
        bucket.sort(key=lambda item: (item["source_timestamp"] or "", item["id"]), reverse=True)
    fair = []
    while len(fair) < MAX_EVENTS and any(groups.values()):
        for pid in projects:
            if groups[pid] and len(fair) < MAX_EVENTS:
                fair.append(groups[pid].pop(0))
    result["events"] = fair
    result["events_truncated"] = len(events) > MAX_EVENTS
    result["remaining_request_budget"] = reader.remaining
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only bounded GitHub activity receipts; no live queue or DB writes")
    parser.add_argument("--registry", default=str(Path(__file__).resolve().parents[1] / "projects.json"))
    parser.add_argument("--projects", required=True, help="Comma-separated canonical project IDs (up to 12)")
    parser.add_argument("--output", help="Write restricted 0600 JSON, otherwise print")
    parser.add_argument("--ledger", help="Opt-in private retained activity file (0600, outside public/)")
    args = parser.parse_args(argv)
    registry = portfolio_registry(args.registry)
    project_ids = args.projects.split(",")
    reader = GithubReader(token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_READ_TOKEN"))
    payload = collect(registry, project_ids, reader=reader)
    if args.ledger:
        from scripts.zcloud_activity_ledger import merge_and_store
        payload = merge_and_store(args.ledger, payload)
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
    if args.output:
        from scripts.zcloud_activity_ledger import assert_private_destination
        assert_private_destination(args.output)
        import tempfile
        target = Path(args.output).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".zcloud-activity-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.chmod(tmp, 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    else:
        sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ActivitySourceError as exc:
        sys.stderr.write("GitHub activity source unavailable: " + str(exc) + "\n")
        raise SystemExit(2)
