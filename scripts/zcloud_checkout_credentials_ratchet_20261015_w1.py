#!/usr/bin/env python3
"""Offline, non-authorizing ratchet against new actions/checkout credential persistence.

Only immutable Git objects are inspected. Historical *unchanged* workflow blobs are
grandfathered; changed workflows and composite actions must use an explicit
persist-credentials: false for every checkout. This is a conservative lexical check:
ambiguous YAML needs independent review rather than a guessed safe parse.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

SHA = re.compile(r"^[0-9a-f]{40}$")
WORKFLOWS = ".github/workflows/"
ACTIONS = ".github/actions/"
MAX_FILES = 750
MAX_FILE_BYTES = 524288
MAX_TREE_BYTES = 2 * 1024 * 1024
STEP = re.compile(r"^(\s*)-\s+uses\s*:\s*['\"]?actions/checkout@([0-9a-f]{40})(?:['\"])?(?:\s*(?:#.*)?)?$")
ANY_CHECKOUT = re.compile(r"actions/checkout\s*@", re.I)
WITH = re.compile(r"^(\s*)with\s*:\s*(?:#.*)?$")
CREDENTIAL = re.compile(r"^(\s*)persist-credentials\s*:\s*(\S+)(?:\s*#.*)?$")
NEW_STEP = re.compile(r"^(\s*)-\s+")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class Denied(Exception):
    pass


def git(*args: str, max_bytes: int = MAX_TREE_BYTES) -> bytes:
    # Never allow caller environment to replace immutable objects, fetch absent
    # objects automatically or alter the global system configuration.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0",
                       GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_CONFIG_SYSTEM=os.devnull)
    try:
        process = subprocess.Popen(["git", *args], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=environment)
        # Bounded reading, not a post-hoc subprocess.run memory bound.
        chunks = bytearray()
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(min(65536, max_bytes + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                raise Denied("git_output_limit")
        if process.wait(timeout=15) != 0:
            raise Denied("git_source_missing")
        return bytes(chunks)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Denied("git_source_unavailable") from exc
    finally:
        if "process" in locals():
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdout:
                process.stdout.close()


def selected(path: str) -> bool:
    return (path.startswith(WORKFLOWS) and path.endswith((".yml", ".yaml"))
            or path.startswith(ACTIONS) and path.endswith(("/action.yml", "/action.yaml")))


def files_at(sha: str) -> dict[str, tuple[str, str]]:
    output = git("ls-tree", "-rz", sha, "--", WORKFLOWS, ACTIONS)
    files = {}
    for raw in output.split(b"\x00"):
        if not raw:
            continue
        try:
            metadata, raw_name = raw.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split()
            name = raw_name.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise Denied("ambiguous_tree") from exc
        if CONTROL.search(name):
            raise Denied("ambiguous_path")
        if not selected(name):
            continue
        if kind != "blob" or mode not in {"100644", "100755"} or not SHA.fullmatch(oid):
            raise Denied("unsupported_source_type")
        if name in files:
            raise Denied("duplicate_source_path")
        files[name] = (mode, oid)
        if len(files) > MAX_FILES:
            raise Denied("too_many_files")
    return files


def blob(oid: str) -> str:
    size = git("cat-file", "-s", oid, max_bytes=20)
    try:
        count = int(size.strip())
    except ValueError as exc:
        raise Denied("invalid_blob_size") from exc
    if not 0 <= count <= MAX_FILE_BYTES:
        raise Denied("oversized_source")
    raw = git("cat-file", "blob", oid, max_bytes=MAX_FILE_BYTES)
    if len(raw) != count:
        raise Denied("changed_blob")
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise Denied("invalid_utf8") from exc


def checkout_risks(source: str) -> list[str]:
    """Reject unsafe checkout and ambiguous syntax without publishing source."""
    lines = source.splitlines()
    active = [line for line in lines if not line.lstrip().startswith("#")]
    uses = []
    for index, line in enumerate(active):
        if ANY_CHECKOUT.search(line):
            match = STEP.fullmatch(line)
            if not match:
                # Folded/flow/aliased/multiline or quoted-key YAML must not
                # bypass inspection. Inert shell text may need manual review.
                return ["ambiguous_checkout"]
            uses.append((index, len(match.group(1))))
    problems = []
    for index, indent in uses:
        end = len(active)
        for n in range(index + 1, len(active)):
            next_line = active[n]
            if next_line.strip() and len(next_line) - len(next_line.lstrip()) <= indent:
                end = n
                break
        # Separate steps can share the indentation of the checkout list item.
        step = active[index + 1:end]
        with_indent = None
        correct = False
        seen = 0
        for line in step:
            m = WITH.fullmatch(line)
            if m:
                if with_indent is not None:
                    problems.append("ambiguous_checkout_with")
                with_indent = len(m.group(1))
            cred = CREDENTIAL.fullmatch(line)
            if cred:
                seen += 1
                if with_indent is None or len(cred.group(1)) <= with_indent:
                    problems.append("credentials_outside_with")
                if cred.group(2).strip("\"'").lower() == "false":
                    correct = True
                else:
                    problems.append("persisted_credentials")
            elif "persist-credentials" in line.lower() and not line.lstrip().startswith("#"):
                problems.append("ambiguous_credentials")
        if seen != 1 or not correct:
            problems.append("missing_explicit_false")
    return sorted(set(problems))


def inspect(base: str, head: str) -> dict:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head) or base == head:
        raise Denied("invalid_commit_identity")
    for sha in (base, head):
        if git("cat-file", "-t", sha, max_bytes=30).strip() != b"commit":
            raise Denied("not_a_commit")
    old, new = files_at(base), files_at(head)
    checked = 0
    for path, (_, oid) in new.items():
        if old.get(path) == new[path]:
            continue
        checked += 1
        risks = checkout_risks(blob(oid))
        if risks:
            # No attacker-controlled filenames or source text in reports.
            raise Denied("unsafe_checkout_" + risks[0])
    return dict(status="pass", changed_sources_checked=checked,
                files_seen=len(new), authorizes_deploy=False,
                authorizes_merge=False, mutation_performed=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect(args.base, args.head)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Denied as exc:
        print(json.dumps(dict(status="deny", reason=str(exc),
                              authorizes_deploy=False, authorizes_merge=False,
                              mutation_performed=False), sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
