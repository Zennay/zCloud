#!/usr/bin/env python3
"""Create private, actionable, source-bound zSentinel PR handoffs.

This is an offline operator work order, never a claim, check rerun, merge,
approval, or deployment request. Selected PRs are a bounded sample.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

from zsentinel_pr_scope_guard import edit_path

SCHEMA = "zsentinel-private-pr-workorders-v1"
BOARD = "zsentinel-portfolio-pr-collision-board-v1"
SHA = re.compile(r"^[0-9a-f]{40}$")
PR = re.compile(r"^https://github\.com/(Zennay/[A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,8})$")
MAX_PRS = 4
MAX_PATHS = 24
MAX_CHECKS = 24
MAX_ISSUE_HINTS = 12


def _timestamp(value):
    if type(value) is not str or len(value) > 48:
        raise ValueError("missing bounded UTC source timestamp")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("unparseable UTC source timestamp") from None
    if instant.tzinfo is None or instant > datetime.now(timezone.utc):
        raise ValueError("future or timezone-free source")
    return instant.astimezone(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()


def _same_repo_url(value, repository):
    if type(value) is not str or len(value) > 240:
        raise ValueError("invalid PR URL")
    match = PR.fullmatch(value)
    if match is None or match.group(1) != repository:
        raise ValueError("untrusted or external PR reference")
    return value


def _markdown(value):
    """Bound opaque provider text; never allow raw Markdown headings/links."""
    if type(value) is not str:
        raise ValueError("operator text must be a string")
    if len(value) > 500 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("unsafe operator text")
    return re.sub(r"([\\\[\]_*\x60<>])", r"\\\1", value)


def _observed_overlap_paths(view, pr_url, head, direct_overlaps=None):
    """Only observed shared filenames, including an older focused PR."""
    paths, related, hinted_issues = set(), set(), set()
    paths_limited = False
    for pair in view.get("shown_collision_pairs", [])[:80]:
        left, right = pair["left"], pair["right"]
        if left["url"] == pr_url:
            related.add(right["url"])
        elif right["url"] == pr_url:
            related.add(left["url"])
        else:
            continue
        for path in pair.get("shared_files", [])[:MAX_PATHS]:
            paths.add(edit_path(path))
    for source in (direct_overlaps or [])[:16]:
        if type(source) is not dict:
            raise ValueError("invalid older PR collision observation")
        related.add(source["other_pr_url"])
        paths_limited |= (source.get("shared_paths_preview_truncated") is True or
                          source.get("other_pr_file_list_incomplete") is True)
        for path in source.get("shared_paths_preview", [])[:12]:
            paths.add(edit_path(path))
    # Every title/body issue mention is an operator search hint, even when
    # no second PR mentions the same issue. Match exact observed PR HEAD to
    # avoid attaching stale issue text from a changed source branch.
    for mention in view.get("observed_issue_to_pr_mentions_unverified", [])[:512]:
        if (type(mention) is dict and mention.get("pr_url") == pr_url
                and mention.get("pr_head_sha") == head):
            n = mention.get("issue_number")
            if type(n) is int and 0 < n < 100000000:
                hinted_issues.add(n)
    for issue in view.get("shown_duplicate_issue_groups", [])[:80]:
        if any(source.get("url") == pr_url for source in issue.get("shown_pr_sources", [])):
            n = issue.get("issue_number")
            if type(n) is int and 0 < n < 100000000:
                hinted_issues.add(n)
    return (sorted(paths)[:MAX_PATHS],
            len(paths) > MAX_PATHS or paths_limited,
            sorted(related)[:8], sorted(hinted_issues)[:MAX_ISSUE_HINTS])


def collect_workorders(board):
    if (type(board) is not dict or board.get("schema") != BOARD
            or any(board.get(key) is not False for key in (
                "claim_authorized", "merge_authorized",
                "deploy_authorized", "writes_performed"))):
        raise ValueError("not a non-authorizing PR observation board")
    desk = board.get("merge_first_attention")
    if (type(desk) is not dict or type(desk.get("items")) is not list
            or len(desk["items"]) > MAX_PRS
            or any(desk.get(k) is not False for k in (
                "claim_authorized", "merge_authorized",
                "deploy_authorized", "writes_performed"))):
        raise ValueError("opt-in current merge-first gate inspection required")
    projects = {view["project"]: view for view in board["project_views"]}
    live = board.get("current_live_pr_gate_observation")
    if (type(live) is not dict or type(live.get("items")) is not list
            or len(live["items"]) > MAX_PRS):
        raise ValueError("missing bounded live gate receipts")
    gate_sources = {}
    for source in live["items"]:
        if (type(source) is not dict or type(source.get("url")) is not str or
                source["url"] in gate_sources or
                source.get("merge_authorized") is not False):
            raise ValueError("duplicate, malformed or authorizing gate source")
        gate_sources[source["url"]] = source
    observed_at = _timestamp(desk.get("observed_at_utc"))
    if _timestamp(live.get("observed_at_utc")) != observed_at:
        raise ValueError("inconsistent live gate and action source times")
    cards, seen = [], set()
    for item in desk["items"]:
        if (type(item) is not dict or item.get("merge_authorized") is not False
                or item.get("owner_claim_verified") is not False
                or item.get("required_branch_rules_verified") is not False
                or item.get("project") not in projects):
            raise ValueError("untrusted or owner-authorizing PR work item")
        project, repo = item["project"], item["repository"]
        if projects[project]["repository"] != repo:
            raise ValueError("work order repository drift")
        url = _same_repo_url(item["url"], repo)
        if url in seen:
            raise ValueError("duplicate selected PR")
        seen.add(url)
        head, base = item.get("head_sha"), item.get("base_sha")
        pinned = bool(type(head) is str and SHA.fullmatch(head) and
                      type(base) is str and SHA.fullmatch(base) and
                      item.get("source_current_head_verified") is True)
        if not pinned and (head is not None or base is not None):
            raise ValueError("untrusted partial PR head/base receipt")
        live_gate = gate_sources.get(url)
        if live_gate is None:
            raise ValueError("selected PR not in current bounded gate receipt")
        if (live_gate.get("project") != project or live_gate.get("url") != url
                or live_gate.get("human_gate_blocker") != item.get("blocker")):
            raise ValueError("gate and operator action refer to different PR evidence")
        if pinned:
            if (live_gate.get("status") != "current_exact_pr_head_and_sample_match"
                    or live_gate.get("head_sha_observed") != head
                    or live_gate.get("base_sha_observed") != base):
                raise ValueError("owner workorder exact-HEAD/base mismatch")
        elif live_gate.get("status") == "current_exact_pr_head_and_sample_match":
            raise ValueError("current gate receipt lost exact HEAD in workorder")
        paths, paths_cut, related, hints = _observed_overlap_paths(
            projects[project], url, head if pinned else None,
            live_gate.get("sampled_file_overlaps_observed"))
        for extra in item.get("observed_file_overlap_prs", [])[:8]:
            related.append(_same_repo_url(extra, repo))
        related = sorted(set(related) - {url})[:8]
        links = []
        for job in item.get("non_green_jobs", [])[:MAX_CHECKS]:
            if (type(job) is not dict or type(job.get("name")) is not str
                    or type(job.get("url")) is not str
                    or job.get("kind") not in ("check_run", "legacy_status")):
                raise ValueError("untrusted GitHub check")
            canonical = (re.fullmatch(
                re.escape("https://github.com/" + repo + "/runs/") + r"[1-9][0-9]*",
                job["url"]) is not None or
                (pinned and job["url"] ==
                 "https://github.com/" + repo + "/commit/" + head + "/checks"))
            if not canonical:
                raise ValueError("noncanonical direct check link")
            links.append({
                "name": _markdown(job["name"]),
                "state": _markdown(str(job.get("state", "unknown"))),
                "url": job["url"], "kind": job["kind"],
            })
        num = int(PR.fullmatch(url).group(2))
        if item.get("number") != num:
            raise ValueError("mismatched PR identity")
        category = item.get("attention_group")
        blocker = item.get("blocker_text")
        next_step = item.get("human_next_step")
        for value in (category, blocker, next_step):
            _markdown(value)
        notes = []
        if not pinned:
            notes.append("Geen stabiele actuele HEAD/basis bevestigd; eerst bij GitHub herlezen.")
        if item.get("historical_gate_change") is not None:
            notes.append("Historische vergelijking: " +
                         _markdown(item["historical_gate_change"]) +
                         "; geen bewijs voor tussenliggende periode.")
        if paths_cut or item.get("non_green_jobs_hidden") or (
                item.get("source_current_head_verified") is not True):
            notes.append("Brongegevens voor conflicterende paden/CI zijn mogelijk afgekapt.")
        if live_gate.get("observed_in_recent_pr_collision_sample") is False:
            notes.append(
                "Dit is een expliciet geselecteerde oudere PR buiten het "
                "recente overzicht. Bestandsbotsingen met andere PRs blijven "
                "onvolledig, ook als geen overlap is gevonden.")
        if live_gate.get("sampled_file_overlaps_hidden"):
            notes.append("Gerelateerde PRs buiten de bounded sample worden niet getoond.")
        if not pinned:
            notes.append("Verifieer HEAD en doelbranch voordat dit als huidige taak geldt.")
        cards.append({
            "project": project, "repository": repo, "number": num,
            "url": url, "files_url": url + "/files", "head_sha": head if pinned else None,
            "base_sha": base if pinned else None,
            "category": _markdown(category), "blocker": _markdown(blocker),
            "human_next_step": _markdown(next_step), "source_current": pinned,
            "rank": item["rank"], "check_links": links,
            "check_links_hidden": int(item.get("non_green_jobs_hidden", 0)),
            "overlap_paths": [_markdown(p) for p in paths],
            "overlap_paths_hidden": paths_cut,
            "related_prs": related, "unverified_issue_hints": hints,
            "source_warnings": notes,
            "owner_claim_verified": False, "required_checks_verified": False,
            "merge_authorized": False, "deploy_authorized": False,
        })
    cards.sort(key=lambda x: (x["rank"], x["project"], x["number"]))
    receipt = {
        "schema": SCHEMA, "source_observed_at_utc": observed_at,
        "project_set": list(board["projects"]), "items": cards,
        "selection_is_not_full_open_pr_backlog":
            desk["source_not_a_complete_pr_inventory"],
        "source": "bounded_read_only_github_pr_and_gate_observations",
        "notion_and_worker_claims_checked": False,
        "branch_protection_checked": False, "required_checks_checked": False,
        "review_requirement_checked": False, "production_status_checked": False,
        "claim_authorized": False, "merge_authorized": False,
        "deploy_authorized": False, "writes_performed": False,
    }
    receipt["source_digest_sha256"] = _digest(receipt)
    return receipt


def render_markdown(bundle):
    if (type(bundle) is not dict or bundle.get("schema") != SCHEMA
            or any(bundle.get(k) is not False for k in (
                "claim_authorized", "merge_authorized",
                "deploy_authorized", "writes_performed"))):
        raise ValueError("invalid owner workorder bundle")
    lines = [
        "# zSentinel · privé-werkopdrachten bestaande product-PRs",
        "",
        "Bronmoment (UTC): " + bundle["source_observed_at_utc"],
        "Bronbewijs SHA256: `" + bundle["source_digest_sha256"] + "`",
        "",
        "**Dit is uitsluitend een handmatige overdracht.** Opgeslagen GitHub-bewijs "
        "is niet live, geen worker-claim, review, CI-required-set, "
        "branch protection, merge- of deploytoestemming.",
        "",
    ]
    if bundle["selection_is_not_full_open_pr_backlog"]:
        lines.append("**ONVOLLEDIGE SELECTIE:** overige open PRs/botsingen onbekend.\n")
    for card in bundle["items"]:
        lines += [
            "## " + card["project"] + " · [PR #" + str(card["number"]) +
            "](" + card["url"] + ")",
            "",
            "**Huidige blokkade:** " + card["blocker"],
            "**Eerstvolgende menselijke actie:** " + card["human_next_step"],
            "**Categorie:** " + card["category"],
            "**Exact PR HEAD:** `" + (card["head_sha"] or "ONBEKEND") + "`",
            "**Exact doelbranchbasis:** `" + (card["base_sha"] or "ONBEKEND") + "`",
            "**Bestandsdiff:** " + card["files_url"],
            "",
            "### Verifieer vóór verandering of merge",
            "1. Open de PR, verifieer opnieuw de HEAD en huidige eigenaar/lease.",
            "2. Inspecteer de bestaande verplicht gestelde exacte-HEAD CI en onafhankelijke review.",
            "3. Respecteer branch protection, bron-/onderzoekintegriteit en de release-autorisatie.",
            "4. Bewaar de bestaande workerclaim; neem geen werk van een andere eigenaar over.",
            "",
        ]
        if card["check_links"]:
            lines.append("### Directe niet-groene check-/statusbronnen")
            for check in card["check_links"]:
                lines.append("- " + check["kind"] + ": [" + check["name"] +
                             "](" + check["url"] + ") · " + check["state"])
            lines.append("")
        if card["check_links_hidden"]:
            lines.append("Extra checks buiten huidige bronvoorvertoning: " +
                         str(card["check_links_hidden"]) + "\n")
        if card["overlap_paths"] or card["related_prs"]:
            lines.append("### Bestandscope-overlap (geen bevestigd Git-conflict)")
            for path in card["overlap_paths"]:
                lines.append("- `" + path.replace("`", "\\`") + "`")
            for other in card["related_prs"]:
                lines.append("- Mogelijk gerelateerde PR: " + other)
            lines.append("")
        if card["unverified_issue_hints"]:
            lines.append("**Ongeverifieerde issuevermeldingen:** " +
                         ", ".join("#" + str(n) for n in card["unverified_issue_hints"]) +
                         " (tekstverwijzingen, geen fixbewijs).\n")
        for warning in card["source_warnings"]:
            lines.append("**Let op:** " + warning)
        lines += ["", "---", ""]
    if not bundle["items"]:
        lines.append("Geen geselecteerde brongebonden PR-werkopdrachten.\n")
    lines.append("Geen automatische claim, CI-dispatch, check-bypass, merge of deploy.\n")
    result = "\n".join(lines)
    if len(result.encode("utf-8")) > 512 * 1024:
        raise ValueError("private workorder Markdown exceeds limit")
    return result
