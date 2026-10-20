#!/usr/bin/env python3
"""Compare source-pinned private zSentinel issue ZIPs, without live authority.

Human source-delta handoff; never a GitHub status, claim, CI or merge check.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
import os
from pathlib import Path
import secrets
import stat
import sys
from zipfile import BadZipFile

from zsentinel_saved_issue_archive import canonical_issue, read_issue_dossier
from zsentinel_saved_gate_dossier import _open_private_extract_parent, _save_private_handoff

MAX_DIFF_BYTES = 48 * 1024


def admit_comparable_source_pair(older, newer):
    """Verify original source scope, chronology and contiguous overlapping SHA receipts.

    Both inputs must already have passed private ZIP and canonical issue/PR
    receipt admission. Returns no current owner, CI or release authority.
    """
    admit_comparable_source_pair(older, newer)
    _, _, _, old_issues, old_sources = older
    _, _, _, new_issues, new_sources = newer
    old_ids = [r["original_review_manifest_sha256"] for r in old_sources]
    new_ids = [r["original_review_manifest_sha256"] for r in new_sources]
    return old_ids[-1], new_ids[-1]


def issue_difference(older, newer, issue_url):
    """Compare fully admitted chronological dossiers for one canonical issue."""
    if not canonical_issue(issue_url):
        raise ValueError("Issue URL must be canonical GitHub source")
    _, old_manifest, old_doc, old_issues, old_sources = older
    _, new_manifest, new_doc, new_issues, new_sources = newer
    if old_doc.get("scope") != new_doc.get("scope"):
        raise ValueError("Incompatible original portfolio scopes")
    old_ids = [s["original_review_manifest_sha256"] for s in old_sources]
    new_ids = [s["original_review_manifest_sha256"] for s in new_sources]
    if len(set(old_ids)) != len(old_ids) or len(set(new_ids)) != len(new_ids):
        raise ValueError("Duplicate original manifest source")
    old_last = datetime.fromisoformat(old_sources[-1]["latest_observed_utc"])
    new_last = datetime.fromisoformat(new_sources[-1]["latest_observed_utc"])
    if new_last <= old_last:
        raise ValueError("Second archive does not have a newer observation")
    shared = set(old_ids) & set(new_ids)
    if shared:
        matches = [size for size in range(1, min(len(old_ids), len(new_ids)) + 1)
                   if old_ids[-size:] == new_ids[:size]]
        if not matches:
            raise ValueError("Overlapping historical sources are not contiguous")
        overlap = max(matches)
        if shared != set(old_ids[-overlap:]):
            raise ValueError("Partial or inconsistent source overlap")
        for older_source, newer_source in zip(
                old_sources[-overlap:], new_sources[:overlap]):
            # Source ordinals are local to each generated dossier: the same
            # original manifest can be #3 in one ZIP and #1 in another.
            # Compare original receipt data, not its display position.
            if ({k: v for k, v in older_source.items() if k != "index"} !=
                    {k: v for k, v in newer_source.items() if k != "index"}):
                raise ValueError("Reused manifest digest with contradictory metadata")
    elif datetime.fromisoformat(new_sources[0]["earliest_observed_utc"]) <= old_last:
        raise ValueError("Nonordered overlapping source windows")
    before = next((r for r in old_issues if r["issue_url"] == issue_url), None)
    after = next((r for r in new_issues if r["issue_url"] == issue_url), None)
    if after is None:
        raise ValueError("Issue not in newer sampled source; cannot infer closure")
    prior = {r["pr_url"]: r for r in (before["linked_existing_prs"] if before else [])}
    latest = {r["pr_url"]: r for r in after["linked_existing_prs"]}
    first_seen = sorted(set(latest) - set(prior))
    not_seen = sorted(set(prior) - set(latest))
    changed = []
    stable = 0
    def source_at(ref, sources, field):
        idx = ref[field]
        if type(idx) is not int or not 1 <= idx <= len(sources):
            raise ValueError("Invalid original source ordinal")
        return sources[idx - 1]["original_review_manifest_sha256"]
    fields = (
        ("observed_head", "saved HEAD"),
        ("observed_primary_gate", "saved blocker"),
        ("source_priority", "latest saved PR priority"),
        ("issue_link_priority", "source issue-linked priority"),
        ("issue_ref_absent_in_last_snapshot", "missing from last issue sample"),
        ("missing_from_final_sample", "PR missing from final sample"),
    )
    for url in sorted(set(prior) & set(latest)):
        p, q = prior[url], latest[url]
        diff = [name for field, name in fields if p.get(field) != q.get(field)]
        if (source_at(p, old_sources, "issue_ref_last_seen_snapshot") !=
                source_at(q, new_sources, "issue_ref_last_seen_snapshot")):
            diff.append("last issue-link observation")
        if (source_at(p, old_sources, "last_seen_snapshot") !=
                source_at(q, new_sources, "last_seen_snapshot")):
            diff.append("last PR observation")
        if diff:
            changed.append((url, diff))
        else:
            stable += 1
    return {
        "url": issue_url,
        "old_manifest": old_ids[-1], "new_manifest": new_ids[-1],
        "old_time": old_sources[-1]["latest_observed_utc"],
        "new_time": new_sources[-1]["latest_observed_utc"],
        "old_missing_batches": len(old_sources[-1]["missing_batch_indexes"]),
        "new_missing_batches": len(new_sources[-1]["missing_batch_indexes"]),
        "issue_not_in_old_sample": before is None,
        "first_seen": first_seen, "no_longer_seen": not_seen,
        "changed": changed, "stable": stable,
    }


def human_markdown(change):
    lines = [
        "# zSentinel private issue-to-existing-PR source delta",
        "",
        "**HISTORICAL PRIVATE SOURCE ONLY: no actual open issue, owner, exact-head "
        "CI, independent review, protected-main merge or deployment verified.**",
        "",
        "- Original issue: " + change["url"],
        "- Older observation UTC: " + change["old_time"],
        "- Newer observation UTC: " + change["new_time"],
        "- Old source manifest SHA256: " + change["old_manifest"],
        "- New source manifest SHA256: " + change["new_manifest"],
        "- Older/newer missing batches: " +
          str(change["old_missing_batches"]) + " / " +
          str(change["new_missing_batches"]),
        "- Issue not present in older sample: " +
          ("YES, NOT proof of actual new issue" if
           change["issue_not_in_old_sample"] else "NO"),
        "- Existing PR links newly observed: " + str(len(change["first_seen"])),
        "- Existing PR links no longer observed: " +
          str(len(change["no_longer_seen"])) +
          " (NOT evidence of resolution, merge or owner release)",
        "- PR links with source evidence changes: " +
          str(len(change["changed"])),
        "- PR links without compared source change: " + str(change["stable"]),
        "",
    ]
    for heading, key in (
        ("Newly observed PR references in later bounded sample", "first_seen"),
        ("PR references no longer observed; NOT RESOLVED", "no_longer_seen"),
    ):
        lines += ["## " + heading, ""]
        links = change[key]
        lines += (["- " + link for link in links] if links else
                  ["- None observed in this bounded source"])
        lines.append("")
    lines += ["## Changed saved evidence for already linked PR references", ""]
    if change["changed"]:
        for url, reasons in change["changed"]:
            lines.append("- " + url + ": " + ", ".join(reasons))
    else:
        lines.append("- No changes visible within comparable saved sources")
    lines += [
        "", "## Required human release-first action", "",
        "Verify the actual GitHub issue, every relevant existing PR and current "
        "claim/file scope. Confirm the exact current HEAD, required CI, independent "
        "reviews and protected-main rules before any merge. Saved source drift "
        "cannot authorize a claim, job retry, GitHub merge or deploy.", "",
    ]
    rendered = "\n".join(lines)
    if len(rendered.encode("utf-8")) > MAX_DIFF_BYTES:
        raise ValueError("Private issue handoff is oversized")
    return rendered



def human_html(change):
    """Script-free private operator view of historical link changes."""
    e = lambda x: escape(str(x), quote=True)
    def links(values):
        if not values:
            return "<li>No references observed in this source selection.</li>"
        return "".join(
            '<li><a href="' + e(value) +
            '" target="_blank" rel="noopener noreferrer">' +
            e(value) + '</a></li>'
            for value in values)
    altered = "".join(
        '<li><a href="' + e(url) +
        '" target="_blank" rel="noopener noreferrer">' + e(url) +
        '</a><br><small>' + e(", ".join(fields)) + '</small></li>'
        for url, fields in change["changed"]
    ) or "<li>No comparable source differences observed.</li>"
    result = ("""<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; connect-src 'none'; script-src 'none'; img-src 'none'; form-action 'none'; base-uri 'none'">
