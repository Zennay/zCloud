#!/usr/bin/env python3
"""No-new-debt guard: dirty actions/checkout on persistent self-hosted jobs.

Advisory, source-only CI. No automatic workflow modification or deployment.
Textual YAML subset; unsupported shapes must be reviewed separately.
"""
import argparse
import collections
import json
import re
import subprocess
import sys

SHA = re.compile(r"[0-9a-f]{40}\Z")
JOB = re.compile(r"^  ([A-Za-z0-9_.-]+):(?:\s*#.*)?\s*$")
STEP = re.compile(r"^      - (?:name|uses|run|id|if|shell|env|with):")
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^'\"\s#]+)")
CLEAN = re.compile(r"^\s+clean\s*:\s*(.*)$")
WORKFLOW_PREFIX = ".github/workflows/"
MAX_FILES, MAX_BYTES = 512, 1024 * 1024


class Denied(ValueError):
    pass


def git(*args):
    result = subprocess.run(["git", *args], capture_output=True, timeout=25,
                            check=False)
    if result.returncode:
        raise Denied("git_source_unavailable")
    return result.stdout


def workflow_tree(sha):
    if not SHA.fullmatch(sha):
        raise Denied("invalid_commit")
    raw = git("ls-tree", "-rz", "--full-tree", sha, WORKFLOW_PREFIX)
    records = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            metadata, raw_path = item.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ")
            path = raw_path.decode("utf-8", "strict")
        except (UnicodeError, ValueError):
            raise Denied("invalid_tree") from None
        if (mode != "100644" and mode != "100755") or kind != "blob":
            raise Denied("nonregular_workflow_manifest")
        if not path.startswith(WORKFLOW_PREFIX) or not path.endswith((".yml", ".yaml")):
            continue
        if path in records or len(records) >= MAX_FILES or not SHA.fullmatch(oid):
            raise Denied("duplicate_or_excess_workflows")
        records[path] = oid
    if not records:
        raise Denied("empty_workflow_tree")
    return records


