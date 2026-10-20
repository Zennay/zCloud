#!/usr/bin/env python3
"""Issue-first, existing-PR-first operator screen for zSentinel.

Read-only: no claim, review, merge, CI dispatch, queue write or deploy.
Observed references in PR descriptions are *not* evidence of fixes.
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import time

import zsentinel_merge_first_board as board
from zsentinel_issue_merge_priority import (
    issue_inventory, link_candidates, priority_rank, unaddressed_urgent
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
    cli.add_argument("--output", help="create new private 0600 offline .html (never overwrite)")
    cli.add_argument("--format", choices=("text", "json"), default="text")
    args = cli.parse_args()
    if (not 1 <= len(args.project) <= 3 or
            len({p.casefold() for p in args.project}) != len(args.project)):
        cli.error("select up to three distinct projects")
    try:
        registry = board.registry()
        budget = [0, time.monotonic() + board.MAX_SECONDS]
        rows, unmatched, partial = collect(args.project, registry, budget)
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
            "selection": "P0_P1_OBSERVED_PR_LINK_FIRST",
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
                print("No product PRs observed; this does not prove the backlog is empty.")
    except (ValueError, OSError, KeyError, TypeError):
        cli.error("invalid project, source, or private output; no merge/write performed")


if __name__ == "__main__":
    main()
