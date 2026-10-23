#!/usr/bin/env python3
"""No-new-debt, source-only detector for deprecated GitHub Actions commands.

This PR-controlled check is *not* independent merge, release, or VPS authorization.
Historical unchanged workflow blobs are grandfathered, not certified safe.
"""
from __future__ import annotations

import argparse
import contextvars
import os
import re
import selectors
import signal
import subprocess
import sys
import time
import unicodedata
from pathlib import PurePosixPath

SHA = re.compile(r"^[0-9a-f]{40}$")
COMMANDS = re.compile(r"::\s*(?:set-env|add-path|set-output|save-state)\b", re.I)
ENV = re.compile(r"actions_allow_unsecure_commands", re.I)
ESCAPED_SCALAR = re.compile(r"\\(?:x([0-9a-fA-F]{2})|u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8}))")
SHELL_CONTINUATION = re.compile(r"\\\r?\n[ \t]*")
ACTION_CODE_EXTENSIONS = frozenset({".js", ".cjs", ".mjs", ".ts", ".sh", ".bash", ".ps1", ".py", ".cmd", ".bat", ".zsh", ".rb", ".pl"})
ACTION_ENTRYPOINT_NAMES = frozenset({"entrypoint", "run", "start", "bootstrap", "Makefile", "GNUmakefile", "package.json"})
MAX_CHANGED = 250
MAX_BLOB = 512 * 1024
MAX_OUTPUT = 1024 * 1024
TIMEOUT = 15
MAX_SCAN_SECONDS = 120
_SCAN_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar('scan_deadline', default=None)


class Denied(ValueError):
    """A source proof could not be established safely."""


