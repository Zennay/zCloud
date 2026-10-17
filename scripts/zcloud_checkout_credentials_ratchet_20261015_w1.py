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
import selectors
import subprocess
import sys
import time
import unicodedata

SHA = re.compile(r"^[0-9a-f]{40}$")
WORKFLOWS = ".github/workflows/"
ACTIONS = ".github/actions/"
MAX_FILES = 750
MAX_FILE_BYTES = 524288
MAX_TREE_BYTES = 2 * 1024 * 1024
MAX_SCAN_SECONDS = 120
MAX_GIT_SECONDS = 15
STEP = re.compile(r"^(\s*)-\s+uses\s*:\s*['\"]?actions/checkout@([0-9a-f]{40})(?:['\"])?(?:\s*(?:#.*)?)?$")
ANY_CHECKOUT = re.compile(r"actions/checkout\s*@", re.I)
STEP_USES = re.compile(r"^(\s+)uses\s*:\s*[\x27\x22]?actions/checkout@([0-9a-f]{40})(?:[\x27\x22])?(?:\s*(?:#.*)?)?$")
WITH = re.compile(r"^(\s*)with\s*:\s*(?:#.*)?$")
CREDENTIAL = re.compile(r"^(\s*)persist-credentials\s*:\s*(\S+)(?:\s*#.*)?$")
NEW_STEP = re.compile(r"^(\s*)-\s+")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
ESCAPED_YAML = re.compile(r"\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|U[0-9a-fA-F]{8}|/|[ \t]*$)")


class Denied(Exception):
    pass


