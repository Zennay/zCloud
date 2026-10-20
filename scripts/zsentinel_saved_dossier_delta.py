#!/usr/bin/env python3
"""Human-readable change view between two saved zSentinel project dossiers.

Both inputs are bounded offline snapshots, never a live ownership, CI,
review, merge, incident-recovery or deployment source. No network or claims.
"""
from __future__ import annotations

import argparse
import csv
from io import StringIO
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import sys
from zipfile import BadZipFile

from zsentinel_saved_gate_dossier import open_dossier, saved_queue

MAX_BYTES = 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}\Z")
HEAD = re.compile(r"[0-9a-f]{40}\Z")
ALLOWED = ("new_in_sample", "left_sample", "changed", "same_in_sample")
PRIORITIES = ("P0", "P1", "P2", "Unspecified")
LABELS = {
    "new_in_sample": "Nieuw in de tweede bronsteekproef",
    "left_sample": "Niet meer in tweede bronsteekproef; NIET bewezen gesloten",
    "changed": "Waargenomen broninformatie veranderd",
    "same_in_sample": "Zelfde opgeslagen bronkenmerken",
}
REASONS = {
    "observed_head": "waargenomen PR-HEAD",
    "gate_source": "gatebron/ontbrekende batch",
    "primary_gate": "primaire waargenomen blokkade",
    "concurrent_gates": "gelijktijdige waargenomen blokkades",
    "priority": "bronprioriteit",
    "linked_issue_urls": "gekoppelde bronissues",
    "next_action": "menselijke vervolgstap",
    "gate_observations": "begrensde CI-/reviewwaarnemingen",
}
COUNTER_KEYS = (
    "checks_completed", "checks_pending", "checks_not_successful",
    "independent_approvals_observed", "changes_requested_observed",
    "legacy_pending_observed", "legacy_failed_observed",
)
BOOLEAN_KEYS = (
    "checks_page_complete", "reviews_page_may_be_truncated",
    "legacy_page_may_be_truncated",
)
FIELDS = tuple(REASONS)


def source_time(value):
    if type(value) is not str or len(value) > 48:
        raise ValueError("Missing bounded source UTC timestamp")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("Source timestamp must be explicit UTC")
    if stamp > datetime.now(timezone.utc):
        raise ValueError("Future source cannot prove a chronological change")
    return stamp


def admit_record(report):
    """Use only source-admitted repo/PR metadata from the existing ZIP reader."""
    _files, manifest, receipt = report
    before = source_time(manifest.get("earliest_observed_at_utc"))
    after = source_time(manifest.get("latest_observed_at_utc"))
    if before > after or (after - before).total_seconds() > 600:
        raise ValueError("Dossier observation interval is inconsistent")
    rows = {}
    for repo, candidate in saved_queue(receipt):
        url = candidate["url"]
        fields = {}
        for name in FIELDS:
            field = "head_observed" if name == "observed_head" else name
            value = candidate.get(field)
            if name == "observed_head":
                if value is not None and (type(value) is not str or HEAD.fullmatch(value) is None):
                    raise ValueError("Invalid PR HEAD observation")
            elif name in ("linked_issue_urls", "concurrent_gates"):
                if type(value) is not list or len(value) > 60 or len(set(value)) != len(value):
                    raise ValueError("Contradictory saved PR link/gate evidence")
                value = sorted(value)
            elif name == "gate_observations" and value is not None:
                if (type(value) is not dict
                        or set(value) != set(COUNTER_KEYS + BOOLEAN_KEYS)
                        or any(type(value[k]) is not int or not 0 <= value[k] <= 100
                               for k in COUNTER_KEYS)
                        or any(type(value[k]) is not bool for k in BOOLEAN_KEYS)
                        or candidate["gate_source"] != "observed_partial_gate_receipt"):
                    raise ValueError("Untrusted saved gate-counter observations")
            fields[name] = value
        rows[url] = {"repository": repo, "number": candidate["pr_number"],
                     "data": fields}
    if not rows or len(rows) != receipt["filtered_pr_candidates"]:
        raise ValueError("Incomplete offline PR snapshot")
    return {"scope": manifest.get("selected_project_filter"),
            "manifest_sha256": manifest["original_review_manifest_sha256"],
            "board_sha256": manifest["original_board_sha256"],
            "earliest": before, "latest": after,
            "missing_batch_indexes": manifest["missing_batch_indexes"],
            "original_source_count": receipt["original_source_pr_candidates"],
            "sample_count": len(rows), "rows": rows}


