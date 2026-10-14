#!/usr/bin/env python3
"""Offline, deny-first review-queue evidence classifier. NEVER authorizes merges/deploys.

Input is an externally collected, *untrusted* snapshot. This utility does not
collect evidence, validate GitHub authenticity or exercise production authority.
"""
import argparse
import json
import os
import stat
import re
import sys
from datetime import datetime, timezone
import unicodedata
from pathlib import PurePosixPath

SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_ITEMS = 500
MAX_BYTES = 1024 * 1024
REQUIRED_GATES = ("580", "1089", "1135")


class InvalidEvidence(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidEvidence(message)


def sha(value):
    return isinstance(value, str) and bool(SHA.fullmatch(value))


def obj(value, name):
    require(type(value) is dict, f"invalid_{name}")
    return value


def paths(value):
    require(type(value) is list and 0 < len(value) <= MAX_ITEMS, "invalid_paths")
    require(all(type(p) is str and 0 < len(p) <= 256 and p[0] != "/" and "\\" not in p
                and all(part not in ("", ".", "..") for part in p.split("/"))
                and not any(ord(c) < 32 or ord(c) == 127 for c in p)
                and PurePosixPath(p).as_posix() == p for p in value), "invalid_path")
    require(len(value) == len(set(value)), "duplicate_paths")
    require(len({unicodedata.normalize("NFC", p).casefold() for p in value}) == len(value),
            "ambiguous_unicode_paths")
    return set(value)


def path_collision(left, right):
    """Directory/file swaps and case/normalization collisions reserve both owners."""
    l = {unicodedata.normalize("NFC", p).casefold() for p in left}
    r = {unicodedata.normalize("NFC", p).casefold() for p in right}
    # Linear in the number of path components, rather than O(files_left * files_right).
    def overlaps(paths, other):
        for path in paths:
            parts = path.split("/")
            for i in range(1, len(parts) + 1):
                if "/".join(parts[:i]) in other:
                    return True
        return False
    return overlaps(l, r) or overlaps(r, l)


def conflicted_owners(all_owners):
    """Return exact conflicting owner pairs using indexed path ancestors.

    File paths and branch names are deliberately never returned to CLI output.
    """
    exact = {}
    descendants = {}
    for owner, files in all_owners:
        for original in files:
            name = unicodedata.normalize("NFC", original).casefold()
            exact.setdefault(name, set()).add(owner)
            parts = name.split("/")
            for i in range(1, len(parts)):
                descendants.setdefault("/".join(parts[:i]), set()).add(owner)
    conflicts = {owner: set() for owner, _ in all_owners}
    for owner, files in all_owners:
        for original in files:
            name = unicodedata.normalize("NFC", original).casefold()
            possible = set(exact.get(name, ())) | set(descendants.get(name, ()))
            parts = name.split("/")
            for i in range(1, len(parts)):
                possible.update(exact.get("/".join(parts[:i]), ()))
            conflicts[owner].update(possible - {owner})
    return conflicts


def complete_files(record, main_sha, head_sha):
    """Deny partial/renumbered file inventories and SHA-unspecific compares."""
    f = obj(record.get("files_evidence"), "files_evidence")
    require(f.get("complete") is True, "incomplete_file_evidence")
    n = f.get("total_count")
    require(type(n) is int and 0 <= n <= MAX_ITEMS, "invalid_file_total")
    require(f.get("head_sha") == head_sha and f.get("base_sha") == main_sha
            and sha(f.get("head_sha")) and sha(f.get("base_sha")),
            "file_compare_sha_mismatch")
    listed = record.get("files")
    require(type(listed) is list and len(listed) == n, "truncated_file_inventory")
    require(f.get("rename_sources_complete") is True,
            "incomplete_rename_source_inventory")
    previous = record.get("previous_files")
    previous_total = f.get("previous_files_total")
    require(type(previous_total) is int and 0 <= previous_total <= MAX_ITEMS,
            "invalid_rename_source_total")
    require(type(previous) is list and len(previous) == previous_total,
            "truncated_rename_source_inventory")
    require(n > 0 or previous_total == 0, "orphaned_rename_sources")
    owned = paths(listed) if listed else set()
    source_paths = paths(previous) if previous else set()
    require(len(owned | source_paths) <= MAX_ITEMS, "file_reservation_limit")
    return owned | source_paths


def branch_identity_key(name):
    """Reject ambiguous Git branch identities rather than silently collapsing refs."""
    require(type(name) is str and 0 < len(name) <= 256
            and name != "main" and name == unicodedata.normalize("NFC", name)
            and all(unicodedata.category(ch) not in ("Cc", "Cf", "Cs") for ch in name)
            and name.strip() == name, "invalid_branch_identity")
    return name.casefold()


def correlate_prless_receipt(report):
    """Cross-check against #1312's separately owned full branch-inventory schema.

    This verifies *consistency*, not signature, API origin or production authority.
    """
    receipt = obj(report.get("branch_inventory_receipt"), "branch_inventory_receipt")
    require(receipt.get("schema") == "zcloud-prless-branch-snapshot-v1"
            and receipt.get("inventory_complete") is True
            and receipt.get("admission_authorized") is False
            and receipt.get("deployment_authorized") is False
            and receipt.get("paths_compared") is False, "invalid_prless_receipt")
    require(receipt.get("main_sha") == report.get("main_sha"),
            "prless_receipt_main_drift")
    branches = report.get("branches")
    listed = receipt.get("pr_less")
    require(type(branches) is list and type(listed) is list
            and len(branches) == len(listed) and len(listed) <= MAX_ITEMS,
            "prless_receipt_coverage_mismatch")
    count = receipt.get("branch_count")
    pr_count = receipt.get("open_pr_head_count")
    pages = (receipt.get("branch_pages"), receipt.get("pr_pages"))
    require(type(count) is int and type(pr_count) is int
            and count >= 1 + len(listed) and pr_count >= 0
            and all(type(x) is int and 1 <= x <= 100 for x in pages),
            "invalid_prless_receipt_counts")
    names = {}
    for item in listed:
        b = obj(item, "prless_receipt_branch")
        name, head = b.get("branch"), b.get("commit_sha")
        key = branch_identity_key(name)
        require(key not in names and sha(head), "invalid_prless_receipt_branch")
        names[key] = (name, head)
    local = {}
    for item in branches:
        b = obj(item, "branch")
        name, head = b.get("name"), b.get("head_sha")
        key = branch_identity_key(name)
        require(key not in local and sha(head), "invalid_local_branch")
        local[key] = (name, head)
    require(names == local, "prless_receipt_identity_drift")


def evidence_freshness(report, *, now=None):
    """Time-window screen for an advisory snapshot; NOT source authentication."""
    value = report.get("observed_at")
    require(type(value) is str
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value),
            "invalid_observation_time")
    try:
        observed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise InvalidEvidence("invalid_observation_time") from None
    current = now if now is not None else datetime.now(timezone.utc)
    require(isinstance(current, datetime) and current.tzinfo is not None,
            "invalid_trusted_clock")
    age = (current.astimezone(timezone.utc) - observed).total_seconds()
    require(-30 <= age <= 300, "stale_or_future_observation")


