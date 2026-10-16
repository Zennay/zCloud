#!/usr/bin/env python3
"""Deny newly introduced direct GitHub expression evaluation in github-script JavaScript."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import subprocess
import time
import sys
from collections import Counter

MAX_WORKFLOWS = 768
MAX_BLOB = 1_048_576
MAX_TOTAL = 32 * 1_048_576
MAX_FINDINGS = 4096
SHA = re.compile(r"^[0-9a-f]{40}$")
USE = re.compile(r"^\s*(?:-\s*)?uses\s*:\s*['\"]?actions/github-script@[^\s#'\"]+", re.I)
LIST_KEY = re.compile(r"^(\s*)-\s*(?:[\w-]+|'[^']+'|\"[^\"]+\")\s*:")
SCRIPT_KEY = re.compile(r"^(\s*)(?:script|'script'|\"script\")\s*:\s*(.*)$")
WITH_KEY = re.compile(r"^(\s*)with\s*:")
EXPR = re.compile(r"\$\s*\{\{")
ESCAPE = re.compile(r"\\(?:x24|u0024|U00000024|x7b|u007b|U0000007b)", re.I)
AMBIG = re.compile(r"(?:^|\s)(?:\*[\w-]+|![\w!]+)\s*(?:#.*)?$")
FLOW = re.compile(r"(?:\bwith\s*:\s*\{|\bscript\s*:\s*\{|\{\s*script\s*:)")
GIT_ENV = {
    key: value for key, value in os.environ.items()
    if not key.startswith(("GIT_", "GITHUB_")) and key not in {"SSH_ASKPASS", "GIT_ASKPASS"}
}
GIT_ENV["GIT_CONFIG_NOSYSTEM"] = "1"
GIT_ENV["GIT_NO_REPLACE_OBJECTS"] = "1"
GIT_ENV["GIT_TERMINAL_PROMPT"] = "0"

ACTION_DECLARED = re.compile(
    r"""^\s*(?:-\s*)?(?:uses|'uses'|"uses")\s*:\s*['"]?actions/github-script@""", re.I
)
FLOW_USE = re.compile(
    r"""^\s*-\s*\{[^#\n]*?\buses\s*:\s*['"]?actions/github-script@""", re.I
)
BLOCK_HEADER = re.compile(
    r"""^\s*(?:-\s*)?(?:[\w.-]+|'[^']+'|"[^"]+")\s*:\s*[|>][0-9+-]*\s*(?:#.*)?$"""
)


def structural_lines(lines: list[str]) -> list[str]:
    """Ignore YAML comments and nested block-scalar *data* when finding action keys.

    The original lines remain available to inspect_step for JavaScript text.
    An indented JS string mentioning 'uses:' must not become a fake action step.
    """
    visible: list[str] = []
    block_indent: int | None = None
    for line in lines:
        trimmed = line.lstrip(" ")
        indent = len(line) - len(trimmed)
        if block_indent is not None:
            if not trimmed.strip() or trimmed.startswith("#") or indent > block_indent:
                visible.append("")
                continue
            block_indent = None
        if trimmed.startswith("#"):
            visible.append("")
            continue
        visible.append(line)
        if BLOCK_HEADER.match(line):
            block_indent = indent
    return visible



class EvidenceError(Exception):
    pass


def git(*args: str, cap: int = MAX_TOTAL) -> bytes:
    """Bound bytes *during* subprocess output, not after unbounded communicate()."""
    if cap < 0 or cap > MAX_TOTAL:
        raise EvidenceError("invalid Git evidence budget")
    try:
        with subprocess.Popen(
            ["git", "--no-pager", *args], env=GIT_ENV, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        ) as process:
            assert process.stdout is not None
            deadline = time.monotonic() + 12
            output = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise EvidenceError("Git evidence exceeded time budget")
                    if not selector.select(remaining):
                        raise EvidenceError("Git evidence exceeded time budget")
                    chunk = os.read(process.stdout.fileno(), min(65536, cap + 1 - len(output)))
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > cap:
                        raise EvidenceError("Git evidence exceeds bounded budget")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EvidenceError("Git evidence exceeded time budget")
            if process.wait(timeout=remaining):
                raise EvidenceError("immutable Git command refused evidence")
            return bytes(output)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("immutable Git evidence unavailable") from exc


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def inventory(sha: str) -> dict[str, str]:
    raw = git("ls-tree", "-rz", "--full-tree", sha, cap=4_000_000)
    result: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            meta, name = item.split(b"\t", 1)
            mode, kind, blob = meta.decode("ascii").split(" ")
            path = name.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise EvidenceError("invalid workflow tree metadata") from exc
        is_workflow = path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))
        is_composite = path in ("action.yml", "action.yaml") or path.endswith(("/action.yml", "/action.yaml"))
        if not (is_workflow or is_composite):
            continue
        if mode != "100644" or kind != "blob" or not SHA.fullmatch(blob):
            raise EvidenceError("symlink or invalid workflow object denied")
        if path in result:
            raise EvidenceError("duplicate workflow paths denied")
        result[path] = blob
    if len(result) > MAX_WORKFLOWS:
        raise EvidenceError("workflow inventory exceeds budget")
    return result