def compare(before, after):
    """Compare two complete saved ZIP sources, no implied GitHub live truth."""
    old, new = admit_record(before), admit_record(after)
    if old["scope"] != new["scope"]:
        raise ValueError("Project scope differs; select matching source scopes")
    if old["latest"] >= new["earliest"]:
        raise ValueError("Dossiers overlap or are not chronologically ordered")
    all_urls = set(old["rows"]) | set(new["rows"])
    if len(all_urls) > 600:
        raise ValueError("Combined PR source inventory too large")
    changed = []
    counts = {kind: 0 for kind in ALLOWED}
    priority_order = {item: n for n, item in enumerate(PRIORITIES)}
    category_order = {item: n for n, item in enumerate(ALLOWED)}
    for url in all_urls:
        a, b = old["rows"].get(url), new["rows"].get(url)
        if a is None:
            category = "new_in_sample"
            reasons = ["source_inventory"]
        elif b is None:
            category = "left_sample"
            reasons = ["source_inventory"]
        else:
            reasons = [key for key in FIELDS if a["data"][key] != b["data"][key]]
            category = "changed" if reasons else "same_in_sample"
        counts[category] += 1
        row = b or a
        priority = row["data"]["priority"]
        if priority not in priority_order:
            raise ValueError("Invalid PR priority from original source")
        changed.append({
            "url": url, "repository": row["repository"], "number": row["number"],
            "priority": priority, "category": category,
            "changed_fields": reasons,
            "before": a["data"] if a else None,
            "after": b["data"] if b else None,
            "closed_or_merged_verified": False,
            "live_status_verified": False,
            "human_follow_up": (
                "Inspecteer de originele PR live; deze PR is alleen nieuw in de steekproef."
                if category == "new_in_sample" else
                "Controleer PR live; verdwijnen uit een steekproef is NOOIT bewijs van merge/sluiting."
                if category == "left_sample" else
                "Inspecteer actuele PR-HEAD, verplichte gates, claims en oorspronkelijke issues."
                if category == "changed" else
                "Geen steekproefwijziging; actuele GitHub- en eigenaarsstatus blijft onbekend."
            ),
        })
    changed.sort(key=lambda x: (
        priority_order[x["priority"]], category_order[x["category"]],
        x["repository"], x["number"]))
    return {
        "schema": "zsentinel-private-saved-dossier-delta-v1",
        "source": "two_complete_saved_zip_observations_not_live",
        "scope": new["scope"],
        "before": {
            "review_manifest_sha256": old["manifest_sha256"],
            "original_board_sha256": old["board_sha256"],
            "earliest_observed_utc": old["earliest"].isoformat(),
            "latest_observed_utc": old["latest"].isoformat(),
            "original_source_candidates": old["original_source_count"],
            "sample_candidates": old["sample_count"],
            "missing_batch_indexes": old["missing_batch_indexes"],
        },
        "after": {
            "review_manifest_sha256": new["manifest_sha256"],
            "original_board_sha256": new["board_sha256"],
            "earliest_observed_utc": new["earliest"].isoformat(),
            "latest_observed_utc": new["latest"].isoformat(),
            "original_source_candidates": new["original_source_count"],
            "sample_candidates": new["sample_count"],
            "missing_batch_indexes": new["missing_batch_indexes"],
        },
        "same_original_manifest": old["manifest_sha256"] == new["manifest_sha256"],
        "same_original_board": old["board_sha256"] == new["board_sha256"],
        "source_windows_may_be_partial": True,
        "observed_transition_counts": counts,
        "items": changed,
        "owner_claim_verified": False,
        "required_ci_or_review_verified": False,
        "merged_on_main_verified": False,
        "live_verified": False,
        "worker_dispatch_authorized": False,
        "merge_authorized": False,
        "writes_performed_to_external_systems": False,
    }


