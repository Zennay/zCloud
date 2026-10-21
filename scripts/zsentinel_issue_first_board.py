#!/usr/bin/env python3
"""Issue-first, existing-PR-first operator screen for zSentinel.

Read-only: no claim, review, merge, CI dispatch, queue write or deploy.
Observed references in PR descriptions are *not* evidence of fixes.
"""
import argparse
import datetime as dt
import hashlib
from urllib.parse import quote
import html
import json
import re
import time

import zsentinel_merge_first_board as board
from portfolio_merge_ready import DB
from zsentinel_claim_visibility import observe as observe_local_claims
from zsentinel_issue_page_bundle import private_json
from zsentinel_issue_merge_priority import (
    issue_inventory, link_candidates, priority_rank, unaddressed_urgent,
    classification, references, title_references, reference_hints
)

MAX_PROJECTS = 3
MAX_PER_PROJECT = 4
TARGET_CANDIDATES_PER_PAGE = 20
MAX_TARGET_PAGES = 50


QA_ONLY_LABELS = frozenset({
    "qa", "qa-only", "qa-validation", "quality-validation",
    "regression", "tests-only", "test-only",
})
QA_BRANCH_PREFIXES = ("qa/", "test/", "tests/", "quality-validation/", "regression/")


def explicit_qa_only_pr(item):
    """Do not route QA-only PRs into either existing-product delivery path."""
    title = item.get("title")
    head = item.get("head")
    labels = item.get("labels")
    if (type(title) is not str or not isinstance(head, dict)
            or not isinstance(head.get("ref"), str)
            or not isinstance(labels, list)):
        # Incomplete metadata is not a free/open product assignment.
        return True
    lowered = title.strip().casefold()
    prefix = re.match(
        r"^(?:\[?p[0-3]\]?[\s:—-]*)?"
        r"(?:qa|tests?|regression|quality[ -]validation)"
        r"(?=\b|\(|:|-)", lowered)
    marked = any(
        isinstance(label, dict)
        and isinstance(label.get("name"), str)
        and label["name"].strip().casefold() in QA_ONLY_LABELS
        for label in labels[:40])
    return bool(prefix or marked or
                head["ref"].casefold().startswith(QA_BRANCH_PREFIXES) or
                lowered.startswith(("chore(qa)", "docs(qa)", "ci(qa)")) or
                "quality-validation" in lowered)


def eligible_pr(item):
    if not isinstance(item, dict):
        raise ValueError("invalid PR item")
    number = item.get("number")
    title = item.get("title")
    base = item.get("base")
    if (not isinstance(number, int) or not 1 <= number <= 9999999
            or not isinstance(title, str) or len(title) > 5000
            or not isinstance(base, dict)):
        raise ValueError("invalid PR source fields")
    lowered = title.strip().casefold()
    # Keep draft product PRs visible, but exclude explicit QA-only work
    # consistently for ordinary and targeted issue discovery.
    if (item.get("state") != "open" or type(item.get("draft")) is not bool
            or base.get("ref") != "main" or explicit_qa_only_pr(item)):
        return None
    rank = (0 if lowered.startswith(("p0", "fix:", "fix("))
            else 1 if lowered.startswith(("p1", "feat:", "feat(")) else 2)
    return rank + (3 if item["draft"] else 0)


def prioritize_observed_work(rows):
    """Stable portfolio-wide P0-first display, fairly interleaved by repo.

    Issue references are only an observed PR title/body hint, not an issue fix.
    A gate marked OBSERVED_GATES_ONLY is still *not* a merge authorization.
    This only changes read-only operator order; no task ownership is inferred.
    """
    repo_index, repo_counts = {}, {}
    ranked = []
    for row in rows:
        repo = row["repo"]
        if repo not in repo_index:
            repo_index[repo] = len(repo_index)
        ordinal = repo_counts.get(repo, 0)
        repo_counts[repo] = ordinal + 1
        observed = row.get("observed_issue_refs") or []
        priority = (0 if any(ref.get("priority") == "P0" for ref in observed)
                    else 1 if any(ref.get("priority") == "P1" for ref in observed)
                    else 2)
        # Product drafts and source-unknown candidates stay visible, but not
        # before equally urgent reviewed/observable existing product work.
        status = row.get("status")
        readiness = (0 if status == "OBSERVED_GATES_ONLY"
                     else 1 if status == "HOLD"
                     else 2 if status == "DRAFT" else 3)
        ranked.append(((priority, readiness, ordinal, repo_index[repo]), row))
    return [row for _, row in sorted(ranked, key=lambda item: item[0])]


def issue_local_claim_evidence(project, local_claims):
    """Attach only validated local aggregate counts to a project issue card.

    This is not issue ownership: local claims are project-wide and external
    Notion/worker state remains unknown. No raw lease identifiers are exposed.
    """
    unknown = {
        "source": "LOCAL_SQLITE_READ_ONLY",
        "status": "UNKNOWN",
        "active_local_leases_observed": None,
        "expired_local_leases_observed": None,
        "active_local_writers_observed": None,
        "ambiguous_active_leases": None,
        "notion_claim_status": "NOT_OBSERVED",
        "owner_free": "NOT_INFERABLE",
        "authorization": "NOT_GRANTED",
    }
    if (not isinstance(project, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", project)
            or not isinstance(local_claims, dict)):
        return {**unknown, "status": "LOCAL_PROJECT_MAPPING_UNKNOWN"}
    row = local_claims.get(project)
    if not isinstance(row, dict):
        return {**unknown, "status": "LOCAL_SOURCE_UNAVAILABLE"}
    allowed = {
        "LOCAL_ACTIVE_CLAIMS_OBSERVED", "EXPIRED_LOCAL_LEASES_OBSERVED",
        "NO_ACTIVE_LOCAL_CLAIMS_OBSERVED", "LOCAL_SOURCE_UNAVAILABLE",
        "CLAIM_INVENTORY_TRUNCATED", "CLAIM_SOURCE_INVALID",
    }
    status = row.get("status")
    if not isinstance(status, str) or status not in allowed:
        return {**unknown, "status": "LOCAL_SOURCE_INVALID"}
    fields = (
        "active_local_leases_observed", "expired_local_leases_observed",
        "active_local_writers_observed", "ambiguous_active_leases",
    )
    counts = {}
    for key in fields:
        value = row.get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= 1000):
            return {**unknown, "status": "LOCAL_SOURCE_INVALID"}
        counts[key] = value
    active = counts["active_local_leases_observed"]
    expired = counts["expired_local_leases_observed"]
    writers = counts["active_local_writers_observed"]
    ambiguous = counts["ambiguous_active_leases"]
    observed_statuses = {
        "LOCAL_ACTIVE_CLAIMS_OBSERVED", "EXPIRED_LOCAL_LEASES_OBSERVED",
        "NO_ACTIVE_LOCAL_CLAIMS_OBSERVED",
    }
    if status in observed_statuses and any(
            type(value) is not int for value in counts.values()):
        return {**unknown, "status": "LOCAL_SOURCE_INVALID"}
    if status == "EXPIRED_LOCAL_LEASES_OBSERVED" and not (
            active == 0 and expired > 0
            and writers == 0 and ambiguous == 0):
        return {**unknown, "status": "LOCAL_SOURCE_INVALID"}
    if status == "NO_ACTIVE_LOCAL_CLAIMS_OBSERVED" and not (
            active == 0 and expired == 0 and writers == 0 and ambiguous == 0):
        return {**unknown, "status": "LOCAL_SOURCE_INVALID"}
    if status == "LOCAL_ACTIVE_CLAIMS_OBSERVED" and not (
            (type(active) is int and active > 0)
            or (type(ambiguous) is int and ambiguous > 0)):
        return {**unknown, "status": "LOCAL_SOURCE_INVALID"}
    if status in {
            "LOCAL_SOURCE_UNAVAILABLE", "CLAIM_INVENTORY_TRUNCATED",
            "CLAIM_SOURCE_INVALID"} and any(value is not None for value in counts.values()):
        return {**unknown, "status": "LOCAL_SOURCE_INVALID"}
    return {**unknown, **counts, "status": status}


