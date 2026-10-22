#!/usr/bin/env python3
"""Build an offline, private history of verified zSentinel operator bundles.

The index compares bounded saved issue samples. It is not a live source, an
ownership detector, evidence of closed issues, or release authorization.
No network, worker, queue, CI, review or deploy operations are performed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
from html import escape
import hashlib
import json
import os
import stat
from pathlib import Path
import re
import sys

from zsentinel_operator_workbench import validated_view
from zsentinel_product_delivery_board import write_create_only
from zsentinel_verify_operator_bundle import (
    BundleInvalid, MAX_PRODUCT, STEM, _decode_json,
    _open_private_directory, _read_private, verify_bundle,
)

INDEX = re.compile(r"^[a-z][a-z0-9_-]{0,50}$")
PRIORITIES = ("P0", "P1", "P2", "Unspecified")
MAX_BUNDLES = 24
MAX_LINKS = 12


def load_snapshot(directory: Path, stem: str) -> dict:
    """Read exactly the verified saved bundle, with no inferred live authority."""
    proof = verify_bundle(directory, stem)
    fd = _open_private_directory(directory)
    try:
        raw = _read_private(fd, stem + ".board.json", MAX_PRODUCT)
    finally:
        import os
        os.close(fd)
    if hashlib.sha256(raw).hexdigest() != proof["board_sha256"]:
        raise BundleInvalid("verified board changed during index collection")
    board = _decode_json(raw)
    view = validated_view(board)
    if view["observed_at_utc"] != board["observed_at_utc"]:
        raise BundleInvalid("saved observation timestamp does not match")
    observed = datetime.fromisoformat(
        board["observed_at_utc"].replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    cards = view["cards"]
    projects = {}
    for card in cards:
        pid = card["project"]
        project = projects.setdefault(pid, {
            "issues": {}, "issue_priorities": {}, "priorities": Counter(), "prs": set(),
            "operations": None,
        })
        project["issues"][card["issue"]] = card["url"]
        project["issue_priorities"][card["issue"]] = card["priority"]
        project["priorities"][card["priority"]] += 1
        project["prs"].update(card["open_pr_candidates"])
    # Retain validated aggregate queue/coverage/recommendation facts from the
    # same saved board. Never reconstruct a queue from issue-card absence.
    for row in view["project_operations"]:
        pid = row["project"]
        project = projects.setdefault(pid, {
            "issues": {}, "issue_priorities": {}, "priorities": Counter(), "prs": set(),
            "operations": None,
        })
        project["operations"] = {
            "issue_coverage": row["issue_coverage"],
            "issue_counts_in_sample": row["issue_counts_in_sample"],
            "queue_observation": row["queue_observation"],
            "queue_source_observed_at_utc": row["queue_source_observed_at_utc"],
            "claim_context": row["claim_context"],
            "recommended_next_step": row["recommended_next_step"],
        }
    # These bounded PR rows were already fully admitted by validated_view().
    # Do not rely on issue-card links: an observed PR can have zero issue cards.
    saved_prs = {
        row["url"]: {
            "url": row["url"],
            "head_sha": row["head_sha"],
            "observed_check_success": row["observed_check_success"],
            "legacy_context_success": row.get("legacy_context_success"),
            "observed_approvals": row["observed_approvals"],
            "mergeable_observed": row["mergeable_observed"],
            "blocking_unknowns": list(row["blocking_unknowns"]),
            "source_observed_at_utc": view["project_source_observed_at_utc"].get("prs"),
            "merge_authorized": False,
        }
        for row in board.get("pr_delivery_observations", [])
    }
    return {
        "stem": stem, "observed": observed,
        "source_times": board["source_times_utc"],
        "source_fresh": board["fresh"],
        "sha256": proof["board_sha256"],
        "projects": projects,
        "saved_pr_observations": saved_prs,
        "card_count": len(cards),
        "sample_truncated": bool(
            board.get("coverage", {}).get("cards_truncated", True)
        ),
        "inventory_complete_verified": False,
    }


def issue_links(items: dict, maximum=MAX_LINKS) -> str:
    """All links originate from validated canonical GitHub issue identities."""
    if not items:
        return "<span>Geen in deze opgeslagen steekproef</span>"
    subset = sorted(items.items())
    links = [
        "<a rel='noopener noreferrer' target='_blank' href='" +
        escape(url, quote=True) + "'>" + escape(issue) + "</a>"
        for issue, url in subset[:maximum]
    ]
    if len(subset) > maximum:
        links.append("+" + str(len(subset) - maximum) + " niet getoond")
    return ", ".join(links)


def queue_summary(queue: dict | None) -> str:
    """Render observed aggregates only; missing/stale sources are not zero."""
    if queue is None:
        return "geen verse queuebron"
    return (
        "actief " + str(queue["active_items"]) +
        " · hoge pogingen " + str(queue["high_claim_attempts"]) +
        " · verlopen leases " + str(queue["expired_active_leases"])
    )


def project_table(snapshot: dict) -> str:
    rows = []
    for pid, data in sorted(snapshot["projects"].items()):
        counts = data["priorities"]
        operations = data.get("operations") or {}
        queue = operations.get("queue_observation")
        coverage = operations.get("issue_coverage", "niet waargenomen")
        source_counts = operations.get("issue_counts_in_sample") or {}
        source_priority = (
            "bron P0 " + str(source_counts.get("P0", "onbekend")) +
            " / P1 " + str(source_counts.get("P1", "onbekend"))
        )
        queue_text = queue_summary(queue)
        # Issue and PR pointers are canonical URLs admitted by the saved
        # product board. Make the historical project actionable to inspect,
        # not actionable to claim, retry or merge.
        issue_rows = sorted(data["issues"].items(), key=lambda row: (
            PRIORITIES.index(data["issue_priorities"][row[0]]), row[0]))
        issue_links_html = "".join(
            "<li><a rel='noopener noreferrer' target='_blank' href='" +
            escape(url, quote=True) + "'>" +
            escape(data["issue_priorities"][issue]) + " · " +
            escape(issue) + "</a></li>"
            for issue, url in issue_rows[:16]
        )
        remaining = max(0, len(issue_rows) - 16)
        saved_navigation = (
            "<details><summary>Opgeslagen issues (" +
            str(len(issue_rows)) + ") en PR-kandidaten (" +
            str(len(data["prs"])) + ")</summary><p>Dit zijn uitsluitend "
            "bronlinks in deze historische steekproef. Open actuele GitHub "
            "en controleer claims, reviews en CI opnieuw.</p>" +
            ("<ul>" + issue_links_html + "</ul>" if issue_rows else
             "<p>Geen issuekaart in dit sample; backlog onbekend.</p>") +
            ("<p>+" + str(remaining) + " issuekaarten niet getoond.</p>"
             if remaining else "") +
            "<p>Historische PR-kandidaten: " +
            pr_links(data["prs"]) + "</p></details>"
        )
        details = (
            "<details><summary>Bron en vervolgactie</summary><p>" +
            "Dekking: " + escape(str(coverage)) + " · " +
            escape(source_priority) + "</p><p>Claimcontext: " +
            escape(str(operations.get("claim_context", "niet geverifieerd"))) +
            "</p><p>Queue-bronklok: " +
            escape(str(operations.get("queue_source_observed_at_utc") or
                       "geen verse queuewaarneming")) + "</p><p>Vervolgadvies: " +
            escape(str(operations.get("recommended_next_step") or
                       "Geen opgeslagen advies voor dit project.")) +
            "</p></details>" + saved_navigation
        )
        rows.append(
            "<tr><th scope='row'>" + escape(pid) + "</th><td>" +
            str(counts["P0"]) + "</td><td>" + str(counts["P1"]) +
            "</td><td>" + str(len(data["issues"])) + "</td><td>" +
            str(len(data["prs"])) + "</td><td>" + escape(str(coverage)) +
            "</td><td>" + escape(queue_text) + details + "</td></tr>"
        )
    return (
        "<table><thead><tr><th>Project (sample)</th><th>Kaart-P0</th>"
        "<th>Kaart-P1</th><th>Issuekaarten</th><th>PR-kandidaten</th>"
        "<th>Brondekking</th><th>Opgeslagen queuebeeld en advies</th></tr></thead><tbody>" +
        ("".join(rows) if rows else
         "<tr><td colspan='7'>Geen projectgegevens in de begrensde sample</td></tr>") +
        "</tbody></table>"
    )


def pr_links(items: set[str]) -> str:
    if not items:
        return "geen"
    urls = sorted(items)
    links = [
        "<a rel='noopener noreferrer' target='_blank' href='" +
        escape(url, quote=True) + "'>" + escape(url.rsplit("/", 1)[-1]) +
        "</a>" for url in urls[:MAX_LINKS]
    ]
    if len(urls) > MAX_LINKS:
        links.append("+" + str(len(urls) - MAX_LINKS) + " niet getoond")
    return ", ".join(links)


def compare(previous: dict, current: dict) -> str:
    rows = []
    old_fresh = previous.get("source_fresh", {})
    new_fresh = current.get("source_fresh", {})
    freshness_changes = {
        name: {"before": old_fresh.get(name), "after": new_fresh.get(name)}
        for name in sorted(set(old_fresh) | set(new_fresh))
        if old_fresh.get(name) != new_fresh.get(name)
    }
    freshness_labels = {
        True: "vers bij opname", False: "verouderd bij opname",
        None: "niet waargenomen",
    }
    freshness_html = (
        "<p><strong>Bronfreshness gewijzigd:</strong> " +
        "; ".join(
            escape(name) + ": " +
            escape(freshness_labels.get(change["before"], "onbekend")) + " → " +
            escape(freshness_labels.get(change["after"], "onbekend"))
            for name, change in freshness_changes.items()
        ) + "</p>" if freshness_changes else
        "<p>Bronfreshness ongewijzigd binnen de opgeslagen bronvlaggen.</p>"
    )
    for project in sorted(set(previous["projects"]) | set(current["projects"])):
        old_data = previous["projects"].get(project, {})
        new_data = current["projects"].get(project, {})
        before = old_data.get("issues", {})
        after = new_data.get("issues", {})
        newly = {key: after[key] for key in after.keys() - before.keys()}
        absent = {key: before[key] for key in before.keys() - after.keys()}
        continued = len(after.keys() & before.keys())
        new_prs = new_data.get("prs", set()) - old_data.get("prs", set())
        missing_prs = old_data.get("prs", set()) - new_data.get("prs", set())
        old_priority = old_data.get("issue_priorities", {})
        new_priority = new_data.get("issue_priorities", {})
        escalated = {
            issue: after[issue] for issue in after.keys() & before.keys()
            if PRIORITIES.index(new_priority[issue]) <
               PRIORITIES.index(old_priority[issue])
        }
        old_ops = old_data.get("operations") or {}
        new_ops = new_data.get("operations") or {}
        old_coverage = old_ops.get("issue_coverage")
        new_coverage = new_ops.get("issue_coverage")
        old_queue = old_ops.get("queue_observation")
        new_queue = new_ops.get("queue_observation")
        old_clock = old_ops.get("queue_source_observed_at_utc")
        new_clock = new_ops.get("queue_source_observed_at_utc")
        old_advice = old_ops.get("recommended_next_step")
        new_advice = new_ops.get("recommended_next_step")
        old_claim_context = old_ops.get("claim_context")
        new_claim_context = new_ops.get("claim_context")
        op_changes = []
        if old_coverage != new_coverage:
            op_changes.append("Dekking: " + str(old_coverage or "niet waargenomen") +
                              " → " + str(new_coverage or "niet waargenomen"))
        if old_queue != new_queue:
            op_changes.append("Queue: " + queue_summary(old_queue) +
                              " → " + queue_summary(new_queue))
        if old_clock != new_clock and (old_clock is not None or new_clock is not None):
            op_changes.append("Queuebronklok: " + str(old_clock or "geen verse bron") +
                              " → " + str(new_clock or "geen verse bron"))
        if old_claim_context != new_claim_context:
            op_changes.append("Claimcontext: " + str(old_claim_context or "onbekend") +
                              " → " + str(new_claim_context or "onbekend"))
        if old_advice != new_advice:
            op_changes.append("Vervolgadvies gewijzigd")
        if not (newly or absent or new_prs or missing_prs or escalated or op_changes):
            continue
        advice_detail = ""
        if old_advice != new_advice:
            advice_detail = (
                "<details><summary>Vervolgadvies oud → nieuw</summary><p>Oud: " +
                escape(str(old_advice or "geen opgeslagen advies")) +
                "</p><p>Nieuw: " + escape(str(new_advice or "geen opgeslagen advies")) +
                "</p></details>"
            )
        rows.append(
            "<tr><th scope='row'>" + escape(project) + "</th><td>" +
            str(len(newly)) + "<br>" + issue_links(newly) + "</td><td>" +
            str(len(absent)) + "<br>" + issue_links(absent) + "</td><td>" +
            str(continued) + "</td><td>" +
            str(len(escalated)) + "<br>" + issue_links(escalated) + "</td><td>" +
            "+" + str(len(new_prs)) + ": " + pr_links(new_prs) +
            "<br>Niet opnieuw waargenomen " + str(len(missing_prs)) + ": " +
            pr_links(missing_prs) + "</td><td>" +
            ("; ".join(escape(value) for value in op_changes) or
             "Geen opgeslagen queue-/advieswijziging") + advice_detail +
            "</td></tr>"
        )
    return (
        "<section class='delta'><h3>" + escape(previous["stem"]) +
        " → " + escape(current["stem"]) + "</h3><p>Nieuwe / niet meer "
        "waargenomen issues, PR-kandidaten en queue-/adviesveranderingen gelden "
        "uitsluitend binnen twee begrensde samples. Niet meer waargenomen betekent "
        "<strong>niet</strong> gesloten, gemerged of eigenaarsvrij. Ontbrekende "
        "queuewaarneming is onbekend, niet nul.</p>" + freshness_html +
        "<table><thead><tr><th>Project</th><th>Nieuw issue</th>"
        "<th>Issue niet opnieuw gezien</th><th>In beide</th>"
        "<th>Urgentie gestegen in sample</th><th>PR-kandidaten gewijzigd</th>"
        "<th>Queue, dekking en vervolgadvies</th></tr></thead><tbody>" +
        ("".join(rows) if rows else
         "<tr><td colspan='7'>Geen projectverschil in de bewaarde issue-, PR- of "
         "queue-/advieswaarnemingen; eventuele bronfreshness staat hierboven.</td></tr>") + "</tbody></table></section>"
    )


def saved_pr_table(snapshot: dict) -> str:
    """Merge-first historical evidence; not an executable release queue."""
    items = snapshot.get("saved_pr_observations", {})
    if not items:
        return ("<p>Geen PR-gatewaarnemingen bewaard. Dit is geen bewijs "
                "dat er geen open PR's zijn.</p>")
    rows = []
    for url, item in sorted(items.items()):
        blockers = ", ".join(item["blocking_unknowns"]) or "geen in sample"
        checks = ("ja, historisch" if item["observed_check_success"]
                  else "nee / niet bewezen")
        legacy = item["legacy_context_success"]
        context = ("onbekend" if legacy is None else
                   "ja, historisch" if legacy else "nee, historisch")
        mergeable = "ja, historisch" if item["mergeable_observed"] else "nee, historisch"
        rows.append(
            "<tr><td><a href='" + escape(url, quote=True) +
            "' target='_blank' rel='noopener noreferrer'>" +
            escape(url.rsplit("/", 1)[-1]) + "</a></td><td><code>" +
            escape(item["head_sha"]) + "</code></td><td>" +
            escape(checks) + "</td><td>" + escape(context) +
            "</td><td>" + escape(mergeable) + "</td><td>" +
            str(item["observed_approvals"]) + "</td><td>" +
            escape(blockers) + "</td></tr>"
        )
    return (
        "<details><summary>Opgeslagen exacte PR-gatewaarnemingen (" +
        str(len(rows)) + "; niet live)</summary><p>Waargenomen checkstatus, "
        "approvals en blocking unknowns zijn historisch. Zelfs een opgeslagen "
        "success is geen huidige verplichte CI-, review- of mergevergunning.</p>"
        "<table><thead><tr><th>PR</th><th>HEAD bij opname</th>"
        "<th>Check bij opname</th><th>Legacy context</th>"
        "<th>Mergeable bij opname</th><th>Approvals bij opname</th>"
        "<th>Onbekende vereisten</th></tr></thead><tbody>" +
        "".join(rows) + "</tbody></table></details>"
    )


def saved_pr_changes(previous: dict, current: dict) -> dict:
    before = previous.get("saved_pr_observations", {})
    after = current.get("saved_pr_observations", {})
    new = sorted(after.keys() - before.keys())
    missing = sorted(before.keys() - after.keys())
    changed = {}
    for url in sorted(before.keys() & after.keys()):
        old, latest = before[url], after[url]
        fields = ("head_sha", "observed_check_success", "legacy_context_success",
                  "observed_approvals", "mergeable_observed", "blocking_unknowns")
        differences = {
            key: {"before": old[key], "after": latest[key]}
            for key in fields if old[key] != latest[key]
        }
        if differences:
            changed[url] = differences
    return {
        "newly_sampled_pr_urls": new,
        "no_longer_sampled_pr_urls": missing,
        "changed_pr_fields": changed,
        "sample_is_current": False,
        "absence_proves_merge_or_closure": False,
    }


def saved_pr_changes_html(previous: dict, current: dict) -> str:
    changes = saved_pr_changes(previous, current)
    new = changes["newly_sampled_pr_urls"]
    missing = changes["no_longer_sampled_pr_urls"]
    changed = changes["changed_pr_fields"]
    def links(urls: list[str]) -> str:
        return (", ".join("<a target='_blank' rel='noopener noreferrer' href='" +
                          escape(url, quote=True) + "'>" +
                          escape(url.rsplit("/", 1)[-1]) + "</a>"
                          for url in urls) if urls else "geen")
    lines = []
    for url, diff in sorted(changed.items()):
        details = ", ".join(
            escape(key) + ": " + escape(str(values["before"])) + " → " +
            escape(str(values["after"])) for key, values in sorted(diff.items())
        )
        lines.append("<li><a href='" + escape(url, quote=True) +
                     "' rel='noopener noreferrer' target='_blank'>" +
                     escape(url.rsplit("/", 1)[-1]) + "</a>: " +
                     details + "</li>")
    return (
        "<details><summary>Opgeslagen PR-gateverandering (" +
        str(len(new)) + " nieuw, " + str(len(missing)) +
        " niet opnieuw gezien, " + str(len(changed)) + " gewijzigd)</summary>"
        "<p>PR-gates zijn slechts in de opgeslagen steekproef vergeleken; "
        "afwezig betekent niet gesloten of gemerged. Voer vóór iedere actie "
        "live exacte-head CI, approvals, claims en branchbescherming na.</p>"
        "<p>Nieuw gezien: " + links(new) + "</p><p>Niet opnieuw gezien: " +
        links(missing) + "</p>" +
        ("<ul>" + "".join(lines) + "</ul>" if lines else "") +
        "</details>"
    )


def saved_attention(snap: dict) -> list[dict]:
    """Sort bounded saved project observations, never allocate or claim work."""
    result = []
    for project, data in snap["projects"].items():
        operations = data.get("operations") or {}
        queue = operations.get("queue_observation")
        expired = queue["expired_active_leases"] if queue is not None else None
        attempts = queue["high_claim_attempts"] if queue is not None else None
        # A truncated card view may omit P0 issues captured in the saved
        # project-level source counts. Never sort only by rendered cards when
        # the admitted source provides a larger bounded issue sample.
        counts = operations.get("issue_counts_in_sample") or data["priorities"]
        result.append({
            "project": project,
            "sampled_p0": counts["P0"], "sampled_p1": counts["P1"],
            "priority_counts_from": (
                "saved_project_source_sample" if operations.get("issue_counts_in_sample")
                else "rendered_issue_cards_only"
            ),
            "sampled_expired_active_leases": expired,
            "sampled_high_claim_attempts": attempts,
            "saved_queue_present": queue is not None,
            "historical_only": True,
            "live_owner_verified": False,
            "needs_live_claim_and_ci_check": True,
        })
    return sorted(
        result,
        key=lambda row: (-row["sampled_p0"], -(row["sampled_expired_active_leases"] or 0),
                         -row["sampled_p1"], -(row["sampled_high_claim_attempts"] or 0),
                         row["project"]),
    )


def historical_merge_attention(snap: dict) -> str:
    """Show all admitted saved PR rows without claiming they are still open."""
    items = snap.get("saved_pr_observations", {})
    if not items:
        return ("<p>Geen opgeslagen exacte-HEAD PR-gatewaarnemingen. "
                "Dit betekent niet dat er geen open PR's zijn.</p>")
    lines = []
    for url, item in sorted(items.items(), key=lambda pair: (
            pair[1]["observed_check_success"],
            -len(pair[1]["blocking_unknowns"]), pair[0])):
        status = ("check geslaagd bij opname" if item["observed_check_success"]
                  else "check niet bewezen bij opname")
        lines.append(
            "<li><a href='" + escape(url, quote=True) +
            "' target='_blank' rel='noopener noreferrer'>" +
            escape(url) + "</a>: <code>" + escape(item["head_sha"][:12]) +
            "</code> · " + status + " · approvals " +
            str(item["observed_approvals"]) + " · onbekende gates " +
            str(len(item["blocking_unknowns"])) + "</li>"
        )
    return ("<h3>Merge-first: opgeslagen PR-gates</h3><p>Maximaal vijf "
            "brongebonden PR-waarnemingen. De opgeslagen waarden zijn "
            "geen actuele merge-vrijgave, geen claim, geen releasebevel. "
            "Controleer huidige HEAD, onafhankelijke review, CI en "
            "branchbescherming opnieuw bij GitHub.</p><ul>" +
            "".join(lines) + "</ul>")



def saved_evidence_freshness_html(snap: dict, now: datetime) -> str:
    """First-screen operator warning based on captured source clocks.

    The board timestamp can be fresh when the GitHub or queue feed is old.
    Display each source separately, preserving originally not-fresh evidence
    and UNKNOWN optional sources without refreshing any external data.
    """
    source_times = snap["source_times"]
    captured_flags = snap["source_fresh"]
    supported = ("inbox", "retry", "claims", "prs", "branches")
    labels = {"inbox": "GitHub-issue-inbox", "retry": "Wachtrij",
              "claims": "Operator-claims", "prs": "Bestaande PR-gates",
              "branches": "PR-loze branches"}
    if (type(source_times) is not dict or
            type(captured_flags) is not dict or
            set(source_times) != set(captured_flags) or
            "inbox" not in source_times or
            not set(source_times) <= set(supported)):
        raise ValueError("historical source clock evidence inconsistent")
    items = []
    stale = []
    for kind in supported:
        if kind not in source_times:
            stale.append(kind + " ontbreekt")
            items.append("<li><strong>" + labels[kind] +
                         "</strong>: niet opgenomen (onbekend)</li>")
            continue
        stamp = datetime.fromisoformat(
            source_times[kind].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("historical source clock has no timezone")
        age = now - stamp.astimezone(timezone.utc)
        minutes = int(age.total_seconds() // 60)
        clock_wrong = age < -timedelta(minutes=2)
        not_fresh = captured_flags[kind] is not True
        outdated = age > timedelta(hours=6)
        state = ("toekomstige klok/onbekend" if clock_wrong else
                 "onvers bij opname" if not_fresh else
                 "ouder dan 6 uur" if outdated else
                 "recent opgenomen, niet live")
        if clock_wrong or not_fresh or outdated:
            stale.append(kind + ": " + state)
        items.append(
            "<li><strong>" + labels[kind] + "</strong>: " + escape(state) +
            " · origineel UTC <code>" + escape(stamp.isoformat()) +
            "</code> · leeftijd " +
            (escape(str(minutes)) + " minuten" if not clock_wrong
             else "onbekend") + "</li>"
        )
    status = ("<strong>Bronnen onvolledig of verouderd.</strong> " +
              str(len(stale)) + " aandachtspunt(en)." if stale else
              "<strong>Bronnen bij opname recent.</strong> " +
              "Blijft historische steekproef, niet live.")
    return ("<section class='evidence-notice' aria-label='Bronactualiteit'>"
            "<h2>Actualiteit van oorspronkelijke bronnen</h2><p>" +
            status + " Boardtijd: <code>" +
            escape(snap["observed"].isoformat()) +
            "</code>. Aanmaaktijd van dit HTML-overzicht zegt niets over "
            "de actualiteit van een bron.</p>"
            "<details open><summary>Bron voor bron (ontbrekend = onbekend)"
            "</summary><ul>" + "".join(items) +
            "</ul></details><p>Hercontroleer echte GitHub-issues, open PR's,"
            " actuele queueclaims en vereiste CI/review voordat je handelt."
            "</p></section>")


def historical_attention_html(snap: dict) -> str:
    """A first-screen handoff drawn only from the most recent saved board."""
    rows = []
    for item in saved_attention(snap):
        expired = item["sampled_expired_active_leases"]
        churn = item["sampled_high_claim_attempts"]
        rows.append(
            "<tr><th scope='row'>" + escape(item["project"]) + "</th><td>" +
            str(item["sampled_p0"]) + " / " + str(item["sampled_p1"]) +
            "</td><td>" + ("onbekend" if expired is None else str(expired)) +
            "</td><td>" + ("onbekend" if churn is None else str(churn)) +
            "</td><td>" + escape(item["priority_counts_from"]) + "</td></tr>"
        )
    return (
        "<section class='delta'><h2>Historische herstelprioriteiten</h2>"
        "<p><strong>Geen live prioritering of opdracht.</strong> De meest recente "
        "bewaarde bordwaarneming " + escape(snap["stem"]) + " van " +
        escape(snap["observed"].isoformat()) + " is gesorteerd op "
        "P0 uit opgeslagen projectsteekproeven (anders issuekaarten), "
        "waargenomen verlopen leases, P1-steekproeven en hoge claimpogingen. "
        "Ontbrekende retrybron wordt nooit nul. Kijk actuele issues, "
        "leases, owners en CI na voordat je iets claimt of vrijgeeft.</p>"
        "<table><thead><tr><th>Project</th><th>Sample P0/P1</th>"
        "<th>Expired leases bij opname</th><th>Hoge claimpogingen</th>"
        "<th>Telbasis</th>"
        "</tr></thead><tbody>" +
        ("".join(rows) if rows else
         "<tr><td colspan='5'>Geen projecten in de opgeslagen steekproef</td></tr>") +
        "</tbody></table>" + historical_merge_attention(snap) + "</section>"
    )


def render(snapshots: list[dict], index_name: str) -> str:
    now = datetime.now(timezone.utc)
    # Explicit order by the saved UTC observation, not the caller's filename.
    snapshots = sorted(snapshots, key=lambda s: (s["observed"], s["stem"]))
    cards = []
    for snap in reversed(snapshots):
        board_age = now - snap["observed"]
        if board_age < -timedelta(minutes=5):
            age_label = "boardtijd >5 min in toekomst (klok onbekend)"
        elif board_age < timedelta(0):
            age_label = "boardtijd iets in toekomst (niet live)"
        elif board_age > timedelta(hours=24):
            age_label = "board ouder dan 24 uur; bronleeftijd hieronder"
        else:
            age_label = "board maximaal 24 uur oud; bronleeftijd hieronder"
        companions = (
            "<a href='" + escape(snap["stem"] + ".desk.html", quote=True) +
            "'>Open interactieve werkbak</a> · <a href='" +
            escape(snap["stem"] + ".receipt.json", quote=True) +
            "'>Open receipt</a>"
        )
        freshness = ", ".join(
            escape(name) + ": " + ("saved-fresh" if fresh else "saved-stale")
            for name, fresh in sorted(snap["source_fresh"].items())
        )
        cards.append(
            "<article><h3>" + escape(snap["stem"]) + "</h3><p><strong>" +
            escape(snap["observed"].isoformat()) +
            "</strong> · " + escape(age_label) + " · " +
            str(snap["card_count"]) + " issuekaarten in sample · " +
            ("sample gekort" if snap["sample_truncated"] else
             "sample niet gekort (bron nog steeds begrensd)") +
            "</p><p>" + companions + "</p>" +
            saved_evidence_freshness_html(snap, now) +
            project_table(snap) + saved_pr_table(snap) +
            "<details><summary>Bronidentiteit en historische bronklokken</summary>"
            "<p>Board SHA-256: <code>" + snap["sha256"] +
            "</code></p><p>" + freshness + "</p></details></article>"
        )
    deltas = [compare(a, b) + saved_pr_changes_html(a, b)
              for a, b in zip(snapshots, snapshots[1:])]
    return (
        "<!doctype html><html lang='nl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='referrer' content='no-referrer'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
        "style-src 'unsafe-inline'\">"
        "<title>zSentinel · Bewaarde herstelbundels</title><style>"
        "body{font:16px/1.6 system-ui;margin:auto;max-width:1050px;"
        "padding:2rem;background:#101827;color:#edf2fb}"
        "article,.delta{border:1px solid #566273;border-radius:12px;"
        "padding:1rem;margin:1rem 0;background:#1a2738}"
        "table{width:100%;border-collapse:collapse;margin:1rem 0}"
        "td,th{border-bottom:1px solid #475367;text-align:left;"
        "vertical-align:top;padding:.6rem;overflow-wrap:anywhere}"
        "a{color:#9ed3ff}code{overflow-wrap:anywhere}"
        ".evidence-notice{border-left:5px solid #f0c36d;"
        "background:#362a1c;padding:.8rem 1rem;margin:1rem 0}"
        ".evidence-notice h2{font-size:1.2rem;margin:.2rem 0}"
        ".evidence-notice li{margin:.2rem 0;overflow-wrap:anywhere}"
        "summary{cursor:pointer}</style></head><body>"
        "<h1>zSentinel · Bewaarde herstelbundels</h1>"
        "<p><strong>Privé offline geschiedenis, geen live monitor.</strong> "
        "Alle bundels zijn lokaal als volledig en byte-identiek geverifieerd. "
        "Een vergelijking bewijst geen gesloten issue, huidige eigenaar, "
        "bruikbare CI, onafhankelijke review, merge of productieherstel.</p>"
        "<p>" + str(len(snapshots)) + " gekoppelde snapshots · index "
        + escape(index_name) + " · gemaakt op " + escape(now.isoformat()) +
        "</p>" + saved_evidence_freshness_html(snapshots[-1], now) +
        historical_attention_html(snapshots[-1]) +
        "<h2>Herstelwerkbanken, nieuwste eerst</h2>" +
        "".join(cards) + "<h2>Verschillen tussen opeenvolgende samples</h2>" +
        ("".join(deltas) if deltas else
         "<p>Minimaal twee snapshots nodig voor verschillen.</p>") +
        "<p>Geen queuewrites, browsernetwerk, claim, CI-trigger, "
        "merge of deploy. Hercontroleer live GitHub en Notion vóór actie.</p>"
        "</body></html>\n"
    )


def render_machine(snapshots: list[dict], index_name: str) -> str:
    """Produce a private machine-readable history without live authority."""
    generated_at = datetime.now(timezone.utc)
    ordered = sorted(snapshots, key=lambda item: (item["observed"], item["stem"]))
    entries = []
    for snap in ordered:
        projects = []
        for project, data in sorted(snap["projects"].items()):
            operations = data.get("operations") or {}
            projects.append({
                "project": project,
                "sampled_issue_count": len(data["issues"]),
                "sampled_priorities": {
                    priority: data["priorities"][priority] for priority in PRIORITIES
                },
                "sampled_issue_ids": sorted(data["issues"]),
                "sampled_pr_candidate_urls": sorted(data["prs"]),
                "saved_project_operations": {
                    "issue_coverage": operations.get("issue_coverage"),
                    "issue_counts_in_sample": operations.get("issue_counts_in_sample"),
                    "queue_observation": operations.get("queue_observation"),
                    "queue_source_observed_at_utc": operations.get("queue_source_observed_at_utc"),
                    "claim_context": operations.get("claim_context", "not_verified"),
                    "recommended_next_step": operations.get("recommended_next_step"),
                },
            })
        entries.append({
            "bundle": snap["stem"],
            "board_sha256": snap["sha256"],
            "saved_board_observed_at_utc": snap["observed"].isoformat(),
            "snapshot_age_seconds_at_index": int(
                (generated_at - snap["observed"]).total_seconds()
            ),
            "future_timestamp_suspected": (
                snap["observed"] > generated_at + timedelta(minutes=5)
            ),
            "saved_individual_source_clocks": snap["source_times"],
            "saved_individual_source_freshness_at_capture": snap["source_fresh"],
            "sampled_cards": snap["card_count"],
            "sample_truncated": snap["sample_truncated"],
            "saved_pr_observations": [
                item for _, item in sorted(snap["saved_pr_observations"].items())
            ],
            "current_inventory_complete_verified": False,
            "projects": projects,
            "historical_attention_order": saved_attention(snap),
        })
    transitions = []
    for old, new in zip(ordered, ordered[1:]):
        projects = []
        for project in sorted(set(old["projects"]) | set(new["projects"])):
            previous = old["projects"].get(project, {})
            current = new["projects"].get(project, {})
            old_issues = previous.get("issues", {})
            new_issues = current.get("issues", {})
            old_prs = previous.get("prs", set())
            new_prs = current.get("prs", set())
            old_priority = previous.get("issue_priorities", {})
            new_priority = current.get("issue_priorities", {})
            raised = sorted(
                issue for issue in old_issues.keys() & new_issues.keys()
                if PRIORITIES.index(new_priority[issue]) <
                   PRIORITIES.index(old_priority[issue])
            )
            old_ops = previous.get("operations") or {}
            new_ops = current.get("operations") or {}
            old_queue = old_ops.get("queue_observation")
            new_queue = new_ops.get("queue_observation")
            old_advice = old_ops.get("recommended_next_step")
            new_advice = new_ops.get("recommended_next_step")
            projects.append({
                "project": project,
                "newly_sampled_issue_ids": sorted(new_issues.keys() - old_issues.keys()),
                "no_longer_sampled_issue_ids": sorted(old_issues.keys() - new_issues.keys()),
                "still_sampled_issue_count": len(old_issues.keys() & new_issues.keys()),
                "higher_sampled_priority_issue_ids": raised,
                "newly_sampled_pr_candidates": sorted(new_prs - old_prs),
                "no_longer_sampled_pr_candidates": sorted(old_prs - new_prs),
                "source_coverage_before": old_ops.get("issue_coverage"),
                "source_coverage_after": new_ops.get("issue_coverage"),
                "queue_observation_before": old_queue,
                "queue_observation_after": new_queue,
                "queue_observation_changed": old_queue != new_queue,
                "queue_source_clock_before": old_ops.get("queue_source_observed_at_utc"),
                "queue_source_clock_after": new_ops.get("queue_source_observed_at_utc"),
                "claim_context_before": old_ops.get("claim_context", "not_verified"),
                "claim_context_after": new_ops.get("claim_context", "not_verified"),
                "recommended_next_step_changed": old_advice != new_advice,
                "recommended_next_step_before": old_advice,
                "recommended_next_step_after": new_advice,
                "current_live_queue_verified": False,
            })
        old_fresh = old.get("source_fresh", {})
        new_fresh = new.get("source_fresh", {})
        transitions.append({
            "from_bundle": old["stem"], "to_bundle": new["stem"],
            "source_freshness_before": old_fresh,
            "source_freshness_after": new_fresh,
            "source_freshness_changes": {
                name: {"before": old_fresh.get(name), "after": new_fresh.get(name)}
                for name in sorted(set(old_fresh) | set(new_fresh))
                if old_fresh.get(name) != new_fresh.get(name)
            },
            "projects": projects,
            "saved_pr_observation_changes": saved_pr_changes(old, new),
            "closure_or_merge_inferred": False,
        })
    report = {
        "schema": "zsentinel-saved-bundle-history-v1",
        "index_name": index_name,
        "generated_at_local_utc": generated_at.isoformat(),
        "saved_bundle_integrity_verified": True,
        "observations_are_historical_and_bounded": True,
        "queue_history_is_historical_and_bounded": True,
        "retry_policy_authorized": False,
        "current_live_source_checked": False,
        "issue_closure_verified": False,
        "current_claims_verified": False,
        "required_ci_verified": False,
        "saved_pr_gate_observations_are_not_current": True,
        "independent_review_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "snapshots": entries,
        "sample_transitions": transitions,
    }
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"



def _history_csv_cell(value):
    """Preserve offline evidence without spreadsheet formula or row injection."""
    import unicodedata

    if value is None:
        return ""
    if type(value) is bool:
        value = "true" if value else "false"
    elif type(value) in (dict, list):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    elif type(value) is not str:
        value = str(value)
    # Historical owner advice is human text: keep each row physically flat.
    value = value.replace("\r", " ").replace("\n", " ").replace("\x00", " ")
    prefix = 0
    while prefix < len(value) and (
        value[prefix].isspace() or
        unicodedata.category(value[prefix]) in ("Cf", "Cc", "Zs", "Zl", "Zp")
    ):
        prefix += 1
    if prefix < len(value) and value[prefix] in "=+-@":
        return "'" + value
    return value


def render_csv_history(snapshots: list[dict], index_name: str, machine_text: str | None = None) -> str:
    """Human spreadsheet timeline: project snapshots plus *sample* deltas.

    Use the exact fully admitted machine-report source model. An empty field
    means not observed; zero is only emitted when a source explicitly said zero.
    An absent issue or PR from a later sample is NOT closure or merge evidence.
    """
    import csv
    from io import StringIO

    report = json.loads(machine_text if machine_text is not None
                        else render_machine(snapshots, index_name))
    columns = (
        "record_kind", "source_bundle", "previous_bundle", "board_sha256",
        "saved_observed_utc", "project", "sampled_p0", "sampled_p1",
        "sampled_issues", "sampled_pr_candidates", "newly_sampled_issues",
        "no_longer_sampled_issues", "raised_sampled_priority_issues",
        "newly_sampled_prs", "no_longer_sampled_prs", "queue_active_before",
        "queue_active_after", "expired_leases_before", "expired_leases_after",
        "high_attempts_after", "queue_source_utc", "issue_coverage",
        "claim_context", "recommended_next_step",
        "source_freshness_at_capture_json", "historical_sample_only",
        "current_claim_verified", "merge_authorized", "live_verified",
    )
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(columns)
    by_bundle = {row["bundle"]: row for row in report["snapshots"]}

    def queue_number(queue, key):
        return queue.get(key) if type(queue) is dict else None

    def emit(**facts):
        writer.writerow([_history_csv_cell(facts.get(name)) for name in columns])

    for snapshot in report["snapshots"]:
        for project in snapshot["projects"]:
            ops = project["saved_project_operations"]
            queue = ops.get("queue_observation")
            emit(
                record_kind="snapshot", source_bundle=snapshot["bundle"],
                board_sha256=snapshot["board_sha256"],
                saved_observed_utc=snapshot["saved_board_observed_at_utc"],
                project=project["project"],
                sampled_p0=project["sampled_priorities"]["P0"],
                sampled_p1=project["sampled_priorities"]["P1"],
                sampled_issues=project["sampled_issue_count"],
                sampled_pr_candidates=len(project["sampled_pr_candidate_urls"]),
                queue_active_after=queue_number(queue, "active_items"),
                expired_leases_after=queue_number(queue, "expired_active_leases"),
                high_attempts_after=queue_number(queue, "high_claim_attempts"),
                queue_source_utc=ops.get("queue_source_observed_at_utc"),
                issue_coverage=ops.get("issue_coverage"),
                claim_context=ops.get("claim_context"),
                recommended_next_step=ops.get("recommended_next_step"),
                source_freshness_at_capture_json=
                    snapshot["saved_individual_source_freshness_at_capture"],
                historical_sample_only=True, current_claim_verified=False,
                merge_authorized=False, live_verified=False,
            )
    for change in report["sample_transitions"]:
        new = by_bundle[change["to_bundle"]]
        for project in change["projects"]:
            old_queue = project.get("queue_observation_before")
            new_queue = project.get("queue_observation_after")
            emit(
                record_kind="sample_change",
                source_bundle=change["to_bundle"],
                previous_bundle=change["from_bundle"],
                board_sha256=new["board_sha256"],
                saved_observed_utc=new["saved_board_observed_at_utc"],
                project=project["project"],
                newly_sampled_issues=len(project["newly_sampled_issue_ids"]),
                no_longer_sampled_issues=len(
                    project["no_longer_sampled_issue_ids"]),
                raised_sampled_priority_issues=len(
                    project["higher_sampled_priority_issue_ids"]),
                newly_sampled_prs=len(project["newly_sampled_pr_candidates"]),
                no_longer_sampled_prs=len(
                    project["no_longer_sampled_pr_candidates"]),
                queue_active_before=queue_number(old_queue, "active_items"),
                queue_active_after=queue_number(new_queue, "active_items"),
                expired_leases_before=queue_number(
                    old_queue, "expired_active_leases"),
                expired_leases_after=queue_number(
                    new_queue, "expired_active_leases"),
                high_attempts_after=queue_number(new_queue, "high_claim_attempts"),
                queue_source_utc=project.get("queue_source_clock_after"),
                issue_coverage=project.get("source_coverage_after"),
                claim_context=project.get("claim_context_after"),
                recommended_next_step=project.get(
                    "recommended_next_step_after"),
                source_freshness_at_capture_json=
                    new["saved_individual_source_freshness_at_capture"],
                historical_sample_only=True, current_claim_verified=False,
                merge_authorized=False, live_verified=False,
            )
    result = "\ufeff" + output.getvalue()
    if len(result.encode("utf-8")) > 1024 * 1024:
        raise ValueError("private historical CSV exceeds one MiB")
    return result





def render_history_pr_csv(snapshots: list[dict], index_name: str,
                          machine_text: str | None = None) -> str:
    """Detailed *historical* merge-first PR rows and observed PR transitions.

    All observations are derived from an existing private source sample. This
    is not a current gate or complete repository PR inventory.
    """
    import csv
    from io import StringIO

    report = json.loads(machine_text if machine_text is not None
                        else render_machine(snapshots, index_name))
    fields = (
        "kind", "source_bundle", "previous_bundle", "observed_at_utc",
        "pr_url", "head_sha_at_capture", "observed_checks_success",
        "observed_legacy_success", "observed_approvals",
        "observed_mergeable", "blocking_unknowns_json",
        "changed_fields_json", "observation_clock_utc",
        "absence_proves_merge", "current_head_verified",
        "current_claim_verified", "merge_authorized", "live_verified",
    )
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(fields)
    snapshots_by_name = {row["bundle"]: row for row in report["snapshots"]}
    for snapshot in report["snapshots"]:
        for pr in snapshot["saved_pr_observations"]:
            data = {
                "kind": "saved_observation",
                "source_bundle": snapshot["bundle"],
                "observed_at_utc": snapshot["saved_board_observed_at_utc"],
                "pr_url": pr["url"], "head_sha_at_capture": pr["head_sha"],
                "observed_checks_success": pr["observed_check_success"],
                "observed_legacy_success": pr["legacy_context_success"],
                "observed_approvals": pr["observed_approvals"],
                "observed_mergeable": pr["mergeable_observed"],
                "blocking_unknowns_json": pr["blocking_unknowns"],
                "observation_clock_utc": pr["source_observed_at_utc"],
                "absence_proves_merge": False,
                "current_head_verified": False, "current_claim_verified": False,
                "merge_authorized": False, "live_verified": False,
            }
            writer.writerow([_history_csv_cell(data.get(key)) for key in fields])
    for transition in report["sample_transitions"]:
        old = snapshots_by_name[transition["from_bundle"]]
        new = snapshots_by_name[transition["to_bundle"]]
        old_rows = {pr["url"]: pr for pr in old["saved_pr_observations"]}
        new_rows = {pr["url"]: pr for pr in new["saved_pr_observations"]}
        changed = transition["saved_pr_observation_changes"]
        kinds = (
            ("newly_sampled", changed["newly_sampled_pr_urls"]),
            ("no_longer_sampled", changed["no_longer_sampled_pr_urls"]),
            ("observed_change", sorted(changed["changed_pr_fields"])),
        )
        for kind, urls in kinds:
            for url in urls:
                row = new_rows.get(url) or old_rows[url]
                data = {
                    "kind": kind,
                    "source_bundle": transition["to_bundle"],
                    "previous_bundle": transition["from_bundle"],
                    "observed_at_utc": new["saved_board_observed_at_utc"],
                    "pr_url": url,
                    "head_sha_at_capture": row["head_sha"],
                    "observed_checks_success": row["observed_check_success"],
                    "observed_legacy_success": row["legacy_context_success"],
                    "observed_approvals": row["observed_approvals"],
                    "observed_mergeable": row["mergeable_observed"],
                    "blocking_unknowns_json": row["blocking_unknowns"],
                    "changed_fields_json": (
                        changed["changed_pr_fields"].get(url) if
                        kind == "observed_change" else None),
                    "observation_clock_utc": row["source_observed_at_utc"],
                    "absence_proves_merge": False,
                    "current_head_verified": False,
                    "current_claim_verified": False,
                    "merge_authorized": False, "live_verified": False,
                }
                writer.writerow([_history_csv_cell(data.get(key)) for key in fields])
    result = "\ufeff" + output.getvalue()
    if len(result.encode("utf-8")) > 1024 * 1024:
        raise ValueError("private historical PR CSV exceeds one MiB")
    return result


def render_history_package(snapshots: list[dict], index_name: str) -> bytes:
    """Portable private HTML/JSON/CSV history plus member checksums.

    All members derive from previously admitted, immutable saved snapshots.
    HTML references to original .desk.html companions require those originals
    beside an extracted index; this archive never fabricates missing sources.
    """
    from io import BytesIO
    from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

    machine = render_machine(snapshots, index_name)
    members = {
        "history.html": render(snapshots, index_name).encode("utf-8"),
        "history.json": machine.encode("utf-8"),
        "history.csv": render_csv_history(
            snapshots, index_name, machine_text=machine).encode("utf-8"),
        "history-prs.csv": render_history_pr_csv(
            snapshots, index_name, machine_text=machine).encode("utf-8"),
        "README.txt": (
            "Private zSentinel historical source sample, NOT a live inventory.\n"
            "Source bundle receipts were verified before this archive was made.\n"
            "Missing issues/PRs do NOT prove closed, merged or free ownership.\n"
            "HTML links to original local desk and receipt require those original\n"
            "private bundle files beside the extracted history.html.\n"
            "No worker claim, CI, review, merge, queue write or deployment authority.\n"
        ).encode("utf-8"),
    }
    if any(not value or len(value) > 1024 * 1024 for value in members.values()):
        raise ValueError("private history package member exceeds one MiB")
    if sum(map(len, members.values())) > 4 * 1024 * 1024:
        raise ValueError("private history package exceeds four-MiB unpacked budget")
    parsed = json.loads(machine)
    receipt = {
        "schema": "zsentinel-private-recovery-history-package-v2",
        "source": "verified_saved_operator_bundles_not_live",
        "generated_at_local_utc": parsed["generated_at_local_utc"],
        "saved_bundle_board_sha256": {
            row["bundle"]: row["board_sha256"] for row in parsed["snapshots"]
        },
        "members_sha256": {
            name: hashlib.sha256(data).hexdigest()
            for name, data in sorted(members.items())
        },
        "historical_sample_only": True,
        "original_companion_files_not_included": True,
        "current_claim_verified": False,
        "required_ci_review_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "live_verified": False,
    }
    members["manifest.json"] = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    out = BytesIO()
    with ZipFile(out, "w", compression=ZIP_DEFLATED,
                 compresslevel=6, allowZip64=False) as package:
        for name, data in members.items():
            info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            info.compress_type = ZIP_DEFLATED
            package.writestr(info, data)
    result = out.getvalue()
    if not result or len(result) > 4 * 1024 * 1024:
        raise ValueError("private historical package exceeds four MiB")
    return result


def write_private_history_package(directory: Path, name: str, data: bytes) -> None:
    """Publish one 0600 history archive atomically inside a trusted 0700 dir."""
    import secrets

    if not INDEX.fullmatch(name) or type(data) is not bytes or not data:
        raise ValueError("invalid private package output")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("private no-follow output unsupported")
    directory_fd = _open_private_directory(directory)
    temporary = ".zsentinel-history-" + secrets.token_hex(16)
    destination = name + ".index.zip"
    fd = None
    try:
        if os.path.lexists(directory / destination):
            raise FileExistsError("private history package already exists")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError("unsafe private archive inode")
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination, src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd, follow_symlinks=False)
        os.fsync(directory_fd)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)



def newest_private_bundles(directory: Path, maximum: int) -> list[str]:
    """Bounded local receipt discovery; never trust a directory entry as a claim."""
    if type(maximum) is not int or not 2 <= maximum <= MAX_BUNDLES:
        raise ValueError("latest bundle count must be between 2 and 24")
    fd = _open_private_directory(directory)
    candidates = []
    try:
        with os.scandir(fd) as entries:
            for index, entry in enumerate(entries):
                if index >= 512:
                    raise ValueError("private directory listing exceeds 512 entries")
                suffix = ".receipt.json"
                if not entry.name.endswith(suffix):
                    continue
                stem = entry.name[:-len(suffix)]
                if STEM.fullmatch(stem) is None:
                    continue
                info = entry.stat(follow_symlinks=False)
                if (not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_nlink != 1):
                    raise BundleInvalid("non-private candidate receipt in directory")
                candidates.append((info.st_mtime_ns, stem))
    finally:
        os.close(fd)
    if len(candidates) < maximum:
        raise ValueError("fewer private bundle receipts than requested")
    # Receipt modification time determines the discovery window, not the
    # historical issue chronology. Every chosen bundle is verified below.
    return [stem for _, stem in sorted(candidates, reverse=True)[:maximum]]


def build_index(directory: Path, stems: list[str], name: str,
                output_format: str = "html") -> dict:
    if type(name) is not str or INDEX.fullmatch(name) is None:
        raise ValueError("invalid private index name")
    if not 2 <= len(stems) <= MAX_BUNDLES or len(set(stems)) != len(stems):
        raise ValueError("supply 2–24 distinct saved bundles")
    if name in stems:
        raise ValueError("index stem must not match a bundled snapshot")
    if any(type(stem) is not str or STEM.fullmatch(stem) is None for stem in stems):
        raise ValueError("invalid source bundle stem")
    if output_format not in ("html", "json", "csv", "prs-csv", "zip"):
        raise ValueError("unsupported private history format")
    snapshots = [load_snapshot(directory, stem) for stem in stems]
    filename = name + ".index." + ("prs.csv" if output_format == "prs-csv"
                                   else output_format)
    if output_format == "zip":
        write_private_history_package(
            directory, name, render_history_package(snapshots, name))
    else:
        payload = (render(snapshots, name) if output_format == "html"
                   else render_csv_history(snapshots, name) if output_format == "csv"
                   else render_history_pr_csv(snapshots, name)
                   if output_format == "prs-csv"
                   else render_machine(snapshots, name))
        write_create_only(directory / filename, payload)
    return {
        "file": filename, "snapshots_indexed": len(snapshots),
        "private_offline_only": True,
        "saved_bundle_integrity_verified": True,
        "issue_closure_verified": False,
        "live_status_verified": False,
        "claim_authorized": False, "merge_authorized": False,
        "deploy_authorized": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True,
                        help="Existing user-owned 0700 directory with saved bundles")
    parser.add_argument("--bundle", action="append", default=[],
                        help="Saved bundle stem; repeat 2–24 times")
    parser.add_argument("--latest", type=int,
                        help="Discover newest 2–24 private receipts (mtime only; not live)")
    parser.add_argument("--name", required=True,
                        help="New create-only index stem")
    parser.add_argument("--format",
                        choices=("html", "json", "csv", "prs-csv", "zip"),
                        default="html",
                        help="Private HTML, JSON, project CSV, PR CSV or checksummed ZIP")
    args = parser.parse_args(argv)
    try:
        if args.latest is not None and args.bundle:
            raise ValueError("choose explicit --bundle or --latest, not both")
        stems = (newest_private_bundles(args.directory, args.latest)
                 if args.latest is not None else args.bundle)
        result = build_index(args.directory, stems, args.name, args.format)
    except (OSError, BundleInvalid, ValueError, TypeError, KeyError) as exc:
        print("zSentinel offline history refused: " +
              (str(exc) if isinstance(exc, (BundleInvalid, ValueError))
               else "private bundle read/write unavailable"), file=sys.stderr)
        return 2
    import json
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
