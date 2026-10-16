#!/usr/bin/env python3
"""Source-only ratchet: deny newly unguarded permanent zCloud VPS runner jobs (#633).

Historical findings are grandfathered per workflow/job ID, not marked resolved.
No hosted result from this tool may authorize a deployment or runner restart.
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys

SHA = re.compile(r"[0-9a-f]{40}\Z")
PATH = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
# Explicit GitHub-hosted label families only. Prefix matching mistakenly
# classifies custom self-hosted names such as ubuntu-vps as GitHub-hosted.
# Unknown/future labels fail closed until verified and allowlisted.
HOSTED = re.compile(
    r"(?:ubuntu-(?:latest|(?:20|22|24|26)\.04(?:-arm)?)"
    r"|windows-(?:latest|2019|2022|2025)"
    r"|macos-(?:latest|(?:13|14|15|26)(?:-large|-intel)?))\Z"
)
JOB = re.compile(r"^  ([A-Za-z_][A-Za-z0-9_-]*):(?:\s*(?:#.*)?)?$")
CHECKOUT = re.compile(r"actions/checkout@[0-9a-f]{40}(?:\s*(?:#.*)?)?$")
GUARD = re.compile(r"python3\s+(?:\./)?scripts/zcloud_vps_runner_guard\.py(?:\s+--json)?\s*(?:#.*)?$")
# Pre-shell hooks and Python module/path shadowing can run attacker-supplied
# code before the first guard command even if that command is visually first.
SHELL_HIJACK = re.compile(
    r"""(?m)^\s+["']?(?:BASH_ENV|PYTHONPATH|PYTHONHOME|PATH)["']?\s*:"""
)
INLINE_HIJACK = re.compile(
    r"""(?m)^\s*env:\s*\{[^\n}]*(?:BASH_ENV|PYTHONPATH|PYTHONHOME|PATH)\s*:"""
)


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], timeout=20, stderr=subprocess.DEVNULL)


def inventory(base: str, head: str) -> list[str]:
    for sha in (base, head):
        if not SHA.fullmatch(sha) or git("cat-file", "-t", sha).strip() != b"commit":
            raise ValueError("not_immutable_commit")
    if subprocess.call(["git", "merge-base", "--is-ancestor", base, head],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20):
        raise ValueError("base_not_ancestor")
    paths = [p.decode("utf-8") for p in
             git("diff", "--name-only", "-z", "--diff-filter=ACMRT",
                 base, head, "--", ".github/workflows").split(b"\0") if p]
    if len(paths) > 256 or len(paths) != len(set(paths)) or any(not PATH.fullmatch(p) for p in paths):
        raise ValueError("invalid_diff_inventory")
    return paths


def source(sha: str, path: str) -> str | None:
    if not PATH.fullmatch(path):
        raise ValueError("invalid_source_path")
    try:
        obj = git("ls-tree", sha, "--", path).decode("ascii").strip()
        if not obj:
            return None
        metadata, treepath = obj.split("\t", 1)
        mode, kind, blob = metadata.split(" ")
        if treepath != path or mode not in {"100644", "100755"} or kind != "blob" or not SHA.fullmatch(blob):
            raise ValueError("unsafe_source_object")
        data = git("cat-file", "blob", blob)
        if len(data) > 256_000 or b"\0" in data:
            raise ValueError("oversized_or_binary_workflow")
        return data.decode("utf-8", "strict")
    except subprocess.CalledProcessError as exc:
        raise ValueError("unreadable_git_object") from exc


def _runner(value: str) -> str:
    val = value.split("#", 1)[0].strip().strip("'\"")
    if HOSTED.fullmatch(val):
        return "hosted"
    if "zcloud" in val.lower() or "vps" in val.lower():
        return "needs_guard"
    if val == "self-hosted" or not val or "${{" in val or val.startswith("{"):
        return "needs_guard"
    # Only a specific dedicated non-zCloud label set is exempt. Generic
    # [self-hosted] and unrecognised labels can schedule on the zCloud VPS.
    if val.startswith("[") and val.endswith("]"):
        labels = [label.strip().strip("'\\\"").lower() for label in val[1:-1].split(",")]
        if len(labels) == 2 and set(labels) == {"self-hosted", "ftmo-research"}:
            return "other_self_hosted"
    return "needs_guard"


