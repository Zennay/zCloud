#!/usr/bin/env python3
"""Deny newly introduced explicit foreign/dynamic actions/checkout repositories.

Source-only, read-only, no authentication/API access or production authority.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import subprocess
import sys

MAX_MANIFESTS = 4096
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_INVENTORY_BYTES = 16 * 1024 * 1024

SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
CHECKOUT = re.compile(r"\bactions/checkout@", re.I)
STEP = re.compile(r"^(\s*)-\s+(?:[A-Za-z_-]+\s*:|\{)")
USES = re.compile(r"^\s*-?\s*uses\s*:\s*['\"]?actions/checkout@", re.I)
# GitHub Actions supports YAML anchors. An action or whole step imported through
# an alias can resolve to checkout without the word "checkout" on the using line.
# Without evaluating the complete YAML graph, deny *new* indirections.
INDIRECT_USE = re.compile(r"""^\s*-?\s*uses\s*:\s*['"]?(?:\*|\$\{\{)""", re.I)
INDIRECT_STEP = re.compile(r"""^\s*-\s*(?:(?:['"])?\*[A-Za-z_][\w-]*['"]?(?:\s+#.*)?|<<\s*:\s*\*[A-Za-z_][\w-]*)\s*$""")
REPO = re.compile(r"(?:^|[,\s{])['\"]?repository['\"]?\s*:\s*(.*)$", re.I)
SAFE = {"${{ github.repository }}", "zennay/zcloud"}
PATH = re.compile(r"\A(?:\.github/workflows/[^/]+\.ya?ml|(?:[^/]+/)*action\.ya?ml)\Z")


def _stripped(value: str) -> str:
    v = value.strip().rstrip(',').strip()
    if v.endswith('}') and not v.endswith('}}'):
        v = v[:-1].rstrip()
    if v[:1] in "\"'" and v[-1:] == v[:1]:
        v = v[1:-1]
    return " ".join(v.split())


def scan(text: str) -> Counter[str]:
    """Count risky repository overrides on checkout steps and ambiguous syntax.

    The inspection is conservative: syntactically unfamiliar checkout lines
    become risk findings rather than being quietly accepted.
    """
    lines = text.splitlines()
    findings: Counter[str] = Counter()
    for i, line in enumerate(lines):
        if line.lstrip().startswith('#'):
            continue
        if INDIRECT_USE.match(line) or INDIRECT_STEP.match(line):
            findings["indirect-action-identity"] += 1
            continue
        if not CHECKOUT.search(line):
            continue
        if not USES.search(line):
            findings["ambiguous-checkout-syntax"] += 1
            continue
        current_indent = len(line) - len(line.lstrip())
        start = i
        for j in range(i, -1, -1):
            step = STEP.match(lines[j])
            if step and len(step.group(1)) <= current_indent:
                start = j
                break
        step_match = STEP.match(lines[start])
        indent = len(step_match.group(1)) if step_match else current_indent
        end = len(lines)
        for j in range(i + 1, len(lines)):
            step = STEP.match(lines[j])
            if step and len(step.group(1)) <= indent:
                end = j
                break
        values: list[str] = []
        for snippet in lines[i:end]:
            if snippet.lstrip().startswith('#'):
                continue
            if re.match(r'^\s*with\s*:\s*(?:\*|&|\$\{\{)', snippet) or re.match(r'^\s*<<\s*:\s*\*', snippet):
                findings['ambiguous-checkout-input-map'] += 1
            match = REPO.search(snippet)
            if match:
                val = match.group(1).split(' #', 1)[0]
                values.append(_stripped(val))
        for v in values:
            if v.lower() not in SAFE:
                findings["foreign-checkout-repository:" + v[:160]] += 1
        if len(values) > 1:
            findings["multiple-checkout-repositories"] += 1
    return findings


def compare(base: dict[str, str], head: dict[str, str]) -> list[dict[str, object]]:
    """Compare per-path multisets: adding another copy of legacy risk fails."""
    growth = []
    for path in sorted(set(base) | set(head)):
        if not PATH.fullmatch(path):
            continue
        before = scan(base.get(path, ""))
        after = scan(head.get(path, ""))
        for reason, count in sorted(after.items()):
            if count > before[reason]:
                growth.append({"path": path, "reason": reason, "new_count": count - before[reason]})
    return growth


def git(repo: str, *args: str) -> bytes:
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                           check=False, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git source read timed out; refusing partial evidence") from exc
    if p.returncode:
        raise ValueError("Git source read failed; refusing partial evidence")
    return p.stdout


def verify_current_main(repo: str, expected_sha: str) -> None:
    """Require canonical GitHub origin and one exact current-main advertisement."""
    origin = git(repo, "remote", "get-url", "origin").decode("utf-8", errors="strict").strip()
    if origin not in ("https://github.com/Zennay/zCloud", "https://github.com/Zennay/zCloud.git"):
        raise ValueError("Git origin is not the canonical zCloud repository")
    advertised = git(repo, "-c", "http.followRedirects=false", "ls-remote",
                     "--exit-code", "origin", "refs/heads/main")
    lines = advertised.decode("ascii", errors="strict").splitlines()
    if len(lines) != 1:
        raise ValueError("Current main advertisement is missing or ambiguous")
    fields = lines[0].split()
    if (len(fields) != 2 or not SHA.fullmatch(fields[0])
            or fields[1] != "refs/heads/main"):
        raise ValueError("Current main advertisement has invalid identity")
    if fields[0].lower() != expected_sha.lower():
        raise ValueError("Current main differs from approved base SHA")


def tree(repo: str, sha: str) -> dict[str, str]:
    # Read Git object modes in the same immutable tree census as filenames.
    # --name-only hides symlinks (120000), which must never be treated as safe
    # workflow/action manifests based on their plain-text link targets.
    raw = git(repo, "ls-tree", "-r", "-z", sha)
    result = {}
    bytes_total = 0
    for candidate in raw.split(b"\x00"):
        if not candidate:
            continue
        fields, separator, raw_path = candidate.partition(b"\t")
        if not separator:
            raise ValueError("Malformed Git tree record")
        try:
            path = raw_path.decode("utf-8", errors="strict")
        except UnicodeDecodeError as e:
            raise ValueError("Non-UTF8 tree path; refusing incomplete inventory") from e
        if not PATH.fullmatch(path):
            continue
        parts = fields.split()
        if len(parts) != 3:
            raise ValueError("Malformed Git tree metadata")
        mode, kind, object_id = parts
        if (mode not in (b"100644", b"100755") or kind != b"blob"
                or not SHA.fullmatch(object_id.decode("ascii", errors="replace"))):
            raise ValueError("Manifest is not an ordinary immutable Git blob")
        if path in result:
            raise ValueError("Duplicate manifest path in immutable Git tree")
        if len(result) >= MAX_MANIFESTS:
            raise ValueError("Manifest count exceeds bounded inventory")
        oid = object_id.decode("ascii", errors="strict").lower()
        size_text = git(repo, "cat-file", "-s", oid)
        try:
            size = int(size_text.decode("ascii").strip())
        except (ValueError, UnicodeDecodeError) as e:
            raise ValueError("Invalid git blob size; refusing inventory") from e
        if size < 0 or size > MAX_MANIFEST_BYTES:
            raise ValueError("Manifest exceeds single-file size budget")
        bytes_total += size
        if bytes_total > MAX_INVENTORY_BYTES:
            raise ValueError("Manifest inventory exceeds total byte budget")
        content = git(repo, "show", oid)
        if len(content) != size:
            raise ValueError("Git content size mismatch; refusing inventory")
        try:
            result[path] = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as e:
            raise ValueError("Non-UTF8 workflow; refusing incomplete inventory") from e
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", required=True, help="Exact 40-character base commit")
    parser.add_argument("--head", required=True, help="Exact 40-character candidate commit")
    parser.add_argument("--require-current-main", action="store_true",
                        help="Reverify canonical main SHA before and after tree scan")
    args = parser.parse_args(argv)
    try:
        if not SHA.fullmatch(args.base) or not SHA.fullmatch(args.head):
            raise ValueError("Both refs must be immutable 40-character SHAs")
        if args.require_current_main:
            verify_current_main(args.repo, args.base)
        base = tree(args.repo, args.base.lower())
        head = tree(args.repo, args.head.lower())
        findings = compare(base, head)
        if args.require_current_main:
            verify_current_main(args.repo, args.base)
        print(json.dumps({"base": args.base.lower(), "head": args.head.lower(),
                          "workflows_base": len(base), "workflows_head": len(head),
                          "new_findings": findings}, sort_keys=True))
        return 1 if findings else 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "indeterminate", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
