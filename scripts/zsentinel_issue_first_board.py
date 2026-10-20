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
import time

import zsentinel_merge_first_board as board
from portfolio_merge_ready import DB
from zsentinel_claim_visibility import observe as observe_local_claims
from zsentinel_issue_page_bundle import private_json
from zsentinel_issue_merge_priority import (
    issue_inventory, link_candidates, priority_rank, unaddressed_urgent,
    classification, references, title_references, reference_hints
)

MAX_PER_PROJECT = 4
TARGET_CANDIDATES_PER_PAGE = 20
MAX_TARGET_PAGES = 50


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
    if (item.get("state") != "open" or item.get("draft") is True
            or base.get("ref") != "main"
            or lowered.startswith(("test(", "test:", "qa(", "qa:", "chore(qa)"))
            or "quality-validation" in lowered):
        return None
    rank = (0 if lowered.startswith(("p0", "fix:", "fix("))
            else 1 if lowered.startswith(("p1", "feat:", "feat(")) else 2)
    return rank


def collect(projects, registry, budget):
    rows, unmatched, partial = [], [], []
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
                report = board.inspect(repo, item["number"], budget,
                                       expected_body_digest=fingerprint,
                                       expected_title_digest=title_digest)
                if (report.get("source") == "COMPLETE"
                        and report.get("status") in {"HOLD", "OBSERVED_GATES_ONLY"}
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
    return rows, unmatched[:24], partial


def collect_targeted(project, issue_number, registry, budget, candidate_page=1,
                     candidate_offset=0, expected_page_sha=None):
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
    if (not isinstance(matches, list) or type(total) is not int
            or type(found.get("incomplete_results")) is not bool):
        raise ValueError("incomplete issue search result")
    page_start = (candidate_page - 1) * TARGET_CANDIDATES_PER_PAGE
    page_numbers = [item.get("number") if isinstance(item, dict)
                    and type(item.get("number")) is int else None
                    for item in matches]
    page_sha = hashlib.sha256(json.dumps(page_numbers,
                       separators=(",", ":")).encode("utf-8")).hexdigest()
    if candidate_offset and expected_page_sha != page_sha:
        raise ValueError("GitHub Search candidate order changed; restart from offset zero")
    if candidate_offset > len(matches):
        raise ValueError("resume offset is outside this search result page")
    window_start = page_start + candidate_offset
    window_end = page_start + len(matches)
    partial = []
    if (found["incomplete_results"] or total > 1000 or total > window_end
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
            report = board.inspect(repo, number, budget,
                                   expected_body_digest=fingerprint,
                                   expected_title_digest=title_digest)
            if (report.get("source") == "COMPLETE"
                    and report.get("status") in {"HOLD", "OBSERVED_GATES_ONLY"}
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
        if len(rows) >= MAX_PER_PROJECT:
            partial.append({"project": project, "reason": "MAX_FOUR_MATCHING_PRS"})
            break
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
                         and final_issue.get("id") == issue.get("id"))
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
                 next_candidate_offset is None and window_end < min(total, 1000)
                 and candidate_page < MAX_TARGET_PAGES else None)
    return rows, issue_info, partial, {
        "search_total": total,
        "search_examined": examined,
        "candidate_pr_numbers": candidate_ids,
        "candidate_page": candidate_page,
        "candidate_offset": candidate_offset,
        "next_candidate_offset": next_candidate_offset,
        "source_page_sha256": page_sha,
        "window_start": window_start,
        "window_end": window_end,
        "next_page": next_page,
        "search_complete": not bool(partial) and window_start == 0
                           and window_end >= total and examined == len(matches),
        "source": "TARGETED_ISSUE_SEARCH"}


def collect_targeted_windows(project, issue_number, registry, budget,
                             candidate_page=1, auto_pages=1):
    """Bounded multiple-page read only; no atomic-search or free-owner claim."""
    if (type(auto_pages) is not int or not 1 <= auto_pages <= 3
            or candidate_page + auto_pages - 1 > MAX_TARGET_PAGES):
        raise ValueError("invalid automatic page window")
    combined, partials, pages = [], [], []
    observed_candidates, observed_prs = set(), {}
    unstable_pr_keys = set()
    issue_reconfirmation_failed = False
    target, search_total = None, None
    for page in range(candidate_page, candidate_page + auto_pages):
        try:
            rows, current_issue, partial, coverage = collect_targeted(
                project, issue_number, registry, budget, candidate_page=page)
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
        "auto_pages_requested": auto_pages,
        "auto_pages_observed": len(pages),
        "page_windows": [{
            "candidate_page": p["candidate_page"],
            "window_start": p["window_start"], "window_end": p["window_end"],
            "search_examined": p["search_examined"],
            "next_page": p["next_page"]} for p in pages],
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
    if len(pages) < auto_pages and last["next_page"] is not None:
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
            local_claims=None):
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
                     " (alleen PR-bodyverwijzing, niet opgelost)</p>")
    related = ("<section><h2>Geobserveerde P0/P1-referenties</h2>" +
               "".join(cards) + "</section>") if cards else ""
    target_section = ""
    if targeted:
        target_section = (
            "<section aria-label='Gericht open issue'><h2>Gericht P0/P1-issue</h2>"
            "<p><a href='" + esc(targeted["url"]) + "'>" +
            esc(targeted["priority"] + " #" + str(targeted["number"]) + " · "
                + targeted["title"]) + "</a></p>"
            "<p>Alleen actuele expliciete PR-bodyverwijzingen, geen ownership-"
            "of oplossingsbewijs. Ook een expliciete PR-titelverwijzing is "
            "uitsluitend een verwijzing, geen claim of bewezen fix.</p>"
        )
        if target_coverage:
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
            if target_coverage["next_page"] is not None:
                target_section += (
                    "<p>Volgende zoekpagina: " +
                    esc(target_coverage["next_page"]) +
                    " (apart opvragen met --candidate-page; geen automatisch "
                    "opnieuw starten of eigenaarschap nemen).</p>"
                )
            if not target_coverage["search_complete"]:
                target_section += (
                    "<p><strong>Brondekking onvolledig.</strong> "
                    "Onbekende PRs zijn niet automatisch onbezet.</p>"
            )
        target_section += "</section>"
    claim_panel = render_claim_visibility(local_claims or {})
    return raw.replace("<main>", claim_panel + target_section +
                       render_issue_sections(unmatched, partial) +
                       related + "<main>", 1)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--project", action="append", required=True,
                     help="canonical project_id (repeat up to three, e.g. cloud, supa)")
    cli.add_argument("--issue", type=int,
                     help="target one known open issue outside the recent PR window; requires one --project")
    cli.add_argument("--candidate-page", type=int, default=1,
                     help="continue targeted issue search on page 1–50 of 20 PRs")
    cli.add_argument("--auto-pages", type=int, default=1,
                     help="follow 1–3 existing PR search pages within the same bounded run")
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
        if args.issue is not None:
            rows, targeted, partial, target_coverage = collect_targeted_windows(
                args.project[0], args.issue, registry, budget,
                candidate_page=args.candidate_page, auto_pages=args.auto_pages)
            unmatched = []
        else:
            rows, unmatched, partial = collect(args.project, registry, budget)
            target_coverage = None
        observed = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        local_claims = observe_local_claims(DB, args.project)
        if args.output:
            board.private_write(board.Path(args.output),
                                to_html(rows, unmatched, partial, observed,
                                        targeted=targeted,
                                        target_coverage=target_coverage,
                                        local_claims=local_claims))
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
            "local_claims": local_claims,
            "prs": rows,
            "missing_authorization": "EXACT_CI_REVIEW_CLAIM_AND_RELEASE_REQUIRE_SEPARATE_EVIDENCE",
            "main_merged": False, "live_verified": False,
        }
        if args.snapshot_output:
            private_json(args.snapshot_output, payload)
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False))
        else:
            for project, claim in local_claims.items():
                print(f"LOCAL_CLAIMS {project} {claim['status']}; "
                      f"leases={claim['active_local_leases_observed']}; "
                      f"writers={claim['active_local_writers_observed']}; "
                      "Notion/other ownership remains UNKNOWN")
            for item in rows:
                refs = ", ".join(str(issue["priority"]) + "#" + str(issue["number"])
                                 for issue in item["observed_issue_refs"]) or "none observed"
                print(f"{item['repo']}#{item['number']}: {item['status']}; "
                      f"issue refs={refs}; review={item['review']}; CI={item['checks']}; "
                      f"workflows={item.get('workflows', 'UNKNOWN')}; "
                      f"next={'; '.join(item['actions'])}")
            for issue in unmatched:
                print(f"OPEN_UNLINKED_OBSERVED {issue['priority']} "
                      f"{issue['repo']}#{issue['number']} (ownership unknown)")
            if partial:
                print("PARTIAL: " + ", ".join(item["project"] + " " + item["reason"]
                                             for item in partial))
            if not rows:
                print("No linked existing product PRs observed; this does not prove none exist.")
            if targeted:
                print(f"TARGET_ISSUE {targeted['priority']} #{targeted['number']} "
                      f"{targeted['url']} (source-only, not resolution proof)")
                print(f"SEARCH_WINDOW page={target_coverage['candidate_page']} "
                      f"observed={target_coverage['search_examined']} "
                      f"total={target_coverage['search_total']} "
                      f"next_page={target_coverage['next_page']} "
                      f"complete={target_coverage['search_complete']}")
    except (ValueError, OSError, KeyError, TypeError):
        cli.error("invalid project, source, or private output; no remote writer action performed")


if __name__ == "__main__":
    main()