def steps_for_use(lines: list[str], idx: int) -> tuple[int, int]:
    use_indent = len(lines[idx]) - len(lines[idx].lstrip(" "))
    start, start_indent = -1, -1
    for j in range(idx, -1, -1):
        m = LIST_KEY.match(lines[j])
        if m and len(m.group(1)) <= use_indent:
            start, start_indent = j, len(m.group(1))
            break
    if start < 0:
        raise EvidenceError("github-script action has no readable step boundary")
    end = len(lines)
    for j in range(idx + 1, len(lines)):
        line = lines[j]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent < start_indent or (indent == start_indent and (LIST_KEY.match(line) or not line.lstrip().startswith("-"))):
            end = j
            break
    return start, end


def inspect_step(lines: list[str], path: str, start: int, end: int, full_source: str) -> tuple[str, str] | None:
    body = "".join(lines[start:end])
    why: set[str] = set()
    script_found = False
    with_indent = -1
    for i in range(start, end):
        line = lines[i]
        wm = WITH_KEY.match(line)
        if wm:
            with_indent = len(wm.group(1))
        if FLOW.search(line) or ("\\x" in line and "script" in line):
            why.add("ambiguous_yaml")
        # YAML unescapes double-quoted mapping keys before the Action uses
        # them. An encoded script key must not bypass the literal-key parser.
        if with_indent >= 0 and line.lstrip().startswith((chr(34), chr(39))) and any(chr(92) + mark in line for mark in ("x", "u", "U")):
            why.add("ambiguous_yaml")
        sm = SCRIPT_KEY.match(line)
        if sm is None:
            continue
        script_indent = len(sm.group(1))
        if with_indent < 0 or script_indent <= with_indent:
            continue
        script_found = True
        scalar = sm.group(2)
        j = i + 1
        while j < end:
            continuation = lines[j]
            if continuation.strip() and not continuation.lstrip().startswith("#"):
                depth = len(continuation) - len(continuation.lstrip(" "))
                if depth <= script_indent:
                    break
            scalar += continuation
            j += 1
        if EXPR.search(scalar):
            why.add("inline_expression")
        if ESCAPE.search(scalar) or AMBIG.search(sm.group(2)):
            why.add("ambiguous_yaml")
    # github-script evaluates JavaScript from with.script; flow-style maps, aliases
    # and escapes cannot be safely interpreted by this intentionally strict scanner.
    if re.search(r'(?m)^\s*with\s*:\s*[*!]\S+', body):
        why.add("ambiguous_yaml")
    if any(line.lstrip().startswith(("? script", "? \"script\"", "? 'script'")) for line in body.splitlines()):
        why.add("ambiguous_yaml")
    if with_indent >= 0 and any(line.lstrip().startswith("<<: *") for line in body.splitlines()):
        why.add("ambiguous_yaml")
    if not script_found and (FLOW.search(body) or re.search(r"\bscript\s*:\s*[*!]", body)):
        why.add("ambiguous_yaml")
    if not why:
        return None
    # YAML aliases, escaped delimiters and flow mappings may be defined outside
    # the step. A changed anchor must never reuse the old ambiguous-step waiver.
    scope = full_source if "ambiguous_yaml" in why else str(start) + "\n" + body
    fingerprint = digest(path + "\n" + scope)
    return fingerprint, "+".join(sorted(why))


