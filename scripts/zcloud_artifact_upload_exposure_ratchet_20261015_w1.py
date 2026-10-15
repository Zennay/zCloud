#!/usr/bin/env python3
"""Fail-closed check for unsafe CI artifact uploads in changed workflow sources.

This only evaluates source, never invokes GitHub APIs or issues deploy authority.
Unchanged historical workflow blobs are intentionally grandfathered.
"""
from __future__ import annotations
import argparse
import os
import re
import selectors
import signal
import subprocess
import sys
import time

SHA = re.compile(r"[a-f0-9]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
USES = re.compile(r"^(\s*)-?\s*uses\s*:\s*['\"]?actions/upload-artifact@[^\s'\"]+['\"]?\s*(?:#.*)?$", re.I)
WITH = re.compile(r"^(\s*)with\s*:\s*(?:#.*)?$", re.I)
KEY = re.compile(r"^(\s*)([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$")
PRIVATE = re.compile(r"(?:^|[/\\])(?:\.env(?:\.[^/\\]*)?|\.git|\.ssh|secrets?|credentials?|\.npmrc|id_rsa|tokens?)(?:\.[^/\\]+)?(?:[/\\]|$)", re.I)
BROAD = re.compile(r"(?:^|\s)(?:\.|\./|\*|\*\*|/|~/)(?:\s|$)|[\$\{\}\[\]]|\*\*|[!~]", re.I)


class Denied(ValueError):
    pass


def git(*args: str, cap: int = 1200000) -> bytes:
    """Bound output while receiving it, and tear down the whole process group."""
    process = None
    poller = None
    succeeded = False
    try:
        env = os.environ.copy()
        env["GIT_NO_REPLACE_OBJECTS"] = "1"
        env["GIT_OPTIONAL_LOCKS"] = "0"
        process = subprocess.Popen(["git", "-c", "core.pager=cat", *args],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=env, start_new_session=True)
        assert process.stdout is not None
        poller = selectors.DefaultSelector()
        poller.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + 10
        chunks = []
        seen = 0
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise Denied("git_deadline")
            if not poller.select(remain):
                raise Denied("git_deadline")
            piece = os.read(process.stdout.fileno(), min(65536, cap - seen + 1))
            if not piece:
                break
            seen += len(piece)
            if seen > cap:
                raise Denied("git_output_unbounded")
            chunks.append(piece)
        if process.wait(timeout=max(0.001, deadline - time.monotonic())):
            raise Denied("git_source_unavailable")
        succeeded = True
        return b"".join(chunks)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Denied("git_unavailable") from e
    finally:
        if poller is not None:
            poller.close()
        if process is not None:
            # A Git wrapper may exit while a forked child holds stdout open.
            if not succeeded:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.wait()
            if process.stdout is not None:
                process.stdout.close()


def workflow_paths(base: str, head: str) -> list[str]:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise Denied("invalid_revision")
    if git("rev-parse", "HEAD").decode().strip() != head:
        raise Denied("head_mismatch")
    try:
        p = subprocess.run(["git", "merge-base", "--is-ancestor", base, head],
                           timeout=10, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Denied("invalid_ancestry") from e
    if p.returncode:
        raise Denied("invalid_ancestry")
    paths = []
    for raw in git("diff", "--name-only", "-z", "--diff-filter=ACMRT", base, head,
                   "--", ".github/workflows/", cap=100000).split(b"\0"):
        if not raw:
            continue
        try:
            name = raw.decode("ascii", "strict")
        except UnicodeError as e:
            raise Denied("ambiguous_workflow_path") from e
        if not WORKFLOW.fullmatch(name):
            raise Denied("ambiguous_workflow_path")
        paths.append(name)
    if len(paths) > 200 or len(paths) != len(set(paths)):
        raise Denied("workflow_inventory_unbounded")
    return paths


def read_workflow(commit: str, path: str) -> str:
    mode = git("ls-tree", "-z", commit, "--", path, cap=1500)
    if not mode.startswith((b"100644 blob ", b"100755 blob ")) or not mode.endswith(path.encode() + b"\0"):
        raise Denied("non_regular_workflow_blob")
    data = git("show", f"{commit}:{path}", cap=600000)
    if b"\0" in data or len(data) > 550000:
        raise Denied("unsafe_workflow_bytes")
    try:
        return data.decode("utf-8", "strict")
    except UnicodeError as e:
        raise Denied("unsafe_workflow_bytes") from e


def examine(text: str) -> None:
    if any(ord(c) < 32 and c not in "\r\n\t" for c in text):
        raise Denied("unsafe_workflow_bytes")
    if "upload-artifact" not in text.lower():
        return
    if re.search(r"(?m)^\s*(?:<<\s*:|(?:[\w-]+\s*:\s*)?[&*!]{1,2}[\w-]+)", text):
        raise Denied("ambiguous_yaml_upload")
    lines = text.splitlines()
    uploads = [i for i, line in enumerate(lines) if USES.match(line)]
    if not uploads:
        raise Denied("unrecognized_upload_step")
    for i in uploads:
        m = USES.match(lines[i])
        assert m
        step_indent = len(m.group(1))
        with_indent = None
        fields = {}
        for line in lines[i+1:]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            if indent <= step_indent:
                break
            with_match = WITH.match(line)
            if with_match and with_indent is None:
                with_indent = len(with_match.group(1))
                continue
            if with_indent is None:
                continue
            if indent <= with_indent:
                break
            key = KEY.match(line)
            if not key or len(key.group(1)) <= with_indent:
                raise Denied("ambiguous_upload_value")
            k = key.group(2).lower()
            if k in fields:
                raise Denied("duplicate_upload_option")
            fields[k] = key.group(3).split(" #", 1)[0].strip().strip("\"'")
        if with_indent is None or "path" not in fields:
            raise Denied("missing_artifact_path")
        if fields.get("include-hidden-files", "false").lower() != "false":
            raise Denied("hidden_files_enabled_or_ambiguous")
        paths = fields["path"].split(",")
        for value in paths:
            path = value.strip().strip("\"'")
            if (not path or ambiguous_artifact_path(path) or BROAD.search(path) or PRIVATE.search(path) or
                path.startswith(("-", "/", "\\")) or ".." in path.split("/") or
                any(c.isspace() or c in "|>&;" + chr(96) + "#:" for c in path)):
                raise Denied("secret_or_broad_artifact_path")


def ambiguous_artifact_path(value: str) -> bool:
    """Disallow upload globs, encoded paths, hidden indirections and secret-like names."""
    if len(value) > 200 or any(char in value for char in ("\\", "*", "?", "%")):
        return True
    if value.endswith("/") or "//" in value:
        return True
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        return True
    if re.search(r"(?:secret|credential|token|privatekey|password|passwd|\.env|id_rsa|\.npmrc)", value, re.I):
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        paths = workflow_paths(args.base, args.head)
        for path in paths:
            examine(read_workflow(args.head, path))
    except Denied as e:
        print("DENY " + str(e), file=sys.stderr)
        return 2
    print(f"PASS: {len(paths)} changed workflow(s) inspected; mutation_authorized=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