def snapshot(report, *, now=None):
    d = obj(report, "snapshot")
    require(sha(d.get("main_sha")), "invalid_main_sha")
    evidence_freshness(d, now=now)
    inv = obj(d.get("inventory"), "inventory")
    require(inv.get("prs_complete") is True and inv.get("branches_complete") is True,
            "incomplete_inventory")
    gates = obj(d.get("gates"), "gates")
    require(all(gates.get(k) in ("open", "closed") for k in REQUIRED_GATES),
            "unknown_release_gate")
    # Closing issues is not enough to release production authority.
    require(type(d.get("prs")) is list and 0 < len(d["prs"]) <= MAX_ITEMS,
            "invalid_pr_inventory")
    require(type(d.get("branches")) is list and len(d["branches"]) <= MAX_ITEMS,
            "invalid_branch_inventory")
    for declared, actual in (("open_pr_count", len(d["prs"])),
                             ("prless_branch_count", len(d["branches"]))):
        count = inv.get(declared)
        require(type(count) is int and 0 <= count <= MAX_ITEMS,
                "invalid_inventory_count")
        require(count == actual, "inventory_count_mismatch")
    correlate_prless_receipt(d)
    all_owners = []
    prs = []
    ids = set()
    for pr in d["prs"]:
        p = obj(pr, "pr")
        n = p.get("number")
        require(type(n) is int and 0 < n < 10000000 and n not in ids,
                "duplicate_or_invalid_pr")
        ids.add(n)
        require(sha(p.get("head_sha")) and sha(p.get("base_sha")), "invalid_pr_sha")
        require(type(p.get("draft")) is bool and type(p.get("owner_approved")) is bool,
                "invalid_pr_admission")
        required_checks = p.get("required_checks")
        require(type(required_checks) is list and 0 < len(required_checks) <= 100
                and all(type(x) is str and 0 < len(x) <= 128 for x in required_checks)
                and len(required_checks) == len(set(required_checks)), "invalid_required_checks")
        checks = p.get("checks")
        require(type(checks) is list and 0 < len(checks) <= 100, "missing_checks")
        seen_checks = set()
        for check in checks:
            c = obj(check, "check")
            name = c.get("name")
            require(type(name) is str and 0 < len(name) <= 128 and name not in seen_checks,
                    "ambiguous_check")
            seen_checks.add(name)
            require(sha(c.get("head_sha")) and c.get("conclusion") in
                    ("success", "failure", "pending", "cancelled", "skipped", "neutral"),
                    "invalid_check")
        files = complete_files(p, d["main_sha"], p["head_sha"])
        require(bool(files), "empty_pr_changes")
        prs.append((n, p, files))
        all_owners.append((f"pr:{n}", files))
    seen_branches = set()
    for branch in d["branches"]:
        b = obj(branch, "branch")
        name = b.get("name")
        key = branch_identity_key(name)
        require(key not in seen_branches, "invalid_branch_identity")
        seen_branches.add(key)
        require(sha(b.get("head_sha")), "invalid_branch_sha")
        all_owners.append((f"branch:{name}",
                           complete_files(b, d["main_sha"], b["head_sha"])))
    overlapping_owners = conflicted_owners(all_owners)
    out = []
    for n, p, files in sorted(prs, key=lambda x: x[0]):
        reasons = []
        if p["base_sha"] != d["main_sha"]:
            reasons.append("stale_base")
        if p["draft"]:
            reasons.append("draft")
        if not p["owner_approved"]:
            reasons.append("owner_review_missing")
        if not set(p["required_checks"]).issubset({c["name"] for c in p["checks"]}):
            reasons.append("required_checks_missing")
        if not all(c["head_sha"] == p["head_sha"] and c["conclusion"] == "success"
                   for c in p["checks"]):
            reasons.append("exact_head_checks_not_green")
        owner_conflicts = overlapping_owners.get(f"pr:{n}", set())
        if owner_conflicts:
            reasons.append("overlapping_owner")
        conflict_summary = {
            "other_prs": sum(c.startswith("pr:") for c in owner_conflicts),
            "prless_branches": sum(c.startswith("branch:") for c in owner_conflicts),
        }
        out.append({"number": n, "review_candidate": not reasons, "reasons": reasons,
                    "conflict_counts": conflict_summary})
    blockers = {key: sum(key in row["reasons"] for row in out)
                for key in ("overlapping_owner", "stale_base", "draft",
                            "owner_review_missing", "required_checks_missing",
                            "exact_head_checks_not_green")}
    return {"main_sha": d["main_sha"], "inventory_complete": True,
            "serialized_gates_closed": all(gates[k] == "closed" for k in REQUIRED_GATES),
            "release_authorized": False, "mutations_performed": False,
            "review_candidates": out, "review_candidate_count": sum(x["review_candidate"] for x in out),
            "blocked_candidate_count": len(out) - sum(x["review_candidate"] for x in out),
            "blocker_counts": blockers,
            "disclaimer": "Offline advisory only: requires authenticated live revalidation, independent review and serialized owner release before any merge, deployment or recovery."}



