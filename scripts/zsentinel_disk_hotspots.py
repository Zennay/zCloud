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
            directory_fd = os.open(
                directory, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY |
                getattr(os, "O_CLOEXEC", 0))
            if os.fstat(directory_fd).st_dev != mount:
                mount_skipped += 1
                continue
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
                    if allocated < limit:
                        continue
                    large_files_seen += 1
                    relative = str(Path(directory, entry.name).relative_to(root))
                    # Path is a display label only, never passed to a shell.
                    item = (allocated, relative, max(0, int(info.st_size)),
                            max(0, int(info.st_mtime)))
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
        "directories_unscanned": directories_unscanned + len(stack),
        "sensitive_names_skipped": sensitive_skipped,
        "symlinks_skipped": symlinks_skipped,
        "other_mounts_skipped": mount_skipped,
        "unreadable_entries": denied,
        "top_files": [
            {"relative_path": name, "allocated_mib": round(n / (1024**2), 1),
             "logical_mib": round(size / (1024**2), 1),
             "last_modified_utc": datetime.fromtimestamp(mtime, timezone.utc).isoformat()}
            for n, name, size, mtime in items
        ],
        "deletion_authorized": False,
        "runner_restart_authorized": False,
        "queue_claims_changed": False,
        "file_contents_read": False,
        "next_step": "Owner must verify each file's active writers, backups, retention and application lease before any separate authorized cleanup.",
    }


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
                     "` · gewijzigd " + item["last_modified_utc"][:10])
    if not report["top_files"]:
        lines.append("Geen groot bestand in het onderzochte venster; geen vrijruimtegarantie.")
    lines += [
        "",
        "**Nodige geautoriseerde actie:** eigenaar controleert actieve writers, "
        "bewaartermijn en herstelback-up vóór een afzonderlijke opruimactie.",
        "Deze tool kan geen veilige verwijdering, vrije ruimte of herstel bewijzen.",
        "",
    ]
    if "disk_growth_comparison" in report:
        from zsentinel_disk_trend import as_markdown
        lines.extend(["", as_markdown(report["disk_growth_comparison"])])
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
             "<p><strong>Alleen-lezen. Geen opruimactie uitgevoerd of toegestaan.</strong></p>",
             "<p>Dekking: " + escape(report["coverage"]) +
             " · items gezien: " + str(report["scanned_entries"]) +
             " · reden grens: " + escape(str(report["truncation_reason"] or "geen")) + "</p>",
             "<p>Vrij op gemeten mount: " +
             str(round(report["mount_capacity"]["available_bytes_to_operator"] / (1024**3), 2)) +
             " GiB · " + str(report["mount_capacity"]["percent_unavailable"]) +
             "% niet beschikbaar. Geen herstelbewijs.</p>",
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
                     escape(row["last_modified_utc"][:10]) + "</small></article>")
    if not report["top_files"]:
        lines.append("<p>Geen kandidaten in deze begrensde bron, niet per se geen ruimteproblemen.</p>")
    if "disk_growth_comparison" in report:
        from zsentinel_disk_trend import as_html
        lines.append(as_html(report["disk_growth_comparison"]))
    lines.append("<p><strong>Vereist:</strong> eigenaar verifieert actieve writers, "
                 "bewaartermijn en back-up vóór afzonderlijk geautoriseerd opruimen.</p></main></html>")
    return "".join(lines)



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
    compare = report.get("disk_growth_comparison")
    if compare is not None:
        from zsentinel_disk_trend import TREND
        if (type(compare) is not dict or compare.get("schema") != TREND
                or compare.get("disk_freed_verified") is not False
                or compare.get("cleanup_authorized") is not False):
            raise ValueError("untrusted private disk change comparison")
    return {
        "schema": "zsentinel-disk-workbench-summary-v1",
        "mount_available_gib_observed": round(
            mount["available_bytes_to_operator"] / (1024**3), 2),
        "mount_unavailable_percent_observed": mount["percent_unavailable"],
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
    return (
        '<section aria-label="VPS schijfruimtewerkbank">'
        '<h2>VPS · Schijfruimtewerkbank (privé)</h2>'
        '<p>Mount: ' + str(summary["mount_available_gib_observed"]) +
        ' GiB beschikbaar; ' +
        str(summary["mount_unavailable_percent_observed"]) +
        '% niet beschikbaar volgens lokaal statvfs. Geen toegang/vrijgavebewijs.</p>'
        '<p>Grote-bestanden inventaris uitsluitend lokaal en begrensd,'
        ' ondergrens ' + str(summary["observed_allocated_gib_min"]) +
        ' GiB toegewezen, minstens ' + str(summary["large_file_candidates_min"]) +
        ' grote bestanden. Dekking: ' + escape(summary["coverage"]) +
        '; aanvullende niet-bezochte mappen: ' +
        str(summary["directories_unscanned"]) +
        '. Bestandsnamen worden bij succesvolle privé-uitvoer uitsluitend'
        ' naar een afzonderlijk 0600-rapport geschreven, NIET in deze'
        ' cockpit. Deze momentopname bewijst de bestandsschrijfstatus niet.</p>' +
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
    parser.add_argument("--previous", type=Path,
                        help="private preceding .json disk snapshot of SAME workspace, max 72h ago")
    parser.add_argument("--output", type=Path, required=True,
                        help="REQUIRED private create-only .json/.md/.html; no filename stdout")
    args = parser.parse_args()
    try:
        if args.previous is not None and args.previous.resolve() == args.output.resolve():
            raise ValueError("previous and new private output cannot overlap")
        report = observe(args.root, min_mib=args.min_mib, max_entries=args.max_entries,
                         max_depth=args.max_depth, max_seconds=args.max_seconds)
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
