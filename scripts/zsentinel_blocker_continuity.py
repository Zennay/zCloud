#!/usr/bin/env python3
"""zSentinel blocker continuity desk: compare two private, bounded operator snapshots.

No network, runner, queue, claim, release or GitHub writes. An absent blocker
from a later sampled report is NEVER evidence of recovery or issue closure.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from html import escape
from pathlib import Path

SCHEMA = "zsentinel-blocker-first-board-v1"
OUTPUT_SCHEMA = "zsentinel-blocker-continuity-v1"
MAX_BYTES = 6 * 1024 * 1024
MAX_CARDS = 12
KINDS = {
    "verified_source_incident_not_runtime_recovery",
    "open_owner_only_issue_not_current_outage_proof",
    "read_only_worker_progress_gap_not_proven_outage",
    "queue_pressure_not_proven_outage",
    "existing_product_pr_gate_inspection_only",
    "historical_not_reconciled",
}
IDENT = re.compile(r"^(?:[0-9a-f]{32}|pr:[a-z0-9_-]{1,40}:[1-9][0-9]{0,8}|owner-issue:[a-z0-9_-]{1,40}:[1-9][0-9]{0,8}|worker:[a-z0-9_-]{1,40}:(?:[1-9]|1[0-6])|queue:[a-z0-9_-]{1,40})$")
PROJECT = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
URL = re.compile(r"^https://github[.]com/Zennay/[A-Za-z0-9_.-]{1,100}/(?:pull/[1-9][0-9]{0,8}|issues/[1-9][0-9]{0,8}|actions/runs/[1-9][0-9]{0,15}/job/[1-9][0-9]{0,15})$")
PRIORITY = {"P0": 0, "P1": 1}


def _duplicate_guard(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _read(path: Path):
    if path.is_symlink():
        raise ValueError("refuse source symlink")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            raise ValueError("source must be a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as src:
            raw = src.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("source exceeds limit")
    finally:
        os.close(fd)
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_guard)


def _plain(value, limit):
    if (type(value) is not str or not 1 <= len(value) <= limit
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("unsafe source string")
    return value


def _cards(board):
    if (type(board) is not dict or board.get("schema") != SCHEMA
            or any(board.get(k) is not False for k in (
                "worker_resumed_verified", "claims_taken", "retries_performed",
                "deployment_authorized", "writes_performed",
                "known_complete_blocker_inventory"))
            or type(board.get("blocker_cards_truncated")) is not bool):
        raise ValueError("untrusted or authorizing source board")
    items = board.get("shown_blocker_cards")
    if type(items) is not list or len(items) > MAX_CARDS:
        raise ValueError("unbounded blocker cards")
    mapped = {}
    for row in items:
        if type(row) is not dict:
            raise ValueError("invalid blocker card")
        key = _plain(row.get("id"), 120)
        if not IDENT.fullmatch(key) or key in mapped:
            raise ValueError("invalid or duplicate blocker identity")
        pid = _plain(row.get("project_id"), 40)
        if not PROJECT.fullmatch(pid):
            raise ValueError("invalid project")
        kind = row.get("kind")
        if kind not in KINDS or row.get("priority") not in PRIORITY:
            raise ValueError("invalid blocker classification")
        # Canonical identity must describe the same project and class.
        if kind == "existing_product_pr_gate_inspection_only" and not key.startswith("pr:" + pid + ":"):
            raise ValueError("PR identity does not match project")
        if kind == "open_owner_only_issue_not_current_outage_proof" and not key.startswith("owner-issue:" + pid + ":"):
            raise ValueError("issue identity does not match project")
        if kind == "read_only_worker_progress_gap_not_proven_outage" and not key.startswith("worker:" + pid + ":"):
            raise ValueError("worker identity does not match project")
        if kind == "queue_pressure_not_proven_outage" and key != "queue:" + pid:
            raise ValueError("queue identity does not match project")
        if kind in ("historical_not_reconciled", "verified_source_incident_not_runtime_recovery") and not re.fullmatch(r"[0-9a-f]{32}", key):
            raise ValueError("incident identity does not match class")
        urls = []
        for field in ("source_owner_issue_url", "source_pr_url", "source_job_url_historical_only"):
            link = row.get(field)
            if link is not None:
                if type(link) is not str or not URL.fullmatch(link):
                    raise ValueError("untrusted source link")
                urls.append(link)
        sha = row.get("source_pr_head")
        if sha is not None and (type(sha) is not str or not re.fullmatch(r"[0-9a-f]{40}", sha)):
            raise ValueError("invalid exact PR head SHA")
        checks = row.get("observed_check_gates", [])
        if type(checks) is not list or len(checks) > 8:
            raise ValueError("unbounded check observations")
        checks = [_plain(value, 190) for value in checks]
        for permission in ("worker_resumed_verified", "permission_to_recover", "owner_claim_verified"):
            if row.get(permission) is not False:
                raise ValueError("source unexpectedly asserts authority or recovery")
        mapped[key] = {
            "id": key, "project_id": pid, "priority": row["priority"],
            "kind": kind,
            "problem": _plain(row.get("problem"), 600),
            "action": _plain(row.get("action"), 600),
            "owner": _plain(row.get("owner"), 160),
            "next_gate": _plain(row.get("next_gate"), 120),
            "links": urls,
            "source_pr_head": sha,
            "observed_check_gates": checks,
        }
    return mapped


def _unwrap(snapshot):
    """Accept the canonical one-command portfolio JSON or a saved board itself."""
    if type(snapshot) is not dict:
        raise ValueError("invalid snapshot")
    if "blocker_first_handoff" not in snapshot:
        return snapshot
    portfolio = snapshot.get("portfolio")
    if (type(portfolio) is not dict
            or portfolio.get("schema") != "zsentinel-portfolio-overview-v1"
            or portfolio.get("actions_performed") is not False
            or portfolio.get("queue_owner_authorized") is not False):
        raise ValueError("untrusted portfolio snapshot")
    return snapshot["blocker_first_handoff"]


def _window(snapshot):
    """A source window is a sampled slice, never a complete issue inventory."""
    if type(snapshot) is not dict or "blocker_first_handoff" not in snapshot:
        return None
    selected = snapshot.get("selected_projects")
    if selected is None:
        return None
    if (type(selected) is not list or not 1 <= len(selected) <= 3
            or len(selected) != len(set(selected))
            or any(type(pid) is not str or not PROJECT.fullmatch(pid)
                   for pid in selected)):
        raise ValueError("invalid selected GitHub window")
    return sorted(selected)


def compare(before, after):
    before_window, after_window = _window(before), _window(after)
    before, after = _unwrap(before), _unwrap(after)
    old, new = _cards(before), _cards(after)
    current, first_seen, not_reobserved, changed = [], [], [], []
    for key in sorted(new, key=lambda k: (
            PRIORITY[new[k]["priority"]], new[k]["project_id"], k)):
        item = new[key]
        if key not in old:
            first_seen.append(item)
            continue
        earlier = old[key]
        if earlier["project_id"] != item["project_id"]:
            raise ValueError("cross-project incident identity drift")
        current.append(item)
        fields = ("kind", "priority", "problem", "action", "owner",
                  "next_gate", "source_pr_head", "observed_check_gates")
        altered = [field for field in fields if item[field] != earlier[field]]
        if altered:
            changed.append({"id": key, "project_id": item["project_id"],
                            "priority": item["priority"], "changed_fields": altered,
                            "previous": earlier, "current": item})
    for key in sorted(set(old) - set(new), key=lambda k: (
            PRIORITY[old[k]["priority"]], old[k]["project_id"], k)):
        not_reobserved.append(old[key])
    return {
        "schema": OUTPUT_SCHEMA,
        "before_sampled_cards": len(old),
        "after_sampled_cards": len(new),
        "before_truncated": before["blocker_cards_truncated"],
        "after_truncated": after["blocker_cards_truncated"],
        "before_sampled_projects": before_window,
        "after_sampled_projects": after_window,
        "same_project_window_verified": (
            before_window == after_window
            if before_window is not None and after_window is not None else False),
        "window_mismatch_or_unknown": (
            before_window is None or after_window is None
            or before_window != after_window),
        "still_observed": current,
        "newly_observed": first_seen,
        "changed_source_details": changed,
        "not_reobserved_not_resolved": not_reobserved,
        "resolved_blockers_verified": 0,
        "worker_resumed_verified": False,
        "source_recency_verified": False,
        "complete_inventory_verified": False,
        "owner_claim_verified": False,
        "claims_taken": False,
        "permission_to_retry": False,
        "permission_to_merge": False,
        "actions_performed": False,
        "next_action": ("inspect_current_P0_and_owner_gates"
                        if any(x["priority"] == "P0" for x in current + first_seen)
                        else "reconcile_old_unobserved_sources_then_inspect_available_product_work"),
    }


def compare_history(snapshots):
    """Bounded, ordered 2-8 snapshot incident recurrence; never runtime proof."""
    if type(snapshots) is not list or not 2 <= len(snapshots) <= 8:
        raise ValueError("two to eight snapshots required")
    by_window = []
    windows = []
    for source in snapshots:
        windows.append(_window(source))
        by_window.append(_cards(_unwrap(source)))
    base = compare(snapshots[-2], snapshots[-1])
    seen = set().union(*(set(items) for items in by_window))
    history = []
    for key in seen:
        sightings = [i for i, items in enumerate(by_window) if key in items]
        item = by_window[sightings[-1]][key]
        if any(by_window[i][key]["project_id"] != item["project_id"] for i in sightings):
            raise ValueError("cross-project incident identity drift")
        streak = 0
        for items in reversed(by_window):
            if key not in items:
                break
            streak += 1
        history.append({
            "id": key, "project_id": item["project_id"],
            "priority": item["priority"], "kind": item["kind"],
            "sightings_in_sample": len(sightings),
            "consecutive_recent_samples": streak,
            "first_sample_index": sightings[0] + 1,
            "last_sample_index": sightings[-1] + 1,
            "current_sample_observed": sightings[-1] == len(by_window) - 1,
            "reappeared_after_gap": (sightings[-1] == len(by_window) - 1 and
                any(b - a > 1 for a, b in zip(sightings, sightings[1:]))),
            "next_gate": item["next_gate"], "owner": item["owner"],
            "links": item["links"], "problem": item["problem"],
            "worker_resumed_verified": False,
            "resolution_verified": False, "claims_taken": False,
            "permission_to_retry": False,
        })
    history.sort(key=lambda row: (
        not row["current_sample_observed"], PRIORITY[row["priority"]],
        -row["consecutive_recent_samples"], -row["sightings_in_sample"],
        row["project_id"], row["id"]))
    urgent = sum(1 for item in history if (
        item["priority"] == "P0" and item["consecutive_recent_samples"] >= 3))
    return {
        **base,
        "schema": "zsentinel-blocker-continuity-history-v1",
        "history_sample_count": len(snapshots),
        "history_project_windows": windows,
        "history_windows_different_or_unknown": (
            any(w is None for w in windows)
            or any(w != windows[0] for w in windows[1:])),
        "history_items": history[:96],
        "history_items_truncated": len(history) > 96,
        "history_unique_items": len(history),
        "repeated_p0_human_attention_candidates": urgent,
        "notifications_delivered": False,
        "resolution_verified": False,
        "worker_resumed_verified": False,
        "claims_taken": False,
        "actions_performed": False,
    }


def as_text(result):
    lines = [
        "zSentinel · bronvergelijking van twee begrensde blockerborden",
        "LET OP: niet opnieuw gezien != opgelost; geen actuele leases of hervatte workers bewezen.",
        ("LET OP: venster verschilt of is ONBEKEND; afwezigheid kan door brondekking komen."
         if result["window_mismatch_or_unknown"] else
         "Beide rapporten hebben hetzelfde geselecteerde projectvenster (niet volledig)."),
        "Huidig " + str(result["after_sampled_cards"]) +
        " | nieuw " + str(len(result["newly_observed"])) +
        " | nog gezien " + str(len(result["still_observed"])) +
        " | veranderd " + str(len(result["changed_source_details"])) +
        " | niet teruggezien (ONBEKEND) " + str(len(result["not_reobserved_not_resolved"])),
    ]
    for title, field in (
        ("EERST NIEUWE BLOKKADES", "newly_observed"),
        ("NOG WAARGENOMEN BLOKKADES", "still_observed"),
        ("NIET TERUGGEZIEN · NIET OPGELOST", "not_reobserved_not_resolved"),
    ):
        lines.append(title)
        for row in result[field]:
            lines.append("  " + row["priority"] + " " + row["project_id"] +
                         " · " + row["problem"] + " | " + row["next_gate"])
        if not result[field]:
            lines.append("  Geen binnen deze steekproef.")
    lines.append("GEWIJZIGD TUSSEN BEIDE BRONMOMENTEN (geen vrijgave):")
    for item in result["changed_source_details"]:
        lines.append("  " + item["priority"] + " " + item["project_id"] +
                     " · " + item["id"] + " | " +
                     ", ".join(item["changed_fields"]))
        before_head = item["previous"]["source_pr_head"]
        after_head = item["current"]["source_pr_head"]
        if before_head != after_head:
            lines.append("    PR-bron HEAD: " +
                         str(before_head or "ONBEKEND")[:12] + " → " +
                         str(after_head or "ONBEKEND")[:12] +
                         " · vereiste exacte-head CI/review opnieuw controleren")
    if not result["changed_source_details"]:
        lines.append("  Geen wijziging waargenomen in deze begrensde steekproef.")
    if result.get("schema") == "zsentinel-blocker-continuity-history-v1":
        lines.append("HERHAALDE P0/P1 BLOKKADES (" + str(result["history_sample_count"]) +
                     " BEGRENZDE BRONMOMENTEN):")
        for row in result["history_items"][:36]:
            lines.append(
                "  " + row["priority"] + " " + row["project_id"] +
                " · gezien " + str(row["sightings_in_sample"]) + "×" +
                " · huidige reeks " + str(row["consecutive_recent_samples"]) +
                " · laatste bron " + ("WAARGENOMEN" if row["current_sample_observed"]
                                     else "ONBEKEND / NIET OPGELOST") +
                " | " + row["next_gate"])
        if result["history_windows_different_or_unknown"]:
            lines.append("  LET OP: projectvensters verschillen/ontbreken; niet gezien != opgelost.")
        if result["history_items_truncated"]:
            lines.append("  Meer historische blokkades dan 96; lijst gedeeltelijk.")
        lines.append("  Menselijke P0-aandachtkandidaten (niet verzonden): " +
                     str(result["repeated_p0_human_attention_candidates"]))
    lines.append("Bronmoment en vereiste toestemming moeten opnieuw worden gecontroleerd.")
    return "\n".join(lines) + "\n"


def as_html(result):
    cards = []
    for label, field in (
        ("1 · Nieuw waargenomen", "newly_observed"),
        ("2 · Nog zichtbaar", "still_observed"),
        ("3 · Uit huidige steekproef verdwenen ≠ opgelost", "not_reobserved_not_resolved"),
    ):
        rows = []
        for row in result[field]:
            links = " ".join(
                '<a href="' + escape(url, quote=True) +
                '" target="_blank" rel="noopener noreferrer">Bron</a>'
                for url in row["links"])
            rows.append(
                '<article><strong>' + escape(row["priority"]) + ' · ' +
                escape(row["project_id"]) + '</strong><p>' +
                escape(row["problem"]) + '</p><p>Actie: ' +
                escape(row["action"]) + '</p><small>Eigenaar: ' +
                escape(row["owner"]) + ' · poort: ' +
                escape(row["next_gate"]) + '</small><p>' + links + '</p></article>')
        cards.append('<section><h2>' + label + '</h2>' +
                     ("".join(rows) if rows else "<p>Geen in deze bronsteekproef.</p>") +
                     "</section>")
    edits = []
    for change in result["changed_source_details"]:
        before_head = change["previous"]["source_pr_head"]
        after_head = change["current"]["source_pr_head"]
        edits.append(
            '<article><strong>' + escape(change["priority"]) + ' · ' +
            escape(change["project_id"]) + '</strong><p>Gewijzigd: ' +
            escape(", ".join(change["changed_fields"])) + '</p><p>' +
            'PR-bron HEAD: ' + escape((before_head or "ONBEKEND")[:12]) +
            ' → ' + escape((after_head or "ONBEKEND")[:12]) +
            ' · geen CI-/review-vrijgave.</p></article>')
    cards.insert(2, '<section><h2>Wijzigingen in bron- of PR-gates</h2>' +
                 (''.join(edits) if edits else
                  '<p>Geen verschillen gezien; dit bewijst geen herstel.</p>') +
                 '</section>')
    if result.get("schema") == "zsentinel-blocker-continuity-history-v1":
        recurring = []
        for item in result["history_items"][:48]:
            source_links = ' '.join(
                '<a href="' + escape(url, quote=True) +
                '" target="_blank" rel="noopener noreferrer">Bekijk GitHub-bron</a>'
                for url in item["links"][:3])
            recurring.append(
                '<article><strong>' + escape(item["priority"]) + ' · ' +
                escape(item["project_id"]) + '</strong><p>' +
                escape(item["problem"]) + '</p><small>' +
                'Waargenomen: ' + str(item["sightings_in_sample"]) + '/' +
                str(result["history_sample_count"]) + ' bronmomenten · ' +
                'reeks: ' + str(item["consecutive_recent_samples"]) + ' · ' +
                ('actueel gezien' if item["current_sample_observed"] else
                 'huidige bron ONBEKEND / niet opgelost') +
                (' · na eerdere ontbrekende bron opnieuw gezien'
                 if item["reappeared_after_gap"] else '') +
                ' · actie: ' + escape(item["next_gate"]) + '</small><p>' +
                source_links + '</p></article>')
        cards.insert(0, '<section><h2>Herhaald herstelwerk · ' +
                     str(result["history_sample_count"]) + ' bronmomenten</h2>' +
                     ('<p class="alert">Verschillende projectvensters: verdwenen is'
                      ' GEEN herstelbewijs.</p>'
                      if result["history_windows_different_or_unknown"] else '') +
                     '<p>Menselijke P0-aandachtkandidaten: ' +
                     str(result["repeated_p0_human_attention_candidates"]) +
                     ' · er is geen melding verstuurd en geen claim gedaan.</p>' +
                     (''.join(recurring) or '<p>Geen incidentkaarten in deze steekproef.</p>') +
                     ('<p>Meer dan 48 regels; weergave beperkt.</p>'
                      if len(result["history_items"]) > 48 else '') +
                     '</section>')
    return ('<!doctype html><html lang="nl"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;">'
            '<title>zSentinel broncontinuïteit</title><style>'
            'body{font:16px system-ui;margin:auto;max-width:960px;padding:2rem;background:#0e1623;color:#e7edf5}'
            'section{margin:2rem 0}article{background:#1b2b3a;border-left:3px solid #e0bd70;'
            'padding:1rem;margin:.6rem 0;border-radius:8px}a{color:#9ecfff}small{color:#c6d0dc}'
            '.alert{border:1px solid #e0bd70;padding:1rem;border-radius:8px}</style></head>'
            '<body><main><h1>zSentinel · blokkades eerst</h1>'
            '<p class="alert">' +
            ('Projectvenster VERSCHILT/ONBEKEND; bronnen zijn niet volledig vergelijkbaar. '
             if result["window_mismatch_or_unknown"] else
             'Het projectvenster is gelijk, maar bronnen blijven beperkt. ') +
            'Dit vergelijkt twee beperkte bronmomenten. Verdwenen ≠ opgelost;'
            ' geen bewijs van hervatte workers, nieuwe claim, CI-vrijgave of productieherstel.</p>' +
            "".join(cards) + '</main></body></html>')


def _write_new(path: Path, content: str):
    raw = content.encode("utf-8")
    if len(raw) > 1024 * 1024:
        raise ValueError("output exceeds one MiB")
    # Exclusive creation: never overwrite a source, symlink, or another owner's file.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as dst:
            dst.write(raw)
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(description="Compare two existing private zSentinel blocker board JSON snapshots without network writes")
    parser.add_argument("--before", required=True, action="append", type=Path,
                        help="repeat 1..7 previous chronological portfolio snapshots")
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--format", choices=("json", "text", "html"), default="html")
    parser.add_argument("--output", type=Path, help="new exclusive 0600 output file; default stdout")
    args = parser.parse_args()
    try:
        paths = args.before + [args.after]
        if len(paths) > 8 or len({p.resolve() for p in paths}) != len(paths):
            raise ValueError("two to eight distinct existing snapshots required")
        if args.output and args.output.resolve() in {p.resolve() for p in paths}:
            raise ValueError("output cannot replace an input snapshot")
        inputs = [_read(path) for path in paths]
        result = (compare(inputs[0], inputs[1]) if len(inputs) == 2
                  else compare_history(inputs))
        rendered = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                    if args.format == "json" else
                    as_text(result) if args.format == "text" else as_html(result))
        if args.output:
            _write_new(args.output, rendered)
        else:
            sys.stdout.write(rendered)
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print("zSentinel blockercontinuïteit ONBEKEND: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