REASONS_NL = {
    "stale_base": "verouderde hoofdbranch",
    "draft": "concept-PR",
    "owner_review_missing": "geen eigenaarstoestemming",
    "required_checks_missing": "verplichte CI-controle ontbreekt",
    "exact_head_checks_not_green": "tests niet groen op exacte commit",
    "overlapping_owner": "bestandsconflict met andere worker",
}


def markdown(result):
    """Bounded, sanitized human-readable advisory; never a release permit."""
    lines = ["### zCloud · deploy-ops reviewoverzicht",
             "Dit is **alleen een bronreview-advies**. Geen toestemming voor merge, herstel of deploy.",
             f"Kandidaat-PR's: **{result['review_candidate_count']} / {len(result['review_candidates'])}**.",
             f"Nog niet reviewgereed: **{result['blocked_candidate_count']}**.",
             f"Geserialiseerde issues allemaal gesloten: **{'ja' if result['serialized_gates_closed'] else 'nee'}**. Ook bij ja ontbreekt vrijgavebewijs.",
             "", "#### Belangrijkste blokkades"]
    for reason, count in sorted(result["blocker_counts"].items(),
                                key=lambda item: (-item[1], item[0])):
        if count:
            lines.append(f"- {REASONS_NL[reason]}: **{count} PR's**.")
    lines += ["", "#### Kandidaten en blokkades"]
    for row in result['review_candidates'][:30]:
        if row['review_candidate']:
            lines.append(f"- PR #{row['number']}: geschikt voor *onafhankelijke bronreview*, niet voor merge.")
        else:
            explanations = ", ".join(REASONS_NL[r] for r in row['reasons'])
            conflicts = row["conflict_counts"]
            details = (f" (conflict met {conflicts['other_prs']} andere PR's en "
                       f"{conflicts['prless_branches']} PR-loze branches)"
                       if sum(conflicts.values()) else "")
            lines.append(f"- PR #{row['number']}: nog niet reviewgereed — {explanations}{details}.")
    if len(result['review_candidates']) > 30:
        lines.append(f"- Nog {len(result['review_candidates']) - 30} PR's niet weergegeven (uitvoerlimiet).")
    lines.append("Geen productieactie uitgevoerd. Herhaal inventarisatie met geauthenticeerde GitHub-data op de actuele SHA.")
    return "\n".join(lines)

