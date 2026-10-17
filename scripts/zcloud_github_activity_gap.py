#!/usr/bin/env python3
"""Audit activity-source gaps without mutating zCloud or its SQLite DB.

Read-only diagnostic, NOT an Activity-feed importer or evidence of delivery.
No PR body, title, review, token, or log text is printed. Issue #1518.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import urllib.error
import urllib.request
from urllib.parse import quote

UTC = dt.timezone.utc
REPO = re.compile(r"https://github\.com/Zennay/([A-Za-z0-9_.-]+)(?:/)?$", re.I)
ID = re.compile(r"[a-z][a-z0-9_-]{0,55}$")
MAX_BYTES = 1_048_576
MAX_PAGES = 2
MAX_REPOS = 32


def timestamp(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(UTC)


def project_repos(path: Path) -> dict[str, str]:
    """Repository targets are whitelisted by local canonical projects.json."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("invalid_registry")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid_registry") from exc
    if not isinstance(data, list):
        raise ValueError("invalid_registry")
    targets: dict[str, str] = {}
    seen = set()
    for p in data:
        if not isinstance(p, dict):
            raise ValueError("invalid_registry")
        key, link = p.get("id"), p.get("repo_url")
        if not isinstance(key, str) or not ID.fullmatch(key) or key in seen:
            raise ValueError("invalid_registry")
        seen.add(key)
        if not isinstance(link, str):
            continue
        match = REPO.fullmatch(link)
        if match:
            targets[key] = match.group(1)
    if not targets or len(targets) > MAX_REPOS:
        raise ValueError("invalid_registry")
    return targets


def read_latest_local(path: Path, ids: list[str]) -> dict[str, dt.datetime | None]:
    """Only timestamps: never read stored titles, details, or runner secrets."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("invalid_history_db")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("invalid_history_db")
    uri = "file:" + quote(str(path.resolve())) + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=2.0)
    try:
        db.execute("PRAGMA query_only=ON")
        existing = {r[1] for r in db.execute("PRAGMA table_info(events)")}
        if not {"project", "ts"}.issubset(existing):
            raise ValueError("invalid_history_schema")
        results = {}
        for key in ids:
            row = db.execute("SELECT MAX(ts) FROM events WHERE project=?", (key,)).fetchone()
            results[key] = timestamp(row[0]) if row else None
        return results
    finally:
        db.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect_refused")


def api_pr_page(repo: str, page: int, token: str | None = None, opener=None) -> list[dict]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo) or not (1 <= page <= MAX_PAGES):
        raise ValueError("invalid_api_target")
    url = (f"https://api.github.com/repos/Zennay/{repo}/pulls"
           f"?state=all&sort=updated&direction=desc&per_page=100&page={page}")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "zcloud-activity-gap-audit/1",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers, method="GET")
    provider = opener if opener is not None else urllib.request.build_opener(NoRedirect())
    with provider.open(request, timeout=8) as response:
        if response.status != 200:
            raise ValueError("source_unavailable")
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("response_oversize")
    try:
        rows = json.loads(raw.decode("utf-8", errors="strict"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("malformed_remote_json") from exc
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("invalid_remote_page")
    return rows


def pr_summary(pages: list[list[dict]], since: dt.datetime) -> dict[str, object]:
    seen: set[int] = set()
    created = merged = updates = 0
    newest = None
    invalid = 0
    for page in pages:
        for item in page:
            if not isinstance(item, dict):
                invalid += 1
                continue
            n = item.get("number")
            if type(n) is not int or n <= 0 or n in seen:
                invalid += 1
                continue
            seen.add(n)
            created_at = timestamp(item.get("created_at"))
            updated_at = timestamp(item.get("updated_at"))
            merged_at = timestamp(item.get("merged_at")) if item.get("merged_at") else None
            if not created_at or not updated_at or updated_at < created_at or (merged_at and merged_at < created_at):
                invalid += 1
                continue
            if created_at >= since:
                created += 1
            if merged_at and merged_at >= since:
                merged += 1
            if updated_at >= since:
                updates += 1
            if newest is None or updated_at > newest:
                newest = updated_at
    return {"created": created, "merged": merged, "updated": updates,
            "latest_github_update": newest.isoformat() if newest else None,
            "invalid_records": invalid, "observed_prs": len(seen)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only GitHub-versus-zCloud activity gap audit")
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--history-db", required=True, type=Path)
    parser.add_argument("--hours", type=int, default=5)
    parser.add_argument("--max-repos", type=int, default=12)
    args = parser.parse_args(argv)
    if not (1 <= args.hours <= 72 and 1 <= args.max_repos <= MAX_REPOS):
        parser.error("hours must be 1..72 and max-repos 1..32")
    try:
        repos = project_repos(args.registry)
        ids = list(repos)[:args.max_repos]
        local = read_latest_local(args.history_db, ids)
    except (OSError, sqlite3.Error, ValueError):
        print('{"error":"invalid_local_evidence","coverage_complete":false}')
        return 2
    since = dt.datetime.now(UTC) - dt.timedelta(hours=args.hours)
    token = os.environ.get("GITHUB_TOKEN") or None
    rows = []
    for key in ids:
        name = repos[key]
        pages: list[list[dict]] = []
        source_error = None
        exhausted = False
        try:
            for page in range(1, MAX_PAGES+1):
                result = api_pr_page(name, page, token)
                pages.append(result)
                if len(result) < 100:
                    exhausted = True
                    break
                last_updated = timestamp(result[-1].get("updated_at")) if isinstance(result[-1], dict) else None
                if last_updated is not None and last_updated < since:
                    exhausted = True
                    break
        except (ValueError, OSError, urllib.error.URLError):
            source_error = "github_source_unavailable"
        summary = pr_summary(pages, since)
        local_time = local[key]
        complete = source_error is None and exhausted and summary["invalid_records"] == 0
        rows.append({"project": key,
                     "local_latest_event": local_time.isoformat() if local_time else None,
                     "github": summary if complete else None,
                     "github_coverage": "complete" if complete else "partial_or_unavailable",
                     "reason": None if complete else (source_error or "partial_or_malformed_remote_evidence"),
                     "feed_includes_pr_events": False})
    print(json.dumps({"period_hours": args.hours, "coverage_complete": all(r["github_coverage"]=="complete" for r in rows),
                      "projects": rows}, separators=(",", ":"), sort_keys=True))
    return 0 if all(r["github_coverage"]=="complete" for r in rows) else 3


if __name__ == "__main__":
    sys.exit(main())
