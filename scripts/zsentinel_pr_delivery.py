#!/usr/bin/env python3
"""Read-only exact-head GitHub PR delivery desk for already-open product work.

This is operator advice, NEVER a merge/release admission check. Each explicit
PR costs <=5 canonical GETs. Existing GitHub status, branch protection, owner
leases and required review policy must still be checked at the actual merge.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import quote

from zsentinel_product_issue_inbox import ROOT, bounded_get, canonical_projects, GithubAdmissionError

SHA = re.compile(r"^[a-f0-9]{40}$")
OWNER_REPO = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")
MAX_PRS = 3
MAX_CHECKS = 30
MAX_REVIEWS = 30
MAX_STATUSES = 30
MAX_CALLS = 5 * MAX_PRS
FAILURE = {"failure", "timed_out", "cancelled", "action_required"}

NEXT_ACTION_NL = {
    "finish_existing_draft_with_owner": "Rond bestaande concept-PR af met eigenaar.",
    "resolve_exact_head_review_with_owner": "Verwerk wijzigingsverzoeken van huidige source head met eigenaar.",
    "fix_exact_head_ci_with_owner": "Los de daadwerkelijk falende head-checks op met eigenaar.",
    "inspect_unlisted_status_contexts_with_owner": "Gecombineerde GitHub-status meldt fout; open actuele PR-checks met eigenaar, want context staat niet in de beperkte steekproef.",
    "wait_for_complete_exact_head_ci": "Wacht op ontbrekende of lopende exact-head CI; blijf aan ander werk bouwen.",
    "request_independent_review": "Vraag onafhankelijke review; geen automatische merge.",
    "confirm_branch_protection_and_release_with_owner": "Laat eigenaar volledige branchebescherming en releasevoorwaarden controleren.",
    "verify_pr_source_with_owner": "PR-bron onvoldoende bereikbaar; eigenaar moet actuele GitHub-status controleren.",
    "resolve_hosted_billing_with_account_owner": "GitHub weigert de hosted job te starten wegens facturatie of bestedingslimiet. Alleen de bevoegde accountbeheerder kan dit herstellen; geen codefix, retry of runneromweg.",
}


def _number(value):
    if type(value) is not int or not 1 <= value <= 100000000:
        raise ValueError("invalid PR number")
    return value


def _sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError("GitHub exact head or base SHA unavailable")
    return value


def discover_open_prs(registry, selected_projects, token=None, limit=MAX_PRS,
                      exclude=None):
    """Find existing PRs before suggesting duplicate source work.

    One GitHub GET per canonical project, bounded to the 20 most recently
    updated open PRs. This is source discovery, not active owner evidence.
    The sampled shortlist is fair across repositories for each priority.
    """
    if (not isinstance(registry, dict) or not isinstance(selected_projects, list)
            or not 1 <= len(selected_projects) <= 3
            or len(set(selected_projects)) != len(selected_projects)
            or type(limit) is not int or not 1 <= limit <= MAX_PRS):
        raise ValueError("invalid existing-PR discovery scope")
    exclude = [] if exclude is None else exclude
    if (not isinstance(exclude, list) or len(exclude) > MAX_PRS
            or any(not isinstance(x, dict)
                   or set(x) != {"project_id", "pr_number"}
                   for x in exclude)):
        raise ValueError("invalid existing-PR exclusions")
    blocked = {(x["project_id"], _number(x["pr_number"])) for x in exclude}
    rows = {}
    coverage = []
    calls = 0
    rate_limited = False
    for pid in selected_projects:
        repo = registry.get(pid) if isinstance(pid, str) else None
        if not isinstance(repo, str) or not OWNER_REPO.fullmatch(repo):
            raise ValueError("unknown canonical PR discovery project")
        if rate_limited:
            coverage.append({"project_id": pid, "status": "not_queried_after_auth_or_rate_limit"})
            continue
        calls += 1
        url = ("https://api.github.com/repos/" + quote(repo, safe="/") +
               "/pulls?state=open&sort=updated&direction=desc&per_page=20&page=1")
        try:
            results = bounded_get(url, token)
            if not isinstance(results, list) or len(results) > 20:
                raise ValueError("invalid open PR page")
            found = []
            seen = set()
            excluded_qa_only = 0
            for source_rank, pr in enumerate(results):
                if not isinstance(pr, dict):
                    continue
                number = pr.get("number")
                title = pr.get("title")
                if (type(number) is not int or not 1 <= number <= 100000000
                        or number in seen or type(title) is not str
                        or not 1 <= len(title) <= 180
                        or any(ord(c) < 32 or ord(c) == 127 for c in title)):
                    continue
                seen.add(number)
                if pr.get("state") != "open" or (pid, number) in blocked:
                    continue
                # A flying product fixer must not route explicit QA-only work
                # ahead of implementation. The branch prefix, title prefix
                # and structured labels are evidence; a generic "CI" title
                # alone is NOT proof of QA-only work (release CI may be P0).
                labels = pr.get("labels")
                qa_labels = (isinstance(labels, list) and any(
                    isinstance(label, dict) and label.get("name") in (
                        "quality-validation", "qa-validation", "qa-only")
                    for label in labels[:30]))
                head = pr.get("head")
                qa_branch = (isinstance(head, dict)
                             and isinstance(head.get("ref"), str)
                             and head["ref"].lower().startswith(("qa/", "test/", "tests/")))
                qa_title = bool(re.match(
                    r"^(?:test|tests|qa|quality[-_ ]validation)(?:\b|\s*\()",
                    title.strip(), flags=re.IGNORECASE))
                if qa_labels or qa_branch or qa_title:
                    excluded_qa_only += 1
                    continue
                # Only explicit title labels affect ranking. No invented P0.
                urgency = re.match(r"^\s*(?:\[(P[01])\]|(P[01])(?=\s|:|-))", title, re.I)
                priority = (urgency.group(1) or urgency.group(2)).upper() if urgency else "Unspecified"
                found.append({
                    "project_id": pid, "pr_number": number,
                    "priority_from_title": priority,
                    "draft_from_listing": pr.get("draft") is True,
                    "source_updated_rank": source_rank,
                    "owner_verified": False,
                })
            rows[pid] = sorted(
                found, key=lambda row: (
                    0 if row["priority_from_title"] == "P0" else
                    1 if row["priority_from_title"] == "P1" else 2,
                    row["draft_from_listing"], row["source_updated_rank"]
                ))
            coverage.append({"project_id": pid,
                             "status": "bounded_truncated" if len(results) == 20
                             else "bounded_sample",
                             "returned": len(results),
                             "excluded_explicit_qa_only": excluded_qa_only,
                             "all_open_prs_observed": False})
        except (OSError, ValueError) as exc:
            reason = str(exc)
            coverage.append({"project_id": pid, "status": "degraded",
                             "reason": reason[:90],
                             "provider_admission": (
                                 exc.category if isinstance(exc, GithubAdmissionError)
                                 else None),
                             "all_open_prs_observed": False})
            # HTTP 401/403 can be scoped to this repository; continue
            # reading unrelated project PRs. A confirmed provider rate-limit
            # is the only cross-project stop condition.
            if (isinstance(exc, GithubAdmissionError)
                    and exc.category == "rate_limit_confirmed"):
                rate_limited = True
    picked = []
    for priority in ("P0", "P1", "Unspecified"):
        while len(picked) < limit:
            advanced = False
            for pid in selected_projects:
                group = rows.get(pid, [])
                position = next((i for i, row in enumerate(group)
                                 if row["priority_from_title"] == priority), None)
                if position is not None:
                    row = group.pop(position)
                    picked.append({"project_id": pid, "pr_number": row["pr_number"]})
                    advanced = True
                    if len(picked) >= limit:
                        break
            if not advanced:
                break
    return {"schema": "zsentinel-open-pr-discovery-v1",
            "selection": picked, "coverage": coverage,
            "calls": calls, "max_calls": len(selected_projects),
            "all_open_prs_observed": False,
            "owner_verified": False, "writes_performed": False,
            "merge_authorized": False}


def observe_prs(registry, selections, token=None, inspect_no_start_annotations=False):
    """Capture exact source head, bounded check-runs and same-head reviews.

    No output claims that checks are *required*, full, protection-satisfying,
    owner-approved or merge-ready. Other PRs and unobserved jobs stay UNKNOWN.
    """
    if (not isinstance(registry, dict) or not isinstance(selections, list)
            or len(selections) > MAX_PRS or type(inspect_no_start_annotations) is not bool):
        raise ValueError("invalid bounded PR selection")
    seen = set()
    items = []
    calls = 0
    rate_limited = False
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    annotations_denied = False
    for row in selections:
        if not isinstance(row, dict) or set(row) != {"project_id", "pr_number"}:
            raise ValueError("invalid PR selection row")
        project, number = row["project_id"], _number(row["pr_number"])
        repo = registry.get(project) if isinstance(project, str) else None
        if (not isinstance(repo, str) or not OWNER_REPO.fullmatch(repo)
                or (project, number) in seen):
            raise ValueError("unknown/duplicate canonical PR")
        seen.add((project, number))
        base_url = "https://api.github.com/repos/" + quote(repo, safe="/")
        item = {
            "project_id": project, "repository": repo, "pr_number": number,
            "url": "https://github.com/" + repo + "/pull/" + str(number),
            "source": "github_exact_pr_readonly", "coverage": "unavailable",
            "action_authorized": False, "merge_authorized": False,
            "deploy_authorized": False, "owner_verified": False,
        }
        if rate_limited:
            item["coverage"] = "not_queried_after_auth_or_rate_limit"
            item["next_step"] = "verify_pr_source_with_owner"
            items.append(item)
            continue
        try:
            calls += 1
            pr = bounded_get(base_url + "/pulls/" + str(number), token)
            head = pr.get("head") if isinstance(pr, dict) else None
            base = pr.get("base") if isinstance(pr, dict) else None
            base_repo = base.get("repo") if isinstance(base, dict) else None
            if (not isinstance(pr, dict) or pr.get("number") != number
                    or pr.get("state") != "open"
                    or not isinstance(base_repo, dict)
                    or base_repo.get("full_name") != repo
                    or not isinstance(head, dict)
                    or not isinstance(base, dict)):
                raise ValueError("PR no longer open in canonical base")
            head_sha, base_sha = _sha(head.get("sha")), _sha(base.get("sha"))
            title = pr.get("title")
            if (type(title) is not str or not 1 <= len(title) <= 180
                    or any(ord(ch) < 32 or ord(ch) == 127 for ch in title)):
                raise ValueError("untrusted PR title")
            # The PR body may mention an issue without fixing it. Display
            # bounded SAME-REPO references only, never infer resolution.
            body = pr.get("body")
            if body is not None and (type(body) is not str or len(body) > 32000):
                raise ValueError("unbounded PR body")
            numbers = []
            for match in re.finditer(r"(?<![A-Za-z0-9/_])#([1-9][0-9]{0,7})\b", body or ""):
                number_ref = int(match.group(1))
                if number_ref not in numbers and len(numbers) < 5:
                    numbers.append(number_ref)
            item.update({
                "title": title, "head_sha": head_sha, "base_sha": base_sha,
                "mentioned_issues_unverified": numbers,
                "draft": pr.get("draft") is True,
                "mergeable": pr.get("mergeable") is True,
                "mergeable_known": pr.get("mergeable") is not None,
                "coverage": "pr_metadata_only",
            })
            # GitHub's check-runs API is not the branch-protection contract.
            # Capture a small exact-head window without inferring missing
            # required checks to be green. Counts are explicitly lower bounds.
            calls += 1
            checks = bounded_get(base_url + "/commits/" + head_sha +
                                 "/check-runs?per_page=" + str(MAX_CHECKS), token)
            runs = checks.get("check_runs") if isinstance(checks, dict) else None
            total = checks.get("total_count") if isinstance(checks, dict) else None
            if (not isinstance(runs, list) or len(runs) > MAX_CHECKS
                    or type(total) is not int or total < len(runs)):
                raise ValueError("untrusted exact-head check-run page")
            failing = []
            waiting = []
            inconclusive = []
            hosted_billing_start_denial = False
            for check in runs:
                if not isinstance(check, dict) or type(check.get("name")) is not str:
                    raise ValueError("invalid check-run")
                name = check["name"][:100]
                status, conclusion = check.get("status"), check.get("conclusion")
                if status != "completed":
                    waiting.append(name)
                elif conclusion in FAILURE:
                    failing.append(name)
                    # GitHub can refuse hosted checks before running product code.
                    # Only observed exact-head check metadata qualifies as evidence;
                    # PR body text or check names alone cannot establish billing.
                    output = check.get("output")
                    if check.get("head_sha") == head_sha and isinstance(output, dict):
                        details = (output.get("title"), output.get("summary"))
                        if all(isinstance(v, (str, type(None))) for v in details):
                            signal = " ".join(v[:8000] for v in details if v).lower()
                            if ("job was not started" in signal and
                                    ("payments have failed" in signal or
                                     "spending limit" in signal)):
                                hosted_billing_start_denial = True
                elif conclusion != "success":
                    # neutral/skipped/stale/unknown are NOT successful proof.
                    # Do not mislabel a skipped workflow as a failed build.
                    inconclusive.append(name)
            item["ci"] = {
                "head_sha": head_sha, "sampled": len(runs),
                "total_reported": total, "all_checks_observed": total == len(runs),
                "failed": failing[:8], "pending": waiting[:8],
                "inconclusive": inconclusive[:8],
                "observed_hosted_billing_start_denial": hosted_billing_start_denial,
                "branch_protection_verified": False,
                "status_contexts_verified": False,
            }
            item["coverage"] = "exact_head_checks_observed"
            # Extra source is opt-in and costs at most ONE exact failed-check
            # GitHub annotation GET per PR. Read message strings only to
            # identify platform start refusal. Never return annotation text,
            # paths, credentials, headers or user-generated failure output.
            if inspect_no_start_annotations and failing and not hosted_billing_start_denial:
                item["ci"]["no_start_annotation_coverage"] = "no_exact_check_selected"
                candidate = next((c for c in runs if (
                    isinstance(c, dict) and c.get("conclusion") in FAILURE
                    and c.get("head_sha") == head_sha
                    and type(c.get("id")) is int
                    and 1 <= c["id"] <= 2**53 - 1
                )), None)
                if candidate is not None and not annotations_denied:
                    calls += 1
                    try:
                        notes = bounded_get(
                            base_url + "/check-runs/" + str(candidate["id"]) +
                            "/annotations?per_page=10", token)
                        if not isinstance(notes, list) or len(notes) > 10:
                            raise ValueError("invalid bounded check annotations")
                        for note in notes:
                            if not isinstance(note, dict):
                                raise ValueError("invalid check annotation row")
                            message = note.get("message")
                            if type(message) is not str or len(message) > 12000:
                                raise ValueError("invalid check annotation message")
                            signal = message.lower()
                            if ("job was not started" in signal and
                                    ("payments have failed" in signal or
                                     "spending limit" in signal)):
                                hosted_billing_start_denial = True
                        item["ci"]["no_start_annotation_coverage"] = (
                            "bounded_exact_head_check_annotations")
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        # A source or permission denial degrades ONLY this
                        # optional diagnosis, not the core PR/CI evidence.
                        item["ci"]["no_start_annotation_coverage"] = "unavailable"
                        if (isinstance(error, GithubAdmissionError)
                                and error.category == "rate_limit_confirmed"):
                            annotations_denied = True
                elif annotations_denied:
                    item["ci"]["no_start_annotation_coverage"] = "not_queried_after_denial"
                item["ci"]["observed_hosted_billing_start_denial"] = (
                    hosted_billing_start_denial)
            # The GitHub check-runs API does not include legacy commit status
            # contexts. Read the exact same SHA separately; still never treat
            # this bounded page as proof that branch protection has passed.
            calls += 1
            legacy = bounded_get(base_url + "/commits/" + head_sha +
                                 "/status?per_page=" + str(MAX_STATUSES), token)
            statuses = legacy.get("statuses") if isinstance(legacy, dict) else None
            combined = legacy.get("state") if isinstance(legacy, dict) else None
            if (not isinstance(statuses, list) or len(statuses) > MAX_STATUSES
                    or combined not in ("pending", "success", "failure", "error")):
                raise ValueError("untrusted exact-head legacy status page")
            latest_context = {}
            for status_row in statuses:
                if not isinstance(status_row, dict):
                    raise ValueError("untrusted commit status context")
                context = status_row.get("context")
                state = status_row.get("state")
                if (type(context) is not str or not 1 <= len(context) <= 180
                        or any(ord(ch) < 32 or ord(ch) == 127 for ch in context)
                        or state not in ("pending", "success", "failure", "error")):
                    raise ValueError("untrusted commit status state")
                # Combined status listings are newest-first. Only the first
                # observation for a context is its current visible state.
                latest_context.setdefault(context, state)
            legacy_failed = [n for n, s in latest_context.items()
                             if s in ("failure", "error")]
            legacy_pending = [n for n, s in latest_context.items()
                              if s == "pending"]
            item["legacy_status_contexts"] = {
                "head_sha": head_sha,
                "combined_observed": combined,
                "sampled": len(statuses),
                "unique_contexts_seen": len(latest_context),
                "page_truncated_or_unknown": len(statuses) == MAX_STATUSES,
                "failed": legacy_failed[:8],
                "pending": legacy_pending[:8],
                "branch_protection_verified": False,
                "required_contexts_verified": False,
            }
            calls += 1
            reviews = bounded_get(base_url + "/pulls/" + str(number) +
                                  "/reviews?per_page=" + str(MAX_REVIEWS), token)
            if not isinstance(reviews, list) or len(reviews) > MAX_REVIEWS:
                raise ValueError("untrusted PR review page")
            # A historic approval for a DIFFERENT source head is not current.
            # Review API order is chronological; last verdict per reviewer
            # wins inside this bounded window, without granting approval.
            latest = {}
            author = pr.get("user")
            author_login = author.get("login") if isinstance(author, dict) else None
            for review in reviews:
                user = review.get("user") if isinstance(review, dict) else None
                login = user.get("login") if isinstance(user, dict) else None
                if (not isinstance(login, str) or len(login) > 100
                        or login == author_login or not isinstance(review, dict)):
                    continue
                state = review.get("state")
                if state not in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                    continue
                if review.get("commit_id") == head_sha:
                    latest[login] = state
            approvals = sum(1 for state in latest.values() if state == "APPROVED")
            requested_changes = any(state == "CHANGES_REQUESTED" for state in latest.values())
            item["reviews"] = {
                "head_sha": head_sha, "same_head_approvals_seen": approvals,
                "same_head_changes_requested": requested_changes,
                "review_page_full": len(reviews) == MAX_REVIEWS,
                "independent_policy_verified": False,
            }
            # Re-read the PR AFTER both evidence calls. A branch force-push
            # while collecting CI/reviews must not be presented as a single
            # up-to-date source snapshot, even in this read-only desk.
            calls += 1
            after = bounded_get(base_url + "/pulls/" + str(number), token)
            later_head = after.get("head") if isinstance(after, dict) else None
            later_base = after.get("base") if isinstance(after, dict) else None
            if (not isinstance(after, dict) or after.get("number") != number
                    or after.get("state") != "open"
                    or after.get("draft") is not item["draft"]
                    or not isinstance(later_head, dict)
                    or not isinstance(later_base, dict)
                    or later_head.get("sha") != head_sha
                    or later_base.get("sha") != base_sha):
                raise ValueError("PR source changed while reading exact-head checks/reviews")
            item["coverage"] = "bounded_exact_head_review_and_checks"
            if item["draft"]:
                next_step = "finish_existing_draft_with_owner"
            elif requested_changes:
                next_step = "resolve_exact_head_review_with_owner"
            elif (hosted_billing_start_denial and len(failing) == 1
                  and total == len(runs) and not legacy_failed):
                next_step = "resolve_hosted_billing_with_account_owner"
            elif failing or legacy_failed:
                next_step = "fix_exact_head_ci_with_owner"
            elif combined in ("failure", "error"):
                # The combined source reports a failure but the sampled page
                # did not identify it: never suggest review or merge.
                next_step = "inspect_unlisted_status_contexts_with_owner"
            elif (waiting or inconclusive or legacy_pending
                  or combined == "pending" or len(statuses) == MAX_STATUSES
                  or total != len(runs)):
                next_step = "wait_for_complete_exact_head_ci"
            elif approvals == 0:
                next_step = "request_independent_review"
            else:
                # Even all observed green + one approval NEVER grant merge.
                next_step = "confirm_branch_protection_and_release_with_owner"
            item["next_step"] = next_step
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if (isinstance(exc, GithubAdmissionError)
                    and exc.category == "rate_limit_confirmed"):
                rate_limited = True
            # Partially observed checks or a moved source HEAD cannot be
            # displayed as current readiness. Keep only the safe PR link.
            for field in ("ci", "legacy_status_contexts", "reviews", "head_sha", "base_sha", "mentioned_issues_unverified"):
                item.pop(field, None)
            item["coverage"] = "source_incomplete_or_changed"
            item["source_gap"] = str(exc)[:120]
            if isinstance(exc, GithubAdmissionError):
                item["provider_admission"] = exc.category
            item["next_step"] = "verify_pr_source_with_owner"
        items.append(item)
    return {
        "schema": "zsentinel-pr-delivery-desk-v1",
        "source": "github_canonical_readonly_bounded_pr_gets",
        "items": items, "request_count": calls,
        "observed_at_utc": observed_at,
        "maximum_requests": MAX_CALLS + (MAX_PRS if inspect_no_start_annotations else 0),
        "merge_authorized": False, "deploy_authorized": False,
        "owner_verified": False, "writes_performed": False,
    }


def pr_delivery_html(report):
    if (not isinstance(report, dict)
            or report.get("schema") != "zsentinel-pr-delivery-desk-v1"
            or report.get("merge_authorized") is not False
            or report.get("writes_performed") is not False
            or type(report.get("items")) is not list
            or len(report["items"]) > MAX_PRS):
        raise ValueError("invalid PR desk receipt")
    labels = NEXT_ACTION_NL
    cards = []
    for row in report["items"]:
        if (not isinstance(row, dict) or row.get("source") != "github_exact_pr_readonly"
                or row.get("merge_authorized") is not False
                or row.get("next_step") not in labels):
            raise ValueError("invalid PR item")
        name = escape(str(row["project_id"]) + " · PR #" + str(row["pr_number"]))
        title = escape(str(row.get("title") or "Bronmetadata onbekend")[:180])
        link = escape(row["url"], quote=True)
        ci = row.get("ci", {})
        reviews = row.get("reviews", {})
        legacy = row.get("legacy_status_contexts", {})
        detail = ("Geobserveerde checks: " + str(ci.get("sampled", "onbekend")) +
                  " / " + str(ci.get("total_reported", "onbekend")) +
                  " · fouten ≥" + str(len(ci.get("failed", []))) +
                  " · bezig ≥" + str(len(ci.get("pending", []))) +
                  " · niet-groen/onbeslist ≥" + str(len(ci.get("inconclusive", []))) +
                  " · klassiek gecombineerd " + str(legacy.get("combined_observed", "onbekend")) +
                  " · klassieke statusfouten ≥" + str(len(legacy.get("failed", []))) +
                  " · klassieke statussen wachten ≥" + str(len(legacy.get("pending", []))) +
                  " · head-approvals gezien: " +
                  str(reviews.get("same_head_approvals_seen", "onbekend")) +
                  (" · checkannotaties: " +
                   str(ci["no_start_annotation_coverage"])[:60]
                   if ci.get("no_start_annotation_coverage") else "") +
                  (" · GitHub-platformstart geweigerd (billing/limiet; eigenaar)"
                   if ci.get("observed_hosted_billing_start_denial") is True else ""))
        sha = row.get("head_sha")
        ci_names = []
        for kind, name in (("Mislukt", "failed"), ("Nog bezig", "pending"),
                           ("Onbeslist", "inconclusive")):
            for check_name in ci.get(name, [])[:6]:
                ci_names.append("<li>" + escape(kind + ": " + str(check_name)[:100]) + "</li>")
        for kind, name in (("Klassieke status mislukt", "failed"),
                           ("Klassieke status wacht", "pending")):
            for context_name in legacy.get(name, [])[:6]:
                ci_names.append("<li>" + escape(kind + ": " +
                                                str(context_name)[:180]) + "</li>")
        observed_checks = (
            '<details><summary>Welke head-checks vragen aandacht?</summary><ul>' +
            "".join(ci_names) + '</ul><p class="small"><a href="' + link +
            '/checks" target="_blank" rel="noopener noreferrer">Bekijk actuele GitHub PR-checks</a>'
            ' · lijst is een beperkte momentopname.</p></details>'
            if ci_names else "")
        legacy_gap = ('<p class="muted">Klassieke statuscontexts zijn slechts '
                      'gedeeltelijk waargenomen: de eerste 30 kunnen ouder '
                      'of onvolledig zijn.</p>' if
                      legacy.get("page_truncated_or_unknown") else "")
        provider_descriptions = {
            "authentication_required": "Authenticatie nodig; bevoegd eigenaar controleert GitHub-toegang.",
            "rate_limit_confirmed": "GitHub-rate-limit bevestigd; geen CI-retries of extra verzoeken starten.",
            "forbidden_or_rate_limit_unknown": "GitHub 403: repo-permissie of rate-limit ONBEKEND; eigenaar moet controleren.",
        }
        denial = row.get("provider_admission")
        if denial is not None and denial not in provider_descriptions:
            raise ValueError("invalid GitHub provider admission category")
        source_gap = (('<p class="muted">Bron onvolledig of gewijzigd: ' +
                       escape(provider_descriptions[denial] if denial else
                              str(row["source_gap"])[:120]) + '</p>')
                      if row.get("source_gap") else "")
        issue_refs = row.get("mentioned_issues_unverified", [])
        if (not isinstance(issue_refs, list) or len(issue_refs) > 5
                or any(type(n) is not int or n < 1 or n > 99999999 for n in issue_refs)):
            raise ValueError("untrusted PR body references")
        repo = row.get("repository")
        if not isinstance(repo, str) or not OWNER_REPO.fullmatch(repo):
            raise ValueError("noncanonical PR source repository")
        mention_links = ('<p class="small">In PR-tekst genoemde issues '
                         '(niet geverifieerd als open of opgelost): ' +
                         " · ".join('<a href="https://github.com/' +
                                  escape(repo, quote=True) + '/issues/' + str(n) +
                                  '" target="_blank" rel="noopener noreferrer">#' +
                                  str(n) + '</a>' for n in issue_refs) +
                         '</p>' if issue_refs else "")
        cards.append(
            '<article class="panel read"><h3><a href="' + link +
            '" rel="noopener noreferrer" target="_blank">' + name +
            " · " + title + '</a></h3><p>' +
            escape(labels[row["next_step"]]) +
            '</p><p class="muted">' + escape(detail) + '</p>' + observed_checks +
            mention_links + legacy_gap + source_gap + '<p class="small">Exacte head: ' +
            escape(sha[:12] if isinstance(sha, str) else "onbekend") +
            ' · bron: beperkte GitHub-steekproef; eigenaar, required checks, branch protection, merge/deploy NIET geautoriseerd.</p></article>'
        )
    discovery = report.get("open_pr_discovery")
    discovery_panel = ""
    if discovery is not None:
        if (not isinstance(discovery, dict) or discovery.get("merge_authorized") is not False
                or discovery.get("all_open_prs_observed") is not False):
            raise ValueError("untrusted open PR discovery source")
        coverage = discovery.get("coverage")
        if isinstance(coverage, list):
            if len(coverage) > 3:
                raise ValueError("unbounded open PR coverage")
            details = []
            for observed in coverage:
                if (not isinstance(observed, dict)
                        or observed.get("status") not in (
                            "bounded_sample", "bounded_truncated", "degraded",
                            "not_queried_after_auth_or_rate_limit")):
                    raise ValueError("invalid open PR coverage")
                pid = escape(str(observed.get("project_id", ""))[:40])
                status = observed["status"]
                message = {
                    "bounded_sample": "Laatste open PR’s in beperkte steekproef",
                    "bounded_truncated": "Meer PR’s dan de eerste 20 mogelijk; lijst ONVOLLEDIG",
                    "degraded": "GitHub bronfout: overige PR’s ONBEKEND",
                    "not_queried_after_auth_or_rate_limit": "Niet bekeken door rechten/rate limit",
                }[status]
                denial = observed.get("provider_admission")
                if denial is not None and denial not in (
                        "authentication_required", "rate_limit_confirmed",
                        "forbidden_or_rate_limit_unknown"):
                    raise ValueError("untrusted GitHub denial category")
                if denial == "rate_limit_confirmed":
                    message = "GitHub-rate-limit bevestigd; overige PR-bronnen uitgesteld"
                elif denial == "authentication_required":
                    message = "GitHub-authenticatie vereist; bevoegd eigenaar nodig"
                elif denial == "forbidden_or_rate_limit_unknown":
                    message = "GitHub 403; eigenaar controleert toegangsrechten/rate-limit"
                qa_count = observed.get("excluded_explicit_qa_only", 0)
                if type(qa_count) is not int or not 0 <= qa_count <= 20:
                    raise ValueError("invalid excluded QA source count")
                details.append("<li>" + pid + " · " + message +
                               (" · expliciet QA-only overgeslagen: " + str(qa_count)
                                if qa_count else "") + "</li>")
            discovery_panel = ('<details><summary>Open PR-brondekking</summary><ul>' +
                               "".join(details) + '</ul><p class="muted">Geen eigendom, '
                               'volledige backlog of herstel bewezen.</p></details>')
        elif coverage not in ("not_requested", "not_requested_capacity_full"):
            raise ValueError("invalid open PR discovery status")
    at = report.get("observed_at_utc")
    if at is not None and (type(at) is not str or len(at) > 40):
        raise ValueError("untrusted PR source observation clock")
    clock = ('<p class="muted">GitHub-bronmoment UTC: ' + escape(at) +
             '. Een latere GitHub-wijziging kan dit overzicht verouderen.</p>'
             if at else "")
    return ('<section><h2>Bestaande PR’s afronden · exacte bronstatus</h2>' + clock +
            '<p class="banner">Eerst bestaande code integreren. Alleen-lezen bronstatus voor maximaal 3 '
            'expliciet gekozen of beperkt ontdekte PR’s; geen compleet check-/review-overzicht, '
            'geen toestemmings- of eigendomsbewijs.</p>' + discovery_panel +
            '<div class="grid">' + ("".join(cards) if cards else
                '<p class="empty">Geen bestaande PR’s geselecteerd of waargenomen; '
                'dit bewijst niet dat er geen open PR’s zijn.</p>') +
            '</div></section>')


def standalone_html(report):
    """Script-free, network-free output once GitHub metadata was read."""
    from zsentinel_recovery_view import CSS
    body = pr_delivery_html(report)
    return (
        '<!doctype html><html lang="nl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        'style-src \'unsafe-inline\'; base-uri \'none\'; img-src \'none\'; '
        'connect-src \'none\'; form-action \'none\'">'
        '<title>zSentinel · Bestaande PR-afronding</title><style>' + CSS +
        '</style></head><body><header><h1>zSentinel · Bestaande PR-afronding</h1>'
        '<p class="muted">Privé, brongebonden operatoroverzicht. Geen live '
        'merge-/runner-/claim- of productiebevoegdheid.</p></header><main>' +
        body + '</main><footer class="muted">Geen automatische retry, review, '
        'merge, deploy, claim of gegevensverzending vanuit dit HTML-document.'
        '</footer></body></html>'
    )


def _markdown_literal(value, limit=180):
    """Escape source text so PR titles/context names are not Markdown markup."""
    safe = str(value or "")[:limit].replace(chr(13), " ").replace(chr(10), " ")
    escaped = []
    for character in safe:
        escaped.append(chr(92) + character if character in (chr(92) + "`*_{}[]()#+-.!|>") else character)
    return "".join(escaped)


def handoff_markdown(report):
    """Explicit offline human handoff; no claims, review requests or writes."""
    if (not isinstance(report, dict)
            or report.get("schema") != "zsentinel-pr-delivery-desk-v1"
            or report.get("merge_authorized") is not False
            or report.get("writes_performed") is not False
            or not isinstance(report.get("items"), list)
            or len(report["items"]) > MAX_PRS):
        raise ValueError("invalid PR handoff source")
    stamp = report.get("observed_at_utc")
    if not isinstance(stamp, str) or len(stamp) > 40:
        raise ValueError("invalid PR source time")
    lines = [
        "# zSentinel · Bestaande PR's: menselijke overdracht",
        "",
        "Bronmoment UTC: " + _markdown_literal(stamp, 40) + ". Alleen een beperkte GitHub-momentopname.",
        "**Geen toestemming voor merge, claim, CI-retry, release of deploy.**",
        "Controleer de actuele eigenaar, complete required checks, onafhankelijke review en branch protection bij uitvoering.",
    ]
    for item in report["items"]:
        if (not isinstance(item, dict)
                or item.get("source") != "github_exact_pr_readonly"
                or item.get("merge_authorized") is not False
                or item.get("next_step") not in NEXT_ACTION_NL
                or not isinstance(item.get("repository"), str)
                or not OWNER_REPO.fullmatch(item["repository"])):
            raise ValueError("invalid PR handoff item")
        repo = item["repository"]
        number = _number(item.get("pr_number"))
        url = "https://github.com/" + repo + "/pull/" + str(number)
        if item.get("url") != url:
            raise ValueError("PR handoff URL not canonical")
        sha = item.get("head_sha")
        sha_label = _sha(sha) if sha is not None else "ONBEKEND"
        title = _markdown_literal(item.get("title") or "Bronmetadata onbekend")
        ci = item.get("ci") or {}
        legacy = item.get("legacy_status_contexts") or {}
        reviews = item.get("reviews") or {}
        if not all(isinstance(section, dict) for section in (ci, legacy, reviews)):
            raise ValueError("invalid PR handoff details")
        lines.extend([
            "",
            "## [" + title + " · #" + str(number) + "](" + url + ")",
            "",
            "- **Probleem:** bestaande PR nog niet bewezen release-klaar; "
            + _markdown_literal(item.get("coverage"), 80) + ".",
            "- **Actie:** " + _markdown_literal(NEXT_ACTION_NL[item["next_step"]]),
            "- **Status:** head `" + sha_label + "`; onafhankelijke review/release niet geautoriseerd.",
            "- **Checks:** waargenomen " + str(ci.get("sampled", "onbekend"))
            + " / " + str(ci.get("total_reported", "onbekend"))
            + "; klassieke gecombineerd " + _markdown_literal(legacy.get("combined_observed", "onbekend"), 30)
            + "; head-approvals gezien " + str(reviews.get("same_head_approvals_seen", "onbekend")) + ".",
        ])
        references = item.get("mentioned_issues_unverified", [])
        if (not isinstance(references, list) or len(references) > 5
                or any(type(n) is not int or not 1 <= n <= 99999999
                       for n in references)):
            raise ValueError("invalid PR issue reference handoff")
        if references:
            issue_links = ", ".join(
                "[#" + str(n) + "](https://github.com/" + repo +
                "/issues/" + str(n) + ")" for n in references
            )
            lines.append("- **Genoemde issues (PR-tekst, open/opgelost NIET geverifieerd):** "
                         + issue_links)
        if ci.get("no_start_annotation_coverage"):
            lines.append("- **Job-startbron:** " +
                         _markdown_literal(ci["no_start_annotation_coverage"], 70) +
                         "; ontbrekende annotaties bewijzen geen gezonde jobstart.")
        if ci.get("observed_hosted_billing_start_denial") is True:
            lines.append("- **Apart platformprobleem:** GitHub heeft een hosted job vóór code-uitvoering geweigerd wegens facturatie/limiet. Alleen accountbeheerder; geen automatische betaling, retry of omweg.")
        for source, bucket in (("check-run", ci), ("klassieke status", legacy)):
            for state in ("failed", "pending", "inconclusive"):
                values = bucket.get(state, [])
                if not isinstance(values, list) or len(values) > 8:
                    raise ValueError("invalid bounded PR handoff evidence")
                for name in values:
                    lines.append("  - " + source + " " + state + ": "
                                 + _markdown_literal(name))
        if legacy.get("page_truncated_or_unknown"):
            lines.append("  - Klassieke statuspagina afgekapt: onbekende contexts kunnen ontbreken.")
        if item.get("source_gap"):
            lines.append("  - Bronprobleem: " + _markdown_literal(item["source_gap"], 120))
        lines.append("- **Bron:** [Open actuele PR en checks](" + url + "/checks).")
    if not report["items"]:
        lines.extend(["", "Geen geselecteerde PR's; dit bewijst niet dat er geen open PR's zijn."])
    lines.extend(["", "_Alle acties zijn menselijk en brongebonden; dit document voert niets uit._", ""])
    return chr(10).join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Offline operator output for bounded, live-read GitHub PR metadata")
    parser.add_argument("--registry", type=Path, default=ROOT / "projects.json")
    parser.add_argument("--pr", action="append", default=[],
                        help="canonical existing PR PROJECT:NUMBER; repeat up to three")
    parser.add_argument("--project", action="append", default=[],
                        help="canonical project to sample for existing open PRs")
    parser.add_argument("--inspect-hosted-no-start", action="store_true",
                        help="optional bounded exact failed-check annotation source, no retry or billing mutation")
    parser.add_argument("--discover-open", action="store_true",
                        help="read one bounded open-PR window per project before further source work")
    parser.add_argument("--format", choices=("html", "json", "text", "md"), default="html")
    args = parser.parse_args(argv)
    try:
        if len(args.pr) > MAX_PRS:
            raise ValueError("at most three existing PRs")
        if not args.pr and not args.discover_open:
            raise ValueError("provide --pr or --project PROJECT --discover-open")
        if args.discover_open and (not args.project or len(args.project) > 3):
            raise ValueError("discover-open requires one to three --project values")
        if args.project and not args.discover_open:
            raise ValueError("--project requires --discover-open")
        registry = canonical_projects(args.registry)
        selected = []
        for value in args.pr:
            if not isinstance(value, str):
                raise ValueError("invalid PR input")
            match = re.fullmatch(r"([a-z][a-z0-9_-]{0,39}):([1-9][0-9]{0,8})", value)
            if not match:
                raise ValueError("expected PROJECT:NUMBER")
            selected.append({
                "project_id": match.group(1), "pr_number": int(match.group(2))
            })
        discovery = None
        token = os.environ.get("GITHUB_TOKEN")
        if args.discover_open:
            if len(selected) < MAX_PRS:
                discovery = discover_open_prs(
                    registry, args.project, token=token,
                    limit=MAX_PRS - len(selected), exclude=selected)
                selected.extend(discovery["selection"])
            else:
                discovery = {"schema": "zsentinel-open-pr-discovery-v1",
                             "coverage": "not_requested_capacity_full",
                             "selection": [], "calls": 0,
                             "all_open_prs_observed": False,
                             "owner_verified": False,
                             "writes_performed": False, "merge_authorized": False}
        report = observe_prs(registry, selected, token=token,
                             inspect_no_start_annotations=args.inspect_hosted_no_start)
        report["open_pr_discovery"] = discovery if discovery is not None else {
            "coverage": "not_requested", "all_open_prs_observed": False,
            "merge_authorized": False}
        if args.format == "html":
            output = standalone_html(report)
        elif args.format == "json":
            output = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2)
        elif args.format == "md":
            output = handoff_markdown(report)
        else:
            lines = ["zSentinel · bestaande PR-afronding; maximaal 3 read-only bronnen"]
            if discovery is not None:
                lines.append("Open PR discovery: bounded, not all branches or active owners.")
            for item in report["items"]:
                lines.append(
                    "%s #%s | head=%s | bron=%s | volgende actie=%s | %s" % (
                        item["project_id"], item["pr_number"],
                        item.get("head_sha", "ONBEKEND")[:12],
                        item["coverage"], item["next_step"], item["url"]
                    )
                )
                for name in ("failed", "pending", "inconclusive"):
                    if item.get("ci", {}).get(name):
                        lines.append("  check-%s: %s" % (
                            name, ", ".join(item["ci"][name])))
                if item.get("ci", {}).get("no_start_annotation_coverage"):
                    lines.append("  job-startbron: " +
                                 item["ci"]["no_start_annotation_coverage"][:70])
                if item.get("ci", {}).get("observed_hosted_billing_start_denial") is True:
                    lines.append("  owner-only: hosted GitHub job start geweigerd (billing/limiet)")
                if item.get("legacy_status_contexts"):
                    lines.append("  classic-status-combined: " +
                                 item["legacy_status_contexts"]["combined_observed"])
                for name in ("failed", "pending"):
                    if item.get("legacy_status_contexts", {}).get(name):
                        lines.append("  status-%s: %s" % (
                            name, ", ".join(item["legacy_status_contexts"][name])))
            lines.append("Geen eigenaar/required CI/review/merge/deploy geautoriseerd.")
            output = "\n".join(lines)
        print(output)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, "zSentinel PR-afronding niet beschikbaar: " + str(exc)[:160] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