def unique_pairs(items):
    def no_duplicates(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise InvalidEvidence("duplicate_json_key")
            d[k] = v
        return d
    return json.loads(items, object_pairs_hook=no_duplicates,
                      parse_constant=lambda _: (_ for _ in ()).throw(InvalidEvidence("non_finite_json")))


def read_manifest_bytes(path):
    """Bounded, descriptor-relative POSIX read; never follow a symlink ancestor.

    A final-component O_NOFOLLOW alone is insufficient: parent directories can
    be replaced with symlinks between a caller's inspection and os.open().
    """
    require(type(path) is str and 0 < len(path) <= 4096 and "\x00" not in path,
            "invalid_manifest_path")
    absolute = path.startswith("/")
    components = path.split("/")
    if absolute:
        components = components[1:]
    require(components and all(part not in ("", "..") for part in components),
            "invalid_manifest_path")
    directory_flags = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                       | getattr(os, "O_CLOEXEC", 0))
    file_flags = (os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
                  | getattr(os, "O_CLOEXEC", 0))
    parent_fd = os.open("/" if absolute else ".", directory_flags)
    try:
        for component in components[:-1]:
            if component == ".":
                continue
            next_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        require(components[-1] not in (".", ".."), "invalid_manifest_path")
        fd = os.open(components[-1], file_flags, dir_fd=parent_fd)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode), "invalid_manifest_file_type")
            require(before.st_size <= MAX_BYTES, "oversized_snapshot")
            body = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
            require(stat.S_ISREG(after.st_mode)
                    and (before.st_dev, before.st_ino, before.st_size,
                         before.st_mtime_ns, before.st_ctime_ns)
                    == (after.st_dev, after.st_ino, after.st_size,
                        after.st_mtime_ns, after.st_ctime_ns)
                    and len(body) == before.st_size,
                    "manifest_changed_during_read")
        require(len(body) <= MAX_BYTES, "oversized_snapshot")
        return body
    finally:
        os.close(parent_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="untrusted local JSON snapshot; no network access")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    a = parser.parse_args(argv)
    try:
        body = read_manifest_bytes(a.manifest)
        result = snapshot(unique_pairs(body.decode("utf-8")))
        print(markdown(result) if a.format == "markdown" else
              json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        # Never echo untrusted filenames, URLs, full JSON, tokens or exception text.
        code = str(error) if isinstance(error, InvalidEvidence) else "unreadable_snapshot"
        print(json.dumps({"error": code, "release_authorized": False,
                          "mutations_performed": False}), file=sys.stdout)
        return 2


if __name__ == "__main__":
    sys.exit(main())
