#!/usr/bin/env python3
"""Bounded, read-only zSentinel disk-pressure workbench.

Identifies candidate large files in an explicitly selected project workspace.
Never deletes, truncates, reads file contents, follows symlinks, changes
ownership, or asserts that any reported file may safely be removed.
"""
from __future__ import annotations

import argparse
import heapq
import json
import os
import socket
import stat
import time
from collections import deque
from datetime import datetime, timezone
from html import escape
from pathlib import Path

SCHEMA = "zsentinel-disk-pressure-workbench-v1"
PRIVATE = frozenset({
    ".ssh", ".gnupg", ".aws", ".config", ".git", ".env", ".secrets",
    "secrets", "credentials", "tokens", "private_keys", ".private",
})
SENSITIVE_PARTS = ("secret", "credential", "token", "password", "api-key", "apikey", "keypair", "private")
SENSITIVE_EXT = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
MAX_FILES = 20000
MAX_DEPTH = 4
MAX_SECONDS = 5
TOP = 15
# Immutable-data naming hints, NOT evidence of ownership, coldness or archive permission.
ARCHIVE_SUFFIX_HINTS = (".hbr2", ".parquet", ".arrow", ".npz", ".safetensors",
                        ".onnx", ".webm", ".mp4", ".tar.gz", ".zip")


def _sensitive(name):
    lower = name.lower()
    return (lower in PRIVATE or lower.endswith(SENSITIVE_EXT)
            or any(word in lower for word in SENSITIVE_PARTS))


def _root(path):
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("explicit absolute project workspace path required")
    if path.is_symlink() or not path.is_dir():
        raise ValueError("existing non-symlink directory required")
    resolved = path.resolve(strict=True)
    if any(_sensitive(part) for part in resolved.parts):
        raise ValueError("private credential and secret workspaces cannot be inspected")
    # Never allow a blind scan of shared OS roots, devices or personal homes.
    if str(resolved) in ("/", "/home", "/home/ubuntu", "/tmp", "/var", "/root",
                         "/etc", "/proc", "/sys", "/dev", "/run") or len(resolved.parts) < 4:
        raise ValueError("choose a specific project workspace, not an OS root")
    return resolved



def _open_directory_beneath(root: Path, directory: Path, mount: int):
    """Open every path component with O_NOFOLLOW relative to trusted root.

    A final-component O_NOFOLLOW alone cannot stop a queued *ancestor*
    directory from being swapped for a symlink between two BFS visits.
    The resulting FD is also checked for the original filesystem device.
    """
    relative = directory.relative_to(root)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(root, flags)
    try:
        if os.fstat(fd).st_dev != mount:
            raise OSError("workspace mount changed during scan")
        for segment in relative.parts:
            if segment in ("", ".", "..") or _sensitive(segment):
                raise OSError("unsafe directory segment")
            newer = os.open(segment, flags, dir_fd=fd)
            os.close(fd)
            fd = newer
            if os.fstat(fd).st_dev != mount:
                raise OSError("directory entered another filesystem")
        return fd
    except BaseException:
        os.close(fd)
        raise


