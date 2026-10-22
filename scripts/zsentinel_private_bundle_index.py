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
    return {
        "stem": stem, "observed": observed,
        "source_times": board["source_times_utc"],
        "source_fresh": board["fresh"],
        "sha256": proof["board_sha256"],
        "projects": projects,
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
            "</p></details>"
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
         "<tr><td colspan='7'>Geen verschil in de bewaarde issue-, PR- of "
         "queue-/advieswaarnemingen</td></tr>") + "</tbody></table></section>"
    )


def render(snapshots: list[dict], index_name: str) -> str:
    now = datetime.now(timezone.utc)
    # Explicit order by the saved UTC observation, not the caller's filename.
    snapshots = sorted(snapshots, key=lambda s: (s["observed"], s["stem"]))
    cards = []
    for snap in reversed(snapshots):
        age = now - snap["observed"]
        if age < -timedelta(minutes=5):
            age_label = "waarneming >5 min in toekomst (mogelijke klokdrift; niet live)"
        elif age < timedelta(0):
            age_label = "tijdstempel iets in toekomst (niet live)"
        elif age > timedelta(hours=24):
            age_label = "ouder dan 24 uur"
        else:
            age_label = "maximaal 24 uur oud (niet live)"
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
            "</p><p>" + companions + "</p>" + project_table(snap) +
            "<details><summary>Bronidentiteit en historische bronklokken</summary>"
            "<p>Board SHA-256: <code>" + snap["sha256"] +
            "</code></p><p>" + freshness + "</p></details></article>"
        )
    deltas = [compare(a, b) for a, b in zip(snapshots, snapshots[1:])]
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
        "summary{cursor:pointer}</style></head><body>"
        "<h1>zSentinel · Bewaarde herstelbundels</h1>"
        "<p><strong>Privé offline geschiedenis, geen live monitor.</strong> "
        "Alle bundels zijn lokaal als volledig en byte-identiek geverifieerd. "
        "Een vergelijking bewijst geen gesloten issue, huidige eigenaar, "
        "bruikbare CI, onafhankelijke review, merge of productieherstel.</p>"
        "<p>" + str(len(snapshots)) + " gekoppelde snapshots · index "
        + escape(index_name) + " · gemaakt op " + escape(now.isoformat()) +
        "</p><h2>Herstelwerkbanken, nieuwste eerst</h2>" +
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
            "current_inventory_complete_verified": False,
            "projects": projects,
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
            "projects": projects, "closure_or_merge_inferred": False,
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
        "independent_review_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "snapshots": entries,
        "sample_transitions": transitions,
    }
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


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
    if output_format not in ("html", "json"):
        raise ValueError("unsupported private history format")
    snapshots = [load_snapshot(directory, stem) for stem in stems]
    payload = (render(snapshots, name) if output_format == "html"
               else render_machine(snapshots, name))
    filename = name + ".index." + output_format
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
    parser.add_argument("--format", choices=("html", "json"), default="html",
                        help="Script-free offline HTML or non-authorizing machine JSON")
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
