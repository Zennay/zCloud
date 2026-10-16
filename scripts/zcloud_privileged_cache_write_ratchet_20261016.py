#!/usr/bin/env python3
"""Deny *new* direct cache writers in privileged GitHub Actions workflows.

This is a source-only, non-authorizing ratchet. It never contacts a VPS.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import subprocess
import sys

import yaml

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CACHE_WRITER_RE = re.compile(r"^actions/cache(?:/save)?@", re.I)
PRIVILEGED_EVENTS = frozenset({
    "pull_request_target", "workflow_run", "issue_comment", "repository_dispatch"
})
MAX_FILES = 2048
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
TIMEOUT = 20


class Denied(ValueError):
    pass


class StrictLoader(yaml.BaseLoader):
    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise Denied("expected mapping")
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise Denied("non-string or duplicate YAML mapping key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def run_git(*args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=True, timeout=TIMEOUT
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise Denied("Git source verification failed") from exc


def verify_commit(sha: str) -> None:
    if not SHA_RE.fullmatch(sha):
        raise Denied("commit must be an immutable 40-hex SHA")
    if run_git("cat-file", "-t", sha).strip() != b"commit":
        raise Denied("SHA is not a commit")


def verify_current_main(expected: str) -> None:
    origin = run_git("remote", "get-url", "origin").strip()
    if origin not in (b"https://github.com/Zennay/zCloud", b"https://github.com/Zennay/zCloud.git"):
        raise Denied("origin is not canonical HTTPS zCloud")
    # Redirects are forbidden: a changed remote must not masquerade as main.
    result = run_git("-c", "http.followRedirects=false", "ls-remote", "--exit-code",
                     "origin", "refs/heads/main").strip().splitlines()
    if result != [expected.encode() + b"\trefs/heads/main"]:
        raise Denied("current main is missing, ambiguous or has moved")


def discover_workflows(revision: str) -> dict[str, str]:
    entries = run_git("ls-tree", "-r", "-z", revision, "--", ".github/workflows").split(b"\0")
    files = {}
    total = 0
    for raw in entries:
        if not raw:
            continue
        try:
            meta, path_b = raw.split(b"\t", 1)
            mode, kind, oid = meta.split(b" ")
            path = path_b.decode("utf-8", "strict")
            oid_s = oid.decode("ascii")
        except (ValueError, UnicodeError) as exc:
            raise Denied("invalid Git tree entry") from exc
        if not path.startswith(".github/workflows/") or not path.endswith((".yml", ".yaml")):
            continue
        if path in files or mode not in (b"100644", b"100755") or kind != b"blob" or not SHA_RE.fullmatch(oid_s):
            raise Denied("duplicate, symlink, gitlink or invalid workflow tree entry")
        if len(files) >= MAX_FILES:
            raise Denied("too many workflows")
        try:
            size = int(run_git("cat-file", "-s", oid_s).strip())
        except ValueError as exc:
            raise Denied("invalid workflow object size") from exc
        if size > MAX_FILE_BYTES or size < 0 or total + size > MAX_TOTAL_BYTES:
            raise Denied("workflow source size limit exceeded")
        body = run_git("cat-file", "-p", oid_s)
        if len(body) != size:
            raise Denied("workflow blob size mismatch")
        total += size
        try:
            files[path] = body.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise Denied("non-UTF8 workflow") from exc
    return files


def event_names(on):
    if isinstance(on, str):
        return {on}
    if isinstance(on, list) and all(isinstance(v, str) for v in on):
        return set(on)
    if isinstance(on, dict):
        return set(on.keys())
    raise Denied("ambiguous workflow triggers")


def cache_writers(path: str, source: str) -> Counter:
    try:
        document = yaml.load(source, Loader=StrictLoader)
    except yaml.YAMLError as exc:
        raise Denied(f"{path}: malformed YAML") from exc
    if not isinstance(document, dict):
        raise Denied(f"{path}: expected workflow mapping")
    if "on" not in document:
        raise Denied(f"{path}: missing event triggers")
    if not (event_names(document["on"]) & PRIVILEGED_EVENTS):
        return Counter()
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        raise Denied(f"{path}: privileged workflow has invalid jobs")
    findings = Counter()
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            raise Denied(f"{path}: invalid privileged job")
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            raise Denied(f"{path}: invalid privileged job steps")
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise Denied(f"{path}: invalid privileged step")
            uses = step.get("uses", "")
            if not isinstance(uses, str):
                raise Denied(f"{path}: non-string action reference")
            if not CACHE_WRITER_RE.match(uses.strip()):
                continue
            with_inputs = step.get("with", {})
            if not isinstance(with_inputs, dict):
                raise Denied(f"{path}: invalid cache inputs")
            # Fingerprint the dangerous step, not merely its count. Replacing
            # an existing key/path/save action must also be independently reviewed.
            sig = json.dumps([
                path, job_id, index, uses.strip().lower(),
                with_inputs.get("key"), with_inputs.get("path"),
                with_inputs.get("save-always")
            ], sort_keys=True, ensure_ascii=True)
            findings[sig] += 1
    return findings


def inventory(revision: str) -> Counter:
    combined = Counter()
    for path, source in discover_workflows(revision).items():
        combined.update(cache_writers(path, source))
    return combined


def compare(base: Counter, head: Counter) -> list[dict]:
    return [{"fingerprint": fingerprint, "added": count}
            for fingerprint, count in sorted((head - base).items())]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--require-current-main", action="store_true")
    args = ap.parse_args(argv)
    try:
        verify_commit(args.base)
        verify_commit(args.head)
        if args.require_current_main:
            verify_current_main(args.base)
        baseline = inventory(args.base)
        candidate = inventory(args.head)
        if args.require_current_main:
            verify_current_main(args.base)
        new = compare(baseline, candidate)
        print(json.dumps({"base": args.base, "head": args.head,
                          "baseline_findings": sum(baseline.values()),
                          "candidate_findings": sum(candidate.values()),
                          "new_findings": new[:40], "new_findings_total": len(new),
                          "production_authorized": False}, indent=2))
        return 2 if new else 0
    except (Denied, RecursionError) as exc:
        print(json.dumps({"error": str(exc), "production_authorized": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