def observe(root: Path, *, min_mib=64, max_entries=MAX_FILES,
            max_depth=MAX_DEPTH, max_seconds=MAX_SECONDS):
    root = _root(root)
    if (type(min_mib) is not int or not 1 <= min_mib <= 1048576
            or type(max_entries) is not int or not 1 <= max_entries <= MAX_FILES
            or type(max_depth) is not int or not 0 <= max_depth <= MAX_DEPTH
            or type(max_seconds) is not int or not 1 <= max_seconds <= MAX_SECONDS):
        raise ValueError("bounded scan settings required")
    started = time.monotonic()
    mount = root.stat().st_dev
    fs = os.statvfs(root)
    if (fs.f_blocks <= 0 or fs.f_frsize <= 0
            or not 0 <= fs.f_bavail <= fs.f_blocks):
        raise ValueError("untrusted workspace mount capacity")
    mount_total = fs.f_blocks * fs.f_frsize
    mount_available = fs.f_bavail * fs.f_frsize
    if any(type(x) is not int or x < 0 for x in
           (fs.f_files, fs.f_ffree, fs.f_favail)):
        raise ValueError("untrusted mount inode counters")
    if fs.f_files > 0:
        if fs.f_ffree > fs.f_files or fs.f_favail > fs.f_ffree:
            raise ValueError("contradictory mount inode counters")
        mount_inode_total = fs.f_files
        mount_inode_available = fs.f_favail
        mount_inode_unavailable_pct = round(100 * (1 - fs.f_favail / fs.f_files), 2)
    else:
        mount_inode_total = mount_inode_available = mount_inode_unavailable_pct = None
    if mount_total > 10**15 or mount_available > mount_total:
        raise ValueError("disk capacity outside bounded diagnostic contract")
    host = socket.gethostname()
    if (type(host) is not str or not 1 <= len(host) <= 255
            or any(not (c.isascii() and (c.isalnum() or c in ".-_"))
                   for c in host)):
        raise ValueError("untrusted current host identity")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("no-follow directory descriptor support required")
    stack = deque([(root, 0)])
    visited = set()
    scanned = 0
    sensitive_skipped = 0
    symlinks_skipped = 0
    directories_unscanned = 0
    denied = 0
    mount_skipped = 0
    large_files_seen = 0
    observed_allocated = 0
    top = []
    cold_files = []
    cold_files_seen = 0
    directory_bytes = {}
    directory_files = {}
    limit = min_mib * 1024 * 1024
    stop_reason = None
    while stack:
        if time.monotonic() - started >= max_seconds:
            stop_reason = "time_budget_exhausted"
            break
        # Breadth-first scan: a busy subproject cannot monopolize all depth
        # slots while other top-level workspaces remain completely unseen.
        directory, depth = stack.popleft()
        directory_fd = None
        try:
            directory_fd = _open_directory_beneath(root, directory, mount)
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    if scanned >= max_entries:
                        stop_reason = "entry_budget_exhausted"
                        break
                    if time.monotonic() - started >= max_seconds:
                        stop_reason = "time_budget_exhausted"
                        break
                    scanned += 1
                    if _sensitive(entry.name):
                        sensitive_skipped += 1
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        denied += 1
                        continue
                    if info.st_dev != mount:
                        mount_skipped += 1
                        continue
                    if stat.S_ISLNK(info.st_mode):
                        symlinks_skipped += 1
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        if depth >= max_depth:
                            directories_unscanned += 1
                        else:
                            stack.append((Path(directory, entry.name), depth + 1))
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    identity = (info.st_dev, info.st_ino)
                    if identity in visited:
                        continue
                    visited.add(identity)
                    allocated = max(0, info.st_blocks) * 512
                    observed_allocated += allocated
                    rel_parts = Path(directory, entry.name).relative_to(root).parts
                    folder = rel_parts[0] if len(rel_parts) > 1 else "(workspace root)"
                    directory_bytes[folder] = directory_bytes.get(folder, 0) + allocated
                    directory_files[folder] = directory_files.get(folder, 0) + 1
                    relative = str(Path(directory, entry.name).relative_to(root))
                    # Path is a private display label only, never passed to a shell.
                    item = (allocated, relative, max(0, int(info.st_size)),
                            max(0, int(info.st_mtime)), int(info.st_dev), int(info.st_ino))
                    # Archive hints require a separate top-N: large live SQLite
                    # files must not hide older immutable-looking replay/data.
                    if (allocated >= 65536 and
                            entry.name.lower().endswith(ARCHIVE_SUFFIX_HINTS)):
                        cold_files_seen += 1
                        if len(cold_files) < TOP:
                            heapq.heappush(cold_files, item)
                        elif item > cold_files[0]:
                            heapq.heapreplace(cold_files, item)
                    if allocated < limit:
                        continue
                    large_files_seen += 1
                    if len(top) < TOP:
                        heapq.heappush(top, item)
                    elif item > top[0]:
                        heapq.heapreplace(top, item)
            if stop_reason:
                break
        except OSError:
            denied += 1
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
    items = sorted(top, reverse=True)
    archive_items = sorted(cold_files, reverse=True)
    complete = (stop_reason is None and not stack and not denied and
                not directories_unscanned and not sensitive_skipped and
                not mount_skipped)
    return {
        "schema": SCHEMA,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "mount_capacity": {
            "host": host,
            "mount_device": mount,
            "filesystem_total_bytes": mount_total,
            "available_bytes_to_operator": mount_available,
            "percent_unavailable": round(
                100 * (1 - mount_available / mount_total), 2),
            "source": "same_host_statvfs_observation_not_cleanup_proof",
            "inode_total": mount_inode_total,
            "inode_available": mount_inode_available,
            "inode_unavailable_percent": mount_inode_unavailable_pct,
        },
        "scan_parameters": {
            "min_mib": min_mib, "max_entries": max_entries,
            "max_depth": max_depth, "max_seconds": max_seconds,
        },
        "coverage": "bounded_complete_with_no_private_exclusions" if complete else "partial",
        "truncation_reason": stop_reason,
        "scanned_entries": scanned,
        "observed_allocated_bytes_lower_bound": observed_allocated,
        "top_workspace_groups": [
            {"relative_group": name, "observed_allocated_mib_min": round(total / 1024**2, 1),
             "files_observed": directory_files[name]}
            for name, total in sorted(directory_bytes.items(),
                                      key=lambda item: (-item[1], item[0]))[:15]
        ],
        # Private, bounded comparison data. The current operator view still
        # shows only 15 groups; disappearing entries outside the top 128
        # must remain UNKNOWN, not be inferred as freed disk.
        "workspace_groups_sample": [
            {"relative_group": name, "observed_allocated_bytes_min": total,
             "files_observed": directory_files[name]}
            for name, total in sorted(directory_bytes.items(),
                                      key=lambda item: (-item[1], item[0]))[:128]
        ],
        "workspace_groups_truncated": len(directory_bytes) > 128,
        "large_files_seen_lower_bound": large_files_seen,
        "archive_suffix_candidates_seen_lower_bound": cold_files_seen,
        "cold_candidates_sample": [
            {"relative_path": name, "allocated_mib": round(n / 1024**2, 3),
             "logical_mib": round(size / 1024**2, 3),
             "last_modified_utc": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
             "scan_device": device, "scan_inode": inode}
            for n, name, size, mtime, device, inode in archive_items
        ],
        "directories_unscanned": directories_unscanned + len(stack),
        "sensitive_names_skipped": sensitive_skipped,
        "symlinks_skipped": symlinks_skipped,
        "other_mounts_skipped": mount_skipped,
        "unreadable_entries": denied,
        "top_files": [
            {"relative_path": name, "allocated_mib": round(n / (1024**2), 1),
             "logical_mib": round(size / (1024**2), 1),
             "last_modified_utc": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
             "scan_device": device, "scan_inode": inode}
            for n, name, size, mtime, device, inode in items
        ],
        "deletion_authorized": False,
        "runner_restart_authorized": False,
        "queue_claims_changed": False,
        "file_contents_read": False,
        "next_step": "Owner must verify each file's active writers, backups, retention and application lease before any separate authorized cleanup.",
    }



