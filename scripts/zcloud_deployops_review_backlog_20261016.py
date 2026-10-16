#!/usr/bin/env python3
"""Two-pass, read-only GitHub PR backlog brief. Never authorizes release."""
import argparse
from datetime import datetime, timezone, timedelta
import json
import re
import sys
import urllib.request

REPO = "Zennay/zCloud"
API = "https://api.github.com/repos/" + REPO
SHA = re.compile(r"^[0-9a-f]{40}$")
PAGE_SIZE = 100
MAX_PAGES = 12
MAX_BYTES = 5 * 1024 * 1024  # Actual 100-PR pages exceed 1 MiB (observed >2 MiB).


class Denied(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Denied("duplicate_json_key")
        result[key] = value
    return result


def _bad_number(_):
    raise Denied("invalid_number")


def fetch(kind, page=0):
    if kind == "main" and page == 0:
        url = API + "/branches/main"
    elif kind in ("pulls", "issues") and type(page) is int and 1 <= page <= MAX_PAGES:
        url = API + f"/{kind}?state=open&per_page={PAGE_SIZE}&page={page}"
    else:
        raise Denied("unsupported_endpoint")
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "zcloud-public-readonly-backlog-brief",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.build_opener(NoRedirect).open(req, timeout=12) as response:
        if response.status != 200 or response.geturl() != url:
            raise Denied("untrusted_response")
        mime = response.headers.get("Content-Type", "").split(";")[0].lower().strip()
        if mime not in ("application/json", "application/vnd.github+json"):
            raise Denied("untrusted_content_type")
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise Denied("oversized_response")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=_bad_number)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise Denied("invalid_json") from exc


def _sha(value):
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise Denied("invalid_sha")
    return value


