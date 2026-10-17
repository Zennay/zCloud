#!/usr/bin/env python3
"""Fail-closed, source-only ratchet for direct Git writes in new/edited workflows.

Advisory PR policy: no deployment, merge, runner, queue, or permission authority.
Existing workflow blobs are grandfathered only while byte-for-byte unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import subprocess
import sys
import time
import unicodedata

HEX = re.compile(r"^[0-9a-f]{40}$")
WORKFLOW = re.compile(r"^\.github/workflows/[^/\x00]+\.(?:yml|yaml)$")
COMPOSITE = re.compile(r"^\.github/actions/(?:[^/\x00]+/)+action\.(?:yml|yaml)$")
GIT_PUSH = re.compile(r"(?i)(?<![\w-])git\s+(?:(?:-C\s+\S+|-c\s+\S+|--[\w-]+(?:=\S+)?|--git-dir\s+\S+)\s+)*(?:push\b|['\"]push['\"](?=\s|$|[;|&]))")
# Reference creation uses the /git/refs collection; tags and notes also
# update Git refs, not just the older /git/refs/heads/... endpoint.
REFS_API = re.compile(r"(?i)\bgit/refs(?:[/#?]|%2f|\\u002f|\b)")
GH_REF_WRITE = re.compile(r"(?i)\bgh\s+api\b.{0,200}(?:\b(?:PATCH|POST|DELETE|PUT)\b|(?:-X|--method)\s+)")
OCTOKIT_REF_WRITE = re.compile(
    r"(?i)\b(?:github|octokit)\.(?:rest\.)?git\.(?:updateRef|createRef|deleteRef)\s*\("
)

# Git supports aliases in -c/config arguments. Invoking the alias may
# execute a write without a direct push subcommand in the command line.
GIT_WRITE_ALIAS = re.compile(
    r"(?i)\balias\.[a-z0-9_.-]+\s*=\s*['\"]?\s*(?:!(?:git\s+)?|git\s+)?(?:push|send-pack)\b"
)
GIT_SHELL_ALIAS = re.compile(
    r"(?i)\balias\.[a-z0-9_.-]+\s*=\s*['\"]?\s*!"
)

# Git's environment-backed config can define an alias across separate YAML
# lines, bypassing a purely single-line Git command detector.
GIT_ENV_CONFIG = re.compile(
    r"(?i)\bGIT_CONFIG_(?:COUNT|KEY_[0-9]+|VALUE_[0-9]+)\b"
)
GOVERNANCE_API = re.compile(
    r"(?i)\b(?:github|octokit)\.(?:rest\.)?repos\."
    r"(?:updateBranchProtection|deleteBranchProtection|createRepoRuleset|"
    r"updateRepoRuleset|deleteRepoRuleset)\s*\("
)

# A PR merge is an indirect write to the protected branch, even without
# git push or the Git refs REST endpoint being present in workflow source.
GH_PR_MERGE = re.compile(
    r"(?i)\bgh\s+(?:(?:(?:-R|--repo)\s+\S+\s+)?pr\s+merge)\b"
)
OCTOKIT_PR_MERGE = re.compile(
    r"(?i)\b(?:github|octokit)\.(?:rest\.)?"
    r"(?:pulls\.merge|repos\.merge)\s*\("
)

# A workflow can call Node/Python child-process APIs with separate argv
# tokens, avoiding the literal "git push" substring in reviewed source.
ARGV_GIT_WRITE = re.compile(
    r"(?i)['\"]git['\"]\s*,\s*(?:\[\s*)?"
    r"['\"](?:push|send-pack)['\"]"
)
REST_MERGE_PATH = re.compile(
    r"(?i)\brepos/[^/\s'\"<>]+/[^/\s'\"<>]+/"
    r"(?:merges\b|pulls/[^/\s'\"<>]+/merge\b)"
)

# GraphQL can update refs with a POST to /graphql and no git/refs URL.
# Conservatively deny dynamic GraphQL entrypoints in newly edited sources.
GRAPHQL_ENDPOINT = re.compile(
    r"(?i)(?:\bgh\s+api\b[^\n]{0,256}\bgraphql\b"
    r"|\b(?:github|octokit)\.graphql\s*\("
    r"|https?://api\.github\.com/graphql\b)"
)
MAX_BLOB = 512 * 1024
MAX_OUTPUT = 16 * 1024 * 1024
MAX_PATHS = 2000
# End-to-end budget bounds large inventories across every child Git call.
SCAN_DEADLINE = time.monotonic() + 120


class AdmissionError(ValueError):
    pass


def git(*args: str) -> bytes:
    """Bound stdout during collection, kill/reap on timeout and never print stderr."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               GIT_NO_LAZY_FETCH="1")
    if time.monotonic() >= SCAN_DEADLINE:
        raise AdmissionError("scan_deadline_exceeded")
    p = None
    selector = selectors.DefaultSelector()
    chunks: list[bytes] = []
    size = 0
    try:
        p = subprocess.Popen(["git", "-c", "core.pager=cat", *args],
                             env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, bufsize=0)
        assert p.stdout is not None
        selector.register(p.stdout, selectors.EVENT_READ)
        deadline = min(time.monotonic() + 15, SCAN_DEADLINE)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AdmissionError("git_timeout")
            if not selector.select(min(remaining, 0.25)):
                continue
            data = os.read(p.stdout.fileno(), min(65536, MAX_OUTPUT - size + 1))
            if not data:
                break
            size += len(data)
            if size > MAX_OUTPUT:
                raise AdmissionError("git_failed_or_oversize")
            chunks.append(data)
        remaining = max(0.01, deadline - time.monotonic())
        if p.wait(timeout=remaining) != 0:
            raise AdmissionError("git_failed_or_oversize")
        return b"".join(chunks)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdmissionError("git_unavailable") from exc
    finally:
        selector.close()
        if p is not None:
            if p.poll() is None:
                p.kill()
            p.wait()
            if p.stdout is not None:
                p.stdout.close()


