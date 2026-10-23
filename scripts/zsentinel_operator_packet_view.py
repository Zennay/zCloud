#!/usr/bin/env python3
"""Open a private source-bound zSentinel owner packet as a read-only action desk.

Shows bounded P0/P1/P2 owner follow-ups. Never interprets an archived packet
as live claims, successful CI or merge authority. No network or writes.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
from pathlib import Path
from zipfile import ZIP_STORED, BadZipFile, LargeZipFile, ZipFile

from zsentinel_operator_packet import MEMBERS, MAX_PACKET_BYTES, build_packet
from zsentinel_runtime_handoff import (
    SCHEMA, _short, _source_report_digest, handoff_html,
)
from zsentinel_private_output import write_private_output


JSON_MAX = 1024 * 1024
SHA = re.compile(r"^[0-9a-f]{64}$")


def _unique(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate owner JSON field")
        output[key] = value
    return output


def _nonfinite(_):
    raise ValueError("non-finite owner JSON number")


def open_packet(path: Path):
    """Reject an altered ZIP; derive the verified action cards from its JSON."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("packet path must be absolute")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("no-follow read unavailable")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW |
                 getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PACKET_BYTES
                or info.st_size < 200):
            raise ValueError("packet is not a bounded regular file")
        # Keep the exact inode open; the filename may be swapped after open.
        with os.fdopen(fd, "rb", closefd=False) as stream:
            actual = stream.read(MAX_PACKET_BYTES + 1)
    finally:
        os.close(fd)
    if len(actual) > MAX_PACKET_BYTES:
        raise ValueError("packet exceeds size limit")
    import io
    with ZipFile(io.BytesIO(actual), "r", allowZip64=False) as archive:
        infos = archive.infolist()
        if (len(infos) != len(MEMBERS)
                or tuple(entry.filename for entry in infos) != MEMBERS
                or any(entry.is_dir() or entry.compress_type != ZIP_STORED
                       or entry.flag_bits & 0x1 or entry.file_size > JSON_MAX
                       or entry.file_size < 1 or entry.filename.startswith("/")
                       or ".." in entry.filename for entry in infos)):
            raise ValueError("untrusted packet member list")
        with archive.open(MEMBERS[0], "r") as source:
            original_json = source.read(JSON_MAX + 1)
        if len(original_json) > JSON_MAX:
            raise ValueError("owner JSON exceeds source budget")
    envelope = json.loads(
        original_json.decode("utf-8"), object_pairs_hook=_unique,
        parse_constant=_nonfinite)
    if type(envelope) is not dict or envelope.get("schema") != SCHEMA:
        raise ValueError("owner JSON has wrong schema")
    recorded_digest = envelope.get("source_report_sha256")
    if type(recorded_digest) is not str or not SHA.fullmatch(recorded_digest):
        raise ValueError("owner JSON has no canonical source digest")
    report = dict(envelope)
    del report["source_report_sha256"]
    digest = _source_report_digest(report)
    if not hmac.compare_digest(digest, recorded_digest):
        raise ValueError("owner JSON source digest mismatch")
    # Stronger than manifest verification: exact archive bytes must match
    # the original approved serializer for every member and archive header.
    rebuilt = build_packet(report)
    if not hmac.compare_digest(actual, rebuilt):
        raise ValueError("owner packet differs from its source-bound contents")
    handoff_html(report)
    return {
        "schema": "zsentinel-private-owner-packet-view-v1",
        "source_report_sha256": digest,
        "source_created_at_utc": report.get("created_at_utc"),
        "partial_source": True,
        "items_truncated": report["items_truncated"],
        "all_sources_complete": False,
        "current_owner_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "recovery_authorized": False,
        "priority_items": [
            {
                "priority": row["priority"],
                "project": row["project"],
                "problem": _short(row["kind"], 80),
                "owner_next_step": _short(row["next_step"], 220),
                "issue_url": row.get("issue_url"),
                "existing_pr_urls": row.get("existing_pr_urls", []),
                "observed_head_sha": row.get("pr_observed_head_sha"),
                "source_state": _short(row.get("evidence"), 80),
            } for row in report["priority_items"]
        ],
        "historical_source_incomplete": report.get("historical_source_incomplete") is True,
        "historical_rechecks_truncated": report.get("historical_rechecks_truncated") is True,
        "historical_owner_rechecks": [
            {
                "priority": row["priority"],
                "project": row["project"],
                "last_seen_utc": row["historical_source_last_seen_utc"],
                "owner_next_step": _short(row["next_step"], 220),
                "current_source_observed": False,
                "historical_reconciliation_required": True,
                "exact_old_job_url": row.get("exact_github_job_url"),
                "exact_old_commit_url": row.get("exact_github_commit_url"),
                "recovery_authorized": False,
            } for row in report.get("historical_owner_rechecks", [])
        ],
    }



