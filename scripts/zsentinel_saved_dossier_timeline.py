#!/usr/bin/env python3
"""Read-only chronological work history over archived zSentinel PR dossiers.

Every input is a private source-bound observation, not a GitHub, ownership,
CI, review, merge, worker-recovery or deployment authority. This command
does not call any external service or modify its source archives.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from html import escape
from io import StringIO
import json
import os
from pathlib import Path
import re
import sys
from zipfile import BadZipFile

from zsentinel_saved_dossier_delta import admit_record, PRIORITIES
from zsentinel_saved_gate_dossier import open_dossier

MIN_SNAPSHOTS = 2
MAX_SNAPSHOTS = 12
MAX_PR_IDS = 600
MAX_OUTPUT = 1024 * 1024
ISSUE_FILTER = re.compile(r"https://github\\.com/Zennay/[A-Za-z0-9_.-]+/issues/[1-9][0-9]{0,8}\\Z")
TAGS = ("changed_head", "changed_gate", "changed_issues",
        "reappeared_in_sample", "missing_between_samples", "last_sample_missing")


def source_freshness(source_snapshots, *, stale_after_hours=6, evaluated_at=None):
    """Label the newest saved observation's age, never live status.

    A recent snapshot is still historical evidence. Clock-skewed or unreadable
    timestamps fail closed and require a fresh source inspection.
    """
    if type(stale_after_hours) is not int or not 1 <= stale_after_hours <= 168:
        raise ValueError("Stale threshold must be an integer from 1 to 168 hours")
    now = evaluated_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Freshness evaluation time must be timezone-aware")
    now = now.astimezone(timezone.utc)
    raw = (source_snapshots[-1].get("latest_observed_utc")
           if source_snapshots else None)
    result = {
        "status": "unknown",
        "latest_observed_utc": raw if isinstance(raw, str) else None,
        "evaluated_at_utc": now.isoformat(),
        "age_hours": None,
        "stale_after_hours": stale_after_hours,
        "source_is_live": False,
        "live_recheck_required_before_action": True,
        "refresh_required": True,
    }
    try:
        if not isinstance(raw, str) or not raw:
            raise ValueError("Missing source timestamp")
        observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("Source timestamp lacks timezone")
        age_seconds = (now - observed.astimezone(timezone.utc)).total_seconds()
        if age_seconds < -300:
            result["status"] = "clock-skew"
            return result
        age_seconds = max(0.0, age_seconds)
        stale = age_seconds > stale_after_hours * 3600
        result.update({
            "status": "stale" if stale else "recent-snapshot",
            "age_hours": round(age_seconds / 3600, 2),
            "refresh_required": stale,
        })
    except (ValueError, TypeError, OverflowError, OSError):
        # Malformed timestamps must never be presented as recent.
        pass
    return result


def build_timeline(paths, *, minimum_sources=MIN_SNAPSHOTS):
    """Summarize up to twelve nonoverlapping fully admitted private sources.

    Issue-first callers may request one saved observation for an immediate
    existing-PR reuse view. The history CLI itself still requires two.
    """
    if (type(minimum_sources) is not int
            or minimum_sources not in (1, MIN_SNAPSHOTS)
            or not minimum_sources <= len(paths) <= MAX_SNAPSHOTS):
        raise ValueError("Expected one to twelve bounded offline source archives")
    if len({str(p.absolute()) for p in paths}) != len(paths):
        raise ValueError("An input archive was repeated")
    source = []
    for path in paths:
        source.append(admit_record(open_dossier(path)))
    scope = source[0]["scope"]
    if any(item["scope"] != scope for item in source):
        raise ValueError("All source snapshots must have the exact same scope")
    for previous, next_item in zip(source, source[1:]):
        if previous["latest"] >= next_item["earliest"]:
            raise ValueError("Source observations are overlapping or unordered")
    urls = sorted(set().union(*(set(snapshot["rows"]) for snapshot in source)))
    if not urls or len(urls) > MAX_PR_IDS:
        raise ValueError("Too many distinct source PR identities")
    rank = {priority: index for index, priority in enumerate(PRIORITIES)}
    history = []
    for url in urls:
        observations = []
        for index, snapshot in enumerate(source):
            row = snapshot["rows"].get(url)
            if row is None:
                continue
            data = row["data"]
            if data["priority"] not in rank:
                raise ValueError("Unrecognized sampled PR priority")
            observations.append((index, row))
        if not observations:
            raise ValueError("Source PR is absent from all observations")
        first_i, first = observations[0]
        last_i, last = observations[-1]
        prev_row = None
        previous_index = None
        tags = set()
        head_changes = 0
        blocker_changes = 0
        comparable_receipt_changes = 0
        incomparable_receipt_pairs = 0
        issue_changes = 0
        issue_links_added_observed = 0
        issue_links_absent_observed = 0
        row_summaries = []
        heads_seen = []
        for idx, row in observations:
            d = row["data"]
            head = d["observed_head"]
            if head is not None and head not in heads_seen:
                heads_seen.append(head)
            changed = []
            linked_now = set(d["linked_issue_urls"])
            links_added = []
            links_absent = []
            comparison_spans_gap = previous_index is not None and idx - previous_index > 1
            if prev_row is not None:
                old = prev_row["data"]
                if previous_index is not None and idx - previous_index > 1:
                    tags.add("reappeared_in_sample")
                    changed.append("reappeared_in_sample")
                if old["observed_head"] != head:
                    tags.add("changed_head")
                    head_changes += 1
                    changed.append("head")
                same_exact_head = (
                    head is not None and old["observed_head"] == head)
                category_changed = (
                    old["primary_gate"] != d["primary_gate"]
                    or old["gate_source"] != d["gate_source"]
                    or old["concurrent_gates"] != d["concurrent_gates"])
                observed_counts_changed = (
                    old.get("gate_observations") != d.get("gate_observations"))
                if category_changed or (same_exact_head and observed_counts_changed):
                    tags.add("changed_gate")
                    blocker_changes += 1
                    changed.append("gate")
                if same_exact_head and observed_counts_changed:
                    comparable_receipt_changes += 1
                    changed.append("same_head_receipt_counts")
                elif not same_exact_head and observed_counts_changed:
                    # Sampled CI/review numbers belong to different HEADs and
                    # may not be counted as progress.
                    incomparable_receipt_pairs += 1
                    changed.append("incomparable_receipt_counts")
                if old["linked_issue_urls"] != d["linked_issue_urls"]:
                    tags.add("changed_issues")
                    issue_changes += 1
                    changed.append("issues")
                    # These are differences between two SAVED observations,
                    # never proof that an issue was opened/closed or fixed.
                    linked_before = set(old["linked_issue_urls"])
                    links_added = sorted(linked_now - linked_before)
                    links_absent = sorted(linked_before - linked_now)
                    issue_links_added_observed += len(links_added)
                    issue_links_absent_observed += len(links_absent)
            row_summaries.append({
                "snapshot_index": idx + 1,
                "observed_utc": source[idx]["latest"].isoformat(),
                "priority": d["priority"],
                "observed_head": head,
                "primary_gate": d["primary_gate"],
                "gate_source": d["gate_source"],
                "linked_issue_urls": d["linked_issue_urls"],
                "newly_observed_linked_issue_urls": links_added,
                "no_longer_observed_linked_issue_urls": links_absent,
                "issue_link_comparison_spans_unsampled_snapshot": comparison_spans_gap,
                "observed_gate_counts": d.get("gate_observations"),
                "changes_from_last_seen_source": changed,
                "live_status_verified": False,
            })
            prev_row = row
            previous_index = idx
        if any(index not in {i for i, _ in observations}
               for index in range(first_i, last_i + 1)):
            tags.add("missing_between_samples")
        if last_i < len(source) - 1:
            tags.add("last_sample_missing")
        recent = last["data"]
        if "last_sample_missing" in tags:
            action = ("Controleer de oorspronkelijke PR live; afwezigheid uit "
                      "latere steekproeven bewijst GEEN merge of sluiting.")
        elif "changed_head" in tags:
            action = ("Controleer de nieuwe exacte PR-HEAD en alle actuele "
                      "verplichte CI, onafhankelijke review en claims.")
        elif recent["primary_gate"] == "source":
            action = ("Bron/HEAD of gatebewijs ontbreekt; herstel de bronselectie "
                      "voordat je een releasebesluit neemt.")
        elif "changed_issues" in tags:
            action = ("Controleer de eerder en later waargenomen issuekoppelingen "
                      "met de PR-eigenaar; steekproefverschil bewijst geen fix.")
        else:
            action = ("Controleer de PR en de huidige owner/CI/review-status live; "
                      "herhaalde opgeslagen signalen zijn geen releasebewijs.")
        history.append({
            "repository": last["repository"],
            "number": last["number"],
            "url": url,
            "latest_sample_priority": recent["priority"],
            "latest_sample_primary_gate": recent["primary_gate"],
            "latest_observed_head": recent["observed_head"],
            "first_seen_snapshot": first_i + 1,
            "last_seen_snapshot": last_i + 1,
            "observed_in_snapshots": len(observations),
            "total_source_snapshots": len(source),
            "missing_from_final_snapshot": last_i != len(source) - 1,
            "distinct_observed_heads": len(heads_seen),
            "observed_head_changes": head_changes,
            "observed_gate_changes": blocker_changes,
            "same_exact_head_receipt_changes": comparable_receipt_changes,
            "incomparable_receipt_observation_pairs": incomparable_receipt_pairs,
            "observed_issue_changes": issue_changes,
            "issue_links_added_between_saved_observations": issue_links_added_observed,
            "issue_links_absent_between_saved_observations": issue_links_absent_observed,
            "tags": [tag for tag in TAGS if tag in tags],
            "observations": row_summaries,
            "human_next_action": action,
            "current_owner_claim_verified": False,
            "required_ci_reviews_verified": False,
            "merged_on_main_verified": False,
            "live_verified": False,
        })
    history.sort(key=lambda row: (
        rank[row["latest_sample_priority"]],
        0 if row["missing_from_final_snapshot"] else 1,
        -row["observed_gate_changes"], -row["observed_head_changes"],
        row["repository"], row["number"]))
    return {
        "schema": "zsentinel-private-saved-dossier-timeline-v1",
        "source": "multiple_saved_private_zip_samples_not_live",
        "scope": scope,
        "source_snapshots": [{
            "index": i + 1,
            "original_review_manifest_sha256": s["manifest_sha256"],
            "original_board_sha256": s["board_sha256"],
            "earliest_observed_utc": s["earliest"].isoformat(),
            "latest_observed_utc": s["latest"].isoformat(),
            "selected_pr_candidates": s["sample_count"],
            "all_original_source_candidates": s["original_source_count"],
            "missing_batch_indexes": s["missing_batch_indexes"],
        } for i, s in enumerate(source)],
        "snapshot_inventories_can_differ": True,
        "absent_from_sample_is_not_closed": True,
        "historical_observations_are_not_current_ci_or_review": True,
        "distinct_pr_candidates": len(history),
        "entries": history,
        "main_merge_authorized": False,
        "worker_dispatch_authorized": False,
        "owner_claim_verified": False,
        "merged_on_main_verified": False,
        "live_verified": False,
        "external_writes_performed": False,
    }




def select_page(report, *, priority=None, tag=None, issue_url=None,
                page=1, page_size=40):
    """Filter the *fully admitted* PR set, retaining honest source totals.

    Pages are human display slices only, not evidence of other PRs being
    closed, green, owner-free, or excluded from GitHub/Notion work.
    """
    if priority is not None and priority not in PRIORITIES:
        raise ValueError("Unknown source priority filter")
    if tag is not None and tag not in TAGS:
        raise ValueError("Unknown observed source-change label")
    if issue_url is not None and (
            type(issue_url) is not str or not ISSUE_FILTER.fullmatch(issue_url)):
        raise ValueError("Issue filter must be a canonical GitHub issue URL")
    if (type(page) is not int or not 1 <= page <= 100
            or type(page_size) is not int or not 1 <= page_size <= 100):
        raise ValueError("Invalid bounded page selection")
    matched = [
        item for item in report["entries"]
        if (priority is None or item["latest_sample_priority"] == priority)
        and (tag is None or tag in item["tags"])
        and (issue_url is None or any(
            issue_url in observed["linked_issue_urls"]
            for observed in item["observations"]))
    ]
    first = (page - 1) * page_size
    if first >= len(matched) and (page != 1 or matched):
        raise ValueError("Page outside selected saved source worklist")
    result = dict(report)
    visible = []
    for item in matched[first:first + page_size]:
        if issue_url is None:
            visible.append(item)
            continue
        seen_in = [row["snapshot_index"] for row in item["observations"]
                   if issue_url in row["linked_issue_urls"]]
        if not seen_in:
            raise ValueError("Issue filter contradicts admitted PR history")
        last_pr_snapshot = item["observations"][-1]
        in_last_pr_snapshot = issue_url in last_pr_snapshot["linked_issue_urls"]
        entry = dict(item)
        entry["selected_issue_source_history"] = {
            "issue_url": issue_url,
            "linked_observed_in_snapshots": seen_in,
            "last_linked_snapshot": seen_in[-1],
            "linked_in_last_observed_pr_snapshot": in_last_pr_snapshot,
            "linked_in_latest_source_snapshot": (
                in_last_pr_snapshot and
                last_pr_snapshot["snapshot_index"] == len(report["source_snapshots"])),
            "current_issue_resolved_verified": False,
            "live_owner_verified": False,
        }
        visible.append(entry)
    result["entries"] = visible
    result["display"] = {
        "source_pr_candidates_total": report["distinct_pr_candidates"],
        "matched_source_pr_candidates": len(matched),
        "shown_source_pr_candidates": len(result["entries"]),
        "first_match_position": first + 1 if result["entries"] else None,
        "last_match_position": first + len(result["entries"]),
        "priority_filter": priority,
        "observed_change_filter": tag,
        "linked_issue_ever_observed_filter": issue_url,
        "linked_issue_filter_is_not_fix_proof": True,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (len(matched) + page_size - 1) // page_size),
        "not_shown_is_never_resolved": True,
    }
    return result


def markdown(report):
    lines = [
        "# zSentinel — historisch PR-volgscherm", "",
        "> Privé opgeslagen momentopnamen: geen actuele GitHub, owner, verplichte "
        "CI, review, main-merge of deploy bewezen.",
        "",
        "- Projectscope: " + (report["scope"] or "oorspronkelijke portfolio-samples"),
        "- Geselecteerd historisch issue: " +
          (report["display"]["linked_issue_ever_observed_filter"] or "alle issues") +
          " (ooit in een opgeslagen PR-koppeling; GEEN actuele eigenaarschap/fix)",
        "- Bronmomenten: " + str(len(report["source_snapshots"])),
        "- Bronactualiteit: " + report["source_freshness"]["status"].upper() +
          " · leeftijd " + (str(report["source_freshness"]["age_hours"]) + " uur"
          if report["source_freshness"]["age_hours"] is not None else "ONBEKEND") +
          " · drempel " + str(report["source_freshness"]["stale_after_hours"]) +
          " uur · laatste bron UTC " +
          str(report["source_freshness"]["latest_observed_utc"] or "ONBEKEND"),
        "- Evaluatie UTC: " + report["source_freshness"]["evaluated_at_utc"] +
          " · live status NIET GEVERIFIEERD; hercontrole vóór actie verplicht",
        "- Verschillende bron-PR's totaal: " +
          str(report["distinct_pr_candidates"]),
        "- Weergegeven: " + str(report["display"]["shown_source_pr_candidates"]) +
          " van " + str(report["display"]["matched_source_pr_candidates"]) +
          " filtermatches · pagina " + str(report["display"]["page"]) + "/" +
          str(report["display"]["total_pages"]), "",
    ]
    for s in report["source_snapshots"]:
        lines.append("- Bron " + str(s["index"]) + ": " +
                     s["latest_observed_utc"] + " · " +
                     str(s["selected_pr_candidates"]) + " kandidaten · manifest " +
                     s["original_review_manifest_sha256"])
    lines.extend(["", "## Bestaande PRs in prioriteitsvolgorde", ""])
    for item in report["entries"]:
        lines.extend([
            "- **" + item["latest_sample_priority"] + "** [" +
            item["repository"] + "#" + str(item["number"]) + "](" +
            item["url"] + ") — " + str(item["observed_in_snapshots"]) +
            "/" + str(item["total_source_snapshots"]) + " bronmomenten; " +
            str(item["observed_head_changes"]) + " HEAD-wissels, " +
            str(item["observed_gate_changes"]) + " gatewaarnemingswissels; " +
            str(item["observed_issue_changes"]) + " issuekoppelingswissels (" +
            str(item["issue_links_added_between_saved_observations"]) +
            " nieuw waargenomen / " +
            str(item["issue_links_absent_between_saved_observations"]) +
            " niet langer waargenomen); " +
            str(item["incomparable_receipt_observation_pairs"]) +
            " niet-vergelijkbare HEAD-receipts.",
            "  - Laatste bron " + str(item["last_seen_snapshot"]) +
            " (ontbreekt in laatste: " +
            ("JA, GEEN sluitbewijs" if item["missing_from_final_snapshot"]
             else "NEE") + "); tags: " +
            (", ".join(item["tags"]) or "geen waarneembare bronwissels") + ".",
            "  - Menselijke actie: " + item["human_next_action"],
        ])
        selected_issue = item.get("selected_issue_source_history")
        if selected_issue is not None:
            lines.append(
                "  - Geselecteerd issue laatst gekoppeld in bron " +
                str(selected_issue["last_linked_snapshot"]) +
                "; nog gekoppeld bij laatste PR-waarneming: " +
                ("JA (historisch)" if selected_issue["linked_in_last_observed_pr_snapshot"]
                 else "NEE (geen fixbewijs)") +
                "; gekoppeld in laatste volledige bronsample: " +
                ("JA (historisch)" if selected_issue["linked_in_latest_source_snapshot"]
                 else "NIET BEWEZEN") + ".")
        for observation in item["observations"]:
            lines.append("    - #" + str(observation["snapshot_index"]) +
                         " " + observation["observed_utc"] + " · HEAD " +
                         (observation["observed_head"] or "ONBEKEND") +
                         " · gate " + observation["primary_gate"] +
                         " · bron " + observation["gate_source"])
            if observation["newly_observed_linked_issue_urls"]:
                lines.append("      + Issuekoppelingen in deze steekproef: " +
                             ", ".join(observation["newly_observed_linked_issue_urls"]))
            if observation["no_longer_observed_linked_issue_urls"]:
                lines.append("      - Issuekoppelingen niet meer in deze steekproef: " +
                             ", ".join(observation["no_longer_observed_linked_issue_urls"]))
            if (observation["issue_link_comparison_spans_unsampled_snapshot"] and
                    (observation["newly_observed_linked_issue_urls"] or
                     observation["no_longer_observed_linked_issue_urls"])):
                lines.append("      ! Er waren tussenliggende bronmomenten zonder deze PR; "
                             "het exacte moment van wijziging is ONBEKEND.")
    lines.extend(["", "Nieuwe of weggevallen PR-waarnemingen zijn NIET bewezen "
                  "nieuw aangemaakt of gemerged. Eerst actuele GitHub PR-HEAD, "
                  "notionclaims, verplichte CI, onafhankelijke review en "
                  "protected-main regels verifiëren.", ""])
    return "\n".join(lines)


def csv_content(report):
    target = StringIO(newline="")
    writer = csv.writer(target, delimiter=";", quotechar='"', lineterminator="\r\n")
    writer.writerow([
        "Project", "Bronprioriteit", "PR URL", "Waarnemingen",
        "Totaal bronmomenten", "Eerste bron", "Laatste bron",
        "Ontbreekt laatste steekproef", "HEAD wijzigingen", "Gate wijzigingen",
        "Issue wijzigingen", "Nieuw geobserveerde issuekoppelingen",
        "Niet meer geobserveerde issuekoppelingen",
        "Zelfde-HEAD ontvangstwaarneming gewijzigd",
        "Niet vergelijkbare HEAD-receipts", "Laatste gateklasse", "Bronlabels",
        "Menselijke vervolgactie", "Actuele CI/review/owner",
        "Main merge bewezen", "Live bewezen", "Bronactualiteit",
        "Leeftijd bron uren", "Bronactualiteit geëvalueerd UTC",
        "Stale-drempel uren", "Live status geverifieerd",
        "Geselecteerd historisch issue", "Laatst gekoppeld bronmoment",
        "Link in laatste PR-steekproef", "Link in laatste totaalsample",
        "Issue live opgelost bewezen",
    ])
    for item in report["entries"]:
        writer.writerow([
            item["repository"], item["latest_sample_priority"], item["url"],
            item["observed_in_snapshots"], item["total_source_snapshots"],
            item["first_seen_snapshot"], item["last_seen_snapshot"],
            "JA, NIET bewezen gemerged" if item["missing_from_final_snapshot"]
            else "NEE",
            item["observed_head_changes"], item["observed_gate_changes"],
            item["observed_issue_changes"],
            item["issue_links_added_between_saved_observations"],
            item["issue_links_absent_between_saved_observations"],
            item["same_exact_head_receipt_changes"],
            item["incomparable_receipt_observation_pairs"],
            item["latest_sample_primary_gate"],
            " | ".join(item["tags"]), item["human_next_action"],
            "ONBEKEND", "ONBEKEND", "ONBEKEND",
            report["source_freshness"]["status"],
            report["source_freshness"]["age_hours"] if report["source_freshness"]["age_hours"] is not None else "ONBEKEND",
            report["source_freshness"]["evaluated_at_utc"],
            report["source_freshness"]["stale_after_hours"],
            "NEE",
            (item.get("selected_issue_source_history") or {}).get("issue_url", ""),
            (item.get("selected_issue_source_history") or {}).get("last_linked_snapshot", ""),
            ("JA, HISTORISCH" if (item.get("selected_issue_source_history") or {})
             .get("linked_in_last_observed_pr_snapshot") else "NIET BEWEZEN"),
            ("JA, HISTORISCH" if (item.get("selected_issue_source_history") or {})
             .get("linked_in_latest_source_snapshot") else "NIET BEWEZEN"),
            "NEE",
        ])
    return "\ufeff" + target.getvalue()


def html_content(report):
    e = lambda value: escape(str(value), quote=True)
    cards = []
    for item in report["entries"]:
        observations = []
        for o in item["observations"]:
            observed = o.get("observed_gate_counts")
            stats = (
                " · checks afgerond " + str(observed["checks_completed"]) +
                ", lopend " + str(observed["checks_pending"]) +
                ", niet succesvol " + str(observed["checks_not_successful"])
                if observed is not None else " · bronchecks ONBEKEND")
            issues = " · ".join(
                '<a href="' + e(url) +
                '" target="_blank" rel="noopener noreferrer">issue #' +
                e(url.rsplit("/", 1)[-1]) + "</a>"
                for url in o["linked_issue_urls"])
            added = o["newly_observed_linked_issue_urls"]
            absent = o["no_longer_observed_linked_issue_urls"]
            issue_delta = ""
            for label, links in (("Nieuw waargenomen gekoppeld issue", added),
                                 ("Niet meer in bron gekoppeld issue", absent)):
                if links:
                    issue_delta += "<p>" + e(label) + ": " + " · ".join(
                        '<a href="' + e(url) +
                        '" target="_blank" rel="noopener noreferrer">' +
                        e(url.rsplit("/", 1)[-1]) + "</a>" for url in links) + "</p>"
            if (o["issue_link_comparison_spans_unsampled_snapshot"]
                    and (added or absent)):
                issue_delta += ("<p>Er ontbreken tussenliggende PR-bronmomenten; "
                                "het werkelijke wijzigingsmoment blijft ONBEKEND.</p>")
            observations.append(
                "<li><strong>Bron " + str(o["snapshot_index"]) +
                "</strong> · " + e(o["observed_utc"]) + " · HEAD " +
                e(o["observed_head"] or "ONBEKEND") + " · " +
                e(o["primary_gate"]) + e(stats) + "<br>" + issues +
                issue_delta + "</li>")
        linked_issue = item.get("selected_issue_source_history")
        focus_note = ""
        if linked_issue is not None:
            focus_note = (
                "<p>Geselecteerd issue laatst gekoppeld bij bron " +
                str(linked_issue["last_linked_snapshot"]) +
                " · nog gekoppeld in laatste PR-bron: " +
                ("JA, HISTORISCH" if linked_issue["linked_in_last_observed_pr_snapshot"]
                 else "NIET WAARGENOMEN") +
                " · gekoppeld in laatste volledige bronsample: " +
                ("JA, HISTORISCH" if linked_issue["linked_in_latest_source_snapshot"]
                 else "NIET BEWEZEN") +
                ". Een ontbrekende koppeling bewijst geen issuefix.</p>")
        cards.append(
            '<article><h2><a target="_blank" rel="noopener noreferrer" href="' +
            e(item["url"]) + '">' + e(item["latest_sample_priority"]) +
            " " + e(item["repository"]) + "#" + str(item["number"]) +
            '</a></h2><p>Bronwaarnemingen: ' +
            str(item["observed_in_snapshots"]) + "/" +
            str(item["total_source_snapshots"]) + " · HEAD-wissels: " +
            str(item["observed_head_changes"]) +
            " · Gatewissels: " + str(item["observed_gate_changes"]) +
            " · Issuekoppelingen nieuw/afwezig: " +
            str(item["issue_links_added_between_saved_observations"]) + "/" +
            str(item["issue_links_absent_between_saved_observations"]) +
            " · Niet-vergelijkbare HEAD-receipts: " +
            str(item["incomparable_receipt_observation_pairs"]) +
            "</p><p><strong>" + e(item["human_next_action"]) +
            "</strong></p>" + focus_note +
            "<details><summary>Volledige bronhistorie</summary><ol>" +
            "".join(observations) + "</ol></details></article>")
    times = " · ".join(
        "Bron " + str(s["index"]) + ": " + e(s["latest_observed_utc"])
        for s in report["source_snapshots"])
    paging = report["display"]
    freshness = report["source_freshness"]
    issue_filter = paging["linked_issue_ever_observed_filter"]
    issue_filter_html = (
        '<p><strong>Historische issue-selectie:</strong> <a target="_blank" '
        'rel="noopener noreferrer" href="' + e(issue_filter) + '">' +
        e(issue_filter) + '</a>. Ooit geobserveerde link, '
        'geen live issuefix of eigenaarschap.</p>'
        if issue_filter else ""
    )
    freshness_age = (str(freshness["age_hours"]) + " uur"
                     if freshness["age_hours"] is not None else "ONBEKEND")
    freshness_notice = (
        "Vernieuw de oorspronkelijke bron vóór beslissingen; dit dossier is historisch."
        if freshness["refresh_required"] else
        "De bron is historisch; controleer live eigenaar, CI en merge vóór actie."
    )
    return ("""<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; object-src 'none'; img-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>zSentinel · historisch PR-overzicht</title>
