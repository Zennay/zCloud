#!/usr/bin/env python3
"""Read-only no-new-debt guard against interrupting self-hosted Actions jobs.

Conservative by design: any newly introduced non-false cancel-in-progress
configuration on a workflow with a self-hosted or dynamically chosen runner is
denied. Unchanged legacy configurations remain grandfathered.
"""
from __future__ import annotations

import argparse
import os
import selectors
import signal
import time
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys

SHA = re.compile(r"[a-f0-9]{40}\Z")
MAX_SOURCE = 250_000
MAX_FILES = 250


class Denied(Exception):
    pass


GIT_TIMEOUT = 15.0


def git(root: Path, *args: str) -> bytes:
    """Bound Git output *while reading* and kill hung pipe-holding descendants."""
    try:
        proc = subprocess.Popen(
            ["git", *args], cwd=root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
        )
    except OSError as exc:
        raise Denied("git_unavailable_or_timeout") from exc
    complete = False
    data = bytearray()
    deadline = time.monotonic() + GIT_TIMEOUT
    try:
        assert proc.stdout is not None
        with selectors.DefaultSelector() as poller:
            poller.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not poller.select(remaining):
                    raise Denied("git_unavailable_or_timeout")
                piece = os.read(
                    proc.stdout.fileno(),
                    min(65536, MAX_SOURCE + 1 - len(data)),
                )
                if not piece:
                    break
                data.extend(piece)
                if len(data) > MAX_SOURCE:
                    raise Denied("git_output_too_large")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Denied("git_unavailable_or_timeout")
        if proc.wait(timeout=remaining):
            raise Denied("git_command_failed")
        complete = True
        return bytes(data)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Denied("git_unavailable_or_timeout") from exc
    finally:
        if not complete:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if proc.stdout is not None:
            proc.stdout.close()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def source(root: Path, sha: str, path: str) -> str | None:
    listing = git(root, "ls-tree", sha, "--", path)
    if not listing:
        return None
    header, separator, name = listing.partition(b"\t")
    if separator != b"\t" or name.rstrip(b"\n").decode("utf-8", "strict") != path:
        raise Denied("unsafe_tree_path")
    if header.split()[:2] not in ([b"100644", b"blob"], [b"100755", b"blob"]):
        raise Denied("unsafe_tree_mode")
    raw = git(root, "show", sha + ":" + path)
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise Denied("invalid_utf8") from exc


def _strip_yaml_comment(line: str) -> str:
    """Respect YAML comment boundaries and quoted '#' runner labels.

    In YAML an unquoted '#' without preceding whitespace is scalar content,
    not a comment. Quotes can also contain '#'; blindly splitting at '#'
    can turn a private runner label into a trusted hosted runner label.
    """
    quote = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote == "'":
            if c == "'":
                if i + 1 < len(line) and line[i + 1] == "'":
                    i += 2
                    continue
                quote = None
        elif quote == '"':
            if c == "\\":
                i += 2
                continue
            if c == '"':
                quote = None
        elif c in ("'", '"'):
            quote = c
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def dangerous_cancellations(text: str) -> Counter[str]:
    # A workflow with dynamic runs-on might select a self-hosted runner.
    # A conservative false positive costs CI review, not a production stop.
    meaningful = [_strip_yaml_comment(line) for line in text.splitlines()]
    # Runner labels are arbitrary on self-hosted machines. A custom label
    # such as ubuntu-private is NOT a GitHub-hosted runner merely because
    # it starts with "ubuntu-". Only a bounded official label allowlist is
    # exempt; complex/aliased/dynamic selectors remain potentially VPS.
    known_hosted = frozenset([
        "ubuntu-latest", "ubuntu-22.04", "ubuntu-24.04",
        "ubuntu-22.04-arm", "ubuntu-24.04-arm",
        "windows-latest", "windows-2022", "windows-2025",
        "macos-latest", "macos-14", "macos-15", "macos-26",
    ])
    runner_values = []
    for line in meaningful:
        # Only genuine job-level runner selectors qualify for a hosted
        # exemption. A reusable job's 'with: pretend-runs-on: ubuntu-latest'
        # or a shell step mentioning 'runs-on:' is never runner identity.
        match = re.fullmatch(r" {4}runs-on:\s*(.*?)\s*", line)
        if match:
            runner_values.append(match.group(1).strip().strip("'\""))
    is_selfhosted = any(
        "self-hosted" in line.lower() for line in meaningful
    ) or not runner_values or any(value not in known_hosted for value in runner_values)
    if not is_selfhosted:
        return Counter()
    bad = Counter()
    prefix: list[str] = []
    for line in meaningful:
        if not line.strip():
            continue
        item = re.match(r"^(\s*)cancel-in-progress\s*:\s*(.*?)\s*$", line)
        if "cancel-in-progress" in line and not (
            item and item.group(2).lower() == "false"
        ):
            # Inline YAML maps and quoted/aliased keys must not bypass the
            # literal-key parser; ambiguous configurations fail closed.
            # Preserve nearest concurrency/group context for move detection.
            context = "|".join(prefix[-2:])
            bad[context + "|" + line] += 1
        if re.match(r"^\s*(?:concurrency|group|jobs|[a-zA-Z0-9_-]+)\s*:", line):
            if not line.lstrip().startswith("cancel-in-progress:"):
                prefix.append(line)
                prefix = prefix[-4:]
    return bad


def audit(root: Path, base: str, head: str) -> dict[str, object]:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise Denied("invalid_commit_identity")
    for rev in (base, head):
        if git(root, "rev-parse", "--verify", rev + "^{commit}").decode().strip() != rev:
            raise Denied("commit_mismatch")
    if git(root, "rev-parse", "HEAD").decode().strip() != head:
        raise Denied("wrong_checkout")
    if git(root, "merge-base", base, head).decode().strip() != base:
        raise Denied("stale_or_diverged_base")
    paths_raw = git(
        root, "diff", "--no-renames", "--diff-filter=ACMRT", "--name-only", "-z",
        base, head, "--", ".github/workflows",
    )
    if paths_raw and not paths_raw.endswith(b"\0"):
        raise Denied("truncated_diff")
    changed: list[str] = []
    for data in paths_raw.split(b"\0"):
        if not data:
            continue
        try:
            path = data.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise Denied("invalid_workflow_path") from exc
        if ".." in Path(path).parts:
            raise Denied("path_traversal")
        if not path.startswith(".github/workflows/"):
            raise Denied("invalid_workflow_path")
        if path.count("/") == 2 and path.endswith((".yml", ".yaml")):
            changed.append(path)
    if len(changed) > MAX_FILES:
        raise Denied("too_many_workflows")
    known = introduced = 0
    for path in changed:
        before = source(root, base, path)
        after = source(root, head, path)
        if after is None:
            raise Denied("missing_changed_workflow")
        legacy = dangerous_cancellations(before or "")
        now = dangerous_cancellations(after)
        known += sum(legacy.values())
        introduced += sum((now - legacy).values())
    return {
        "policy": "selfhosted-noninterrupting-no-new-debt-v1",
        "base": base,
        "head": head,
        "changed_workflows": len(changed),
        "legacy_unsafe": known,
        "new_unsafe": introduced,
        "ok": introduced == 0,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    arguments = parser.parse_args(argv)
    try:
        result = audit(arguments.repo_root.resolve(), arguments.base, arguments.head)
    except (Denied, UnicodeError, OSError) as exc:
        result = {
            "policy": "selfhosted-noninterrupting-no-new-debt-v1",
            "ok": False,
            "reason": str(exc) if isinstance(exc, Denied) else "invalid_source",
            "mutation_performed": False,
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
