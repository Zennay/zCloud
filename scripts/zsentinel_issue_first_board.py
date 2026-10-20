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
from zsentinel_issue_merge_priority import (
    issue_inventory, link_candidates, priority_rank, unaddressed_urgent,
    classification, references
)

MAX_PER_PROJECT = 4


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
            links = link_candidates([item for _, _, item in available], issues)
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
                report = board.inspect(repo, item["number"], budget,
                                       expected_body_digest=fingerprint)
                if (report.get("source") == "COMPLETE"
                        and report.get("status") in {"HOLD", "OBSERVED_GATES_ONLY"}
                        and report.get("head") == observed_head
                        and isinstance(observed_head, str)):
                    report["observed_issue_refs"] = [
                        issues[number] for number in links.get(item["number"], [])][:8]
                    report["issue_reference_source"] = "OBSERVED_PR_BODY"
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


def collect_targeted(project, issue_number, registry, budget):
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
    query = quote(f"repo:{repo} is:pr is:open {issue_number}", safe="")
    found = board.bounded_gh(f"search/issues?q={query}&per_page=100", budget)
    if not isinstance(found, dict):
        raise ValueError("invalid issue search source")
    matches = found.get("items")
    total = found.get("total_count")
    if (not isinstance(matches, list) or type(total) is not int
            or type(found.get("incomplete_results")) is not bool):
        raise ValueError("incomplete issue search result")
    partial = []
    if found["incomplete_results"] or total > len(matches):
        partial.append({"project": project, "reason": "ISSUE_SEARCH_PARTIAL"})
    # Search can yield unrelated PRs with a coincidental numeric token.
    # Inspect no more than 32 candidates, and never equate search hits to links.
    if len(matches) > 32:
        partial.append({"project": project, "reason": "CANDIDATE_WINDOW_32"})
    rows, examined, unique = [], 0, set()
    for hit in matches[:32]:
        if not isinstance(hit, dict) or not isinstance(hit.get("number"), int):
            partial.append({"project": project, "reason": "MALFORMED_SEARCH_ITEM"})
            continue
        number = hit["number"]
        if number in unique:
            partial.append({"project": project, "reason": "DUPLICATE_SEARCH_ITEM"})
            continue
        unique.add(number)
        if not 1 <= number <= 9999999:
            continue
        # Search issue's PR-specific listing can be imperfect; source PR GET
        # is the only admitted current description and exact head.
        try:
            record = board.bounded_gh(f"repos/{repo}/pulls/{number}", budget)
            if eligible_pr(record) is None:
                continue
            body = record.get("body") or ""
            if not isinstance(body, str) or len(body) > 128000:
                continue
            if issue_number not in references(body, {issue_number}):
                continue
            fingerprint = hashlib.sha256(body.encode("utf-8")).hexdigest()
            report = board.inspect(repo, number, budget,
                                   expected_body_digest=fingerprint)
            if (report.get("source") == "COMPLETE"
                    and report.get("status") in {"HOLD", "OBSERVED_GATES_ONLY"}
                    and report.get("head") == (record.get("head") or {}).get("sha")):
                report["observed_issue_refs"] = [issue_info]
                report["issue_reference_source"] = "TARGETED_VERIFIED_PR_BODY"
            else:
                report["observed_issue_refs"] = []
                report["issue_reference_source"] = "UNKNOWN_OR_MOVED"
            report["reference_proves_fix"] = False
            report["active_claim_status"] = "UNKNOWN_NOT_OWNED"
            rows.append(report)
        except (RuntimeError, ValueError, KeyError, TypeError):
            partial.append({"project": project, "reason": "CANDIDATE_SOURCE_UNAVAILABLE"})
        examined += 1
        if len(rows) >= MAX_PER_PROJECT:
            partial.append({"project": project, "reason": "MAX_FOUR_MATCHING_PRS"})
            break
    # Issue might close while its PRs are being read. We will not advertise
    # current open urgency on an issue whose last read no longer corroborates.
    final_issue = board.bounded_gh(f"repos/{repo}/issues/{issue_number}", budget)
    if (not isinstance(final_issue, dict)
            or final_issue.get("state") != "open"
            or final_issue.get("number") != issue_number):
        partial.append({"project": project, "reason": "ISSUE_SOURCE_MOVED"})
        for row in rows:
            row["observed_issue_refs"] = []
            row["issue_reference_source"] = "UNKNOWN_OR_MOVED"
    return rows, issue_info, partial, {"search_total": total,
                                      "search_examined": min(len(matches), 32),
                                      "source": "TARGETED_ISSUE_SEARCH"}


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


def to_html(rows, unmatched, partial, observed):
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
    return raw.replace("<main>", render_issue_sections(unmatched, partial) +
                       related + "<main>", 1)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--project", action="append", required=True,
                     help="canonical project_id (repeat up to three, e.g. cloud, supa)")
    cli.add_argument("--issue", type=int,
                     help="target one known open issue outside the recent PR window; requires one --project")
    cli.add_argument("--output", help="create new private 0600 offline .html (never overwrite)")
    cli.add_argument("--format", choices=("text", "json"), default="text")
    args = cli.parse_args()
    if (not 1 <= len(args.project) <= 3 or
            len({p.casefold() for p in args.project}) != len(args.project)):
        cli.error("select up to three distinct projects")
    if args.issue is not None and (len(args.project) != 1 or not 1 <= args.issue <= 9999999):
        cli.error("--issue requires one project and positive issue number")
    try:
        registry = board.registry()
        budget = [0, time.monotonic() + board.MAX_SECONDS]
        targeted = None
        if args.issue is not None:
            rows, targeted, partial, target_coverage = collect_targeted(
                args.project[0], args.issue, registry, budget)
            unmatched = []
        else:
            rows, unmatched, partial = collect(args.project, registry, budget)
            target_coverage = None
        observed = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if args.output:
            board.private_write(board.Path(args.output),
                                to_html(rows, unmatched, partial, observed))
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
            "prs": rows,
            "missing_authorization": "EXACT_CI_REVIEW_CLAIM_AND_RELEASE_REQUIRE_SEPARATE_EVIDENCE",
            "main_merged": False, "live_verified": False,
        }
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False))
        else:
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
    except (ValueError, OSError, KeyError, TypeError):
        cli.error("invalid project, source, or private output; no merge/write performed")


if __name__ == "__main__":
    main()
