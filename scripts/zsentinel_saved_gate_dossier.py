#!/usr/bin/env python3
"""Open an existing private zSentinel project handoff without contacting services.

This is a user-facing offline delivery reader, not a QA tool, worker, CI
gate, claim verifier, merge authority or proof of GitHub provenance.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import sys
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED

FILES = ("project-dossier.html", "project-actions.md",
         "project-prs.csv", "source-receipt.json", "source-checksums.json")
MAX_ARCHIVE = 1024 * 1024
MAX_MEMBER = MAX_ARCHIVE
MAX_EXPANDED = 4 * MAX_ARCHIVE
SHA = re.compile(r"[a-f0-9]{64}\Z")
PROJECT = re.compile(r"Zennay/[A-Za-z0-9_.-]{1,100}\Z")

PR_URL = re.compile(r"https://github\.com/(Zennay/[A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,8})\Z")
ISSUE_URL = re.compile(r"https://github\.com/Zennay/[A-Za-z0-9_.-]{1,100}/issues/[1-9][0-9]{0,8}\Z")
PRIORITY = ("P0", "P1", "P2", "Unspecified")
CATEGORIES = {"source", "failed", "pending", "changes", "partial", "review", "policy"}
SOURCES = {"missing_batch", "source_unavailable_or_head_changed", "observed_partial_gate_receipt"}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON source field")
        result[key] = value
    return result


def strict_json(raw: bytes):
    obj = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=unique_pairs,
                     parse_constant=lambda _: (_ for _ in ()).throw(
                         ValueError("Nonfinite source number")))
    if type(obj) is not dict:
        raise ValueError("JSON source must be an object")
    return obj


def read_archive(path: Path):
    """Read one private archived handoff from pinned no-follow descriptors.

    Refuse symlink ancestors, mutable shared parents and foreign/public input.
    An internally consistent ZIP is not an authenticated GitHub source.
    """
    if not all(hasattr(os, flag) for flag in
               ("O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY")):
        raise ValueError("Required local file safety is unavailable")
    if not path.name or path.name in (".", ".."):
        raise ValueError("Invalid private archive name")

    parentfd = _open_private_extract_parent(path.parent)
    try:
        first = os.stat(path.name, dir_fd=parentfd, follow_symlinks=False)
        if (not stat.S_ISREG(first.st_mode)
                or first.st_uid != os.geteuid()
                or stat.S_IMODE(first.st_mode) & 0o077
                or not 100 <= first.st_size <= MAX_ARCHIVE):
            raise ValueError("Source is not an owned private bounded archive")

        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW |
                     os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                     dir_fd=parentfd)
        try:
            before = os.fstat(fd)
            identity = (first.st_dev, first.st_ino, first.st_size,
                        first.st_mtime_ns, first.st_ctime_ns)
            observed = (before.st_dev, before.st_ino, before.st_size,
                        before.st_mtime_ns, before.st_ctime_ns)
            if (not stat.S_ISREG(before.st_mode)
                    or observed != identity
                    or before.st_uid != os.geteuid()
                    or stat.S_IMODE(before.st_mode) & 0o077):
                raise ValueError("Private archive changed during safe open")
            with os.fdopen(os.dup(fd), "rb") as source:
                raw = source.read(MAX_ARCHIVE + 1)
            after = os.fstat(fd)
            final = os.stat(path.name, dir_fd=parentfd, follow_symlinks=False)
            end = (after.st_dev, after.st_ino, after.st_size,
                   after.st_mtime_ns, after.st_ctime_ns)
            path_end = (final.st_dev, final.st_ino, final.st_size,
                        final.st_mtime_ns, final.st_ctime_ns)
            if (len(raw) != first.st_size or end != identity
                    or path_end != identity or not stat.S_ISREG(final.st_mode)
                    or final.st_uid != os.geteuid()
                    or stat.S_IMODE(final.st_mode) & 0o077):
                raise ValueError("Private archive replaced or modified during read")
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(parentfd)


def open_dossier(path: Path):
    """Return validated archive payloads; hashes prove consistency, not author."""
    payload = read_archive(path)
    with ZipFile(BytesIO(payload), mode="r") as archive:
        infos = archive.infolist()
        if len(infos) != len(FILES) or {i.filename for i in infos} != set(FILES):
            raise ValueError("Unexpected or duplicate project ZIP members")
        if sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise ValueError("Expanded project dossier exceeds budget")
        extracted = {}
        for info in infos:
            if (info.filename not in FILES or info.is_dir()
                    or info.flag_bits & 0x1 or info.compress_type != ZIP_DEFLATED
                    or not 1 <= info.file_size <= MAX_MEMBER
                    or not 0 <= info.compress_size <= MAX_ARCHIVE):
                raise ValueError("Unsafe ZIP member type or limits")
            # Only regular Unix files are emitted by the package builder.
            if info.create_system != 3 or (info.external_attr >> 16) != 0o100600:
                raise ValueError("Noncanonical ZIP member permissions")
            with archive.open(info, "r") as stream:
                data = stream.read(MAX_MEMBER + 1)
            if len(data) != info.file_size:
                raise ValueError("Archive member changed or exceeded limit")
            extracted[info.filename] = data

    manifest = strict_json(extracted["source-checksums.json"])
    receipt = strict_json(extracted["source-receipt.json"])
    if (manifest.get("schema") != "zsentinel-private-project-dossier-zip-v1"
            or manifest.get("source") != "offline_saved_manifest_and_gate_batches_not_live"
            or receipt.get("schema") != "zsentinel-private-portfolio-work-suggestions-v1"
            or receipt.get("source") != "saved_exact_manifest_gate_batches_not_live"
            or type(manifest.get("files_sha256")) is not dict
            or set(manifest["files_sha256"]) != set(FILES[:-1])):
        raise ValueError("Unrecognized or incomplete source-bound project dossier")
    for name in FILES[:-1]:
        checksum = manifest["files_sha256"][name]
        if type(checksum) is not str or SHA.fullmatch(checksum) is None:
            raise ValueError("Invalid dossier member SHA256")
        if hashlib.sha256(extracted[name]).hexdigest() != checksum:
            raise ValueError("Private project handoff member checksum mismatch")
    for key in ("original_review_manifest_sha256", "original_board_sha256"):
        value = manifest.get(key)
        if (type(value) is not str or SHA.fullmatch(value) is None
                or value != receipt.get(key)):
            raise ValueError("Original source identity is inconsistent")
    for key in ("earliest_observed_at_utc", "latest_observed_at_utc",
                "missing_batch_indexes", "selected_project_filter"):
        if manifest.get(key) != receipt.get(key if key !=
            "earliest_observed_at_utc" else "oldest_observed_at_utc") and not (
            key == "latest_observed_at_utc" and manifest.get(key) ==
            receipt.get("latest_observed_at_utc")):
            raise ValueError("Conflicting private source coverage")
    project = manifest["selected_project_filter"]
    if project is not None and (type(project) is not str
                                or PROJECT.fullmatch(project) is None):
        raise ValueError("Invalid project handoff scope")
    if any(manifest.get(flag) is not False for flag in
           ("claim_authorized", "worker_dispatch_authorized",
            "required_ci_review_verified", "merge_authorized",
            "deploy_authorized", "live_verified")):
        raise ValueError("Project archive claims release authority")
    if any(receipt.get(flag) is not False for flag in
           ("known_complete_project_or_pr_inventory",
            "owner_claim_verified", "required_exact_head_ci_verified",
            "independent_reviews_and_branch_rules_verified",
            "queue_mutation_authorized", "merge_authorized", "deploy_authorized",
            "worker_dispatch_authorized", "worker_recovery_verified",
            "writes_performed", "live_verified")):
        raise ValueError("Project source claims worker/release authority")
    if manifest.get("read_only") is not True:
        raise ValueError("Private source archive must be explicitly read-only")
    # The bundle only contains its own selected source scope. A source-neutral
    # archive may contain multiple repos, but a project-specific one must not.
    projects = receipt.get("projects")
    if (type(projects) is not list or not projects
            or type(receipt.get("filtered_pr_candidates")) is not int
            or not 1 <= receipt["filtered_pr_candidates"] <= 300
            or type(receipt.get("original_source_pr_candidates")) is not int
            or not 1 <= receipt["original_source_pr_candidates"] <= 300
            or receipt["filtered_pr_candidates"] >
               receipt["original_source_pr_candidates"]):
        raise ValueError("Invalid project inventory in private ZIP")
    if project is not None and (len(projects) != 1
                                or type(projects[0]) is not dict
                                or projects[0].get("repository") != project):
        raise ValueError("Project-specific ZIP contains foreign project rows")
    saved_queue(receipt)
    return extracted, manifest, receipt


def _open_private_extract_parent(directory: Path) -> int:
    """Pin every ancestor descriptor; reject symlink and shared-write detours.

    The final destination parent must belong to this operator, not merely
    resolve to a writable path supplied by an untrusted archive or symlink.
    No directories are created by this admission function.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    absolute = Path(os.path.abspath(os.fspath(directory)))
    if not absolute.is_absolute() or absolute.parts[0] != os.sep:
        raise ValueError("Invalid private extract parent")
    descriptor = os.open(os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            before = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode) or before.st_mode & 0o022:
                raise ValueError("Unsafe private extract parent component")
            nextfd = os.open(component, flags, dir_fd=descriptor)
            try:
                opened = os.fstat(nextfd)
                if (not stat.S_ISDIR(opened.st_mode)
                        or (opened.st_dev, opened.st_ino) !=
                           (before.st_dev, before.st_ino)
                        or opened.st_mode & 0o022):
                    raise ValueError("Private extract parent identity changed")
            except BaseException:
                os.close(nextfd)
                raise
            os.close(descriptor)
            descriptor = nextfd
        owner = os.fstat(descriptor)
        if (not stat.S_ISDIR(owner.st_mode)
                or owner.st_uid != os.geteuid()
                or owner.st_mode & 0o022):
            raise ValueError("Private extract destination parent is not owned and safe")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def extract_dossier(path: Path, destination: Path, *, expected_manifest_sha=None):
    """Create a new private directory only after fully verifying the ZIP.

    Never reuses or overwrites a prior directory, file or symlink.
    """
    files, manifest, receipt = open_dossier(path)
    if (expected_manifest_sha is not None and
            manifest["original_review_manifest_sha256"] != expected_manifest_sha):
        raise ValueError("Dossier manifest SHA256 differs from operator pin")
    # Retain the parent-directory handle for the entire extraction. Refuse
    # parent symlinks and never resolve an attacker-replaced destination path.
    if not destination.name or destination.name in (".", ".."):
        raise ValueError("Invalid private destination name")
    parentfd = _open_private_extract_parent(destination.parent)
    try:
        os.mkdir(destination.name, 0o700, dir_fd=parentfd)
        childfd = None
        created = []
        try:
            childfd = os.open(destination.name, os.O_RDONLY |
                              os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parentfd)
            created_directory = os.fstat(childfd)
            if (not stat.S_ISDIR(created_directory.st_mode)
                    or created_directory.st_uid != os.geteuid()
                    or stat.S_IMODE(created_directory.st_mode) & 0o077):
                raise ValueError("Extracted dossier directory is not private")
            for name in FILES:
                fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                             os.O_NOFOLLOW, 0o600, dir_fd=childfd)
                created.append(name)
                with os.fdopen(fd, "wb") as sink:
                    sink.write(files[name])
                    sink.flush()
                    os.fsync(sink.fileno())
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
    return manifest, receipt