def observed_issue_inbox(rows, unlinked, partial, repo_projects,
                         source_inventory=None, limit=48, local_claims=None):
    """Turn the *observed* P0/P1 issue window into a read-only action inbox.

    A selected PR's explicit issue reference is an inspection hint, not a fix,
    ownership grant or merge permission. Unlinked issues are only unlinked
    within the bounded *selected* product PR window, never unowned.
    """
    if type(limit) is not int or not 1 <= limit <= 120:
        raise ValueError("invalid bounded issue inbox limit")
    if not isinstance(repo_projects, dict):
        raise ValueError("missing canonical project registry")
    if local_claims is None:
        local_claims = {}
    if not isinstance(local_claims, dict) or len(local_claims) > MAX_PROJECTS:
        raise ValueError("invalid bounded local claim evidence")
    if source_inventory is None:
        source_inventory = []
    if not isinstance(source_inventory, list) or len(source_inventory) > 300:
        raise ValueError("invalid bounded issue source")
    issue_map = {}
    for source_issue in source_inventory:
        repo, number = source_issue["repo"], source_issue["number"]
        key = (repo, number)
        if key in issue_map:
            raise ValueError("duplicate canonical priority issue")
        candidates = source_issue["candidate_pr_numbers"]
        if (not isinstance(candidates, list) or len(candidates) > 10
                or any(type(n) is not int or not 1 <= n <= 9999999
                       for n in candidates) or len(set(candidates)) != len(candidates)):
            raise ValueError("invalid source PR candidates")
        issue_map[key] = {
            "repo": repo, "number": number,
            "priority": source_issue["priority"],
            "title": source_issue["title"], "url": source_issue["url"],
            "reference_coverage": "OBSERVED_PRIORITY_ISSUE_AND_RECENT_PR_PAGE",
            "linked_prs": [], "ownership": "NOT_INFERABLE",
            "candidate_pr_numbers": candidates,
            "candidate_pr_count_in_sample": source_issue["candidate_pr_count_in_sample"],
            "candidate_prs_omitted": source_issue["candidate_prs_omitted"],
            "candidate_pr_source": "UNVERIFIED_RECENT_OPEN_PR_LISTING",
        }
    for row in rows:
        repo = row["repo"]
        for ref in row.get("observed_issue_refs") or []:
            if ref.get("priority") not in {"P0", "P1"}:
                continue
            key = (repo, ref["number"])
            entry = issue_map.setdefault(key, {
                "repo": repo, "number": ref["number"], "priority": ref["priority"],
                "title": ref["title"], "url": ref["url"],
                "reference_coverage": "SELECTED_PR_SOURCE_ONLY",
                "linked_prs": [], "ownership": "NOT_INFERABLE",
                "candidate_pr_numbers": [], "candidate_pr_count_in_sample": 0,
                "candidate_prs_omitted": 0, "candidate_pr_source": "NOT_OBSERVED",
            })
            if (entry["priority"] != ref["priority"]
                    or entry["title"] != ref["title"] or entry["url"] != ref["url"]):
                raise ValueError("issue identity changed inside source window")
            if len(entry["linked_prs"]) >= board.MAX_PRS:
                raise ValueError("unbounded PRs linked to one issue")
            # Keep exact-HEAD gate *observations* attached to their own PR:
            # a headline HOLD alone cannot tell the operator what to inspect.
            # This is not an authorization or a required-context inventory.
            entry["linked_prs"].append({
                "number": row["number"], "status": row["status"],
                "url": f"https://github.com/{repo}/pull/{row['number']}",
                "review": row.get("review", "UNKNOWN"),
                "checks": row.get("checks", "UNKNOWN"),
                "workflows": row.get("workflows", "UNKNOWN"),
                "gate_actions": [
                    action[:160] for action in (row.get("actions") or [])[:3]
                    if isinstance(action, str)
                ],
            })
    for ref in unlinked:
        key = (ref["repo"], ref["number"])
        if key in issue_map:
            continue
        issue_map[key] = {
            "repo": ref["repo"], "number": ref["number"],
            "priority": ref["priority"], "title": ref["title"],
            "url": ref["url"], "reference_coverage": "UNLINKED_WITHIN_SELECTED_PR_WINDOW",
            "linked_prs": [], "ownership": "NOT_INFERABLE",
            "candidate_pr_numbers": [], "candidate_pr_count_in_sample": 0,
            "candidate_prs_omitted": 0, "candidate_pr_source": "NOT_OBSERVED",
        }
    # First prefer existing PRs for the same severity; source-unknown issues
    # are still explicitly shown, never silently assigned to a new worker.
    # Round-robin inside each priority and PR-reuse bucket. Without
    # this, one high-volume project's P0 issues bury all other projects'
    # equally urgent owner action candidates in the 48-card display cap.
    fair_slots = {}
    fair_rows = []
    for issue in sorted(issue_map.values(), key=lambda item: (
            0 if item["priority"] == "P0" else 1,
            0 if item["linked_prs"] else
            1 if item["candidate_pr_numbers"] else 2,
            item["repo"].casefold(), item["number"])):
        level = 0 if issue["priority"] == "P0" else 1
        reuse = (0 if issue["linked_prs"] else
                 1 if issue["candidate_pr_numbers"] else 2)
        key = (level, reuse, issue["repo"])
        position = fair_slots.get(key, 0)
        fair_slots[key] = position + 1
        fair_rows.append(((level, reuse, position,
                           issue["repo"].casefold(), issue["number"]), issue))
    ordered = [item for _, item in sorted(fair_rows, key=lambda pair: pair[0])]
    for item in ordered:
        selected = {pr["number"] for pr in item["linked_prs"]}
        item["uninspected_candidate_pr_numbers"] = [
            number for number in item["candidate_pr_numbers"]
            if number not in selected
        ]
        project = repo_projects.get(item["repo"])
        if (isinstance(project, str)
                and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", project)):
            item["project"] = project
            item["read_only_issue_command"] = (
                "python3 scripts/zsentinel_issue_first_board.py --project " +
                project + " --issue " + str(item["number"]) +
                (" --candidate-pr " +
                 str(item["uninspected_candidate_pr_numbers"][0])
                 if item["uninspected_candidate_pr_numbers"] else ""))
        else:
            item["project"] = "UNKNOWN"
            item["read_only_issue_command"] = None
        item["local_claim_evidence"] = issue_local_claim_evidence(
            item["project"], local_claims)
        local_status = item["local_claim_evidence"]["status"]
        if local_status == "EXPIRED_LOCAL_LEASES_OBSERVED":
            item["operator_action"] = (
                "Verify the observed expired lease against live worker, Notion "
                "and queue state before any claim; then inspect existing PR gates"
            )
        elif local_status in {
                "CLAIM_INVENTORY_TRUNCATED", "CLAIM_SOURCE_INVALID",
                "LOCAL_SOURCE_UNAVAILABLE", "LOCAL_SOURCE_INVALID",
                "LOCAL_PROJECT_MAPPING_UNKNOWN", "UNKNOWN"}:
            item["operator_action"] = (
                "Re-establish trusted current owner/lease evidence; never infer "
                "vacancy from missing or incomplete local data; inspect existing PR gates"
            )
        elif local_status == "LOCAL_ACTIVE_CLAIMS_OBSERVED":
            item["operator_action"] = (
                "Respect observed local active/ambiguous claims; independently "
                "confirm current owner and inspect exact-HEAD existing PR gates"
            )
        else:
            item["operator_action"] = (
                "Inspect selected existing PR and exact-HEAD review/CI/claim gates"
                if item["linked_prs"] else
                "Inspect an already-observed but not yet rechecked existing PR"
                if item["candidate_pr_numbers"] else
                "Search older existing PRs, then independently confirm ownership"
            )
        item["merged_main"] = "NOT_PROVEN"
        item["live_verified"] = False
    limited = ordered[:limit]
    return {
        "coverage": "PARTIAL_OBSERVATION" if partial else "SELECTED_SOURCE_WINDOW_ONLY",
        "ownership": "NOT_INFERABLE",
        "total_observed_issues": len(ordered),
        "linked_observed_issues": sum(bool(x["linked_prs"]) for x in ordered),
        "uninspected_pr_candidate_issues": sum(
            bool(x["uninspected_candidate_pr_numbers"]) and not x["linked_prs"]
            for x in ordered),
        "unlinked_in_sample": sum(
            not x["linked_prs"] and not x["candidate_pr_numbers"]
            for x in ordered),
        "visible": len(limited),
        "omitted": len(ordered) - len(limited),
        "items": limited,
    }


def render_issue_inbox(inbox):
    """Private offline HTML; values are escaped, no new network requests."""
    esc = lambda value: html.escape(str(value), quote=True)
    if not inbox["total_observed_issues"]:
        return ("<section><h2>P0/P1 issue-inbox</h2><p>Geen issues in het "
                "begrensde bronvenster waargenomen; dit bewijst geen lege backlog."
                "</p></section>")
    cards = []
    for issue in inbox["items"]:
        prs = issue["linked_prs"]
        links = (" · ".join(
            "<a href='" + esc(pr["url"]) + "'>PR #" + esc(pr["number"]) +
            "</a> (" + esc(pr["status"]) + "; review " +
            esc(pr["review"]) + "; CI " + esc(pr["checks"]) +
            "; workflows " + esc(pr["workflows"]) +
            ("; volgende gate: " + esc(" / ".join(pr["gate_actions"]))
             if pr["gate_actions"] else "; volgende gate: broncontrole nodig") +
            ")" for pr in prs)
            if prs else "Geen gekoppelde PR binnen deze selectie bevestigd")
        local = issue.get("local_claim_evidence") or {}
        count_text = lambda key: (
            str(local.get(key)) if type(local.get(key)) is int else "UNKNOWN")
        local_summary = (
            "<br>Lokale projectclaims (alleen read-only): " +
            esc(local.get("status", "UNKNOWN")) +
            " · actief=" + esc(count_text("active_local_leases_observed")) +
            " · verlopen=" + esc(count_text("expired_local_leases_observed")) +
            " · schrijvers=" + esc(count_text("active_local_writers_observed")) +
            " · ambigu=" + esc(count_text("ambiguous_active_leases")) +
            ". Geen owner-vrijgave; Notion/externe claims blijven UNKNOWN.")
        outstanding = issue["uninspected_candidate_pr_numbers"]
        candidate_links = (" · ".join(
            "<a href='https://github.com/" + esc(issue["repo"]) +
            "/pull/" + esc(number) + "'>PR #" + esc(number) + "</a>"
            for number in outstanding[:10])
            if outstanding else "geen binnen deze bronselectie")
        cards.append(
            "<li><strong>" + esc(issue["priority"]) + "</strong> · "
            "<a href='" + esc(issue["url"]) + "'>" + esc(issue["repo"]) + "#" +
            esc(issue["number"]) + " · " + esc(issue["title"]) + "</a>"
            "<br>Actueel geïnspecteerde kandidaat-PRs: " + links +
            "<br>Andere PRs uit bronlijst (nog niet opnieuw geverifieerd): " +
            candidate_links + " · extra kandidaten buiten venster: " +
            esc(issue["candidate_prs_omitted"]) +
            local_summary +
            "<br>Volgende stap: " + esc(issue["operator_action"]) +
            ("<br>Lees bestaande PRs: <code>" +
             esc(issue["read_only_issue_command"]) + "</code>"
             if issue["read_only_issue_command"] else
             "<br>Projectmapping onbekend: handmatige broncontrole nodig") +
            "<br><small>Eigenaarschap: onbekend · main: niet bewezen · "
            "live: niet geverifieerd</small></li>")
    return ("<section aria-label='P0/P1 issue-inbox'><h2>P0/P1 "
            "issue-inbox · bestaande PR eerst</h2><p>Waargenomen issues: " +
            esc(inbox["total_observed_issues"]) + "; gekoppeld binnen "
            "de geselecteerde PRs: " + esc(inbox["linked_observed_issues"]) +
            "; niet opnieuw geïnspecteerde PR-kandidaten: " +
            esc(inbox["uninspected_pr_candidate_issues"]) +
            "; zonder waargenomen PR-link: " + esc(inbox["unlinked_in_sample"]) +
            "; niet getoond: " + esc(inbox["omitted"]) + ".</p><p>"
            "Dit is een begrensde bronselectie, geen compleet issue-overzicht "
            "en geen toestemming tot claim, merge of deploy. Dekking: " +
            esc(inbox["coverage"]) + ".</p><ol>" +
            "".join(cards) + "</ol></section>")


def collect(projects, registry, budget):
    rows, unmatched, partial, source_inventory = [], [], [], []
    for project in projects:
        choices = [(repo, key) for repo, key in registry.values() if key == project]
        if len(choices) != 1:
            partial.append({"project": project, "reason": "UNREGISTERED_OR_AMBIGUOUS"})
            continue
        repo, _ = choices[0]
        try:
            prs = board.bounded_gh(
                f"repos/{repo}/pulls?state=open&per_page=100&sort=updated&direction=desc",
                budget)
            raw_issues = board.bounded_gh(
                f"repos/{repo}/issues?state=open&per_page=100&sort=updated&direction=desc",
                budget)
            if not isinstance(prs, list):
                raise ValueError("missing PR source")
            issues, truncated_issues = issue_inventory(raw_issues, repo)
            if len(prs) == 100 or truncated_issues:
                partial.append({"project": project, "reason": "SOURCE_WINDOW_100"})
            available = []
            for position, entry in enumerate(prs):
                rank = eligible_pr(entry)
                if rank is not None:
                    available.append((rank, position, entry))
            links = link_candidates([item for _, _, item in available], issues, repo=repo)
            # Preserve *all* observed priority issues in the same already-read
            # 100-issue source page. Previously any issue referenced by PR #5
            # would disappear when only the top four PRs were inspected.
            # This reverse map is only a source-listing hint: PR and issue may
            # have changed; no gates, fix or owner availability can be inferred.
            references_by_issue = {number: [] for number in issues}
            for pr_number, issue_numbers in links.items():
                for issue_number in issue_numbers:
                    references_by_issue[issue_number].append(pr_number)
            for number, source_issue in sorted(issues.items()):
                candidates = sorted(set(references_by_issue[number]))
                source_inventory.append({
                    "project": project, "repo": repo, **source_issue,
                    "candidate_pr_count_in_sample": len(candidates),
                    "candidate_pr_numbers": candidates[:10],
                    "candidate_prs_omitted": max(0, len(candidates) - 10),
                    "candidate_pr_source": "UNVERIFIED_RECENT_OPEN_PR_LISTING",
                })
            sorted_prs = sorted(
                available,
                key=lambda item: (
                    priority_rank(links.get(item[2]["number"], []), issues),
                    item[0], item[1]))
            # Unlinked means only *no observed link in the sampled product
            # PR descriptions*. It does not assert unowned or unresolved work.
            for item in unaddressed_urgent(issues, links, limit=8):
                unmatched.append({"project": project, "repo": repo, **item})
            for _, _, item in sorted_prs[:MAX_PER_PROJECT]:
                observed_head = (item.get("head") or {}).get("sha")
                body = item.get("body") or ""
                if not isinstance(body, str) or len(body) > 128000:
                    partial.append({"project": project, "reason": "INVALID_PR_BODY"})
                    continue
                fingerprint = hashlib.sha256(body.encode("utf-8")).hexdigest()
                title_digest = hashlib.sha256(item["title"].encode("utf-8")).hexdigest()
                report = board.delivery_receipt(board.inspect(repo, item["number"], budget,
                                       expected_body_digest=fingerprint,
                                       expected_title_digest=title_digest))
                if (report.get("source") == "COMPLETE"
                        and report.get("status") in {"HOLD", "OBSERVED_GATES_ONLY", "DRAFT"}
                        and report.get("head") == observed_head
                        and isinstance(observed_head, str)):
                    report["observed_issue_refs"] = [
                        issues[number] for number in links.get(item["number"], [])][:8]
                    report["issue_reference_source"] = "OBSERVED_PR_TITLE_OR_BODY"
                else:
                    report["observed_issue_refs"] = []
                    report["issue_reference_source"] = "UNKNOWN_OR_MOVED"
                report["reference_proves_fix"] = False
                report["active_claim_status"] = "UNKNOWN_NOT_OWNED"
                rows.append(report)
        except (RuntimeError, ValueError, KeyError, TypeError, OSError):
            partial.append({"project": project, "reason": "SOURCE_UNAVAILABLE"})
            # One project's source failure must never discard another.
            continue
    return prioritize_observed_work(rows), unmatched[:24], partial, source_inventory


def collect_targeted(project, issue_number, registry, budget, candidate_page=1,
                     candidate_offset=0, expected_page_sha=None, max_reports=12):
    """Find existing product PRs for an explicit urgent issue, even when old.

    The ordinary issue-first inventory sees only the most recently modified
    100 PRs. This targeted route uses a bounded native GitHub search to reach
    older still-open PRs, then verifies the canonical issue and PR descriptions
    from their actual repository sources. Search text is never ownership proof.
    """
    choices = [(repo, key) for repo, key in registry.values() if key == project]
    if len(choices) != 1:
        raise ValueError("ambiguous canonical issue project")
    repo, _ = choices[0]
    issue = board.bounded_gh(f"repos/{repo}/issues/{issue_number}", budget)
    if (not isinstance(issue, dict) or issue.get("number") != issue_number
            or issue.get("state") != "open" or "pull_request" in issue):
        raise ValueError("issue does not exist as an open issue")
    level = classification(issue)
    issue_info = level or {"number": issue_number, "priority": "UNCLASSIFIED",
                           "title": str(issue.get("title") or "")[:160]}
    issue_info["url"] = f"https://github.com/{repo}/issues/{issue_number}"
    if type(candidate_page) is not int or not 1 <= candidate_page <= MAX_TARGET_PAGES:
        raise ValueError("candidate page outside bounded GitHub Search window")
    if type(max_reports) is not int or not 1 <= max_reports <= board.MAX_PRS:
        raise ValueError("requested existing PR report limit outside bounded workboard")
    if type(candidate_offset) is not int or not 0 <= candidate_offset < TARGET_CANDIDATES_PER_PAGE:
        raise ValueError("candidate offset outside bounded search page")
    if candidate_offset and (not isinstance(expected_page_sha, str)
                             or len(expected_page_sha) != 64
                             or any(c not in "0123456789abcdef" for c in expected_page_sha)):
        raise ValueError("resuming a page requires its exact ordered candidate fingerprint")
    query = quote(f"repo:{repo} is:pr is:open {issue_number}", safe="")
    # Each page is independently bounded and can be inspected without scanning
    # all previous candidates. GitHub Search has a 1000-hit result ceiling.
    found = board.bounded_gh(
        f"search/issues?q={query}&sort=updated&order=desc"
        f"&per_page={TARGET_CANDIDATES_PER_PAGE}&page={candidate_page}", budget)
    if not isinstance(found, dict):
        raise ValueError("invalid issue search source")
    matches = found.get("items")
    total = found.get("total_count")
    if (not isinstance(matches, list) or type(total) is not int or total < 0
            or type(found.get("incomplete_results")) is not bool):
        raise ValueError("incomplete issue search result")
    page_start = (candidate_page - 1) * TARGET_CANDIDATES_PER_PAGE
    page_numbers = [item.get("number") if isinstance(item, dict)
                    and type(item.get("number")) is int else None
                    for item in matches]
    # A resume token binds the *whole GitHub search window*, not only the
    # numerically ordered candidates. New totals/incompleteness require a
    # fresh offset-zero scan even if the returned PR numbers happen to match.
    page_receipt = {"candidate_pr_numbers": page_numbers,
                    "search_total": total,
                    "incomplete_results": found["incomplete_results"]}
    page_sha = hashlib.sha256(json.dumps(
        page_receipt, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if candidate_offset and expected_page_sha != page_sha:
        raise ValueError("GitHub Search candidate order changed; restart from offset zero")
    if candidate_offset and candidate_offset >= len(matches):
        raise ValueError("resume offset must identify an unread candidate on this page")
    window_start = page_start + candidate_offset
    source_page_end = page_start + len(matches)
    partial = []
    if (found["incomplete_results"] or total > 1000 or total > source_page_end
            or window_start > 0):
        partial.append({"project": project, "reason": "SEARCH_WINDOW_PARTIAL"})
    if len(matches) > TARGET_CANDIDATES_PER_PAGE:
        raise ValueError("unbounded search candidate source")
    # Search can yield unrelated PRs with coincidental numeric tokens. The
    # actual PR body and current head are the only admitted link evidence.
    rows, examined, unique, candidate_ids = [], 0, set(), []
    for hit in matches[candidate_offset:]:
        # Reserve calls/time for exact-source PR evidence and final issue
        # reconciliation; an exhausted source must never masquerade as DONE.
        if (budget[0] > board.MAX_GETS - 8
                or time.monotonic() > budget[1] - 10):
            partial.append({"project": project, "reason": "SOURCE_BUDGET_PARTIAL"})
            break
        examined += 1
        if not isinstance(hit, dict) or not isinstance(hit.get("number"), int):
            partial.append({"project": project, "reason": "MALFORMED_SEARCH_ITEM"})
            continue
        number = hit["number"]
        if number in unique:
            partial.append({"project": project, "reason": "DUPLICATE_SEARCH_ITEM"})
            continue
        unique.add(number)
        if not 1 <= number <= 9999999:
            partial.append({"project": project, "reason": "MALFORMED_SEARCH_ITEM"})
            continue
        candidate_ids.append(number)
        # Search issue's PR-specific listing can be imperfect; source PR GET
        # is the only admitted current description and exact head.
        try:
            record = board.bounded_gh(f"repos/{repo}/pulls/{number}", budget)
            if eligible_pr(record) is None:
                continue
            body = record.get("body") or ""
            if not isinstance(body, str) or len(body) > 128000:
                continue
            if issue_number not in reference_hints(record["title"], body,
                                                    {issue_number}, repo=repo):
                continue
            fingerprint = hashlib.sha256(body.encode("utf-8")).hexdigest()
            title_digest = hashlib.sha256(
                record["title"].encode("utf-8")).hexdigest()
            report = board.delivery_receipt(board.inspect(repo, number, budget,
                                   expected_body_digest=fingerprint,
                                   expected_title_digest=title_digest))
            if (report.get("source") == "COMPLETE"
                    and report.get("status") in {"HOLD", "OBSERVED_GATES_ONLY", "DRAFT"}
                    and report.get("head") == (record.get("head") or {}).get("sha")):
                report["observed_issue_refs"] = [issue_info]
                report["issue_reference_source"] = (
                    "TARGETED_VERIFIED_PR_BODY" if
                    issue_number in references(body, {issue_number}, repo=repo)
                    else "TARGETED_VERIFIED_PR_TITLE"
                )
            else:
                report["observed_issue_refs"] = []
                report["issue_reference_source"] = "UNKNOWN_OR_MOVED"
            report["reference_proves_fix"] = False
            report["active_claim_status"] = "UNKNOWN_NOT_OWNED"
            rows.append(report)
        except (RuntimeError, ValueError, KeyError, TypeError):
            partial.append({"project": project, "reason": "CANDIDATE_SOURCE_UNAVAILABLE"})
        if len(rows) >= max_reports:
            if (candidate_offset + examined < len(matches)
                    or page_start + len(matches) < min(total, 1000)):
                partial.append({"project": project, "reason": "LINKED_PR_REPORT_LIMIT"})
            break
    window_end = window_start + examined
    next_candidate_offset = (candidate_offset + examined
                             if candidate_offset + examined < len(matches) else None)
    if next_candidate_offset is not None:
        partial.append({"project": project, "reason": "CURRENT_PAGE_CANDIDATES_UNSEEN"})
    # Never advertise a current open issue without an independent end-of-scan
    # issue read. Failed reconciliation preserves the PR gate observations but
    # clears purported current issue links and records the source limitation.
    try:
        final_issue = board.bounded_gh(f"repos/{repo}/issues/{issue_number}", budget)
        issue_current = (isinstance(final_issue, dict)
                         and final_issue.get("state") == "open"
                         and final_issue.get("number") == issue_number
                         and final_issue.get("id") == issue.get("id")
                         and "pull_request" not in final_issue)
        if issue_current:
            final_level = classification(final_issue)
            final_priority = (final_level["priority"] if final_level
                              else "UNCLASSIFIED")
            final_title = str(final_issue.get("title") or "")[:160]
            issue_current = (final_priority == issue_info["priority"]
                             and final_title == issue_info["title"])
    except (RuntimeError, ValueError, KeyError, TypeError, OSError):
        issue_current = False
    if not issue_current:
        partial.append({"project": project, "reason": "ISSUE_SOURCE_UNVERIFIED"})
        for row in rows:
            row["observed_issue_refs"] = []
            row["issue_reference_source"] = "UNKNOWN_OR_MOVED"
        issue_info["current_state"] = "UNKNOWN"
    else:
        issue_info["current_state"] = "OPEN_RECONFIRMED"
    next_page = (candidate_page + 1 if
                 next_candidate_offset is None and source_page_end < min(total, 1000)
                 and candidate_page < MAX_TARGET_PAGES else None)
    return rows, issue_info, partial, {
        "search_total": total,
        "search_examined": examined,
        "candidate_pr_numbers": candidate_ids,
        "candidate_page": candidate_page,
        "candidate_offset": candidate_offset,
        "max_linked_prs": max_reports,
        "next_candidate_offset": next_candidate_offset,
        "source_page_sha256": page_sha,
        "window_start": window_start,
        "window_end": window_end,
        "source_page_end": source_page_end,
        "next_page": next_page,
        "search_complete": not bool(partial) and window_start == 0
                           and source_page_end >= total and examined == len(matches),
        "source": "TARGETED_ISSUE_SEARCH"}


def collect_explicit_prs(project, issue_number, registry, budget, pr_numbers):
    """Read exact existing PRs without GitHub Search; never infer entire backlog.

    Explicit PR numbers may originate from Notion, a known GitHub URL or an
    operator handoff. Even a matching title/body hint is not ownership or
    proof of fixing the selected issue. Up to ten PRs share the same budget.
    """
    if (not isinstance(pr_numbers, list) or not 1 <= len(pr_numbers) <= 10
            or any(type(number) is not int or not 1 <= number <= 9999999
                   for number in pr_numbers)
            or len(set(pr_numbers)) != len(pr_numbers)):
        raise ValueError("expected 1–10 distinct existing PR numbers")
    choices = [(repo, key) for repo, key in registry.values() if key == project]
    if len(choices) != 1:
        raise ValueError("ambiguous canonical project")
    repo, _ = choices[0]
    issue = board.bounded_gh(f"repos/{repo}/issues/{issue_number}", budget)
    if (not isinstance(issue, dict) or issue.get("state") != "open"
            or issue.get("number") != issue_number or "pull_request" in issue
            or type(issue.get("id")) is not int or issue["id"] <= 0):
        raise ValueError("target must be a confirmed canonical open issue")
    priority = classification(issue)
    target = dict(priority or {
        "number": issue_number, "priority": "UNCLASSIFIED",
        "title": str(issue.get("title") or "")[:160]})
    target["url"] = f"https://github.com/{repo}/issues/{issue_number}"
    rows, limitations, examined, outcomes = [], [], 0, []
    for number in pr_numbers:
        # Each full detailed PR receipt takes up to 7 bounded GETs. Reserve
        # source budget for a final independent canonical issue observation.
        if budget[0] > board.MAX_GETS - 9 or time.monotonic() > budget[1] - 10:
            limitations.append({"project": project, "reason": "EXPLICIT_PR_BUDGET_PARTIAL"})
            break
        examined += 1
        try:
            pr = board.bounded_gh(f"repos/{repo}/pulls/{number}", budget)
            # Explicit known PRs can be historical: a closed source PR may
            # already be on main while its linked issue remains open. Never
            # let open-only discovery hide that independently checked receipt.
            closed_product = (isinstance(pr, dict)
                              and pr.get("state") == "closed"
                              and type(pr.get("draft")) is bool
                              and not explicit_qa_only_pr(pr))
            if eligible_pr(pr) is None and not closed_product:
                outcomes.append({"number": number, "status": "NOT_ELIGIBLE",
                                 "issue_reference_source": "NOT_VERIFIED"})
                limitations.append({"project": project,
                                    "reason": f"EXPLICIT_PR_NOT_ELIGIBLE_{number}"})
                continue
            body = pr.get("body") or ""
            title = pr["title"]
            if not isinstance(body, str) or len(body) > 128000:
                outcomes.append({"number": number, "status": "INVALID_PR_BODY",
                                 "issue_reference_source": "NOT_VERIFIED"})
                limitations.append({"project": project,
                                    "reason": f"EXPLICIT_PR_BODY_UNAVAILABLE_{number}"})
                continue
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            title_digest = hashlib.sha256(title.encode("utf-8")).hexdigest()
            report = board.delivery_receipt(board.inspect(repo, number, budget,
                                   expected_body_digest=digest,
                                   expected_title_digest=title_digest))
            has_ref = issue_number in reference_hints(
                title, body, {issue_number}, repo=repo)
            source_matches = (report.get("source") == "COMPLETE"
                              and report.get("head") == (pr.get("head") or {}).get("sha"))
            trustworthy = (source_matches and report.get("status") in {
                "HOLD", "OBSERVED_GATES_ONLY", "DRAFT"})
            historical = (source_matches and closed_product and
                          report.get("status") in {
                              "CLOSED_NOT_MERGED", "MERGED_ON_MAIN_VERIFIED",
                              "MERGED_INTO_NONDEFAULT_BRANCH"})
            report["observed_issue_refs"] = ([target] if has_ref and
                                             (trustworthy or historical) else [])
            report["issue_reference_source"] = (
                "EXPLICIT_PR_VERIFIED_REFERENCE" if has_ref and trustworthy
                else "HISTORICAL_PR_REFERENCE_NOT_FIX_PROOF" if has_ref and historical
                else "EXPLICIT_PR_NO_CURRENT_VERIFIED_REFERENCE")
            report["reference_proves_fix"] = False
            report["active_claim_status"] = "UNKNOWN_NOT_OWNED"
            rows.append(report)
            outcomes.append({"number": number,
                             "status": report.get("status", "SOURCE_UNAVAILABLE"),
                             "head": report.get("head"),
                             "main_merged": report["main_merged"],
                             "main_commit": report.get("main_commit"),
                             "live_verified": False,
                             "issue_reference_source": report["issue_reference_source"]})
            if not has_ref:
                limitations.append({"project": project,
                                    "reason": f"PR_{number}_ISSUE_LINK_NOT_OBSERVED"})
        except (RuntimeError, ValueError, KeyError, TypeError, OSError):
            outcomes.append({"number": number, "status": "SOURCE_UNAVAILABLE",
                             "issue_reference_source": "NOT_VERIFIED"})
            limitations.append({"project": project,
                                "reason": f"EXPLICIT_PR_SOURCE_UNAVAILABLE_{number}"})
    remaining = pr_numbers[examined:]
    outcomes.extend({"number": number, "status": "UNREAD",
                     "issue_reference_source": "NOT_VERIFIED"}
                    for number in remaining)
    if remaining:
        limitations.append({"project": project, "reason": "EXPLICIT_PR_SELECTION_UNREAD"})
    try:
        final = board.bounded_gh(f"repos/{repo}/issues/{issue_number}", budget)
        final_class = classification(final) if isinstance(final, dict) else None
        final_priority = (final_class["priority"] if final_class
                          else "UNCLASSIFIED")
        current = (isinstance(final, dict)
                   and final.get("state") == "open"
                   and final.get("number") == issue_number
                   and final.get("id") == issue.get("id")
                   and "pull_request" not in final
                   and str(final.get("title") or "")[:160] == target["title"]
                   and final_priority == target["priority"])
    except (RuntimeError, ValueError, KeyError, TypeError, OSError):
        current = False
    target["current_state"] = "OPEN_RECONFIRMED" if current else "UNKNOWN"
    if not current:
        limitations.append({"project": project, "reason": "FINAL_ISSUE_SOURCE_UNVERIFIED"})
        for report in rows:
            report["observed_issue_refs"] = []
            report["issue_reference_source"] = "UNKNOWN_OR_MOVED"
        for outcome in outcomes:
            if outcome["issue_reference_source"] in {
                    "EXPLICIT_PR_VERIFIED_REFERENCE",
                    "HISTORICAL_PR_REFERENCE_NOT_FIX_PROOF"}:
                outcome["issue_reference_source"] = "UNKNOWN_OR_MOVED"
    limitations.append({"project": project, "reason": "EXPLICIT_PR_SELECTION_NOT_SEARCH"})
    coverage = {
        "source": "EXPLICIT_PR_CANONICAL_GET",
        "candidate_pr_numbers": pr_numbers[:examined],
        "requested_pr_numbers": pr_numbers,
        "remaining_pr_numbers": remaining,
        "candidate_outcomes": outcomes,
        "search_examined": examined,
        "search_total": None,
        "candidate_page": None,
        "next_page": None,
        "next_candidate_offset": None,
        "source_page_sha256": None,
        "window_start": None,
        "window_end": None,
        "search_complete": False,
        "max_linked_prs": len(pr_numbers),
        "linked_prs_observed": len(rows),
        "auto_pages_requested": 0,
        "auto_pages_observed": 0,
        "page_windows": [],
    }
    return rows, target, limitations, coverage


def collect_targeted_windows(project, issue_number, registry, budget,
                             candidate_page=1, auto_pages=1, candidate_offset=0,
                             expected_page_sha=None, max_linked_prs=12):
    """Bounded multiple-page read only; no atomic-search or free-owner claim."""
    if (type(auto_pages) is not int or not 1 <= auto_pages <= 3
            or candidate_page + auto_pages - 1 > MAX_TARGET_PAGES):
        raise ValueError("invalid automatic page window")
    if type(max_linked_prs) is not int or not 1 <= max_linked_prs <= board.MAX_PRS:
        raise ValueError("invalid linked PR report cap")
    combined, partials, pages = [], [], []
    observed_candidates, observed_prs = set(), {}
    unstable_pr_keys = set()
    issue_reconfirmation_failed = False
    target, search_total = None, None
    for page in range(candidate_page, candidate_page + auto_pages):
        try:
            rows, current_issue, partial, coverage = collect_targeted(
                project, issue_number, registry, budget, candidate_page=page,
                candidate_offset=(candidate_offset if not pages else 0),
                expected_page_sha=(expected_page_sha if not pages else None),
                max_reports=max_linked_prs - len(combined))
        except (RuntimeError, ValueError, OSError, KeyError, TypeError):
            if not pages:
                raise
            issue_reconfirmation_failed = True
            partials.append({"project": project, "reason": "FOLLOW_PAGE_UNAVAILABLE"})
            break
        if target is None:
            target, search_total = dict(current_issue), coverage["search_total"]
        else:
            if (current_issue.get("url") != target.get("url")
                    or current_issue.get("title") != target.get("title")
                    or current_issue.get("priority") != target.get("priority")
                    or current_issue.get("current_state") != "OPEN_RECONFIRMED"):
                issue_reconfirmation_failed = True
                partials.append({"project": project, "reason": "ISSUE_MOVED_BETWEEN_PAGES"})
            if coverage["search_total"] != search_total:
                partials.append({"project": project, "reason": "SEARCH_TOTAL_CHANGED"})
        if current_issue.get("current_state") != "OPEN_RECONFIRMED":
            issue_reconfirmation_failed = True
        candidates = coverage.get("candidate_pr_numbers")
        if not isinstance(candidates, list) or len(candidates) != coverage["search_examined"]:
            partials.append({"project": project, "reason": "CANDIDATE_IDS_INCOMPLETE"})
        else:
            if len(set(candidates)) != len(candidates) or observed_candidates.intersection(candidates):
                partials.append({"project": project, "reason": "CANDIDATE_OVERLAP_BETWEEN_PAGES"})
            observed_candidates.update(candidates)
        for row in rows:
            identity = (row.get("repo"), row.get("number"))
            if identity in observed_prs:
                reason = ("PR_HEAD_CHANGED_BETWEEN_PAGES"
                          if observed_prs[identity] != row.get("head")
                          else "PR_REPEATED_BETWEEN_PAGES")
                partials.append({"project": project, "reason": reason})
                unstable_pr_keys.add(identity)
                continue
            observed_prs[identity] = row.get("head")
            combined.append(row)
        partials.extend(partial)
        pages.append(coverage)
        if len(combined) >= max_linked_prs:
            if coverage["next_candidate_offset"] is not None or coverage["next_page"] is not None:
                partials.append({"project": project, "reason": "TOTAL_LINKED_PR_REPORT_LIMIT"})
            break
        if coverage.get("next_candidate_offset") is not None:
            partials.append({"project": project, "reason": "STOPPED_INSIDE_CURRENT_PAGE"})
            break
        if coverage["next_page"] is None:
            break
        if budget[0] > board.MAX_GETS - 10 or time.monotonic() > budget[1] - 10:
            partials.append({"project": project, "reason": "AUTO_PAGE_BUDGET_PARTIAL"})
            break
    if not pages:
        raise ValueError("no observed search window")
    first, last = pages[0], pages[-1]
    summary = dict(first)
    summary.update({
        "window_end": last["window_end"],
        "search_examined": sum(p["search_examined"] for p in pages),
        "candidate_pr_numbers": sorted(observed_candidates),
        "next_page": last["next_page"],
        "next_candidate_offset": last.get("next_candidate_offset"),
        "source_page_sha256": last["source_page_sha256"],
        "auto_pages_requested": auto_pages,
        "auto_pages_observed": len(pages),
        "max_linked_prs": max_linked_prs,
        "linked_prs_observed": len(combined),
        "page_windows": [{
            "candidate_page": p["candidate_page"],
            "window_start": p["window_start"], "window_end": p["window_end"],
            "source_page_end": p["source_page_end"],
            "search_examined": p["search_examined"],
            "next_page": p["next_page"],
            "candidate_offset": p["candidate_offset"],
            "next_candidate_offset": p["next_candidate_offset"],
            "source_page_sha256": p["source_page_sha256"]} for p in pages],
        "search_complete": (first["search_complete"] and not partials
                            if len(pages) == 1 else False),
        "cross_page_source": ("SINGLE_GITHUB_SEARCH_PAGE" if len(pages) == 1
                              else "NONATOMIC_GITHUB_SEARCH"),
    })
    # Multi-page search consists of separate mutable GitHub windows. Recheck
    # the actual issue after the final window, never trust a prior page's
    # open-state observation as the state of the combined handoff.
    if len(pages) > 1:
        matches = [(repo_name, key) for repo_name, key in registry.values()
                   if key == project]
        if len(matches) != 1:
            issue_reconfirmation_failed = True
            partials.append({"project": project, "reason": "AMBIGUOUS_FINAL_ISSUE_REPO"})
        else:
            try:
                final = board.bounded_gh(
                    f"repos/{matches[0][0]}/issues/{issue_number}", budget)
                if (not isinstance(final, dict) or final.get("state") != "open"
                        or final.get("number") != issue_number
                        or "pull_request" in final):
                    issue_reconfirmation_failed = True
                    partials.append({"project": project, "reason": "FINAL_ISSUE_NOT_OPEN"})
                else:
                    current_class = classification(final)
                    current_priority = (current_class["priority"] if current_class
                                        else "UNCLASSIFIED")
                    current_title = str(final.get("title") or "")[:160]
                    if (target["priority"] != current_priority or
                            target["title"] != current_title):
                        issue_reconfirmation_failed = True
                        partials.append({"project": project, "reason": "FINAL_ISSUE_PRIORITY_OR_TITLE_CHANGED"})
            except (RuntimeError, ValueError, OSError, KeyError, TypeError):
                issue_reconfirmation_failed = True
                partials.append({"project": project, "reason": "FINAL_ISSUE_SOURCE_UNAVAILABLE"})
    if issue_reconfirmation_failed:
        target["current_state"] = "UNKNOWN"
        for row in combined:
            row["observed_issue_refs"] = []
            row["issue_reference_source"] = "UNKNOWN_OR_MOVED"
        partials.append({"project": project, "reason": "ISSUE_NOT_CURRENTLY_RECONFIRMED"})
    elif unstable_pr_keys:
        for row in combined:
            if (row.get("repo"), row.get("number")) in unstable_pr_keys:
                row["observed_issue_refs"] = []
                row["issue_reference_source"] = "PR_SOURCE_MOVED"
    if len(pages) > 1:
        partials.append({"project": project, "reason": "NONATOMIC_SEARCH_PAGINATION"})
    if len(pages) < auto_pages and (
            last["next_page"] is not None or last["next_candidate_offset"] is not None):
        partials.append({"project": project, "reason": "AUTO_PAGE_NOT_FINISHED"})
    deduped, seen = [], set()
    for item in partials:
        k = (item["project"], item["reason"])
        if k not in seen:
            deduped.append(item)
            seen.add(k)
    return combined, target, deduped, summary


def render_issue_sections(unmatched, partial):
    esc = lambda x: html.escape(str(x), quote=True)
    note = (
        "<section aria-label='Issue-first bewijs'><h2>Open issue → bestaande PR</h2>"
        "<p>Een PR-bodyverwijzing bewijst geen oplossing, claim, actuele eigenaar "
        "of mergegerechtigdheid. Een issue zonder waargenomen PR-koppeling kan "
        "elders al in uitvoering zijn.</p>"
    )
    if partial:
        note += "<h3>Onvolledige bronnen</h3><ul>"
        for entry in partial:
            note += "<li>" + esc(entry["project"]) + ": " + esc(entry["reason"]) + "</li>"
        note += "</ul>"
    if unmatched:
        note += "<details><summary>P0/P1 zonder geobserveerde PR-koppeling (" + str(len(unmatched)) + ")</summary><ul>"
        for issue in unmatched:
            note += ("<li><a href='" + esc(issue["url"]) + "'>"
                     + esc(issue["priority"]) + " · " + esc(issue["repo"]) +
                     "#" + esc(issue["number"]) + " · " + esc(issue["title"]) + "</a></li>")
        note += "</ul></details>"
    return note + "</section>"


def render_claim_visibility(source):
    esc = lambda value: html.escape(str(value), quote=True)
    cards = [
        "<section aria-label='Lokale ownerclaims'>",
        "<h2>Lokale werkclaims (alleen bronwaarneming)</h2>",
        "<p>Een lokale SQLite-observatie kan nooit aantonen dat Notion, een andere "
        "worker of een externe bron vrij is. Er is geen claim, owner-ID, "
        "worker-ID of mergeautorisatie geëxporteerd.</p>",
    ]
    for project, result in source.items():
        cards.append(
            "<p><strong>" + esc(project) + "</strong>: " + esc(result["status"]) +
            " · lokale actieve leases=" +
            esc(result["active_local_leases_observed"] if
                result["active_local_leases_observed"] is not None else "UNKNOWN") +
            " · verlopen lokale leases=" +
            esc(result["expired_local_leases_observed"] if
                result["expired_local_leases_observed"] is not None else "UNKNOWN") +
            " · exclusieve schrijvers=" +
            esc(result["active_local_writers_observed"] if
                result["active_local_writers_observed"] is not None else "UNKNOWN") +
            " · ambigue leases=" +
            esc(result["ambiguous_active_leases"] if
                result["ambiguous_active_leases"] is not None else "UNKNOWN") +
            " · Notion=" + esc(result["notion_claim_status"]) + "</p>"
        )
    cards.append("</section>")
    return "".join(cards)


def to_html(rows, unmatched, partial, observed, targeted=None, target_coverage=None,
            local_claims=None, issue_inbox=None):
    raw = board.render(rows, observed)
    esc = lambda x: html.escape(str(x), quote=True)
    cards = []
    for row in rows:
        refs = row["observed_issue_refs"]
        if not refs:
            continue
        url = "https://github.com/" + row["repo"] + "/pull/" + str(row["number"])
        links = " · ".join(
            "<a href='" + esc(item["url"]) + "'>" + esc(item["priority"]) +
            " #" + esc(item["number"]) + "</a>" for item in refs)
        cards.append("<p><a href='" + esc(url) + "'>" + esc(row["repo"]) + "#" +
                     esc(row["number"]) + "</a> → " + links +
                     " (alleen geobserveerde PR-titel/bodyverwijzing, niet opgelost)</p>")
    related = ("<section><h2>Geobserveerde P0/P1-referenties</h2>" +
               "".join(cards) + "</section>") if cards else ""
    target_section = ""
    if targeted:
        target_section = (
            "<section aria-label='Gericht issue'><h2>Gericht issue (bronwaarneming)</h2>"
            "<p><strong>Actuele issue-status: " +
            esc(targeted.get("current_state", "UNKNOWN")) + "</strong></p>"
            "<p><a href='" + esc(targeted["url"]) + "'>" +
            esc(targeted["priority"] + " #" + str(targeted["number"]) + " · "
                + targeted["title"]) + "</a></p>"
            "<p>Expliciete PR-body- of titelverwijzingen zijn alleen hints, "
            "geen ownership-, fix- of mergebewijs. Bij UNKNOWN mag de eerdere "
            "P0/P1-prioriteit niet als actueel bewezen gelden.</p>"
        )
        if target_coverage and target_coverage.get("source") == "EXPLICIT_PR_CANONICAL_GET":
            numbers = target_coverage["requested_pr_numbers"]
            canonical_repo_url = targeted["url"].rsplit("/issues/", 1)[0]
            selected_links = ", ".join(
                "<a href='" + esc(canonical_repo_url + "/pull/" + str(number)) +
                "'>#" + esc(number) + "</a>" for number in numbers)
            target_section += (
                "<p><strong>Rechtstreeks opgevraagde bestaande PRs:</strong> " +
                selected_links +
                ". Actuele PR-HEAD/checks/review waar bron beschikbaar is; "
                "geen GitHub Search, geen volledige backlogdekking of eigenaarschapsbewijs.</p>"
                "<p>Gecontroleerde kandidaat-PRs: " +
                esc(target_coverage["search_examined"]) +
                "; bruikbare PR-rapporten: " +
                esc(target_coverage["linked_prs_observed"]) + ".</p>"
            )
            remaining = target_coverage.get("remaining_pr_numbers", [])
            if remaining:
                remaining_links = ", ".join(
                    "<a href='" + esc(canonical_repo_url + "/pull/" + str(n)) +
                    "'>#" + esc(n) + "</a>" for n in remaining)
                target_section += (
                    "<p><strong>Nog NIET geïnspecteerde PRs:</strong> " +
                    remaining_links +
                    ". Plan deze apart als rechtstreekse --candidate-pr-opdracht "
                    "met hetzelfde issue; dit is geen gelukt bronbewijs.</p>"
                )
            outcome_items = []
            for item in target_coverage.get("candidate_outcomes", []):
                pr_url = canonical_repo_url + "/pull/" + str(item["number"])
                # Direct, source-verified main receipts are navigable;
                # historical references and missing proof cannot close issues.
                verified_commit = item.get("main_commit")
                main_note = " · main: NIET BEVESTIGD"
                if (item.get("main_merged") is True
                        and isinstance(verified_commit, str)
                        and re.fullmatch(r"[0-9a-f]{40}", verified_commit)):
                    main_note = (
                        " · main: <a href='" +
                        esc(canonical_repo_url + "/commit/" + verified_commit) +
                        "'>BEVESTIGD " + esc(verified_commit[:12]) + "</a>")
                outcome_items.append(
                    "<li><a href='" + esc(pr_url) + "'>PR #" +
                    esc(item["number"]) + "</a>: " +
                    esc(item["status"]) + " · issueverband: " +
                    esc(item["issue_reference_source"]) + main_note +
                    " · productie: NIET BEVESTIGD</li>")
            target_section += (
                "<details open><summary>Uitkomst per opgegeven PR (" +
                esc(len(outcome_items)) + ")</summary><ul>" +
                "".join(outcome_items) + "</ul></details>"
            )
            if not target_coverage["search_complete"]:
                target_section += (
                    "<p><strong>Alle andere bestaande PRs blijven onbekend.</strong> "
                    "Een ontbrekende expliciete issueverwijzing is geen afwezige "
                    "PR en geen goedgekeurde fix.</p>"
                )
        elif target_coverage:
            target_section += (
                "<p>Search total: " + esc(target_coverage["search_total"]) +
                " · Pagina: " + esc(target_coverage["candidate_page"]) +
                " · Venster: " + esc(target_coverage["window_start"] + 1) +
                "–" + esc(target_coverage["window_end"]) +
                " · Daadwerkelijk bekeken: " +
                esc(target_coverage["search_examined"]) +
                " · Bron: " + esc(target_coverage["source"]) + "</p>"
            )
            if target_coverage.get("auto_pages_observed", 1) > 1:
                target_section += ("<p>Automatisch gelezen pagina\u0027s: " +
                    esc(", ".join(str(p["candidate_page"]) for p in target_coverage["page_windows"])) +
                    ". Niet-atomaire GitHub-zoekbron; geen volledig actueel PR-bewijs.</p>")
            if target_coverage.get("next_candidate_offset") is not None:
                resume_offset = target_coverage["next_candidate_offset"]
                if resume_offset == 0:
                    target_section += (
                        "<p><strong>Deze pagina is nog niet onderzocht.</strong> "
                        "Begin pagina " + esc(target_coverage["candidate_page"]) +
                        " opnieuw zonder --candidate-offset. Een verlopen "
                        "bronbudget bewijst geen afwezigheid van PRs.</p>"
                    )
                else:
                    target_section += (
                        "<p><strong>Deze pagina is niet volledig onderzocht.</strong> "
                        "Hervat pagina " + esc(target_coverage["candidate_page"]) +
                        " op kandidaat-offset " + esc(resume_offset) +
                        " met --candidate-offset en --expected-page-sha " +
                        esc(target_coverage["source_page_sha256"]) +
                        ". Als de zoekvolgorde wijzigt, weigert het programma "
                        "de vervolgopdracht.</p>"
                    )
            if target_coverage["next_page"] is not None:
                target_section += (
                    "<p>Volgende zoekpagina: " +
                    esc(target_coverage["next_page"]) +
                    " (apart opvragen met --candidate-page; geen automatisch "
                    "opnieuw starten of eigenaarschap nemen).</p>"
                )
            target_section += (
                "<p>Geobserveerde bestaande PR-rapporten: " +
                esc(target_coverage.get("linked_prs_observed", 0)) +
                " / maximum " + esc(target_coverage.get("max_linked_prs", 12)) +
                " voor deze gerichte zoekopdracht.</p>"
            )
            if not target_coverage["search_complete"]:
                target_section += (
                    "<p><strong>Brondekking onvolledig.</strong> "
                    "Onbekende PRs zijn niet automatisch onbezet.</p>"
            )
        target_section += "</section>"
    claim_panel = render_claim_visibility(local_claims or {})
    inbox_panel = render_issue_inbox(issue_inbox) if issue_inbox is not None else ""
    return raw.replace("<main>", claim_panel + inbox_panel + target_section +
                       render_issue_sections(unmatched, partial) +
                       related + "<main>", 1)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--project", action="append", required=True,
                     help="canonical project_id (repeat up to three, e.g. cloud, supa)")
    cli.add_argument("--issue", type=int,
                     help="target one known open issue outside the recent PR window; requires one --project")
    cli.add_argument("--candidate-pr", type=int, action="append",
                     help="directly inspect 1–10 known existing product PRs, including closed receipts, without search")
    cli.add_argument("--candidate-page", type=int, default=1,
                     help="continue targeted issue search on page 1–50 of 20 PRs")
    cli.add_argument("--auto-pages", type=int, default=1,
                     help="follow 1–3 existing PR search pages within the same bounded run")
    cli.add_argument("--max-linked-prs", type=int, default=12,
                     help="inspect up to 1–12 linked existing PR reports in this full targeted search")
    cli.add_argument("--candidate-offset", type=int, default=0,
                     help="resume candidate index 0–19 inside the exact same result page")
    cli.add_argument("--expected-page-sha",
                     help="ordered candidate fingerprint printed with an unfinished source page")
    cli.add_argument("--output", help="create new private 0600 offline .html (never overwrite)")
    cli.add_argument("--snapshot-output",
                     help="save this targeted issue search page as a NEW private 0600 JSON snapshot")
    cli.add_argument("--format", choices=("text", "json"), default="text")
    args = cli.parse_args()
    if (not 1 <= len(args.project) <= 3 or
            len({p.casefold() for p in args.project}) != len(args.project)):
        cli.error("select up to three distinct projects")
    if args.issue is not None and (len(args.project) != 1 or not 1 <= args.issue <= 9999999):
        cli.error("--issue requires one project and positive issue number")
    if not 1 <= args.candidate_page <= MAX_TARGET_PAGES:
        cli.error("--candidate-page must be between 1 and 50")
    if args.issue is None and args.candidate_page != 1:
        cli.error("--candidate-page requires --issue")
    if not 1 <= args.auto_pages <= 3 or args.candidate_page + args.auto_pages - 1 > MAX_TARGET_PAGES:
        cli.error("--auto-pages must remain inside page 1–50 and be at most 3")
    if args.issue is None and args.auto_pages != 1:
        cli.error("--auto-pages requires --issue")
    if args.candidate_pr:
        if (args.issue is None or len(args.candidate_pr) > 10
                or len(set(args.candidate_pr)) != len(args.candidate_pr)
                or any(not 1 <= number <= 9999999 for number in args.candidate_pr)):
            cli.error("--candidate-pr requires --issue and 1–10 unique positive PR numbers")
        if (args.candidate_page != 1 or args.auto_pages != 1
                or args.candidate_offset != 0 or args.expected_page_sha
                or args.snapshot_output or args.max_linked_prs != board.MAX_PRS):
            cli.error("--candidate-pr is a direct source mode; no search pages, offsets or snapshots")
    if not 1 <= args.max_linked_prs <= board.MAX_PRS:
        cli.error("--max-linked-prs must be 1–12")
    if args.issue is None and args.max_linked_prs != board.MAX_PRS:
        cli.error("--max-linked-prs requires --issue")
    if not 0 <= args.candidate_offset < TARGET_CANDIDATES_PER_PAGE:
        cli.error("--candidate-offset must be 0–19")
    if args.candidate_offset and (
            args.issue is None or not isinstance(args.expected_page_sha, str)
            or len(args.expected_page_sha) != 64 or
            any(c not in "0123456789abcdef" for c in args.expected_page_sha)):
        cli.error("--candidate-offset requires --issue and --expected-page-sha (64 lowercase hex)")
    if args.expected_page_sha and not args.candidate_offset:
        cli.error("--expected-page-sha requires --candidate-offset")
    if args.snapshot_output and args.candidate_offset:
        cli.error("--snapshot-output requires a complete page from offset zero")
    if args.snapshot_output and args.auto_pages != 1:
        cli.error("--snapshot-output requires exactly one source page")
    if args.snapshot_output and args.issue is None:
        cli.error("--snapshot-output requires a targeted --issue")
    if args.snapshot_output and args.output:
        cli.error("--output and --snapshot-output cannot be combined")
    try:
        registry = board.registry()
        budget = [0, time.monotonic() + board.MAX_SECONDS]
        targeted = None
        source_inventory = []
        if args.issue is not None:
            if args.candidate_pr:
                rows, targeted, partial, target_coverage = collect_explicit_prs(
                    args.project[0], args.issue, registry, budget,
                    args.candidate_pr)
            else:
                rows, targeted, partial, target_coverage = collect_targeted_windows(
                    args.project[0], args.issue, registry, budget,
                    candidate_page=args.candidate_page, auto_pages=args.auto_pages,
                    candidate_offset=args.candidate_offset,
                    expected_page_sha=args.expected_page_sha,
                    max_linked_prs=args.max_linked_prs)
            unmatched = []
        else:
            rows, unmatched, partial, source_inventory = collect(
                args.project, registry, budget)
            target_coverage = None
        observed = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        repo_projects = {
            repo: project for repo, project in registry.values()
            if project in args.project
        }
        local_claims = observe_local_claims(DB, args.project)
        issue_inbox = (observed_issue_inbox(
            rows, unmatched, partial, repo_projects,
            source_inventory=source_inventory, local_claims=local_claims)
            if args.issue is None else None)
        if args.output:
            board.private_write(board.Path(args.output),
                                to_html(rows, unmatched, partial, observed,
                                        targeted=targeted,
                                        target_coverage=target_coverage,
                                        local_claims=local_claims,
                                        issue_inbox=issue_inbox))
        payload = {
            "observed_utc": observed,
            "project_ids": args.project,
            "github_gets": budget[0],
            "source_partial": bool(partial),
            "partial_reasons": partial,
            "selection": ("TARGETED_OPEN_ISSUE" if args.issue is not None
                          else "P0_P1_OBSERVED_PR_LINK_FIRST"),
            "targeted_issue": targeted,
            "targeted_coverage": target_coverage,
            "observed_unlinked_issues": unmatched,
            "issue_inbox": issue_inbox,
            "local_claims": local_claims,
            "prs": rows,
            "missing_authorization": "EXACT_CI_REVIEW_CLAIM_AND_RELEASE_REQUIRE_SEPARATE_EVIDENCE",
            # Legacy top-level flag means this read-only invocation merged
            # nothing. Never interpret it as a verdict on historical PRs.
            "main_merged": False,
            "main_merged_meaning": "THIS_INVOCATION_PERFORMED_NO_MERGE",
            "main_delivery_scope": "PER_PR_EXACT_SOURCE_ONLY",
            "main_verified_prs": sum(row.get("main_merged") is True for row in rows),
            "live_verified": False,
        }
        if args.snapshot_output:
            private_json(args.snapshot_output, payload)
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False))
        else:
            for project, claim in local_claims.items():
                print(f"LOCAL_CLAIMS {project} {claim['status']}; "
                      f"leases={claim['active_local_leases_observed']}; "
                      f"expired_leases={claim['expired_local_leases_observed']}; "
                      f"writers={claim['active_local_writers_observed']}; "
                      "expired leases are evidence only; Notion/other ownership remains UNKNOWN")
            if issue_inbox:
                print("P0_P1_ISSUE_INBOX observed=" +
                      str(issue_inbox["total_observed_issues"]) +
                      " linked=" + str(issue_inbox["linked_observed_issues"]) +
                      " uninspected_candidates=" +
                      str(issue_inbox["uninspected_pr_candidate_issues"]) +
                      " unlinked_within_sample=" + str(issue_inbox["unlinked_in_sample"]) +
                      " coverage=" + issue_inbox["coverage"])
                for issue in issue_inbox["items"]:
                    prs = ",".join(
                        "#" + str(pr["number"]) + ":" + pr["status"] +
                        "(review=" + str(pr["review"]) + ";ci=" +
                        str(pr["checks"]) + ";workflows=" +
                        str(pr["workflows"]) + ")"
                        for pr in issue["linked_prs"]) or "UNKNOWN"
                    hinted = ",".join(str(n) for n in
                                      issue["uninspected_candidate_pr_numbers"]) or "UNKNOWN"
                    print("ISSUE_ACTION " + issue["priority"] + " " +
                          issue["url"] + " inspected_prs=" + prs +
                          " uninspected_pr_candidates=" + hinted +
                          " next=" + issue["operator_action"] +
                          " local_claims=" +
                          str((issue.get("local_claim_evidence") or {}).get("status", "UNKNOWN")) +
                          "(active=" + str((issue.get("local_claim_evidence") or {}).get("active_local_leases_observed")
                                           if type((issue.get("local_claim_evidence") or {}).get("active_local_leases_observed")) is int else "UNKNOWN") +
                          ";expired=" + str((issue.get("local_claim_evidence") or {}).get("expired_local_leases_observed")
                                            if type((issue.get("local_claim_evidence") or {}).get("expired_local_leases_observed")) is int else "UNKNOWN") +
                          ";owner_free=NOT_INFERABLE)" +
                          " readonly_command=" +
                          (issue["read_only_issue_command"] or "UNAVAILABLE"))
            for item in rows:
                refs = ", ".join(
                    str(issue["priority"]) + "#" + str(issue["number"]) +
                    " (" + issue["url"] + ")"
                    for issue in item["observed_issue_refs"]) or "none observed"
                print(f"{item['repo']}#{item['number']}: {item['status']}; "
                      f"issue refs={refs}; review={item['review']}; CI={item['checks']}; "
                      f"workflows={item.get('workflows', 'UNKNOWN')}; "
                      f"next={'; '.join(item['actions'])}")
                holds = item.get("workflow_holds")
                if isinstance(holds, dict):
                    if holds.get("coverage") != "COMPLETE":
                        print(f"  CI_RUN_SOURCE {item['repo']}#{item['number']} UNKNOWN")
                    else:
                        for hold in holds.get("items", []):
                            print(f"  CI_OBSERVED_NON_GREEN {hold['status']} "
                                  f"{hold['name']} {hold['url']} | "
                                  f"next: {hold['next_action']}")
                        if holds.get("more"):
                            print("  CI_HOLD_MORE additional observations outside bounded display")
            for issue in unmatched:
                print(f"OPEN_UNLINKED_OBSERVED {issue['priority']} "
                      f"{issue['repo']}#{issue['number']} "
                      f"{issue['url']} (ownership unknown)")
            if partial:
                print("PARTIAL: " + ", ".join(item["project"] + " " + item["reason"]
                                             for item in partial))
            if not rows:
                print("No linked existing product PRs observed; this does not prove none exist.")
            if targeted:
                print(f"TARGET_ISSUE {targeted['priority']} #{targeted['number']} "
                      f"{targeted['url']} state={targeted.get('current_state', 'UNKNOWN')} "
                      "(issue priority may be stale if state is UNKNOWN; not fix proof)")
                if target_coverage.get("source") == "EXPLICIT_PR_CANONICAL_GET":
                    for item in target_coverage.get("candidate_outcomes", []):
                        print(f"DIRECT_PR #{item['number']} status={item['status']} "
                              f"issue_link={item['issue_reference_source']} "
                              f"main_delivery={'VERIFIED' if item.get('main_merged') is True else 'NOT_VERIFIED'} "
                              f"main_commit={item.get('main_commit') or 'NOT_VERIFIED'} "
                              "live_verified=false")
                    print("DIRECT_PR_SELECTION " +
                          ",".join(str(n) for n in target_coverage["requested_pr_numbers"]) +
                          f" inspected={target_coverage['search_examined']} " +
                          f"reports={target_coverage['linked_prs_observed']} " +
                          f"remaining_prs={','.join(str(n) for n in target_coverage['remaining_pr_numbers']) or 'none'} " +
                          "backlog_coverage=UNKNOWN_NOT_SEARCHED")
                else:
                    print(f"SEARCH_WINDOW page={target_coverage['candidate_page']} "
                      f"observed={target_coverage['search_examined']} "
                      f"total={target_coverage['search_total']} "
                      f"next_page={target_coverage['next_page']} "
                      f"next_candidate_offset={target_coverage.get('next_candidate_offset')} "
                      f"linked_prs={target_coverage.get('linked_prs_observed')} "
                      f"max_linked_prs={target_coverage.get('max_linked_prs')} "
                      f"source_page_sha256={target_coverage.get('source_page_sha256')} "
                      f"complete={target_coverage['search_complete']}")
    except (ValueError, OSError, KeyError, TypeError):
        cli.error("invalid project, source, or private output; no remote writer action performed")


if __name__ == "__main__":
    main()
