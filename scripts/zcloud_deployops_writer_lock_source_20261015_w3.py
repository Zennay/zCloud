#!/usr/bin/env python3
"""Read-only, fail-closed inventory of the seven known zCloud VPS deploy writers.

This is SOURCE evidence, not a runtime lock probe or authorization to deploy.
In particular, GitHub Actions concurrency locks are not proven active by YAML.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import re
import stat
from pathlib import Path

PRODUCTION_GROUP = "zcloud-production-deploy"
# Values are the jobs that mutate the VPS, never hosted PR verification jobs.
WRITERS = {
    "zcloud-vps-deploy.yml": ("deploy", "workflow"),
    "zcloud-postsend-tab-preservation-live.yml": ("install", "job"),
    "zcloud-safe-memory-reclaim-20261013.yml": ("reclaim", "job"),
    "zcloud-v2-activate-addon-in-place.yml": ("activate", "job"),
    "zcloud-worker-tab-guard.yml": ("install", "job"),
    "zcloud-no-browser-churn-hotfix.yml": ("install", "job"),
    "zcloud-emergency-addon-hot-reload.yml": ("activate", "job"),
}
KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?:[ ]*(.*))?$")
JOB = re.compile(r"^  ([A-Za-z][A-Za-z0-9_-]*):[ ]*$")
MAX_BYTES = 256 * 1024


class AmbiguousSource(ValueError):
    pass


def _read_regular(root: Path, filename: str) -> str:
    """Pin every directory and the file itself with NOFOLLOW fds.

    An ordinary is_symlink/stat/read_bytes sequence is TOCTOU-vulnerable: a
    competing process could replace a checked parent or leaf with a symlink
    between validation and read. CI is isolated, but operator output must also
    refuse that race rather than silently inspecting a different source.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW
    directory_flags = flags | os.O_DIRECTORY
    fds = []
    try:
        absolute = root.absolute()
        # Traverse from /, never resolving the supplied root through symlinks.
        if not absolute.is_absolute() or ".." in absolute.parts:
            raise AmbiguousSource("invalid_source_root")
        fd = os.open("/", directory_flags)
        fds.append(fd)
        for component in (*absolute.parts[1:], ".github", "workflows"):
            if component in ("", ".", ".."):
                if component == "..":
                    raise AmbiguousSource("invalid_source_root")
                continue
            fd = os.open(component, directory_flags, dir_fd=fd)
            fds.append(fd)
        leaf = os.open(filename, flags, dir_fd=fd)
        fds.append(leaf)
        metadata = os.fstat(leaf)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
            raise AmbiguousSource("not_bounded_regular_source")
        raw = os.read(leaf, MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or b"\x00" in raw:
            raise AmbiguousSource("invalid_source_bytes")
        try:
            data = raw.decode("utf-8")
        except UnicodeError as exc:
            raise AmbiguousSource("invalid_utf8") from exc
        if "\t" in data:
            raise AmbiguousSource("tab_indentation_or_scalar")
        return data
    except OSError as exc:
        # Linux may return ELOOP for a leaf and ENOTDIR for a parent symlink.
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise AmbiguousSource("symlink_source") from exc
        raise AmbiguousSource("unreadable_source") from exc
    finally:
        for descriptor in reversed(fds):
            os.close(descriptor)


def _key(line: str, indent: int):
    if not line.startswith(" " * indent) or line.startswith(" " * (indent + 1)):
        return None
    match = KEY.fullmatch(line[indent:])
    return match.groups() if match else None


def _section(lines: list[str], key: str, indent: int, low: int, high: int):
    starts = []
    for i in range(low, high):
        parsed = _key(lines[i], indent)
        if parsed and parsed[0] == key:
            starts.append((i, parsed[1]))
    if len(starts) != 1:
        raise AmbiguousSource("missing_or_duplicate_" + key)
    start, value = starts[0]
    if value not in ("", None):
        raise AmbiguousSource("inline_" + key)
    end = next((i for i in range(start + 1, high)
                if lines[i].strip() and not lines[i].lstrip().startswith("#")
                and len(lines[i]) - len(lines[i].lstrip(" ")) <= indent), high)
    return start, end


def _lock(lines: list[str], indent: int, low: int, high: int):
    try:
        start, end = _section(lines, "concurrency", indent, low, high)
    except AmbiguousSource as exc:
        if str(exc) == "missing_or_duplicate_concurrency":
            matches = [i for i in range(low, high)
                       if (parsed := _key(lines[i], indent))
                       and parsed[0] == "concurrency"]
            if not matches:
                return "missing_concurrency"
        return "ambiguous_concurrency"
    values = {}
    for line in lines[start + 1:end]:
        parsed = _key(line, indent + 2)
        if parsed:
            k, v = parsed
            if k in values:
                return "duplicate_lock_field"
            values[k] = v
    if values.get("group") != PRODUCTION_GROUP:
        return "wrong_group"
    if values.get("cancel-in-progress") != "false":
        return "unsafe_cancel_policy"
    return "serialized_source"


def _parent_cancel_status(lines: list[str]) -> str:
    """Workflow concurrency applies regardless of YAML key order."""
    roots = [i for i in range(len(lines))
             if (parsed := _key(lines[i], 0)) and parsed[0] == "concurrency"]
    if not roots:
        return "safe"
    if len(roots) != 1:
        return "ambiguous_parent_concurrency"
    try:
        start, end = _section(lines, "concurrency", 0, 0, len(lines))
    except AmbiguousSource:
        return "ambiguous_parent_concurrency"
    fields = {}
    for line in lines[start + 1:end]:
        parsed = _key(line, 2)
        if parsed:
            key, value = parsed
            if key in fields:
                return "ambiguous_parent_concurrency"
            fields[key] = value
    if fields.get("cancel-in-progress") != "false":
        return "unsafe_parent_cancel_policy"
    return "safe"


def inspect_workflow(root: Path, filename: str, job: str, scope: str) -> str:
    try:
        lines = _read_regular(root, filename).splitlines()
        # A YAML root field can legally appear after jobs. Restrict the job
        # enumeration to the real jobs block, never a later root section.
        # Exact root key matching is intentionally conservative.
        try:
            jobs_at, jobs_end = _section(lines, "jobs", 0, 0, len(lines))
        except AmbiguousSource:
            return "ambiguous_jobs"
        jobs = [i for i in range(jobs_at + 1, jobs_end)
                if JOB.fullmatch(lines[i])]
        selected = [i for i in jobs if JOB.fullmatch(lines[i]).group(1) == job]
        if len(selected) != 1:
            return "missing_or_duplicate_mutation_job"
        at = selected[0]
        job_end = next((i for i in jobs if i > at), jobs_end)
        if scope == "workflow":
            return _lock(lines, 0, 0, len(lines))
        parent_status = _parent_cancel_status(lines)
        if parent_status != "safe":
            return parent_status
        return _lock(lines, 4, at + 1, job_end)
    except AmbiguousSource as exc:
        return str(exc)


def inventory(root: Path) -> dict:
    details = {}
    for name, (job, scope) in WRITERS.items():
        result = inspect_workflow(root, name, job, scope)
        details[name] = {"mutation_job": job, "required_scope": scope,
                         "source_result": result}
    unsafe = sorted(name for name, item in details.items()
                    if item["source_result"] != "serialized_source")
    return {
        "schema": "zcloud-writer-lock-source-v1",
        "group": PRODUCTION_GROUP,
        "total_writers": len(WRITERS),
        "source_serialized": len(WRITERS) - len(unsafe),
        "source_unverified": unsafe,
        "source_lock_complete": not unsafe,
        "runtime_lock_verified": False,
        "release_authorized": False,
        "dispatch_authorized": False,
        "mutation_performed": False,
        "writers": details,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--expect-serialized", action="store_true",
                        help="Exit nonzero when any source writer lacks the shared lock")
    args = parser.parse_args(argv)
    report = inventory(args.root)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    # Ordinary read-only inventory succeeds even when debt is open. A later,
    # deliberately enabled enforcement gate must opt in to strict failure.
    return 1 if args.expect_serialized and not report["source_lock_complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