def validate_commit(value: str) -> str:
    if not HEX.fullmatch(value):
        raise AdmissionError("invalid_commit_sha")
    obj_type = git("cat-file", "-t", value).strip()
    if obj_type != b"commit":
        raise AdmissionError("not_commit")
    return value


def tree(commit: str) -> dict[str, str]:
    listing = git("ls-tree", "-rz", "--full-tree", commit, "--", ".github/workflows", ".github/actions")
    if not listing:
        raise AdmissionError("missing_workflows")
    entries = listing.split(b"\x00")
    if entries[-1] != b"":
        raise AdmissionError("corrupt_tree")
    if len(entries) > MAX_PATHS:
        raise AdmissionError("too_many_paths")
    result: dict[str, str] = {}
    for entry in entries[:-1]:
        try:
            metadata, path_bytes = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ")
            path = path_bytes.decode("utf-8", errors="strict")
        except (ValueError, UnicodeError) as exc:
            raise AdmissionError("invalid_tree_entry") from exc
        if path in (".github/workflows", ".github/actions"):
            raise AdmissionError("nonregular_source_root")
        if path.startswith(".github/actions/") and mode == "120000":
            raise AdmissionError("nonregular_action_tree")
        if not (WORKFLOW.fullmatch(path) or COMPOSITE.fullmatch(path)):
            # Only workflow bodies and local composite action metadata are scoped.
            continue
        if mode != "100644" or kind != "blob" or not HEX.fullmatch(oid):
            raise AdmissionError("nonregular_workflow")
        if path in result:
            raise AdmissionError("duplicate_workflow_path")
        result[path] = oid
    if not any(WORKFLOW.fullmatch(path) for path in result):
        raise AdmissionError("missing_workflows")
    return result


