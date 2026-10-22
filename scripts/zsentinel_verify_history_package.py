#!/usr/bin/env python3
"""Inspect a private zSentinel history handoff ZIP and its internal provenance.

No source collection, QA suite, network, worker claim, merge or deploy.
Checksums detect accidental alteration inside the saved package; they do NOT
authenticate the original source or authorize production recovery.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import re
import stat
import sys
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from zsentinel_product_delivery_board import unique

PACKAGE = re.compile(r"^[a-z][a-z0-9_-]{0,50}\.index\.zip$")
HEX = re.compile(r"^[a-f0-9]{64}$")
LEGACY_MEMBERS = {"history.html", "history.json", "history.csv",
                  "README.txt", "manifest.json"}
MEMBERS = LEGACY_MEMBERS | {"history-prs.csv"}
MAX_ARCHIVE = 4 * 1024 * 1024
MAX_MEMBER = 1024 * 1024


def private_archive_bytes(path: Path) -> bytes:
    """Refuse symlinks, unsafe permissions and changed private file identity."""
    if not PACKAGE.fullmatch(path.name):
        raise ValueError("unexpected history-package filename")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("private no-follow inspection unavailable")
    directory = path.parent
    before_dir = directory.lstat()
    if (not stat.S_ISDIR(before_dir.st_mode)
            or before_dir.st_uid != os.geteuid()
            or stat.S_IMODE(before_dir.st_mode) & 0o077):
        raise ValueError("history parent is not an owned private directory")
    directory_fd = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        actual_dir = os.fstat(directory_fd)
        if (actual_dir.st_dev, actual_dir.st_ino) != (
                before_dir.st_dev, before_dir.st_ino):
            raise ValueError("history directory changed")
        before = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or not 1 <= before.st_size <= MAX_ARCHIVE):
            raise ValueError("history package is not a bounded private file")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW |
                     getattr(os, "O_NONBLOCK", 0), dir_fd=directory_fd)
        try:
            current = os.fstat(fd)
            if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("history source inode changed")
            with os.fdopen(os.dup(fd), "rb") as stream:
                raw = stream.read(MAX_ARCHIVE + 1)
            after = os.fstat(fd)
            if (len(raw) != before.st_size
                    or (after.st_size, after.st_mtime_ns) !=
                       (before.st_size, before.st_mtime_ns)
                    or len(raw) > MAX_ARCHIVE):
                raise ValueError("history source modified during read")
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)


def _json(data: bytes) -> dict:
    document = json.loads(
        data.decode("utf-8"), object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("invalid non-finite JSON value")))
    if type(document) is not dict:
        raise ValueError("source JSON must be an object")
    return document


def inspect_history_bytes(archive: bytes) -> dict:
    """Verify internal JSON/CSV/manifest binding of these exact bytes."""
    if type(archive) is not bytes or not 1 <= len(archive) <= MAX_ARCHIVE:
        raise ValueError("invalid archive bytes")
    with ZipFile(BytesIO(archive)) as package:
        info = package.infolist()
        filenames = {member.filename for member in info}
        if (len(info) != len(filenames) or filenames not in
                (LEGACY_MEMBERS, MEMBERS)):
            raise ValueError("history ZIP has missing, duplicate or extra entries")
        has_pr_history = "history-prs.csv" in filenames
        parts = {}
        for member in info:
            mode = member.external_attr >> 16
            if (member.compress_type != ZIP_DEFLATED
                    or stat.S_IFMT(mode) != stat.S_IFREG
                    or stat.S_IMODE(mode) != 0o600
                    or not 1 <= member.file_size <= MAX_MEMBER):
                raise ValueError("invalid saved history ZIP member")
            parts[member.filename] = package.read(member)
            if len(parts[member.filename]) != member.file_size:
                raise ValueError("truncated history ZIP member")
        if sum(len(data) for data in parts.values()) > 4 * MAX_MEMBER:
            raise ValueError("unbounded history ZIP expansion")

    manifest = _json(parts["manifest.json"])
    report = _json(parts["history.json"])
    schema = ("zsentinel-private-recovery-history-package-v2"
              if has_pr_history else
              "zsentinel-private-recovery-history-package-v1")
    if (manifest.get("schema") != schema
            or report.get("schema") != "zsentinel-saved-bundle-history-v1"
            or manifest.get("source") != "verified_saved_operator_bundles_not_live"
            or manifest.get("generated_at_local_utc") !=
               report.get("generated_at_local_utc")
            or any(manifest.get(key) is not False for key in (
                "current_claim_verified", "required_ci_review_verified",
                "merge_authorized", "deploy_authorized", "live_verified"))
            or manifest.get("historical_sample_only") is not True
            or report.get("current_live_source_checked") is not False
            or report.get("merge_authorized") is not False
            or report.get("deploy_authorized") is not False):
        raise ValueError("historical release authority contract mismatch")
    expected_digests = {
        name: hashlib.sha256(parts[name]).hexdigest()
        for name in filenames - {"manifest.json"}
    }
    if manifest.get("members_sha256") != expected_digests:
        raise ValueError("saved history member digest mismatch")
    snapshots = report.get("snapshots")
    changes = report.get("sample_transitions")
    if (type(snapshots) is not list or not 2 <= len(snapshots) <= 24
            or type(changes) is not list or len(changes) != len(snapshots) - 1):
        raise ValueError("invalid historical snapshot series")
    source_heads = {}
    expected = set()
    for snapshot in snapshots:
        stem = snapshot.get("bundle")
        sha = snapshot.get("board_sha256")
        projects = snapshot.get("projects")
        if (type(stem) is not str or stem in source_heads
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,50}", stem)
                or type(sha) is not str or HEX.fullmatch(sha) is None
                or type(projects) is not list):
            raise ValueError("invalid saved historical source identities")
        source_heads[stem] = sha
        for project in projects:
            pid = project.get("project")
            if type(pid) is not str:
                raise ValueError("invalid project snapshot")
            key = ("snapshot", stem, "", pid)
            if key in expected:
                raise ValueError("duplicate sampled project")
            expected.add(key)
    if manifest.get("saved_bundle_board_sha256") != source_heads:
        raise ValueError("source board identity does not match ZIP manifest")
    for change in changes:
        old, new = change.get("from_bundle"), change.get("to_bundle")
        if old not in source_heads or new not in source_heads:
            raise ValueError("unbound historical source transition")
        for project in change.get("projects", []):
            pid = project.get("project")
            if type(pid) is not str:
                raise ValueError("invalid transition project")
            key = ("sample_change", new, old, pid)
            if key in expected:
                raise ValueError("duplicate sampled source transition")
            expected.add(key)

    csv_text = parts["history.csv"].decode("utf-8-sig")
    if not parts["history.csv"].startswith(b"\xef\xbb\xbf"):
        raise ValueError("missing spreadsheet UTF-8 BOM")
    reader = csv.DictReader(StringIO(csv_text), delimiter=";")
    required = ("record_kind", "source_bundle", "previous_bundle",
                "board_sha256", "project", "historical_sample_only",
                "current_claim_verified", "merge_authorized", "live_verified")
    if any(name not in (reader.fieldnames or []) for name in required):
        raise ValueError("CSV history provenance columns are missing")
    observed = set()
    for row in reader:
        key = (row["record_kind"], row["source_bundle"],
               row["previous_bundle"], row["project"])
        if key not in expected or key in observed:
            raise ValueError("history CSV contains missing/foreign project row")
        if (row["board_sha256"] != source_heads[key[1]]
                or row["historical_sample_only"] != "true"
                or any(row[field] != "false" for field in (
                    "current_claim_verified", "merge_authorized",
                    "live_verified"))):
            raise ValueError("CSV record misstates historical provenance")
        observed.add(key)
    if observed != expected:
        raise ValueError("history CSV project coverage incomplete")

    # Row identity alone misses contradictory numbers or human guidance in a
    # spreadsheet. Rebuild the *entire* canonical CSV from this exact saved
    # source JSON, including the UTF-8 BOM, headers and every column.
    # Deliberately do not reread GitHub, Notion, queue or original bundles.
    from zsentinel_private_bundle_index import render_csv_history
    canonical_csv = render_csv_history(
        [], report.get("index_name", ""),
        machine_text=parts["history.json"].decode("utf-8"),
    ).encode("utf-8")
    if canonical_csv != parts["history.csv"]:
        raise ValueError("CSV values do not match the saved machine history")
    if has_pr_history:
        from zsentinel_private_bundle_index import render_history_pr_csv
        expected_pr_csv = render_history_pr_csv(
            [], report.get("index_name", ""),
            machine_text=parts["history.json"].decode("utf-8"),
        ).encode("utf-8")
        if expected_pr_csv != parts["history-prs.csv"]:
            raise ValueError("PR gate CSV values disagree with saved JSON")
    return {
        "schema": "zsentinel-private-recovery-history-inspection-v1",
        "package_sha256": hashlib.sha256(archive).hexdigest(),
        "source_bundle_count": len(snapshots),
        "project_snapshot_and_change_rows": len(expected),
        "saved_checksum_consistent": True,
        "all_csv_values_rebuilt_from_saved_json": True,
        "pr_gate_timeline_included": has_pr_history,
        "source_authenticity_independently_verified": False,
        "current_claim_verified": False,
        "required_ci_review_verified": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "live_verified": False,
    }


def historical_source_age(report: dict, *, as_of=None,
                          stale_after_hours: int = 6) -> dict:
    """Show the age of the actual source inputs, not just the exported board.

    A newly generated board may contain older GitHub, claim and queue sources.
    Treat the oldest input of the latest saved board as the effective age.
    Previously captured 'fresh=False' is never upgraded to fresh on export.
    No source is fetched and no live recovery authority can be inferred.
    """
    from datetime import datetime, timezone, timedelta

    if type(stale_after_hours) is not int or not 1 <= stale_after_hours <= 168:
        raise ValueError("invalid historical source age threshold")
    observed_at = as_of if as_of is not None else datetime.now(timezone.utc)
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise ValueError("historical age clock must be timezone-aware")
    observed_at = observed_at.astimezone(timezone.utc)
    snapshots = report.get("snapshots")
    if type(snapshots) is not list or not 2 <= len(snapshots) <= 24:
        raise ValueError("invalid history source series")

    def parsed_stamp(value):
        if type(value) is not str or not 1 <= len(value) <= 50:
            raise ValueError("invalid original source time")
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("original source time is not UTC-aware")
        return stamp.astimezone(timezone.utc)

    boards = [(parsed_stamp(row.get("saved_board_observed_at_utc")),
               row.get("bundle"), row) for row in snapshots]
    if any(type(stem) is not str for _, stem, _ in boards):
        raise ValueError("invalid original bundle identity")
    latest_board_time, _, latest = max(boards, key=lambda row: (row[0], row[1]))
    clocks = latest.get("saved_individual_source_clocks")
    captured_freshness = latest.get(
        "saved_individual_source_freshness_at_capture")
    allowed = {"inbox", "retry", "branches", "claims", "prs"}
    if (type(clocks) is not dict or type(captured_freshness) is not dict
            or not clocks or "inbox" not in clocks or set(clocks) !=
            set(captured_freshness) or not set(clocks) <= allowed
            or any(type(flag) is not bool
                   for flag in captured_freshness.values())):
        raise ValueError("latest original source clocks/freshness incomplete")
    inputs = {kind: parsed_stamp(clock) for kind, clock in clocks.items()}
    if any(stamp > latest_board_time for stamp in inputs.values()):
        raise ValueError("original source clocks exceed their captured board")
    oldest_input = min(inputs.values())
    newest_input = max(inputs.values())
    board_age_seconds = (observed_at - latest_board_time).total_seconds()
    oldest_input_age_seconds = (observed_at - oldest_input).total_seconds()
    suspected_future = (
        latest_board_time > observed_at + timedelta(minutes=2)
        or any(stamp > observed_at + timedelta(minutes=2)
               for stamp in inputs.values())
    )
    stale_input_kinds = sorted(
        kind for kind, stamp in inputs.items()
        if not captured_freshness[kind] or
        (observed_at - stamp).total_seconds() > stale_after_hours * 3600
    )
    status = (
        "future_clock_unknown" if suspected_future else
        "stale_historical_sample" if stale_input_kinds else
        "recent_historical_sample_not_live"
    )
    return {
        # Backwards-compatible board timeline fields, explicitly labelled as
        # board times. Do not misread these as the newest collected GitHub data.
        "source_observation_oldest_utc":
            min(item[0] for item in boards).isoformat(),
        "source_observation_newest_utc": latest_board_time.isoformat(),
        "source_age_hours": round(oldest_input_age_seconds / 3600, 2),
        "latest_board_age_hours": round(board_age_seconds / 3600, 2),
        "latest_bundle_evidence_oldest_utc": oldest_input.isoformat(),
        "latest_bundle_evidence_newest_utc": newest_input.isoformat(),
        "latest_bundle_source_clocks_utc": {
            kind: stamp.isoformat() for kind, stamp in sorted(inputs.items())
        },
        "source_freshness_at_capture": dict(sorted(captured_freshness.items())),
        "source_age_hours_by_kind": {
            kind: round((observed_at - stamp).total_seconds() / 3600, 2)
            for kind, stamp in sorted(inputs.items())
        },
        "stale_or_not_fresh_at_capture_sources": stale_input_kinds,
        "stale_after_hours": stale_after_hours,
        "source_age_status": status,
        "historical_even_when_recent": True,
        "source_is_live_verified": False,
    }


def verify_against_original_bundles(
        archive: bytes, originals_directory: Path) -> dict:
    """Recompute history from complete *original* verified private bundles.

    This upgrades portable package checksum matching into local original-
    source matching, but it is not an authenticated external timestamp,
    current GitHub/Notion status, ownership proof or release authorization.
    """
    inspect_history_bytes(archive)
    from zsentinel_private_bundle_index import load_snapshot, render_machine

    with ZipFile(BytesIO(archive)) as package:
        report = _json(package.read("history.json"))
    if type(report.get("index_name")) is not str or not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,50}", report["index_name"]):
        raise ValueError("invalid private history index identity")
    sources = report.get("snapshots")
    if type(sources) is not list or not 2 <= len(sources) <= 24:
        raise ValueError("invalid private history source inventory")

    stems = []
    seen = set()
    source_hashes = {}
    for row in sources:
        if type(row) is not dict:
            raise ValueError("malformed saved history source")
        stem, digest = row.get("bundle"), row.get("board_sha256")
        if (type(stem) is not str or not re.fullmatch(
                r"[a-z][a-z0-9_-]{0,50}", stem)
                or stem in seen or type(digest) is not str
                or HEX.fullmatch(digest) is None):
            raise ValueError("invalid original bundle identifier")
        stems.append(stem)
        seen.add(stem)
        source_hashes[stem] = digest

    # load_snapshot calls the existing complete bundle verifier and compares
    # the original board bytes to its own receipt (including 0600/0700
    # permissions, no-follow paths, HTML/owner source hash binding).
    originals = [load_snapshot(originals_directory, stem) for stem in stems]
    if any(snap["sha256"] != source_hashes[snap["stem"]]
           for snap in originals):
        raise ValueError("original private board SHA differs from package")

    rebuilt = _json(render_machine(
        originals, report["index_name"]).encode("utf-8"))
    # Created-at index and computed ages cannot be reproduced later.
    # Everything that was actually captured from original saved source
    # bundles, including claims/queue, prior PRs and sample transitions,
    # must otherwise match the submitted handoff exactly.
    def invariant_snapshots(value):
        rows = value.get("snapshots")
        if type(rows) is not list:
            raise ValueError("history is missing source snapshots")
        result = []
        for row in rows:
            if type(row) is not dict:
                raise ValueError("malformed source snapshot")
            item = dict(row)
            item.pop("snapshot_age_seconds_at_index", None)
            item.pop("future_timestamp_suspected", None)
            result.append(item)
        return result

    if (invariant_snapshots(report) != invariant_snapshots(rebuilt)
            or report.get("sample_transitions") !=
               rebuilt.get("sample_transitions")):
        raise ValueError("historical content differs from original saved sources")

    return {
        "original_private_bundle_files_reverified": True,
        "original_private_bundle_count": len(originals),
        "original_bundle_board_sha256": source_hashes,
        "historical_snapshot_content_matches_originals": True,
        "historical_transition_content_matches_originals": True,
        "local_receipt_consistency_only": True,
        "independent_origin_authenticity_verified": False,
        "current_github_or_notion_verified": False,
        "claim_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "live_verified": False,
    }


def private_owner_brief(report: dict, *, stale_after_hours: int = 6,
                        project: str | None = None) -> str:
    """Source-scoped human owner decisions from the latest saved history.

    Not a current backlog, CI/review gate, claim inventory or release order.
    """
    if type(report.get("snapshots")) is not list or not report["snapshots"]:
        raise ValueError("missing saved historical snapshots")
    age = historical_source_age(report, stale_after_hours=stale_after_hours)
    latest = max(
        report["snapshots"],
        key=lambda row: (row["saved_board_observed_at_utc"], row["bundle"]),
    )
    projects = {row["project"]: row for row in latest["projects"]}
    attention = latest.get("historical_attention_order")
    if project is not None:
        if (type(project) is not str or re.fullmatch(
                r"Zennay/[A-Za-z0-9_.-]{1,100}", project) is None
                or project not in projects):
            raise ValueError("selected project absent from latest saved source")
        attention = [row for row in attention if row["project"] == project]
    if type(attention) is not list or len(attention) > 40:
        raise ValueError("invalid historical priority list")

    def safe(value):
        if value is None:
            return "onbekend"
        if type(value) is not str:
            value = str(value)
        value = value.replace("\r", " ").replace("\n", " ")[:400]
        for char in ("\\", "`", "*", "_", "[", "]", "(", ")", "<", ">",
                     "|", "#", "!"):
            value = value.replace(char, "\\" + char)
        return value

    lines = [
        "# zSentinel · privé eigenaarsoverdracht uit opgeslagen bron",
        "",
        "**HISTORISCHE STEEKPROEF — GEEN LIVE HERSTEL-/MERGEOPDRACHT.**",
        "",
        "Origineel opgeslagen bundelmoment: " +
            safe(latest["saved_board_observed_at_utc"]) + ".",
        "Historische bronleeftijd: " + safe(age["source_age_hours"]) +
            " uur; status: " + safe(age["source_age_status"]) + ".",
        "Bronbundel: `" + safe(latest["bundle"]) + "`.",
        "Bronboard SHA-256: `" + safe(latest["board_sha256"]) + "`.",
        "",
        "Projectselectie: " + safe(project or "alle opgeslagen projecten") + ".",
        "",
        "## Historische projectprioriteiten",
        "",
        "Onderstaande volgorde volgt de opgeslagen P0-, verlopen lease- en",
        "P1-steekproeven. Verkeerde of ontbrekende brondekking blijft onbekend.",
        "",
    ]
    for rank, item in enumerate(attention[:24], start=1):
        project = item["project"]
        raw = projects.get(project)
        if type(raw) is not dict:
            raise ValueError("attention entry not bound to saved project")
        op = raw.get("saved_project_operations") or {}
        queue = op.get("queue_observation")
        if type(queue) is not dict:
            queue = None
        lines.extend([
            "### " + str(rank) + ". " + safe(project),
            "",
            "- P0/P1 in opgeslagen steekproef: " +
                safe(item["sampled_p0"]) + " / " + safe(item["sampled_p1"]),
            "- Verlopen leases bij opname: " +
                safe(queue.get("expired_active_leases") if queue else None) +
                "; hoge claimpogingen: " +
                safe(queue.get("high_claim_attempts") if queue else None),
            "- Dekking: " + safe(op.get("issue_coverage")) +
                "; claimcontext bij opname: " +
                safe(op.get("claim_context")),
            "- Historisch eigenaaradvies: " +
                safe(op.get("recommended_next_step")),
            "- **Eigenaaractie vóór uitvoering:** controleer het huidige issue,"
                " open PR's, exacte HEAD, actieve claims, verplichte CI, review"
                " en bevoegdheid opnieuw.",
            "",
        ])
    prs = latest.get("saved_pr_observations")
    if type(prs) is not list or len(prs) > 5:
        raise ValueError("unbounded saved merge-first PR observations")
    if project is not None:
        prs = [pr for pr in prs
               if pr["url"].startswith("https://github.com/" + project + "/pull/")]
    lines.extend([
        "## Historische merge-first kandidaten",
        "",
        "Opgenomen PR's waren waargenomen bij deze eerdere bron; niet",
        "waargenomen PR's zijn onbekend, niet bewezen gesloten of gemerged.",
        "",
    ])
    for pr in sorted(prs, key=lambda x: x["url"]):
        lines.extend([
            "- " + safe(pr["url"]) + " — HEAD `" +
                safe(pr["head_sha"][:12]) + "`; check bij opname " +
                safe(pr["observed_check_success"]) +
                "; approvals bij opname " + safe(pr["observed_approvals"]) +
                "; onbekende gates " + safe(len(pr["blocking_unknowns"])) + ".",
        ])
    if not prs:
        lines.append("- Geen PR-gatewaarnemingen in deze begrensde bron.")
    lines.extend([
        "",
        "## Vereiste actuele menselijke controle",
        "",
        "1. Haal actuele issues en PR-heads rechtstreeks uit GitHub.",
        "2. Controleer huidige Notion/queue-claims en eigenaarschap.",
        "3. Controleer exacte-head verplichte CI, onafhankelijke review en",
        "   branch protection voordat een bevoegde merge mogelijk is.",
        "4. Voor release: controleer aparte VPS-/apparaat-/securitygates.",
        "",
        "Deze overdracht claimt geen toestemming of live herstel.",
        "Geen queueclaim, CI-dispatch, review, merge, restart of deploy.",
        "",
    ])
    result = "\n".join(lines)
    if not 1 <= len(result.encode("utf-8")) <= 1024 * 1024:
        raise ValueError("private owner summary exceeds size limit")
    return result


def inspect_history_package(path: Path) -> dict:
    """Verify one existing private history package without changing it."""
    return inspect_history_bytes(private_archive_bytes(path))


def extract_verified_history(archive: bytes, destination: Path,
                             basename: str) -> list[str]:
    """Install five reviewed, inert data files into an owned 0700 directory.

    The caller MUST inspect these exact bytes first. Create-only destination
    names are derived from the canonical package basename, not ZIP metadata.
    Every file remains private, the original ZIP and source bundles untouched.
    """
    if not PACKAGE.fullmatch(basename + ".index.zip"):
        raise ValueError("invalid private export basename")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("no-follow private extraction is unavailable")
    # Inspect the same byte sequence we are about to publish. This is a
    # read-model/content-boundary check, not a QA or production action.
    inspect_history_bytes(archive)
    outputs = (
        ("history.html", basename + ".index.html"),
        ("history.json", basename + ".index.json"),
        ("history.csv", basename + ".index.csv"),
        ("history-prs.csv", basename + ".index.prs.csv"),
        ("README.txt", basename + ".index.README.txt"),
        ("manifest.json", basename + ".index.manifest.json"),
    )
    prior = destination.lstat()
    if (not stat.S_ISDIR(prior.st_mode)
            or prior.st_uid != os.geteuid()
            or stat.S_IMODE(prior.st_mode) != 0o700):
        raise ValueError("extraction requires an existing owned 0700 directory")
    directory_fd = os.open(
        destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created = []
    try:
        current = os.fstat(directory_fd)
        if ((prior.st_dev, prior.st_ino) != (current.st_dev, current.st_ino)
                or current.st_uid != os.geteuid()
                or stat.S_IMODE(current.st_mode) != 0o700):
            raise ValueError("private extraction directory changed")
        with ZipFile(BytesIO(archive)) as package:
            # Named, validated and bounded member access. Never use
            # ZipFile.extract or names/paths supplied by an archive entry.
            archived = set(package.namelist())
            values = [(filename, package.read(member))
                      for member, filename in outputs if member in archived]
        for _, filename in values:
            try:
                os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise FileExistsError("create-only extracted history already exists")
        for filename, data in values:
            fd = os.open(
                filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600, dir_fd=directory_fd)
            created.append((filename, os.fstat(fd).st_ino))
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        os.fsync(directory_fd)
    except BaseException:
        # Only remove files we created and that retain the exact inode.
        # Never delete a caller's pre-existing file or a swapped pathname.
        for filename, inode in reversed(created):
            try:
                found = os.stat(
                    filename, dir_fd=directory_fd, follow_symlinks=False)
                if (stat.S_ISREG(found.st_mode) and found.st_ino == inode):
                    os.unlink(filename, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        os.close(directory_fd)
    return [filename for filename, _ in values]




def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="Existing privately stored .index.zip package")
    parser.add_argument("--extract-to", type=Path,
                        help="Optional existing owned 0700 folder for versioned "
                             "create-only private companion files")
    parser.add_argument("--verify-sources", type=Path,
                        help="Recheck the complete original private operator "
                             "bundles and compare saved content, not just ZIP hashes")
    parser.add_argument("--stale-after-hours", type=int, default=6,
                        help="Mark old observation time explicitly, 1–168 hours")
    parser.add_argument("--owner-brief-output", type=Path,
                        help="Create one owner-private Markdown worklist from "
                             "verified saved source data; never print source notes")
    parser.add_argument("--owner-brief-project",
                        help="Restrict owner brief to one canonical Zennay/project "
                             "seen in the latest original source sample")
    args = parser.parse_args(argv)
    try:
        archive = private_archive_bytes(args.source)
        result = inspect_history_bytes(archive)
        with ZipFile(BytesIO(archive)) as saved:
            age_report = _json(saved.read("history.json"))
        result.update(historical_source_age(
            age_report, stale_after_hours=args.stale_after_hours))
        if args.owner_brief_project is not None and args.owner_brief_output is None:
            raise ValueError("project filter requires private owner brief output")
        if args.owner_brief_output is not None and args.extract_to is not None:
            raise ValueError("create owner brief or extract files per operation")
        if args.verify_sources is not None:
            result.update(verify_against_original_bundles(
                archive, args.verify_sources))
        else:
            result["original_private_bundle_files_reverified"] = False
        if args.extract_to is not None:
            stem = args.source.name[:-len(".index.zip")]
            created = extract_verified_history(
                archive, args.extract_to, stem)
            result["private_extracted_files"] = created
            result["private_extraction_only"] = True
        if args.owner_brief_output is not None:
            if args.owner_brief_output.suffix.lower() != ".md":
                raise ValueError("private owner brief must end in .md")
            if args.owner_brief_output.resolve() == args.source.resolve():
                raise ValueError("cannot replace the source archive")
            from zsentinel_product_delivery_board import write_create_only
            write_create_only(args.owner_brief_output, private_owner_brief(
                age_report, stale_after_hours=args.stale_after_hours,
                project=args.owner_brief_project))
            result["owner_private_brief_written"] = True
            result["owner_private_brief_only_from_saved_sources"] = True
    except (OSError, ValueError, KeyError, TypeError, UnicodeError,
            BadZipFile, OverflowError, csv.Error) as exc:
        print("History package inspection refused: " +
              type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