def markdown(result):
    rows = [
        "# zSentinel — veranderingen tussen twee privéprojectdossiers", "",
        "> Beide volledige ZIP-bestanden zijn alleen opgeslagen steekproeven, niet "
        "de actuele GitHub/Notion-waarheid. Geen CI-, review- of mergevrijgave.",
        "",
        "- Scope: `" + (result["scope"] or "alle projecten in bron") + "`",
        "- Oude bron UTC: " + result["before"]["latest_observed_utc"],
        "- Nieuwe bron UTC: " + result["after"]["earliest_observed_utc"],
        "- Zelfde bronmanifest: " + ("JA" if result["same_original_manifest"] else
                                  "NEE — zoekscope kan verschillen"),
        "- Zelfde origineel board: " + ("JA" if result["same_original_board"] else
                                    "NEE — source coverage kan verschillen"),
        "- Oude PR's in sample: " + str(result["before"]["sample_candidates"]) +
          ", nieuwe: " + str(result["after"]["sample_candidates"]),
        "- Nieuwe/weggevallen bronkandidaten zijn **NIET** automatisch gemaakt,"
          " opgelost of gemerged.", "",
    ]
    for group in ALLOWED:
        entries = [i for i in result["items"] if i["category"] == group]
        rows += ["## " + LABELS[group] + " (" + str(len(entries)) + ")", ""]
        for item in entries:
            if group == "same_in_sample":
                continue
            rows.append("- **" + item["priority"] + "** [" +
                        item["repository"] + "#" + str(item["number"]) +
                        "](" + item["url"] + "): " +
                        (", ".join(REASONS[x] for x in item["changed_fields"]
                                   if x in REASONS) or LABELS[group]) +
                        ". " + item["human_follow_up"])
        if not entries:
            rows.append("Geen verschil in de twee steekproeven waargenomen.")
        rows.append("")
    rows += ["**Verplicht voordat iets echt geleverd is:** actuele eigenaar en "
             "claim, exacte GitHub HEAD, vereiste CI, onafhankelijke review,"
             " branchregels, merge-commit op main en aparte live-verificatie.",
             "Deze overdracht wijzigt niets buiten één lokaal privébestand.", ""]
    return "\n".join(rows)



def csv_handoff(result):
    """One spreadsheet-safe observational row per saved PR."""
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", quotechar='"',
                        lineterminator="\\r\\n")
    writer.writerow([
        "Vorige bronmanifest SHA256", "Nieuwe bronmanifest SHA256",
        "Vorig bronmoment UTC", "Nieuw bronmoment UTC",
        "Bronproject", "Prioriteit bron", "PR URL", "Veranderklasse",
        "Waargenomen verschillen", "PR HEAD vorige bron", "PR HEAD nieuwe bron",
        "Gatebron vorige bron", "Gatebron nieuwe bron",
        "Gateklasse vorige bron", "Gateklasse nieuwe bron",
        "Menselijke vervolgactie", "Actuele claim/CI/review bevestigd",
        "Gemerged op main bewezen", "Live bewezen",
    ])
    for item in result["items"]:
        old = item["before"] or {}
        new = item["after"] or {}
        writer.writerow([
            result["before"]["review_manifest_sha256"],
            result["after"]["review_manifest_sha256"],
            result["before"]["latest_observed_utc"],
            result["after"]["earliest_observed_utc"],
            item["repository"], item["priority"], item["url"],
            item["category"],
            " | ".join(REASONS[k] for k in item["changed_fields"]
                       if k in REASONS) or "STEEKPROEF_VERANDERD",
            old.get("observed_head") or "ONBEKEND",
            new.get("observed_head") or "ONBEKEND",
            old.get("gate_source") or "ONBEKEND",
            new.get("gate_source") or "ONBEKEND",
            old.get("primary_gate") or "ONBEKEND",
            new.get("primary_gate") or "ONBEKEND",
            item["human_follow_up"],
            "ONBEKEND", "ONBEKEND", "ONBEKEND",
        ])
    return "\\ufeff" + output.getvalue()