def attach_open_references(report, root: Path):
    """Opt-in local /proc read; attach private per-file details without authorizing cleanup."""
    if (type(report) is not dict or report.get("schema") != SCHEMA
            or report.get("deletion_authorized") is not False
            or "file_open_reference_observation" in report):
        raise ValueError("untrusted or already annotated disk report")
    from zsentinel_disk_file_activity import observe_open_references
    activity = observe_open_references(_root(root), report["top_files"])
    for index, row in enumerate(report["top_files"]):
        row["open_references_observed"] = activity["per_candidate"][index]["open_descriptors_observed"]
        row["file_reidentified"] = activity["per_candidate"][index]["file_reidentified"]
    report["file_open_reference_observation"] = {
        key: value for key, value in activity.items() if key != "per_candidate"
    }
    return report


def attach_unlinked_open_files(report, root: Path):
    """Sample currently open unlinked same-filesystem objects; never authorize cleanup."""
    if (type(report) is not dict or report.get("schema") != SCHEMA
            or report.get("deletion_authorized") is not False
            or "unlinked_open_files_observation" in report):
        raise ValueError("untrusted or already annotated disk report")
    from zsentinel_disk_unlinked_open import observe_unlinked
    report["unlinked_open_files_observation"] = observe_unlinked(_root(root))
    return report


