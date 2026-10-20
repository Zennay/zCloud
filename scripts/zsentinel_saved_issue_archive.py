#!/usr/bin/env python3
"""Reopen a private saved zSentinel issue-continuity ZIP for human product triage.

This offline reader verifies archive consistency and displays the source-bound
issue/PR handoff. It does not authenticate GitHub, authorize merges or change
claims, work queues, workflows, or production state.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from zsentinel_saved_gate_dossier import (
    ISSUE_URL, PR_URL, SHA, _open_private_extract_parent, read_archive,
    source_recency_summary, strict_json,
)

FILES = ("issue-dossier.html", "issue-actions.md", "issue-prs.csv",
         "source-receipt.json", "source-checksums.json")
SCHEMA = "zsentinel-private-issue-continuity-zip-v1"
SOURCE = "previously_saved_private_zip_sources_not_live"
JSON_SCHEMA = "zsentinel-private-saved-issue-first-handoff-v1"
PRIORITIES = ("P0", "P1", "P2", "Unspecified")
MAX_MEMBER = 1024 * 1024
MAX_TOTAL = 4 * MAX_MEMBER
MAX_ISSUES = 100
MAX_LINKED_PRS = 300
HEAD = re.compile(r"[0-9a-f]{40}\Z")


def canonical_issue(value):
    return type(value) is str and ISSUE_URL.fullmatch(value) is not None


def canonical_pr(value, repo):
    match = PR_URL.fullmatch(value) if type(value) is str else None
    return match is not None and match.group(1) == repo


def _readonly_flags(obj, names):
    if any(obj.get(name) is not False for name in names):
        raise ValueError("Source claims privileged authority")


def _validate_receipt(report, manifest):
    if (type(report) is not dict or report.get("schema") != JSON_SCHEMA
            or report.get("source") !=
            "existing_pr_issue_links_in_saved_private_snapshots_not_live"):
        raise ValueError("Unrecognized historical issue evidence")
    _readonly_flags(report, (
        "issue_inventory_known_complete", "github_open_or_closed_state_verified",
        "owner_claim_verified", "required_ci_review_verified",
        "main_merge_verified", "live_verified",
        "writes_performed_to_external_systems",
    ))
    snapshots = report.get("source_snapshots")
    if type(snapshots) is not list or not 1 <= len(snapshots) <= 12:
        raise ValueError("Invalid source snapshot count")
    actual_hashes = []
    for index, sample in enumerate(snapshots, 1):
        if type(sample) is not dict or sample.get("index") != index:
            raise ValueError("Invalid ordered source snapshot")
        digest = sample.get("original_review_manifest_sha256")
        missing = sample.get("missing_batch_indexes")
        if (type(digest) is not str or SHA.fullmatch(digest) is None
                or type(missing) is not list or len(missing) > 300
                or any(type(n) is not int or not 1 <= n <= 300 for n in missing)
                or len(set(missing)) != len(missing)):
            raise ValueError("Invalid source hash or incomplete-batch receipt")
        actual_hashes.append(digest)
    if actual_hashes != manifest.get("original_manifest_sha256"):
        raise ValueError("Source snapshot manifest mismatch")
    selection = report.get("selection")
    if type(selection) is not dict:
        raise ValueError("Missing human issue selection")
    selected = selection.get("exact_source_issue_filter")
    if selected is not None and not canonical_issue(selected):
        raise ValueError("Invalid selected issue URL")
    if selected != manifest.get("selected_issue"):
        raise ValueError("Archive issue selection disagrees with source receipt")
    rows = report.get("issues")
    if (type(rows) is not list or len(rows) > MAX_ISSUES
            or type(selection.get("shown_source_issues")) is not int
            or selection["shown_source_issues"] != len(rows)
            or type(selection.get("matched_source_issues")) is not int
            or selection["matched_source_issues"] < len(rows)
            or type(selection.get("total_source_issues")) is not int
            or selection["total_source_issues"] < selection["matched_source_issues"]
            or (selected is not None and
                (len(rows) != 1 or rows[0].get("issue_url") != selected))):
        raise ValueError("Issue selection count or identity mismatch")
    rank = {name: i for i, name in enumerate(PRIORITIES)}
    seen_issues = set()
    for issue in rows:
        if type(issue) is not dict:
            raise ValueError("Invalid issue row")
        url, repo = issue.get("issue_url"), issue.get("repository")
        if (not canonical_issue(url) or type(repo) is not str
                or not url.startswith("https://github.com/" + repo + "/issues/")
                or url in seen_issues
                or issue.get("issue_number") != int(url.rsplit("/", 1)[-1])):
            raise ValueError("Malformed or duplicated source issue")
        seen_issues.add(url)
        prs = issue.get("linked_existing_prs")
        if type(prs) is not list or not 1 <= len(prs) <= MAX_LINKED_PRS:
            raise ValueError("Invalid bounded saved PR links")
        seen_prs = set()
        for pr in prs:
            if type(pr) is not dict:
                raise ValueError("Invalid saved PR reference")
            link = pr.get("pr_url")
            if (not canonical_pr(link, repo)
                    or link in seen_prs
                    or pr.get("pr_number") != int(link.rsplit("/", 1)[-1])
                    or pr.get("source_priority") not in rank
                    or pr.get("issue_link_priority") not in rank
                    or pr.get("owner_claim_verified") is not False
                    or pr.get("required_ci_review_verified") is not False
                    or pr.get("main_merge_verified") is not False):
                raise ValueError("Unsafe or authorizing historical PR link")
            seen_prs.add(link)
            observed_head = pr.get("observed_head")
            if observed_head is not None and (
                    type(observed_head) is not str or
                    HEAD.fullmatch(observed_head) is None):
                raise ValueError("Noncanonical saved PR HEAD")
            spans = pr.get("issue_ref_observed_in_snapshots")
            if (type(spans) is not list or not spans or len(spans) > len(snapshots)
                    or sorted(set(spans)) != spans or
                    any(type(n) is not int or not 1 <= n <= len(snapshots)
                        for n in spans)
                    or pr.get("issue_ref_last_seen_snapshot") != spans[-1]
                    or pr.get("issue_ref_absent_in_last_snapshot") is not
                    (spans[-1] != len(snapshots))):
                raise ValueError("Contradictory sampled issue relationship")
        if (issue.get("highest_source_priority") not in rank or
                issue["highest_source_priority"] !=
                min((p["issue_link_priority"] for p in prs),
                    key=lambda v: rank[v]) or
                issue.get("read_only") is not True):
            raise ValueError("Issue priority or read-only receipt disagrees")
        _readonly_flags(issue, (
            "issue_open_state_verified", "owners_verified",
            "existing_prs_merged_verified", "worker_dispatch_authorized",
        ))
    return rows, snapshots


def read_issue_dossier(path: Path):
    """Admit exactly the five fixed original 0600 members and their hashes."""
    raw = read_archive(path)
    extracted = {}
    with ZipFile(BytesIO(raw), mode="r") as archive:
        infos = archive.infolist()
        if len(infos) != len(FILES) or {i.filename for i in infos} != set(FILES):
            raise ValueError("Unexpected or duplicate issue archive members")
        if sum(i.file_size for i in infos) > MAX_TOTAL:
            raise ValueError("Issue archive expansion exceeds private budget")
        for info in infos:
            if (info.filename not in FILES or info.is_dir()
                    or info.flag_bits & 1 or info.compress_type != ZIP_DEFLATED
                    or not 1 <= info.file_size <= MAX_MEMBER
                    or info.create_system != 3
                    or info.external_attr >> 16 != 0o100600):
                raise ValueError("Noncanonical or unsafe issue ZIP member")
            with archive.open(info, "r") as reader:
                data = reader.read(MAX_MEMBER + 1)
            if len(data) != info.file_size:
                raise ValueError("Issue ZIP member exceeded size limit")
            extracted[info.filename] = data
    manifest = strict_json(extracted["source-checksums.json"])
    if (manifest.get("schema") != SCHEMA
            or manifest.get("source") != SOURCE
            or manifest.get("read_only") is not True
            or type(manifest.get("files_sha256")) is not dict
            or set(manifest["files_sha256"]) != set(FILES[:-1])):
        raise ValueError("Unrecognized issue package schema")
    _readonly_flags(manifest, (
        "known_complete_github_inventory", "owner_claim_verified",
        "required_exact_head_ci_and_review_verified", "merge_authorized",
        "worker_dispatch_authorized", "main_merge_verified", "live_verified",
    ))
    for filename in FILES[:-1]:
        expected = manifest["files_sha256"][filename]
        if (type(expected) is not str or SHA.fullmatch(expected) is None
                or hashlib.sha256(extracted[filename]).hexdigest() != expected):
            raise ValueError("Issue package member checksum mismatch")
    receipt = strict_json(extracted["source-receipt.json"])
    rows, samples = _validate_receipt(receipt, manifest)
    return extracted, manifest, receipt, rows, samples


def extract_private(files, destination: Path):
    """Create only a new 0700 folder and its five 0600 original files."""
    if not destination.name or destination.name in (".", ".."):
        raise ValueError("Invalid destination directory name")
    parentfd = _open_private_extract_parent(destination.parent)
    childfd = None
    created = []
    try:
        os.mkdir(destination.name, mode=0o700, dir_fd=parentfd)
        try:
            childfd = os.open(
                destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parentfd)
            d = os.fstat(childfd)
            if (not stat.S_ISDIR(d.st_mode) or d.st_uid != os.geteuid()
                    or stat.S_IMODE(d.st_mode) & 0o077):
                raise ValueError("Cannot guarantee private extraction folder")
            for name in FILES:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_NOFOLLOW, 0o600, dir_fd=childfd)
                created.append(name)
                with os.fdopen(fd, "wb") as output:
                    output.write(files[name])
                    output.flush()
                    os.fsync(output.fileno())
            os.fsync(childfd)
        except BaseException:
            if childfd is not None:
                for name in created:
                    os.unlink(name, dir_fd=childfd)
            os.rmdir(destination.name, dir_fd=parentfd)
            raise
        finally:
            if childfd is not None:
                os.close(childfd)
        os.fsync(parentfd)
    finally:
        os.close(parentfd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True,
                        help="One previously generated private issue continuity ZIP")
    parser.add_argument("--inspect-issue", help="Canonical issue URL in saved source")
    parser.add_argument("--limit", type=int, default=20,
                        help="Maximum printed issue groups (1..50)")
    parser.add_argument("--extract-to", type=Path,
                        help="New private directory for original HTML/Markdown/CSV/JSON")
    args = parser.parse_args(argv)
    try:
        if (not 1 <= args.limit <= 50 or
                (args.inspect_issue is not None and
                 not canonical_issue(args.inspect_issue))):
            raise ValueError("Invalid issue selection")
        files, manifest, report, issues, samples = read_issue_dossier(args.input)
        if args.inspect_issue:
            issues = [row for row in issues
                      if row["issue_url"] == args.inspect_issue]
            if len(issues) != 1:
                raise ValueError("Selected issue absent from this bounded ZIP")
        print("zSentinel PRIVATE historic issue dossier (not live).")
        print("Original source observations: " + str(len(samples)))
        print("Original manifest identities: " +
              ", ".join(s["original_review_manifest_sha256"][:12]
                        for s in samples))
        print(source_recency_summary(samples[-1].get("latest_observed_utc")))
        print("Human issue groups shown: " + str(min(len(issues), args.limit)) +
              " of " + str(len(issues)) + " in this saved selection")
        for issue in issues[:args.limit]:
            print(issue["highest_source_priority"] + " " + issue["issue_url"] +
                  " / historical existing PR candidates " +
                  str(len(issue["linked_existing_prs"])))
            print("  " + str(issue.get("triage_focus", "Recheck original issue")))
            for candidate in issue["linked_existing_prs"][:5]:
                print("    " + candidate["pr_url"] + " / issue-link " +
                      candidate["issue_link_priority"] + " / HEAD " +
                      (candidate["observed_head"] or "UNKNOWN"))
            if len(issue["linked_existing_prs"]) > 5:
                print("    Further saved PR references omitted, NOT resolved")
        print("CURRENT OWNER / REQUIRED CI / REVIEW / MAIN / DEPLOY: UNKNOWN")
        if args.extract_to is not None:
            extract_private(files, args.extract_to)
            print("Original validated issue dossier extracted to a NEW private "
                  "directory. No external writes or approvals.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, BadZipFile,
            OverflowError) as exc:
        print("Refused unsafe or incomplete issue archive: " +
              type(exc).__name__, file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