def _safe_checkout_step(lines: list[str]) -> bool:
    """Only a minimal canonical checkout may precede VPS identity admission.

    The checkout action executes before the identity guard. Reject alternative
    repositories, ref/path overrides, extra actions configuration and YAML
    syntax we cannot prove is a plain static input mapping.
    """
    inside_inputs = False
    saw_inputs = False
    inputs: dict[str, str] = {}
    allowed = {"persist-credentials", "clean", "submodules", "lfs", "fetch-depth"}
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # First '- uses:' field is stored without original YAML indentation.
        if index == 0:
            if not re.fullmatch(r"(?:uses|name|id):[^\n]+", line.strip()):
                return False
            continue
        if line.startswith("          "):
            if not inside_inputs:
                return False
            parsed = re.fullmatch(r"          ([a-z][a-z0-9-]*):\s*([^#\n]*)(?:#.*)?", line)
            if not parsed:
                return False
            key, value = parsed.group(1), parsed.group(2).strip()
            if value[:1] in ("'", '"') and value[-1:] == value[:1]:
                value = value[1:-1]
            if key in inputs or key not in allowed or "${{" in value:
                return False
            inputs[key] = value
            continue
        if not line.startswith("        "):
            return False
        field = line[8:]
        if re.fullmatch(r"with:\s*(?:#.*)?", field):
            if saw_inputs:
                return False
            saw_inputs, inside_inputs = True, True
        elif re.fullmatch(r"(?:name|id|uses):\s*[^\n]+", field):
            inside_inputs = False
        else:
            # No inline/aliased 'with:', env, defaults or executable hooks.
            return False
    if not saw_inputs or inputs.get("persist-credentials") != "false":
        return False
    if inputs.get("clean", "true") != "true":
        return False
    if inputs.get("submodules", "false") != "false":
        return False
    if inputs.get("lfs", "false") != "false":
        return False
    if "fetch-depth" in inputs and not re.fullmatch(r"(?:0|[1-9][0-9]{0,3})", inputs["fetch-depth"]):
        return False
    return True


def _early_guard(job: list[str]) -> bool:
    # If a later step uses a status-check override, it can run even after the
    # identity guard fails. No source-only admission can trust that sequence.
    if SHELL_HIJACK.search("\n".join(job)) or INLINE_HIJACK.search("\n".join(job)):
        return False
    if re.search(r"(?i)\b(?:always|failure|cancelled)\s*\(", "\n".join(job)):
        return False
    # Only a pinned checkout and shell 'set' may precede the actual guard command.
    # YAML quoted keys and merge aliases must not synthesize a container or
    # pre-step service behind our literal mapping scan.
    forbidden = re.compile(r"""^    (?:["']?(?:defaults|container|services)["']?|<<|\?\s*\S+)\s*:""")
    if any(forbidden.match(line) for line in job):
        return False
    # Tagged/quoted/unknown job keys can alias pre-step resources. Parse only
    # literal, known job-level keys; nested keys belong to their own mapping.
    allowed_job_keys = {
        "name", "runs-on", "needs", "permissions", "if", "timeout-minutes",
        "strategy", "concurrency", "env", "environment", "outputs", "steps",
    }
    for line in job:
        if line.startswith("    ") and not line.startswith("     ") and line.strip():
            if line.lstrip().startswith("#"):
                continue
            key = re.match(r"^    ([a-z][a-z0-9-]*):", line)
            if not key or key.group(1) not in allowed_job_keys:
                return False
    headings = [n for n, line in enumerate(job)
                if re.fullmatch(r"    steps:\s*(?:#.*)?", line)]
    if len(headings) != 1:
        return False
    steps = []
    for line in job[headings[0] + 1:]:
        if re.match(r"^      -\s+", line):
            steps.append([line[8:]])
        elif steps:
            steps[-1].append(line)
    checkout_seen = False
    for lines in steps:
        text = "\n".join(lines)
        uses = re.findall(r"(?m)^\s*uses:\s*([^\n]+)", text)
        runs = re.findall(r"(?m)^\s*run:\s*([^\n]*)", text)
        if len(uses) > 1 or len(runs) > 1 or (uses and runs):
            return False
        if uses:
            if re.search(r"(?m)^\s*(?:if|continue-on-error):", text):
                return False
            if checkout_seen or not CHECKOUT.fullmatch(uses[0].strip()) or not _safe_checkout_step(lines):
                return False
            checkout_seen = True
            continue
        if runs:
            # A different step cwd could load a stale/attacker-supplied guard
            # instead of the guard checked out at the trusted repository root.
            if re.search(r"(?m)^\s*working-directory:", text):
                return False
            # The identity check may not be skipped or demoted to nonfatal.
            if re.search(r"(?m)^\s*(?:if|continue-on-error):", text):
                return False
            # Folded YAML scripts join lines and do not prove a standalone
            # early executable guard command.
            if not checkout_seen or runs[0].strip() not in {"|", "|-"}:
                return False
            shells = re.findall(r"(?m)^\s*shell:\s*([^\n#]+)", text)
            if any(value.strip().strip("'\"") != "bash" for value in shells):
                return False
            seen_run = False
            for line in lines:
                stripped = line.strip()
                if not seen_run:
                    if re.match(r"^run:\s*", stripped):
                        seen_run = True
                    continue
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped in {"set -euo pipefail", "set -eu"}:
                    continue
                return bool(GUARD.fullmatch(stripped))
            return False
        return False
    return False


