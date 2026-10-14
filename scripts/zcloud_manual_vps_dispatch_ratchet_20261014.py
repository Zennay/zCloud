#!/usr/bin/env python3
"""Fail-closed source-only ratchet for NEW manual self-hosted Actions jobs.

A manual workflow is not necessarily safe: anyone with workflow dispatch rights
could choose a branch or re-run it. This audit checks the job-level trust
boundary; it does not call the GitHub API, dispatch jobs or authorize deployment.
Existing unsafe job identities are tracked as debt, not silently repaired.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = ".github/workflows/"
MAX_BYTES = 131072
HEX_SHA = re.compile(r"^[0-9a-f]{40}$")
FILENAME = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")
JOB = re.compile(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$", re.M)
TRUST = (
    r"github\.repository\s*==\s*['\"]Zennay/zCloud['\"]",
    r"github\.ref\s*==\s*['\"]refs/heads/main['\"]",
    r"github\.actor\s*==\s*['\"]Zennay['\"]",
    r"github\.triggering_actor\s*==\s*['\"]Zennay['\"]",
    r"github\.run_attempt\s*==\s*1\b",
)

class RefError(Exception):
    pass

def git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=20,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RefError("unreadable_git_object") from exc

def manifest(root: Path, commit: str) -> dict[str, bytes]:
    if not HEX_SHA.fullmatch(commit):
        raise RefError("ref_must_be_immutable_sha")
    resolved = git(root, "rev-parse", "--verify", commit + "^{commit}").decode().strip()
    if resolved != commit:
        raise RefError("not_exact_commit")
    raw = git(root, "ls-tree", "-rz", commit, "--", ROOT)
    out: dict[str, bytes] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        metadata, sep, path_bytes = entry.partition(b"\t")
        if not sep:
            raise RefError("invalid_tree_entry")
        try:
            mode, typ, oid = metadata.decode("ascii").split()
            path = path_bytes.decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise RefError("invalid_tree_encoding") from exc
        if not path.startswith(ROOT):
            raise RefError("unexpected_tree_scope")
        short = path[len(ROOT):]
        if "/" in short or not FILENAME.fullmatch(short):
            # Do not omit YAML files hidden below unexpected folders.
            if short.lower().endswith((".yml", ".yaml")):
                raise RefError("unsupported_workflow_path")
            continue
        if mode != "100644" or typ != "blob":
            raise RefError("non_regular_workflow")
        body = git(root, "cat-file", "blob", oid)
        if len(body) > MAX_BYTES:
            raise RefError("oversized_workflow")
        try:
            out[path] = body.decode("utf-8").encode("utf-8")
        except UnicodeError as exc:
            raise RefError("non_utf8_workflow") from exc
    return out

def split_jobs(source: str) -> dict[str, str]:
    jobs = re.search(r"(?m)^jobs:[ \t]*(?:#.*)?$", source)
    if jobs is None:
        return {}
    text = source[jobs.end():]
    next_top = re.search(r"(?m)^\S", text)
    if next_top:
        text = text[:next_top.start()]
    matches = list(JOB.finditer(text))
    return {m.group(1): text[m.end():(matches[i + 1].start() if i + 1 < len(matches) else len(text))]
            for i, m in enumerate(matches)}

def manual_event(source: str) -> bool:
    # Reject aliases/anchors in the event section rather than guessing which
    # event actually fires (YAML merging and flow maps change semantics).
    source = re.sub(r"(?m)^\s*#.*$", "", source)
    event = re.search(r"(?m)^on:[ \t]*(.*)$", source)
    if not event:
        if re.search(r"(?m)^['\"]on['\"]\s*:", source):
            raise RefError("ambiguous_event_syntax")
        return False
    rest = event.group(1)
    if rest.strip():
        if any(ch in rest for ch in "{}&*"):
            raise RefError("ambiguous_event_syntax")
        return bool(re.search(r"\bworkflow_dispatch\b", rest))
    tail = source[event.end():]
    end = re.search(r"(?m)^\S", tail)
    block = tail[:end.start()] if end else tail
    # Glob wildcards in paths: are valid event filters, not YAML aliases.
    if re.search(r"(?m)^[ \t]*(?:<<:|[A-Za-z_-]+:[ \t]*[&*]|-[ \t]*[&*])", block):
        raise RefError("ambiguous_event_syntax")
    return bool(re.search(r"(?m)^  workflow_dispatch\s*:", block)) or bool(
        re.search(r"\bworkflow_dispatch\b", block) and block.lstrip().startswith("[")
    )

def risky_manual_jobs(path: str, body: bytes) -> set[tuple[str, str]]:
    source = body.decode("utf-8")
    jobs = split_jobs(source)
    # Duplicate top-level keys are YAML-loader dependent and can replace an
    # apparently safe event or job with a dangerous second definition.
    if (len(re.findall(r"(?m)^on:[ \\t]*", source)) != 1 or
            len(re.findall(r"(?m)^jobs:[ \\t]*", source)) != 1):
        raise RefError("duplicate_or_missing_authority_keys")
    # Detect unsupported constructs that could hide runner/jobs/event authority.
    if (re.search(r"(?m)^['\"]?(?:jobs|on)['\"]?:\s*[&*{]", source)
            or re.search(r"(?m)^\s*<<:\s*\*", source)):
        raise RefError("ambiguous_workflow_structure")
    if not manual_event(source):
        return set()
    findings: set[tuple[str, str]] = set()
    for job, block in jobs.items():
        runner = re.search(r"(?m)^    runs-on:\s*(.*)$", block)
        reuse = re.search(r"(?m)^    uses:\s*(.*)$", block)
        if not runner and not reuse:
            continue
        selection = runner.group(1) if runner else reuse.group(1)
        # Dynamic runner selection and reusable jobs require independent proof.
        # A custom label such as "vps" or "ftmo-research" can select an
        # actual self-hosted runner without spelling "self-hosted".
        hosted = bool(runner and re.fullmatch(
            r"(?:ubuntu|windows|macos)-(?:latest|[0-9][A-Za-z0-9._-]*)",
            selection.strip().strip("'\\\""),
        ))
        potentially_vps = bool(reuse) or bool(runner and not hosted)
        if not potentially_vps:
            continue
        # Enforce actual job-level if, not shell/source comments or step-level if.
        admission = re.search(r"(?m)^    if:[ \t]*(.*(?:\n {6,}.+)*)", block)
        value = admission.group(1) if admission else ""
        value = "\n".join(line.split(" #", 1)[0] for line in value.splitlines()).strip()
        if value.startswith((">-", ">", "|-", "|")):
            value = value.split("\n", 1)[-1].strip()
        if value.startswith("$" + "{{") and value.endswith("}}"):
            value = value[3:-2].strip()
        # Exact positive conjunctions only. Merely mentioning the five trusted
        # strings inside a literal, function or OR clause is not authorization.
        clauses = [part.strip() for part in value.split("&&")]
        allowed = [any(re.fullmatch(rule, part) for rule in TRUST) for part in clauses]
        complete = all(any(re.fullmatch(rule, part) for part in clauses) for rule in TRUST)
        if not complete or not all(allowed):
            findings.add((path, job))
    # An apparently manual workflow with unparseable/missing jobs must not pass.
    if not jobs and re.search(r"\b(?:runs-on|uses)\s*:", source):
        raise RefError("ambiguous_job_structure")
    return findings

def evaluate(base: dict[str, bytes], head: dict[str, bytes]) -> dict[str, object]:
    previous: set[tuple[str, str]] = set()
    candidate: set[tuple[str, str]] = set()
    # Isolate new exposure to modified/additive workflows. Legacy ambiguous
    # YAML outside this PR must not prevent independent safe changes.
    modified = {p for p in head if base.get(p) != head[p]}
    for path in sorted(modified):
        if path in base:
            previous |= risky_manual_jobs(path, base[path])
        candidate |= risky_manual_jobs(path, head[path])
    # Grandfather only the identical unsafe job identity. A modified job with
    # unchanged debt remains visible as debt, not as evidence of safety.
    additions = sorted(candidate - previous)
    return {
        "policy": "manual-vps-dispatch-no-new-debt-v1",
        "ok": not additions,
        "legacy_debt_count": len(previous),
        "candidate_debt_count": len(candidate),
        "new_debt_count": len(additions),
        "new_debt": [{"workflow": path, "job": job} for path, job in additions[:25]],
        "evidence_truncated": len(additions) > 25,
        "mutation_performed": False,
        "production_mutation_authorized": False,
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args()
    try:
        output = evaluate(manifest(args.repo_root, args.base_sha),
                          manifest(args.repo_root, args.head_sha))
    except RefError as exc:
        output = {"ok": False, "reason": str(exc), "mutation_performed": False,
                  "production_mutation_authorized": False}
    print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return 0 if output["ok"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
