#!/usr/bin/env python3
"""Deny new workflow_run token-write exposure. Source only: no deploy authority."""
from __future__ import annotations
import argparse
import os
import re
import subprocess
import selectors
import sys
import time
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
LIMIT = 1024 * 1024

def git(root: Path, *args: str, limit: int = LIMIT) -> bytes:
    """Read bounded Git stdout with a wall-clock deadline, no stderr content."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("invalid_git_limit")
    command = ["git", "-C", str(root), *args]
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    )
    data = bytearray()
    deadline = time.monotonic() + 15
    try:
        assert proc.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("git_elapsed_deadline")
                chunk = os.read(proc.stdout.fileno(), min(65536, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("git_output_limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("git_elapsed_deadline")
        code = proc.wait(timeout=remaining)
        if code:
            raise subprocess.CalledProcessError(code, command)
        return bytes(data)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        raise
    finally:
        if proc.stdout is not None:
            proc.stdout.close()


def paths_changed(root: Path, base: str, head: str) -> list[str]:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise ValueError("invalid_revision")
    if git(root, "rev-parse", "HEAD", limit=64).strip().decode() != head:
        raise ValueError("wrong_checkout")
    for rev in (base, head):
        if git(root, "cat-file", "-t", rev, limit=64).strip() != b"commit":
            raise ValueError("not_commit")
    subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", base, head],
                   check=True, timeout=12, capture_output=True,
                   env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"})
    if not git(root, "ls-tree", head, "--", ".github/workflows",
               limit=512).startswith(b"040000 tree "):
        raise ValueError("workflow_directory_not_tree")
    raw = git(root, "diff", "--name-only", "-z", "--diff-filter=ACMRT",
              f"{base}...{head}", "--", ".github/workflows/", limit=131072)
    paths = [p.decode("utf-8", "strict") for p in raw.split(b"\0") if p]
    if len(paths) > 250 or len(paths) != len(set(paths)):
        raise ValueError("incomplete_path_inventory")
    if any(not WORKFLOW.fullmatch(p) for p in paths):
        raise ValueError("unsafe_workflow_path")
    return paths

def mapping_lines(source: str) -> list[tuple[int, str]]:
    """Skip literal run blocks using their actual first body-line indentation."""
    result = []
    header = body = None
    for line in source.splitlines():
        prefix = line[:len(line) - len(line.lstrip(" \t"))]
        if "\t" in prefix:
            raise ValueError("tab_indentation")
        value = line.lstrip(" ")
        indent = len(line) - len(value)
        if header is not None:
            if not value:
                continue
            if body is None and indent > header:
                body = indent
                continue
            if body is not None and indent >= body:
                continue
            header = body = None
        if not value or value.startswith("#"):
            continue
        result.append((indent, value))
        if re.fullmatch(r"(?:-[ ]+)?run:[ ]*[|>]-?(?:[ ]+#.*)?", value):
            header = indent
    return result

def classify_workflow(source: str) -> list[str]:
    rows = mapping_lines(source)
    trigger = ambiguous = False
    on_indent = None
    in_sequence = False
    for indent, value in rows:
        if re.match(r"""(?:on|'on'|"on")[ ]*:""", value):
            on_indent = indent
            trailing = value.split(":", 1)[1]
            in_sequence = trailing.strip().startswith("[")
            trigger |= bool(re.search(r"\bworkflow_run\b", trailing))
            ambiguous |= "*" in trailing or "&" in trailing or r"\u" in trailing
            continue
        if on_indent is not None:
            if indent > on_indent:
                # The on: block may be a mapping OR a YAML block sequence.
                trigger |= bool(re.match(r"""(?:-[ ]+)?['"]?workflow_run['"]?[ ]*(?::|$)""", value))
                if in_sequence:
                    trigger |= bool(re.search(r"\bworkflow_run\b", value))
                ambiguous |= value.startswith("<<:") or r"\u" in value or r"\x" in value
                continue
            if in_sequence:
                trigger |= bool(re.search(r"\bworkflow_run\b", value))
            on_indent = None
            in_sequence = False
        ambiguous |= value.startswith(("<<:", "? on", "? workflow_run"))
    if ambiguous:
        return ["unreviewed_yaml_event_alias_or_encoding"]
    if not trigger:
        return []
    root_readonly = False
    root_perms = None
    for indent, value in rows:
        if indent == 0 and re.match(r"permissions[ ]*:", value):
            root_perms = indent
            trailing = value.split(":", 1)[1].strip()
            if trailing == "read-all":
                root_readonly = True
            elif trailing.startswith("{"):
                if re.search(r"\bwrite\b", trailing):
                    return ["workflow_run_write_permission"]
                root_readonly |= bool(re.search(
                    r"\b(?:contents|actions|checks|issues|pull-requests):[ ]*read\b",
                    trailing))
            elif trailing and not trailing.startswith("#"):
                return ["workflow_run_ambiguous_root_permissions"]
            continue
        if root_perms is not None and indent > root_perms:
            if re.search(r"\b[a-z-]+[ ]*:[ ]*write\b", value):
                return ["workflow_run_write_permission"]
            root_readonly |= bool(re.match(
                r"[a-z-]+[ ]*:[ ]*read(?:[ \t]*(?:#.*)?)?$", value))
        elif root_perms is not None:
            root_perms = None
    for _indent, value in rows:
        # YAML permission aliases/tags/encoded scalars may resolve to a write
        # grant after expansion; do not infer read-only from raw text.
        if re.match(r"(?:permissions|contents|actions|checks|issues|pull-requests|id-token)[ ]*:[ ]*(?:[*&]|!!|['\"]|\\\\u|\\\\x)", value):
            return ["workflow_run_unreviewed_permission_alias"]
        if re.match(r"permissions[ ]*:[ ]*write-all\b", value):
            return ["workflow_run_write_permission"]
        if re.search(r"\b[a-z-]+[ ]*:[ ]*write\b", value):
            return ["workflow_run_write_permission"]
        if value.startswith("permissions:") and re.search(r"\bwrite\b", value):
            return ["workflow_run_write_permission"]
    return [] if root_readonly else ["workflow_run_missing_readonly_permissions"]

def scan(root: Path, base: str, head: str) -> dict[str, list[str]]:
    found = {}
    for path in paths_changed(root, base, head):
        mode = git(root, "ls-tree", head, "--", path, limit=512)
        if not mode.startswith((b"100644 blob ", b"100755 blob ")):
            raise ValueError("unsafe_workflow_mode")
        size = int(git(root, "cat-file", "-s", f"{head}:{path}", limit=32).strip())
        if size > LIMIT:
            raise ValueError("workflow_too_large")
        raw = git(root, "show", f"{head}:{path}")
        if b"\0" in raw:
            raise ValueError("workflow_nul")
        findings = classify_workflow(raw.decode("utf-8", "strict"))
        if findings:
            found[path] = findings
    return found

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    a = p.parse_args(argv)
    try:
        issues = scan(a.root, a.base, a.head)
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        print(f"DENY untrusted workflow source ({type(error).__name__})", file=sys.stderr)
        return 2
    for file, reasons in sorted(issues.items()):
        print(f"DENY {file}: {','.join(reasons[:10])}", file=sys.stderr)
    if issues:
        return 1
    print("PASS: changed workflow_run sources retain explicit non-writing token privileges")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
