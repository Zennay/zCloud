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
IMAGE_REF = re.compile(r"^[A-Za-z0-9_./:-]+@sha256:[0-9a-fA-F]{64}$")
KEY = r"""(?:image|["']image["'])"""
CONTAINER = r"""(?:container|["']container["'])"""
USES = r"""(?:uses|["']uses["'])"""
IMAGE_LINE = re.compile(r"^\s*" + KEY + r"\s*:\s*(.*)$")
CONTAINER_LINE = re.compile(r"^\s*" + CONTAINER + r"\s*:\s*(.*)$")
USES_SCALAR = re.compile(r"^\s*(?:-\s*)?" + USES + r"\s*:\s*(.*)$")
FLOW_IMAGE = re.compile(r"""(?<![\w])(?:image|["']image["'])\s*:""")
FLOW_USES = re.compile(r"""(?<![\w])(?:uses|["']uses["'])\s*:""")
FLOW_CONTAINER = re.compile(r"""(?<![\w])(?:container|["']container["'])\s*:""")
FLOW_DOCKER = re.compile(r"""^\s*-\s*\{.*(?:uses|["']uses["'])\s*:\s*["']?docker://""")
EXPLICIT_IMAGE_KEY = re.compile(r"""^\s*\?\s*(?:image|container|uses|["']image["']|["']container["']|["']uses["'])\s*(?:#.*)?$""")
ESCAPED_DOUBLE_QUOTED_KEY = re.compile(r"""^\s*(?:-\s*)?"(?:\\.|[^"\\])*"\s*:""")
ENCODED_FLOW_DOCKER = re.compile(r"""^\s*-\s*\{[^}]*\\(?:x|u|U)[0-9a-fA-F]+[^}]*docker://""")
ENCODED_FLOW_KEY = re.compile(r"""\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})""")
BLOCK_USES = re.compile(r"""^\s*(?:-\s*)?(?:uses|["']uses["'])\s*:\s*[|>][-+0-9]*\s*(?:#.*)?$""")
MAX_BLOB = 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_FILES = 512
MAX_FINDINGS = 8192
MAX_GIT_TREE_BYTES = 16 * 1024 * 1024


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
    return bool(re.fullmatch(r"(?:.*/)?action\.ya?ml", path))


def _tree(sha: str) -> list[tuple[str, str]]:
    if not SHA.fullmatch(sha):
        raise ValueError("invalid_revision")
    # Scan all committed paths: local actions may live anywhere in a repo.
    raw = _git("ls-tree", "-rz", sha)
    if len(raw) > MAX_GIT_TREE_BYTES:
        raise ValueError("git_tree_listing_over_limit")
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
        # Local Actions can live at any repository depth. A symlink or
        # gitlink anywhere may conceal an executable Action under an arbitrary
        # alias path; the source inventory cannot safely resolve its target.
        if mode in ("120000", "160000"):
            raise ValueError("opaque_git_source_entry")
        if not _selected(path):
            continue
        if mode != "100644" or kind != "blob" or not SHA.fullmatch(blob_sha):
            raise ValueError("unsafe_workflow_git_mode")
        entries.append((path, blob_sha))
    if len(entries) > MAX_FILES:
        raise ValueError("workflow_inventory_over_limit")
    if not any(path.startswith(".github/workflows/") for path, _ in entries):
        raise ValueError("missing_workflow_source_tree")
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
    # Only a complete, balanced YAML scalar can count as an image digest.
    # Aliases, expressions, block strings and malformed quotes are denied.
    if "${{" in value or value.lstrip().startswith(("*", "&", "|", ">")):
        return False
    candidate = value.split("#", 1)[0].strip()
    if candidate.startswith(("'", '"')):
        quote = candidate[0]
        if len(candidate) < 2 or not candidate.endswith(quote):
            return False
        candidate = candidate[1:-1]
    elif any(char in candidate for char in ("'", '"', "\\")):
        return False
    return bool(IMAGE_REF.fullmatch(candidate))