def risks(sha: str) -> tuple[Counter, int]:
    listing = inventory(sha)
    found: Counter = Counter()
    total = 0
    for path, blob in sorted(listing.items()):
        length = git("cat-file", "-s", blob, cap=50).decode("ascii", "strict").strip()
        if not length.isdecimal() or int(length) > MAX_BLOB:
            raise EvidenceError("workflow blob size exceeds budget")
        total += int(length)
        if total > MAX_TOTAL:
            raise EvidenceError("total workflow blob budget exceeded")
        data = git("cat-file", "blob", blob, cap=MAX_BLOB)
        if len(data) != int(length):
            raise EvidenceError("workflow blob size does not match Git metadata")
        try:
            source = data.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise EvidenceError("invalid UTF-8 workflow") from exc
        if "\x00" in source:
            raise EvidenceError("workflow includes NUL byte")
        lines = source.splitlines(keepends=True)
        visible = structural_lines(lines)
        # Count actual YAML action declarations, not text in comments or JS.
        # Quoted keys and inline flow mappings remain deliberately ambiguous.
        declared_actions = sum(
            bool(ACTION_DECLARED.match(line) or FLOW_USE.search(line)) for line in visible
        )
        parsed_actions = sum(bool(USE.match(line)) for line in visible)
        if declared_actions > parsed_actions:
            found[(path, digest(path + "\n" + source), "ambiguous_yaml")] += 1
        # YAML decoding can reveal an executable action name hidden by
        # backslash-escaped quoted scalars or mapping keys. Treat any newly
        # escaped action identity or mapping key as untrusted source structure.
        escaped_yaml_key_or_uses = any(
            (re.match(r'^\s*(?:-\s*)?uses\s*:', line) or
             line.lstrip().startswith((chr(34), chr(39))))
            and any(chr(92) + kind in line for kind in ("x", "u", "U"))
            and ":" in line
            for line in visible
        )
        if escaped_yaml_key_or_uses:
            found[(path, digest(path + "\n" + source), "ambiguous_yaml")] += 1
        # An alias can hide the action identity itself. If github-script appears
        # anywhere in a workflow, refuse a new unresolved uses alias even when
        # the step action is not spelled out on the uses line.
        if "actions/github-script@" in source.lower() and re.search(r"(?m)^\s*(?:-\s*)?uses\s*:\s*\*", "\n".join(visible)):
            found[(path, digest(path + "\n" + source), "ambiguous_yaml")] += 1
        processed: set[tuple[int, int]] = set()
        for i, line in enumerate(visible):
            if USE.match(line):
                start, end = steps_for_use(lines, i)
                if (start, end) in processed:
                    continue
                processed.add((start, end))
                risk = inspect_step(lines, path, start, end, source)
                if risk:
                    fingerprint, reason = risk
                    found[(path, fingerprint, reason)] += 1
                    if sum(found.values()) > MAX_FINDINGS:
                        raise EvidenceError("finding budget exceeded")
    return found, len(listing)


def evaluate(base: str, head: str) -> dict:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head) or base == head:
        raise EvidenceError("two distinct immutable SHAs are required")
    git("cat-file", "-e", base + "^{commit}", cap=0)
    git("cat-file", "-e", head + "^{commit}", cap=0)
    git("merge-base", "--is-ancestor", base, head, cap=0)
    old, old_count = risks(base)
    new, new_count = risks(head)
    added = new - old
    return {
        "schema": "zcloud-github-script-eval-ratchet-v1",
        "base_sha": base,
        "head_sha": head,
        "baseline_manifests": old_count,
        "candidate_manifests": new_count,
        "baseline_findings": sum(old.values()),
        "candidate_findings": sum(new.values()),
        "new_findings": sum(added.values()),
        "new_reasons": dict(sorted(Counter({k: sum(v for (p, f, r), v in added.items() if r == k) for k in {k[2] for k in added}}).items())),
        "inventory_complete": True,
        "release_authorized": False,
        "production_mutation_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    try:
        report = evaluate(args.base, args.head)
    except EvidenceError as exc:
        print(json.dumps({"schema": "zcloud-github-script-eval-ratchet-v1", "error": str(exc),
                          "inventory_complete": False, "release_authorized": False,
                          "production_mutation_authorized": False}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 1 if report["new_findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
