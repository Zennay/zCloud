#!/usr/bin/env python3
"""Bounded issue-first prioritisation for existing zSentinel merge workboards.

Only projects already selected for a read-only PR workboard are supported.
Issue/PR references are observational clues, never proof of fixes, claims,
required reviews, merge authorization or production completion.
"""
import re

# A single GitHub action may link several issues: "Fixes: #12, #13 and #14".
# These are candidate *references* only; GitHub issue state, reviews and
# exact-head gates must still be observed independently.
REF = re.compile(
    r"\b(?:close[ds]?|fix(?:e[ds])?|resolve[ds]?|addresses|refs?)"
    r"\s*:?\s+\#([1-9][0-9]{0,6})(?![0-9])"
    r"((?:\s*(?:,|&|\band\b|\bor\b)\s*\#(?:[1-9][0-9]{0,6})(?![0-9]))*)",
    re.IGNORECASE,
)
CONTINUATION_REF = re.compile(r"\#([1-9][0-9]{0,6})(?![0-9])")
TITLE_ISSUE_REF = re.compile(r"(?<![A-Za-z0-9_])#([1-9][0-9]{0,6})(?![0-9])")
PRIO_TITLE = re.compile(r"^\s*(?:\[)?P([01])(?:\]|\b|\s*[-:])", re.IGNORECASE)
LABELS_P0 = {"p0", "priority:p0", "priority/p0", "critical", "severity:critical",
             "severity/p0", "urgent", "blocker"}
LABELS_P1 = {"p1", "priority:p1", "priority/p1", "high", "severity:high",
             "severity/p1"}


def classification(issue):
    if not isinstance(issue, dict) or "pull_request" in issue:
        return None
    number = issue.get("number")
    title = issue.get("title")
    labels = issue.get("labels")
    if (not isinstance(number, int) or not (1 <= number <= 9999999)
            or not isinstance(title, str) or not isinstance(labels, list)
            or len(title) > 5000 or len(labels) > 100):
        raise ValueError("untrusted issue response")
    names = set()
    for item in labels:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("untrusted issue labels")
        names.add(item["name"].strip().casefold())
    match = PRIO_TITLE.match(title)
    if names & LABELS_P0 or (match and match.group(1) == "0"):
        prio = "P0"
    elif names & LABELS_P1 or (match and match.group(1) == "1"):
        prio = "P1"
    else:
        return None
    if issue.get("state") != "open":
        return None
    return {"number": number, "priority": prio, "title": title[:160]}


def issue_inventory(raw, repo):
    if not isinstance(raw, list):
        raise ValueError("untrusted GitHub issue list")
    results = {}
    for item in raw:
        issue = classification(item)
        if issue:
            number = issue["number"]
            if number in results:
                raise ValueError("duplicate issue source")
            issue["url"] = f"https://github.com/{repo}/issues/{number}"
            results[number] = issue
    return results, len(raw) == 100



CANONICAL_REPO = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$")


def canonical_issue_references(text, existing, repo):
    """Admit explicit links only when they name this exact canonical repo.

    Cross-repository shorthand or URLs are not same-repo issue evidence.
    They cannot silently link an unrelated issue with the same number.
    """
    if (not isinstance(text, str) or not isinstance(existing, (dict, set, list))
            or not isinstance(repo, str) or not CANONICAL_REPO.fullmatch(repo)
            or len(text) > 128000):
        return []
    escaped = re.escape(repo)
    url = re.compile(
        r"(?<![A-Za-z0-9/])https://github\.com/" + escaped +
        r"/issues/([1-9][0-9]{0,6})(?![0-9/])",
        re.IGNORECASE,
    )
    short = re.compile(
        r"(?<![A-Za-z0-9_./-])" + escaped +
        r"#([1-9][0-9]{0,6})(?![0-9])",
        re.IGNORECASE,
    )
    return sorted({int(n) for n in url.findall(text) + short.findall(text)
                   if int(n) in existing})[:20]


def references(body, existing, repo=None):
    if not isinstance(body, str):
        return []
    if len(body) > 128000:
        return []
    linked = set(canonical_issue_references(body, existing, repo))
    for number, continuation in REF.findall(body):
        # Only the original GitHub action and its contiguous #N list count.
        # Do not match unrelated prose, other repository shorthand or bare
        # numbers following a list separator.
        for candidate in (number, *CONTINUATION_REF.findall(continuation)):
            parsed = int(candidate)
            if parsed in existing:
                linked.add(parsed)
            if len(linked) >= 20:
                break
        if len(linked) >= 20:
            break
    return sorted(linked)[:20]


def title_references(title, existing, repo=None):
    """Explicit same-repository #123 mentions are issue *hints*, not fixes."""
    if not isinstance(title, str) or len(title) > 5000:
        return []
    return sorted({int(number) for number in TITLE_ISSUE_REF.findall(title)
                   if int(number) in existing} |
                  set(canonical_issue_references(title, existing, repo)))[:20]


def reference_hints(title, body, existing, repo=None):
    """Union of explicit PR-title mentions and bounded body action links."""
    return sorted(set(references(body, existing, repo=repo)) |
                  set(title_references(title, existing, repo=repo)))[:20]


def link_candidates(pr_list, issues, repo=None):
    """Stable source-ranked mappings; neither title nor body proves a fix."""
    links = {}
    for item in pr_list:
        number = item.get("number")
        if not isinstance(number, int):
            raise ValueError("invalid PR ref")
        links[number] = reference_hints(item.get("title"), item.get("body"),
                                       issues, repo=repo)
    return links


def priority_rank(linked, issues):
    return min((0 if issues[n]["priority"] == "P0" else 1 for n in linked), default=2)


def unaddressed_urgent(issues, links, limit=8):
    referenced = {number for group in links.values() for number in group}
    return sorted((item for number, item in issues.items()
                   if number not in referenced),
                  key=lambda issue: (0 if issue["priority"] == "P0" else 1,
                                     issue["number"]))[:limit]
