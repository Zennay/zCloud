#!/usr/bin/env python3
"""Non-authorizing, offline no-new-debt check for workflow run-shell expressions.

This checks committed Git trees, never reads working-tree workflow files, and
never launches jobs. It is a conservative syntactic tripwire, NOT a YAML parser,
runner attestation, production/release approval, or full injection analysis.
"""
from __future__ import annotations

import argparse
import hashlib
from collections import Counter
import json
import re
import subprocess

MAX_WORKFLOWS = 512
MAX_BLOB_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_EXPRESSIONS = 8192
SHA = re.compile(r"^[0-9a-f]{40}$")
RUN = re.compile(r"^( *)(?:-\s*)?(?:run|['\"]run['\"])\s*:\s*(.*)$")
RUN_ESCAPED_KEY = re.compile(
    r'^( *)(?:-\s*)?"(?:r|\\u0072|\\x72)(?:u|\\u0075|\\x75)(?:n|\\u006e|\\x6e)"\s*:\s*(.*)$',
    re.I,
)
EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)
UNTRUSTED = re.compile(r"\b(?:github\.event\b|github\.head_ref\b|github\.ref_name\b|github\.ref\b|github\.base_ref\b|inputs\b)", re.I)
ROOT_CONTEXT_JSON = re.compile(r"\btojson\s*\(\s*github\s*\)", re.I)
BRACKET_PROPERTY = re.compile(r"\[\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\1\s*\]")
DYNAMIC_PROPERTY = re.compile(r"\b(?:github|inputs)\s*\[", re.I)
FLOW_RUN_KEY = re.compile(
    r'(?<![A-Za-z0-9_])(?:run|["]run["]|[\x27]run[\x27]|"(?:r|\\u0072|\\x72)(?:u|\\u0075|\\x75)(?:n|\\u006e|\\x6e)")\s*:',
    re.I,
)

EXPLICIT_RUN_KEY = re.compile(r'^\s*(?:-\s*)?\?\s*(?:run|["]run["]|[\x27]run[\x27])\s*(?:#.*)?$')


class EvidenceError(ValueError):
    """The evidence is incomplete, invalid or ambiguous: deny."""


def git(*args: str) -> bytes:
    result = subprocess.run(["git", *args], capture_output=True, timeout=15, check=False)
    if result.returncode:
        raise EvidenceError("git_evidence_unavailable")
    return result.stdout


def tree_files(rev: str) -> dict[str, str]:
    if not SHA.fullmatch(rev):
        raise EvidenceError("invalid_commit_sha")
    entries = git("ls-tree", "-rz", rev, "--", ".github/workflows")
    files: dict[str, str] = {}
    count = 0
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        count += 1
        if count > MAX_WORKFLOWS:
            raise EvidenceError("too_many_tree_entries")
        try:
            meta, raw_path = entry.split(b"\t", 1)
            mode, kind, blob = meta.decode("ascii").split(" ")
            path = raw_path.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise EvidenceError("malformed_git_tree") from exc
        if not path.startswith(".github/workflows/") or "/../" in path or "\x00" in path:
            raise EvidenceError("unsafe_workflow_path")
        if not (path.endswith(".yml") or path.endswith(".yaml")):
            continue
        if mode != "100644" or kind != "blob" or not SHA.fullmatch(blob):
            raise EvidenceError("non_regular_workflow")
        if path in files:
            raise EvidenceError("duplicate_workflow_path")
        files[path] = blob
    if not files:
        raise EvidenceError("empty_workflow_inventory")
    return files


def run_shell_scalars(source: str) -> list[str]:
    """Extract run scalar lines conservatively, not a claim of full YAML parsing."""
    lines = source.splitlines()
    scalars = []
    i = 0
    while i < len(lines):
        match = RUN.match(lines[i]) or RUN_ESCAPED_KEY.match(lines[i])
        if not match:
            i += 1
            continue
        indent = len(match.group(1))
        payload = [match.group(2)]
        i += 1
        while i < len(lines):
            current = lines[i]
            if current.strip() and len(current) - len(current.lstrip(" ")) <= indent:
                break
            payload.append(current)
            i += 1
        scalars.append("\n".join(payload))
    return scalars