<style>:root{font-family:system-ui;color-scheme:dark}
body{background:#102034;color:#f2f8ff;margin:0;line-height:1.55}
main{max-width:1200px;margin:auto;padding:clamp(16px,4vw,40px)}
aside,article{padding:16px 20px;border:1px solid #607b9b;border-radius:12px;margin:15px 0;background:#1a324e}
aside{border-color:#e9b65d}a{color:#9bddff}
a:focus-visible,summary:focus-visible{outline:3px solid #f4c86c;outline-offset:4px}
article h2{font-size:1.15rem}article details{margin-top:14px}
summary{cursor:pointer;font-weight:bold;padding:9px 0}
li{margin:9px 0;overflow-wrap:anywhere}
@media print{body,aside,article{background:white;color:black}a{color:#05466b}}
</style></head><body><main><h1>Historische zSentinel PR-werkrij</h1>
<aside><strong>Niet live en nooit mergevrijgave.</strong> Alle statussen zijn
alleen waarnemingen in privé opgeslagen bronnen. Ook herhaald ontbreken
in latere bronnen bewijst geen merge, sluiting of vrij eigenaarschap.</aside>
<p><strong>Bronactualiteit: """ + e(freshness["status"].upper()) + " · leeftijd " + e(freshness_age) + " · drempel " + str(freshness["stale_after_hours"]) + " uur · laatste bron UTC " + e(freshness["latest_observed_utc"] or "ONBEKEND") + ".</strong> " + e(freshness_notice) + " Evaluatie UTC: " + e(freshness["evaluated_at_utc"]) + ". Een recente snapshot is geen live status.</p><p>""" + times + """</p><p><strong>Pagina """ +
str(paging["page"]) + "/" + str(paging["total_pages"]) + """</strong> · """ +
str(paging["shown_source_pr_candidates"]) + " getoond van " +
str(paging["matched_source_pr_candidates"]) + " filtersmatches; " +
str(paging["source_pr_candidates_total"]) + """ PRs in de bron.
Niet-getoond is niet opgelost.</p>""" + issue_filter_html + "".join(cards) + """
<footer>Controleer de actuele GitHub HEAD, CI, reviews, Notion-eigenaar,
protected-main en de echte merge-commit voordat een release mag plaatsvinden.
Geen API-calls, geen automatische claims of dispatch.</footer>
</main></body></html>""")


def render(report, fmt):
    if fmt == "json":
        rendered = json.dumps(report, sort_keys=True, indent=2,
                              ensure_ascii=False) + "\n"
    elif fmt == "markdown":
        rendered = markdown(report)
    elif fmt == "csv":
        rendered = csv_content(report)
    else:
        rendered = html_content(report)
    result = rendered.encode("utf-8")
    if len(result) == 0 or len(result) > MAX_OUTPUT:
        raise ValueError("Private timeline report exceeds one MiB")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", required=True,
                        help="Chronologically ordered private portfolio-zip, repeat 2-12 times")
    parser.add_argument("--stale-after-hours", type=int, default=6,
                        help="Mark saved evidence stale after 1..168 hours (default: 6)")
    parser.add_argument("--priority", choices=PRIORITIES,
                        help="Filter saved PRs by priority after full source admission")
    parser.add_argument("--change", choices=TAGS,
                        help="Only show selected observed historical change type")
    parser.add_argument("--issue",
                        help="Filter PRs ever observed linked to exact canonical issue URL; never implies a fix")
    parser.add_argument("--page", type=int, default=1,
                        help="Display page, 1..100, default 1")
    parser.add_argument("--page-size", type=int, default=40,
                        help="Human display PRs per page, 1..100, default 40")
    parser.add_argument("--format", choices=("html", "markdown", "csv", "json"),
                        default="html")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_timeline(args.source)
        report["source_freshness"] = source_freshness(
            report["source_snapshots"], stale_after_hours=args.stale_after_hours)
        selected = select_page(report, priority=args.priority, tag=args.change,
                               issue_url=args.issue, page=args.page,
                               page_size=args.page_size)
        data = render(selected, args.format)
        if not hasattr(os, "O_NOFOLLOW"):
            raise ValueError("Required safe output flags unavailable")
        fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            args.output.unlink(missing_ok=True)
            raise
        print("Private read-only PR timeline written: " +
              str(selected["display"]["shown_source_pr_candidates"]) +
              " shown, " +
              str(selected["display"]["matched_source_pr_candidates"]) +
              " matched out of " + str(report["distinct_pr_candidates"]) +
              " source PRs across " + str(len(report["source_snapshots"])) +
              " snapshots. Source freshness=" +
              report["source_freshness"]["status"] + ", age_hours=" +
              str(report["source_freshness"]["age_hours"]) +
              ". No current merge/live status inferred.")
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError,
            BadZipFile, OverflowError) as exc:
        print("Refused invalid, reordered or oversized source dossier: " +
              type(exc).__name__, file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