def markdown(report):
    if report.get("schema") != SCHEMA or report.get("deletion_authorized") is not False:
        raise ValueError("untrusted disk workbench source")
    lines = [
        "# zSentinel · VPS schijfruimtewerkbank",
        "",
        "**Alleen-lezen. Geen verwijdering, truncate, herstart of vrijgegeven claim.**",
        "Bron: " + report["observed_at_utc"],
        "Workspace: " + report["root"].replace("|", "/"),
        "Dekking: **" + report["coverage"] + "**; ook in volledige dekking geen buiten-root analyse.",
        "Mount vrije ruimte bij bronmoment: " +
        str(round(report["mount_capacity"]["available_bytes_to_operator"] / (1024**3), 2)) +
        " GiB beschikbaar (" + str(report["mount_capacity"]["percent_unavailable"]) +
        "% niet beschikbaar); momentopname, geen schoonmaakbewijs.",
        "Inodes nog beschikbaar: " +
        str(report["mount_capacity"].get("inode_available") if
            report["mount_capacity"].get("inode_available") is not None else "ONBEKEND") +
        "; inodebezetting niet beschikbaar: " +
        str(report["mount_capacity"].get("inode_unavailable_percent") if
            report["mount_capacity"].get("inode_unavailable_percent") is not None
            else "ONBEKEND") + "%; geen schijfopruimbevoegdheid.",
        "Waargenomen toegewezen ruimte (ondergrens): " +
        str(round(report["observed_allocated_bytes_lower_bound"] / 1024**3, 2)) + " GiB.",
        "Beperkt door: " + str(report["truncation_reason"] or "geen tijd-/itemlimiet"),
        "Extra ongescand: " + str(report["directories_unscanned"]) +
        " mappen; " + str(report["sensitive_names_skipped"]) +
        " gevoelige namen bewust weggelaten.",
        "",
        "## Grootste workspace-onderdelen (gemeten ondergrenzen, geen schoonmaakadvies)",
    ]
    for group in report["top_workspace_groups"]:
        label = group["relative_group"].replace("\\", "/").replace("`", "'").replace("|", "/")
        lines.append("- " + str(group["observed_allocated_mib_min"]) +
                     " MiB waargenomen: `" + label + "` · " +
                     str(group["files_observed"]) + " bestanden in begrensde scan")
    lines += [
        "",
        "## Grote bestanden · kandidaten voor eigenaarcontrole, GEEN verwijderadvies",
    ]
    for item in report["top_files"]:
        safe = item["relative_path"].replace("\\", "/").replace("`", "'").replace("|", "/")
        lines.append("- " + str(item["allocated_mib"]) + " MiB toegewezen: `" + safe +
                     "` · gewijzigd " + item["last_modified_utc"][:10] +
                     (" · open verwijzingen waargenomen: " + str(item["open_references_observed"])
                      + (" (bestandsidentiteit bevestigd, schrijver ONBEKEND)"
                         if item.get("file_reidentified") else
                         " (bestand vervangen of verdwenen, schrijver ONBEKEND)")
                      if "open_references_observed" in item else ""))
    if not report["top_files"]:
        lines.append("Geen groot bestand in het onderzochte venster; geen vrijruimtegarantie.")
    if "file_open_reference_observation" in report:
        from zsentinel_disk_file_activity import private_markdown
        lines.extend(["", "## Open bestandsverwijzingen (optionele alleen-lezen bron)",
                      private_markdown(report["file_open_reference_observation"])])
    lines += [
        "",
        "**Nodige geautoriseerde actie:** eigenaar controleert actieve writers, "
        "bewaartermijn en herstelback-up vóór een afzonderlijke opruimactie.",
        "Deze tool kan geen veilige verwijdering, vrije ruimte of herstel bewijzen.",
        "",
    ]
    if "unlinked_open_files_observation" in report:
        from zsentinel_disk_unlinked_open import private_markdown
        lines.extend(["", "## Verwijderde maar nog geopende bestanden",
                      private_markdown(report["unlinked_open_files_observation"])])
    if "disk_growth_comparison" in report:
        from zsentinel_disk_trend import as_markdown
        lines.extend(["", as_markdown(report["disk_growth_comparison"])])
    if "sqlite_footprint" in report:
        from zsentinel_sqlite_footprint import markdown as sqlite_markdown
        lines.extend(["", sqlite_markdown(report["sqlite_footprint"])])
    if "archive_readiness" in report:
        from zsentinel_archive_readiness import markdown as archive_markdown
        lines.extend(["", archive_markdown(report["archive_readiness"])])
    return "\n".join(lines)


