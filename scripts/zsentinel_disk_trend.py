#!/usr/bin/env python3
"""Private read-only comparison of two bounded zSentinel disk observations."""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

SCHEMA = "zsentinel-disk-pressure-workbench-v1"
TREND = "zsentinel-disk-observed-change-v1"
MAX_BYTES = 512 * 1024
PARAMS = ("min_mib", "max_entries", "max_depth", "max_seconds")


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate snapshot key")
        out[key] = value
    return out


def load_previous(path):
    """Private regular 0600 JSON; no symlinks, paths or secret content published."""
    if not isinstance(path, Path) or not path.is_absolute() or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("absolute private snapshot path and nofollow required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or not 1 <= info.st_size <= MAX_BYTES:
            raise ValueError("snapshot must be private bounded regular file")
        data = os.read(fd, MAX_BYTES + 1)
        if not 1 <= len(data) <= MAX_BYTES:
            raise ValueError("snapshot too large or empty")
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
    finally:
        os.close(fd)


def _utc(value):
    if type(value) is not str or len(value) > 45:
        raise ValueError("invalid observation clock")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid observation clock") from exc
    if date.tzinfo is None or date.utcoffset() != timedelta(0):
        raise ValueError("UTC clock required")
    return date.astimezone(timezone.utc)


def _source(source):
    if (type(source) is not dict or source.get("schema") != SCHEMA
            or any(source.get(k) is not False for k in
                   ("deletion_authorized", "queue_claims_changed",
                    "runner_restart_authorized", "file_contents_read"))
            or source.get("coverage") not in
                   ("partial", "bounded_complete_with_no_private_exclusions")
            or type(source.get("workspace_groups_truncated")) is not bool):
        raise ValueError("untrusted source or authorizing flags")
    root = source.get("root")
    params = source.get("scan_parameters")
    total = source.get("observed_allocated_bytes_lower_bound")
    groups = source.get("workspace_groups_sample")
    if (type(root) is not str or not root.startswith("/") or len(root) > 1024
            or type(params) is not dict or set(params) != set(PARAMS)
            or any(type(params[key]) is not int for key in PARAMS)
            or not 1 <= params["min_mib"] <= 1048576
            or not 1 <= params["max_entries"] <= 20000
            or not 0 <= params["max_depth"] <= 4
            or not 1 <= params["max_seconds"] <= 5
            or type(total) is not int or not 0 <= total <= 10**15
            or type(groups) is not list or len(groups) > 128):
        raise ValueError("invalid bounded disk observation")
    mapping = {}
    for row in groups:
        if type(row) is not dict:
            raise ValueError("invalid folder observation")
        name = row.get("relative_group")
        size = row.get("observed_allocated_bytes_min")
        count = row.get("files_observed")
        if (type(name) is not str or not 1 <= len(name) <= 255
                or name in (".", "..") or "/" in name or chr(92) in name
                or any(ord(char) < 32 or ord(char) == 127 for char in name)
                or type(size) is not int or not 0 <= size <= 10**15
                or type(count) is not int or not 0 <= count <= 20000
                or name in mapping):
            raise ValueError("invalid folder identity or count")
        mapping[name] = (size, count)
    mount = source.get("mount_capacity")
    if (type(mount) is not dict
            or mount.get("source") != "same_host_statvfs_observation_not_cleanup_proof"
            or type(mount.get("host")) is not str
            or not 1 <= len(mount["host"]) <= 255
            or any(not (c.isascii() and (c.isalnum() or c in ".-_"))
                   for c in mount["host"])
            or type(mount.get("mount_device")) is not int
            or not 0 <= mount["mount_device"] <= 10**10
            or type(mount.get("filesystem_total_bytes")) is not int
            or not 1 <= mount["filesystem_total_bytes"] <= 10**15
            or type(mount.get("available_bytes_to_operator")) is not int
            or not 0 <= mount["available_bytes_to_operator"] <= mount["filesystem_total_bytes"]
            or type(mount.get("percent_unavailable")) not in (int, float)
            or not 0 <= mount["percent_unavailable"] <= 100):
        raise ValueError("missing or untrusted same-host capacity evidence")
    return root, params, _utc(source.get("observed_at_utc")), total, mapping, mount


def compare(previous, current):
    root1, params1, time1, total1, groups1, mount1 = _source(previous)
    root2, params2, time2, total2, groups2, mount2 = _source(current)
    if root1 != root2 or params1 != params2:
        raise ValueError("same exact workspace and scanner settings required")
    if any(mount1[key] != mount2[key] for key in
           ("host", "mount_device", "filesystem_total_bytes")):
        raise ValueError("same workspace path is not enough; host and filesystem must match")
    if not timedelta(0) < time2 - time1 <= timedelta(hours=72):
        raise ValueError("two strictly ordered observations within 72 hours required")
    common = sorted(set(groups1) & set(groups2))
    changes = [
        {"relative_group": group,
         "observed_delta_bytes": groups2[group][0] - groups1[group][0],
         "observed_file_count_delta": groups2[group][1] - groups1[group][1]}
        for group in common if groups2[group] != groups1[group]
    ]
    changes.sort(key=lambda row: (-row["observed_delta_bytes"], row["relative_group"]))
    return {
        "schema": TREND,
        "observed_previous_utc": time1.isoformat(),
        "observed_current_utc": time2.isoformat(),
        "coverage": "bounded_sample_difference_not_reclaimed_space",
        "any_partial_snapshot": previous["coverage"] == "partial" or current["coverage"] == "partial",
        "any_truncated_groups": previous["workspace_groups_truncated"] or current["workspace_groups_truncated"],
        "observed_allocated_difference_bytes_not_disk_growth": total2 - total1,
        "mount_free_previous_bytes": mount1["available_bytes_to_operator"],
        "mount_free_current_bytes": mount2["available_bytes_to_operator"],
        "mount_free_change_bytes_not_cleanup_proof": (
            mount2["available_bytes_to_operator"] - mount1["available_bytes_to_operator"]),
        "mount_unavailable_previous_percent": mount1["percent_unavailable"],
        "mount_unavailable_current_percent": mount2["percent_unavailable"],
        "shared_groups": len(common),
        "unmatched_previous_groups": len(groups1) - len(common),
        "unmatched_current_groups": len(groups2) - len(common),
        "changed_shared_groups": changes[:20],
        "changed_groups_truncated": len(changes) > 20,
        "disk_freed_verified": False, "cleanup_authorized": False,
        "runner_recovered_verified": False, "claims_changed": False,
    }


def as_html(trend):
    if (type(trend) is not dict or trend.get("schema") != TREND
            or trend.get("cleanup_authorized") is not False
            or trend.get("disk_freed_verified") is not False):
        raise ValueError("untrusted trend for HTML")
    cards = "".join("<li><code>" + escape(row["relative_group"]) +
                    "</code>: waargenomen verschil " +
                    str(round(row["observed_delta_bytes"] / 1048576, 1)) +
                    " MiB, geen bewezen schijfgroei</li>"
                    for row in trend["changed_shared_groups"])
    return (
        '<section aria-label="VPS schijfverschil tussen privémomentopnamen">'
        '<h2>Schijfverschil · twee privé-momentopnamen</h2>'
        '<p>Waargenomen verschilsom: ' +
        str(round(trend["observed_allocated_difference_bytes_not_disk_growth"] / 1048576, 1)) +
        ' MiB. Dit is GEEN bewijs van vrijgemaakt volume of extra ruimtegebruik.'
        ' Gesamplede groepen veranderen ook door afkapping en actieve schrijvers.</p>'
        '<p>Beschikbaar op dezelfde host/mount: ' +
        str(round(trend["mount_free_previous_bytes"] / (1024**3), 2)) +
        ' GiB → ' + str(round(trend["mount_free_current_bytes"] / (1024**3), 2)) +
        ' GiB. Verschil: ' +
        str(round(trend["mount_free_change_bytes_not_cleanup_proof"] / (1024**2), 1)) +
        ' MiB (statvfs; GEEN bewijs van veilig opgeschoonde bestanden).</p>'
        '<p>Dezelfde groepen: ' + str(trend["shared_groups"]) +
        '; alleen eerder gezien: ' + str(trend["unmatched_previous_groups"]) +
        '; alleen recent gezien: ' + str(trend["unmatched_current_groups"]) +
        ' (afwezig betekent ONBEKEND).</p><ul>' + cards + '</ul>'
        '<p>Geen verwijdering, claimvrijgave, runnerherstel of deployrecht.</p>'
        '</section>'
    )


def as_markdown(trend):
    if (type(trend) is not dict or trend.get("schema") != TREND
            or trend.get("disk_freed_verified") is not False):
        raise ValueError("untrusted private comparison")
    lines = ["## Schijfverschil tussen privé-observaties", "",
             "**Verschil in gesamplede blokken: " +
             str(round(trend["observed_allocated_difference_bytes_not_disk_growth"] / 1048576, 1)) +
             " MiB. Geen bewijs van vrijgekomen of nieuw verbruik.**",
             "Op dezelfde host/mount vrij volgens statvfs: " +
             str(round(trend["mount_free_previous_bytes"] / (1024**3), 2)) + " → " +
             str(round(trend["mount_free_current_bytes"] / (1024**3), 2)) +
             " GiB; andere processen kunnen dit beïnvloeden.",
             "Gemeenschappelijke groepen: " + str(trend["shared_groups"]) +
             "; alleen oud: " + str(trend["unmatched_previous_groups"]) +
             "; alleen recent: " + str(trend["unmatched_current_groups"]) + "."]
    for row in trend["changed_shared_groups"]:
        lines.append("- " + row["relative_group"].replace(chr(10), " ") +
                     ": " + str(round(row["observed_delta_bytes"] / 1048576, 1)) +
                     " MiB waargenomen verschil")
    lines.append("Geen cleanup-, worker- of claimbevoegdheid.")
    return "\n".join(lines)