def render(result, fmt):
    if fmt == "json":
        body = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    elif fmt == "markdown":
        body = markdown(result)
    elif fmt == "csv":
        body = csv_handoff(result)
    else:
        e = lambda x: escape(str(x), quote=True)
        source = result["before"]
        target = result["after"]
        panels = []
        for group in ALLOWED:
            group_rows = [item for item in result["items"] if item["category"] == group]
            items = []
            for item in group_rows[:300]:
                notes = ", ".join(REASONS[x] for x in item["changed_fields"] if x in REASONS)
                items.append('<li><a target="_blank" rel="noopener noreferrer" href="' +
                             e(item["url"]) + '">' + e(item["priority"]) + " " +
                             e(item["repository"]) + "#" + str(item["number"]) +
                             "</a> — " + e(notes or LABELS[group]) + ". " +
                             e(item["human_follow_up"]) + "</li>")
            panels.append("<section><details" + (" open" if group != "same_in_sample"
                                                   else "") + "><summary>" +
                          e(LABELS[group]) + " (" + str(len(group_rows)) +
                          ")</summary><ol>" + "".join(items) +
                          "</ol></details></section>")
        body = ("""<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'">
<title>zSentinel · opgeslagen PR-veranderingen</title>
<style>:root{font-family:system-ui;color-scheme:dark}
body{margin:0;background:#101d2e;color:#f1f7ff;line-height:1.55}
main{max-width:1050px;margin:auto;padding:clamp(16px,4vw,42px)}
aside,section{border:1px solid #60758d;border-radius:12px;padding:16px;margin:15px 0;background:#1c3048}
aside{border-color:#e5bb77}summary{cursor:pointer;font-weight:700;padding:8px}
a{color:#a1d9ff}a:focus-visible,summary:focus-visible{outline:3px solid #f4c86c}
li{margin:10px 0}code{overflow-wrap:anywhere}small{color:#d7e6f2}
@media print{body,aside,section{background:white;color:black}a{color:#075080}}
</style></head><body><main><h1>PR-bronverandering</h1>
<aside><strong>Niet live, niet gemerged.</strong> Dit is uitsluitend een vergelijking
tussen twee opgeslagen privébronmomenten. Een verdwenen PR is niet bewezen
gemerged of gesloten. Geen bestaande CI/review/claim/security-gate vervangen.</aside><p>
Oud: """ + e(source["latest_observed_utc"]) + " · nieuw: " +
e(target["earliest_observed_utc"]) + """<br>Manifest gelijk: """ +
("ja" if result["same_original_manifest"] else "nee (verschillende bronscope mogelijk)") +
"""<br>Board gelijk: """ + ("ja" if result["same_original_board"] else
                             "nee (verschillende coverage mogelijk)") +
"""<br>Oorspronkelijke bron-SHA's: <code>""" +
e(source["review_manifest_sha256"]) + "</code> / <code>" +
e(target["review_manifest_sha256"]) + "</code></p>" +
"".join(panels) + """<footer><small>Privé, scriptvrije offline weergave.
Verifieer huidige PR-HEAD, review, CI, eigenaar en echte main-SHA in GitHub.</small>
</footer></main></body></html>""")
    data = body.encode("utf-8")
    if not 1 <= len(data) <= MAX_BYTES:
        raise ValueError("Private saved-dossier comparison exceeds one MiB")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True,
                        help="Previously saved private project ZIP")
    parser.add_argument("--after", type=Path, required=True,
                        help="Later saved private project ZIP, same project selection")
    parser.add_argument("--format", choices=("html", "markdown", "json", "csv"),
                        default="html")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.before == args.after:
            raise ValueError("Comparison inputs must be separate private dossiers")
        result = compare(open_dossier(args.before), open_dossier(args.after))
        data = render(result, args.format)
        # Existing sources are immutable. Only a new, private, non-overwriting
        # output file is allowed, after both inputs are fully validated.
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
        groups = result["observed_transition_counts"]
        print("Private offline PR source delta created. New in sample: " +
              str(groups["new_in_sample"]) + "; changed: " +
              str(groups["changed"]) + "; left sample (NOT resolved): " +
              str(groups["left_sample"]) + ". No merge or live status inferred.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError,
            OverflowError, BadZipFile) as exc:
        print("Refused incomplete, unordered or invalid private source: " +
              type(exc).__name__, file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