def fingerprint_risks(source: str) -> Counter[str]:
    out: Counter[str] = Counter()
    # A run alias can inherit code from an anchor elsewhere in its workflow.
    # Since this is deliberately not a YAML parser, any edit to that workflow
    # must be treated as new ambiguity while such a run alias is present.
    workflow_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    # Flow-style YAML can put the run key after an inline brace, bypassing a
    # newline-oriented scalar extractor. Treat new flow-run forms as ambiguous
    # rather than silently claiming that no dangerous interpolation exists.
    # Digest the entire line so rewriting an existing flow-run also counts.
    for line in source.splitlines():
        # YAML's '? run' mapping splits the key from the value.
        # Conservatively bind the ambiguity to the full workflow blob.
        if EXPLICIT_RUN_KEY.match(line):
            out["ambiguous_explicit_run_key|workflow_sha256:" + workflow_digest] += 1
        match = FLOW_RUN_KEY.search(line)
        # Braces *after* run: can simply be the normal ${{ expression }}
        # delimiters. Only a brace *before* the key indicates flow mapping.
        if match and "{" in line[:match.start()]:
            out["ambiguous_flow_run:" + hashlib.sha256(line.strip().encode("utf-8")).hexdigest()] += 1
    for scalar in run_shell_scalars(source):
        # A previously seen expression is not safe to move from a harmless
        # command to a more dangerous one. Bind findings to their actual run
        # scalar contents, not solely to the expression spelling.
        scalar_digest = hashlib.sha256(scalar.encode("utf-8")).hexdigest()
        value = scalar.lstrip()
        if value.startswith("*") or value.startswith("!") or value.startswith("&"):
            out["ambiguous_yaml_run_scalar|workflow_sha256:" + workflow_digest] += 1
        expressions = EXPR.findall(scalar)
        # A well-formed first expression must not conceal a *second*
        # unfinished interpolation in the same shell scalar.
        if scalar.count("${{") != len(expressions):
            out["incomplete_expression|run_sha256:" + scalar_digest] += 1
        for expr in expressions:
            normalized = " ".join(expr.split()).lower()
            if len(normalized) > 4096:
                raise EvidenceError("expression_too_large")
            # GitHub expressions allow github['event']['issue'] as well as
            # github.event.issue. Both must hit the same trust policy.
            normalized = BRACKET_PROPERTY.sub(lambda m: "." + m.group(2), normalized)
            normalized = re.sub(r"\s*\.\s*", ".", normalized)
            if UNTRUSTED.search(normalized) or ROOT_CONTEXT_JSON.search(normalized):
                out[normalized + "|run_sha256:" + scalar_digest] += 1
            elif DYNAMIC_PROPERTY.search(normalized):
                # Unknown computed key might resolve to event or inputs.
                out["ambiguous_dynamic_property|run_sha256:" + scalar_digest] += 1
    return out


def snapshot(rev: str) -> dict[str, Counter[str]]:
    files = tree_files(rev)
    total_bytes = 0
    found = 0
    result = {}
    for path, blob in sorted(files.items()):
        try:
            size = int(git("cat-file", "-s", blob).strip())
        except ValueError as exc:
            raise EvidenceError("invalid_git_blob_size") from exc
        if size < 0 or size > MAX_BLOB_BYTES:
            raise EvidenceError("workflow_blob_too_large")
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise EvidenceError("workflow_tree_too_large")
        data = git("cat-file", "blob", blob)
        if len(data) != size:
            raise EvidenceError("unstable_blob_size")
        try:
            source = data.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise EvidenceError("invalid_workflow_utf8") from exc
        if "\t" in source:
            raise EvidenceError("ambiguous_workflow_tabs")
        risks = fingerprint_risks(source)
        found += sum(risks.values())
        if found > MAX_EXPRESSIONS:
            raise EvidenceError("too_many_risk_expressions")
        result[path] = risks
    return result


def audit(base: str, head: str) -> dict:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise EvidenceError("invalid_commit_sha")
    if subprocess.run(["git", "merge-base", "--is-ancestor", base, head], capture_output=True, timeout=15).returncode:
        raise EvidenceError("base_not_head_ancestor")
    before = snapshot(base)
    after = snapshot(head)
    newly = {}
    for path, risks in after.items():
        diff = risks - before.get(path, Counter())
        if diff:
            newly[path] = sum(diff.values())
    return {
        "schema": "zcloud-shell-expression-ratchet-v1",
        "base_sha": base,
        "head_sha": head,
        "baseline_risks": sum(sum(x.values()) for x in before.values()),
        "candidate_risks": sum(sum(x.values()) for x in after.values()),
        "new_risks": sum(newly.values()),
        "affected_workflows": sorted(newly),
        "inventory_complete": True,
        "release_authorized": False,
        "deployment_authorized": False,
        "mutation_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Immutable complete 40-hex Git SHA")
    parser.add_argument("--head", required=True, help="Immutable complete 40-hex Git SHA")
    args = parser.parse_args()
    try:
        report = audit(args.base, args.head)
        print(json.dumps(report, sort_keys=True))
        return 1 if report["new_risks"] else 0
    except (EvidenceError, subprocess.TimeoutExpired, OSError) as exc:
        print(json.dumps({"schema": "zcloud-shell-expression-ratchet-v1", "inventory_complete": False,
                          "error": str(exc) if isinstance(exc, EvidenceError) else "git_evidence_unavailable",
                          "release_authorized": False, "deployment_authorized": False,
                          "mutation_performed": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
