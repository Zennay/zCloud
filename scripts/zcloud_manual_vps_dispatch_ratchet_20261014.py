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
import os
import selectors
import time
import re
import subprocess
from pathlib import Path

ROOT = ".github/workflows/"
MAX_BYTES = 131072
MAX_WORKFLOWS = 4096
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_GIT_STDOUT_BYTES = 8 * 1024 * 1024
HEX_SHA = re.compile(r"^[0-9a-f]{40}$")
FILENAME = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")
# A runner with a "ubuntu-N" looking label is not necessarily GitHub-hosted.
# Keep the exemption to known exact GitHub-hosted image labels; unknown labels
# are conservatively considered able to select a private runner.
HOSTED_LABELS = frozenset({
    "ubuntu-latest", "ubuntu-22.04", "ubuntu-24.04", "ubuntu-24.04-arm",
    "windows-latest", "windows-2022", "windows-2025",
    "macos-latest", "macos-13", "macos-14", "macos-15",
    "macos-15-intel", "macos-26", "macos-26-intel",
})
JOB = re.compile(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$", re.M)
TRUST = (
    r"github\.event_name\s*==\s*['\"]workflow_dispatch['\"]",
    r"github\.repository\s*==\s*['\"]Zennay/zCloud['\"]",
    r"github\.ref\s*==\s*['\"]refs/heads/main['\"]",
    r"github\.actor\s*==\s*['\"]Zennay['\"]",
    r"github\.triggering_actor\s*==\s*['\"]Zennay['\"]",
    r"github\.run_attempt\s*==\s*1\b",
)

class RefError(Exception):
    pass

def git(root: Path, *args: str) -> bytes:
    """Read bounded Git output; hostile PR trees must not exhaust CI memory."""
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        deadline = time.monotonic() + 20
        chunks: list[bytes] = []
        size = 0
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not ready.select(remaining):
                    raise RefError("git_read_timeout")
                chunk = os.read(process.stdout.fileno(), min(65536, MAX_GIT_STDOUT_BYTES - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_GIT_STDOUT_BYTES:
                    raise RefError("git_output_size_limit")
                chunks.append(chunk)
        remaining = max(0.001, deadline - time.monotonic())
        if process.wait(timeout=remaining) != 0:
            raise RefError("unreadable_git_object")
        return b"".join(chunks)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RefError("unreadable_git_object") from exc
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()

def manifest(root: Path, commit: str) -> dict[str, bytes]:
    if not HEX_SHA.fullmatch(commit):
        raise RefError("ref_must_be_immutable_sha")
    resolved = git(root, "rev-parse", "--verify", commit + "^{commit}").decode().strip()
    if resolved != commit:
        raise RefError("not_exact_commit")
    raw = git(root, "ls-tree", "-rz", commit, "--", ROOT)
    out: dict[str, bytes] = {}
    accumulated_bytes = 0
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
        if len(out) >= MAX_WORKFLOWS:
            raise RefError("workflow_count_limit")
        # Reject oversized untrusted blobs BEFORE reading their bytes into
        # Python. cat-file blob followed by a length check could allocate an
        # attacker-controlled multi-GB file from a PR's Git object tree.
        try:
            declared_size = int(git(root, "cat-file", "-s", oid).strip())
        except ValueError as exc:
            raise RefError("invalid_workflow_blob_size") from exc
        if declared_size < 0 or declared_size > MAX_BYTES:
            raise RefError("oversized_workflow")
        if accumulated_bytes + declared_size > MAX_TOTAL_BYTES:
            raise RefError("aggregate_workflow_bytes_limit")
        body = git(root, "cat-file", "blob", oid)
        if len(body) != declared_size:
            raise RefError("workflow_blob_size_mismatch")
        accumulated_bytes += declared_size
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
    next_top = re.search(r"(?m)^(?!#)\S", text)
    if next_top:
        text = text[:next_top.start()]
    matches = list(JOB.finditer(text))
    # Quoted, space-before-colon, inline-map and duplicate job identifiers are
    # valid YAML variants but cannot be safely inspected by this bounded parser.
    # Reject them rather than silently letting an uninspected VPS job through.
    for line in text.splitlines():
        if re.match(r"^  \S", line) and not line.lstrip().startswith("#"):
            if not JOB.fullmatch(line):
                raise RefError("unsupported_job_identifier")
    names = [m.group(1) for m in matches]
    if len(names) != len(set(names)):
        raise RefError("duplicate_job_identifier")
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
    # A trailing YAML comment does not change a block-form event mapping.
    # Treat "on: # explanation" exactly like "on:"; otherwise a manual VPS
    # workflow can disappear from the audit with a single innocent comment.
    if rest.lstrip().startswith("#"):
        rest = ""
    if rest.strip():
        # Inline YAML comments are not event names. Ignore comment-only
        # references to workflow_dispatch, without ignoring incomplete lists.
        event_scalar = rest.split(" #", 1)[0].strip()
        # Folded/literal/tagged YAML or a multiline flow list may resolve a
        # workflow_dispatch event *after* the first line. Never silently
        # classify an incomplete flow list as non-manual and skip its jobs.
        if any(ch in rest for ch in "{}&*") or rest.lstrip().startswith((">", "|", "!")):
            raise RefError("ambiguous_event_syntax")
        if rest.lstrip().startswith("["):
            if (not event_scalar.endswith("]") or event_scalar.count("[") != 1
                    or event_scalar.count("]") != 1):
                raise RefError("ambiguous_event_syntax")
        return bool(re.search(r"\bworkflow_dispatch\b", event_scalar))
    tail = source[event.end():]
    end = re.search(r"(?m)^(?!#)\S", tail)
    block = tail[:end.start()] if end else tail
    # Glob wildcards in paths: are valid event filters, not YAML aliases.
    if re.search(r"(?m)^[ \t]*(?:<<:|[A-Za-z_-]+:[ \t]*[&*]|-[ \t]*[&*])", block):
        raise RefError("ambiguous_event_syntax")
    # GitHub Actions accepts both block maps and block sequences, with either
    # quoted or bare event names and any legal YAML indentation width.
    map_event = r"""(?m)^[ \t]+(?:workflow_dispatch|['"]workflow_dispatch['"])[ \t]*:"""
    list_event = r"""(?m)^[ \t]+-[ \t]+(?:workflow_dispatch|['"]workflow_dispatch['"])[ \t]*(?:#.*)?$"""
    return bool(re.search(map_event, block) or re.search(list_event, block))

def risky_manual_jobs(path: str, body: bytes) -> set[tuple[str, str]]:
    source = body.decode("utf-8")
    jobs = split_jobs(source)
    # Duplicate top-level keys are YAML-loader dependent and can replace an
    # apparently safe event or job with a dangerous second definition.
    if (len(re.findall(r"(?m)^on:[ \t]*", source)) != 1 or
            len(re.findall(r"(?m)^jobs:[ \t]*", source)) != 1):
        raise RefError("duplicate_or_missing_authority_keys")
    # Detect unsupported constructs that could hide runner/jobs/event authority.
    if (re.search(r"(?m)^['\"]?(?:jobs|on)['\"]?:\s*[&*{]", source)
            or re.search(r"(?m)^\s*<<:\s*\*", source)):
        raise RefError("ambiguous_workflow_structure")
    if not manual_event(source):
        return set()
    # Manual YAML with unsupported/quoted/inline jobs must not be treated as
    # "no risky jobs". An absent valid jobs mapping is an unknown authority
    # boundary, not evidence that no self-hosted work can run.
    if not jobs:
        raise RefError("missing_or_ambiguous_jobs")
    findings: set[tuple[str, str]] = set()
    for job, block in jobs.items():
        # Alternate YAML spellings of job authority keys are real mappings,
        # but our strict reader would otherwise skip them (e.g. runs-on : or
        # "runs-on":). Detect before checking whether a runner is hosted.
        for field in re.finditer(
            r"""(?m)^(?P<indent>[ \t]+)(?P<key>['"]?(?:runs-on|uses|if)['"]?)[ \t]*:""",
            block,
        ):
            # Step-level "uses:" has deeper indentation and is not a
            # job-level reusable-workflow key. Inspect job keys even when
            # they occur AFTER a steps block: YAML mapping order is arbitrary.
            if "\t" in field.group("indent"):
                raise RefError("unsupported_job_authority_syntax")
            if len(field.group("indent")) >= 8:
                continue
            literal = field.group(0)
            expected = "    " + field.group("key").strip("'\\\"") + ":"
            if literal != expected:
                raise RefError("unsupported_job_authority_syntax")
        # YAML permits repeated mapping keys; some parsers use the first value,
        # others the last. Duplicate job-local runner/if/uses keys must never
        # launder a risky manual VPS step through a benign earlier field.
        for critical in ("runs-on", "if", "uses", "permissions"):
            pattern = rf"(?m)^    {critical}[ \t]*:"
            if len(re.findall(pattern, block)) > 1:
                raise RefError("duplicate_job_authority_key")
        runner = re.search(r"(?m)^    runs-on:\s*(.*)$", block)
        reuse = re.search(r"(?m)^    uses:\s*(.*)$", block)
        if not runner and not reuse:
            raise RefError("missing_or_ambiguous_job_runner")
        selection = runner.group(1) if runner else reuse.group(1)
        # Dynamic runner selection and reusable jobs require independent proof.
        # A custom label such as "vps" or "ftmo-research" can select an
        # actual self-hosted runner without spelling "self-hosted".
        hosted = bool(runner and
                      selection.split(" #", 1)[0].strip().strip("'\\\"") in HOSTED_LABELS)
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
        # Exact positive conjunctions only. Merely mentioning the six trusted
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

def workflow_authority_header(source: str) -> tuple[str, ...]:
    """Track global permissions, triggers and other authority of unsafe jobs.

    Workflow display names and YAML comments cannot change job authority.
    Other global YAML edits can: they must not launder an unchanged unsafe
    job through the no-new-debt grandfathering rule.
    """
    root = re.search(r"(?m)^jobs:[ \t]*", source)
    if root is None:
        raise RefError("missing_or_ambiguous_jobs")
    header = source[:root.start()]
    return tuple(
        line.rstrip() for line in header.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
        and not (
            re.match(r"^(?:name|run-name):[ \t]", line)
            and not re.search(r"(?<![A-Za-z0-9_])[&*][A-Za-z_][A-Za-z0-9_-]*", line)
        )
    )


def evaluate(base: dict[str, bytes], head: dict[str, bytes]) -> dict[str, object]:
    previous: set[tuple[str, str]] = set()
    candidate: set[tuple[str, str]] = set()
    # Isolate new exposure to modified/additive workflows. Legacy ambiguous
    # YAML outside this PR must not prevent independent safe changes.
    modified = {p for p in head if base.get(p) != head[p]}
    ambiguous_base_count = 0
    for path in sorted(modified):
        if path in base:
            try:
                previous |= risky_manual_jobs(path, base[path])
            except RefError:
                # An older ambiguous workflow must be repairable without a
                # blanket baseline waiver. Any unguarded candidate job still
                # incurs NEW debt, while a fully parseable guarded replacement
                # is safe to admit for review.
                ambiguous_base_count += 1
        candidate |= risky_manual_jobs(path, head[path])
    # Grandfather only the identical unsafe job identity. A modified job with
    # unchanged debt remains visible as debt, not as evidence of safety.
    # An old unguarded job must not be allowed to acquire NEW authority while
    # keeping its original (path, job) identity. Require owner review whenever
    # its actual job block changes and the resulting job remains unguarded.
    changed_old_debt: set[tuple[str, str]] = set()
    for path in modified:
        if path not in base:
            continue
        # Only compare legacy authority when there is actually an unsafe
        # identity shared by both snapshots. A malformed historical workflow
        # can otherwise not be repaired merely because its header is invalid.
        shared = sorted(key for key in previous & candidate if key[0] == path)
        if not shared:
            continue
        try:
            before_source = base[path].decode("utf-8")
            after_source = head[path].decode("utf-8")
            before_jobs = split_jobs(before_source)
            after_jobs = split_jobs(after_source)
            authority_changed = (
                workflow_authority_header(before_source)
                != workflow_authority_header(after_source)
            )
        except UnicodeError as exc:
            raise RefError("invalid_workflow_encoding") from exc
        for key in shared:
            if (before_jobs.get(key[1]) != after_jobs.get(key[1])
                    or authority_changed):
                changed_old_debt.add(key)
    additions = sorted((candidate - previous) | changed_old_debt)
    return {
        "policy": "manual-vps-dispatch-no-new-debt-v1",
        "ok": not additions,
        "legacy_debt_count": len(previous),
        "candidate_debt_count": len(candidate),
        "ambiguous_touched_baseline_count": ambiguous_base_count,
        "inventory_scope": "changed_workflows_only",
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