def html(report):
    from html import escape
    md = markdown(report)
    lines = ["<!doctype html><html lang='nl'><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<title>zSentinel · Schijfruimtewerkbank</title>",
             "<style>body{font:16px system-ui;line-height:1.5;max-width:950px;"
             "margin:2rem auto;padding:0 1rem;background:#0f172a;color:#f1f5f9}"
             "article{border:1px solid #475569;border-radius:14px;padding:1rem;"
             "margin:.7rem 0;background:#1e293b}small{color:#cbd5e1}"
             "h1,h2{line-height:1.2}strong{color:#fbbf24}"
             "code{overflow-wrap:anywhere}</style><main>",
             "<h1>zSentinel · VPS schijfruimtewerkbank</h1>",
             "<p><strong>Alleen-lezen. Geen opruimactie uitgevoerd of toegestaan.</strong></p>"]
    from zsentinel_disk_unlinked_open import public_summary as unlinked_public_summary
    brief = operator_action_brief(
        report["mount_capacity"], report["coverage"],
        (unlinked_public_summary(report["unlinked_open_files_observation"])
         if "unlinked_open_files_observation" in report else None))
    lines += [
        "<article><h2>Wat nu?</h2><p>" + escape(brief["problem"]) +
        "</p><p>" + escape(brief["next_action"]) +
        "</p><p>" + escape(brief["status"]) + "</p></article>",
             "<p>Dekking: " + escape(report["coverage"]) +
             " · items gezien: " + str(report["scanned_entries"]) +
             " · reden grens: " + escape(str(report["truncation_reason"] or "geen")) + "</p>",
             "<p>Vrij op gemeten mount: " +
             str(round(report["mount_capacity"]["available_bytes_to_operator"] / (1024**3), 2)) +
             " GiB · " + str(report["mount_capacity"]["percent_unavailable"]) +
             "% niet beschikbaar. Geen herstelbewijs.</p>",
             "<p>Inodes nog beschikbaar: " +
             escape(str(report["mount_capacity"].get("inode_available") if
                        report["mount_capacity"].get("inode_available") is not None
                        else "ONBEKEND")) +
             "; percentage inodes niet beschikbaar: " +
             escape(str(report["mount_capacity"].get("inode_unavailable_percent") if
                        report["mount_capacity"].get("inode_unavailable_percent") is not None
                        else "ONBEKEND")) +
             "%. Geen inode-herstelbewijs.</p>",
             "<p>Waargenomen ruimte (ondergrens): " +
             str(round(report["observed_allocated_bytes_lower_bound"] / 1024**3,2)) + " GiB; " +
             str(report["directories_unscanned"]) + " mappen niet bezocht.</p>",
             "<h2>Grote projectonderdelen (alleen waargenomen ondergrens)</h2>"]
    for group in report["top_workspace_groups"]:
        lines.append("<article><strong>" + str(group["observed_allocated_mib_min"]) +
                     " MiB waargenomen</strong><p><code>" +
                     escape(group["relative_group"]) + "</code></p><small>" +
                     str(group["files_observed"]) + " gevonden bestanden · " +
                     "geen bewijs dat ze ongebruikt zijn</small></article>")
    lines.append("<h2>Grote bestanden voor eigenaarcontrole</h2>")
    for row in report["top_files"]:
        lines.append("<article><strong>" + str(row["allocated_mib"]) +
                     " MiB toegewezen</strong><p><code>" +
                     escape(row["relative_path"]) + "</code></p><small>Laatste wijziging: " +
                     escape(row["last_modified_utc"][:10]) +
                     (" · Open verwijzingen in steekproef: " + str(row["open_references_observed"])
                      + ("; bestandsidentiteit bevestigd, schrijver ONBEKEND"
                         if row.get("file_reidentified") else
                         "; oorspronkelijke bestandsidentiteit niet meer aanwezig")
                      if "open_references_observed" in row else "") +
                     "</small></article>")
    if not report["top_files"]:
        lines.append("<p>Geen kandidaten in deze begrensde bron, niet per se geen ruimteproblemen.</p>")
    if "file_open_reference_observation" in report:
        from zsentinel_disk_file_activity import private_markdown
        lines.append("<article><h2>Open verwijzingen</h2><p>" +
                     escape(private_markdown(report["file_open_reference_observation"])) +
                     "</p></article>")
    if "unlinked_open_files_observation" in report:
        from zsentinel_disk_unlinked_open import private_markdown
        lines.append("<article><h2>Verwijderd maar nog geopend (zelfde mount)</h2><p>" +
                     escape(private_markdown(report["unlinked_open_files_observation"])) +
                     "</p></article>")
    if "disk_growth_comparison" in report:
        from zsentinel_disk_trend import as_html
        lines.append(as_html(report["disk_growth_comparison"]))
    if "archive_readiness" in report:
        from zsentinel_archive_readiness import markdown as archive_markdown
        lines.append("<article><h2>Externe opslag · koude-bestandenplan</h2><pre "
                     "style=\"white-space:pre-wrap;overflow-wrap:anywhere\">" +
                     escape(archive_markdown(report["archive_readiness"])) +
                     "</pre></article>")
    if "sqlite_footprint" in report:
        from zsentinel_sqlite_footprint import markdown as sqlite_markdown
        lines.append("<article><h2>SQLite en WAL · eigenaarwerkbank</h2><pre "
                     "style=\"white-space:pre-wrap;overflow-wrap:anywhere\">" +
                     escape(sqlite_markdown(report["sqlite_footprint"])) +
                     "</pre></article>")
    lines.append("<p><strong>Vereist:</strong> eigenaar verifieert actieve writers, "
                 "bewaartermijn en back-up vóór afzonderlijk geautoriseerd opruimen.</p></main></html>")
    return "".join(lines)




def operator_action_brief(mount, scan_coverage, orphan_summary=None):
    """Three-line, path-free operator handoff. Advisory only; never dispatch."""
    unavailable = mount["percent_unavailable"]
    if unavailable >= 95:
        problem = "PROBLEEM: mount minstens 95% niet beschikbaar; urgent capaciteitstekort mogelijk."
        severity = "urgent_owner_review"
    elif unavailable >= 90:
        problem = "PROBLEEM: mount minstens 90% niet beschikbaar; verhoogde schijfdruk."
        severity = "elevated_owner_review"
    else:
        problem = "PROBLEEM: lokale capaciteit gemeten; geen schijfdrempelalarm in deze steekproef."
        severity = "observe_only"
    if orphan_summary is None:
        candidate = "verwijderde-open bron niet ingeschakeld"
    elif orphan_summary["unlinked_files_observed"] > 0:
        candidate = ("minstens " + str(orphan_summary["unlinked_files_observed"]) +
                     " verwijderde-open inode(s) op dezelfde mount; geen reclaimbewijs")
    else:
        candidate = "geen verwijderde-open inode waargenomen; afwezigheid NIET bewezen"
    return {
        "schema": "zsentinel-disk-operator-action-v1",
        "severity": severity,
        "problem": problem,
        "next_action": ("ACTIE: bevoegd VPS-eigenaar onderzoekt actieve writers, "
                        "retentie en back-up; autoriseert eventuele aparte ingreep."),
        "status": ("STATUS: alleen-lezen; scan " + scan_coverage +
                   "; " + candidate + "; cleanup en runnerherstel NIET bevestigd."),
        "operator_action_only": True,
        "automatic_cleanup_authorized": False,
        "runner_restart_authorized": False,
    }