def unsafe_jobs(doc: str | None) -> dict[str, str]:
    if doc is None:
        return {}
    # Quoted/tagged/merge root keys can shadow or synthesize jobs invisibly.
    if re.search(r"(?m)^(?:[\"']jobs[\"']|[?!]\s*jobs|<<)\s*:", doc):
        raise ValueError("unsupported_jobs_mapping")
    lines = doc.splitlines()
    # Root defaults.run.shell can override the trusted first-run interpreter.
    workflow_defaults = bool(re.search(r"""(?m)^(?:defaults|["\']defaults["\']|[?!]\s*defaults)\s*:""", doc))
    # Repository-level env is inherited by each guarded job.
    root_env = bool(re.search(r"""(?m)^env:\s*(?:#.*)?\n(?:  [^\n]*\n)*?  ["']?(?:BASH_ENV|PYTHONPATH|PYTHONHOME|PATH)["']?\s*:""", doc))
    root_env = root_env or bool(re.search(r"""(?m)^env:\s*\{[^\n}]*(?:BASH_ENV|PYTHONPATH|PYTHONHOME|PATH)\s*:""", doc))
    roots = [i for i, l in enumerate(lines) if re.fullmatch(r"jobs:\s*(?:#.*)?", l)]
    if len(roots) != 1:
        raise ValueError("ambiguous_jobs_root")
    start = roots[0] + 1
    stop = next((i for i in range(start, len(lines)) if lines[i] and not lines[i][0].isspace()), len(lines))
    region = lines[start:stop]
    for line in region:
        # Inline mapping, aliases and quoted job keys cannot vanish from inventory.
        if (line.startswith("  ") and not line.startswith("   ")
                and line.strip() and not line.lstrip().startswith("#")
                and not JOB.fullmatch(line)):
            raise ValueError("unsupported_job_mapping")
    indices = [(i, JOB.fullmatch(l).group(1)) for i, l in enumerate(region) if JOB.fullmatch(l)]
    if not indices or len(indices) > 150 or len({x[1] for x in indices}) != len(indices):
        raise ValueError("ambiguous_job_ids")
    risks = {}
    for n, (index, name) in enumerate(indices):
        body = region[index + 1:indices[n + 1][0] if n + 1 < len(indices) else len(region)]
        # Check the *entire* job mapping before exempting GitHub-hosted
        # runners: a quoted/merged shadow of 'runs-on' must not disappear
        # behind an apparently safe ubuntu-latest declaration.
        for line in body:
            if not line.startswith("    ") or line.startswith("     ") or not line.strip():
                continue
            if line.lstrip().startswith("#"):
                continue
            key = re.match(r"^    ([a-z][a-z0-9-]*):", line)
            if not key:
                raise ValueError("unsupported_job_mapping_key")
            if key.group(1) not in {
                "name", "runs-on", "needs", "permissions", "if",
                "timeout-minutes", "continue-on-error", "strategy",
                "concurrency", "env", "environment", "outputs",
                "steps", "uses", "with", "secrets", "container",
                "services", "defaults",
            }:
                raise ValueError("unknown_job_mapping_key")
        runners = [f.group(1) for l in body if (f := re.fullmatch(r"    runs-on:\s*(.*)", l))]
        if not runners:
            if not any(re.match(r"^    uses:\s*", l) for l in body):
                risks[name] = "unknown_runner"
            continue
        if len(runners) != 1:
            risks[name] = "ambiguous_runner"
        elif _runner(runners[0]) not in {"hosted", "other_self_hosted"} and (
            workflow_defaults or root_env or not _early_guard(body)
        ):
            risks[name] = "missing_early_runner_guard"
    return risks