def _scan(path: str, source: str) -> list[Finding]:
    import hashlib

    findings: list[Finding] = []
    # Bind legacy unsafe source to the *entire execution context* in its
    # committed workflow or Action metadata. Any edit to jobs, triggers,
    # permissions, shell, Docker inputs, or an enclosing step is then new debt
    # requiring independent review; benign same-file edits may also deny.
    source_identity = hashlib.sha256(source.encode("utf-8")).digest()
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
        # Fail closed for YAML mapping escape syntax: a quoted key can
        # evaluate to image, container or uses without containing those bytes.
        encoded_key = ESCAPED_DOUBLE_QUOTED_KEY.match(line)
        if encoded_key and "\\" in encoded_key.group(0):
            value, kind = line, "ambiguous_escaped_key"
        elif EXPLICIT_IMAGE_KEY.match(line):
            value, kind = line, "ambiguous_explicit_key"
        elif FLOW_DOCKER.match(line):
            value, kind = line, "ambiguous_flow_docker"
        elif ENCODED_FLOW_DOCKER.match(line):
            value, kind = line, "ambiguous_encoded_flow_docker"
        elif "{" in line and ENCODED_FLOW_KEY.search(line):
            # YAML flow keys can spell "uses"/"container"/"image" with
            # escaped quoted characters.  The decoded key is opaque here.
            value, kind = line, "ambiguous_encoded_flow_key"
        elif BLOCK_USES.match(line):
            value, kind = line, "ambiguous_uses_block"
        match = IMAGE_LINE.match(line) if value is None else None
        if match:
            value, kind = match.group(1), "image"
        else:
            match = USES_SCALAR.match(line) if value is None else None
            if match:
                scalar = match.group(1).strip()
                # Inspect the complete scalar, never just a digest prefix.
                if scalar.lstrip("'\"").startswith("docker://"):
                    value, kind = scalar.replace("docker://", "", 1), "docker_uses"
                elif not scalar or scalar.startswith(("*", "&", "!", "[", "{", "${{")):
                    value, kind = line, "ambiguous_uses_reference"
            if value is None:
                match = CONTAINER_LINE.match(line)
                if match and match.group(1).strip():
                    value, kind = match.group(1), "inline_container"
                elif value is None and "{" in line:
                    # A YAML flow map can contain a step or complete job on
                    # one line; block-only matchers cannot see those keys.
                    # Treat source as opaque instead of accepting tag drift.
                    if FLOW_USES.search(line):
                        value, kind = line, "ambiguous_flow_uses"
                    elif FLOW_CONTAINER.search(line):
                        value, kind = line, "ambiguous_flow_container"
                    elif FLOW_IMAGE.search(line):
                        value, kind = line, "ambiguous_flow_image"
        if value is None or kind is None:
            continue
        # An unpinned image, even if introduced by an opaque YAML flow map,
        # cannot silently become new source debt.
        if not kind.startswith("ambiguous_") and "{" not in value and "[" not in value and _safe_literal(value):
            continue
        fingerprint = hashlib.sha256(source_identity + line.encode("utf-8")).hexdigest()
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


def dutch_operator_summary(report: dict[str, object], status: int) -> str:
    """Produce bounded, non-secret, non-authorizing source evidence for CI.

    Unknown/malformed results raise rather than displaying an optimistic green.
    No image names, YAML text, paths, branch names or error strings are emitted.
    """
    if not isinstance(report, dict) or report.get("policy") != "container-image-digest-no-new-debt-v1":
        raise ValueError("invalid_report_policy")
    if any(report.get(flag) is not False for flag in (
        "release_authorized", "deployment_authorized", "mutation_performed"
    )):
        raise ValueError("source_report_claims_authority")
    if status == 2:
        if report.get("inventory_complete") is not False:
            raise ValueError("invalid_incomplete_evidence")
        return (
            "### Containerimages — bronbewijs incompleet\n\n"
            "**Afgewezen:** Git-objecten of broninformatie ontbreken of zijn onveilig.\n\n"
            "Dit geeft **geen** toestemming voor deployment, runnerherstel of wijzigingen.\n"
        )
    if status not in (0, 1) or report.get("inventory_complete") is not True:
        raise ValueError("inconsistent_source_report")
    counts = []
    for key in ("baseline_findings", "candidate_findings", "new_findings"):
        count = report.get(key)
        if type(count) is not int or not 0 <= count <= MAX_FINDINGS:
            raise ValueError("invalid_finding_count")
        counts.append(count)
    baseline, candidate, added = counts
    # Even the user-facing summary must reject internally inconsistent
    # evidence rather than turn a truncated or forged report into green.
    exposed = report.get("new_risks")
    if added > candidate or type(exposed) is not list or len(exposed) != min(added, 25):
        raise ValueError("invalid_risk_inventory_consistency")
    if (status == 0) != (added == 0):
        raise ValueError("status_finding_mismatch")
    verdict = "Geen nieuwe risico's gevonden" if status == 0 else "Afgewezen: nieuwe risico's gevonden"
    return (
        f"### Containerimages — {verdict}\n\n"
        "| Broncontrole | Aantal |\n|---|---:|\n"
        f"| Bestaande aandachtspunten | {baseline} |\n"
        f"| Kandidaat-aandachtspunten | {candidate} |\n"
        f"| Nieuwe risico's | {added} |\n\n"
        "**Alleen GitHub-broncontrole.** Geen VPS-herstel, productie-deploy of vrijgave.\n"
    )


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