def public_summary(report):
    """Privacy-minimal cockpit receipt: never publish private filenames."""
    if (type(report) is not dict or report.get("schema") != SCHEMA
            or report.get("deletion_authorized") is not False
            or report.get("runner_restart_authorized") is not False
            or report.get("queue_claims_changed") is not False
            or report.get("file_contents_read") is not False
            or type(report.get("top_files")) is not list
            or len(report["top_files"]) > TOP
            or report.get("coverage") not in (
                "bounded_complete_with_no_private_exclusions", "partial")):
        raise ValueError("untrusted disk hotspot observation")
    mount = report.get("mount_capacity")
    if (type(mount) is not dict
            or mount.get("source") != "same_host_statvfs_observation_not_cleanup_proof"
            or type(mount.get("filesystem_total_bytes")) is not int
            or type(mount.get("available_bytes_to_operator")) is not int
            or not 0 <= mount["available_bytes_to_operator"] <= mount["filesystem_total_bytes"]
            or type(mount.get("percent_unavailable")) not in (int, float)
            or not 0 <= mount["percent_unavailable"] <= 100):
        raise ValueError("invalid mount observation for public capacity summary")
    inode_total = mount.get("inode_total")
    inode_available = mount.get("inode_available")
    inode_pct = mount.get("inode_unavailable_percent")
    if any(x is not None for x in (inode_total, inode_available, inode_pct)):
        if (type(inode_total) is not int or type(inode_available) is not int
                or not 1 <= inode_total <= 10**15
                or not 0 <= inode_available <= inode_total
                or type(inode_pct) not in (int, float)
                or not 0 <= inode_pct <= 100
                or abs(inode_pct - 100 * (1 - inode_available / inode_total)) > 0.015):
            raise ValueError("invalid inode capacity receipt")
    compare = report.get("disk_growth_comparison")
    if compare is not None:
        from zsentinel_disk_trend import TREND
        if (type(compare) is not dict or compare.get("schema") != TREND
                or compare.get("disk_freed_verified") is not False
                or compare.get("cleanup_authorized") is not False):
            raise ValueError("untrusted private disk change comparison")
    activity = report.get("file_open_reference_observation")
    if activity is not None and (type(activity) is not dict
            or activity.get("schema") != "zsentinel-disk-open-references-v1"
            or activity.get("safe_to_delete") is not False
            or activity.get("process_identifiers_disclosed") is not False
            or type(activity.get("files_with_observed_open_references")) is not int
            or not 0 <= activity["files_with_observed_open_references"] <= len(report["top_files"])):
        raise ValueError("untrusted private open-reference evidence")
    orphan = report.get("unlinked_open_files_observation")
    if orphan is not None:
        from zsentinel_disk_unlinked_open import public_summary as orphan_public
        orphan_summary = orphan_public(orphan)
    else:
        orphan_summary = None
    brief = operator_action_brief(mount, report["coverage"], orphan_summary)
    archive_obs = report.get("archive_readiness")
    archive_public = None
    if archive_obs is not None:
        from zsentinel_archive_readiness import public_summary as archive_summary
        archive_public = archive_summary(archive_obs)
    sqlite_obs = report.get("sqlite_footprint")
    if sqlite_obs is not None and (
            type(sqlite_obs) is not dict
            or sqlite_obs.get("schema") != "zsentinel-sqlite-owner-desk-v1"
            or sqlite_obs.get("checkpoint_or_database_write") is not False
            or sqlite_obs.get("reclaimable_bytes_verified") is not False
            or type(sqlite_obs.get("reports")) is not list
            or not 1 <= len(sqlite_obs["reports"]) <= 8):
        raise ValueError("untrusted optional SQLite disk evidence")
    return {
        "schema": "zsentinel-disk-workbench-summary-v1",
        "sqlite_files_metadata_observed": len(sqlite_obs["reports"]) if sqlite_obs else None,
        "sqlite_reclaimable_bytes_verified": False,
        "external_archive_readiness": archive_public,
        "operator_action": brief,
        "unlinked_open_files": orphan_summary,
        "files_with_open_references_observed": (
            activity["files_with_observed_open_references"] if activity else None),
        "open_reference_coverage": activity["coverage"] if activity else "not_observed",
        "mount_available_gib_observed": round(
            mount["available_bytes_to_operator"] / (1024**3), 2),
        "mount_unavailable_percent_observed": mount["percent_unavailable"],
        "mount_inode_unavailable_percent_observed": mount.get("inode_unavailable_percent"),
        "mount_inode_available_observed": mount.get("inode_available"),
        "mount_free_change_mib_not_cleanup": (
            round(compare["mount_free_change_bytes_not_cleanup_proof"] / 1048576, 1)
            if compare is not None else None),
        "observed_change_mib_not_growth": (
            round(compare["observed_allocated_difference_bytes_not_disk_growth"] / 1048576, 1)
            if compare is not None else None),
        "matched_previous_groups": compare["shared_groups"] if compare is not None else None,
        "comparison_partial": compare["any_partial_snapshot"] if compare is not None else None,
        "observed_at_utc": report["observed_at_utc"],
        "coverage": report["coverage"],
        "truncation_reason": report["truncation_reason"],
        "observed_allocated_gib_min": round(
            report["observed_allocated_bytes_lower_bound"] / (1024**3), 2),
        "large_file_candidates_min": report["large_files_seen_lower_bound"],
        "directories_unscanned": report["directories_unscanned"],
        # Caller renders this BEFORE any optional create-only output is
        # written. A direct metadata-only scan must never claim a report.
        "private_report_written": False,
        "private_report_creation": "not_verified_at_summary_time",
        "deletion_authorized": False,
        "runner_restart_authorized": False,
        "queue_claims_changed": False,
        "report_contains_paths": False,
    }


