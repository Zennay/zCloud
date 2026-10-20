#!/usr/bin/env python3
"""Read-only storage tier inventory for the VPS. Never transfers or deletes data.

Cold candidates here are GitHub runner diagnostics with no recent writes,
and completed Fuckball research reports outside the trainer's recent window.
Encrypted offloading is handled by the existing zcloud-drive-archive machinery,
which must verify remote content before any separately authorized cleanup.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from html import escape
import os
import stat
import subprocess
from itertools import chain
import time
from pathlib import Path

HOME = Path("/home/ubuntu")
ROOT = HOME / "zennay-cloud"
TRAINING = Path("/opt/fuckball-training")
ARCHIVE = "vpsarchive:zcloud-vps"
MIN_AGE_DAYS = 3
MAX_LIST = 100
MAX_SCAN_CANDIDATES = 10000
MAX_SCAN_SECONDS = 8
TRAINING_MIN_FREE_GIB = 10



def candidate_file(path: Path, cutoff: float) -> bool:
    try:
        st = path.lstat()
        return path.is_file() and not path.is_symlink() and st.st_nlink == 1 and st.st_mtime < cutoff
    except OSError:
        return False


def runner_diagnostics(cutoff: float, deadline: float | None = None):
    for runner in sorted(HOME.glob("actions-runner-*")):
        if deadline is not None and time.monotonic() >= deadline:
            return
        if runner.is_symlink():
            continue
        diagnostic = runner / "_diag"
        if not diagnostic.is_dir() or diagnostic.is_symlink():
            continue
        for path in diagnostic.iterdir():
            if deadline is not None and time.monotonic() >= deadline:
                return
            if not (path.name.startswith(("Worker_", "Runner_", "SelfUpdate_")) and path.suffix == ".log"):
                continue
            if candidate_file(path, cutoff):
                stat = path.stat()
                yield {
                    "kind": "aged-runner-log-owner-review",
                    "source": str(path),
                    "relative_archive": f"cold/runner-diagnostics/{runner.name}/{path.name}",
                    "bytes": stat.st_size,
                    "modified_utc": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
                    "can_remove_automatically": False,
                    "no_open_handle_verified": False,
                    "archive_transfer_approved": False,
                }


def training_reports(cutoff: float, deadline: float | None = None):
    try:
        status = json.loads((TRAINING / "status.json").read_text())
        cycles = status["cycles"]
        if type(cycles) is not int or cycles < 50:
            return
    except (OSError, KeyError, TypeError, ValueError):
        return
    for path in TRAINING.glob("cycle-*-report.json"):
        if deadline is not None and time.monotonic() >= deadline:
            return
        try:
            cycle = int(path.name.removeprefix("cycle-").removesuffix("-report.json"))
        except ValueError:
            continue
        if cycle >= cycles - 50 or not candidate_file(path, cutoff):
            continue
        stat = path.stat()
        yield {
            "kind": "aged-training-report-owner-review",
            "source": str(path),
            "relative_archive": f"cold/fuckball-training/reports/{path.name}",
            "bytes": stat.st_size,
            "modified_utc": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
            "can_remove_automatically": False,
            "no_open_handle_verified": False,
            "archive_transfer_approved": False,
        }



def old_app_release_review(cutoff: float, deadline: float | None = None):
    """Only named, old standalone APK exports; not live build directories.

    These are REVIEW candidates, never proven superseded, archived or removable.
    """
    old_enough = min(cutoff, dt.datetime.now(dt.timezone.utc).timestamp() - 14 * 86400)
    for folder, pattern, archive_group in (
        (HOME, "RaiseAI-v*.apk", "home"),
        (HOME / "Downloads", "*.apk", "downloads"),
    ):
        if not folder.is_dir() or folder.is_symlink():
            continue
        for path in sorted(folder.glob(pattern)):
            if deadline is not None and time.monotonic() >= deadline:
                return
            if not candidate_file(path, old_enough):
                continue
            stat = path.stat()
            yield {
                "kind": "manual-review-old-app-release",
                "source": str(path),
                "relative_archive": f"cold/manual-app-release-review/{archive_group}/{path.name}",
                "bytes": stat.st_size,
                "modified_utc": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
                "release_superseded_verified": False,
                "can_remove_automatically": False,
                "no_open_handle_verified": False,
                "archive_transfer_approved": False,
            }


def observe_open_handle_hints(rows: list[dict], seconds: float = 2.0) -> dict:
    """Read-only positive /proc FD hints for displayed candidates only.

    An absence of matches is never proof that any file is closed. Proc access
    may be restricted, processes race, other hosts may hold NFS handles, and
    candidate inodes may be replaced between observations.
    """
    result = {
        "mode": "bounded_proc_fd_positive_only",
        "sampled_candidates": len(rows),
        "observed_open_files": 0,
        "files_open_unknown": len(rows),
        "scanned_processes": 0,
        "scanned_fds": 0,
        "observation_incomplete": True,
        "no_open_handles_proven": False,
        "archive_transfer_authorized": False,
        "source_deletion_authorized": False,
    }
    targets = {}
    for row in rows[:100]:
        row["open_handle_observed"] = False
        row["open_handle_clear_verified"] = False
        try:
            p = Path(row["source"])
            st = p.lstat()
            if stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                targets.setdefault((st.st_dev, st.st_ino), []).append(row)
        except (OSError, ValueError):
            continue
    if not targets:
        return result
    end = time.monotonic() + max(0.1, min(seconds, 2.0))
    scanned_procs = 0
    scanned_fds = 0
    partial = False
    try:
        processes = (item for item in Path("/proc").iterdir()
                     if item.name.isdecimal() and item.is_dir())
        for proc in processes:
            if time.monotonic() >= end or scanned_procs >= 350 or scanned_fds >= 5000:
                partial = True
                break
            scanned_procs += 1
            try:
                descriptors = (proc / "fd").iterdir()
            except (OSError, PermissionError):
                partial = True
                continue
            for fd in descriptors:
                if time.monotonic() >= end or scanned_fds >= 5000:
                    partial = True
                    break
                scanned_fds += 1
                try:
                    st = fd.stat()
                except (OSError, PermissionError):
                    partial = True
                    continue
                for row in targets.get((st.st_dev, st.st_ino), ()):
                    row["open_handle_observed"] = True
    except (OSError, PermissionError):
        partial = True
    marked = sum(row.get("open_handle_observed", False) is True for row in rows[:100])
    result.update({
        "observed_open_files": marked,
        "files_open_unknown": len(rows) - marked,
        "scanned_processes": scanned_procs,
        "scanned_fds": scanned_fds,
        "observation_incomplete": partial,
    })
    return result


def filesystem():
    root = os.statvfs("/")
    return {"total_bytes": root.f_blocks * root.f_frsize,
            "free_bytes": root.f_bavail * root.f_frsize}


def disk_pressure(free_bytes: int, candidate_bytes: int) -> dict:
    """Separate measured headroom from hypothetical gains that were not freed."""
    reserve = TRAINING_MIN_FREE_GIB * (1024 ** 3)
    gap = max(0, reserve - free_bytes)
    return {
        "minimum_training_free_bytes": reserve,
        "actual_free_bytes": free_bytes,
        "actual_reserve_met": free_bytes >= reserve,
        "bytes_needed_to_reserve": gap,
        "candidate_assessment_bytes": candidate_bytes,
        "possible_gap_after_verified_pruning_bytes": max(0, gap - candidate_bytes),
        "would_candidate_volume_cover_gap_if_all_safe": candidate_bytes >= gap,
        "actual_bytes_reclaimed_by_inventory": 0,
        "free_space_post_cleanup_verified": False,
        "training_restart_authorized": False,
    }


def archive_mount_status(path: Path = HOME / "VPS-Archief") -> dict:
    """Bounded mount-status observation, not a remote write/crypt attestation."""
    info = {
        "mount_point": str(path), "state": "unknown",
        "filesystem_type": None, "remote_write_verified": False,
        "transfer_authorized": False,
    }
    if path.is_symlink():
        info["state"] = "unsafe_symlink"
        return info
    try:
        for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            head, sep, tail = line.partition(" - ")
            fields, details = head.split(), tail.split()
            if not sep or len(fields) < 6 or len(details) < 3:
                continue
            if fields[4] != str(path):
                continue
            info["filesystem_type"] = details[0]
            options = set(fields[5].split(",")) | set(details[2].split(","))
            info["state"] = "mounted_read_only" if "ro" in options else "mounted_write_unverified"
            return info
        info["state"] = "not_mounted"
    except OSError:
        info["state"] = "mountinfo_unavailable"
    return info



def hotspot_inventory() -> dict:
    """Bounded sizes of known HOT directories, never cleanup candidates.

    Each du -x stays on its own filesystem with a four-second budget;
    report only category/size and keep unknown distinct from zero.
    """
    categories = (
        ("actions_work", HOME / "actions-runner-zcloud" / "_work", "zCloud Actions runner owner"),
        ("user_cache", HOME / ".cache", "VPS cache / consuming-project owners"),
        ("gradle_cache", HOME / ".gradle", "RaiseAI Android build owner"),
        ("raiseai_build", HOME / "raiseai-build", "RaiseAI build owner"),
        ("ftmo_runner_install", Path("/opt/ftmo-runner"), "FTMO runner owner"),
    )
    measured, incomplete = [], []
    for label, target, owner in categories:
        if target.is_symlink() or not target.is_dir():
            incomplete.append({"category": label, "reason": "not_a_verified_directory"})
            continue
        try:
            run = subprocess.run(
                ["du", "-sx", "-B1", "--", str(target)],
                capture_output=True, text=True, timeout=4, check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
            number = run.stdout.strip().split(maxsplit=1)[0] if run.stdout.strip() else ""
            if run.returncode != 0 or not number.isdecimal():
                raise ValueError("incomplete")
            measured.append({
                "category": label, "bytes": int(number), "owner": owner,
                "classification": "hot_or_owner_approval_required",
                "source_is_known_unreferenced": False,
                "can_remove_automatically": False,
            })
        except (OSError, ValueError, subprocess.TimeoutExpired):
            incomplete.append({"category": label, "reason": "measurement_incomplete"})
    return {
        "mode": "explicit_opt_in_bounded_du",
        "measured": sorted(measured, key=lambda row: -row["bytes"]),
        "incomplete": incomplete,
        "scanned_categories": len(categories),
        "all_categories_measured": not incomplete,
        "bytes_proven_reclaimable": 0,
        "source_deletion_authorized": False,
    }




def capacity_recovery_plan(local_capacity: dict, review_bytes: int, target_gib: int,
                           hotspot: dict | None) -> dict:
    """Explain measurable recovery headroom, never manufacture safe cleanup."""
    gib = 1024 ** 3
    free = local_capacity["free_bytes"]
    total = local_capacity["total_bytes"]
    target_bytes = target_gib * gib
    gap = max(0, target_bytes - free)
    candidate_limit = max(0, review_bytes)
    hot_bytes = sum(x["bytes"] for x in (hotspot or {}).get("measured", []))
    if target_bytes > total:
        reason = "target_exceeds_local_volume_capacity"
        recommendation = "Disk/storage owner: expand the local volume or revise the goal; relocating files cannot make the requested free-space target fit."
    elif gap == 0:
        reason = "target_currently_met"
        recommendation = "Storage owner: no removal is needed to meet this target; preserve active workloads and watch for new growth."
    elif candidate_limit < gap:
        reason = "aged_review_scope_insufficient"
        recommendation = "Storage owner: plan additional capacity or identify independently-owned, unreferenced old artifacts. Do not clear active cache, runner work, databases or model files based on directory size."
    else:
        reason = "aged_review_scope_only_hypothetically_sufficient"
        recommendation = "Storage/archive owner: assess immutable old candidates one by one, obtain independent recovery proof and authorized retention approval; no automated cleanup permitted."
    return {
        "requested_free_gib": target_gib,
        "requested_free_bytes": target_bytes,
        "local_total_bytes": total,
        "local_free_bytes": free,
        "shortfall_bytes": gap,
        "reason": reason,
        "recommended_owner_action": recommendation,
        "aged_review_upper_bound_bytes": candidate_limit,
        "hot_directories_observed_bytes": hot_bytes,
        "hot_directories_reclaimable_proven_bytes": 0,
        "actually_reclaimed_bytes": 0,
        "archive_copied_verified": False,
        "cleanup_authorized": False,
        "training_restart_authorized": False,
    }


def compare_storage_snapshots(previous_path: Path, current: dict) -> dict:
    """Bounded operator-owned snapshots: compare measurements, not cleanup proofs."""
    try:
        if (previous_path.is_symlink() or not previous_path.is_file()
                or previous_path.stat().st_size > 131072):
            raise ValueError("snapshot denied")
        prior = json.loads(previous_path.read_text(encoding="utf-8"))
        if not isinstance(prior, dict) or prior.get("schema") != "zcloud-storage-tier-plan-v1":
            raise ValueError("wrong schema")
        stamp = prior.get("observed_at")
        if not isinstance(stamp, str) or len(stamp) > 64:
            raise ValueError("timestamp missing")
        before = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        after = dt.datetime.fromisoformat(current["observed_at"])
        if before.tzinfo is None or before.utcoffset() is None or before > after:
            raise ValueError("timestamp invalid")
        previous_free = prior.get("filesystem", {}).get("free_bytes")
        current_free = current["filesystem"]["free_bytes"]
        if (type(previous_free) is not int or not 0 <= previous_free <= (1 << 63)
                or type(current_free) is not int or not 0 <= current_free <= (1 << 63)):
            raise ValueError("bad free bytes")
        def categories(inventory: object) -> dict[str, int] | None:
            if inventory is None:
                return None
            if not isinstance(inventory, dict) or not isinstance(inventory.get("measured"), list):
                raise ValueError("bad hot paths")
            values = {}
            for row in inventory["measured"]:
                if not isinstance(row, dict):
                    raise ValueError("bad row")
                label, size = row.get("category"), row.get("bytes")
                if (not isinstance(label, str) or label not in {
                    "actions_work", "user_cache", "gradle_cache", "raiseai_build", "ftmo_runner_install"
                } or label in values or type(size) is not int or not 0 <= size <= (1 << 63)):
                    raise ValueError("bad category")
                values[label] = size
            return values
        old = categories(prior.get("hotspot_inventory"))
        new = categories(current.get("hotspot_inventory"))
        changes = []
        if old is not None and new is not None:
            for name in sorted(set(old) & set(new)):
                changes.append({"category": name, "old_bytes": old[name],
                                "new_bytes": new[name], "delta_bytes": new[name] - old[name]})
            changes.sort(key=lambda row: -row["delta_bytes"])
        return {
            "source": "operator_supplied_read_only_snapshot",
            "previous_observed_at": before.isoformat(),
            "free_space_delta_bytes": current_free - previous_free,
            "local_space_lost_bytes": max(0, previous_free - current_free),
            "hot_path_deltas": changes,
            "hot_path_observations_comparable": old is not None and new is not None
                                              and set(old) == set(new),
            "source_deletion_inferred": False,
            "archive_copy_or_restore_inferred": False,
        }
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError, json.JSONDecodeError):
        raise ValueError("Not a valid bounded zCloud storage inventory snapshot.") from None



def render_html(report: dict) -> str:
    """Self-contained responsive operator view, no JS or external resources."""
    esc = lambda value: escape(str(value), quote=True)
    gib = 1024 ** 3
    pressure = report["disk_pressure"]
    remaining = pressure["bytes_needed_to_reserve"]
    reserve_state = "Below operational reserve" if remaining else "Reserve currently met"
    archive = report["archive_mount"]
    plan = report.get("capacity_plan")
    planning_html = ""
    if plan is not None:
        planning_html = (
            '<section aria-labelledby="capacity-plan"><h2 id="capacity-plan">Capacity recovery plan</h2>'
            '<p class="metric">' + esc(f'{plan["shortfall_bytes"] / gib:.2f} GiB') +
            '</p><p>Still needed for ' + esc(plan["requested_free_gib"]) +
            ' GiB free-space target.</p>'
            '<p><strong>' + esc(plan["reason"]) + '</strong></p>'
            '<p>' + esc(plan["recommended_owner_action"]) + '</p>'
            '<p class="muted">Reviewed aged files are only hypothetical;'
            ' authorized cleanup and reclaimed bytes: zero.</p></section>'
        )
    rows = []
    hot = report.get("hotspot_inventory")
    if hot is not None:
        for item in hot["measured"]:
            rows.append(
                "<tr><th scope='row'>" + esc(item["category"]) + "</th>"
                "<td>" + esc(f'{item["bytes"] / gib:.2f} GiB') + "</td>"
                "<td>" + esc(item["owner"]) + "</td>"
                "<td>Owner approval required</td></tr>"
            )
        for item in hot["incomplete"]:
            rows.append("<tr><th scope='row'>" + esc(item["category"]) + "</th>"
                        "<td>Unknown</td><td>Owner to inspect</td><td>Incomplete scan</td></tr>")
    else:
        rows.append("<tr><td colspan='4'>Optional hot-path inventory not requested.</td></tr>")

    review_rows = "".join(
        "<tr><th scope='row'>" + esc(kind) + "</th>"
        "<td>" + esc(report["cold_candidate_counts"].get(kind, 0)) + "</td>"
        "<td>" + esc(f'{size / (1024 ** 2):.2f} MiB') + "</td>"
        "<td>Open-handle / owner review required</td></tr>"
        for kind, size in sorted(report["cold_candidate_bytes"].items())
    ) or "<tr><td colspan='4'>No aged file candidates observed.</td></tr>"
    omitted = report.get("examples_omitted", 0)
    scan_warning = (
        '<p class="warning">Inventory scan incomplete: counts and bytes are only lower bounds; '
        'never authorize archive cleanup from this report.</p>'
        if not report.get("inventory_complete", False) else ""
    )
    archive_review_html = (
        '<section aria-labelledby="candidate-review"><h2 id="candidate-review">Aged file owner review</h2>'
        '<p class="muted">Age does not prove a log is closed, a release superseded,'
        ' an archive transfer verified or a file safe to delete.</p>'
        + scan_warning +
        '<table><thead><tr><th>Owner review category</th><th>Count</th>'
        '<th>Size</th><th>Disposition</th></tr></thead><tbody>'
        + review_rows + '</tbody></table>'
        '<p class="fine">Total observed: ' + esc(report.get("total_candidate_count", 0))
        + ' files. Item examples omitted beyond limit: ' + esc(omitted)
        + '. Automatically reclaimable: 0 bytes.</p></section>'
    )
    hints = report.get("open_handle_hints")
    hint_section = ""
    if hints is not None:
        hint_section = (
            "<section aria-labelledby='handle'><h2 id='handle'>Open-file warning</h2>"
            "<p>Open source-file handles actually observed: <strong>" +
            esc(hints["observed_open_files"]) + "</strong> in " +
            esc(hints["sampled_candidates"]) + " displayed review objects.</p>"
            "<p class='muted'>Non-matches, partial /proc visibility and process exits never "
            "prove the file is closed. Owner verification remains mandatory.</p></section>"
        )
    change = report.get("storage_change")
    change_section = ""
    if change is not None:
        deltas = "".join(
            "<tr><th scope='row'>" + esc(item["category"]) + "</th>"
            "<td>" + esc(f'{item["delta_bytes"] / gib:+.3f} GiB') + "</td></tr>"
            for item in change["hot_path_deltas"]
        ) or "<tr><td colspan='2'>No comparable path sizes.</td></tr>"
        change_section = (
            "<section aria-labelledby='change'><h2 id='change'>Change since previous snapshot</h2>"
            "<p>Local free-space change: <strong>"
            + esc(f'{change["free_space_delta_bytes"] / gib:+.3f} GiB')
            + "</strong>. This does not prove deletion, upload or archival integrity.</p>"
            "<table><thead><tr><th>Category</th><th>Change</th></tr></thead><tbody>"
            + deltas + "</tbody></table></section>"
        )
    css = """
    :root{font:16px/1.55 system-ui,Arial,sans-serif;color:#1d2937;background:#f5f7f9}
    *{box-sizing:border-box}body{margin:0}main{max-width:1000px;margin:auto;padding:clamp(16px,4vw,40px)}
    h1{font-size:clamp(1.6rem,3vw,2.2rem);line-height:1.18;margin:0}
    h2{font-size:1.2rem;margin:0 0 14px}
    header{border-bottom:1px solid #cdd4db;padding-bottom:22px}
    .muted{color:#52606d}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(195px,1fr));gap:12px}
    .card,section{background:white;border:1px solid #dce3e9;border-radius:12px;padding:20px}
    section{margin:18px 0;overflow-x:auto}.metric{font-size:1.75rem;font-weight:750;line-height:1.2}
    table{width:100%;border-collapse:collapse;font-size:.95rem}th,td{text-align:left;padding:10px;border-bottom:1px solid #edf0f3}
    th{font-weight:650}thead th{color:#52606d}.warning{border-left:4px solid #8d6b2b;padding-left:12px}
    .fine{font-size:.85rem}footer{padding:18px 0;color:#52606d}
    @media(max-width:520px){section,.card{padding:14px}th,td{padding:8px}}
    @media print{:root{background:white}section,.card{break-inside:avoid}}
    """
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>zCloud storage owner overview</title><style>' + css + '</style></head><body><main>'
        '<header><h1>zCloud VPS storage overview</h1>'
        '<p class="muted">Read-only observation · ' + esc(report["observed_at"])
        + '</p><p class="warning"><strong>' + esc(reserve_state)
        + '</strong> · Nothing here authorizes migration, deletion or restart.</p></header>'
        '<section aria-label="Disk capacity"><div class="cards">'
        '<div class="card"><div class="metric">' + esc(f'{pressure["actual_free_bytes"]/gib:.2f}') +
        ' GiB</div><div class="muted">Actually free on local root</div></div>'
        '<div class="card"><div class="metric">' + esc(f'{remaining/gib:.2f}') +
        ' GiB</div><div class="muted">Remaining training reserve gap</div></div>'
        '<div class="card"><div class="metric">' + esc(f'{report["total_candidate_bytes"]/gib:.3f}') +
        ' GiB</div><div class="muted">Unverified aged file assessment only</div></div>'
        '</div></section>'
        '<section aria-labelledby="hot"><h2 id="hot">Large local folders</h2>'
        '<p class="muted">These are live or owner-protected footprints. Size is NOT reclaimable storage.</p>'
        '<table><thead><tr><th>Category</th><th>Size</th><th>Responsible owner</th><th>Disposition</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody></table></section>'
        + planning_html + archive_review_html + hint_section + change_section +
        '<section aria-labelledby="archive"><h2 id="archive">External archive</h2>'
        '<p>Browse mount status: <strong>' + esc(archive["state"]) + '</strong>.</p>'
        '<p class="muted">The existing crypt-backed remote uploader and independent checksum/restore '
        'proof are separate, owner-gated processes.</p></section>'
        '<footer class="fine">Transferred: 0 bytes · deleted: 0 bytes · '
        'training restart authorized: no. Current source report is not a security or release approval.</footer>'
        '</main></body></html>'
    )


def operator_summary(report: dict) -> str:
    """Plain-language CLI view: no speculative reclaimed totals."""
    gb = 1024 ** 3
    pressure = report["disk_pressure"]
    lines = [
        "zCloud VPS storage: read-only assessment (NOT cleanup permission)",
        f"Local free: {pressure['actual_free_bytes'] / gb:.2f} GiB",
        f"Training reserve: {pressure['minimum_training_free_bytes'] / gb:.2f} GiB",
        f"Still needed: {pressure['bytes_needed_to_reserve'] / gb:.2f} GiB",
        f"Assessed old files: {report['total_candidate_bytes'] / gb:.3f} GiB (NOT safe-to-delete proof)",
        f"Archive browse mount: {report['archive_mount']['state']} (upload NOT authorized)",
    ]
    if not report.get("inventory_complete", False):
        lines.append("WARNING: inventory scan incomplete (" + str(report.get("scan_limit_reason")) +
                     "); all observed totals are lower bounds, never proof of complete archival scope.")
    plan = report.get("capacity_plan")
    if plan is not None:
        lines.append(f"Owner planning target: {plan['requested_free_gib']} GiB; "
                     f"gap: {plan['shortfall_bytes']/gb:.2f} GiB; {plan['reason']}")
        lines.append("Next step: " + plan["recommended_owner_action"])
    lines.append(
        f"Old-file review rows: {report.get('total_candidate_count', 0)}; "
        f"examples omitted: {report.get('examples_omitted', 0)}; "
        "open-handle or owner release NOT verified")
    for kind in sorted(report["cold_candidate_counts"]):
        lines.append(
            f"  {kind}: {report['cold_candidate_counts'][kind]} entries, "
            f"{report['cold_candidate_bytes'][kind]/gb:.4f} GiB (owner review)")
    hints = report.get("open_handle_hints")
    if hints is not None:
        lines.append(
            f"Open-handle hints (displayed candidates only): "
            f"{hints['observed_open_files']} observed open / "
            f"{hints['files_open_unknown']} unverified; "
            "no closed-file proof or permission to move.")
    hotspots = report.get("hotspot_inventory")
    if hotspots is not None:
        lines.append("Large local hot paths: owner review required, NOT reclaimable-by-default")
        for entry in hotspots["measured"]:
            lines.append(f"  {entry['category']}: {entry['bytes'] / gb:.2f} GiB — {entry['owner']}")
        if hotspots["incomplete"]:
            lines.append("  Incomplete: " + ", ".join(row["category"] for row in hotspots["incomplete"]))
    diff = report.get("storage_change")
    if diff is not None:
        lines.append(f"Free-space change: {diff['free_space_delta_bytes'] / gb:+.2f} GiB since previous observation")
        for row in diff["hot_path_deltas"]:
            lines.append(f"  {row['category']}: {row['delta_bytes'] / gb:+.2f} GiB since previous observation")
        if not diff["hot_path_observations_comparable"]:
            lines.append("  Hot-path comparison incomplete: never assume absent equals zero.")
    lines.append("Files moved: 0. Files deleted: 0. Training restart authorized: no.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--older-than-days", type=int, default=MIN_AGE_DAYS)
    ap.add_argument("--target-free-gib", type=int, default=10,
                    help="Non-authorizing local free-space goal for owner planning (10..1000 GiB)")
    ap.add_argument("--limit", type=int, default=MAX_LIST)
    ap.add_argument("--previous-report", type=Path, default=None,
                    help="Read-only bounded comparison with a previous private JSON snapshot")
    ap.add_argument("--open-handle-hints", action="store_true",
                    help="Bounded read-only positive /proc FD hints on up to 100 shown old files; never proves idle")
    ap.add_argument("--hotspot-inventory", action="store_true",
                    help="Bounded size assessment of named hot directories, never cleanup permission")
    ap.add_argument("--format", choices=("json", "text", "html"), default="json")
    ap.add_argument("--include-release-reviews", action="store_true",
                    help="Also assess old standalone APK exports; never assert superseded or safe to prune")
    args = ap.parse_args()
    if args.older_than_days < 3 or args.limit < 1 or args.limit > 5000 or not 10 <= args.target_free_gib <= 1000:
        ap.error("requires at least 3 cold days and 1..5000 displayed candidates")
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - args.older_than_days * 86400
    totals, count, examples = {}, {}, []
    total_count = 0
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    additional = old_app_release_review(cutoff, deadline) if args.include_release_reviews else ()
    scan_limit_reason = None
    for row in chain(runner_diagnostics(cutoff, deadline), training_reports(cutoff, deadline), additional):
        if time.monotonic() >= deadline:
            scan_limit_reason = "scan_time_budget_exhausted"
            break
        if total_count >= MAX_SCAN_CANDIDATES:
            scan_limit_reason = "max_candidate_limit_reached"
            break
        total_count += 1
        kind = row["kind"]
        totals[kind] = totals.get(kind, 0) + row["bytes"]
        count[kind] = count.get(kind, 0) + 1
        if len(examples) < args.limit:
            examples.append(row)
    if scan_limit_reason is None and time.monotonic() >= deadline:
        scan_limit_reason = "scan_time_budget_exhausted"
    handle_hints = observe_open_handle_hints(examples[:100]) if args.open_handle_hints else None
    live_filesystem = filesystem()
    total_assessed = sum(totals.values())
    hot_inventory = hotspot_inventory() if args.hotspot_inventory else None
    result = {
        "schema": "zcloud-storage-tier-plan-v1",
        "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "read_only": True,
        "remote": ARCHIVE,
        "minimum_age_days": args.older_than_days,
        "review_only_app_release_minimum_age_days": 14,
        "review_only_app_releases_included": args.include_release_reviews,
        "filesystem": live_filesystem,
        "disk_pressure": disk_pressure(live_filesystem["free_bytes"], total_assessed),
        "archive_mount": archive_mount_status(),
        "hotspot_inventory": hot_inventory,
        "capacity_plan": capacity_recovery_plan(live_filesystem, total_assessed,
                                                 args.target_free_gib, hot_inventory),
        "open_handle_hints": handle_hints,
        "cold_candidate_counts": count,
        "cold_candidate_bytes": totals,
        "total_candidate_bytes": total_assessed,
        "total_candidate_count": total_count,
        "inventory_complete": scan_limit_reason is None,
        "scan_limit_reason": scan_limit_reason,
        "scan_seconds_budget": MAX_SCAN_SECONDS,
        "scan_candidate_limit": MAX_SCAN_CANDIDATES,
        "examples_limit": args.limit,
        "examples_omitted": max(0, total_count - len(examples)),
        "archive_transfer_attempted": False,
        "examples": examples,
        "excluded": [
            "live code, GitHub runner executables, working checkouts and active logs",
            "HaxLab raw/derived training shards, manifests, databases and models",
            "Fuckball active cycle, latest checkpoints and 50 most recent reports",
            "FTMO live trading/paper state, credentials and browser profiles",
            "files with multiple hardlinks or symlinks",
            "unproven superseded APK exports (manual review only, 14+ day minimum)",
        ],
        "move_policy": "Old file age does not prove closed file handles or inactive ownership. Explicit owner review plus existing encrypted remote full-byte verification required before any authorized transfer or cleanup.",
        "source_deletion_authorized": False,
        "remote_copies_verified": False,
        "bytes_reclaimed": 0,
    }
    if args.previous_report is not None:
        try:
            result["storage_change"] = compare_storage_snapshots(args.previous_report, result)
        except ValueError as error:
            ap.error(str(error))
    if args.format == "text":
        print(operator_summary(result))
    elif args.format == "html":
        print(render_html(result))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
