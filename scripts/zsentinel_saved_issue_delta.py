#!/usr/bin/env python3
"""Compare source-pinned private zSentinel issue ZIPs, without live authority.

Human source-delta handoff; never a GitHub status, claim, CI or merge check.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
from zipfile import BadZipFile

from zsentinel_saved_issue_archive import canonical_issue, read_issue_dossier
from zsentinel_saved_gate_dossier import _save_private_handoff

MAX_DIFF_BYTES = 48 * 1024


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
        if old_sources[-overlap:] != new_sources[:overlap]:
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True, type=Path,
                        help="Older private issue continuity ZIP")
    parser.add_argument("--current", required=True, type=Path,
                        help="Newer private issue continuity ZIP")
    parser.add_argument("--issue", required=True,
                        help="Canonical exact source issue URL")
    parser.add_argument("--output", type=Path, required=True,
                        help="New private human handoff .md; never overwrite")
    args = parser.parse_args(argv)
    try:
        previous = read_issue_dossier(args.previous)
        current = read_issue_dossier(args.current)
        change = issue_difference(previous, current, args.issue)
        handoff = human_markdown(change)
        _save_private_handoff(args.output, handoff)
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
