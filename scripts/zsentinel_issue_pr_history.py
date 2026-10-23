#!/usr/bin/env python3
"""Bind historical PR changes to real existing P0/P1 issue links (read-only)."""
from __future__ import annotations
import re

URL = re.compile(r"^https://github\.com/Zennay/[A-Za-z0-9_.-]{1,100}/pull/[1-9][0-9]{0,8}$")
EVENTS = {
    "head_or_base_changed": "PR-HEAD of doelbasis gewijzigd tussen bronopnamen",
    "seen_only_current": "PR alleen in huidige steekproef; niet bewezen nieuw",
    "seen_only_previous": "PR alleen in vorige steekproef; niet bewezen gesloten",
    "new_file_overlap": "Nieuw waargenomen gedeeld bestandspad",
    "overlap_not_seen": "Eerdere bestandsoverlap niet meer gezien; geen mergebewijs",
    "current_overlap_sample_only":
        "Overlap alleen in huidige onvolledige steekproef; NIET bewezen nieuw",
    "previous_overlap_sample_only":
        "Overlap alleen in vorige onvolledige steekproef; NIET bewezen opgelost",
}


def attach(report, delta):
    """Only positive, source-bound observations; NEVER proof of fix or permission."""
    if delta is None:
        report["historical_pr_delta"] = None
        report["historical_pr_issue_count"] = 0
        return
    if (type(delta) is not dict
            or delta.get("schema") != "zsentinel-portfolio-pr-snapshot-delta-v1"
            or type(delta.get("projects")) is not list
            or [p.get("project") for p in delta["projects"]] != report["chosen_projects"]
            or any(delta.get(k) is not False for k in
                   ("claim_authorized", "merge_authorized", "writes_performed"))):
        raise ValueError("historical PR source not compatible with current issue report")
    mapped = {}
    for row in delta["projects"]:
        project = row["project"]
        repo = row["repository"]
        if not isinstance(repo, str) or not re.fullmatch(r"Zennay/[A-Za-z0-9_.-]{1,100}", repo):
            raise ValueError("historical repository identity invalid")
        change_map = {}
        def add(url, kind, other=None):
            if (type(url) is not str or URL.fullmatch(url) is None or
                    not url.startswith("https://github.com/" + repo + "/pull/") or
                    (other is not None and
                     (URL.fullmatch(other) is None or
                      not other.startswith("https://github.com/" + repo + "/pull/")))):
                raise ValueError("historical PR URL outside selected source repository")
            change_map.setdefault(url, []).append({
                "kind": kind, "text_nl": EVENTS[kind],
                "related_pr_url": other,
                "current_gate_verified": False, "fix_verified": False,
                "claim_authorized": False, "merge_authorized": False})
        for item in row["head_or_base_changed_in_both_samples"]:
            add(item["url"], "head_or_base_changed")
        for url in row["seen_only_in_current_sample_not_proven_new"]:
            add(url, "seen_only_current")
        for url in row["seen_only_in_previous_sample_not_proven_closed"]:
            add(url, "seen_only_previous")
        for key, kind in (
                ("added_collision_pairs_if_complete", "new_file_overlap"),
                ("disappeared_collision_pairs_if_complete", "overlap_not_seen")):
            for pair in row[key]:
                add(pair["left"], kind, pair["right"])
                add(pair["right"], kind, pair["left"])
        # Sample-only appearances matter for triage on busy repos, but they
        # NEVER prove that a collision is genuinely new or resolved.
        for key, kind in (
                ("overlap_pairs_only_in_current_sample_not_proven_new",
                 "current_overlap_sample_only"),
                ("overlap_pairs_only_in_previous_sample_not_proven_resolved",
                 "previous_overlap_sample_only")):
            for pair in row.get(key, []):
                add(pair["left"], kind, pair["right"])
                add(pair["right"], kind, pair["left"])
        mapped[project] = (
            change_map, row["both_snapshots_stable"],
            row["complete_open_pr_inventory_comparable"])
    issue_count = 0
    for issue in report["issues"]:
        matches, stable, complete = mapped[issue["project"]]
        changed = 0
        for pr in issue["existing_prs"]:
            events = matches.get(pr["url"], [])
            pr["historical_source_events"] = events
            pr["historical_source_activity_observed"] = bool(events)
            changed += bool(events)
        issue["historical_linked_prs_with_changes"] = changed
        issue["historical_linked_pr_activity_observed"] = changed > 0
        issue["historical_pr_inventory_comparable"] = complete
        issue["historical_pr_inventory_stable"] = stable
        issue_count += changed > 0
    report["historical_pr_delta"] = delta
    report["historical_pr_issue_count"] = issue_count
    report["historical_pr_events_do_not_authorize_merge"] = True
