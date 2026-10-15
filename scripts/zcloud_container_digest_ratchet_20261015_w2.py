#!/usr/bin/env python3
"""Fail-closed, source-only no-new-debt guard for GitHub Actions container images.

Syntactic inventory, not a YAML execution proof or production-release approval.
Uses immutable Git objects; no network, file writes, Docker or VPS access.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass

SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"@sha256:[0-9a-fA-F]{64}(?=$|[\s#}])")
KEY = r"""(?:image|["']image["'])"""
CONTAINER = r"""(?:container|["']container["'])"""
USES = r"""(?:uses|["']uses["'])"""
IMAGE_LINE = re.compile(r"^\s*" + KEY + r"\s*:\s*(.*)$")
CONTAINER_LINE = re.compile(r"^\s*" + CONTAINER + r"\s*:\s*(.*)$")
DOCKER_USE = re.compile(r"^\s*(?:-\s*)?" + USE + r"\s*:\s*['\"]?docker://([^\s'\"#]+)")
FLOW_IMAGE = re.compile(r"""(?<![\w])(?:image|["']image["'])\s*:""")
MAX_BLOB = 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_FILES = 512
MAX_FINDINGS = 8192


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    # Bind legacy exceptions to the actual scalar content, without emitting it.
    fingerprint: str

    def key(self) -> tuple[str, str, str]:
        return self.path, self.kind, self.fingerprint

    def public(self) -> dict[str, object]:
        return {"path": self.path, "line": self.line, "kind": self.kind}


def _git(*args: str) -> bytes:
    done = subprocess.run(
        ["git", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=30,
    )
    if done.returncode:
        raise ValueError("incomplete_git_source")
    return done.stdout


def _selected(path: str) -> bool:
    if re.fullmatch(r"\.github/workflows/[^/]+\.ya?ml", path):
        return True
    return bool(re.fullmatch(r"(?:\.github/actions/|.+/)(?:action\.ya?ml)", path))


def _tree(sha: str) -> list[tuple[str, str]]:
    if not SHA.fullmatch(sha):
        raise ValueError("invalid_revision")
    raw = _git("ls-tree", "-rz", sha, "--", ".github/workflows", ".github/actions")
    entries: list[tuple[str, str]] = []
    for item in raw.split(b"\x00"):
        if not item:
            continue
        try:
            header, path_bytes = item.split(b"\t", 1)
            mode, kind, blob_sha = header.decode("ascii").split()
            path = path_bytes.decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("invalid_git_tree") from exc
        if not _selected(path):
            continue
        if mode != "100644" or kind != "blob" or not SHA.fullmatch(blob_sha):
            raise ValueError("unsafe_workflow_git_mode")
        entries.append((path, blob_sha))
    if len(entries) > MAX_FILES:
        raise ValueError("workflow_inventory_over_limit")
    return sorted(entries)


def _read(sha: str) -> str:
    size_raw = _git("cat-file", "-s", sha)
    try:
        size = int(size_raw.strip())
    except ValueError as exc:
        raise ValueError("bad_git_blob_size") from exc
    if size < 0 or size > MAX_BLOB:
        raise ValueError("workflow_blob_over_limit")
    blob = _git("cat-file", "blob", sha)
    if len(blob) != size:
        raise ValueError("inconsistent_git_blob_size")
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid_workflow_utf8") from exc


def _safe_literal(value: str) -> bool:
    # Dynamic expressions, YAML alias/merge and multi-line strings require
    # independent review even if they contain a literal digest substring.
    if "${{" in value or value.lstrip().startswith(("*", "&", "|", ">")):
        return False
    candidate = value.strip().strip("'\"")
    return bool(DIGEST.search(candidate))


def _scan(path: str, source: str) -> list[Finding]:
    import hashlib

    findings: list[Finding] = []
    multiline_indent: int | None = None
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if not stripped or stripped.startswith("#"):
            continue
        if multiline_indent is not None:
            if indent > multiline_indent:
                continue
            multiline_indent = None
        # Do not accidentally interpret text inside a shell run block as YAML.
        if re.match(r"^(?:-\s*)?(?:run|['\"]run['\"])\s*:\s*[|>]", stripped):
            multiline_indent = indent
            continue
        value: str | None = None
        kind: str | None = None
        match = IMAGE_LINE.match(line)
        if match:
            value, kind = match.group(1), "image"
        else:
            match = DOCKER_USE.match(line)
            if match:
                value, kind = match.group(1), "docker_uses"
            else:
                match = CONTAINER_LINE.match(line)
                if match and match.group(1).strip():
                    value, kind = match.group(1), "inline_container"
                elif "{" in line and FLOW_IMAGE.search(line):
                    value, kind = line, "ambiguous_flow_image"
        if value is None or kind is None:
            continue
        # An unpinned image, even if introduced by an opaque YAML flow map,
        # cannot silently become new source debt.
        if kind != "ambiguous_flow_image" and _safe_literal(value):
            continue
        fingerprint = hashlib.sha256(line.encode("utf-8")).hexdigest()
        findings.append(Finding(path, number, kind, fingerprint))
        if len(findings) > MAX_FINDINGS:
            raise ValueError("risk_inventory_over_limit")
    return findings


def inventory(sha: str) -> list[Finding]:
    findings: list[Finding] = []
    total = 0
    for path, blob_sha in _tree(sha):
        source = _read(blob_sha)
        total += len(source.encode("utf-8"))
        if total > MAX_TOTAL:
            raise ValueError("workflow_inventory_bytes_over_limit")
        findings.extend(_scan(path, source))
        if len(findings) > MAX_FINDINGS:
            raise ValueError("risk_inventory_over_limit")
    return findings


def compare(base: list[Finding], candidate: list[Finding]) -> dict[str, object]:
    from collections import Counter

    prior = Counter(item.key() for item in base)
    added: list[dict[str, object]] = []
    for item in candidate:
        key = item.key()
        if prior[key]:
            prior[key] -= 1
        else:
            added.append(item.public())
    return {
        "policy": "container-image-digest-no-new-debt-v1",
        "baseline_findings": len(base),
        "candidate_findings": len(candidate),
        "new_findings": len(added),
        "new_risks": added[:25],
        "inventory_complete": True,
        "release_authorized": False,
        "deployment_authorized": False,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-sha", required=True)
    p.add_argument("--head-sha", required=True)
    args = p.parse_args(argv)
    try:
        if not SHA.fullmatch(args.base_sha) or not SHA.fullmatch(args.head_sha):
            raise ValueError("invalid_revision")
        data = compare(inventory(args.base_sha), inventory(args.head_sha))
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({
            "policy": "container-image-digest-no-new-debt-v1",
            "inventory_complete": False,
            "error": str(exc)[:64],
            "release_authorized": False,
            "deployment_authorized": False,
            "mutation_performed": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(data, sort_keys=True, separators=(",", ":")))
    return 1 if data["new_findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
