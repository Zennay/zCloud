#!/usr/bin/env python3
"""Merge private, bounded zSentinel PR-graph snapshots into one offline collision handoff.

This makes cross-window file overlap *visible* without additional GitHub GETs.
It cannot prove globally complete source coverage, ownership, CI, review,
actual Git merge conflicts, worker recovery or release authorization.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import stat
import sys

from zsentinel_pr_scope_guard import REPO, SHA, edit_path, unique

SCHEMA = "zsentinel-saved-pr-cross-window-collisions-v1"
SOURCE = "zsentinel-pr-file-collision-graph-v1"
MAX_INPUTS = 6
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 12 * 1024 * 1024
MAX_PRS = 288
MAX_PAIRS = 200
MAX_FILES_PER_PR = 100
MAX_SHARED_PATHS = 24
MAX_SECONDS_APART = 600
MAX_REPORT_BYTES = 2 * 1024 * 1024


def read_bounded(path: Path):
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise ValueError("safe regular-file opening is unavailable")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 2 <= before.st_size <= MAX_SOURCE_BYTES:
        raise ValueError("source must be a bounded regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode)
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns)):
            raise ValueError("source changed before read")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(MAX_SOURCE_BYTES + 1)
        after = os.fstat(fd)
        if (len(raw) != meta.st_size or len(raw) > MAX_SOURCE_BYTES
                or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                != (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns)):
            raise ValueError("source changed during read")
    finally:
        os.close(fd)
    parsed = json.loads(raw.decode("utf-8", errors="strict"),
                        object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(
                            ValueError("nonfinite evidence number")))
    if type(parsed) is not dict:
        raise ValueError("source must contain a JSON object")
    return parsed, len(raw)


def utc_seconds(value):
    if type(value) is not str or not 1 <= len(value) <= 40:
        raise ValueError("invalid observation time")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("observation must use UTC")
    unix = stamp.timestamp()
    if unix > datetime.now(timezone.utc).timestamp() + 300:
        raise ValueError("future observation")
    return unix


def inspect_source(data, repository):
    if (data.get("schema") != SOURCE or data.get("repository") != repository
            or data.get("source") != "bounded_double_read_current_open_github_prs_and_files"
            or data.get("open_pr_snapshot_stable") is not True
            or any(data.get(key) is not False for key in
                   ("file_edit_authorized", "claim_authorized",
                    "merge_authorized", "writes_performed",
                    "notion_claims_verified", "worker_file_leases_verified"))):
        raise ValueError("not a stable, nonauthorizing PR collision source")
    observed = utc_seconds(data.get("observed_at_utc"))
    heads = data.get("inspected_pr_heads")
    receipts = data.get("open_pr_page_receipts")
    size = data.get("open_pr_page_size")
    if (type(heads) is not list or len(heads) > 48
            or type(data.get("inspected_prs")) is not int
            or data["inspected_prs"] != len(heads)
            or type(receipts) is not list or not 1 <= len(receipts) <= 3
            or type(size) is not int or not 1 <= size <= 48):
        raise ValueError("unbounded graph source inventory")
    pages = []
    for receipt in receipts:
        if type(receipt) is not dict:
            raise ValueError("invalid page evidence")
        page, seen = receipt.get("page"), receipt.get("observed_prs")
        if (type(page) is not int or not 1 <= page <= 50
                or type(seen) is not int or not 0 <= seen <= size
                or receipt.get("first_position") != (page - 1) * size + 1
                or receipt.get("last_position") != (page - 1) * size + seen):
            raise ValueError("invalid page position")
        pages.append(page)
    if pages != list(range(pages[0], pages[0] + len(pages))):
        raise ValueError("source pages must be contiguous")
    if data.get("open_pr_page_number") != pages[0]:
        raise ValueError("source page provenance mismatch")
    found = {}
    incomplete = []
    for pr in heads:
        if type(pr) is not dict:
            raise ValueError("invalid PR evidence")
        number, url = pr.get("number"), pr.get("url")
        files = pr.get("file_paths_observed")
        head, base = pr.get("head_sha"), pr.get("base_sha")
        if (type(number) is not int or not 0 < number < 100000000
                or url != "https://github.com/" + repository + "/pull/" + str(number)
                or type(head) is not str or SHA.fullmatch(head) is None
                or type(base) is not str or SHA.fullmatch(base) is None
                or type(files) is not list or len(files) > MAX_FILES_PER_PR
                or pr.get("file_list_complete") not in (True, False)
                or type(pr.get("file_count_observed")) is not int
                or pr["file_count_observed"] != len(files)
                or number in found):
            raise ValueError("invalid, duplicate or unsupported PR inventory")
        names = []
        for name in files:
            if type(name) is not str or name.endswith("/"):
                raise ValueError("PR source paths must name individual files")
            names.append(edit_path(name))
        if len(names) != len(set(names)):
            raise ValueError("duplicate file evidence within PR")
        found[number] = {
            "number": number, "url": url,
            "head_sha": head, "base_sha": base, "files": names,
        }
        if pr["file_list_complete"] is False:
            incomplete.append(number)
    return observed, pages, found, incomplete


def combine(paths, repository):
    if not REPO.fullmatch(repository) or not 2 <= len(paths) <= MAX_INPUTS:
        raise ValueError("supply two to six private graph snapshots for one repository")
    times, all_pages, all_prs, partial_prs = [], set(), {}, []
    total_bytes = 0
    for index, path in enumerate(paths):
        data, size = read_bounded(path)
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("combined sources exceed bounded size")
        seen_at, pages, prs, incomplete = inspect_source(data, repository)
        if any(page in all_pages for page in pages):
            raise ValueError("overlapping source pages cannot be combined")
        all_pages.update(pages)
        times.append(seen_at)
        partial_prs.extend({"pr": number, "snapshot": index + 1}
                           for number in incomplete)
        for number, pr in prs.items():
            if number in all_prs:
                # A PR moving between dated pages breaks cross-window
                # ownership proof even if it happens to have the same SHA.
                raise ValueError("one PR appears in multiple source windows")
            pr["snapshot"] = index + 1
            all_prs[number] = pr
    if max(times) - min(times) > MAX_SECONDS_APART:
        raise ValueError("saved PR snapshots are too far apart in time")
    if len(all_prs) > MAX_PRS:
        raise ValueError("too many combined PR identities")

    owners = {}
    for pr in all_prs.values():
        for name in pr["files"]:
            owners.setdefault(name, set()).add(pr["number"])
    paired = {}
    for name, numbers in owners.items():
        ordered = sorted(numbers)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1:]:
                # Existing individual graph reports already cover within-
                # snapshot collisions; this reports NEW cross-window ones.
                if all_prs[left]["snapshot"] == all_prs[right]["snapshot"]:
                    continue
                paired.setdefault((left, right), []).append(name)
    collisions = sorted(paired.items(), key=lambda item: (-len(item[1]), item[0]))
    rows = []
    for (left, right), names in collisions[:MAX_PAIRS]:
        a, b = all_prs[left], all_prs[right]
        rows.append({
            "left_url": a["url"], "right_url": b["url"],
            "left_head_sha": a["head_sha"], "right_head_sha": b["head_sha"],
            "left_snapshot": a["snapshot"], "right_snapshot": b["snapshot"],
            "shared_files_observed": len(names),
            "shared_paths": sorted(names)[:MAX_SHARED_PATHS],
            "shared_paths_truncated": len(names) > MAX_SHARED_PATHS,
            "actual_git_merge_conflict_verified": False,
        })
    return {
        "schema": SCHEMA,
        "repository": repository,
        "source": "bounded_saved_double_read_github_graphs_not_one_live_snapshot",
        "snapshot_count": len(paths),
        "total_observed_prs": len(all_prs),
        "observed_source_page_numbers": sorted(all_pages),
        "observation_window_utc": [
            datetime.fromtimestamp(t, timezone.utc).isoformat()
            for t in (min(times), max(times))],
        "cross_snapshot_file_collision_pairs_observed": len(collisions),
        "shown_collision_pairs": rows,
        "collision_pairs_truncated": len(collisions) > MAX_PAIRS,
        "prs_with_partial_file_scope": partial_prs[:48],
        "partial_file_scope_list_truncated": len(partial_prs) > 48,
        "source_complete_for_all_open_prs": False,
        "source_is_atomic_current_snapshot": False,
        "worker_claims_verified": False,
        "prless_branches_verified": False,
        "required_ci_and_reviews_verified": False,
        "merge_authorized": False,
        "claim_authorized": False,
        "deploy_authorized": False,
        "writes_performed": False,
    }


def render(report, fmt):
    lines = [
        "# zSentinel — privé cross-window PR-bestandsbotsingen",
        "",
        "Repository: " + report["repository"],
        "Bronmomenten (UTC): " + " tot ".join(report["observation_window_utc"]),
        "Opgeslagen momentopnamen: " + str(report["snapshot_count"]) +
        " · PRs: " + str(report["total_observed_prs"]),
        "Geziene GitHub-PR-pagina's: " +
        ", ".join(map(str, report["observed_source_page_numbers"])),
        "Nieuwe OVER-PAGINA bestandsparen: " +
        str(report["cross_snapshot_file_collision_pairs_observed"]),
        "Geen atomische live bron, geen eigenaarsclaim en geen mergevrijgave.",
        "",
    ]
    for row in report["shown_collision_pairs"]:
        lines.append("- [PR](" + row["left_url"] + ") ↔ [PR](" +
                     row["right_url"] + ") · " +
                     str(row["shared_files_observed"]) +
                     " gedeelde bestandspaden, bronmomenten " +
                     str(row["left_snapshot"]) + "/" + str(row["right_snapshot"]))
        for name in row["shared_paths"]:
            lines.append("    - " + name.replace("\n", " "))
        if row["shared_paths_truncated"]:
            lines.append("    - … overige paden niet getoond")
    if not report["shown_collision_pairs"]:
        lines.append("Geen cross-window botsing in deze bestanden gezien; GEEN vrijgave.")
    if report["collision_pairs_truncated"]:
        lines.append("Meer conflicten buiten de begrensde weergave.")
    if report["prs_with_partial_file_scope"]:
        lines.append("WAARSCHUWING: enkele PR-bestandlijsten zijn niet volledig.")
    lines.extend([
        "",
        "Vereiste CI, review, huidige Notion-claims en production owner "
        "worden in dit rapport niet beoordeeld.",
        "",
    ])
    if fmt == "markdown":
        return "\n".join(lines)
    if fmt == "text":
        return "\n".join(line.replace("**", "") for line in lines)
    if fmt == "json":
        return json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    rows = "".join(
        "<tr><td><a rel='noopener noreferrer' href='" +
        escape(item["left_url"], quote=True) + "'>PR</a> ↔ "
        "<a rel='noopener noreferrer' href='" +
        escape(item["right_url"], quote=True) + "'>PR</a></td><td>" +
        str(item["shared_files_observed"]) + "</td><td>" +
        "<br>".join(escape(name) for name in item["shared_paths"]) +
        "</td></tr>"
        for item in report["shown_collision_pairs"])
    return (
        "<!doctype html><html lang='nl'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta http-equiv='Content-Security-Policy' "
        "content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; connect-src 'none'\">"
        "<title>zSentinel · opgeslagen PR-vensters</title>"
        "<style>body{font:16px system-ui;background:#111820;color:#f0f3f8;"
        "margin:28px auto;max-width:1000px;padding:16px}"
        "table{width:100%;border-collapse:collapse}th,td{padding:12px;"
        "border-bottom:1px solid #445;text-align:left;vertical-align:top;"
        "overflow-wrap:anywhere}a{color:#b5d9ff}</style>"
        "<h1>zSentinel · cross-window PR-bestandsbotsingen</h1>"
        "<p>Repository: " + escape(report["repository"]) +
        " · momentopnamen: " + str(report["snapshot_count"]) +
        " · onderzochte PRs: " + str(report["total_observed_prs"]) +
        "</p><aside>Geen atomische live bron, eigenaars- of mergevrijgave. "
        "Andere GitHub-pagina's blijven UNKNOWN.</aside>"
        "<table><thead><tr><th>Open bron-PRs</th><th>Gedeelde paden</th>"
        "<th>Geobserveerde bestanden</th></tr></thead><tbody>" +
        (rows or "<tr><td colspan='3'>Geen botsing gezien, geen vrijgave.</td></tr>") +
        "</tbody></table></html>"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True,
                        help="Exacte canonieke Zennay/eigen-repo identiteit")
    parser.add_argument("--input", type=Path, action="append", required=True,
                        help="Privé JSON bron uit --audit-open-prs --format json")
    parser.add_argument("--format", choices=("json", "markdown", "text", "html"),
                        default="html")
    parser.add_argument("--output", type=Path, required=True,
                        help="Nieuw privé bestand, nooit bestaande data overschrijven")
    args = parser.parse_args(argv)
    try:
        report = combine(args.input, args.repo)
        raw = render(report, args.format).encode("utf-8")
        if len(raw) > MAX_REPORT_BYTES:
            raise ValueError("output too large")
        fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            args.output.unlink(missing_ok=True)
            raise
        print("Private cross-window PR collision handoff written; NO claim/merge authority.")
        return 0
    except (OSError, ValueError, UnicodeError, OverflowError, TypeError):
        print("Refused: invalid or conflicting saved PR windows; no actions.",
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