def _job_fingerprints(doc: str | None) -> dict[str, tuple[str, ...]]:
    """Structural comparison of existing risky jobs, not unrelated jobs/comments."""
    if doc is None:
        return {}
    lines = doc.splitlines()
    root = next((i for i, line in enumerate(lines)
                 if re.fullmatch(r"jobs:\s*(?:#.*)?", line)), None)
    if root is None:
        return {}
    stop = next((i for i in range(root + 1, len(lines))
                 if lines[i] and not lines[i][0].isspace()), len(lines))
    body = lines[root + 1:stop]
    ids = [(i, m.group(1)) for i, line in enumerate(body)
           if (m := JOB.fullmatch(line))]
    result = {}
    for idx, (start, name) in enumerate(ids):
        limit = ids[idx + 1][0] if idx + 1 < len(ids) else len(body)
        meaningful = tuple(line.rstrip() for line in body[start:limit]
                           if line.strip() and not line.lstrip().startswith("#"))
        result[name] = meaningful
    return result


def _workflow_envelope(doc: str | None) -> tuple[str, ...]:
    """Preserve root triggers/privileges around legacy unsafe jobs.

    This excludes job bodies so adding an independent, safe hosted job does
    not itself increase the exposure attributed to a legacy VPS job.
    """
    if doc is None:
        return ()
    lines = doc.splitlines()
    root = next((i for i, line in enumerate(lines)
                 if re.fullmatch(r"jobs:\s*(?:#.*)?", line)), None)
    if root is None:
        return tuple(line.rstrip() for line in lines if line.strip()
                     and not line.lstrip().startswith("#"))
    end = next((i for i in range(root + 1, len(lines))
                if lines[i] and not lines[i][0].isspace()), len(lines))
    surrounding = lines[:root + 1] + lines[end:]
    return tuple(line.rstrip() for line in surrounding if line.strip()
                 and not line.lstrip().startswith("#"))


def evaluate(base: str, head: str) -> dict:
    paths = inventory(base, head)
    findings, old_count, new_count = [], 0, 0
    for path in paths:
        old_doc, new_doc = source(base, path), source(head, path)
        old = unsafe_jobs(old_doc)
        new = unsafe_jobs(new_doc)
        old_count += len(old)
        new_count += len(new)
        # Unchanged unsafe jobs remain grandfathered. Mutating the same
        # unsafe job (even with an identical finding category) is fresh debt.
        old_fp = _job_fingerprints(old_doc)
        new_fp = _job_fingerprints(new_doc)
        envelope_changed = _workflow_envelope(old_doc) != _workflow_envelope(new_doc)
        for job, reason in sorted(new.items()):
            if job not in old or old[job] != reason:
                findings.append({"path": path, "job": job, "reason": reason})
            elif old_fp.get(job) != new_fp.get(job):
                findings.append({"path": path, "job": job,
                                 "reason": "modified_legacy_unsafe_job"})
            elif envelope_changed:
                findings.append({"path": path, "job": job,
                                 "reason": "modified_legacy_unsafe_workflow_envelope"})
    return {"source_only": True, "mutation_performed": False, "release_authorized": False,
            "changed_workflows": len(paths), "legacy_debt_in_changed_files": old_count,
            "remaining_debt_in_changed_files": new_count, "new_findings": findings}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    args = p.parse_args(argv)
    try:
        report = evaluate(args.base, args.head)
    except (ValueError, OSError, UnicodeError, subprocess.SubprocessError) as error:
        report = {"source_only": True, "mutation_performed": False, "release_authorized": False,
                  "new_findings": [{"reason": "unverifiable_source", "detail": type(error).__name__}]}
    print(json.dumps(report, sort_keys=True))
    return 1 if report["new_findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
