#!/usr/bin/env python3
"""Read-only exact-head GitHub PR delivery desk for already-open product work.

This is operator advice, NEVER a merge/release admission check. Each explicit
PR costs <=3 canonical GETs. Existing GitHub status, branch protection, owner
leases and required review policy must still be checked at the actual merge.
"""
from __future__ import annotations

import re
from html import escape
from urllib.parse import quote

from zsentinel_product_issue_inbox import bounded_get

SHA = re.compile(r"^[a-f0-9]{40}$")
OWNER_REPO = re.compile(r"^Zennay/[A-Za-z0-9_.-]{1,100}$")
MAX_PRS = 3
MAX_CHECKS = 30
MAX_REVIEWS = 30
MAX_CALLS = 3 * MAX_PRS
PASS = {"success", "neutral", "skipped"}


def _number(value):
    if type(value) is not int or not 1 <= value <= 100000000:
        raise ValueError("invalid PR number")
    return value


def _sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError("GitHub exact head or base SHA unavailable")
    return value


def observe_prs(registry, selections, token=None):
    """Capture exact source head, bounded check-runs and same-head reviews.

    No output claims that checks are *required*, full, protection-satisfying,
    owner-approved or merge-ready. Other PRs and unobserved jobs stay UNKNOWN.
    """
    if (not isinstance(registry, dict) or not isinstance(selections, list)
            or len(selections) > MAX_PRS):
        raise ValueError("invalid bounded PR selection")
    seen = set()
    items = []
    calls = 0
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
            item.update({
                "title": title, "head_sha": head_sha, "base_sha": base_sha,
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
            for check in runs:
                if not isinstance(check, dict) or type(check.get("name")) is not str:
                    raise ValueError("invalid check-run")
                name = check["name"][:100]
                status, conclusion = check.get("status"), check.get("conclusion")
                if status != "completed":
                    waiting.append(name)
                elif conclusion not in PASS:
                    failing.append(name)
            item["ci"] = {
                "head_sha": head_sha, "sampled": len(runs),
                "total_reported": total, "all_checks_observed": total == len(runs),
                "failed": failing[:8], "pending": waiting[:8],
                "branch_protection_verified": False,
                "status_contexts_verified": False,
            }
            item["coverage"] = "exact_head_checks_observed"
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
            item["coverage"] = "bounded_exact_head_review_and_checks"
            if item["draft"]:
                next_step = "finish_existing_draft_with_owner"
            elif requested_changes:
                next_step = "resolve_exact_head_review_with_owner"
            elif failing:
                next_step = "fix_exact_head_ci_with_owner"
            elif waiting or total != len(runs):
                next_step = "wait_for_complete_exact_head_ci"
            elif approvals == 0:
                next_step = "request_independent_review"
            else:
                # Even all observed green + one approval NEVER grant merge.
                next_step = "confirm_branch_protection_and_release_with_owner"
            item["next_step"] = next_step
        except (OSError, ValueError, TypeError, KeyError) as exc:
            item["source_gap"] = str(exc)[:120]
            item["next_step"] = "verify_pr_source_with_owner"
        items.append(item)
    return {
        "schema": "zsentinel-pr-delivery-desk-v1",
        "source": "github_canonical_readonly_bounded_pr_gets",
        "items": items, "request_count": calls,
        "maximum_requests": MAX_CALLS,
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
    labels = {
        "finish_existing_draft_with_owner": "Rond bestaande concept-PR af met eigenaar.",
        "resolve_exact_head_review_with_owner": "Verwerk wijzigingsverzoeken van huidige source head met eigenaar.",
        "fix_exact_head_ci_with_owner": "Los de daadwerkelijk falende head-checks op met eigenaar.",
        "wait_for_complete_exact_head_ci": "Wacht op ontbrekende of lopende exact-head CI; blijf aan ander werk bouwen.",
        "request_independent_review": "Vraag onafhankelijke review; geen automatische merge.",
        "confirm_branch_protection_and_release_with_owner": "Laat eigenaar volledige branchebescherming en releasevoorwaarden controleren.",
        "verify_pr_source_with_owner": "PR-bron onvoldoende bereikbaar; eigenaar moet actuele GitHub-status controleren.",
    }
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
        detail = ("Geobserveerde checks: " + str(ci.get("sampled", "onbekend")) +
                  " / " + str(ci.get("total_reported", "onbekend")) +
                  " · fouten ≥" + str(len(ci.get("failed", []))) +
                  " · bezig ≥" + str(len(ci.get("pending", []))) +
                  " · head-approvals gezien: " +
                  str(reviews.get("same_head_approvals_seen", "onbekend")))
        sha = row.get("head_sha")
        cards.append(
            '<article class="panel read"><h3><a href="' + link +
            '" rel="noopener noreferrer" target="_blank">' + name +
            " · " + title + '</a></h3><p>' +
            escape(labels[row["next_step"]]) +
            '</p><p class="muted">' + escape(detail) + '</p><p class="small">Exacte head: ' +
            escape(sha[:12] if isinstance(sha, str) else "onbekend") +
            ' · bron: beperkte GitHub-steekproef; eigenaar, required checks, branch protection, merge/deploy NIET geautoriseerd.</p></article>'
        )
    return ('<section><h2>Bestaande PR’s afronden · exacte bronstatus</h2>'
            '<p class="banner">Eerst bestaande code integreren. Alleen-lezen bronstatus voor maximaal 3 expliciet geselecteerde PR’s; '
            'geen compleet check-/review-overzicht, geen toestemmings- of eigendomsbewijs.</p>'
            '<div class="grid">' + ("".join(cards) if cards else
                '<p class="empty">Geen bestaande PR’s geselecteerd; dit bewijst niet dat er geen open PR’s zijn.</p>') +
            '</div></section>')
