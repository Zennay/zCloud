#!/usr/bin/env python3
"""No-new-debt guard: dirty actions/checkout on persistent self-hosted jobs.

Advisory, source-only CI. No automatic workflow modification or deployment.
Textual YAML subset; unsupported shapes must be reviewed separately.
"""
import argparse
import collections
import json
import os
import re
import select
import subprocess
import sys
import time

SHA = re.compile(r"[0-9a-f]{40}\Z")
JOB = re.compile(r"^  (?P<quote>['\"]?)(?P<id>[A-Za-z0-9_.-]+)(?P=quote)\s*:(?:\s*#.*)?\s*$")
STEP = re.compile(r"^      -(?:\s|$)")
USES = re.compile(r"^\s*(?:-\s*)?['\"]?uses['\"]?\s*:\s*['\"]?([^'\"\s#]+)")
CLEAN = re.compile(r"^\s+['\"]?clean['\"]?\s*:\s*(.*)$")
WORKFLOW_PREFIX = ".github/workflows/"
MAX_FILES, MAX_BYTES = 512, 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_TREE_BYTES = 512 * 1024
MAX_GIT_OUTPUT = MAX_BYTES + 8192
MAX_GIT_SECONDS = 25
CANONICAL_ORIGINS = frozenset({
    "https://github.com/Zennay/zCloud",
    "https://github.com/Zennay/zCloud.git",
})


class Denied(ValueError):
    pass


