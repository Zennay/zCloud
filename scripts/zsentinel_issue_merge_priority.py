#!/usr/bin/env python3
"""Bounded issue-first prioritisation for existing zSentinel merge workboards.

Only projects already selected for a read-only PR workboard are supported.
Issue/PR references are observational clues, never proof of fixes, claims,
required reviews, merge authorization or production completion.
"""
import re

REF = re.compile(
    r"\b(?:close[ds]?|fix(?:e[ds])?|resolve[ds]?|addresses|refs?)\s+\#([1-9][0-9]{0,6})\b",
    re.IGNORECASE,
)
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


def references(body, existing):
    if not isinstance(body, str):
        return []
    if len(body) > 128000:
        return []
    return sorted({int(match) for match in REF.findall(body)
                   if int(match) in existing})[:20]


def link_candidates(pr_list, issues):
    """Stable source-ranked PR mapping; never treats body refs as accepted fixes."""
    links = {}
    for item in pr_list:
        number = item.get("number")
        if not isinstance(number, int):
            raise ValueError("invalid PR ref")
        links[number] = references(item.get("body"), issues)
    return links


def priority_rank(linked, issues):
    return min((0 if issues[n]["priority"] == "P0" else 1 for n in linked), default=2)


def unaddressed_urgent(issues, links, limit=8):
    referenced = {number for group in links.values() for number in group}
    return sorted((item for number, item in issues.items()
                   if number not in referenced),
                  key=lambda issue: (0 if issue["priority"] == "P0" else 1,
                                     issue["number"]))[:limit]
