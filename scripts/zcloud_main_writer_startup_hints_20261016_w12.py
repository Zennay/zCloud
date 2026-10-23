#!/usr/bin/env python3
"""Offline positive trigger and runner hints for SHA-bound main writer candidates."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
import zcloud_main_writer_compat_inventory_20261016_w12 as core

KEY = re.compile(r"[A-Za-z_][A-Za-z_0-9-]*\Z")
EVENT = re.compile(r"^([ \t]+)([A-Za-z_][A-Za-z_0-9-]*)\s*:\s*(?:#.*)?$")
AUTO = set(("push pull_request pull_request_target workflow_run schedule "
            "repository_dispatch issues issue_comment release deployment "
            "deployment_status check_run check_suite create delete fork watch "
            "status public label milestone merge_group workflow_job discussion "
            "discussion_comment branch_protection_rule").split())
MANUAL = {"workflow_dispatch", "workflow_call"}


class Denied(Exception):
    """Stable, redacted reason."""


def push_scope_hints(lines: list[str], workflow_path: str | None = None) -> dict:
    """Report presence of exact-indent push filter *keys*; not their effect.

    GitHub YAML parsing, path match semantics and real trigger activity are
    outside this scanner. Key presence alone can never certify a filter.
    """
    in_root_on = False
    in_push = False
    in_paths = False
    paths = False
    branches = False
    own_path_literal = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            in_root_on = bool(re.fullmatch(r"on:\s*(?:#.*)?", line))
            in_push = False
            in_paths = False
            continue
        if not in_root_on:
            continue
        if re.fullmatch(r"  push:\s*(?:#.*)?", line):
            in_push = True
            in_paths = False
            continue
        if re.match(r"^  [a-zA-Z_][a-zA-Z_0-9-]*\s*:", line):
            in_push = False
            in_paths = False
            continue
        if in_push:
            if re.match(r"^    paths\s*:", line):
                paths = True
                in_paths = True
            elif re.match(r"^    [A-Za-z_][A-Za-z_0-9-]*\s*:", line):
                in_paths = False
            if re.match(r"^    branches\s*:", line):
                branches = True
            if in_paths and workflow_path and re.match(r"^      -\s+", line):
                literal = line.split("-", 1)[1].split(" #", 1)[0].strip()
                if literal.startswith(("'", '"')) and literal.endswith(literal[:1]):
                    literal = literal[1:-1]
                if literal == workflow_path:
                    own_path_literal = True
    return {
        "push_paths_filter_key_hint": paths,
        "push_branches_filter_key_hint": branches,
        "push_paths_mentions_own_yaml_literal_hint": own_path_literal,
        "push_filter_effectiveness_verified": False,
    }


def observe(raw: str, workflow_path: str | None = None) -> dict:
    lines = raw.splitlines()
    if len(lines) > 20000:
        raise Denied("workflow_line_limit")
    events, runners = set(), set()
    roots, unknown, active, indent = 0, False, False, None
    for line in lines:
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        if re.match(r"^[ \t]+runs-on\s*:", line):
            suffix = line.split(":", 1)[1].lower()
            if "self-hosted" in suffix:
                runners.add("self_hosted_hint")
            if any(x in suffix for x in ("ubuntu-latest", "windows-latest", "macos-latest")):
                runners.add("github_hosted_hint")
            if not suffix.strip() or "$" + "{{" in suffix:
                runners.add("dynamic_or_multiline_runner_hint")
        if line[0] not in " \t":
            active, indent = False, None
            if not line.startswith("on:"):
                if re.match(r"""^['"]on['"]\s*:""", line):
                    unknown = True
                continue
            roots += 1
            if roots > 1:
                unknown = True
            tail = line[3:].split(" #", 1)[0].strip()
            if not tail:
                active = True
            elif tail.startswith("[") and tail.endswith("]"):
                values = [part.strip() for part in tail[1:-1].split(",")]
                if values and all(KEY.fullmatch(v) for v in values):
                    events.update(values)
                else:
                    unknown = True
            elif KEY.fullmatch(tail):
                events.add(tail)
            else:
                unknown = True
            continue
        if active:
            match = EVENT.fullmatch(line)
            if match:
                depth = len(match.group(1).expandtabs(8))
                if indent is None:
                    indent = depth
                if depth == indent:
                    events.add(match.group(2))
                elif depth < indent:
                    unknown = True
            elif indent is None:
                unknown = True
    if roots != 1 or not events:
        unknown = True
    return {
        "automatic_event_hints": sorted(events & AUTO),
        "manual_event_hints": sorted(events & MANUAL),
        "unrecognized_event_hints": sorted(events - AUTO - MANUAL),
        "unknown_trigger_syntax": unknown,
        "runner_hints": sorted(runners),
        **push_scope_hints(lines, workflow_path),
        "manual_only_verified": False,
        "actual_activity_verified": False,
    }