def _date(value, now):
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise Denied("invalid_timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Denied("invalid_timestamp") from exc
    if dt > now + timedelta(minutes=5):
        raise Denied("future_timestamp")
    return dt


def _main(reader):
    value = reader("main", 0)
    if not isinstance(value, dict) or not isinstance(value.get("commit"), dict):
        raise Denied("invalid_main")
    return _sha(value["commit"].get("sha"))


def _inventory(reader, now):
    found, numbers = [], set()
    for page in range(1, MAX_PAGES + 1):
        batch = reader("pulls", page)
        if not isinstance(batch, list) or len(batch) > PAGE_SIZE:
            raise Denied("bad_page")
        for item in batch:
            if not isinstance(item, dict):
                raise Denied("bad_pr")
            n, title, draft = item.get("number"), item.get("title"), item.get("draft")
            if type(n) is not int or n <= 0 or n > 10000000 or n in numbers:
                raise Denied("duplicate_or_bad_number")
            numbers.add(n)
            if item.get("html_url") != f"https://github.com/{REPO}/pull/{n}":
                raise Denied("bad_pr_url")
            if not isinstance(title, str) or not 1 <= len(title) <= 256:
                raise Denied("bad_title")
            if type(draft) is not bool:
                raise Denied("bad_draft")
            head, base = item.get("head"), item.get("base")
            if not isinstance(head, dict) or not isinstance(base, dict):
                raise Denied("bad_refs")
            head_sha, base_sha = _sha(head.get("sha")), _sha(base.get("sha"))
            base_ref = base.get("ref")
            if not isinstance(base_ref, str) or not 1 <= len(base_ref) <= 100:
                raise Denied("bad_base")
            _date(item.get("updated_at"), now)
            found.append((n, title, draft, head_sha, base_sha,
                          base_ref, item["updated_at"]))
        if len(batch) < PAGE_SIZE:
            return tuple(sorted(found))
    raise Denied("unbounded_or_incomplete_pagination")


def _issue_inventory(reader, now):
    """Include both issues and PRs from GitHub /issues to prove no issue was hidden."""
    rows, numbers = [], set()
    for page in range(1, MAX_PAGES + 1):
        batch = reader("issues", page)
        if not isinstance(batch, list) or len(batch) > PAGE_SIZE:
            raise Denied("bad_issue_page")
        for item in batch:
            if not isinstance(item, dict):
                raise Denied("bad_issue")
            n, title = item.get("number"), item.get("title")
            if type(n) is not int or not 1 <= n <= 10000000 or n in numbers:
                raise Denied("bad_or_duplicate_issue")
            numbers.add(n)
            if not isinstance(title, str) or not 1 <= len(title) <= 256:
                raise Denied("bad_issue_title")
            marker = item.get("pull_request")
            is_pr = isinstance(marker, dict)
            if marker is not None and not is_pr:
                raise Denied("bad_pull_marker")
            expected_url = f"https://github.com/{REPO}/{'pull' if is_pr else 'issues'}/{n}"
            if item.get("html_url") != expected_url:
                raise Denied("bad_issue_url")
            labels = item.get("labels")
            if not isinstance(labels, list) or len(labels) > 100:
                raise Denied("bad_labels")
            names = []
            for label in labels:
                name = label.get("name") if isinstance(label, dict) else None
                if not isinstance(name, str) or not 1 <= len(name) <= 100:
                    raise Denied("bad_label")
                names.append(name)
            if len(set(names)) != len(names):
                raise Denied("duplicate_label")
            people = item.get("assignees")
            if not isinstance(people, list) or len(people) > 50:
                raise Denied("bad_assignees")
            logins = []
            for person in people:
                login = person.get("login") if isinstance(person, dict) else None
                if (not isinstance(login, str) or not 1 <= len(login) <= 100
                        or any(ord(c) < 32 or ord(c) == 127 for c in login)):
                    raise Denied("bad_assignee")
                logins.append(login)
            if len(set(logins)) != len(logins):
                raise Denied("duplicate_assignee")
            _date(item.get("updated_at"), now)
            rows.append((n, title, is_pr, item["updated_at"], tuple(sorted(names)),
                         tuple(sorted(logins))))
        if len(batch) < PAGE_SIZE:
            return tuple(sorted(rows))
    raise Denied("issue_pagination_cap")


def _priority(title, labels):
    # Text and labels supplied by GitHub are triage hints, NOT verified severity.
    evidence = [title.split(":", 1)[0].strip().upper()]
    evidence.extend(label.strip().upper() for label in labels)
    return 0 if "P0" in evidence else 1 if "P1" in evidence else 2


def collect(reader=fetch, now=None, *, include_issues=False):
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise Denied("bad_clock")
    main = _main(reader)
    first = _inventory(reader, now)
    if first != _inventory(reader, now):
        raise Denied("pr_drift")
    issues = None
    if include_issues:
        all_first = _issue_inventory(reader, now)
        if all_first != _issue_inventory(reader, now):
            raise Denied("issue_drift")
        issue_pr_numbers = {row[0] for row in all_first if row[2]}
        if issue_pr_numbers != {row[0] for row in first}:
            raise Denied("pr_issue_cohort_mismatch")
        issues = tuple(row for row in all_first if not row[2])
    if _main(reader) != main:
        raise Denied("main_drift")
    return {"schema": "zcloud-review-backlog-v1", "repo": REPO,
            "main_sha": main, "checked_at": now,
            "prs": first, "issues": issues, "complete": True,
            "merge_authorized": False, "deploy_authorized": False}


def _safe(text):
    text = re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", text)
    for char in ("\\", "|", "[", "]", "<", ">"):
        text = text.replace(char, "\\" + char)
    return " ".join(text.split())[:160]


def render(data):
    if (not isinstance(data, dict) or data.get("schema") != "zcloud-review-backlog-v1"
            or data.get("repo") != REPO or data.get("complete") is not True
            or data.get("merge_authorized") is not False
            or data.get("deploy_authorized") is not False):
        raise Denied("untrusted_inventory")
    _sha(data.get("main_sha"))
    now, prs = data["checked_at"], data["prs"]
    if not isinstance(prs, tuple) or len(prs) > MAX_PAGES * PAGE_SIZE:
        raise Denied("bad_inventory")
    stale = sorted((row for row in prs if now - _date(row[6], now)
                    >= timedelta(days=7)), key=lambda row: (row[6], row[0]))
    draft = sum(row[2] for row in prs)
    off_main = sum(row[5] != "main" for row in prs)
    issues = data.get("issues")
    if issues is not None:
        if (not isinstance(issues, tuple) or len(issues) > MAX_PAGES * PAGE_SIZE
                or len({row[0] for row in issues}) != len(issues)):
            raise Denied("invalid_issue_inventory")
    lines = [
        "## zCloud deploy-ops — open PR's (alleen-lezen)", "",
        f"- Main SHA: {data['main_sha']}",
        f"- Volledig twee keer gelezen: {len(prs)} open PR's.",
        f"- Concepten: {draft}; niet-concepten: {len(prs) - draft}.",
        f"- Zeven dagen geen update: {len(stale)}.",
        f"- PR's gericht op andere branch dan main: {off_main}.",
        "- Geen merge-, release-, eigenaar- of productiegoedkeuring.",
        "", "### Oudste onbehandelde updates", "",
        "| PR | Titel | Dagen | Concept |", "| --- | --- | ---: | --- |",
    ]
    if issues is not None:
        issue_rows = sorted(issues, key=lambda row: (_priority(row[1], row[4]),
                                                    row[3], row[0]))
        p0 = sum(_priority(row[1], row[4]) == 0 for row in issues)
        p1 = sum(_priority(row[1], row[4]) == 1 for row in issues)
        issue_section = [
            "## zCloud deploy-ops — open issues eerst (alleen-lezen)", "",
            f"- Apart geteld: {len(issues)} open issues; GitHub PR-issue-records uitgesloten.",
            f"- Bronlabels/titel signaleren P0: {p0}, P1: {p1} (niet onafhankelijk geverifieerd).",
            "- Volledige PR/issue-cohortcontrole in twee afzonderlijke uitleesrondes.",
            "- **GitHub-toewijzing is geen volledige code-/PR-/Notion-eigendomsaudit; niet toegewezen is niet vrij.**",
            "- **Een issue is niet automatisch vrij, opgelost of releasegerechtigd.**",
            "", "| Issue | Titel | Bronurgentie | GitHub-toewijzing | Dagen zonder update |",
            "| --- | --- | --- | --- | ---: |",
        ]
        for n, title, _, updated, labels, assignees in issue_rows[:20]:
            age = max(0, int((now - _date(updated, now)).total_seconds() // 86400))
            urgency = ("P0" if _priority(title, labels) == 0 else
                       "P1" if _priority(title, labels) == 1 else "niet gemarkeerd")
            owners = ", ".join(_safe(login) for login in assignees) if assignees else "niet toegewezen"
            issue_section.append(
                f"| [#{n}](https://github.com/{REPO}/issues/{n}) "
                f"| {_safe(title)} | {urgency} | {owners} | {age} |")
        if not issue_rows:
            issue_section.append("| — | Geen open issues | — | — | — |")
        lines = issue_section + [""] + lines
    for number, title, is_draft, _, _, _, updated in stale[:15]:
        age = int((now - _date(updated, now)).total_seconds() // 86400)
        lines.append(f"| [#{number}](https://github.com/{REPO}/pull/{number}) "
                     f"| {_safe(title)} | {age} | {'ja' if is_draft else 'nee'} |")
    if not stale:
        lines.append("| — | Geen verouderde PR-updates | — | — |")
    lines.extend(["", "Eerst onafhankelijke review: eigenaars, gewijzigde bestanden,",
                  "PR-loze branches, exacte HEAD-CI en releasegates apart controleren.",
                  "VPS, runner, Firefox, SQLite en workerstatus niet geverifieerd."])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--expected-main-sha", help="Require workflow checkout SHA to still be current main")
    parser.add_argument("--include-issues", action="store_true", help="Fully recheck open issues before PR backlog")
    args = parser.parse_args(argv)
    try:
        snapshot = collect(include_issues=args.include_issues)
        if args.expected_main_sha is not None and snapshot["main_sha"] != _sha(args.expected_main_sha):
            raise Denied("main_not_checkout_sha")
        brief = render(snapshot)
        if args.output:
            from pathlib import Path
            Path(args.output).write_text(brief, encoding="utf-8")
        else:
            print(brief, end="")
        return 0
    except (Denied, OSError, TimeoutError, KeyError, TypeError, ValueError):
        print("ZCLOUD_BACKLOG_BRIEF_DENIED: incomplete or drifting evidence",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