def _safe_link(url, label):
    """Render only canonical Zennay GitHub issue/PR/job/commit links."""
    if type(url) is not str:
        return ""
    patterns = (
        (r"https://github\.com/Zennay/[A-Za-z0-9_.-]+/(?:issues|pull)/[1-9][0-9]*", "issue"),
        (r"https://github\.com/Zennay/[A-Za-z0-9_.-]+/actions/runs/[1-9][0-9]*/job/[1-9][0-9]*", "job"),
        (r"https://github\.com/Zennay/[A-Za-z0-9_.-]+/commit/[0-9a-f]{40}", "commit"),
    )
    if not any(re.fullmatch(pattern, url) for pattern, _kind in patterns):
        return ""
    return ('<a href="' + escape(url, quote=True) +
            '" target="_blank" rel="noopener noreferrer">' +
            escape(label) + '</a>')


def render_html(report):
    """Build a script-free offline owner desk from already verified packet data."""
    cards = []
    for item in report["priority_items"]:
        links = []
        if item.get("issue_url"):
            link = _safe_link(item["issue_url"], "Bestaand issue")
            if link:
                links.append(link)
        for index, url in enumerate(item.get("existing_pr_urls", []), 1):
            link = _safe_link(url, "Bestaande PR" if index == 1 else
                              "Bestaande PR " + str(index))
            if link:
                links.append(link)
        head = item.get("observed_head_sha")
        head_line = (" · geobserveerde HEAD " + escape(head[:12])
                     if type(head) is str and SHA.fullmatch(head) else "")
        cards.append(
            '<article class="card"><header><span class="priority">' +
            escape(item["priority"]) + '</span><strong>' +
            escape(item["project"]) + '</strong></header><h3>' +
            escape(item["problem"]) + '</h3><p>' +
            escape(item["owner_next_step"]) + '</p><p class="muted">' +
            escape(item["source_state"]) + head_line + '</p><p>' +
            " · ".join(links) + '</p></article>')
    historical = []
    for item in report["historical_owner_rechecks"]:
        links = []
        for key, label in (("exact_old_job_url", "Oude exacte job"),
                           ("exact_old_commit_url", "Oude exacte commit")):
            link = _safe_link(item.get(key), label)
            if link:
                links.append(link)
        historical.append(
            '<article class="card historical"><header><span class="priority">' +
            escape(item["priority"]) + '</span><strong>' +
            escape(item["project"]) + '</strong></header><p><strong>Laatste '
            'waarneming:</strong> ' + escape(item["last_seen_utc"]) +
            '</p><p>' + escape(item["owner_next_step"]) + '</p><p>' +
            " · ".join(links) + '</p></article>')
    if not cards:
        cards.append('<p>Geen eigenaaractie in deze begrensde bron. '
                     'Dit is geen compleet herstelbewijs of vrijgave.</p>')
    history_html = (
        '<section><h2>Historisch · niet actueel waargenomen</h2><p>Deze '
        'meldingen moeten opnieuw aan actuele bronnen worden getoetst.</p>' +
        "".join(historical) + '</section>'
        if historical or report["historical_rechecks_truncated"] or
        report["historical_source_incomplete"] else "")
    truncation = (
        '<p class="warning">De lijst is afgekapt; niet-getoonde items zijn '
        'niet opgelost.</p>' if report["items_truncated"] or
        report["historical_rechecks_truncated"] or
        report["historical_source_incomplete"] else "")
    return (
        '<!doctype html><html lang="nl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        """<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; img-src 'none'; connect-src 'none'">"""
        '<meta name="referrer" content="no-referrer">'
        '<title>zSentinel · offline eigenaarwerkbak</title><style>'
        'body{font:16px/1.55 system-ui,sans-serif;max-width:70rem;margin:auto;'
        'padding:1.25rem;color:#18202b;background:#f7f8fa}'
        '.card,section{background:white;border:1px solid #cbd3dd;border-radius:.6rem;'
        'padding:1rem;margin:1rem 0;overflow-wrap:anywhere}'
        'header{display:flex;gap:.75rem;align-items:center;flex-wrap:wrap}'
        '.priority{font-weight:700;border:1px solid #7b8794;border-radius:.25rem;'
        'padding:.1rem .4rem}.muted{color:#586574;font-size:.9rem}'
        '.warning{border-left:4px solid #a05a00;padding:.75rem}'
        '.historical{border-style:dashed}a{color:#07549e}h1,h2,h3{line-height:1.25}'
        'code{overflow-wrap:anywhere}</style></head><body>'
        '<h1>zSentinel · offline eigenaarwerkbak</h1>'
        '<p><strong>Privé bronmoment:</strong> ' +
        escape(str(report["source_created_at_utc"])) + '</p><p><strong>Bron '
        'SHA-256:</strong> <code>' + escape(report["source_report_sha256"]) +
        '</code></p><p>Brondekking is partieel. Deze export verifieert geen '
        'huidige claims, CI, reviews of mergebevoegdheid. Geen retry, claim, '
        'merge, deploy of runnerherstel uitgevoerd of toegestaan.</p>' +
        truncation + '<section><h2>Opvolgen · P0 eerst</h2>' +
        "".join(cards) + '</section>' + history_html +
        '<footer><p>Actuele eigenaar, PR-HEAD, verplichte CI en reviews moeten '
        'apart worden gecontroleerd. Een archief is geen live status.</p></footer>'
        '</body></html>\n'
    )

