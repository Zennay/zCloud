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
MAX_BYTES = 512 * 1024
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
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
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
        checks = [_plain(value, 155) for value in checks]
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


def compare(before, after):
    old, new = _cards(before), _cards(after)
    current, first_seen, not_reobserved, changed = [], [], [], []
    for key in sorted(new, key=lambda k: (
            PRIORITY[new[k]["priority"]], new[k]["project_id"], k)):
        item = new[key]
        if key not in old:
            first_seen.append(item)
            continue
        earlier = old[key]
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


def as_text(result):
    lines = [
        "zSentinel · bronvergelijking van twee begrensde blockerborden",
        "LET OP: niet opnieuw gezien != opgelost; geen actuele leases of hervatte workers bewezen.",
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
    return ('<!doctype html><html lang="nl"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;">'
            '<title>zSentinel broncontinuïteit</title><style>'
            'body{font:16px system-ui;margin:auto;max-width:960px;padding:2rem;background:#0e1623;color:#e7edf5}'
            'section{margin:2rem 0}article{background:#1b2b3a;border-left:3px solid #e0bd70;'
            'padding:1rem;margin:.6rem 0;border-radius:8px}a{color:#9ecfff}small{color:#c6d0dc}'
            '.alert{border:1px solid #e0bd70;padding:1rem;border-radius:8px}</style></head>'
            '<body><main><h1>zSentinel · blokkades eerst</h1>'
            '<p class="alert">Dit vergelijkt twee beperkte bronmomenten. Verdwenen ≠ opgelost;'
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
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--format", choices=("json", "text", "html"), default="html")
    parser.add_argument("--output", type=Path, help="new exclusive 0600 output file; default stdout")
    args = parser.parse_args()
    try:
        if args.before.resolve() == args.after.resolve():
            raise ValueError("two distinct existing source snapshots are required")
        if args.output and args.output.resolve() in (args.before.resolve(), args.after.resolve()):
            raise ValueError("output cannot replace a source snapshot")
        result = compare(_read(args.before), _read(args.after))
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
