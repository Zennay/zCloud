#!/usr/bin/env python3
"""Read-only no-new-debt admission for GitHub-hosted matrix fan-out.

Scope is *only* literal, recognized GitHub-managed runner labels. Custom,
self-hosted, dynamic and reusable jobs are owned by separate policies.
Bound individual and aggregate parallelism without changing any workflow.
"""
import argparse
import hashlib
import math
import os
import re
import selectors
import subprocess
import sys
import time

import yaml

MAX_WORKFLOW_BYTES = 1_048_576
MAX_GIT_OUTPUT = 2_097_152
SHA = re.compile(r"^[0-9a-f]{40}$")
HOSTED = re.compile(
    r"^(?:ubuntu-(?:latest|22\.04|24\.04(?:-arm)?|slim)"
    r"|windows-(?:latest|2022|2025(?:-arm)?)"
    r"|macos-(?:latest|13|14|15|26)(?:-large|-xlarge|-intel)?)$"
)


class InvalidSource(Exception):
    pass


class StrictLoader(yaml.SafeLoader):
    MAX_DEPTH = 64
    MAX_NODES = 30000

    def __init__(self, stream):
        super().__init__(stream)
        self.depth = 0
        self.nodes = 0

    def compose_node(self, parent, index):
        self.depth += 1
        self.nodes += 1
        try:
            if self.depth > self.MAX_DEPTH or self.nodes > self.MAX_NODES:
                raise InvalidSource("yaml_complexity_limit")
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1