def analyze(source, path):
    """List syntactically evident checkout hygiene risks, not release approval.

    We intentionally inspect only literal self-hosted runs-on jobs. Dynamic
    runner expressions and aliases are separate limitations, never a clear
    authorization to run on the VPS.
    """
    risks = []
    if len(source.encode("utf-8")) > MAX_BYTES or "\t" in source:
        raise Denied("oversized_or_tab_indented_yaml")
    lines = source.splitlines()
    jobs = []
    current = None
    for line in lines:
        match = JOB.match(line)
        if match:
            current = (match.group(1), [])
            jobs.append(current)
        elif current and (line.startswith("    ") or not line.strip()):
            current[1].append(line)
        elif current and line and not line.startswith("#"):
            current = None

    for job_name, body in jobs:
        # Do not mistake text inside a shell command for a runner declaration:
        # match only the top-level job mapping and its sequence entries.
        before_steps = []
        for line in body:
            if line.startswith("    steps:"):
                break
            before_steps.append(line)
        runner_fields = [l for l in before_steps
                         if re.match(r"^    runs-on\s*:", l)]
        runner_list_items = [l for l in before_steps
                             if re.match(r"^      -\s*['\\\"]?self-hosted['\\\"]?(?:\s*#.*)?$", l)]
        selfhosted = (any("self-hosted" in l.lower() for l in runner_fields)
                      or bool(runner_list_items))
        # A dynamic runner may select the permanent VPS even if its source
        # text does not contain the literal word self-hosted.
        dynamic_runner = any(chr(36) + "{" in line or "*" in line.split(":", 1)[1]
                             for line in runner_fields)
        if dynamic_runner and any("checkout@" in line.lower() for line in body):
            risks.append((path, job_name, "runner", "dynamic-runner-checkout"))
        if not selfhosted:
            continue
        # A self-hosted job using an aliased, expression-valued or flow-style
        # steps declaration cannot be checked by this source subset. Refuse
        # new instances rather than claiming they contain no dirty checkout.
        declarations = [line for line in body
                        if re.match(r"^    steps\s*:", line)]
        for decl in declarations:
            tail = decl.split(":", 1)[1].split("#", 1)[0].strip()
            if tail and tail != "[]":
                risks.append((path, job_name, "steps", "nonliteral-steps"))
        if len(declarations) > 1:
            risks.append((path, job_name, "steps", "duplicate-steps-map"))
        for line in body:
            # YAML flow-style steps bypass conventional `- uses:` scanners.
            # Their nested action/input identity is not proven: require
            # independent review for each newly introduced flow-style step.
            if re.match(r"^      -\s*\{", line):
                risks.append((path, job_name, "steps", "flow-step-unverified"))
        steps = []
        step = None
        for line in body:
            if STEP.match(line):
                step = [line]
                steps.append(step)
            elif step is not None:
                step.append(line)
        for index, step_lines in enumerate(steps):
            action_refs = []
            for line in step_lines:
                hit = USES.match(line)
                if hit:
                    action_refs.append(hit.group(1).lower())
            if len(action_refs) > 1:
                risks.append((path, job_name, str(index), "ambiguous-action"))
                continue
            if not action_refs:
                continue
            action = action_refs[0]
            if action.startswith("actions/checkout@"):
                clean_values = []
                in_with = False
                for line in step_lines:
                    if re.match(r"^        with\s*:", line):
                        in_with = True
                        if re.search(r"^        with\s*:\s*\{", line):
                            risks.append((path, job_name, str(index), "inline-with-unverified"))
                    elif re.match(r"^        [A-Za-z_-]+\s*:", line):
                        in_with = False
                    elif in_with:
                        match = CLEAN.match(line)
                        if match:
                            raw = match.group(1).split("#", 1)[0].strip().strip("'\"").lower()
                            clean_values.append(raw)
                        if re.match(r"^\s+<<\s*:", line):
                            risks.append((path, job_name, str(index), "merged-with-map"))
                if len(clean_values) > 1:
                    risks.append((path, job_name, str(index), "duplicate-clean"))
                for value in clean_values:
                    if value != "true":
                        risks.append((path, job_name, str(index), "unsafe-clean=" + value[:60]))
            elif "checkout" in action and ("$" in action or "*" in action):
                risks.append((path, job_name, str(index), "unresolved-checkout-action"))
    return risks


def inventory(sha):
    findings = []
    for path, oid in sorted(workflow_tree(sha).items()):
        data = git("cat-file", "blob", oid)
        if len(data) > MAX_BYTES:
            raise Denied("workflow_size_cap")
        try:
            source = data.decode("utf-8", "strict")
        except UnicodeError:
            raise Denied("invalid_workflow_utf8") from None
        findings.extend(analyze(source, path))
        if len(findings) > 8192:
            raise Denied("finding_cap")
    return collections.Counter(findings)


def run(base, head):
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise Denied("invalid_commit")
    if git("rev-parse", "HEAD").decode().strip() != head:
        raise Denied("not_exact_head")
    if git("merge-base", "--is-ancestor", base, head) != b"":
        raise Denied("unexpected_ancestor_result")
    before = git("ls-remote", "--exit-code", "origin", "refs/heads/main").decode().split()
    if len(before) != 2 or before[0] != base or before[1] != "refs/heads/main":
        raise Denied("moving_or_foreign_main")
    old, new = inventory(base), inventory(head)
    after = git("ls-remote", "--exit-code", "origin", "refs/heads/main").decode().split()
    if after != before:
        raise Denied("main_moved_mid_scan")
    additions = sorted((new - old).elements())
    return {"policy": "selfhosted-checkout-clean-no-new-debt-v1",
            "base": base, "head": head,
            "baseline_findings": sum(old.values()),
            "candidate_findings": sum(new.values()),
            "new_findings": additions[:100],
            "new_finding_count": len(additions),
            "deploy_authorized": False,
            "mutation_performed": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    try:
        report = run(args.base, args.head)
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 2 if report["new_finding_count"] else 0
    except (Denied, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__,
                          "deploy_authorized": False, "mutation_performed": False}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