def saved_queue(receipt: dict, *, project=None, priority=None):
    """Read known PR hints from verified archive, never assert current state."""
    projects = receipt.get("projects")
    if type(projects) is not list or not 1 <= len(projects) <= 100:
        raise ValueError("Invalid bounded project queue")
    if project is not None and (type(project) is not str
                                or PROJECT.fullmatch(project) is None):
        raise ValueError("Invalid requested canonical project")
    if priority is not None and priority not in PRIORITY:
        raise ValueError("Invalid requested priority")
    original_total = receipt.get("filtered_pr_candidates")
    if type(original_total) is not int or not 1 <= original_total <= 300:
        raise ValueError("Invalid original PR evidence cardinality")
    queue = []
    seen = set()
    discovered_projects = set()
    for group in projects:
        repo = group.get("repository") if type(group) is dict else None
        rows = group.get("candidates") if type(group) is dict else None
        if (type(repo) is not str or PROJECT.fullmatch(repo) is None
                or repo in discovered_projects or type(rows) is not list
                or not 1 <= len(rows) <= 300
                or group.get("source_prs_observed") != len(rows)
                or group.get("read_only") is not True
                or any(group.get(key) is not False for key in
                       ("ownership_verified", "merge_authorized", "live_verified"))):
            raise ValueError("Inconsistent sampled project inventory or authority")
        discovered_projects.add(repo)
        for row in rows:
            if type(row) is not dict:
                raise ValueError("Invalid saved candidate record")
            url, number = row.get("url"), row.get("pr_number")
            match = PR_URL.fullmatch(url) if type(url) is str else None
            issues = row.get("linked_issue_urls")
            if (match is None or match.group(1) != repo
                    or type(number) is not int or int(match.group(2)) != number
                    or url in seen or row.get("priority") not in PRIORITY
                    or row.get("primary_gate") not in CATEGORIES
                    or row.get("gate_source") not in SOURCES
                    or row.get("review_and_merge_authorized") is not False
                    or type(issues) is not list or len(issues) > 60
                    or any(type(i) is not str or
                           ISSUE_URL.fullmatch(i) is None or
                           not i.startswith("https://github.com/" + repo +
                                            "/issues/") for i in issues)
                    or len(set(issues)) != len(issues)
                    or type(row.get("next_action")) is not str
                    or not 1 <= len(row["next_action"]) <= 300
                    or any(ord(ch) < 32 or ord(ch) == 127
                           for ch in row["next_action"])):
                raise ValueError("Untrusted saved PR or issue source")
            observed = row.get("gate_observations")
            count_names = (
                "checks_completed", "checks_pending", "checks_not_successful",
                "independent_approvals_observed", "changes_requested_observed",
                "legacy_pending_observed", "legacy_failed_observed",
            )
            flag_names = (
                "checks_page_complete", "reviews_page_may_be_truncated",
                "legacy_page_may_be_truncated",
            )
            if observed is not None:
                if (type(observed) is not dict
                        or set(observed) != set(count_names + flag_names)
                        or row["gate_source"] != "observed_partial_gate_receipt"
                        or any(type(observed[k]) is not int or
                               not 0 <= observed[k] <= 100 for k in count_names)
                        or any(type(observed[k]) is not bool for k in flag_names)):
                    raise ValueError("Untrusted saved gate observation")
            seen.add(url)
            if ((project is None or repo == project)
                    and (priority is None or priority == row["priority"])):
                queue.append((repo, row))
    if len(seen) != original_total or (project is not None
                                      and project not in discovered_projects):
        raise ValueError("Saved PR count or requested project contradicts source")
    return queue


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True,
                        help="One previously generated private portfolio-zip")
    parser.add_argument("--show-queue", action="store_true",
                        help="Show human next actions for already-sampled PRs")
    parser.add_argument("--project", help="Optional canonical source project filter")
    parser.add_argument("--priority", choices=PRIORITY,
                        help="Filter printed sampled PR work, never GitHub")
    parser.add_argument("--limit", type=int, default=20,
                        help="Maximum printed PR hints (1-50, default 20)")
    parser.add_argument("--extract-to", type=Path,
                        help="New, nonexistent private directory; otherwise inspect only")
    parser.add_argument("--expect-review-sha256",
                        help="Optional trusted original manifest SHA256 to pin")
    args = parser.parse_args(argv)
    try:
        if (args.expect_review_sha256 is not None
                and SHA.fullmatch(args.expect_review_sha256) is None):
            raise ValueError("Invalid expected source SHA256")
        if (not 1 <= args.limit <= 50
                or (args.priority is not None and not args.show_queue)
                or (args.project is not None and not args.show_queue)):
            raise ValueError("Invalid read-only queue selection")
        if args.extract_to is None:
            _files, manifest, receipt = open_dossier(args.input)
        else:
            manifest, receipt = extract_dossier(
                args.input, args.extract_to,
                expected_manifest_sha=args.expect_review_sha256)
        if (args.expect_review_sha256 is not None and
                manifest["original_review_manifest_sha256"] !=
                args.expect_review_sha256):
            raise ValueError("Dossier does not match operator-pinned manifest")
        print("Source-consistent offline project dossier: " +
              str(manifest["selected_project_filter"] or "sampled portfolio"))
        print("Original review manifest SHA256: " +
              manifest["original_review_manifest_sha256"])
        print("Source-observed PRs: " + str(receipt["filtered_pr_candidates"]) +
              " / " + str(receipt["original_source_pr_candidates"]))
        print("Archive hashes are internally consistent, NOT authenticated; "
              "no current GitHub owner, CI, merge or live state is established.")
        if args.show_queue:
            ordered = saved_queue(receipt, project=args.project,
                                  priority=args.priority)
            for repo, item in ordered[:args.limit]:
                print(item["priority"] + " " + repo + "#" +
                      str(item["pr_number"]) + " [" +
                      item["primary_gate"] + ", " + item["gate_source"] +
                      "]: " + item["next_action"])
                print("  Original PR: " + item["url"])
                print("  Linked source issues: " +
                      (", ".join(item["linked_issue_urls"]) or "none observed"))
                observed = item.get("gate_observations")
                if observed is not None:
                    print("  Sampled exact-HEAD gates (NOT required CI proof): " +
                          str(observed["checks_completed"]) + " completed, " +
                          str(observed["checks_pending"]) + " pending, " +
                          str(observed["checks_not_successful"]) + " unsuccessful; " +
                          str(observed["independent_approvals_observed"]) +
                          " observed independent approvals")
            if len(ordered) > args.limit:
                print(str(len(ordered) - args.limit) +
                      " additional source-sampled PRs not displayed (NOT resolved).")
            if not ordered:
                print("No PRs in this saved filter; current GitHub coverage UNKNOWN.")
        if args.extract_to is not None:
            print("Private dossier extracted into: " + str(args.extract_to))
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError,
            BadZipFile, OverflowError, EOFError) as exc:
        print("Refused unsafe, incomplete or mismatched private dossier: " +
              str(type(exc).__name__), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
