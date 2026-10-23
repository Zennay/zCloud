"""Fail-closed, read-only zSentinel work-packet selection.

Consumes a *separately collected and verified* bounded receipt. Never fetches
GitHub/Notion, acquires a lease, edits a queue, or authorizes a PR merge.
This ranks genuine implementation and already-reviewable integration work
instead of spawning another QA-only branch when a project is held.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat

MAX_BYTES = 256 * 1024
MAX_ITEMS = 256
MAX_PACKET = 4
FRESH_FOR = timedelta(minutes=15)
FUTURE_SKEW = timedelta(minutes=2)
ID = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:#-]{0,95}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
PRIORITIES = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
KINDS = {"pr", "issue"}
CLAIMS = {"free", "active", "unknown"}
HOLDS = {"none", "external", "release"}
WORK_TYPES = {"product", "qa-only", "review-only"}
OPTIONAL_DELIVERY = frozenset({"work_type"})
REQUIRED_ITEM = frozenset({
    "project", "item_id", "kind", "priority", "impact", "effort",
    "owner", "claim", "hold", "source_allowed", "files",
})
OPTIONAL_ITEM = frozenset({
    "head_sha", "base_sha", "ci_passed", "review_approved",
    "mergeable", "draft",
})


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate evidence key")
        result[key] = value
    return result


def _utc(value):
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("invalid observation timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("invalid observation timestamp") from exc
    if parsed.utcoffset() != timedelta():
        raise ValueError("invalid observation timestamp")
    return parsed


def _file_scope(value):
    if type(value) is not str or len(value) > 180 or not value:
        raise ValueError("invalid scope path")
    parts = value.split("/")
    if (value.startswith("/") or "\\" in value or
            any(p in ("", ".", "..") for p in parts) or
            any(ord(c) < 32 for c in value) or
            parts[0] in (".git", ".env")):
        raise ValueError("invalid scope path")
    return value


def _validate_item(item, projects):
    if type(item) is not dict or not REQUIRED_ITEM <= item.keys() or item.keys() - (REQUIRED_ITEM | OPTIONAL_ITEM | OPTIONAL_DELIVERY):
        raise ValueError("invalid item schema")
    if "work_type" in item and (
        type(item["work_type"]) is not str or item["work_type"] not in WORK_TYPES
    ):
        raise ValueError("invalid work type")
    project = item["project"]
    if type(project) is not str or project not in projects:
        raise ValueError("unknown project")
    if type(item["item_id"]) is not str or not ITEM_ID.fullmatch(item["item_id"]):
        raise ValueError("invalid item identity")
    if (type(item["kind"]) is not str or item["kind"] not in KINDS or
            type(item["priority"]) is not str or item["priority"] not in PRIORITIES):
        raise ValueError("invalid kind/priority")
    for field in ("impact", "effort"):
        value = item[field]
        if type(value) is not int or not 1 <= value <= 5:
            raise ValueError("invalid effort/impact")
    if any(type(item[field]) is not str for field in ("owner", "claim", "hold")) or any(
        item[field] not in allowed for field, allowed in (
            ("owner", CLAIMS), ("claim", CLAIMS), ("hold", HOLDS)
        )
    ):
        raise ValueError("invalid owner or gate")
    if type(item["source_allowed"]) is not bool:
        raise ValueError("invalid source permission")
    files = item["files"]
    if type(files) is not list or not 1 <= len(files) <= 12:
        raise ValueError("missing bounded file scope")
    paths = [_file_scope(p) for p in files]
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate file scope")
    if item["kind"] == "pr":
        required = OPTIONAL_ITEM
        if not required <= item.keys():
            raise ValueError("incomplete PR receipt")
        for field in ("ci_passed", "review_approved", "mergeable", "draft"):
            if type(item[field]) is not bool:
                raise ValueError("invalid PR evidence")
        for field in ("head_sha", "base_sha"):
            value = item[field]
            if type(value) is not str or not SHA.fullmatch(value):
                raise ValueError("invalid SHA")
    elif item.keys() & OPTIONAL_ITEM:
        raise ValueError("issue cannot carry PR admission flags")
    return paths


def _overlap(first, second):
    return any(a == b or a.startswith(b + "/") or b.startswith(a + "/")
               for a in first for b in second)


def select_packets(receipt, *, now=None, limit=MAX_PACKET):
    """Choose disjoint delivery packets; output is advice, never permission.

    An external gate blocks releases, but does not block independent, already
    authorized *source-only* issue implementation. Unknown owner/lease
    evidence blocks all candidates for that project.
    """
    now = now or datetime.now(timezone.utc)
    if type(limit) is not int or not 1 <= limit <= MAX_PACKET:
        raise ValueError("invalid packet cap")
    if type(receipt) is not dict or set(receipt) != {
        "schema_version", "observed_at", "projects", "items"
    } or type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1:
        raise ValueError("invalid evidence envelope")
    observed = _utc(receipt["observed_at"])
    if now.tzinfo is None or now - observed > FRESH_FOR or observed - now > FUTURE_SKEW:
        raise ValueError("stale or future evidence")
    project_rows = receipt["projects"]
    items = receipt["items"]
    if (type(project_rows) is not list or not 1 <= len(project_rows) <= 32 or
            type(items) is not list or len(items) > MAX_ITEMS):
        raise ValueError("unbounded evidence")
    projects = {}
    for row in project_rows:
        if type(row) is not dict or set(row) != {"id", "main_sha", "lease_verified"}:
            raise ValueError("invalid project receipt")
        pid, sha = row["id"], row["main_sha"]
        if (type(pid) is not str or not ID.fullmatch(pid) or pid in projects or
                type(sha) is not str or not SHA.fullmatch(sha) or
                type(row["lease_verified"]) is not bool):
            raise ValueError("invalid project identity/provenance")
        projects[pid] = row

    seen = set()
    candidates = []
    blocked = {"ownership_or_lease": 0, "release_or_external_gate": 0,
               "unverified_pr": 0, "source_not_allowed": 0,
               "qa_or_review_only": 0}
    for item in items:
        files = _validate_item(item, projects)
        key = (item["project"], item["item_id"])
        if key in seen:
            raise ValueError("duplicate candidate identity")
        seen.add(key)
        # An explicit original-source QA/review classification is not eligible
        # for a new zSentinel product packet. Legacy untyped evidence remains
        # structurally readable, but must not be called QA-classified.
        if item.get("work_type") in {"qa-only", "review-only"}:
            blocked["qa_or_review_only"] += 1
            continue
        if (not projects[item["project"]]["lease_verified"] or
                item["owner"] != "free" or item["claim"] != "free"):
            blocked["ownership_or_lease"] += 1
            continue
        if not item["source_allowed"]:
            blocked["source_not_allowed"] += 1
            continue
        if item["kind"] == "pr":
            if item["hold"] != "none":
                blocked["release_or_external_gate"] += 1
                continue
            if (item["draft"] or not item["ci_passed"] or
                    not item["review_approved"] or not item["mergeable"] or
                    item["base_sha"] != projects[item["project"]]["main_sha"]):
                blocked["unverified_pr"] += 1
                continue
        elif item["hold"] == "release":
            blocked["release_or_external_gate"] += 1
            continue
        # Project-verified source-only issues remain selectable even when a
        # *different* live/rights/device milestone has an external hold.
        action = "review_integrate_existing_pr" if item["kind"] == "pr" else "implement_issue"
        candidates.append((
            (PRIORITIES[item["priority"]], 0 if item["kind"] == "pr" else 1,
             -item["impact"], item["effort"], item["project"], item["item_id"]),
            {"project": item["project"], "item_id": item["item_id"],
             "priority": item["priority"], "action": action, "files": files,
             "delivery": ["build_fix", "inspect_build_or_existing_required_CI",
                          "exact_head_review_and_integrate_if_authorized"],
             "work_type": item.get("work_type") or "legacy_unclassified",
             "authorization_granted": False},
        ))

    chosen = []
    scopes = {}
    counts = {}
    for _, packet in sorted(candidates, key=lambda pair: pair[0]):
        project = packet["project"]
        if counts.get(project, 0) >= 2:
            continue
        if _overlap(packet["files"], scopes.get(project, [])):
            continue
        chosen.append(packet)
        scopes.setdefault(project, []).extend(packet["files"])
        counts[project] = counts.get(project, 0) + 1
        if len(chosen) >= limit:
            break
    return {
        "schema": "zsentinel-recovery-packets-v1",
        "observation": receipt["observed_at"],
        "mutation_performed": False,
        "queue_claimed": False,
        "merge_or_deploy_authorized": False,
        "considered": len(items),
        "blocked": blocked,
        "packets": chosen,
    }


def _regular_json(path):
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("not a bounded regular receipt")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("oversized receipt")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path, help="locally verified read-only JSON snapshot")
    parser.add_argument("--limit", type=int, default=MAX_PACKET)
    args = parser.parse_args(argv)
    try:
        report = select_packets(_regular_json(args.receipt), limit=args.limit)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        parser.exit(2, "invalid or untrusted recovery evidence\n")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