def unique_mapping(loader, node):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, (str, int, bool)) or key in mapping:
            raise InvalidSource("duplicate_or_invalid_yaml_key")
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def git(*args, limit=MAX_GIT_OUTPUT):
    """Enforce Git byte/time budgets while reading, not after buffering."""
    try:
        proc = subprocess.Popen(
            ["git", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except OSError as exc:
        raise InvalidSource("git_command_unavailable") from exc
    output = []
    stdout_bytes = 0
    stderr_bytes = 0
    deadline = time.monotonic() + 20
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
            selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InvalidSource("git_command_timeout")
                ready = selector.select(timeout=remaining)
                if not ready:
                    raise InvalidSource("git_command_timeout")
                for key, _ in ready:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        stdout_bytes += len(chunk)
                        if stdout_bytes > limit:
                            raise InvalidSource("git_output_too_large")
                        output.append(chunk)
                    else:
                        stderr_bytes += len(chunk)
                        if stderr_bytes > 8192:
                            raise InvalidSource("git_stderr_too_large")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise InvalidSource("git_command_timeout")
        if proc.wait(timeout=remaining):
            raise InvalidSource("git_command_failed")
        return b"".join(output)
    except subprocess.TimeoutExpired as exc:
        raise InvalidSource("git_command_timeout") from exc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def require_commit(revision):
    if not SHA.fullmatch(revision):
        raise InvalidSource("invalid_immutable_revision")
    if git("cat-file", "-t", revision, limit=80).strip() != b"commit":
        raise InvalidSource("expected_commit")


def changed_paths(base, head):
    raw = git(
        "diff", "--name-only", "--no-renames", "-z",
        base, head, "--", ".github/workflows"
    )
    try:
        paths = [part.decode("utf-8") for part in raw.split(b"\0") if part]
    except UnicodeDecodeError as exc:
        raise InvalidSource("invalid_workflow_path_encoding") from exc
    if len(paths) > 512:
        raise InvalidSource("changed_workflow_path_budget_exceeded")
    return sorted(set(paths))


def workflow_path(path):
    if not path.startswith(".github/workflows/") or path.count("/") != 2:
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        raise InvalidSource("workflow_path_control_character")
    if path.endswith((".yml", ".yaml")):
        return True
    if path.lower().endswith((".yml", ".yaml")):
        raise InvalidSource("noncanonical_workflow_extension")
    return False


def read_blob(revision, path):
    try:
        line = git("ls-tree", revision, "--", path, limit=800).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise InvalidSource("invalid_tree_encoding") from exc
    if not line:
        return None
    if "\t" not in line:
        raise InvalidSource("ambiguous_tree_entry")
    metadata, candidate_path = line.split("\t", 1)
    entry = metadata.split()
    if (candidate_path != path or len(entry) != 3
            or entry[0] not in ("100644", "100755")
            or entry[1] != "blob" or not SHA.fullmatch(entry[2])):
        raise InvalidSource("nonregular_or_invalid_workflow")
    try:
        size = int(git("cat-file", "-s", entry[2], limit=40).strip())
    except ValueError as exc:
        raise InvalidSource("invalid_blob_size") from exc
    if size > MAX_WORKFLOW_BYTES:
        raise InvalidSource("workflow_too_large")
    raw = git("cat-file", "blob", entry[2], limit=MAX_WORKFLOW_BYTES)
    if len(raw) != size:
        raise InvalidSource("blob_length_mismatch")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidSource("invalid_workflow_encoding") from exc


MAX_MATRIX_JOBS = 16
MAX_MATRIX_PARALLEL = 4


def hosted_runner(value):
    """Exact canonical GitHub-managed runner labels only."""
    if isinstance(value, str):
        return HOSTED.fullmatch(value) is not None
    if isinstance(value, list) and 0 < len(value) <= 8:
        return all(isinstance(x, str) and HOSTED.fullmatch(x) for x in value)
    return False


def static_value(value):
    if isinstance(value, str):
        return "$" + "{{" not in value
    if isinstance(value, (int, bool)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def matrix_size(value):
    """Conservative static GitHub matrix upper bound, never subtract excludes."""
    if not isinstance(value, dict):
        return None
    axes, product = 0, 1
    included = 0
    for key, values in value.items():
        if not isinstance(key, str):
            return None
        if key in ("include", "exclude"):
            if not isinstance(values, list) or len(values) > MAX_MATRIX_JOBS:
                return None
            for row in values:
                if not isinstance(row, dict) or len(row) > 32:
                    return None
                if any(not isinstance(k, str) or not static_value(v)
                       for k, v in row.items()):
                    return None
            if key == "include":
                included = len(values)
            continue
        if not isinstance(values, list) or not values:
            return None
        if any(not static_value(item) for item in values):
            return None
        axes += 1
        product *= len(values)
        if product > MAX_MATRIX_JOBS:
            return product
    if axes == 0:
        return included if included else None
    return product + included


def violations(source):
    try:
        document = yaml.load(source, Loader=StrictLoader)
    except (yaml.YAMLError, UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise InvalidSource("unparseable_workflow_yaml") from exc
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
        raise InvalidSource("invalid_workflow_jobs")
    risks = set()
    total_slots = 0
    for job_id, job in document["jobs"].items():
        if not isinstance(job_id, str) or not isinstance(job, dict):
            raise InvalidSource("invalid_job_definition")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in job_id):
            raise InvalidSource("job_identifier_control_character")
        if "uses" in job or not hosted_runner(job.get("runs-on")):
            continue  # reusable and self-hosted job policy has another owner
        strategy = job.get("strategy")
        if strategy is None:
            continue
        if not isinstance(strategy, dict):
            raise InvalidSource("invalid_hosted_strategy")
        if "matrix" not in strategy:
            continue
        matrix = strategy["matrix"]
        shape = matrix_size(matrix)
        parallel = strategy.get("max-parallel")
        if not isinstance(parallel, int) or isinstance(parallel, bool):
            reason = "max_parallel_not_static_integer"
        elif not (1 <= parallel <= MAX_MATRIX_PARALLEL):
            reason = "max_parallel_outside_1_to_4"
        else:
            reason = None
        if shape is None:
            shape_reason = "dynamic_or_invalid_matrix"
        elif shape > MAX_MATRIX_JOBS:
            shape_reason = "matrix_fanout_over_16"
        else:
            shape_reason = None
        # Exact unsafe configuration digest avoids laundering a new increase
        # under an old reason. Digest only, no arbitrary source logged.
        runner = job["runs-on"]
        runner_identity = runner if isinstance(runner, str) else ",".join(runner)
        digest = hashlib.sha256(repr((runner_identity, strategy)).encode("utf-8")).hexdigest()[:20]
        if reason:
            risks.add((job_id, runner_identity, digest, reason))
        if shape_reason:
            risks.add((job_id, runner_identity, digest, shape_reason))
        # A collection of independently "safe" matrices can collectively
        # exhaust hosted execution slots. For dynamic/invalid, fail per job.
        if shape is not None:
            total_slots += shape
    if total_slots > MAX_MATRIX_JOBS:
        # Bound legacy risk by exact derived aggregate, not only reason name.
        digest = hashlib.sha256(str(total_slots).encode("ascii")).hexdigest()[:20]
        risks.add(("__workflow__", "hosted", digest, "aggregate_hosted_matrix_over_16"))
    return risks


def audit(base, head):
    require_commit(base)
    require_commit(head)
    old_count = 0
    new_count = 0
    denied = []
    examined = 0
    for path in changed_paths(base, head):
        if not workflow_path(path):
            continue
        after = read_blob(head, path)
        if after is None:
            continue  # Deleting a workflow cannot introduce a job.
        before = read_blob(base, path)
        baseline = violations(before) if before is not None else set()
        current = violations(after)
        old_count += len(baseline)
        new_count += len(current)
        examined += 1
        for job_id, runner, _fingerprint, reason in sorted(current - baseline):
            denied.append((path, job_id, runner, reason))
    return examined, old_count, new_count, denied


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        examined, baseline, current, denied = audit(args.base, args.head)
    except InvalidSource as exc:
        print("ZCLOUD_HOSTED_MATRIX=DENY invalid_source=" + str(exc))
        return 2
    print(
        "ZCLOUD_HOSTED_MATRIX="
        + ("DENY" if denied else "PASS")
        + f" changed_workflows={examined} existing_debt={baseline}"
        + f" current_debt={current} new_debt={len(denied)}"
        + " mutation_performed=false"
    )
    for path, job, runner, reason in denied[:25]:
        print(f"NEW_HOSTED_MATRIX_DEBT path={path} job={job} runner={runner} reason={reason}")
    return 1 if denied else 0


if __name__ == "__main__":
    sys.exit(main())