<title>zSentinel - Historisch issueverschil</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif}
body{margin:0;background:#101d2e;color:#f1f7ff;line-height:1.6}
main{max-width:980px;margin:auto;padding:clamp(16px,4vw,42px)}
a{color:#96dcff;overflow-wrap:anywhere}a:focus-visible{outline:3px solid #f5dc81}
section,aside{border:1px solid #597890;background:#1b3048;border-radius:12px;padding:15px 20px;margin:15px 0}
aside{border-color:#e2ba72}li{margin:10px 0;overflow-wrap:anywhere}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,245px),1fr));gap:14px}
strong.value{font-size:1.5rem}code{overflow-wrap:anywhere}
@media print{body,section,aside{color:black;background:white}}
</style></head><body><main><h1>Issue → bestaande PR · bronverschil</h1>
<aside><strong>Historische privébronnen; NIET live.</strong> Afwezigheid in
een steekproef bewijst geen issue-sluiting, PR-merge, vrij eigenaarschap,
geslaagde CI, review of herstel. Controleer huidige GitHub/Notion-bronnen
voordat een eigenaar werk claimt of merge-/release-acties onderneemt.</aside>
<p>Origineel GitHub-issue: """ +
'<a href="' + e(change["url"]) +
'" target="_blank" rel="noopener noreferrer">' +
e(change["url"]) + '</a>' + """</p>
<section><h2>Oorspronkelijke bron</h2>
<p>Ouder: """ + e(change["old_time"]) + """ · <code>""" +
e(change["old_manifest"]) + """</code></p><p>Nieuwer: """ +
e(change["new_time"]) + """ · <code>""" +
e(change["new_manifest"]) + """</code></p><p>Ontbrekende batches:
""" + str(change["old_missing_batches"]) + " → " +
str(change["new_missing_batches"]) + """ (beperkte snapshots)</p></section>
<div class="cards"><section><strong class="value">""" +
str(len(change["first_seen"])) + """</strong><p>Nieuw waargenomen PR-links
in de latere bron, niet bewezen nieuw geopend.</p></section>
<section><strong class="value">""" +
str(len(change["no_longer_seen"])) + """</strong><p>Niet meer gezien;
<b>NIET bewezen afgerond</b>.</p></section>
<section><strong class="value">""" +
str(len(change["changed"])) + """</strong><p>Gewijzigde historische
PR-gegevens, geen actuele checkstatus.</p></section></div>
<section><h2>Nieuw in steekproef</h2><ol>""" +
links(change["first_seen"]) + """</ol></section>
<section><h2>Verdwenen uit steekproef — NIET opgelost</h2><ol>""" +
links(change["no_longer_seen"]) + """</ol></section>
<section><h2>Brongegevens veranderd</h2><ol>""" +
altered + """</ol></section>
<footer>Geen fetch, workerclaim, CI-dispatch, PR-merge of deploy door dit
statische dossier. Verplichte toestemming, reviewers en CI blijven gelden.
</footer></main></body></html>""")
    if len(result.encode("utf-8")) > MAX_DIFF_BYTES:
        raise ValueError("Private HTML issue handoff exceeds size budget")
    return result


def save_private_html(destination: Path, body: str):
    """Create an owner-only file without replacing an existing handoff."""
    if not destination.name.endswith(".html") or destination.name in (".", ".."):
        raise ValueError("Select a new private .html output")
    data = body.encode("utf-8")
    if not data or len(data) > MAX_DIFF_BYTES:
        raise ValueError("Invalid private HTML handoff size")
    parentfd = _open_private_extract_parent(destination.parent)
    stage = ".zsentinel-issue-delta-" + secrets.token_hex(12) + ".tmp"
    stagefd = None
    identity = None
    try:
        stagefd = os.open(stage, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                          os.O_NOFOLLOW, 0o600, dir_fd=parentfd)
        opened = os.fstat(stagefd)
        identity = (opened.st_dev, opened.st_ino)
        if (not stat.S_ISREG(opened.st_mode) or
                opened.st_uid != os.geteuid() or
                stat.S_IMODE(opened.st_mode) & 0o077):
            raise ValueError("Unable to create private handoff")
        pending = memoryview(data)
        while pending:
            count = os.write(stagefd, pending)
            if count <= 0:
                raise OSError("Private HTML output did not progress")
            pending = pending[count:]
        os.fsync(stagefd)
        check = os.stat(stage, dir_fd=parentfd, follow_symlinks=False)
        if (not stat.S_ISREG(check.st_mode)
                or (check.st_dev, check.st_ino) != identity):
            raise ValueError("Staged handoff identity changed")
        os.link(stage, destination.name, src_dir_fd=parentfd,
                dst_dir_fd=parentfd, follow_symlinks=False)
        os.fsync(parentfd)
    finally:
        if stagefd is not None:
            os.close(stagefd)
        if identity is not None:
            try:
                existing = os.stat(stage, dir_fd=parentfd, follow_symlinks=False)
                if (existing.st_dev, existing.st_ino) == identity:
                    os.unlink(stage, dir_fd=parentfd)
                    os.fsync(parentfd)
            except OSError:
                pass
        os.close(parentfd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True, type=Path,
                        help="Older private issue continuity ZIP")
    parser.add_argument("--current", required=True, type=Path,
                        help="Newer private issue continuity ZIP")
    parser.add_argument("--issue", required=True,
                        help="Canonical exact source issue URL")
    parser.add_argument("--format", choices=("markdown", "html"),
                        default="markdown")
    parser.add_argument("--output", type=Path, required=True,
                        help="New owner-only .md or .html handoff; never overwrite")
    args = parser.parse_args(argv)
    try:
        previous = read_issue_dossier(args.previous)
        current = read_issue_dossier(args.current)
        change = issue_difference(previous, current, args.issue)
        if args.format == "html":
            save_private_html(args.output, human_html(change))
        else:
            _save_private_handoff(args.output, human_markdown(change))
        print("Private source-bound issue difference saved: " + args.issue)
        print("Existing PR references newly sampled: " +
              str(len(change["first_seen"])))
        print("References not sampled later, NOT RESOLVED: " +
              str(len(change["no_longer_seen"])))
        print("PR source changes observed: " + str(len(change["changed"])))
        print("CURRENT PR HEAD / OWNER / CI / REVIEW / MAIN / LIVE: UNKNOWN")
        return 0
    except (OSError, TypeError, ValueError, KeyError, OverflowError,
            UnicodeError, BadZipFile) as exc:
        print("Refused unsafe or incomplete issue history: " +
              type(exc).__name__, file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
