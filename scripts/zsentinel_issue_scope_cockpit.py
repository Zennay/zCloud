#!/usr/bin/env python3
"""zSentinel: one-shot P0/P1 product issue + live PR file overlap cockpit.

Integrates the already-merged canonical product delivery workbench source with
the bounded GitHub PR collision observer. No writes, claims, retries or merges.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone, timedelta
from html import escape
import json
import os
from pathlib import Path
import re
import sys

from zsentinel_operator_workbench import read_source_and_sha, validated_view
from zsentinel_product_issue_inbox import canonical_projects, collect as collect_live_issues
from zsentinel_product_delivery_board import prepare as prepare_delivery_board
from zsentinel_pr_scope_guard import ROOT, registry
from zsentinel_pr_scope_portfolio import collect_portfolio

MAX_PROJECTS = 3
MAX_ISSUES = 60
MAX_PRS_PER_PROJECT = 16
P = {"P0": 0, "P1": 1, "P2": 2, "Unspecified": 3}
PR = re.compile(r"^https://github\.com/Zennay/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]{0,8})$")
OWNER_ONLY = frozenset({
    "https://github.com/Zennay/zCloud/issues/1326",
    "https://github.com/Zennay/zCloud/issues/1523",
})


def _safe_projects(source, canonical, explicit=None):
    cards = source["cards"]
    scope = {row["project"] for row in cards}
    if explicit:
        if (len(explicit) > MAX_PROJECTS or len(set(explicit)) != len(explicit)
                or any(p not in canonical or p not in source["projects"] for p in explicit)):
            raise ValueError("select at most three existing canonical projects in the source board")
        return explicit
    # Issue-first fair portfolio selection; never assume absence of sampled
    # issues means that another project has no owner or no live work.
    ranked = {}
    for card in cards:
        p = card["project"]
        if p not in canonical:
            continue
        score = P[card["priority"]]
        current = ranked.setdefault(p, [3, 0])
        current[0] = min(current[0], score)
        current[1] += 1
    return [pid for pid, _ in sorted(ranked.items(),
                                    key=lambda item: (item[1][0], -item[1][1], item[0]))[:MAX_PROJECTS]]


def _saved_pr_gates(raw):
    """Bounded saved exact-head observations; never convert success to approval."""
    rows = raw.get("pr_delivery_observations", [])
    if type(rows) is not list or len(rows) > 5:
        raise ValueError("unexpected source PR gate observation count")
    result = {}
    for row in rows:
        if type(row) is not dict:
            raise ValueError("invalid saved PR gate observation")
        link, head = row.get("url"), row.get("head_sha")
        approvals = row.get("observed_approvals")
        check = row.get("observed_check_success")
        blockers = row.get("blocking_unknowns")
        if (type(link) is not str or PR.fullmatch(link) is None
                or link in result or type(head) is not str
                or re.fullmatch(r"[a-f0-9]{40}", head) is None
                or type(approvals) is not int or not 0 <= approvals <= 100
                or type(check) is not bool
                or type(blockers) is not list or len(blockers) > 20
                or any(type(v) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,70}", v)
                       for v in blockers)
                or row.get("merge_authorized") is not False):
            raise ValueError("saved source PR gate receipt cannot authorize merge")
        result[link] = {
            "observed_exact_head_sha": head,
            "saved_native_checks_success": check,
            "saved_independent_approval_count_unverified": approvals,
            "saved_remaining_unknowns": blockers,
            "current_head_and_required_rules_not_verified": True,
            "merge_authorized": False,
        }
    return result


def compose(source, source_sha, canonical, selected, collision, saved_gates=None):
    if (not isinstance(source_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", source_sha)
            or type(collision) is not dict
            or collision.get("schema") != "zsentinel-portfolio-pr-collision-board-v1"
            or collision.get("projects") != selected
            or any(collision.get(field) is not False for field in (
                "notion_claims_verified", "prless_branch_owners_verified",
                "worker_file_leases_verified", "file_edit_authorized",
                "claim_authorized", "merge_authorized", "deploy_authorized",
                "writes_performed"))
            or type(collision.get("project_views")) is not list
            or len(collision["project_views"]) != len(selected)):
        raise ValueError("scope source unexpectedly authorizes work or mismatches issue projects")
    saved_gates = saved_gates or {}
    if type(saved_gates) is not dict or len(saved_gates) > 5:
        raise ValueError("invalid saved PR gate map")
    exact = {}
    for item in collision["project_views"]:
        pid = item.get("project")
        repo = item.get("repository")
        if (pid not in selected or pid in exact or repo != canonical[pid]
                or item.get("source_status") not in (
                    "unavailable", "partial_open_pr_sample", "complete_open_pr_sample_only")
                or type(item.get("sample_complete")) is not bool):
            raise ValueError("untrusted project collision source")
        related = {}
        for pair in item.get("shown_collision_pairs", []):
            if type(pair) is not dict:
                raise ValueError("invalid PR collision record")
            a, b = pair.get("left"), pair.get("right")
            if type(a) is not dict or type(b) is not dict:
                raise ValueError("invalid PR collision identity")
            for own, other in ((a, b), (b, a)):
                link, ref = own.get("url"), other.get("url")
                if (type(link) is not str or type(ref) is not str
                        or not link.startswith("https://github.com/" + repo + "/pull/")
                        or not ref.startswith("https://github.com/" + repo + "/pull/")
                        or type(own.get("number")) is not int
                        or link != "https://github.com/" + repo + "/pull/" + str(own["number"])
                        or type(other.get("number")) is not int
                        or ref != "https://github.com/" + repo + "/pull/" + str(other["number"])):
                    raise ValueError("cross-repository collision data")
                related.setdefault(link, set()).add(ref)
        heads = item.get("inspected_pr_heads", [])
        # Current portfolio producer does not export these head receipts;
        # never infer a PR is conflict-free just because it has no pair.
        if heads and type(heads) is not list:
            raise ValueError("malformed inspected PR metadata")
        exact[pid] = {"source": item["source_status"],
                      "complete": item["sample_complete"],
                      "collisions": related,
                      "observed_open_prs": item.get("observed_open_prs"),
                      "observed_pairs": item.get("observed_collision_pairs"),
                      "known_disjoint": {
                          p.get("url") for p in item.get(
                              "unpaired_open_prs_not_claim_verified", [])
                          if type(p) is dict and type(p.get("url")) is str
                      } if item["sample_complete"] else set()}
    if len(exact) != len(selected):
        raise ValueError("missing project collision coverage")
    try:
        source_at = datetime.fromisoformat(source["observed_at_utc"].replace("Z", "+00:00"))
        overlay_at = datetime.fromisoformat(collision["observed_at_utc"].replace("Z", "+00:00"))
        if source_at.tzinfo is None or overlay_at.tzinfo is None:
            raise ValueError("source clock has no UTC offset")
        delta = abs((overlay_at - source_at).total_seconds())
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid source time") from None
    coherent = delta <= 600
    issue_rows = []
    for card in source["cards"][:MAX_ISSUES]:
        pid = card["project"]
        if pid not in exact:
            continue
        state = exact[pid]
        repo = canonical[pid]
        if not card["url"].startswith("https://github.com/" + repo + "/issues/"):
            raise ValueError("source issue not from canonical project repository")
        refs = []
        for link in card["open_pr_candidates"]:
            m = PR.fullmatch(link)
            if m is None or link.rsplit("/pull/", 1)[0] != "https://github.com/" + repo:
                raise ValueError("untrusted cross-project PR candidate")
            rivals = sorted(state["collisions"].get(link, set()))
            if rivals:
                status = "shared_file_paths_observed"
            elif coherent and state["complete"] and link in state["known_disjoint"]:
                status = "no_shared_paths_in_complete_open_pr_snapshot_only"
            else:
                status = "scope_unknown_or_uninspected"
            gates = saved_gates.get(link)
            if gates is not None and (not coherent or
                    gates.get("merge_authorized") is not False):
                gates = None
            refs.append({"url": link, "file_scope_state": status,
                         "overlap_with_open_prs": rivals[:16],
                         "saved_exact_head_gates": gates,
                         "current_branch_required_checks_verified": False,
                         "independent_review_current_head_verified": False,
                         "collision_is_merge_failure": False,
                         "claim_authorized": False,
                         "merge_authorized": False})
        if card["url"] in OWNER_ONLY:
            route = "authorized_infrastructure_owner_only"
        elif refs and any(p["file_scope_state"] == "shared_file_paths_observed" for p in refs):
            route = "coordinate_existing_pr_file_overlap_and_owner"
        elif refs:
            route = "inspect_existing_pr_and_owner_gates_first"
        else:
            route = "verify_live_owner_then_product_implementation"
        issue_rows.append({
            "priority": card["priority"], "project": pid,
            "issue": card["issue"], "url": card["url"],
            "existing_prs": refs, "next_action": route,
            "issue_evidence_stale": not coherent,
            "observed_claim_context": card["claim_context"],
            "owner_verified": False, "claim_authorized": False,
            "merge_authorized": False, "deploy_authorized": False,
        })
    issue_rows.sort(key=lambda x: (P[x["priority"]], x["project"], x["issue"]))
    return {
        "schema": "zsentinel-integrated-issue-pr-scope-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "product_issue_source_sha256": source_sha,
        "source_issue_observed_at_utc": source["observed_at_utc"],
        "source_pr_observed_at_utc": collision["observed_at_utc"],
        "source_clocks_within_10_minutes": coherent,
        "chosen_projects": selected,
        "github_scope_by_project": [{
            "project": pid, "source_status": exact[pid]["source"],
            "pr_sample_complete": exact[pid]["complete"],
            "observed_open_prs": exact[pid]["observed_open_prs"],
            "observed_overlap_pairs": exact[pid]["observed_pairs"],
        } for pid in selected],
        "issues": issue_rows,
        "claims_verified": False,
        "live_runner_recovered": False,
        "automatic_dispatch": False,
        "may_take_ownership": False,
        "merge_authorized": False, "deploy_authorized": False,
        "writes_performed": False,
    }


def output_text(report, markdown=False):
    intro = ("# zSentinel · Gecombineerde product- en PR-bestandsscope"
             if markdown else "zSentinel | productissues + PR-bestandsscope")
    lines = [intro, "Issue-bron SHA256: " + report["product_issue_source_sha256"],
             "Bronmomenten ≤10 min uiteen: " +
             ("ja" if report["source_clocks_within_10_minutes"] else "NEE — oude bron is geen live claim")]
    for pid in report["github_scope_by_project"]:
        lines.append(pid["project"] + ": PR-bron " + pid["source_status"] +
                     ", waargenomen gedeelde padenparen " +
                     str(pid["observed_overlap_pairs"]))
    for row in report["issues"]:
        ref = ("[" + row["issue"] + "](" + row["url"] + ")" if markdown
               else row["url"])
        lines.append(row["priority"] + " | " + row["project"] + " | " +
                     ref + " | " + row["next_action"])
        for pr in row["existing_prs"]:
            link = ("[" + pr["url"] + "](" + pr["url"] + ")" if markdown else pr["url"])
            lines.append("  " + link + " · " + pr["file_scope_state"])
            gates = pr["saved_exact_head_gates"]
            if gates is not None:
                lines.append("    Historische exacte HEAD " + gates["observed_exact_head_sha"] +
                             " · checks: " + str(gates["saved_native_checks_success"]) +
                             " · observed approvals: " +
                             str(gates["saved_independent_approval_count_unverified"]) +
                             " (geen actuele merge-autorisatie)")
            for other in pr["overlap_with_open_prs"]:
                lines.append("    Gedeeld bestandspad met " + other)
    lines.extend(["", "Gedeeld pad ≠ mergeconflict. Geen claims, PR-less ownership, CI of review geautoriseerd.",
                  "P0 infrastructuur/billing uitsluitend bevoegd eigenaar. Geen writes, retries, merge of deploy."])
    return "\n".join(lines) + "\n"


def output_html(report):
    cards = []
    for row in report["issues"]:
        prs = []
        for pr in row["existing_prs"]:
            rivals = "".join(
                "<li><a rel='noopener noreferrer' href='" + escape(other, quote=True) +
                "'>" + escape(other) + "</a></li>"
                for other in pr["overlap_with_open_prs"])
            gates = pr["saved_exact_head_gates"]
            gate_note = ("<p>Opgeslagen HEAD " +
                         escape(gates["observed_exact_head_sha"][:12]) +
                         " · native checks: " +
                         ("geslaagd (historische waarneming)" if gates["saved_native_checks_success"]
                          else "niet volledig geslaagd (historische waarneming)") +
                         " · waargenomen approvals: " +
                         str(gates["saved_independent_approval_count_unverified"]) +
                         ". Verplichte huidige-head rules/review niet geverifieerd.</p>"
                         if gates is not None else
                         "<p>Geen verse, exact opgeslagen check/review-waarneming.</p>")
            prs.append(
                "<li><a rel='noopener noreferrer' href='" +
                escape(pr["url"], quote=True) + "'>" + escape(pr["url"]) +
                "</a> · " + escape(pr["file_scope_state"]) +
                gate_note +
                ("<ul>" + rivals + "</ul>" if rivals else "") + "</li>")
        cards.append(
            "<article><small>" + escape(row["priority"]) + " · " +
            escape(row["project"]) + "</small><h2><a rel='noopener noreferrer' href='" +
            escape(row["url"], quote=True) + "'>" + escape(row["issue"]) +
            "</a></h2><p><strong>Volgende stap:</strong> " +
            escape(row["next_action"].replace("_", " ")) + "</p>" +
            ("<ul>" + "".join(prs) + "</ul>" if prs else
             "<p>Geen bestaande PR-link in het bronissue; eigenaarschap blijft onbekend.</p>") +
            "</article>")
    return ("<!doctype html><html lang='nl'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='referrer' content='no-referrer'>"
            "<meta http-equiv='Content-Security-Policy' "
            "content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
            "form-action 'none'; object-src 'none'\">"
            "<title>zSentinel · Product + PR-scope</title>"
            "<style>body{font:16px/1.5 system-ui;background:#101a29;color:#f2f7fd;"
            "max-width:1060px;margin:auto;padding:24px}h1{font-size:28px}"
            "a{color:#b1dbff}article{margin:14px 0;padding:16px;"
            "border:1px solid #556981;border-radius:12px;background:#1c293b}"
            "small{font-weight:bold;color:#dfc794}ul{overflow-wrap:anywhere}"
            "aside{background:#303744;padding:18px;border-left:4px solid #e7c576}"
            "@media(min-width:770px){main{display:grid;grid-template-columns:1fr 1fr;gap:14px}}"
            "</style><h1>zSentinel · Productissues en PR-bestandscope</h1>"
            "<aside>Geen geverifieerd eigenaarschap, live claims, volledige branch-"
            "inventaris, CI, review of merge-/deploybevoegdheid. Een gedeelde "
            "bestandsnaam bewijst geen Git-mergeconflict. Alleen read-only "
            "GitHub-data en een lokaal gekozen issue-bronbestand.</aside>"
            "<p>Projecten: " + escape(", ".join(report["chosen_projects"])) +
            ". Bronmomenten samenhangend: " +
            ("ja" if report["source_clocks_within_10_minutes"] else
             "NEE — controleer eerst nieuwe issue-evidence") + "</p><main>" +
            "".join(cards) + "</main><footer><small>Issue SHA256 " +
            escape(report["product_issue_source_sha256"]) + "</small></footer></html>")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source_choice = parser.add_mutually_exclusive_group(required=True)
    source_choice.add_argument("--issue-board", type=Path,
                               help="Bestaande privé JSON-export van de Product Delivery Board")
    source_choice.add_argument("--live-issues", action="store_true",
                               help="Lees open GitHub productissues nu; alleen met --project (maximaal drie)")
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--project", action="append")
    parser.add_argument("--max-prs", type=int, default=10)
    parser.add_argument("--format", choices=("html", "json", "text", "markdown"),
                        default="html")
    args = parser.parse_args(argv)
    try:
        canonical = registry(args.registry)
        if args.live_issues:
            if not args.project or len(args.project) > MAX_PROJECTS:
                raise ValueError("--live-issues needs 1–3 explicit canonical --project values")
            # Reuse the already-merged canonical issue collector and triage
            # policy, rather than creating a second issue/PR search.
            live_registry = canonical_projects(args.registry)
            if canonical != live_registry:
                raise ValueError("canonical registry disagreement")
            if args.max_prs > 12:
                raise ValueError("live issue + PR mode caps --max-prs at 12 per project")
            inbox = collect_live_issues(canonical, args.project,
                                        os.environ.get("GITHUB_TOKEN"), max_items=60)
            source = prepare_delivery_board(inbox, limit=60)
            raw = json.dumps(source, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
            sha = hashlib.sha256(raw).hexdigest()
        else:
            source, sha = read_source_and_sha(args.issue_board)
        view = validated_view(source)
        projects = _safe_projects(view, canonical, args.project)
        if not projects:
            raise ValueError("no canonical product-issue projects in the selected issue source")
        pr_scope = collect_portfolio(projects, canonical, os.environ.get("GITHUB_TOKEN"), args.max_prs)
        report = compose(view, sha, canonical, projects, pr_scope,
                         saved_gates=_saved_pr_gates(source))
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.format == "html":
            print(output_html(report))
        else:
            print(output_text(report, markdown=args.format == "markdown"))
        return 2 if any(p["existing_prs"] and any(
            ref["file_scope_state"] == "shared_file_paths_observed"
            for ref in p["existing_prs"]) for p in report["issues"]) else (
            3 if not pr_scope["source_coverage_complete"] else 0)
    except (OSError, ValueError) as exc:
        print("zSentinel: source-integration unavailable — " + str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