def git(*args: str, max_output: int = MAX_OUTPUT) -> bytes:
    """Read Git's output with an active byte limit and a monotonic deadline."""
    if not 0 < max_output <= MAX_OUTPUT:
        raise Denied("Invalid Git output budget")
    shared = _SCAN_DEADLINE.get()
    if shared is not None and time.monotonic() >= shared:
        raise Denied("Shared Git scan budget exhausted")
    try:
        proc = subprocess.Popen(
            ["git", "--no-replace-objects", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise Denied("Git source proof unavailable") from exc
    chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + TIMEOUT
    completed_ok = False
    shared = _SCAN_DEADLINE.get()
    if shared is not None:
        deadline = min(deadline, shared)
    try:
        assert proc.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Denied("Git process exceeded deadline")
                if not selector.select(remaining):
                    raise Denied("Git process stalled")
                chunk = os.read(proc.stdout.fileno(), min(65536, max_output + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_output:
                    raise Denied("Git output exceeds budget")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Denied("Git process exceeded deadline")
        if proc.wait(timeout=remaining) != 0:
            raise Denied("Git source proof failed")
        completed_ok = True
        return b"".join(chunks)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Denied("Git source proof unavailable") from exc
    finally:
        # A Git wrapper may fork, exit successfully, and leave descendants
        # holding our stdout pipe. Kill the entire session on *any* failure,
        # even after the parent has already exited.
        if not completed_ok:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()


def require_sha(value: str) -> str:
    if not SHA.fullmatch(value):
        raise Denied("Expected immutable 40-character lowercase Git SHA")
    if git("cat-file", "-t", value).strip() != b"commit":
        raise Denied("SHA is not a commit")
    return value


def entry(commit: str, path: str) -> tuple[str, str] | None:
    raw = git("ls-tree", "-z", commit, "--", path)
    if not raw:
        return None
    records = [r for r in raw.split(b"\0") if r]
    if len(records) != 1:
        raise Denied("Ambiguous tree path")
    try:
        metadata, name = records[0].split(b"\t", 1)
        mode, kind, _oid = metadata.decode("ascii").split()
        if name.decode("utf-8") != path:
            raise Denied("Unexpected Git tree path")
    except (UnicodeDecodeError, ValueError) as exc:
        raise Denied("Malformed Git tree entry") from exc
    return mode, kind


def proper_file(commit: str, path: str) -> bool:
    for prefix in (".github", ".github/workflows" if path.startswith(".github/workflows/") else ".github/actions"):
        parent = entry(commit, prefix)
        if parent != ("040000", "tree"):
            raise Denied("Missing or unsafe workflow ancestry")
    parts = PurePosixPath(path).parts
    for end in range(3, len(parts)):
        parent = entry(commit, "/".join(parts[:end]))
        if parent != ("040000", "tree"):
            raise Denied("Workflow path contains a non-directory")
    node = entry(commit, path)
    if node is None:
        return False
    if node != ("100644", "blob") and node != ("100755", "blob"):
        raise Denied("Workflow is not a regular Git blob")
    return True


def candidate(path: str, mode: str | None = None) -> bool:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or any(
        unicodedata.category(ch) in ("Cf", "Cc") for ch in path
    ):
        raise Denied("Invalid or control-bearing workflow pathname")
    if len(p.parts) == 3 and p.parts[:2] == (".github", "workflows"):
        return p.suffix in (".yml", ".yaml")
    if len(p.parts) >= 3 and p.parts[:2] == (".github", "actions"):
        # Action metadata alone is insufficient: locally executed JavaScript,
        # shell, and Python can emit legacy workflow commands without editing
        # any action.yml file. Treat these changed implementation blobs as
        # source too; unchanged historical files remain grandfathered.
        return (p.name in ("action.yml", "action.yaml", "Dockerfile", "Containerfile")
                or p.name in ACTION_ENTRYPOINT_NAMES
                or p.suffix.lower() in ACTION_CODE_EXTENSIONS
                or mode == "100755")
    return False


def require_clean_source(source: str) -> None:
    """Reject ambiguous control bytes without breaking ordinary CRLF or tabs."""
    if any(
        unicodedata.category(ch) == "Cf"
        or (unicodedata.category(ch) == "Cc" and ch not in "\r\n\t")
        for ch in source
    ):
        raise Denied("Hidden or invalid control character in changed source")


def decoded_scalar_escapes(source: str) -> str:
    """Conservatively surface hidden shell/YAML escapes before lexical checks.

    YAML's double-quoted scalar parsing expands x/u/U escapes. Expanding in
    all changed YAML (including comments/single quotes) intentionally errs
    on the side of denying ambiguous additions, not silent approval.
    """
    def expand(match: re.Match[str]) -> str:
        value = int(next(group for group in match.groups() if group), 16)
        if value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
            raise Denied("Invalid escaped Unicode scalar in workflow")
        return chr(value)

    for _ in range(8):
        newer = ESCAPED_SCALAR.sub(expand, source)
        if newer == source:
            return source
        require_clean_source(newer)
        source = newer
    raise Denied("Excessively nested YAML scalar escapes")


def check_content(content: bytes) -> None:
    if len(content) > MAX_BLOB:
        raise Denied("Workflow blob exceeds size limit")
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Denied("Workflow blob is not UTF-8") from exc
    require_clean_source(source)
    if ENV.search(source) or COMMANDS.search(source):
        raise Denied("New or changed workflow uses legacy unsafe command syntax")
    expanded = decoded_scalar_escapes(source)
    if ENV.search(expanded) or COMMANDS.search(expanded):
        raise Denied("Encoded legacy unsafe command syntax in changed workflow")
    # Shell removes backslash-newline pairs before executing run scripts. YAML
    # block scalars retain these lines, allowing invisible command assembly.
    joined = SHELL_CONTINUATION.sub("", expanded)
    if ENV.search(joined) or COMMANDS.search(joined):
        raise Denied("Shell-continued legacy unsafe command syntax")
    # Windows cmd.exe removes caret escaping before interpreting echo output.
    # Conservatively test the joined form for changed local Action scripts.
    cmd_joined = re.sub(r"\^(?=[A-Za-z_-])", "", joined)
    if ENV.search(cmd_joined) or COMMANDS.search(cmd_joined):
        raise Denied("CMD caret-escaped legacy unsafe command syntax")
    # PowerShell uses a backtick to escape literal characters and line breaks.
    # Only known static token escapes are normalized; expression evaluation
    # remains outside this conservative source-only boundary.
    ps_joined = re.sub(r"`\r?\n[ \t]*", "", joined)
    ps_joined = re.sub(r"`(?=[A-Za-z_-])", "", ps_joined)
    if ENV.search(ps_joined) or COMMANDS.search(ps_joined):
        raise Denied("PowerShell backtick-escaped legacy unsafe command syntax")

    # Unquoted POSIX-shell/backslash escapes can emit forbidden literal token
    # characters at runtime (e.g. echo ::set\-output). Reconstruct static
    # slash/caret/backtick combinations conservatively; this does not execute
    # script expressions or approve imported code.
    plain_tokens = re.sub(r"\\(?=[A-Za-z_-])", "", joined)
    plain_tokens = re.sub(r"\^(?=[A-Za-z_-])", "", plain_tokens)
    plain_tokens = re.sub(r"`\r?\n[ \t]*", "", plain_tokens)
    plain_tokens = re.sub(r"`(?=[A-Za-z_-])", "", plain_tokens)
    if ENV.search(plain_tokens) or COMMANDS.search(plain_tokens):
        raise Denied("Escaped legacy command tokens in changed source")



def scan(base: str, head: str) -> tuple[int, int]:
    """Enforce a shared monotonic ceiling across all Git reads in one scan."""
    if not 0 < MAX_SCAN_SECONDS <= 3600:
        raise Denied("Invalid shared scan budget")
    token = _SCAN_DEADLINE.set(time.monotonic() + MAX_SCAN_SECONDS)
    try:
        return _scan(base, head)
    finally:
        _SCAN_DEADLINE.reset(token)


def _scan(base: str, head: str) -> tuple[int, int]:
    base, head = require_sha(base), require_sha(head)
    # Deny disconnected/unrelated bases, including stale unrelated source trees.
    try:
        git("merge-base", "--is-ancestor", base, head)
    except Denied as exc:
        raise Denied("Base is not an ancestor of HEAD") from exc
    for root in (".github", ".github/workflows"):
        if entry(head, root) != ("040000", "tree"):
            raise Denied("Required workflow root missing or unsafe")
    actions = entry(head, ".github/actions")
    if actions is not None and actions != ("040000", "tree"):
        raise Denied("Composite action root is not a Git tree")
    raw = git("diff", "--no-ext-diff", "--no-renames", "--name-only", "-z",
              "--diff-filter=ACMRT", base, head, "--", ".github/workflows", ".github/actions")
    try:
        changed = [p.decode("utf-8") for p in raw.split(b"\0") if p]
    except UnicodeDecodeError as exc:
        raise Denied("Non-UTF8 Git changed path") from exc
    if len(changed) > MAX_CHANGED:
        raise Denied("Too many changed workflow paths")
    checked = 0
    for path in changed:
        # Changes to .github/actions/ may introduce an opaque submodule or a
        # symlink without changing an action.yml. Such paths must not bypass
        # the metadata-file filter: a Git link cannot be source-verified here.
        if path.startswith((".github/actions/", ".github/workflows/")):
            node = entry(head, path)
            if node is not None and node not in (
                ("100644", "blob"), ("100755", "blob"), ("040000", "tree")
            ):
                raise Denied("Changed Actions source uses unsafe Git tree entry")
        if not candidate(path, node[0] if node is not None else None):
            continue
        if not proper_file(head, path):
            continue  # A deleted file cannot introduce a new unsafe command.
        size = git("cat-file", "-s", f"{head}:{path}")
        try:
            nbytes = int(size)
        except ValueError as exc:
            raise Denied("Malformed Git blob size") from exc
        if nbytes > MAX_BLOB:
            raise Denied("Workflow blob exceeds size limit")
        content = git("show", f"{head}:{path}", max_output=MAX_BLOB)
        check_content(content)
        checked += 1
    return len(changed), checked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    args = ap.parse_args()
    try:
        changed, checked = scan(args.base, args.head)
    except Denied as exc:
        print(f"DENY: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: {changed} changed workflow/action paths; {checked} new or edited YAML files checked. "
          "Unchanged legacy unreviewed; authorizes_merge=false; authorizes_deploy=false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