def read_blob(repo: Path, sha: str, row: dict) -> str:
    path = row["path"]
    if not core.SAFE_PATH.fullmatch(path) or "//" in path or "/../" in path:
        raise Denied("unsafe_workflow_path")
    try:
        entry = core.git(repo, "ls-tree", "--full-tree", sha, path, max_output=512)
        meta, name = entry.rstrip(b"\n").split(b"\t", 1)
        fields = meta.split(b" ")
        if (name != path.encode("ascii") or len(fields) != 3 or
            fields[0] not in (b"100644", b"100755") or fields[1] != b"blob"):
            raise Denied("missing_regular_blob")
        digest = fields[2].decode("ascii")
        if not core.SHA.fullmatch(digest):
            raise Denied("invalid_git_blob_sha")
        size = int(core.git(repo, "cat-file", "-s", digest, max_output=64))
        if not 0 <= size <= core.MAX_BLOB_BYTES:
            raise Denied("blob_too_large")
        data = core.git(repo, "cat-file", "blob", digest, max_output=core.MAX_BLOB_BYTES)
        if (len(data) != size or b"\x00" in data or
            hashlib.sha256(data).hexdigest() != row["source_sha256"]):
            raise Denied("workflow_sha256_drift")
        text = data.decode("utf-8", "strict")
        if any((ord(c) < 32 and c not in "\r\n\t") or
               0x7f <= ord(c) <= 0x9f for c in text):
            raise Denied("workflow_hidden_controls")
        return text
    except (core.InventoryError, ValueError, UnicodeError) as exc:
        raise Denied("invalid_git_evidence") from exc


def audit(repo: Path, sha: str) -> dict:
    if not core.SHA.fullmatch(sha):
        raise Denied("invalid_main_sha")
    inventory = core.inventory(repo, sha)
    records = []
    for row in inventory["candidates"]:
        hints = observe(read_blob(repo, sha, row), workflow_path=row["path"])
        records.append({
            "path": row["path"], "source_sha256": row["source_sha256"],
            "priority": "P1" if row["classification"] == "direct_writer_candidate" else "P2",
            **hints,
        })
    core.pinned(repo, sha)
    return {
        "schema": "zcloud-main-writer-startup-hints-v1",
        "main_sha": sha, "workflows_scanned": inventory["workflows_scanned"],
        "candidate_count": len(records),
        "automatic_event_hint_count": sum(bool(r["automatic_event_hints"]) for r in records),
        "manual_event_hint_count": sum(bool(r["manual_event_hints"]) for r in records),
        "self_hosted_hint_count": sum("self_hosted_hint" in r["runner_hints"] for r in records),
        "unknown_trigger_count": sum(r["unknown_trigger_syntax"] for r in records),
        "push_paths_key_hint_count": sum(r["push_paths_filter_key_hint"] for r in records),
        "push_branches_key_hint_count": sum(r["push_branches_filter_key_hint"] for r in records),
        "push_own_yaml_literal_hint_count": sum(
            r["push_paths_mentions_own_yaml_literal_hint"] for r in records),
        "candidates": records, "manual_only_verified": False,
        "actual_activity_verified": False, "safe_to_enable_branch_protection": False,
        "production_mutation_authorized": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Static source hints; never approval")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--expected-main-sha", required=True)
    parser.add_argument("--format", choices=("json", "csv", "markdown"), default="json")
    a = parser.parse_args(argv)
    try:
        data = audit(Path(a.repo), a.expected_main_sha)
    except (Denied, core.InventoryError) as exc:
        print(json.dumps({"ok": False, "error": str(exc),
                          "safe_to_enable_branch_protection": False,
                          "production_mutation_authorized": False}, sort_keys=True))
        return 2
    if a.format == "csv":
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(("main_sha", "workflow_path", "source_sha256", "priority",
                         "automatic_event_hints", "manual_event_hints",
                         "unrecognized_event_hints", "unknown_trigger_syntax",
                         "runner_hints", "push_paths_filter_key_hint",
                         "push_branches_filter_key_hint",
                         "push_paths_mentions_own_yaml_literal_hint",
                         "push_filter_effectiveness_verified",
                         "manual_only_verified", "branch_protection_authorized"))
        for row in data["candidates"]:
            writer.writerow((data["main_sha"], row["path"], row["source_sha256"],
                             row["priority"], "|".join(row["automatic_event_hints"]),
                             "|".join(row["manual_event_hints"]),
                             "|".join(row["unrecognized_event_hints"]),
                             str(row["unknown_trigger_syntax"]).lower(),
                             "|".join(row["runner_hints"]),
                             str(row["push_paths_filter_key_hint"]).lower(),
                             str(row["push_branches_filter_key_hint"]).lower(),
                             str(row["push_paths_mentions_own_yaml_literal_hint"]).lower(),
                             "false", "false", "false"))
    elif a.format == "markdown":
        print("## zCloud writer-starttriggers — statische hints, GEEN VRIJGAVE")
        for key in ("main_sha", "candidate_count", "automatic_event_hint_count",
                    "manual_event_hint_count", "self_hosted_hint_count",
                    "unknown_trigger_count", "push_paths_key_hint_count",
                    "push_branches_key_hint_count", "push_own_yaml_literal_hint_count"):
            print(key + ": " + str(data[key]))
        for row in data["candidates"]:
            print("- " + row["path"] + ": auto=" +
                  ",".join(row["automatic_event_hints"]) + "; handmatig=" +
                  ",".join(row["manual_event_hints"]) + "; runner=" +
                  ",".join(row["runner_hints"]) + "; push-paths-key=" +
                  str(row["push_paths_filter_key_hint"]).lower())
        print("Niet bewezen: exclusief handmatige start, echte runtime of branch-beveiliging.")
    else:
        print(json.dumps(data, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
