#!/usr/bin/env python3
"""Deny newly introduced reusable-workflow secret inheritance (source-only).

This is an intentionally conservative lexical ratchet, NOT a YAML parser, release
approval, or a proof that an existing workflow is safe. No GitHub/VPS mutation.
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

SHA = re.compile(r"^[0-9a-f]{40}$")
ROOT = ".github/workflows/"
SUFFIXES = (".yml", ".yaml")
MAX_FILES = 400
MAX_BYTES = 524288
MAX_TOTAL_BYTES = 16 * 1024 * 1024


class Denied(Exception):
    pass


def git(*args: str, limit: int = MAX_TOTAL_BYTES) -> bytes:
    """Bound both captured output and wall time before trusting Git objects.

    subprocess.run(stdout=PIPE) would first buffer the *entire* untrusted blob
    in memory, making a post-hoc size check ineffective against large objects.
    Read through a selector so an oversized producer is killed early.
    """
    if type(limit) is not int or limit < 0 or limit > MAX_TOTAL_BYTES:
        raise Denied("invalid_source_budget")
    deadline = time.monotonic() + 20
    try:
        proc = subprocess.Popen(
            ["git", *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise Denied("git_source_unavailable") from exc
    selector = selectors.DefaultSelector()
    data = bytearray()
    try:
        assert proc.stdout is not None
        selector.register(proc.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Denied("git_source_timeout")
            ready = selector.select(timeout=remaining)
            if not ready:
                raise Denied("git_source_timeout")
            for key, _ in ready:
                # Even the last read is bounded: one byte beyond limit is
                # sufficient to prove a violation and terminate the process.
                chunk = os.read(key.fd, min(65536, limit + 1 - len(data)))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                data.extend(chunk)
                if len(data) > limit:
                    raise Denied("source_budget_exceeded")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Denied("git_source_timeout")
        try:
            code = proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise Denied("git_source_timeout") from exc
        if code != 0:
            raise Denied("git_source_unavailable")
        return bytes(data)
    except OSError as exc:
        raise Denied("git_source_unavailable") from exc
    finally:
        selector.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()


def require_workflow_root(sha: str) -> None:
    # Listing a path beneath a symlink or absent directory may return an
    # empty file set. Never mistake a missing workflows directory for safety.
    for root in (".github", ".github/workflows"):
        try:
            kind = git("cat-file", "-t", f"{sha}:{root}", limit=32).strip()
        except Denied as exc:
            raise Denied("missing_workflow_directory") from exc
        if kind != b"tree":
            raise Denied("non_directory_workflow_root")


def paths_and_modes(sha: str) -> dict[str, tuple[str, str]]:
    records = git("ls-tree", "-r", "-z", sha, "--", ROOT).split(b"\0")
    result: dict[str, tuple[str, str]] = {}
    for rec in records:
        if not rec:
            continue
        try:
            desc, rawpath = rec.split(b"\t", 1)
            mode, kind, oid = desc.decode("ascii").split()
            path = rawpath.decode("utf-8", "strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise Denied("ambiguous_tree") from exc
        if path in result:
            raise Denied("duplicate_path")
        if not path.startswith(ROOT) or not path.endswith(SUFFIXES):
            continue
        if mode not in {"100644", "100755"} or kind != "blob" or not SHA.fullmatch(oid):
            raise Denied("unsupported_workflow_file")
        result[path] = (mode, oid)
        if len(result) > MAX_FILES:
            raise Denied("too_many_workflows")
    return result


def has_secret_inherit(text: str) -> bool:
    """Conservative screen of both simple and suspicious opaque YAML spellings.

    Historical matching files remain unchanged by hash. Any changed file
    containing a possible inheritance syntax must get independent review.
    """
    clean = "\n".join(
        line for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    # Most GitHub Actions implementations use 'secrets: inherit' literally.
    direct = re.search(
        r"(?im)^\s*['\"]?secrets['\"]?\s*:\s*['\"]?inherit['\"]?\s*(?:#.*)?$",
        clean,
    )
    if direct:
        return True
    # More conservative than the parser: catches quoted/flow/indented variations,
    # at the cost of occasionally requesting independent review for safe files.
    # YAML complex mapping keys may put '?' and ':' on separate lines.
    # Literal unicode escapes could conceal either word inside a quoted key.
    # Such opaque candidates are denied rather than silently trusted.
    if re.search(r"\\(?:u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2})", clean):
        return True
    return bool(
        re.search(r"(?i)\bsecrets\b", clean)
        and re.search(r"(?i)\binherit\b", clean)
    )


def inspect(base: str, head: str) -> dict[str, object]:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head) or base == head:
        raise Denied("invalid_commit_identity")
    if not git("merge-base", "--is-ancestor", base, head, limit=0) == b"":
        raise Denied("invalid_commit_graph")
    require_workflow_root(base)
    require_workflow_root(head)
    base_tree = paths_and_modes(base)
    head_tree = paths_and_modes(head)
    # A present directory holding only non-workflow files can otherwise
    # look like an empty, passing inventory after disabling all CI.
    if not base_tree or not head_tree:
        raise Denied("empty_workflow_inventory")
    changed = sorted(path for path in head_tree if base_tree.get(path) != head_tree[path])
    total = 0
    denied_paths = 0
    for path in changed:
        # Read immutable Git blobs, NOT mutable filesystem checkout paths.
        oid = head_tree[path][1]
        size = git("cat-file", "-s", oid, limit=20)
        try:
            count = int(size.strip())
        except ValueError as exc:
            raise Denied("bad_blob_size") from exc
        if count < 0 or count > MAX_BYTES:
            raise Denied("oversized_workflow")
        total += count
        if total > MAX_TOTAL_BYTES:
            raise Denied("source_budget_exceeded")
        blob = git("cat-file", "blob", oid, limit=MAX_BYTES)
        try:
            workflow = blob.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise Denied("invalid_workflow_utf8") from exc
        if "\0" in workflow:
            raise Denied("invalid_workflow_content")
        if has_secret_inherit(workflow):
            denied_paths += 1
    if denied_paths:
        raise Denied("modified_workflow_may_inherit_secrets")
    return {
        "status": "pass",
        "base": base,
        "head": head,
        "workflows_examined": len(head_tree),
        "changed_workflows_checked": len(changed),
        "mutation_performed": False,
        "authorizes_deploy": False,
        "authorizes_merge": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect(args.base, args.head)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Denied as exc:
        print(json.dumps({
            "status": "deny", "reason": str(exc), "mutation_performed": False,
            "authorizes_deploy": False, "authorizes_merge": False,
        }, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