def public_summary_html(summary):
    if (type(summary) is not dict
            or summary.get("schema") != "zsentinel-disk-workbench-summary-v1"
            or summary.get("deletion_authorized") is not False
            or summary.get("report_contains_paths") is not False):
        raise ValueError("untrusted disk workbench summary")
    brief = summary.get("operator_action")
    if (type(brief) is not dict or brief.get("schema") != "zsentinel-disk-operator-action-v1"
            or brief.get("automatic_cleanup_authorized") is not False
            or brief.get("runner_restart_authorized") is not False):
        raise ValueError("invalid disk operator action evidence")
    for key in ("problem", "next_action", "status"):
        if type(brief.get(key)) is not str or not 1 <= len(brief[key]) <= 350:
            raise ValueError("invalid disk operator message")
    action = ('<aside aria-label="VPS schijf eigenaaractie">'
              '<p><strong>' + escape(brief["problem"]) + '</strong></p>'
              '<p>' + escape(brief["next_action"]) + '</p>'
              '<p>' + escape(brief["status"]) + '</p></aside>')
    return (
        '<section aria-label="VPS schijfruimtewerkbank">' + action +
        '<h2>VPS · Schijfruimtewerkbank (privé)</h2>'
        '<p>Mount: ' + str(summary["mount_available_gib_observed"]) +
        ' GiB beschikbaar; ' +
        str(summary["mount_unavailable_percent_observed"]) +
        '% niet beschikbaar volgens lokaal statvfs. Geen toegang/vrijgavebewijs.</p>' +
        '<p>Inodes nog beschikbaar: ' +
        escape(str(summary["mount_inode_available_observed"]) if
               summary["mount_inode_available_observed"] is not None else "ONBEKEND") +
        '; inodes niet beschikbaar: ' +
        escape((str(summary["mount_inode_unavailable_percent_observed"]) + "%") if
               summary["mount_inode_unavailable_percent_observed"] is not None else "ONBEKEND") +
        '. Alleen metadata, geen cleanup- of releasebevoegdheid.</p>' +
        ('<p>SQLite/WAL-metadata in privé-diskrapport: ' +
         str(summary['sqlite_files_metadata_observed']) +
         ' database(s) waargenomen; interne freelist is geen bewezen'
         ' vrijmaakbare opslag. Geen SQL, checkpoint of opschoning.</p>'
         if summary.get('sqlite_files_metadata_observed') is not None else '') +
        ('<p>Externe opslag op andere mount waargenomen; circa ' +
         str(summary["external_archive_readiness"]["archive_available_gib_estimate"]) +
         ' GiB doelruimte-indicatie, ' +
         str(summary["external_archive_readiness"]["candidate_count_sampled_not_transferable"]) +
         ' oude bestandshints. Geen bestanden gekopieerd, verplaatst of ' +
         'verwijderd; eigenaarschap, writerstatus en archiefrechten NIET bewezen.</p>'
         if summary.get("external_archive_readiness") is not None else '') +
        '<p>Grote-bestanden inventaris uitsluitend lokaal en begrensd,'
        ' ondergrens ' + str(summary["observed_allocated_gib_min"]) +
        ' GiB toegewezen, minstens ' + str(summary["large_file_candidates_min"]) +
        ' grote bestanden. Dekking: ' + escape(summary["coverage"]) +
        '; aanvullende niet-bezochte mappen: ' +
        str(summary["directories_unscanned"]) +
        '. Bestandsnamen worden bij succesvolle privé-uitvoer uitsluitend'
        ' naar een afzonderlijk 0600-rapport geschreven, NIET in deze'
        ' cockpit. Deze momentopname bewijst de bestandsschrijfstatus niet.</p>' +
        ('<p>Verwijderd maar nog geopend op dezelfde mount: ' +
         str(summary['unlinked_open_files']['unlinked_files_observed']) +
         ' unieke bestanden in ' +
         escape(summary['unlinked_open_files']['coverage']) +
         ' /proc-steekproef; ' +
         str(summary['unlinked_open_files']['observed_allocated_mib_not_reclaimable']) +
         ' MiB waargenomen toegewezen blokken, GEEN vrijmaakbaarheidsbewijs. '
         'Kan buiten projectmap liggen; géén automatische proces- of cleanupactie.</p>'
         if summary.get('unlinked_open_files') is not None else '') +
        ('<p>Optioneel /proc: ' + str(summary['files_with_open_references_observed']) +
         ' grote-bestandkandidaten met aangetroffen open verwijzingen; ' +
         'bron ' + escape(summary['open_reference_coverage']) +
         '. Geen verwijzing gezien is GEEN bewijs van vrijgegeven schrijvers.</p>'
         if summary.get('files_with_open_references_observed') is not None else '') +
        ('<p>Gemeten vrije ruimte op dezelfde host/mount veranderde met ' +
         str(summary["mount_free_change_mib_not_cleanup"]) +
         ' MiB (geen bewijs van welke bestanden dit veroorzaakten).</p>'
         if summary.get("mount_free_change_mib_not_cleanup") is not None else '') +
        ('<p>Vergelijking met vorige privé-scan: ' +
         str(summary["observed_change_mib_not_growth"]) +
         ' MiB verschil in waargenomen toegewezen data (GEEN bewijs van'
         ' werkelijk gegroeide of vrijgemaakte schijfruimte).'
         ' Overlappende groepen: ' + str(summary["matched_previous_groups"]) +
         '; minstens één bron gedeeltelijk: ' +
         str(summary["comparison_partial"]) + '.</p>'
         if summary.get("observed_change_mib_not_growth") is not None else '') +
        '<p>Actie: eigenaar controleert actieve schrijvers, bewaartermijnen en'
        ' backups vóór een afzonderlijk geautoriseerde opruimactie.'
        ' Geen bestand verwijderd, worker herstart of claim vrijgegeven.</p>'
        '</section>'
    )