def git(*args: str, max_bytes: int = MAX_TREE_BYTES,
        deadline: float | None = None) -> bytes:
    """Bound Git subprocess output AND wall time, even for a stalled producer.

    Buffered pipe.read can block indefinitely *before* subprocess.wait(timeout)
    runs. A selector with a monotonic deadline controls every read and wait.
    """
    if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_TREE_BYTES:
        raise Denied("invalid_git_output_budget")
    now = time.monotonic()
    if deadline is not None and now >= deadline:
        raise Denied("scan_deadline_exceeded")
    stop = min(now + MAX_GIT_SECONDS, deadline) if deadline is not None else now + MAX_GIT_SECONDS
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0",
                       GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_CONFIG_SYSTEM=os.devnull)
    process = None
    try:
        process = subprocess.Popen(["git", *args], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=environment, close_fds=True)
        output = bytearray()
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                seconds_left = stop - time.monotonic()
                if seconds_left <= 0:
                    raise Denied("git_source_timeout")
                ready = selector.select(timeout=seconds_left)
                if not ready:
                    raise Denied("git_source_timeout")
                for key, _ in ready:
                    chunk = os.read(key.fd, min(65536, max_bytes + 1 - len(output)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk)
                    if len(output) > max_bytes:
                        raise Denied("git_output_limit")
        seconds_left = stop - time.monotonic()
        if seconds_left <= 0:
            raise Denied("git_source_timeout")
        if process.wait(timeout=seconds_left) != 0:
            raise Denied("git_source_missing")
        return bytes(output)
    except subprocess.TimeoutExpired as exc:
        raise Denied("git_source_timeout") from exc
    except OSError as exc:
        raise Denied("git_source_unavailable") from exc
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdout is not None:
                process.stdout.close()


def selected(path: str) -> bool:
    return (path.startswith(WORKFLOWS) and path.endswith((".yml", ".yaml"))
            or path.startswith(ACTIONS) and path.endswith(("/action.yml", "/action.yaml")))


def files_at(sha: str, deadline: float | None = None) -> dict[str, tuple[str, str]]:
    output = git("ls-tree", "-rz", sha, "--", WORKFLOWS, ACTIONS, deadline=deadline)
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
        # A local composite-action directory can be replaced by a symlink
        # before its action.yml is enumerated. A symlink anywhere within
        # .github/actions is not an ordinary auditable action source.
        if name.startswith(ACTIONS) and mode not in {"100644", "100755"}:
            raise Denied("unsupported_action_entry")
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


def blob(oid: str, deadline: float | None = None) -> str:
    size = git("cat-file", "-s", oid, max_bytes=20, deadline=deadline)
    try:
        count = int(size.strip())
    except ValueError as exc:
        raise Denied("invalid_blob_size") from exc
    if not 0 <= count <= MAX_FILE_BYTES:
        raise Denied("oversized_source")
    raw = git("cat-file", "blob", oid, max_bytes=MAX_FILE_BYTES, deadline=deadline)
    if len(raw) != count:
        raise Denied("changed_blob")
    try:
        decoded = raw.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise Denied("invalid_utf8") from exc
    # Different YAML/Unicode libraries disagree about line separators,
    # zero-width controls and bidirectional marks. Never let an invisible
    # character rewrite the effective 'uses'/'with' key after scanning.
    if any((ch in "\u0085\u2028\u2029"
            or unicodedata.category(ch) in {"Cf", "Cs"}
            or (ord(ch) < 32 and ch not in "\t\n\r")
            or ord(ch) == 127) for ch in decoded):
        raise Denied("ambiguous_source_control")
    return decoded


def workflow_active_lines(source: str) -> list[str]:
    """Ignore YAML run block *body* text, which is shell data, not YAML actions.

    Only literal/folded run scalars are recognized; non-obvious YAML remains
    subject to the conservative lexical gate. Dedented lines are still scanned.
    """
    active: list[str] = []
    run_indent: int | None = None
    for line in source.splitlines():
        indent = len(line) - len(line.lstrip(" "))
        if run_indent is not None:
            if not line.strip() or indent > run_indent:
                continue
            run_indent = None
        if line.lstrip().startswith("#"):
            continue
        active.append(line)
        if re.fullmatch(r"\s*(?:-\s+)?run\s*:\s*[|>][0-9+-]*(?:\s*#.*)?", line):
            run_indent = indent
    return active


def checkout_risks(source: str) -> list[str]:
    """Reject unsafe checkout and ambiguous syntax without publishing source."""
    active = workflow_active_lines(source)
    # YAML quoted scalars can decode \u0040 to @ *after* our regex scan.
    # Any encoded YAML 'uses' or action value is not a proved literal.
    # YAML additionally recognizes \/ inside double-quoted strings and a
    # trailing backslash can join physical lines. Both can conceal checkout
    # spelling from a naive regex; reject them even without the target text.
    for line in active:
        if ESCAPED_YAML.search(line) and (
            re.search(r"(?i)\b(?:uses|actions|checkout)\b", line)
            or re.match(r"^\s*(?:-\s*)?['\"]", line)
            or '"' in line
        ):
            return ["opaque_escaped_yaml"]
    uses = []
    for index, line in enumerate(active):
        if not ANY_CHECKOUT.search(line):
            continue
        match = STEP.fullmatch(line)
        if match:
            uses.append((index, len(match.group(1)), index))
            continue
        # Canonical GitHub Actions also permits:
        #   - name: Checkout
        #     uses: actions/checkout@<full-SHA>
        #     with: ...
        # Bind uses to one direct step introduced by name, id or if.
        # An arbitrary nested YAML mapping must never count as a step.
        named = STEP_USES.fullmatch(line)
        if not named:
            return ["ambiguous_checkout"]
        uses_indent = len(named.group(1))
        leader = next((n for n in range(index - 1, -1, -1)
                       if active[n].strip() and
                       len(active[n]) - len(active[n].lstrip(" ")) < uses_indent), None)
        if leader is None:
            return ["ambiguous_checkout"]
        parent = active[leader]
        parent_indent = len(parent) - len(parent.lstrip(" "))
        if (uses_indent != parent_indent + 2 or
                not re.fullmatch(r"\s*-\s+(?:name|id|if)\s*:\s*[^#\s].*", parent)):
            return ["ambiguous_checkout"]
        uses.append((index, parent_indent, leader))
    problems = []
    for index, indent, leader in uses:
        end = len(active)
        for n in range(index + 1, len(active)):
            next_line = active[n]
            if next_line.strip() and len(next_line) - len(next_line.lstrip()) <= indent:
                end = n
                break
        # Separate steps can share the indentation of the checkout list item.
        step = active[index + 1:end]
        # Duplicate YAML step 'uses:' can shadow the checkout value (or vice
        # versa) depending on which YAML reader accepts the mapping. A
        # lexical check must never certify the resulting step as unambiguous.
        all_step = active[leader:end]
        direct_uses = sum(
            bool(re.fullmatch(r"\s*uses\s*:\s*.*", row)) and
            len(row) - len(row.lstrip(" ")) == indent + 2
            for row in all_step
        )
        short_use = int(bool(re.fullmatch(r"\s*-\s+uses\s*:\s*.*", active[leader])))
        if direct_uses + short_use != 1:
            problems.append("ambiguous_step_uses")
        with_indent = None
        correct = False
        seen = 0
        for line in step:
            # Duplicate YAML mapping keys can shadow the safe initial 'with'
            # and silently reinstate checkout's credential-persistence default.
            # A second 'with: {}', 'with: *alias' or folded value is unsafe
            # even though it does not contain 'persist-credentials'.
            if re.match(r"^\s*(?:with|[\x27\x22]with[\x27\x22])\s*:", line):
                m = WITH.fullmatch(line)
                if with_indent is not None:
                    problems.append("ambiguous_checkout_with")
                if m is None:
                    problems.append("opaque_checkout_with")
                else:
                    with_indent = len(m.group(1))
                    # A nested env.with/run.with is NOT checkout's direct with.
                    if with_indent != indent + 2:
                        problems.append("nested_checkout_with")
            # Explicit YAML keys and merges/aliases may introduce or
            # shadow a second 'with' mapping after the safely declared one.
            # Their expansion is not proved by this lexical scanner.
            if re.match(r"^\s*\?\s*[\x27\x22]?with\b", line):
                problems.append("opaque_checkout_with")
            if re.match(r"^\s*<<\s*:", line):
                problems.append("opaque_checkout_merge")
            # A YAML tag can redefine the same scalar 'with' key while
            # hiding it behind !!str / !<tag:yaml.org,2002:str>.
            # Escapes in a checkout step are similarly not safe to interpret
            # with a lexical proof, even if the key name itself is encoded.
            if re.match(r"^\s*(?:!![^\s:]+|!<[^>]+>)\s+with\s*:", line):
                problems.append("opaque_checkout_with")
            if ESCAPED_YAML.search(line):
                problems.append("opaque_checkout_escape")
            cred = CREDENTIAL.fullmatch(line)
            if cred:
                seen += 1
                # Require the option to be directly under the direct with.
                # Noncanonical indentation is conservatively sent to review.
                if with_indent is None or len(cred.group(1)) != with_indent + 2:
                    problems.append("credentials_outside_with")
                if cred.group(2).strip("\"'").lower() == "false":
                    correct = True
                else:
                    problems.append("persisted_credentials")
            elif "persist-credentials" in line.lower() and not line.lstrip().startswith("#"):
                problems.append("ambiguous_credentials")
        if seen != 1 or not correct:
            problems.append("missing_explicit_false")
    priority = {"persisted_credentials": 0, "ambiguous_checkout_with": 0,
                "opaque_checkout_with": 1, "ambiguous_credentials": 2,
                "credentials_outside_with": 3,
                "missing_explicit_false": 4}
    return sorted(set(problems), key=lambda reason: (priority.get(reason, 5), reason))


def inspect(base: str, head: str) -> dict:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head) or base == head:
        raise Denied("invalid_commit_identity")
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    for sha in (base, head):
        if git("cat-file", "-t", sha, max_bytes=30, deadline=deadline).strip() != b"commit":
            raise Denied("not_a_commit")
    # Never accept a missing/symlinked workflow root as an empty safe scan.
    # The canonical repository requires both directories in both commits.
    for sha in (base, head):
        for root in (".github", ".github/workflows"):
            try:
                kind = git("cat-file", "-t", f"{sha}:{root}", max_bytes=30, deadline=deadline).strip()
            except Denied as exc:
                raise Denied("missing_workflow_root") from exc
            if kind != b"tree":
                raise Denied("unsupported_workflow_root")
    # Include the actions *root* itself: recursive listing with a trailing
    # slash would silently skip .github/actions when it becomes a symlink.
    for sha in (base, head):
        raw = git("ls-tree", "-z", sha, "--", ".github/actions", deadline=deadline)
        if raw:
            record = raw.removesuffix(bytes([0]))
            try:
                metadata, path = record.split(bytes([9]), 1)
                mode, kind, _ = metadata.decode("ascii").split()
            except (ValueError, UnicodeError) as exc:
                raise Denied("ambiguous_action_root") from exc
            if path != b".github/actions" or mode != "040000" or kind != "tree":
                raise Denied("unsupported_action_root")
    old, new = files_at(base, deadline), files_at(head, deadline)
    checked = 0
    for path, (_, oid) in new.items():
        if old.get(path) == new[path]:
            continue
        checked += 1
        risks = checkout_risks(blob(oid, deadline))
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
