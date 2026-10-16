#!/usr/bin/env python3
"""Read-only, fail-closed inventory of the seven known zCloud VPS deploy writers.

This is SOURCE evidence, not a runtime lock probe or authorization to deploy.
In particular, GitHub Actions concurrency locks are not proven active by YAML.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
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
# YAML permits quoted/escaped mapping keys, explicit mapping syntax, merge
# aliases and multiple documents. The narrow stdlib scanner cannot soundly
# interpret those variants, so refuse them rather than trusting a shadow key.
SHADOW_KEY = re.compile(r"""^ {0,6}(?:(?:'[^']*'|"[^"]*"|<<)\s*:|\?\s)""")


class AmbiguousSource(ValueError):
    pass


def _read_regular(root: Path, filename: str) -> str:
    """Pin every directory and the file itself with NOFOLLOW fds.

    An ordinary is_symlink/stat/read_bytes sequence is TOCTOU-vulnerable: a
    competing process could replace a checked parent or leaf with a symlink
    between validation and read. CI is isolated, but operator output must also
    refuse that race rather than silently inspecting a different source.
    """
    # O_NONBLOCK avoids hanging on a maliciously substituted FIFO, which
    # otherwise blocks at open() before fstat() can reject non-regular files.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
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
        chunks = []
        remaining = MAX_BYTES + 1
        while remaining:
            part = os.read(leaf, min(65536, remaining))
            if not part:
                break
            chunks.append(part)
            remaining -= len(part)
        raw = b"".join(chunks)
        after = os.fstat(leaf)
        identity = lambda meta: (meta.st_dev, meta.st_ino, meta.st_mode,
                                 meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)
        if len(raw) != metadata.st_size or identity(metadata) != identity(after):
            raise AmbiguousSource("source_changed_during_read")
        if len(raw) > MAX_BYTES or b"\x00" in raw:
            raise AmbiguousSource("invalid_source_bytes")
        try:
            data = raw.decode("utf-8")
        except UnicodeError as exc:
            raise AmbiguousSource("invalid_utf8") from exc
        if "\t" in data:
            raise AmbiguousSource("tab_indentation_or_scalar")
        for line in data.splitlines():
            if SHADOW_KEY.match(line) or line in ("---", "..."):
                raise AmbiguousSource("unsupported_yaml_mapping_form")
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
    # Workflow-level and job-level concurrency groups share GitHub's
    # repository-wide group namespace. A parent competing for the same
    # production lock is not independent non-cancelling write evidence.
    group = fields.get("group")
    if not group or "#" in group:
        # Inline YAML comments can change a plain scalar's actual group;
        # this narrow source reader cannot prove comment semantics.
        return "ambiguous_parent_group"
    group = group.strip()
    # Quoted plain YAML scalars have different source text from their runtime
    # values. Folded/literal scalars, anchors, aliases, tags and flow values
    # can similarly hide an identical production group from this line parser.
    # Refuse forms that require YAML interpretation rather than guessing.
    if group.startswith(("'", '"', "|", ">", "*", "&", "!", "{", "[")):
        return "ambiguous_parent_group"
    group = group.casefold()
    canonical = PRODUCTION_GROUP.casefold()
    # GitHub concurrency group names are case-insensitive, unlike ordinary
    # YAML scalars. Strip YAML's insignificant trailing whitespace as well.
    if group == canonical:
        return "parent_shares_production_group"
    if "${{" in group:
        # A fixed nonmatching prefix is disjoint. If an expression might
        # complete the canonical group, do not infer independent locking.
        prefix = group.split("${{", 1)[0]
        if not prefix or canonical.startswith(prefix):
            return "ambiguous_parent_group"
    return "safe"


def _runner_targets(lines: list[str], jobs: list[int], jobs_end: int,
                    expected_job: str) -> str:
    """Confirm the entire known workflow runner topology, not just a lock.

    A newly introduced self-hosted writer beside the locked job must not be
    silently treated as serialized. Only the seven already-reviewed workflow
    shapes are accepted: one target VPS job and GitHub-hosted companion jobs.
    This is deliberately not a repository-wide discovery or YAML interpreter.
    """
    for pos, at in enumerate(jobs):
        job_name = JOB.fullmatch(lines[at]).group(1)
        end = jobs[pos + 1] if pos + 1 < len(jobs) else jobs_end
        runners = [value for i in range(at + 1, end)
                   if (parsed := _key(lines[i], 4))
                   and parsed[0] == "runs-on"
                   for value in [parsed[1]]]
        if len(runners) != 1:
            return "missing_or_duplicate_runner_target"
        value = runners[0]
        if job_name == expected_job:
            if value != "[self-hosted, zcloud, vps]":
                return "unexpected_mutation_runner_target"
        elif value != "ubuntu-latest":
            return "unexpected_companion_runner_target"
    return "safe"


def inspect_workflow(root: Path, filename: str, job: str, scope: str,
                     *, source_text: str | None = None) -> str:
    try:
        lines = (source_text if source_text is not None else
                 _read_regular(root, filename)).splitlines()
        # A YAML root field can legally appear after jobs. Restrict the job
        # enumeration to the real jobs block, never a later root section.
        # Exact root key matching is intentionally conservative.
        try:
            jobs_at, jobs_end = _section(lines, "jobs", 0, 0, len(lines))
        except AmbiguousSource:
            return "ambiguous_jobs"
        jobs = []
        for i in range(jobs_at + 1, jobs_end):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent == 2:
                # Inline/anchored/flow job declarations are not covered by
                # the simple runner parser. Without this refusal a new
                # unguarded VPS writer could be invisible in a safe report.
                if not JOB.fullmatch(line):
                    return "unsupported_job_mapping"
                jobs.append(i)
        selected = [i for i in jobs if JOB.fullmatch(lines[i]).group(1) == job]
        if len(selected) != 1:
            return "missing_or_duplicate_mutation_job"
        at = selected[0]
        job_end = next((i for i in jobs if i > at), jobs_end)
        runner_status = _runner_targets(lines, jobs, jobs_end, job)
        if runner_status != "safe":
            return runner_status
        if scope == "workflow":
            source_result = _lock(lines, 0, 0, len(lines))
            if source_result != "serialized_source":
                return source_result
            # The canonical deploy uses one workflow-level non-cancelling lock.
            # A nested lock on the live job could cancel the transaction or
            # deadlock against the already-held shared group.
            nested = [i for i in range(at + 1, job_end)
                      if (parsed := _key(lines[i], 4))
                      and parsed[0] == "concurrency"]
            if nested:
                return "unexpected_nested_concurrency"
            return source_result
        parent_status = _parent_cancel_status(lines)
        if parent_status != "safe":
            return parent_status
        return _lock(lines, 4, at + 1, job_end)
    except AmbiguousSource as exc:
        return str(exc)


def inventory(root: Path) -> dict:
    """Bind every decision to the same bytes used to compute its digest.

    SHA-256 proves only equality of captured source snapshots, never GitHub
    origin, canonical-main provenance, a runtime lock or deployment authority.
    """
    details = {}
    for name, (job, scope) in WRITERS.items():
        digest = None
        try:
            source_text = _read_regular(root, name)
            digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
            result = inspect_workflow(root, name, job, scope,
                                      source_text=source_text)
        except AmbiguousSource as exc:
            result = str(exc)
        details[name] = {"mutation_job": job, "required_scope": scope,
                         "source_result": result, "source_sha256": digest}
    # A multi-file scan is not an atomic filesystem snapshot. Re-read all
    # seven pinned paths after the classification pass and refuse a receipt if
    # even one input changed (or ceased to be safely readable). This detects
    # ordinary cross-file churn without pretending to prove an atomic instant.
    # An ABA change between the two reads remains outside this claim.
    for name, item in details.items():
        first_digest = item["source_sha256"]
        if first_digest is None:
            continue
        try:
            second = _read_regular(root, name)
            stable = hashlib.sha256(second.encode("utf-8")).hexdigest() == first_digest
        except AmbiguousSource:
            stable = False
        if not stable:
            item["source_result"] = "unstable_source_snapshot"
            item["source_sha256"] = None
    unsafe = sorted(name for name, item in details.items()
                    if item["source_result"] != "serialized_source")
    complete = all(item["source_sha256"] is not None
                   for item in details.values())
    # "complete" means two equal, safely bounded read passes. It is NOT a
    # filesystem-atomic snapshot or an origin authentication.
    snapshot = None
    if complete:
        h = hashlib.sha256(b"zcloud-seven-writer-snapshot-v1\0")
        for name in sorted(details):
            h.update(name.encode("utf-8") + b"\0" +
                     details[name]["source_sha256"].encode("ascii") + b"\n")
        snapshot = h.hexdigest()
    return {
        "schema": "zcloud-writer-lock-source-v1",
        "group": PRODUCTION_GROUP,
        "total_writers": len(WRITERS),
        "source_serialized": len(WRITERS) - len(unsafe),
        "source_unverified": unsafe,
        "source_lock_complete": not unsafe,
        "source_snapshot_complete": complete,
        "source_snapshot_sha256": snapshot,
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
    parser.add_argument("--expect-source-snapshot-sha256",
                        metavar="64_HEX_CHARACTERS",
                        help="Compare the complete seven-file source snapshot with an "
                             "independently supplied digest. This does not prove origin.")
    args = parser.parse_args(argv)
    expected = args.expect_source_snapshot_sha256
    if expected is not None and re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        parser.error("--expect-source-snapshot-sha256 needs exactly 64 lowercase hex characters")
    report = inventory(args.root)
    if expected is not None:
        report["source_snapshot_matches_expected"] = (
            report["source_snapshot_complete"] and
            report["source_snapshot_sha256"] == expected
        )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    # Default observation exits 0 even if there is known open debt. Exit 2
    # for a snapshot mismatch/incomplete input, 1 for a missing source lock,
    # and 0 only when the explicitly selected checks succeed. None of these
    # exit codes grant deployment or mutation authority.
    if expected is not None and not report["source_snapshot_matches_expected"]:
        return 2
    if args.expect_serialized and not report["source_lock_complete"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