def shell_git_writer(line: str, forbidden: tuple[str, ...]) -> bool:
    """Token-based direct Git-writer classifier; handles quoted option arguments."""
    # Tokenize rather than short-circuiting on a raw regex: the latter
    # mistakes 'alias.demo=echo push' inside a quoted -c value for a command.
    for match in re.finditer(r"(?i)(?<![\w-])git\s+", line):
        lexer = shlex.shlex(line[match.end():], posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        try:
            pieces = list(lexer)
        except ValueError:
            # Example: actual_head="$(git rev-parse HEAD)" is well-quoted
            # as an entire shell line, but its suffix from "git" onwards
            # has an unmatched closing quote. Do not falsely deny reads.
            try:
                full = shlex.shlex(line, posix=True, punctuation_chars=";&|")
                full.whitespace_split = True
                full.commenters = ""
                list(full)
            except ValueError:
                return True
            if any(word in line.lower() for word in forbidden):
                return True
            continue
        for part in pieces[:16]:
            if part in (";", "&&", "||", "|", "&"):
                break
            if part.lower() in forbidden:
                return True
    return False


def suspicious_lines(source: str) -> list[str]:
    findings: list[str] = []
    # Conservative lexical policy. Multiline/dynamic commands are not parsed
    # as shell or YAML; see operator guide for explicit limitations.
    # POSIX shell removes a backslash-newline before tokenization. Fold
    # physical continuations before testing commands, but not arbitrary lines.
    normalized = re.sub(r"\\[ \t]*\r?\n[ \t]*", " ", source)
    for line in normalized.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if shell_git_writer(line, ("push",)):
            findings.append("git_push")
        if shell_git_writer(line, ("send-pack",)):
            findings.append("git_send_pack")
        if REFS_API.search(line) and (
            re.search(r"(?i)\b(?:gh\s+api|curl|wget|PATCH|POST|DELETE|PUT)\b", line)
            or "api.github.com" in line.lower()
        ):
            findings.append("git_refs_api")
        if re.search(r"(?i)\bgh\s+api\b", line) and GH_REF_WRITE.search(line):
            # Dynamic gh API writes are unsafe when target cannot be proven.
            findings.append("gh_api_write")
        if OCTOKIT_REF_WRITE.search(line):
            findings.append("octokit_git_refs_write")
        if GIT_WRITE_ALIAS.search(line) or GIT_SHELL_ALIAS.search(line):
            findings.append("git_alias_indirect_write")
        if GIT_ENV_CONFIG.search(line):
            findings.append("git_environment_config_injection")
        if GOVERNANCE_API.search(line):
            findings.append("octokit_repository_governance_write")
        if GH_PR_MERGE.search(line) or OCTOKIT_PR_MERGE.search(line):
            findings.append("indirect_pr_merge_write")
        if ARGV_GIT_WRITE.search(line):
            findings.append("separate_argv_git_writer")
        if REST_MERGE_PATH.search(line) and (
            re.search(r"(?i)\b(?:gh\s+api|curl|wget)\b", line)
            or "api.github.com" in line.lower()
        ):
            findings.append("rest_merge_write")
        if GRAPHQL_ENDPOINT.search(line):
            findings.append("dynamic_graphql_write_unverifiable")
        if len(findings) >= 15:
            break
    return sorted(set(findings))


def audit(base: str, head: str) -> dict[str, object]:
    validate_commit(base)
    validate_commit(head)
    # Unrelated histories must not be used to launder historic unsafe blobs.
    if git("merge-base", base, head).strip() != base.encode("ascii"):
        raise AdmissionError("unrelated_commit_history")
    base_tree = tree(base)
    head_tree = tree(head)
    changed = sorted(p for p in head_tree if base_tree.get(p) != head_tree[p])
    if len(changed) > MAX_PATHS:
        raise AdmissionError("too_many_changes")
    flagged: list[dict[str, object]] = []
    for path in changed:
        # Check immutable object size before requesting any potentially large bytes.
        oid = head_tree[path]
        raw_size = git("cat-file", "-s", oid).strip()
        if not raw_size.isdigit() or int(raw_size) > MAX_BLOB:
            raise AdmissionError("invalid_workflow_blob")
        blob = git("cat-file", "blob", oid)
        if len(blob) != int(raw_size) or b"\x00" in blob:
            raise AdmissionError("invalid_workflow_blob")
        try:
            content = blob.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise AdmissionError("invalid_workflow_encoding") from exc
        # Invisible format controls (RTL overrides, ZWJ, bidi isolates, etc.)
        # can make a reviewed workflow appear different from the Git blob.
        # Preserve ordinary non-ASCII letters: only invisible controls deny.
        if any(ord(ch) in (0x2028, 0x2029) or
               unicodedata.category(ch) == "Cf" for ch in content):
            raise AdmissionError("opaque_workflow_encoding")
        reasons = suspicious_lines(content)
        if reasons:
            flagged.append({"path": path, "reasons": reasons})
    return {
        "status": "deny" if flagged else "pass",
        "base": base, "head": head,
        "workflows_in_head": sum(WORKFLOW.fullmatch(p) is not None for p in head_tree),
        "composites_in_head": sum(COMPOSITE.fullmatch(p) is not None for p in head_tree),
        "changed_workflows_checked": sum(WORKFLOW.fullmatch(p) is not None for p in changed),
        "changed_composites_checked": sum(COMPOSITE.fullmatch(p) is not None for p in changed),
        "legacy_workflows_not_rescanned": sum(WORKFLOW.fullmatch(p) is not None for p in head_tree if p not in changed),
        "legacy_composites_not_rescanned": sum(COMPOSITE.fullmatch(p) is not None for p in head_tree if p not in changed),
        "findings": flagged,
        "source_only": True, "authorizes_merge": False,
        "authorizes_deploy": False, "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        result = audit(args.base, args.head)
    except AdmissionError as exc:
        result = {"status": "deny", "reason": str(exc), "source_only": True,
                  "authorizes_merge": False, "authorizes_deploy": False,
                  "mutation_performed": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
