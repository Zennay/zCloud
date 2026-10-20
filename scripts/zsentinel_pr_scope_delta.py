#!/usr/bin/env python3
"""Compare two private, bounded zSentinel PR snapshots; never infer ownership."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import stat

SCHEMA = "zsentinel-portfolio-pr-snapshot-delta-v1"
BOARD = "zsentinel-portfolio-pr-collision-board-v1"
SHA = re.compile(r"^[a-f0-9]{40}$")
MAX_BYTES = 2 * 1024 * 1024
MAX_AGE_SECONDS = 7 * 86400


def _clock(value):
    if type(value) is not str or len(value) > 48:
        raise ValueError("snapshot timestamp invalid")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("snapshot timestamp invalid") from None
    if dt.tzinfo is None:
        raise ValueError("snapshot UTC offset absent")
    return dt.astimezone(timezone.utc)


def read_private_previous(path: Path):
    """No follow, no shared/other-readable historical PR metadata."""
    if not isinstance(path, Path) or path.is_symlink():
        raise ValueError("previous snapshot must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        meta = os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode)
                or meta.st_uid != os.geteuid()
                or meta.st_mode & 0o077
                or not 0 < meta.st_size <= MAX_BYTES):
            raise ValueError("previous snapshot must be private 0600 and bounded")
        with os.fdopen(fd, "rb", closefd=False) as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("previous snapshot oversized")
    finally:
        os.close(fd)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate snapshot JSON keys")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("previous snapshot is not valid JSON") from None


def _pins(view, repo):
    result = {}
    rows = view.get("sampled_open_pr_heads")
    if type(rows) is not list or len(rows) > 16:
        raise ValueError("PR source identities invalid")
    for row in rows:
        if type(row) is not dict:
            raise ValueError("PR source row invalid")
        number, head, base = row.get("number"), row.get("head_sha"), row.get("base_sha")
        if (type(number) is not int or not 0 < number < 100000000
                or type(head) is not str or SHA.fullmatch(head) is None
                or type(base) is not str or SHA.fullmatch(base) is None
                or row.get("url") != f"https://github.com/{repo}/pull/{number}"
                or number in result):
            raise ValueError("PR source identity or SHA invalid")
        result[number] = (head, base)
    return result


def _pairs(view, repo):
    pairs = set()
    rows = view.get("shown_collision_pairs")
    if type(rows) is not list or len(rows) > 80:
        raise ValueError("collision source invalid")
    for row in rows:
        if type(row) is not dict:
            raise ValueError("collision row invalid")
        numbers = []
        for side in ("left", "right"):
            pr = row.get(side)
            if type(pr) is not dict or type(pr.get("number")) is not int:
                raise ValueError("collision PR invalid")
            number = pr["number"]
            if pr.get("url") != f"https://github.com/{repo}/pull/{number}":
                raise ValueError("collision source URL mismatch")
            numbers.append(number)
        if numbers[0] == numbers[1]:
            raise ValueError("collision cannot refer to same PR")
        key = tuple(sorted(numbers))
        if key in pairs:
            raise ValueError("duplicate collision pair")
        pairs.add(key)
    return pairs


def compare(previous, current):
    """Positive source observations only; incomplete snapshots cannot prove removal."""
    if (type(previous) is not dict or type(current) is not dict
            or previous.get("schema") != BOARD or current.get("schema") != BOARD
            or any(doc.get(key) is not False for doc in (previous, current)
                   for key in ("claim_authorized", "merge_authorized",
                               "deploy_authorized", "writes_performed"))):
        raise ValueError("not a read-only zSentinel portfolio snapshot")
    projects = current.get("projects")
    if (type(projects) is not list or not 1 <= len(projects) <= 3
            or projects != previous.get("projects") or len(set(projects)) != len(projects)):
        raise ValueError("both snapshots must contain identical ordered projects")
    earlier, now = _clock(previous.get("observed_at_utc")), _clock(current.get("observed_at_utc"))
    age = (now - earlier).total_seconds()
    if age < 0 or age > MAX_AGE_SECONDS or now > datetime.now(timezone.utc):
        raise ValueError("snapshots must be ordered, not future, and within seven days")
    old_views, new_views = previous.get("project_views"), current.get("project_views")
    if (type(old_views) is not list or type(new_views) is not list
            or len(old_views) != len(projects) or len(new_views) != len(projects)):
        raise ValueError("snapshot project views unavailable")
    result = []
    for project, old, new in zip(projects, old_views, new_views):
        if type(old) is not dict or type(new) is not dict:
            raise ValueError("invalid project snapshot")
        repo = old.get("repository")
        if (old.get("project") != project or new.get("project") != project
                or type(repo) is not str or not re.fullmatch(r"Zennay/[A-Za-z0-9_.-]{1,100}", repo)
                or new.get("repository") != repo):
            raise ValueError("project or repository changed across snapshots")
        old_stable = old.get("open_pr_snapshot_stable") is True and old.get("source_status") != "unavailable"
        new_stable = new.get("open_pr_snapshot_stable") is True and new.get("source_status") != "unavailable"
        stable = old_stable and new_stable
        both_complete = (stable and old.get("sample_complete") is True
                         and new.get("sample_complete") is True)
        old_pins = _pins(old, repo) if old_stable else {}
        new_pins = _pins(new, repo) if new_stable else {}
        head_changes = []
        for number in sorted(old_pins.keys() & new_pins.keys()):
            if old_pins[number] != new_pins[number]:
                head_changes.append({
                    "url": f"https://github.com/{repo}/pull/{number}",
                    "previous_head": old_pins[number][0],
                    "current_head": new_pins[number][0],
                    "base_changed": old_pins[number][1] != new_pins[number][1],
                    "head_changed": old_pins[number][0] != new_pins[number][0],
                })
        old_pairs = _pairs(old, repo) if both_complete else set()
        new_pairs = _pairs(new, repo) if both_complete else set()
        def describe(pairs):
            return [{"left": f"https://github.com/{repo}/pull/{a}",
                     "right": f"https://github.com/{repo}/pull/{b}"}
                    for a, b in sorted(pairs)[:40]]
        result.append({
            "project": project, "repository": repo,
            "both_snapshots_stable": stable,
            "complete_open_pr_inventory_comparable": both_complete,
            "head_or_base_changed_in_both_samples": head_changes if stable else [],
            "seen_only_in_current_sample_not_proven_new": (
                [f"https://github.com/{repo}/pull/{n}"
                 for n in sorted(new_pins.keys() - old_pins.keys())] if stable else []),
            "seen_only_in_previous_sample_not_proven_closed": (
                [f"https://github.com/{repo}/pull/{n}"
                 for n in sorted(old_pins.keys() - new_pins.keys())] if stable else []),
            "added_collision_pairs_if_complete": describe(new_pairs - old_pairs),
            "disappeared_collision_pairs_if_complete": describe(old_pairs - new_pairs),
            "absence_of_conflicts_proven": False,
            "claim_authorized": False, "merge_authorized": False,
        })
    return {
        "schema": SCHEMA,
        "previous_observed_at_utc": earlier.isoformat(),
        "current_observed_at_utc": now.isoformat(),
        "elapsed_seconds": int(age),
        "projects": result,
        "current_release_authorized": False,
        "claim_authorized": False, "merge_authorized": False,
        "writes_performed": False,
    }


def render_delta(delta, format="text"):
    """Source-link-first operator delivery: never label sample drift as closure."""
    if not delta:
        return ""
    if format == "html":
        def link(url):
            return ("<a rel='noopener noreferrer' href='" +
                    escape(url, quote=True) + "'>" + escape(url) + "</a>")
        sections = []
        for row in delta["projects"]:
            links = []
            for changed in row["head_or_base_changed_in_both_samples"]:
                links.append("<li>HEAD/basis gewijzigd: " + link(changed["url"]) +
                             " (" + escape(changed["previous_head"][:12]) +
                             " → " + escape(changed["current_head"][:12]) + ")</li>")
            for url in row["seen_only_in_current_sample_not_proven_new"]:
                links.append("<li>Alleen huidige steekproef: " + link(url) +
                             " (niet bewezen nieuw)</li>")
            for url in row["seen_only_in_previous_sample_not_proven_closed"]:
                links.append("<li>Alleen vorige steekproef: " + link(url) +
                             " (niet bewezen gesloten)</li>")
            if row["complete_open_pr_inventory_comparable"]:
                for pair in row["added_collision_pairs_if_complete"]:
                    links.append("<li>Nieuwe waargenomen bestandsoverlap: " +
                                 link(pair["left"]) + " ↔ " + link(pair["right"]) + "</li>")
                for pair in row["disappeared_collision_pairs_if_complete"]:
                    links.append("<li>Niet meer waargenomen overlap: " +
                                 link(pair["left"]) + " ↔ " + link(pair["right"]) +
                                 " (geen bewijs van geslaagde merge of release)</li>")
            sections.append("<section><h3>" + escape(row["project"]) + "</h3><p>" +
                            ("Twee volledige stabiele open-PR snapshots; claims NIET gecontroleerd."
                             if row["complete_open_pr_inventory_comparable"] else
                             "ONVOLLEDIGE dekking: geen bewijs dat PR's gesloten zijn of botsingen verdwenen.") +
                            "</p><ul>" + ("".join(links) or
                            "<li>Geen verandering aantoonbaar in deze bron.</li>") +
                            "</ul></section>")
        return ("<section><h2>Historische PR-verandering (niet-autoriserend)</h2><p>" +
                escape(delta["previous_observed_at_utc"]) + " → " +
                escape(delta["current_observed_at_utc"]) +
                "</p>" + "".join(sections) + "</section>")
    lines = ["Historische PR-verandering: " + delta["previous_observed_at_utc"] +
             " → " + delta["current_observed_at_utc"],
             "Alleen waargenomen bronverschillen; GEEN claim- of mergevrijgave."]
    for row in delta["projects"]:
        lines.append(row["project"] + ": " +
                     ("beide open-PR inventarissen volledig" if row["complete_open_pr_inventory_comparable"]
                      else "ONVOLLEDIG: gesloten PR/botsing niet bewezen") +
                     ", gewijzigde HEAD/basis " +
                     str(len(row["head_or_base_changed_in_both_samples"])) +
                     ", huidige-steekproef-only " +
                     str(len(row["seen_only_in_current_sample_not_proven_new"])) +
                     ", vorige-steekproef-only " +
                     str(len(row["seen_only_in_previous_sample_not_proven_closed"])) +
                     ", nieuwe overlapparen " +
                     str(len(row["added_collision_pairs_if_complete"])) +
                     ", niet-meer-geobserveerde overlapparen " +
                     str(len(row["disappeared_collision_pairs_if_complete"])))
        for item in row["head_or_base_changed_in_both_samples"]:
            lines.append("  HEAD/basis: " + item["url"] + " " +
                         item["previous_head"][:12] + " → " + item["current_head"][:12])
        for url in row["seen_only_in_current_sample_not_proven_new"]:
            lines.append("  Alleen huidige steekproef, niet bewezen nieuw: " + url)
        for url in row["seen_only_in_previous_sample_not_proven_closed"]:
            lines.append("  Alleen vorige steekproef, niet bewezen gesloten: " + url)
        if row["complete_open_pr_inventory_comparable"]:
            for pair in row["added_collision_pairs_if_complete"]:
                lines.append("  Nieuw waargenomen overlap: " +
                             pair["left"] + " ↔ " + pair["right"])
            for pair in row["disappeared_collision_pairs_if_complete"]:
                lines.append("  Niet meer waargenomen overlap, GEEN merge-bewijs: " +
                             pair["left"] + " ↔ " + pair["right"])
    if format == "markdown":
        return "\n".join(["## " + lines[0], ""] + ["- " + line for line in lines[1:]]) + "\n"
    return "\n".join(lines)