def main():
    parser = argparse.ArgumentParser(
        description="Open source-exact offline zSentinel owner ZIP; do not run actions")
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--format", choices=("text", "json", "html"), default="text")
    parser.add_argument("--output", type=Path,
                        help="Optional new private mode-0600 HTML file; create-only")
    args = parser.parse_args()
    if args.output is not None and args.format != "html":
        parser.error("--output requires --format html")
    try:
        report = open_packet(args.packet)
    except (OSError, ValueError, TypeError, KeyError,
            UnicodeError, RecursionError, EOFError, BadZipFile,
            LargeZipFile, RuntimeError) as exc:
        parser.exit(2, f"zSentinel owner packet refused: {exc}\n")
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    elif args.format == "html":
        rendered = render_html(report)
        if args.output is None:
            print(rendered, end="")
        else:
            try:
                write_private_output(args.output, rendered, "html")
            except (OSError, ValueError):
                # Destination paths are private; do not echo them to shared logs.
                parser.exit(
                    2, "zSentinel owner packet output refused: private destination unavailable or unsafe.\n")
            print("Created NEW private offline owner desk (0600); no actions.",
                  file=sys.stderr)
    else:
        print("zSentinel · PRIVÉ / OFFLINE / BRONMOMENT " +
              str(report["source_created_at_utc"]))
        print("SHA256-bron: " + report["source_report_sha256"])
        print("Huidige claims, exacte CI, review en merge: NIET geverifieerd; "
              "GEEN automatisch herstel.")
        for row in report["priority_items"]:
            print(row["priority"] + " " + row["project"] + " · " +
                  row["problem"] + " · " + row["owner_next_step"])
            for url in row["existing_pr_urls"]:
                print("  Bestaande PR (opnieuw exact controleren): " + url)
            if row["issue_url"]:
                print("  Bestaand issue: " + row["issue_url"])
        if report["items_truncated"]:
            print("Extra eigenaaracties afgekapt: bron blijft onvolledig.")
        if report["historical_owner_rechecks"]:
            print("HISTORISCH · NIET ACTUEEL WAARGENOMEN; eigenaar hercontroleert bron:")
            for row in report["historical_owner_rechecks"]:
                print("  " + row["priority"] + " " + row["project"] +
                      " · laatst gezien " + row["last_seen_utc"])
                print("    " + row["owner_next_step"])
                if row["exact_old_job_url"]:
                    print("    Oude exacte job, NIET actuele status: " +
                          row["exact_old_job_url"])
        if report["historical_rechecks_truncated"]:
            print("Historische lijst afgekapt: niet-getoonde incidenten zijn NIET opgelost.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