def main():
    parser = argparse.ArgumentParser(description="Bounded read-only project disk hotspot workbench")
    parser.add_argument("--root", required=True, type=Path, help="specific existing project workspace")
    parser.add_argument("--min-mib", type=int, default=64)
    parser.add_argument("--max-entries", type=int, default=MAX_FILES)
    parser.add_argument("--max-depth", type=int, default=MAX_DEPTH)
    parser.add_argument("--max-seconds", type=int, default=MAX_SECONDS)
    parser.add_argument("--format", choices=("json", "md", "html"), default="md")
    parser.add_argument("--inspect-unlinked-files", action="store_true",
                        help="optional private same-mount deleted-but-open file metadata only")
    parser.add_argument("--inspect-open-handles", action="store_true",
                        help="optional bounded /proc fd metadata only; no PID or writer proof")
    parser.add_argument("--archive-target", type=Path,
                        help="optional separate existing archive mount; no copy or delete")
    parser.add_argument("--archive-min-age-days", type=int, default=7)
    parser.add_argument("--sqlite", action="append", default=[],
                        help="optional relative existing SQLite DB inside workspace; max 8, metadata only")
    parser.add_argument("--previous", type=Path,
                        help="private preceding .json disk snapshot of SAME workspace, max 72h ago")
    parser.add_argument("--output", type=Path, required=True,
                        help="REQUIRED private create-only .json/.md/.html; no filename stdout")
    args = parser.parse_args()
    try:
        if args.previous is not None and args.previous.resolve() == args.output.resolve():
            raise ValueError("previous and new private output cannot overlap")
        report = observe(args.root, min_mib=(min(args.min_mib, 1)
                         if args.archive_target is not None else args.min_mib),
                         max_entries=args.max_entries,
                         max_depth=args.max_depth, max_seconds=args.max_seconds)
        if args.inspect_open_handles:
            attach_open_references(report, args.root)
        if args.inspect_unlinked_files:
            attach_unlinked_open_files(report, args.root)
        if args.archive_target is not None:
            from zsentinel_archive_readiness import observe as archive_observe
            report["archive_readiness"] = archive_observe(
                report, args.archive_target, min_age_days=args.archive_min_age_days)
        if args.sqlite:
            from zsentinel_sqlite_footprint import observe_many
            report["sqlite_footprint"] = observe_many(args.root, args.sqlite)
        if args.previous is not None:
            from zsentinel_disk_trend import load_previous, compare
            report["disk_growth_comparison"] = compare(load_previous(args.previous), report)
        content = (json.dumps(report, ensure_ascii=True, indent=2) if args.format == "json"
                   else html(report) if args.format == "html" else markdown(report))
        from zsentinel_private_output import write_private_output
        write_private_output(args.output, content, args.format)
        print("Private disk workbench created (exclusive output; no paths or contents printed).")
    except (ValueError, OSError, OverflowError) as exc:
        parser.exit(2, "zSentinel disk observation unavailable or unsafe; no changes made.\n")


if __name__ == "__main__":
    raise SystemExit(main())