def git(*args):
    """Read bounded Git stdout without first buffering attacker-sized output.

    This scanner only issues read-only Git commands. Pipe polling ensures even
    malformed trees and adversarial Git metadata cannot exhaust hosted memory.
    """
    deadline = time.monotonic() + MAX_GIT_SECONDS
    with subprocess.Popen(["git", *args], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as proc:
        result = bytearray()
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Denied("git_source_timeout")
                if not select.select([proc.stdout], [], [], remaining)[0]:
                    raise Denied("git_source_timeout")
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                result.extend(chunk)
                if len(result) > MAX_GIT_OUTPUT:
                    raise Denied("git_output_size_cap")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Denied("git_source_timeout")
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise Denied("git_source_timeout") from None
            if proc.returncode:
                raise Denied("git_source_unavailable")
            return bytes(result)
        except BaseException:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            raise


def workflow_tree(sha):
    if not SHA.fullmatch(sha):
        raise Denied("invalid_commit")
    raw = git("ls-tree", "-rz", "--full-tree", sha, WORKFLOW_PREFIX)
    if len(raw) > MAX_TREE_BYTES:
        raise Denied("workflow_tree_size_cap")
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
    in_jobs = False
    jobs_headers = 0
    noncanonical_jobs_reported = False
    for line in lines:
        # Only actual top-level `jobs` children are job IDs. A flow/alias
        # jobs map is not safely inspectable by this bounded source parser.
        header = re.match(r"^['\\\"]?jobs['\\\"]?\s*:\s*(.*)$", line)
        if header:
            jobs_headers += 1
            in_jobs = True
            current = None
            if header.group(1).split("#", 1)[0].strip():
                risks.append((path, "workflow", "jobs", "opaque-jobs-map"))
            continue
        if line and not line.startswith((" ", "#")):
            in_jobs = False
            current = None
            continue
        match = JOB.match(line) if in_jobs else None
        if match:
            current = (match.group("id"), [])
            jobs.append(current)
        elif in_jobs and line.startswith("  ") and not line.startswith("   ") and (
                line.strip() and not line.lstrip().startswith("#")):
            # A flow-style job, tagged key or inline mapping can conceal its
            # runner and checkout. Never report an incomplete scan as clean.
            risks.append((path, "workflow", "jobs", "opaque-job-entry"))
            current = None
        elif in_jobs and not jobs and line.startswith("    ") and line.strip() and (
                not line.lstrip().startswith("#")):
            # YAML permits indents other than two spaces for jobs. This
            # textual subset only recognizes the canonical form; reject
            # noncanonical candidates instead of ignoring their contents.
            if not noncanonical_jobs_reported:
                risks.append((path, "workflow", "jobs", "noncanonical-job-indent"))
                noncanonical_jobs_reported = True
        elif current and (line.startswith("    ") or not line.strip()):
            current[1].append(line)
        elif current and line and not line.startswith("#"):
            current = None
    if jobs_headers != 1:
        risks.append((path, "workflow", "jobs", "missing-or-duplicated-jobs-map"))

    for job_name, body in jobs:
        # Mapping order is irrelevant in YAML: runs-on may appear *after*
        # steps. Restrict runner-sequence entries to runs-on itself; a list
        # under a different job key must never declare the runner implicitly.
        runner_fields = [l for l in body
                         if re.match(r"^    ['\"]?runs-on['\"]?\s*:", l)]
        runner_items = []
        in_runner = False
        for line in body:
            if re.match(r"^    ['\"]?[A-Za-z_-]+['\"]?\s*:", line):
                in_runner = bool(re.match(r"^    ['\"]?runs-on['\"]?\s*:", line))
            elif in_runner and re.match(r"^      -\s*", line):
                runner_items.append(line)
        runner_source = runner_fields + runner_items
        selfhosted = any(re.search(r"(?<![A-Za-z0-9_-])self-hosted(?![A-Za-z0-9_-])",
                                    line, re.IGNORECASE) for line in runner_source)
        # A YAML alias, mapping, or GitHub expression can resolve to
        # self-hosted even without a literal label; deny new checkout jobs.
        dynamic_runner = (
            any(chr(36) + "{" in line or re.search(r":\s*[*&{]", line)
                for line in runner_fields + runner_items)
            or (not runner_items and any(
                re.match(r"^    ['\"]?runs-on['\"]?\s*:\s*(?:#.*)?$", line)
                for line in runner_fields))
        )
        if dynamic_runner and any("checkout@" in line.lower() for line in body):
            risks.append((path, job_name, "runner", "dynamic-runner-checkout"))
        if not selfhosted:
            continue
        # A self-hosted job using an aliased, expression-valued or flow-style
        # steps declaration cannot be checked by this source subset. Refuse
        # new instances rather than claiming they contain no dirty checkout.
        declarations = [line for line in body
                        if re.match(r"^    ['\"]?steps['\"]?\s*:", line)]
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
        # YAML steps can begin with any valid property (e.g. timeout-minutes
        # or continue-on-error), or a bare dash and multiline mapping.
        # Scope entries to the real steps list, not the runs-on labels.
        steps = []
        step = None
        in_steps = False
        for line in body:
            if re.match(r"^    ['\"]?steps['\"]?\s*:", line):
                in_steps = True
                step = None
                continue
            if re.match(r"^    ['\"]?[A-Za-z_-]+['\"]?\s*:", line):
                in_steps = False
                step = None
                continue
            if not in_steps:
                continue
            if STEP.match(line):
                step = [line]
                steps.append(step)
            elif step is not None:
                step.append(line)
        for index, step_lines in enumerate(steps):
            initial = step_lines[0].split("-", 1)[1].strip()
            if (initial.startswith(("*", "&", "!", "[", "{"))
                or (initial and not re.match(r"^['\"]?[A-Za-z_-]+['\"]?\s*:", initial))):
                risks.append((path, job_name, str(index), "opaque-step-entry"))
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
                with_blocks = 0
                for line in step_lines:
                    if re.match(r"^(?:        |      -\s+)['\"]?with['\"]?\s*:", line):
                        with_blocks += 1
                        in_with = True
                        with_value = line.split(":", 1)[1].split("#", 1)[0].strip()
                        if with_value:
                            # Inline flow maps and inherited/unknown maps can
                            # override clean; only a literal block is proven.
                            risks.append((path, job_name, str(index),
                                          "opaque-with-map"))
                    elif re.match(r"^        ['\"]?[A-Za-z_-]+['\"]?\s*:", line):
                        in_with = False
                    elif in_with:
                        match = CLEAN.match(line)
                        if match:
                            raw = match.group(1).split("#", 1)[0].strip().strip("'\"").lower()
                            clean_values.append(raw)
                        if re.match(r"^\s+<<\s*:", line):
                            risks.append((path, job_name, str(index), "merged-with-map"))
                if with_blocks > 1:
                    risks.append((path, job_name, str(index), "duplicate-with-map"))
                if len(clean_values) > 1:
                    risks.append((path, job_name, str(index), "duplicate-clean"))
                for value in clean_values:
                    if value != "true":
                        risks.append((path, job_name, str(index), "unsafe-clean=" + value[:60]))
            elif (action.startswith(("*", "&", "!")) or chr(36) in action):
                # Unknown action identities may resolve to checkout via YAML
                # aliases/tags or expression-selected uses references.
                risks.append((path, job_name, str(index), "unresolved-action-reference"))
    return risks


def inventory(sha):
    findings = []
    total = 0
    for path, oid in sorted(workflow_tree(sha).items()):
        # An untrusted blob's object size must be checked before its contents
        # are read. A large workflow must not exhaust hosted CI memory.
        try:
            raw_size = git("cat-file", "-s", oid).decode("ascii", "strict").strip()
        except UnicodeDecodeError:
            raise Denied("invalid_workflow_blob_size") from None
        if not re.fullmatch(r"[0-9]{1,10}", raw_size):
            raise Denied("invalid_workflow_blob_size")
        size = int(raw_size)
        if size > MAX_BYTES:
            raise Denied("workflow_size_cap")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise Denied("workflow_aggregate_size_cap")
        data = git("cat-file", "blob", oid)
        if len(data) != size:
            raise Denied("workflow_blob_size_drift")
        try:
            source = data.decode("utf-8", "strict")
        except UnicodeError:
            raise Denied("invalid_workflow_utf8") from None
        findings.extend(analyze(source, path))
        if len(findings) > 8192:
            raise Denied("finding_cap")
    return collections.Counter(findings)


def run(base, head, *, trusted_origins=None):
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise Denied("invalid_commit")
    # PR candidate code is only executed on GitHub-hosted runners, but the
    # Git history scanner must still reject redirected or replaced origins.
    # The alternate allowlist exists exclusively for local temporary Git
    # fixtures; the public CLI always uses the canonical origins above.
    allowed = CANONICAL_ORIGINS if trusted_origins is None else frozenset(trusted_origins)
    try:
        origin_lines = git("remote", "get-url", "origin").decode("utf-8", "strict").splitlines()
    except UnicodeDecodeError:
        raise Denied("invalid_git_origin") from None
    if len(origin_lines) != 1 or origin_lines[0] not in allowed:
        raise Denied("foreign_checkout_origin")
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
            "canonical_origin_verified": trusted_origins is None,
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
