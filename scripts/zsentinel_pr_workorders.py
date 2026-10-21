#!/usr/bin/env python3
"""Create private, actionable, source-bound zSentinel PR handoffs.

This is an offline operator work order, never a claim, check rerun, merge,
approval, or deployment request. Selected PRs are a bounded sample.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import csv
import hashlib
import io
import json
import re

from zsentinel_pr_scope_guard import edit_path
from zsentinel_pr_gate_continuity import (
    LABELS as HISTORICAL_CHANGE_LABELS, NEXT_STEP_LABELS,
)

SCHEMA = "zsentinel-private-pr-workorders-v1"
BOARD = "zsentinel-portfolio-pr-collision-board-v1"
SHA = re.compile(r"^[0-9a-f]{40}$")
PR = re.compile(r"^https://github\.com/(Zennay/[A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,8})$")
MAX_PRS = 4
MAX_PATHS = 24
MAX_CHECKS = 24
MAX_ISSUE_HINTS = 12
SOURCE_FRESHNESS_SECONDS = 6 * 60 * 60
ATTENTION_LABELS = {
    "refresh_source": "Herlees bron en huidige HEAD",
    "defer_unchanged_hold": "HOLD ongewijzigd: ander werk oppakken",
    "inspect_changed_evidence": "Nieuw gatebewijs: eigenaar laten beoordelen",
    "await_required_gates": "Wacht op verplichte gates en onafhankelijke review",
}


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


def _commit_link(card, sha):
    """Canonical pinned source-only GitHub commit link, never merge clearance."""
    _same_repo_url(card["url"], card["repository"])
    if type(sha) is not str or SHA.fullmatch(sha) is None:
        raise ValueError("invalid exact commit SHA in private work order")
    return "https://github.com/" + card["repository"] + "/commit/" + sha


def _article_anchor(card):
    """Unique offline URL fragment across repositories with the same PR number."""
    url = _same_repo_url(card["url"], card["repository"])
    return "pr-" + url.removeprefix("https://github.com/").replace("/", "-")


def _markdown(value):
    """Bound opaque provider text; never allow raw Markdown headings/links."""
    if type(value) is not str:
        raise ValueError("operator text must be a string")
    if len(value) > 500 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("unsafe operator text")
    return re.sub(r"([\\\[\]_*\x60<>])", r"\\\1", value)


def _observed_overlap_paths(view, pr_url, head, repository, direct_overlaps=None):
    """Only observed shared filenames, including an older focused PR."""
    paths, related, hinted_issues = set(), set(), set()
    paths_limited = False
    # An unstable before/after GitHub inventory is not evidence of a
    # collision at the selected exact HEAD. Suppress *positive* edges,
    # including focused older-PR overlaps, until a stable source exists.
    stable_scope = view.get("open_pr_snapshot_stable") is True
    if not stable_scope:
        paths_limited = True
    for pair in (view.get("shown_collision_pairs", [])[:80] if stable_scope else []):
        if (type(pair) is not dict or type(pair.get("left")) is not dict
                or type(pair.get("right")) is not dict):
            raise ValueError("malformed observed PR collision")
        left, right = pair["left"], pair["right"]
        left_url = _same_repo_url(left.get("url"), repository)
        right_url = _same_repo_url(right.get("url"), repository)
        if left_url == pr_url:
            selected_sha, peer_url = left.get("head_sha"), right_url
        elif right_url == pr_url:
            selected_sha, peer_url = right.get("head_sha"), left_url
        else:
            continue
        # A collision graph can be sampled before an independently refreshed
        # gate receipt sees a newer PR HEAD. Never ascribe the old graph's
        # shared files or peers to this newly observed HEAD.
        if head is None or selected_sha != head:
            paths_limited = True
            continue
        related.add(peer_url)
        for path in pair.get("shared_files", [])[:MAX_PATHS]:
            paths.add(edit_path(path))
    # A focused overlap receipt is only bound to an exact PR head when
    # the second GitHub read confirmed the same head/base. On a drifted or
    # missing HEAD, discard *all* positive links rather than letting the
    # fallback sample impersonate a current collision.
    if head is None and direct_overlaps:
        paths_limited = True
    if head is not None and stable_scope:
        for source in (direct_overlaps or [])[:16]:
            if type(source) is not dict:
                raise ValueError("invalid older PR collision observation")
            related.add(_same_repo_url(source.get("other_pr_url"), repository))
            paths_limited |= (source.get("shared_paths_preview_truncated") is True or
                              source.get("other_pr_file_list_incomplete") is True)
            for path in source.get("shared_paths_preview", [])[:12]:
                paths.add(edit_path(path))
    # Every title/body issue mention is an operator search hint, even when
    # no second PR mentions the same issue. Match exact observed PR HEAD to
    # avoid attaching stale issue text from a changed source branch.
    for mention in (view.get("observed_issue_to_pr_mentions_unverified", [])[:512]
                    if stable_scope else []):
        if (type(mention) is dict and mention.get("pr_url") == pr_url
                and mention.get("pr_head_sha") == head):
            n = mention.get("issue_number")
            if type(n) is int and 0 < n < 100000000:
                hinted_issues.add(n)
    for issue in (view.get("shown_duplicate_issue_groups", [])[:80]
                  if stable_scope else []):
        if head is not None and any(
                source.get("url") == pr_url and source.get("head_sha") == head
                for source in issue.get("shown_pr_sources", [])):
            n = issue.get("issue_number")
            if type(n) is int and 0 < n < 100000000:
                hinted_issues.add(n)
    # A sampled list of paths is not a full repository clearance. Make
    # project-level incomplete GitHub pagination, omitted collision pairs
    # and focused peer-list truncation visible on each affected workorder.
    project_scope_partial = (view.get("sample_complete") is not True or
                             view.get("open_pr_snapshot_stable") is not True or
                             view.get("source_truncated") is True)
    return (sorted(paths)[:MAX_PATHS],
            len(paths) > MAX_PATHS or paths_limited or project_scope_partial,
            sorted(related)[:8], sorted(hinted_issues)[:MAX_ISSUE_HINTS])



def _collision_peer_receipts(view, selected_url, selected_head, repository,
                             focused_sources=None):
    """Exact observed peer commits; never infer current ownership or mergeability.

    The whole sampled inventory must be stable, and the selected PR must be
    independently head-pinned by the gate read. If a peer is observed at
    two incompatible HEADs, drop the peer instead of choosing one.
    """
    if selected_head is None or view.get("open_pr_snapshot_stable") is not True:
        return [], True
    peers, conflicted = {}, set()
    partial = (view.get("sample_complete") is not True or
               view.get("source_truncated") is True)
    def accept(raw_url, raw_sha, count, preview, truncated, provenance):
        nonlocal partial
        url = _same_repo_url(raw_url, repository)
        if url == selected_url:
            partial = True
            return
        if type(raw_sha) is not str or SHA.fullmatch(raw_sha) is None:
            partial = True
            return
        if type(count) is not int or not 1 <= count <= 10000:
            partial = True
            return
        if type(preview) is not list or len(preview) > MAX_PATHS:
            partial = True
            return
        paths = sorted({edit_path(path) for path in preview})
        if not paths or len(paths) > count:
            partial = True
            return
        row = {"url": url, "head_sha_at_observation": raw_sha,
               "shared_file_count_observed": count,
               "shared_files_preview": paths,
               "preview_incomplete": bool(truncated or len(paths) < count),
               "source": provenance,
               "owner_verified": False, "merge_authorized": False}
        if url in conflicted:
            return
        if url in peers:
            # Two independent source windows may show different peer heads
            # or incompatible file lists. Never stitch incompatible evidence.
            old = peers[url]
            if (old["head_sha_at_observation"] != raw_sha or
                    old["shared_file_count_observed"] != count or
                    old["shared_files_preview"] != paths):
                partial = True
                conflicted.add(url)
                peers.pop(url)
            return
        peers[url] = row

    for pair in view.get("shown_collision_pairs", [])[:80]:
        if (type(pair) is not dict or type(pair.get("left")) is not dict
                or type(pair.get("right")) is not dict):
            partial = True
            continue
        left, right = pair["left"], pair["right"]
        if left.get("url") == selected_url:
            owner, peer = left, right
        elif right.get("url") == selected_url:
            owner, peer = right, left
        else:
            continue
        if owner.get("head_sha") != selected_head:
            partial = True
            continue
        accept(peer.get("url"), peer.get("head_sha"),
               pair.get("shared_file_count_observed"),
               pair.get("shared_files"), pair.get("shared_files_truncated"),
               "stable_open_pr_collision_sample")

    for item in (focused_sources or [])[:16]:
        if type(item) is not dict:
            partial = True
            continue
        accept(item.get("other_pr_url"), item.get("other_pr_head_when_read"),
               item.get("shared_file_count_observed"),
               item.get("shared_paths_preview"),
               item.get("shared_paths_preview_truncated") is True or
               item.get("other_pr_file_list_incomplete") is True,
               "focused_pr_overlap_with_stable_inventory")

    if len(peers) > 8 or len(view.get("shown_collision_pairs", [])) > 80 or (
            focused_sources is not None and len(focused_sources) > 16):
        partial = True
    result = [peers[url] for url in sorted(peers)[:8]]
    return result, partial


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
    views, project_ids = board.get("project_views"), board.get("projects")
    if (type(views) is not list or type(project_ids) is not list
            or not 1 <= len(views) <= 3 or len(views) != len(project_ids)
            or any(type(view) is not dict or
                   type(view.get("project")) is not str or
                   type(view.get("repository")) is not str for view in views)
            or any(type(project) is not str for project in project_ids)):
        raise ValueError("missing bounded project source")
    projects = {view["project"]: view for view in views}
    if (len(projects) != len(views) or len(set(project_ids)) != len(project_ids)
            or set(projects) != set(project_ids)):
        raise ValueError("duplicate or drifting project source")
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
    # Exact HEAD at the time of reading is not an assertion that the same
    # PR is still current hours later. Preserve historical receipts, but
    # withdraw their source-current label from the operator workbench.
    source_stale = (
        datetime.now(timezone.utc) -
        datetime.fromisoformat(observed_at)).total_seconds() > SOURCE_FRESHNESS_SECONDS
    # Reuse the bounded, already-computed before/after gate evidence.
    # No additional GitHub reads or re-dispatch are necessary.
    history_by_pr = {}
    history = board.get("historical_gate_episodes")
    if history is not None:
        if (type(history) is not dict or type(history.get("items")) is not list
                or len(history["items"]) > 2 * MAX_PRS
                or history.get("merge_authorized") is not False
                or history.get("writes_performed") is not False):
            raise ValueError("invalid private historical gate source")
        if history.get("comparison_status") == "two_bounded_observations":
            # The saved two-snapshot comparator is stamped by the initial
            # portfolio observation. A later live gate re-read has a separate
            # observed_at_utc. Both are legitimate but not interchangeable.
            if (_timestamp(history.get("current_observed_at_utc")) !=
                    _timestamp(board.get("observed_at_utc"))):
                raise ValueError("historical PR comparison observation drift")
            for source in history["items"]:
                if (type(source) is not dict or
                        type(source.get("url")) is not str or
                        source["url"] in history_by_pr):
                    raise ValueError("invalid duplicate historical PR")
                history_by_pr[source["url"]] = source
        elif (history.get("comparison_status") !=
              "one_or_both_gate_observations_absent" or history["items"]):
            raise ValueError("invalid historical gate coverage")
    if history is None:
        history_status, previous_observed, history_interval = (
            "not_requested", None, None)
    else:
        history_status = history["comparison_status"]
        previous_observed = _timestamp(history["previous_observed_at_utc"])
        history_interval = history["elapsed_seconds"]
        if (type(history_interval) is not int or
                not 0 <= history_interval <= 7 * 86400):
            raise ValueError("invalid bounded historical time interval")
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
            projects[project], url, head if pinned else None, repo,
            live_gate.get("sampled_file_overlaps_observed"))
        if live_gate.get("sampled_file_overlaps_hidden"):
            paths_cut = True
        peer_evidence, peer_partial = _collision_peer_receipts(
            projects[project], url, head if pinned else None, repo,
            live_gate.get("sampled_file_overlaps_observed"))
        if live_gate.get("sampled_file_overlaps_hidden"):
            peer_partial = True
        # The summary can include an earlier file-scope sample, not
        # necessarily the second read's exact HEAD. Never surface those
        # links as positive overlap when the current HEAD could not be pinned.
        if pinned:
            for extra in item.get("observed_file_overlap_prs", [])[:8]:
                related.append(_same_repo_url(extra, repo))
        elif item.get("observed_file_overlap_prs"):
            paths_cut = True
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
        if source_stale:
            notes.append(
                "HISTORISCHE BRON: de opgeslagen GitHub/PR-gatewaarneming is "
                "ouder dan zes uur. Herlees HEAD, reviews en verplichte checks "
                "voordat een PR als actueel wordt behandeld.")
        if not pinned:
            notes.append("Geen stabiele actuele HEAD/basis bevestigd; eerst bij GitHub herlezen.")
        # The existing portfolio CLI already computes bounded comparison
        # receipts. Promote that *source* into the operator handoff instead
        # of forcing an owner to read another JSON/Markdown section.
        history_code = item.get("historical_gate_change")
        history_step = item.get("historical_next_step")
        history_new = item.get("new_evidence_since_previous_sample")
        if type(history_new) is not bool:
            raise ValueError("invalid historical new-evidence flag")
        if history_code is None:
            if history_step is not None or history_new:
                raise ValueError("unsourced historical PR next step")
            history_state, history_label, step_label = (
                "not_compared", "Geen historische bronvergelijking", None)
        else:
            if (type(history_code) is not str
                    or history_code not in HISTORICAL_CHANGE_LABELS
                    or type(history_step) is not str
                    or history_step not in NEXT_STEP_LABELS):
                raise ValueError("malformed or unknown historical gate comparison")
            history_state = {
                "same_exact_head_and_gate_receipts_at_two_observations":
                    "unchanged_two_snapshots",
                "gate_receipt_changed_same_head":
                    "changed_gate_receipt",
                "head_changed_or_source_uncertain":
                    "changed_or_uncertain_head",
                "gate_source_incomplete_no_stability_claim":
                    "incomplete_gate_source",
                "newly_selected_in_current_sample_not_new_pr":
                    "newly_selected_not_new_pr",
            }.get(history_code)
            if history_state is None:
                raise ValueError("historical PR source no longer selected")
            if history_new != (history_state != "unchanged_two_snapshots"):
                raise ValueError("historical attention change flag mismatch")
            history_label = HISTORICAL_CHANGE_LABELS[history_code]
            step_label = NEXT_STEP_LABELS[history_step]
            notes.append("Historische vergelijking: " +
                         _markdown(history_label) +
                         "; géén bewijs over de tijd tussen de opnames.")
        historical_changed_checks, historical_changed_categories = [], []
        historical_changed_checks_hidden = 0
        historical_source = history_by_pr.get(url)
        if historical_source is not None:
            if (historical_source.get("project") != project
                    or historical_source.get("change") != history_code
                    or historical_source.get("operator_next_step") != history_step):
                raise ValueError("historical PR receipt differs from owner action")
            for category in historical_source.get(
                    "observed_changed_gate_evidence_categories", [])[:6]:
                historical_changed_categories.append(_markdown(category))
            for change in historical_source.get(
                    "observed_changed_non_green_checks", [])[:16]:
                if (type(change) is not dict or
                        type(change.get("kind")) is not str or
                        type(change.get("label")) is not str or
                        type(change.get("url")) is not str):
                    raise ValueError("invalid changed GitHub job receipt")
                link = change["url"]
                if (re.fullmatch(re.escape(
                        "https://github.com/" + repo + "/runs/") +
                        r"[1-9][0-9]*", link) is None
                        and not (pinned and link ==
                                 "https://github.com/" + repo + "/commit/" +
                                 head + "/checks")):
                    historical_changed_checks_hidden += 1
                    continue
                historical_changed_checks.append({
                    "kind": _markdown(change["kind"]),
                    "label": _markdown(change["label"]),
                    "url": link,
                })
            hidden = historical_source.get(
                "observed_changed_non_green_checks_hidden", 0)
            if type(hidden) is not int or not 0 <= hidden <= 200:
                raise ValueError("invalid bounded historical job omission")
            historical_changed_checks_hidden += hidden
        elif history_code is not None:
            raise ValueError("historical comparison missing PR gate row")
        defer_repeat = (history_state == "unchanged_two_snapshots"
                        and item["rank"] == 5)
        if defer_repeat:
            notes.append("Ongewijzigde waarneming: dezelfde HOLD niet "
                         "opnieuw onderzoeken zonder nieuw bronbewijs; "
                         "vervolg ander veilig werk.")
        if history_state in ("incomplete_gate_source",
                             "changed_or_uncertain_head",
                             "newly_selected_not_new_pr"):
            notes.append(
                "Bron vraagt mogelijk herbeoordeling, maar bewijst niet "
                "dat een PR-HEAD, merge of CI-status echt gewijzigd is.")
        if paths_cut or item.get("non_green_jobs_hidden") or (
                item.get("source_current_head_verified") is not True):
            notes.append("Brongegevens voor conflicterende paden/CI zijn mogelijk afgekapt.")
        if peer_partial:
            notes.append("Peer-HEAD-vergelijking is onvolledig; overige "
                         "open PRs, veranderde HEADs en ontbrekende paden "
                         "kunnen niet worden uitgesloten.")
        if paths_cut:
            notes.append(
                "Niet alle bestandsverbindingen zijn aan de exact waargenomen "
                "PR-HEAD gebonden; een oude botsingssteekproef wordt niet "
                "als actuele bestandsbotsing gepresenteerd.")
        if live_gate.get("observed_in_recent_pr_collision_sample") is False:
            notes.append(
                "Dit is een expliciet geselecteerde oudere PR buiten het "
                "recente overzicht. Bestandsbotsingen met andere PRs blijven "
                "onvolledig, ook als geen overlap is gevonden.")
        if live_gate.get("sampled_file_overlaps_hidden"):
            notes.append("Gerelateerde PRs buiten de bounded sample worden niet getoond.")
        if not pinned:
            notes.append("Verifieer HEAD en doelbranch voordat dit als huidige taak geldt.")
        if source_stale or not pinned or history_state in (
                "changed_or_uncertain_head", "incomplete_gate_source",
                "newly_selected_not_new_pr"):
            attention_status = "refresh_source"
        elif defer_repeat:
            attention_status = "defer_unchanged_hold"
        elif history_state == "changed_gate_receipt":
            attention_status = "inspect_changed_evidence"
        else:
            attention_status = "await_required_gates"
        cards.append({
            "attention_status": attention_status,
            "attention_label": ATTENTION_LABELS[attention_status],
            "project": project, "repository": repo, "number": num,
            "url": url, "files_url": url + "/files", "head_sha": head if pinned else None,
            "base_sha": base if pinned else None,
            "category": _markdown(category), "blocker": _markdown(blocker),
            "human_next_step": _markdown(next_step),
            "source_current": pinned and not source_stale,
            "source_exact_head_at_observation": pinned,
            "rank": item["rank"], "check_links": links,
            "change_since_previous_observation": history_state,
            "historical_change_code": history_code,
            "historical_change_label": history_label,
            "historical_next_step": _markdown(step_label) if step_label else None,
            "historical_changed_gate_evidence_categories":
                historical_changed_categories,
            "historical_changed_job_links": historical_changed_checks,
            "historical_changed_job_links_hidden":
                historical_changed_checks_hidden,
            "new_evidence_since_previous_sample": history_new,
            "repeat_hold_deferred": defer_repeat,
            "check_links_hidden": int(item.get("non_green_jobs_hidden", 0)),
            "overlap_paths": [_markdown(p) for p in paths],
            "overlap_paths_hidden": paths_cut,
            "related_prs": related, "unverified_issue_hints": hints,
            "peer_head_evidence": peer_evidence,
            "peer_head_evidence_partial": peer_partial,
            "source_warnings": notes,
            "owner_claim_verified": False, "required_checks_verified": False,
            "merge_authorized": False, "deploy_authorized": False,
        })
    cards.sort(key=lambda x: (x["rank"], x["project"], x["number"]))
    receipt = {
        "schema": SCHEMA, "source_observed_at_utc": observed_at,
        "project_set": list(project_ids), "items": cards,
        "source_observation_stale": source_stale,
        "source_freshness_budget_seconds": SOURCE_FRESHNESS_SECONDS,
        "historical_comparison_status": history_status,
        "previous_source_observed_at_utc": previous_observed,
        "historical_observation_interval_seconds": history_interval,
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




def render_csv(bundle):
    """Bounded private owner worklist for manual spreadsheet/filter workflows.

    Each line remains a sourced observation, never a merge instruction. A
    potentially active Excel formula is made inert before csv.writer quotes it.
    """
    if (type(bundle) is not dict or bundle.get("schema") != SCHEMA
            or type(bundle.get("items")) is not list
            or len(bundle["items"]) > MAX_PRS
            or any(bundle.get(k) is not False for k in (
                "claim_authorized", "merge_authorized",
                "deploy_authorized", "writes_performed"))):
        raise ValueError("invalid private owner CSV")
    fields = (
        "record_type", "project", "pr_number", "pr_url",
        "attention_status", "attention_label",
        "head_sha_at_observation", "current_source_verified", "rank",
        "category", "blocker", "human_next_step", "source_observed_at_utc",
        "source_sha256", "link", "detail",
        "change_since_previous_observation", "new_evidence_since_previous",
        "repeat_hold_deferred", "historical_next_step",
        "previous_source_observed_at_utc", "historical_comparison_status",
        "historical_observation_interval_seconds",
    )
    def inert(value):
        value = "" if value is None else str(value)
        if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in value):
            raise ValueError("invalid private CSV cell")
        if len(value) > 2000:
            raise ValueError("oversized private CSV cell")
        # Quotes are not sufficient to prevent spreadsheet formula execution.
        if value.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_ALL)
    writer.writerow(fields)
    observed = bundle["source_observed_at_utc"]
    source_hash = bundle["source_digest_sha256"]
    def add(kind, card=None, **extra):
        row = {
            "record_type": kind, "source_observed_at_utc": observed,
            "source_sha256": source_hash,
            "previous_source_observed_at_utc":
                bundle["previous_source_observed_at_utc"],
            "historical_comparison_status":
                bundle["historical_comparison_status"],
            "historical_observation_interval_seconds":
                bundle["historical_observation_interval_seconds"],
        }
        if card is not None:
            row.update({
                "project": card["project"], "pr_number": card["number"],
                "pr_url": _same_repo_url(card["url"], card["repository"]),
                "head_sha_at_observation": card["head_sha"],
                "current_source_verified": bool(card["source_current"]),
                "rank": card["rank"], "category": card["category"],
                "blocker": card["blocker"],
                "human_next_step": card["human_next_step"],
                "attention_status": card["attention_status"],
                "attention_label": card["attention_label"],
                "change_since_previous_observation":
                    card["change_since_previous_observation"],
                "new_evidence_since_previous":
                    card["new_evidence_since_previous_sample"],
                "repeat_hold_deferred": card["repeat_hold_deferred"],
                "historical_next_step": card["historical_next_step"],
            })
        row.update(extra)
        writer.writerow([inert(row.get(key)) for key in fields])
    add("receipt", detail=(
        "BOUNDED READ-ONLY SOURCE; NOT a full PR inventory, ownership proof, "
        "required-CI set, review, merge or deploy approval. "
        "Historical observation=" +
        str(bundle.get("source_observation_stale") is True) +
        "; incomplete selection=" +
        str(bundle.get("selection_is_not_full_open_pr_backlog") is True)))
    for card in bundle["items"]:
        repo = card["repository"]
        pr_url = _same_repo_url(card["url"], repo)
        add("pr", card, link=pr_url + "/files", detail=(
            "CURRENT STATUS NOT VERIFIED" if not card["source_current"]
            else "EXACT HEAD AT SOURCE MOMENT; NOT MERGE AUTHORITY"))
        if card["head_sha"] and card["base_sha"]:
            add("observed_head_commit", card,
                link=_commit_link(card, card["head_sha"]),
                detail="PINNED AT SOURCE MOMENT ONLY; recheck before any merge")
            add("observed_base_commit", card,
                link=_commit_link(card, card["base_sha"]),
                detail="OBSERVED TARGET BASE ONLY; branch may have moved")
        if card["change_since_previous_observation"] != "not_compared":
            add("observed_change", card,
                link=pr_url,
                detail=("BOUNDED HISTORICAL COMPARISON; NO UNINTERRUPTED "
                        "STATE OR RELEASE CLEARANCE. Code: " +
                        str(card["historical_change_code"])))
        for category in card["historical_changed_gate_evidence_categories"]:
            add("changed_gate_evidence", card, link=pr_url, detail=category)
        for change in card["historical_changed_job_links"]:
            add("changed_check", card, link=change["url"], detail=(
                change["kind"] + " | " + change["label"] +
                " | historical source; check current state directly"))
        if card["historical_changed_job_links_hidden"]:
            add("coverage_warning", card, detail=(
                "Historical check changes not shown: " +
                str(card["historical_changed_job_links_hidden"])))
        for check in card["check_links"][:MAX_CHECKS]:
            link = check["url"]
            if not (re.fullmatch(
                    re.escape("https://github.com/" + repo + "/runs/") +
                    r"[1-9][0-9]*", link) or
                    (card["head_sha"] and link ==
                     "https://github.com/" + repo + "/commit/" +
                     card["head_sha"] + "/checks")):
                raise ValueError("unsafe exact-head check link")
            add("check", card, link=link, detail=(
                check["kind"] + " | " + check["name"] + " | " + check["state"]))
        if card["check_links_hidden"]:
            add("coverage_warning", card, detail=(
                "Additional non-green checks not shown: " +
                str(card["check_links_hidden"])))
        for peer in card["peer_head_evidence"][:8]:
            add("peer_head_collision", card,
                link=_commit_link(card, peer["head_sha_at_observation"]),
                detail=("Observed shared paths with " + peer["url"] +
                        " | count=" + str(peer["shared_file_count_observed"]) +
                        " | partial=" + str(peer["preview_incomplete"]) +
                        " | source=" + peer["source"] +
                        "; HEAD may have changed; ownership and merge UNKNOWN"))
            for path in peer["shared_files_preview"][:MAX_PATHS]:
                add("peer_shared_file", card,
                    link=peer["url"], detail=edit_path(path))
        if card["peer_head_evidence_partial"]:
            add("coverage_warning", card,
                detail="Pinned peer HEAD/collision coverage PARTIAL; no clearance")
        for related in card["related_prs"][:8]:
            add("related_pr", card, link=_same_repo_url(related, repo),
                detail="Observed shared-file hint; Git merge conflict UNKNOWN")
        for path in card["overlap_paths"][:MAX_PATHS]:
            add("overlap_path", card, detail=edit_path(path))
        if card["overlap_paths_hidden"]:
            add("coverage_warning", card, detail=(
                "Open-PR scope and shared-file evidence incomplete; "
                "absence is not clearance"))
        for number in card["unverified_issue_hints"][:MAX_ISSUE_HINTS]:
            if type(number) is not int or not 0 < number < 100000000:
                raise ValueError("invalid issue hint")
            add("issue_hint_unverified", card,
                link="https://github.com/" + repo + "/issues/" + str(number),
                detail="PR text only; link identity/status/fix unverified")
        for warning in card["source_warnings"][:16]:
            add("source_warning", card, detail=warning)
    data = "\ufeff" + stream.getvalue()
    if len(data.encode("utf-8")) > 512 * 1024:
        raise ValueError("private workorder CSV exceeds limit")
    return data

def render_html(bundle):
    """One-file, script-free private owner desk; never a live merge console."""
    if (type(bundle) is not dict or bundle.get("schema") != SCHEMA
            or type(bundle.get("items")) is not list
            or len(bundle["items"]) > MAX_PRS
            or any(bundle.get(k) is not False for k in (
                "claim_authorized", "merge_authorized",
                "deploy_authorized", "writes_performed"))):
        raise ValueError("invalid private owner desk")
    def e(value):
        return escape(str(value), quote=True)

    parts = [
        "<!doctype html><html lang=\"nl\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>zSentinel · private PR werkopdrachten</title>",
        "<style>body{font:16px/1.5 system-ui,sans-serif;background:#f8f9fb;"
        "color:#17202b;max-width:960px;margin:auto;padding:1.25rem}"
        "article{background:white;border:1px solid #cbd2dc;border-radius:12px;"
        "padding:1.2rem;margin:1.1rem 0;overflow-wrap:anywhere}"
        "a{color:#17529b}a:focus-visible{outline:3px solid #17529b}"
        "h1,h2,h3{line-height:1.25}ul{padding-left:1.4rem}"
        "code{overflow-wrap:anywhere}.notice{border-left:5px solid #606b7b;"
        "padding:.7rem 1rem;background:#e9ecf0;margin:1rem 0}"
        ".warning{border-left-color:#a34c20;background:#fff0df}"
        ".meta{color:#425063;font-size:.92rem}"
        "nav{border:1px solid #cbd2dc;border-radius:12px;padding:.8rem 1.2rem;"
        "background:#fff;margin:1rem 0}nav ol{margin:.4rem 0;padding-left:1.6rem}"
        ".tag{display:inline-block;padding:.1rem .45rem;border:1px solid #7a8594;"
        "border-radius:.35rem;font-size:.85rem}"
        "article:target{outline:3px solid #17529b;outline-offset:2px}"
        "@media print{body{max-width:none;padding:0;background:white}"
        "nav{display:none}article{break-inside:avoid;border-color:#777}"
        "a{color:inherit;text-decoration:underline}}"
        "</style></head><body>",
        "<header><h1>zSentinel · private PR-werkopdrachten</h1>",
        "<p class=\"meta\">Waargenomen (UTC): " + e(bundle["source_observed_at_utc"]) +
        " · SHA256: <code>" + e(bundle["source_digest_sha256"]) + "</code></p>",
        "<p class=\"notice\">Alle acties zijn handmatig. Dit offline dossier bewijst "
        "geen huidige eigenaar, vereiste CI, onafhankelijke review of merge- en "
        "deployrecht.</p></header>",
    ]
    if bundle["previous_source_observed_at_utc"]:
        parts.append("<p class=\"meta\">Vorige bronopname (UTC): " +
                     e(bundle["previous_source_observed_at_utc"]) +
                     " · tussenruimte: " +
                     e(bundle["historical_observation_interval_seconds"]) +
                     " seconden · dekking: " +
                     e(bundle["historical_comparison_status"]) +
                     "</p><p>Geen bewijs voor een ononderbroken toestand "
                     "tussen beide meetmomenten.</p>")
    elif bundle["historical_comparison_status"] == "not_requested":
        parts.append("<p class=\"meta\">Geen eerder bronbestand ter "
                     "vergelijking meegegeven.</p>")
    if bundle.get("source_observation_stale") is True:
        parts.append("<p class=\"notice warning\"><strong>HISTORISCHE BRON "
                     "(&gt;6 uur).</strong> HEAD, reviews en CI opnieuw bekijken; "
                     "geen actuele status bewezen.</p>")
    if bundle.get("selection_is_not_full_open_pr_backlog") is True:
        parts.append("<p class=\"notice warning\">ONVOLLEDIGE SELECTIE: "
                     "meer open PRs en bestandsbotsingen mogelijk.</p>")
    if bundle["items"]:
        change_labels = {
            "changed_gate_receipt": "Gatebewijs gewijzigd bij dezelfde HEAD",
            "changed_or_uncertain_head": "HEAD veranderd of bron onzeker",
            "incomplete_gate_source": "Vergelijking onvolledig",
            "newly_selected_not_new_pr": "Nieuw in selectie, niet bewezen nieuwe PR",
            "unchanged_two_snapshots": "Zelfde HEAD/gates in twee opnames",
            "not_compared": "Geen historische vergelijking",
        }
        counts = {key: 0 for key in change_labels}
        for card in bundle["items"]:
            state = card["change_since_previous_observation"]
            if state not in counts:
                raise ValueError("untrusted comparison state")
            counts[state] += 1
        parts.append("<section class=\"notice\" aria-label=\"Historische wijzigingen\">"
                     "<strong>Veranderingen sinds vorige opname (maximaal "
                     "twee momentopnames; geen continu toezicht)</strong><ul>")
        for state, label in change_labels.items():
            parts.append("<li>" + e(label) + ": <strong>" +
                         str(counts[state]) + "</strong></li>")
        parts.append("</ul><p>Een ongewijzigde HOLD hoeft niet direct opnieuw "
                     "onderzocht te worden, maar is geen releasevrijgave; "
                     "een ontbrekende vergelijking is geen bewijs van rust.</p>"
                     "</section>")
        attention_counts = {key: 0 for key in ATTENTION_LABELS}
        for card in bundle["items"]:
            state = card["attention_status"]
            if state not in attention_counts:
                raise ValueError("unknown operator action disposition")
            attention_counts[state] += 1
        parts.append("<section aria-label=\"Werkvolgorde op basis van bron\" class=\"notice\">"
                     "<strong>Wat nu aandacht vraagt (bronobservatie, geen vrijgave)</strong><ul>")
        for state, label in ATTENTION_LABELS.items():
            parts.append("<li>" + e(label) + ": <strong>" +
                         str(attention_counts[state]) + "</strong></li>")
        parts.append("</ul></section>")
        parts.append("<nav aria-label=\"Inhoud PR-werkopdrachten\"><strong>"
                     "Werkvolgorde · " + str(len(bundle["items"])) +
                     " geselecteerde PRs (geen volledige repositorydekking)"
                     "</strong><ol>")
        for summary in bundle["items"]:
            parts.append("<li><a href=\"#" + e(_article_anchor(summary)) + "\">" +
                         e(summary["project"]) + " · PR #" +
                         e(summary["number"]) + "</a> — " +
                         e(summary["category"]) + " · " +
                         e(summary["attention_label"]) + "</li>")
        parts.append("</ol></nav><main>")
    for card in bundle["items"]:
        repo = card["repository"]
        url = _same_repo_url(card["url"], repo)
        files = url + "/files"
        parts += [
            "<article id=\"" + e(_article_anchor(card)) + "\"><h2>" +
            e(card["project"]) + " · <a target=\"_blank\" "
            "rel=\"noopener noreferrer\" href=\"" + e(url) + "\">PR #" +
            e(card["number"]) + "</a></h2>",
            "<p><span class=\"tag\">" +
            ("HISTORISCH of HEAD ONBEKEND" if not card["source_current"]
             else "HEAD vastgepind bij recente bronopname") +
            "</span> · rang " + e(card["rank"]) + "</p>",
            "<p><strong>Vergelijking:</strong> " +
            e(card["historical_change_label"]) +
            (" · <strong>Ongewijzigde HOLD: laat liggen tot nieuw bewijs</strong>"
             if card["repeat_hold_deferred"] else "") +
            "</p>",
            "<p><strong>Werkvolgorde:</strong> " + e(card["attention_label"]) +
            " (bronobservatie)</p>" +
            "<p><strong>Blokkade:</strong> " + e(card["blocker"]) + "<br>"
            "<strong>Menselijke vervolgstap:</strong> " +
            e(card["human_next_step"]) + "</p>",
            "<p class=\"meta\">Categorie: " + e(card["category"]) +
            " · HEAD bij observatie: <code>" + e(card["head_sha"] or "ONBEKEND") +
            "</code><br>Doelbasis: <code>" + e(card["base_sha"] or "ONBEKEND") +
            "</code> · <a target=\"_blank\" rel=\"noopener noreferrer\" "
            "href=\"" + e(files) + "\">Bestandswijzigingen</a></p>",
        ]
        if card["head_sha"] and card["base_sha"]:
            parts.append("<p class=\"meta\">Bronlinks: "
                         "<a target=\"_blank\" rel=\"noopener noreferrer\" href=\"" +
                         e(_commit_link(card, card["head_sha"])) +
                         "\">Geobserveerde HEAD-commit</a> · "
                         "<a target=\"_blank\" rel=\"noopener noreferrer\" href=\"" +
                         e(_commit_link(card, card["base_sha"])) +
                         "\">Geobserveerde doelbranchbasis</a>. "
                         "Historische links, geen gatevrijgave.</p>")
        if card.get("historical_next_step"):
            parts.append("<p><strong>Vervolg op bronvergelijking:</strong> " +
                         e(card["historical_next_step"]) +
                         "</p><p class=\"meta\">Momentopnames bewijzen niet wat "
                         "tussen de opnames gebeurde.</p>")
        if card["historical_changed_gate_evidence_categories"]:
            parts.append("<h3>Gewijzigde gatebewijssoorten</h3><ul>")
            for evidence in card["historical_changed_gate_evidence_categories"]:
                parts.append("<li>" + e(evidence) + "</li>")
            parts.append("</ul>")
        if card["historical_changed_job_links"]:
            parts.append("<h3>Checkverschillen tussen twee bronmomenten</h3><ul>")
            for change in card["historical_changed_job_links"]:
                parts.append("<li><a target=\"_blank\" rel=\"noopener noreferrer\" "
                             "href=\"" + e(change["url"]) + "\">" +
                             e(change["label"]) + "</a> · " +
                             e(change["kind"]) + "</li>")
            parts.append("</ul>")
        if card["historical_changed_job_links_hidden"]:
            parts.append("<p class=\"notice warning\">Nog " +
                         e(card["historical_changed_job_links_hidden"]) +
                         " historische checkverschillen niet getoond; geen "
                         "volledige gate-inventaris.</p>")
        if card.get("check_links"):
            parts.append("<h3>Niet-groene checkbronnen</h3><ul>")
            for check in card["check_links"]:
                link = check["url"]
                # The collector admits only canonical check-run or pinned
                # commit-check links. Recheck before HTML interpolation.
                if not (re.fullmatch(
                        re.escape("https://github.com/" + repo + "/runs/") +
                        r"[1-9][0-9]*", link) or
                        (card["head_sha"] and link ==
                         "https://github.com/" + repo + "/commit/" +
                         card["head_sha"] + "/checks")):
                    raise ValueError("unsafe private check link")
                parts.append("<li><a target=\"_blank\" rel=\"noopener noreferrer\" "
                             "href=\"" + e(link) + "\">" + e(check["name"]) +
                             "</a> · " + e(check["state"]) + "</li>")
            parts.append("</ul>")
        if card.get("check_links_hidden"):
            parts.append("<p class=\"notice warning\">Nog " +
                         e(card["check_links_hidden"]) +
                         " niet-groene checkbronnen buiten deze preview; "
                         "dit is geen volledige CI-inventaris.</p>")
        if card.get("peer_head_evidence"):
            parts.append("<h3>Gedeelde bestanden met exacte geobserveerde peer-HEAD</h3>"
                         "<p class=\"meta\">Een snapshot van bronbestanden; "
                         "geen Git-conflict-, eigenaar- of mergevrijgave.</p><ul>")
            for peer in card["peer_head_evidence"][:8]:
                peer_url = _same_repo_url(peer["url"], repo)
                peer_sha = peer["head_sha_at_observation"]
                peer_commit = _commit_link(card, peer_sha)
                parts.append("<li><a target=\"_blank\" rel=\"noopener noreferrer\" href=\"" +
                             e(peer_url) + "\">" + e(peer_url) + "</a> · "
                             "<a target=\"_blank\" rel=\"noopener noreferrer\" href=\"" +
                             e(peer_commit) + "\">HEAD " + e(peer_sha[:12]) +
                             "</a> · waargenomen gedeeld: " +
                             str(peer["shared_file_count_observed"]) +
                             (" · padenpreview onvolledig" if peer["preview_incomplete"] else "") +
                             "<ul>")
                for path in peer["shared_files_preview"][:MAX_PATHS]:
                    parts.append("<li><code>" + e(path) + "</code></li>")
                parts.append("</ul></li>")
            parts.append("</ul>")
        if card.get("peer_head_evidence_partial"):
            parts.append("<p class=\"notice warning\">Peer-HEAD-brondekking "
                         "ONVOLLEDIG; ontbrekende botsingen niet uitgesloten.</p>")
        if card.get("related_prs"):
            parts.append("<h3>Gerelateerde bron-PRs (geen mergeconflictbewijs)</h3><ul>")
            for related in card["related_prs"]:
                parts.append("<li><a target=\"_blank\" rel=\"noopener noreferrer\" "
                             "href=\"" + e(_same_repo_url(related, repo)) +
                             "\">" + e(related) + "</a></li>")
            parts.append("</ul>")
        if card.get("overlap_paths"):
            parts.append("<h3>Waargenomen gedeelde paden</h3><ul>")
            for path in card["overlap_paths"]:
                parts.append("<li><code>" + e(path) + "</code></li>")
            parts.append("</ul>")
        if card.get("unverified_issue_hints"):
            parts.append("<h3>Ongeverifieerde issuevermeldingen</h3>"
                         "<p>Nummers uit PR-tekst, niet uit gecontroleerde "
                         "issuebron. Link kan ook een PR of ontbrekend nummer "
                         "openen.</p><ul>")
            for n in card["unverified_issue_hints"][:MAX_ISSUE_HINTS]:
                if type(n) is not int or not 1 <= n < 100000000:
                    raise ValueError("untrusted issue number hint")
                issue_url = "https://github.com/" + repo + "/issues/" + str(n)
                parts.append("<li><a target=\"_blank\" rel=\"noopener noreferrer\" "
                             "href=\"" + e(issue_url) + "\">Controleer nummer #" +
                             str(n) + "</a> (onbevestigd)</li>")
            parts.append("</ul>")
        if card.get("overlap_paths_hidden"):
            parts.append("<p class=\"notice warning\">"
                         "Niet alle mogelijke gedeelde bestandspaden zijn "
                         "getoond. Geen conflictvrije conclusie.</p>")
        parts.append("<details><summary>Controlepunten vóór eventuele merge"
                     " (altijd handmatig)</summary><ol>"
                     "<li>Herlees exacte HEAD en basis en controleer actieve "
                     "claim/werkafspraak.</li>"
                     "<li>Controleer alle werkelijk verplichte exact-HEAD "
                     "CI-checks, niet alleen deze steekproef.</li>"
                     "<li>Vereiste onafhankelijke review en beschermde branchregels "
                     "moeten voldaan zijn.</li>"
                     "<li>Bevoegde releaser beslist over merge/deploy; geen "
                     "automatische acties vanuit dit dossier.</li>"
                     "</ol></details>")
        if card.get("source_warnings"):
            parts.append("<h3>Bronwaarschuwingen</h3><ul>")
            for warning in card["source_warnings"]:
                parts.append("<li>" + e(warning) + "</li>")
            parts.append("</ul>")
        parts.append("<p class=\"meta\">Geen claim, workflowherstart, merge of "
                     "deploy geautoriseerd.</p></article>")
    if bundle["items"]:
        parts.append("</main>")
    else:
        parts.append("<p>Geen geselecteerde brongebonden PR-werkopdrachten.</p>")
    parts.append("</body></html>\n")
    output = "".join(parts)
    if len(output.encode("utf-8")) > 512 * 1024:
        raise ValueError("private workorder HTML exceeds limit")
    return output


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
        ("Vorige bronmoment (UTC): " +
         (bundle["previous_source_observed_at_utc"] or "niet meegegeven") +
         " · vergelijkingsdekking: " + bundle["historical_comparison_status"]),
        "Bronbewijs SHA256: `" + bundle["source_digest_sha256"] + "`",
        "",
        "**Dit is uitsluitend een handmatige overdracht.** Opgeslagen GitHub-bewijs "
        "is niet live, geen worker-claim, review, CI-required-set, "
        "branch protection, merge- of deploytoestemming.",
        "",
    ]
    if bundle.get("source_observation_stale") is True:
        lines.append(
            "**HISTORISCHE BRON (>6 uur):** geen actuele PR-/HEAD-/"
            "review-/CI-toestand bewezen. Bronlinks zijn voor hercontrole, "
            "niet voor mergebesluit.\n")
    if bundle["selection_is_not_full_open_pr_backlog"]:
        lines.append("**ONVOLLEDIGE SELECTIE:** overige open PRs/botsingen onbekend.\n")
    change_counts = {
        "changed_gate_receipt": 0, "changed_or_uncertain_head": 0,
        "incomplete_gate_source": 0, "newly_selected_not_new_pr": 0,
        "unchanged_two_snapshots": 0, "not_compared": 0,
    }
    for card in bundle["items"]:
        state = card["change_since_previous_observation"]
        if state not in change_counts:
            raise ValueError("untrusted historical PR comparison state")
        change_counts[state] += 1
    lines += [
        "## Veranderingen sinds voorgaande PR-opname",
        "Gatebewijs gewijzigd: " + str(change_counts["changed_gate_receipt"]) +
        " · HEAD onzeker: " + str(change_counts["changed_or_uncertain_head"]) +
        " · onvolledige gatebron: " + str(change_counts["incomplete_gate_source"]) +
        " · nieuw geselecteerd: " + str(change_counts["newly_selected_not_new_pr"]) +
        " · ongewijzigd in twee opnames: " +
        str(change_counts["unchanged_two_snapshots"]) +
        " · niet vergeleken: " + str(change_counts["not_compared"]),
        "Twee momentopnames, geen doorlopend toezicht of CI-/reviewvrijgave.", "",
    ]
    for card in bundle["items"]:
        lines += [
            "## " + card["project"] + " · [PR #" + str(card["number"]) +
            "](" + card["url"] + ")",
            "",
            "**Huidige blokkade:** " + card["blocker"],
            "**Eerstvolgende menselijke actie:** " + card["human_next_step"],
            "**Werkvolgorde volgens bronopname:** " + card["attention_label"] +
            " (geen merge-/claimtoestemming)",
            "**Categorie:** " + card["category"],
            "**Wijziging sinds vorige opname:** " +
            card["historical_change_label"] +
            (" · HOLD pas herbeoordelen bij nieuw bewijs"
             if card["repeat_hold_deferred"] else "") +
            (" · herbeoordelen; geen bewezen wijziging"
             if card["change_since_previous_observation"] in (
                 "changed_or_uncertain_head", "incomplete_gate_source",
                 "newly_selected_not_new_pr") else ""),
            "**Exact PR HEAD:** " + (
                "[`" + card["head_sha"] + "`](" +
                _commit_link(card, card["head_sha"]) + ")" if card["head_sha"]
                else "ONBEKEND"),
            "**Exact doelbranchbasis:** " + (
                "[`" + card["base_sha"] + "`](" +
                _commit_link(card, card["base_sha"]) + ")" if card["base_sha"]
                else "ONBEKEND"),
            "**Bestandsdiff:** " + card["files_url"],
            "",
            "### Verifieer vóór verandering of merge",
            "1. Open de PR, verifieer opnieuw de HEAD en huidige eigenaar/lease.",
            "2. Inspecteer de bestaande verplicht gestelde exacte-HEAD CI en onafhankelijke review.",
            "3. Respecteer branch protection, bron-/onderzoekintegriteit en de release-autorisatie.",
            "4. Bewaar de bestaande workerclaim; neem geen werk van een andere eigenaar over.",
            "",
        ]
        if card["historical_next_step"]:
            lines.append("**Historische vervolgstap:** " +
                         card["historical_next_step"] +
                         " (geen bewijs over tussenliggende periode).\n")
        if card["historical_changed_gate_evidence_categories"]:
            lines.append("### Veranderd gatebewijs")
            for category in card["historical_changed_gate_evidence_categories"]:
                lines.append("- " + category)
            lines.append("")
        if card["historical_changed_job_links"]:
            lines.append("### Historisch waargenomen checkverschillen")
            for change in card["historical_changed_job_links"]:
                lines.append("- " + change["kind"] + ": [" + change["label"] +
                             "](" + change["url"] + ")")
            lines.append("")
        if card["historical_changed_job_links_hidden"]:
            lines.append("Meer historische checkverschillen buiten voorvertoning: " +
                         str(card["historical_changed_job_links_hidden"]) +
                         " (geen volledige gatebron).\n")
        if card["check_links"]:
            lines.append("### Directe niet-groene check-/statusbronnen")
            for check in card["check_links"]:
                lines.append("- " + check["kind"] + ": [" + check["name"] +
                             "](" + check["url"] + ") · " + check["state"])
            lines.append("")
        if card["check_links_hidden"]:
            lines.append("Extra checks buiten huidige bronvoorvertoning: " +
                         str(card["check_links_hidden"]) + "\n")
        if card["peer_head_evidence"]:
            lines.append("### Geobserveerde botsende PR-commits (geen actuele vrijgave)")
            for peer in card["peer_head_evidence"][:8]:
                lines.append("- [" + peer["url"] + "](" + peer["url"] + ")" +
                             " · [HEAD `" + peer["head_sha_at_observation"][:12] +
                             "`](" + _commit_link(card, peer["head_sha_at_observation"]) +
                             ") · " + str(peer["shared_file_count_observed"]) +
                             " gedeelde bestandspaden in bron" +
                             (" (preview onvolledig)" if peer["preview_incomplete"] else ""))
                for path in peer["shared_files_preview"][:MAX_PATHS]:
                    lines.append("  - `" + path.replace("`", "\\`") + "`")
            lines.append("")
        if card["peer_head_evidence_partial"]:
            lines.append("**ONVOLLEDIGE peer-HEAD bron:** geen conflictvrije conclusie.\n")
        if card["overlap_paths"] or card["related_prs"]:
            lines.append("### Bestandscope-overlap (geen bevestigd Git-conflict)")
            for path in card["overlap_paths"]:
                lines.append("- `" + path.replace("`", "\\`") + "`")
            for other in card["related_prs"]:
                lines.append("- Mogelijk gerelateerde PR: " + other)
            lines.append("")
        if card["unverified_issue_hints"]:
            issue_links = []
            for n in card["unverified_issue_hints"][:MAX_ISSUE_HINTS]:
                if type(n) is not int or not 1 <= n < 100000000:
                    raise ValueError("untrusted issue number hint")
                issue_links.append("[nummer #" + str(n) + "](" +
                                   "https://github.com/" + card["repository"] +
                                   "/issues/" + str(n) + ")")
            lines.append("**Ongeverifieerde issuevermeldingen:** " +
                         ", ".join(issue_links) +
                         " (tekstverwijzingen, link kan naar PR of ontbrekend "
                         "nummer leiden; geen fixbewijs).\n")
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
