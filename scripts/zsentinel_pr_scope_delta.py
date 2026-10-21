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


def _scope_path(value):
    if (type(value) is not str or not 1 <= len(value) <= 180
            or value.startswith("/") or "\\" in value or "//" in value
            or any(part in ("", ".", "..") for part in value.split("/"))
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("scope conflict path invalid")
    return value


def _scope_pairs(view, repo):
    """Return optional path-pair identities; None means a pre-feature snapshot."""
    rows = view.get("shown_path_scope_collision_pairs")
    if rows is None:
        return None
    if type(rows) is not list or len(rows) > 80:
        raise ValueError("scope collision source invalid")
    result = set()
    allowed = {"left_file_conflicts_with_right_descendant",
               "right_file_conflicts_with_left_descendant"}
    for row in rows:
        if type(row) is not dict:
            raise ValueError("scope collision row invalid")
        left, right = row.get("left"), row.get("right")
        if type(left) is not dict or type(right) is not dict:
            raise ValueError("scope collision PR invalid")
        a, b = left.get("number"), right.get("number")
        if (type(a) is not int or type(b) is not int or not 0 < a < b < 100000000
                or left.get("url") != f"https://github.com/{repo}/pull/{a}"
                or right.get("url") != f"https://github.com/{repo}/pull/{b}"):
            raise ValueError("scope collision source URL mismatch")
        conflicts = row.get("path_conflicts")
        count = row.get("path_conflict_count_observed")
        truncated = row.get("path_conflicts_truncated")
        if (type(conflicts) is not list or len(conflicts) > 24
                or type(count) is not int or count < 1 or count < len(conflicts)
                or type(truncated) is not bool):
            raise ValueError("scope collision count or preview invalid")
        if truncated != (count > len(conflicts)):
            raise ValueError("scope collision preview completeness mismatch")
        for item in conflicts:
            if type(item) is not dict or item.get("relation") not in allowed:
                raise ValueError("scope collision relation invalid")
            left_path = _scope_path(item.get("left_path"))
            right_path = _scope_path(item.get("right_path"))
            if (item["relation"] == "left_file_conflicts_with_right_descendant"
                    and not right_path.startswith(left_path + "/")):
                raise ValueError("left-file ancestor relation does not match paths")
            if (item["relation"] == "right_file_conflicts_with_left_descendant"
                    and not left_path.startswith(right_path + "/")):
                raise ValueError("right-file ancestor relation does not match paths")
            key = (a, b, left_path, right_path, item["relation"])
            if key in result:
                raise ValueError("duplicate scope collision path pair")
            result.add(key)
    return result


def _connected_pr_sets(pairs):
    """Compute complete observed pair components, not claim/merge rights."""
    adjacency = {}
    for left, right in sorted(pairs):
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
    visited = set()
    groups = []
    for start in sorted(adjacency):
        if start in visited:
            continue
        todo = [start]
        group = set()
        while todo:
            node = todo.pop()
            if node in visited:
                continue
            visited.add(node)
            group.add(node)
            todo.extend(adjacency[node] - visited)
        if len(group) > 1:
            groups.append(tuple(sorted(group)))
    return sorted(groups, key=lambda group: (-len(group), group))


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
        # A busy repository rarely yields a complete sample. Preserve
        # positive *sample-only* overlap movement without pretending it is
        # a newly introduced or truly resolved full-repository collision.
        old_seen_pairs = _pairs(old, repo) if old_stable else set()
        new_seen_pairs = _pairs(new, repo) if new_stable else set()
        old_pairs = old_seen_pairs if both_complete else set()
        new_pairs = new_seen_pairs if both_complete else set()
        old_seen_scope = (_scope_pairs(old, repo)
                          if old.get("source_status") != "unavailable" else None)
        new_seen_scope = (_scope_pairs(new, repo)
                          if new.get("source_status") != "unavailable" else None)
        scope_comparable = (both_complete and old_seen_scope is not None
                            and new_seen_scope is not None)
        old_scope_pairs = old_seen_scope if scope_comparable else set()
        new_scope_pairs = new_seen_scope if scope_comparable else set()
        old_clusters = set(_connected_pr_sets(old_pairs))
        new_clusters = set(_connected_pr_sets(new_pairs))
        def linked_groups(groups):
            return [{
                "pr_numbers_observed": list(members),
                "pr_urls": [f"https://github.com/{repo}/pull/{number}"
                            for number in members],
                "not_proof_of_owner_or_merge": True,
            } for members in sorted(groups, key=lambda group: (-len(group), group))[:24]]
        def describe(pairs):
            return [{"left": f"https://github.com/{repo}/pull/{a}",
                     "right": f"https://github.com/{repo}/pull/{b}"}
                    for a, b in sorted(pairs)[:40]]
        def describe_scope(pairs):
            return [{"left": f"https://github.com/{repo}/pull/{a}",
                     "right": f"https://github.com/{repo}/pull/{b}",
                     "left_path": left_path, "right_path": right_path,
                     "relation": relation}
                    for a, b, left_path, right_path, relation in sorted(pairs)[:40]]
        current_scope_delta = (new_seen_scope - old_seen_scope
                               if stable and old_seen_scope is not None
                               and new_seen_scope is not None else set())
        previous_scope_delta = (old_seen_scope - new_seen_scope
                                if stable and old_seen_scope is not None
                                and new_seen_scope is not None else set())
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
            "scope_conflicts_comparable_if_complete": scope_comparable,
            "added_scope_conflicts_if_complete": describe_scope(new_scope_pairs - old_scope_pairs),
            "disappeared_scope_conflicts_if_complete": describe_scope(old_scope_pairs - new_scope_pairs),
            "scope_conflicts_only_in_current_sample_not_proven_new": (
                describe_scope(current_scope_delta) if stable and not scope_comparable else []),
            "scope_conflicts_only_in_previous_sample_not_proven_resolved": (
                describe_scope(previous_scope_delta) if stable and not scope_comparable else []),
            "scope_conflict_sample_preview_truncated": (
                stable and not scope_comparable and
                (len(current_scope_delta) > 40 or len(previous_scope_delta) > 40)),
            "scope_conflict_comparison_unavailable": (
                old_seen_scope is None or new_seen_scope is None),
            "connected_cluster_comparison_complete": both_complete,
            "current_connected_clusters_if_complete": (
                linked_groups(new_clusters) if both_complete else []),
            "connected_clusters_only_in_current_full_inventory": (
                linked_groups(new_clusters - old_clusters) if both_complete else []),
            "connected_clusters_only_in_previous_full_inventory": (
                linked_groups(old_clusters - new_clusters) if both_complete else []),
            "cluster_changes_prove_merges_or_owner_free": False,
            "overlap_pairs_only_in_current_sample_not_proven_new": (
                describe(new_seen_pairs - old_seen_pairs)
                if stable and not both_complete else []),
            "overlap_pairs_only_in_previous_sample_not_proven_resolved": (
                describe(old_seen_pairs - new_seen_pairs)
                if stable and not both_complete else []),
            "overlap_sample_pair_preview_truncated": (
                stable and not both_complete and
                (len(new_seen_pairs - old_seen_pairs) > 40 or
                 len(old_seen_pairs - new_seen_pairs) > 40)),
            "sample_change_may_reflect_pr_window_drift": not both_complete,
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
            if not row["complete_open_pr_inventory_comparable"]:
                for pair in row["overlap_pairs_only_in_current_sample_not_proven_new"]:
                    links.append("<li>Alleen huidig waargenomen botsingspaar: " +
                                 link(pair["left"]) + " ↔ " + link(pair["right"]) +
                                 " (NIET bewezen nieuw in repository)</li>")
                for pair in row["overlap_pairs_only_in_previous_sample_not_proven_resolved"]:
                    links.append("<li>Alleen eerder waargenomen botsingspaar: " +
                                 link(pair["left"]) + " ↔ " + link(pair["right"]) +
                                 " (NIET bewezen opgelost)</li>")
                if row["overlap_sample_pair_preview_truncated"]:
                    links.append("<li>Meer historische overlapparen buiten zichtbare limiet.</li>")
                for conflict in row.get("scope_conflicts_only_in_current_sample_not_proven_new", []):
                    links.append("<li>Alleen huidige bestand/map-steekproef: " +
                                 link(conflict["left"]) + " · <code>" + escape(conflict["left_path"]) +
                                 "</code> ↔ " + link(conflict["right"]) + " · <code>" +
                                 escape(conflict["right_path"]) + "</code> [" +
                                 escape(conflict["relation"]) + "] (niet bewezen nieuw)</li>")
                for conflict in row.get("scope_conflicts_only_in_previous_sample_not_proven_resolved", []):
                    links.append("<li>Alleen eerdere bestand/map-steekproef: " +
                                 link(conflict["left"]) + " · <code>" + escape(conflict["left_path"]) +
                                 "</code> ↔ " + link(conflict["right"]) + " · <code>" +
                                 escape(conflict["right_path"]) + "</code> [" +
                                 escape(conflict["relation"]) + "] (niet bewezen opgelost)</li>")
                if row.get("scope_conflict_sample_preview_truncated"):
                    links.append("<li>Meer bestand/map-ancestorparen buiten zichtbare limiet.</li>")
                if row.get("scope_conflict_comparison_unavailable"):
                    links.append("<li>Bestand/map-historie onbekend: minstens één snapshot dateert van vóór deze bronvelden.</li>")
            if row["complete_open_pr_inventory_comparable"]:
                for pair in row["added_collision_pairs_if_complete"]:
                    links.append("<li>Nieuwe waargenomen bestandsoverlap: " +
                                 link(pair["left"]) + " ↔ " + link(pair["right"]) + "</li>")
                for pair in row["disappeared_collision_pairs_if_complete"]:
                    links.append("<li>Niet meer waargenomen overlap: " +
                                 link(pair["left"]) + " ↔ " + link(pair["right"]) +
                                 " (geen bewijs van geslaagde merge of release)</li>")
                for conflict in row.get("added_scope_conflicts_if_complete", []):
                    links.append("<li>Nieuwe bestand/map-ancestorrelatie: " +
                                 link(conflict["left"]) + " · <code>" + escape(conflict["left_path"]) +
                                 "</code> ↔ " + link(conflict["right"]) + " · <code>" +
                                 escape(conflict["right_path"]) + "</code> [" +
                                 escape(conflict["relation"]) + "]</li>")
                for conflict in row.get("disappeared_scope_conflicts_if_complete", []):
                    links.append("<li>Niet meer waargenomen bestand/map-ancestorrelatie: " +
                                 link(conflict["left"]) + " · <code>" + escape(conflict["left_path"]) +
                                 "</code> ↔ " + link(conflict["right"]) + " · <code>" +
                                 escape(conflict["right_path"]) + "</code> [" +
                                 escape(conflict["relation"]) + "] (geen mergebewijs)</li>")
            if row["connected_cluster_comparison_complete"]:
                for group in row["connected_clusters_only_in_current_full_inventory"]:
                    member_links = " · ".join(link(url) for url in group["pr_urls"])
                    links.append("<li>Nieuwe/gewijzigde connectiegroep in huidige VOLLEDIGE " +
                                 "inventaris: " + member_links +
                                 " (geen bewijs van nieuwe bron-PRs of eigenaar)</li>")
                for group in row["connected_clusters_only_in_previous_full_inventory"]:
                    member_links = " · ".join(link(url) for url in group["pr_urls"])
                    links.append("<li>Vorige connectiegroep niet identiek in huidige VOLLEDIGE " +
                                 "inventaris: " + member_links +
                                 " (GEEN bewijs van merge, fix of release)</li>")
            else:
                links.append("<li>Verandering van verbonden PR-groepen ONBEKEND: " +
                             "minstens één snapshot was onvolledig.</li>")
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
                     ", nieuwe bestand/map-relaties " +
                     str(len(row.get("added_scope_conflicts_if_complete", []))) +
                     ", niet-meer-waargenomen bestand/map-relaties " +
                     str(len(row.get("disappeared_scope_conflicts_if_complete", []))) +
                     ", niet-meer-geobserveerde overlapparen " +
                     str(len(row["disappeared_collision_pairs_if_complete"])) +
                     ", steekproef-only huidige overlapparen " +
                     str(len(row["overlap_pairs_only_in_current_sample_not_proven_new"])) +
                     ", steekproef-only eerdere overlapparen " +
                     str(len(row["overlap_pairs_only_in_previous_sample_not_proven_resolved"])) +
                     (" (extra paren verborgen)" if
                      row["overlap_sample_pair_preview_truncated"] else ""))
        if row["connected_cluster_comparison_complete"]:
            lines.append("  Verbonden PR-groepen: eerdere/nieuwe volledige bron vergelijkbaar; " +
                         str(len(row["connected_clusters_only_in_current_full_inventory"])) +
                         " uitsluitend huidig, " +
                         str(len(row["connected_clusters_only_in_previous_full_inventory"])) +
                         " uitsluitend eerder (GEEN bewijs van merge).")
            for group in row["connected_clusters_only_in_current_full_inventory"]:
                lines.append("    Huidige connectiegroep: " + " | ".join(group["pr_urls"]))
            for group in row["connected_clusters_only_in_previous_full_inventory"]:
                lines.append("    Eerdere connectiegroep: " + " | ".join(group["pr_urls"]) +
                             " (NIET bewezen opgelost)")
        else:
            lines.append("  Connectiegroep-verschillen ONBEKEND: bron onvolledig.")
        for item in row["head_or_base_changed_in_both_samples"]:
            lines.append("  HEAD/basis: " + item["url"] + " " +
                         item["previous_head"][:12] + " → " + item["current_head"][:12])
        for url in row["seen_only_in_current_sample_not_proven_new"]:
            lines.append("  Alleen huidige steekproef, niet bewezen nieuw: " + url)
        for url in row["seen_only_in_previous_sample_not_proven_closed"]:
            lines.append("  Alleen vorige steekproef, niet bewezen gesloten: " + url)
        if row.get("scope_conflict_comparison_unavailable"):
            lines.append("  Bestand/map-historie onbekend: minstens één snapshot bevat deze bronvelden niet of was niet beschikbaar.")
        if row.get("scope_conflict_sample_preview_truncated"):
            lines.append("  Meer historische bestand/map-ancestorparen buiten zichtbare limiet.")
        if not row["complete_open_pr_inventory_comparable"]:
            for pair in row["overlap_pairs_only_in_current_sample_not_proven_new"]:
                lines.append("  Alleen huidige botsingssteekproef, NIET bewezen nieuw: " +
                             pair["left"] + " ↔ " + pair["right"])
            for pair in row["overlap_pairs_only_in_previous_sample_not_proven_resolved"]:
                lines.append("  Alleen vorige botsingssteekproef, NIET bewezen opgelost: " +
                             pair["left"] + " ↔ " + pair["right"])
            for conflict in row.get("scope_conflicts_only_in_current_sample_not_proven_new", []):
                lines.append("  Alleen huidige bestand/map-steekproef, NIET bewezen nieuw: " +
                             conflict["left"] + " " + conflict["left_path"] + " ↔ " +
                             conflict["right"] + " " + conflict["right_path"] +
                             " [" + conflict["relation"] + "]")
            for conflict in row.get("scope_conflicts_only_in_previous_sample_not_proven_resolved", []):
                lines.append("  Alleen vorige bestand/map-steekproef, NIET bewezen opgelost: " +
                             conflict["left"] + " " + conflict["left_path"] + " ↔ " +
                             conflict["right"] + " " + conflict["right_path"] +
                             " [" + conflict["relation"] + "]")
        if row["complete_open_pr_inventory_comparable"]:
            for pair in row["added_collision_pairs_if_complete"]:
                lines.append("  Nieuw waargenomen overlap: " +
                             pair["left"] + " ↔ " + pair["right"])
            for pair in row["disappeared_collision_pairs_if_complete"]:
                lines.append("  Niet meer waargenomen overlap, GEEN merge-bewijs: " +
                             pair["left"] + " ↔ " + pair["right"])
            for conflict in row.get("added_scope_conflicts_if_complete", []):
                lines.append("  Nieuwe bestand/map-relatie: " + conflict["left"] +
                             " " + conflict["left_path"] + " ↔ " + conflict["right"] +
                             " " + conflict["right_path"] + " [" + conflict["relation"] + "]")
            for conflict in row.get("disappeared_scope_conflicts_if_complete", []):
                lines.append("  Niet meer waargenomen bestand/map-relatie (geen mergebewijs): " +
                             conflict["left"] + " " + conflict["left_path"] + " ↔ " +
                             conflict["right"] + " " + conflict["right_path"] +
                             " [" + conflict["relation"] + "]")
    if format == "markdown":
        return "\n".join(["## " + lines[0], ""] + ["- " + line for line in lines[1:]]) + "\n"
    return "\n".join(lines)
