#!/usr/bin/env python3
"""Read-only Git object ratchet: deny newly duplicated GitHub Actions workflow names."""
import argparse
import itertools
import json
import re
import subprocess
from pathlib import PurePosixPath

SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
MAX_FILES = 512
MAX_BLOB = 131072
MAX_TOTAL = 8 * 1024 * 1024


class InvalidInventory(ValueError):
    pass


def _git(args, cwd, max_bytes):
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=15, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise InvalidInventory("git_read_failed") from exc
    if len(proc.stdout) > max_bytes:
        raise InvalidInventory("git_output_too_large")
    return proc.stdout


def _scalar(value):
    value = value.strip()
    if not value or value.startswith(("|", ">", "&", "*", "!", "{", "[")):
        raise InvalidInventory("unsupported_workflow_name")
    if value[0] == '"':
        try:
            name, pos = json.JSONDecoder().raw_decode(value)
        except (ValueError, TypeError) as exc:
            raise InvalidInventory("unsupported_workflow_name") from exc
        trailing = value[pos:].strip()
        if not isinstance(name, str) or (trailing and not trailing.startswith("#")):
            raise InvalidInventory("unsupported_workflow_name")
    elif value[0] == "'":
        match = re.fullmatch(r"'((?:[^']|'')*)'(?:\s*#.*)?", value)
        if not match:
            raise InvalidInventory("unsupported_workflow_name")
        name = match.group(1).replace("''", "'")
    else:
        name = value.split(" #", 1)[0].rstrip()
        if "#" in name or ": " in name or name.startswith(("-", "?")):
            raise InvalidInventory("unsupported_workflow_name")
    if (not name or len(name) > 200 or
            any(ord(c) < 32 or ord(c) == 127 for c in name) or
            chr(36) + "{{" in name):
        raise InvalidInventory("unsupported_workflow_name")
    return name


def workflow_name(path, source):
    try:
        source = source.decode("utf-8", "strict").removeprefix("\ufeff")
    except UnicodeDecodeError as exc:
        raise InvalidInventory("non_utf8_workflow") from exc
    matches = re.findall(r"(?m)^name:[ \t]*(.*)$", source)
    if len(matches) > 1:
        raise InvalidInventory("duplicate_name_field")
    return _scalar(matches[0]) if matches else PurePosixPath(path).name


def pairs(items):
    by_name = {}
    for path, name in items.items():
        by_name.setdefault(name, []).append(path)
    return {(name, a, b) for name, paths in by_name.items()
            for a, b in itertools.combinations(sorted(paths), 2)}


def judge(base, candidate):
    new = sorted(pairs(candidate) - pairs(base))
    return {
        "policy": "workflow-name-no-new-collision-v1",
        "status": "deny" if new else "pass",
        "added_collisions": [{"name": n, "paths": [a, b]} for n, a, b in new[:20]],
        "added_collision_count": len(new),
        "baseline_collision_count": len(pairs(base)),
        "candidate_collision_count": len(pairs(candidate)),
        "merge_authorized": False,
        "production_mutation_authorized": False,
    }


def read_names(sha, *, cwd):
    if not SHA.fullmatch(sha):
        raise InvalidInventory("invalid_sha")
    raw = _git(["ls-tree", "-rz", "--full-tree", sha, "--",
                ".github/workflows"], cwd, MAX_FILES * 200)
    names, total = {}, 0
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        if len(names) >= MAX_FILES:
            raise InvalidInventory("too_many_workflows")
        try:
            meta, raw_path = entry.split(b"\t", 1)
            mode, kind, blobsha = meta.decode("ascii").split(" ")
            path = raw_path.decode("ascii")
        except (UnicodeError, ValueError) as exc:
            raise InvalidInventory("invalid_git_tree") from exc
        if (mode not in {"100644", "100755"} or kind != "blob"
                or not SHA.fullmatch(blobsha)):
            raise InvalidInventory("nonregular_workflow")
        if not WORKFLOW.fullmatch(path) or path in names:
            raise InvalidInventory("unexpected_workflow_path")
        blob = _git(["cat-file", "blob", blobsha], cwd, MAX_BLOB)
        total += len(blob)
        if total > MAX_TOTAL:
            raise InvalidInventory("workflow_sources_too_large")
        names[path] = workflow_name(path, blob)
    if not names:
        raise InvalidInventory("empty_workflow_inventory")
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", default=".")
    opts = parser.parse_args()
    try:
        base = read_names(opts.base, cwd=opts.root)
        head = read_names(opts.head, cwd=opts.root)
        result = judge(base, head)
        result["workflow_count_base"] = len(base)
        result["workflow_count_head"] = len(head)
    except InvalidInventory as exc:
        result = {
            "policy": "workflow-name-no-new-collision-v1", "status": "deny",
            "error_code": str(exc), "merge_authorized": False,
            "production_mutation_authorized": False,
        }
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
