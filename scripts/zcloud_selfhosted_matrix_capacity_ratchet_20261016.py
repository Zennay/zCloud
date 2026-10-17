#!/usr/bin/env python3
"""Deny new VPS matrix fan-out in changed GitHub Actions workflows.

Source-only/no authority to dispatch, cancel or change runners. Existing
unsafe jobs are a baseline, not evidence of safety; touching a legacy job
without reducing its risk must be reviewed by its owner.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import selectors
import subprocess
import sys
import time

import yaml

POLICY = "zcloud-selfhosted-matrix-capacity-v1"
SHA = re.compile(r"^[a-f0-9]{40}$")
MAX_BLOB = 1_000_000
MAX_CHANGED = 512
MAX_MATRIX_JOBS = 4


class InvalidSource(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    """Bound parser nesting and node count before constructing YAML objects."""

    MAX_DEPTH = 64
    MAX_NODES = 30_000

    def __init__(self, stream):
        super().__init__(stream)
        self._depth = 0
        self._nodes = 0

    def compose_node(self, parent, index):
        self._depth += 1
        self._nodes += 1
        try:
            if self._depth > self.MAX_DEPTH or self._nodes > self.MAX_NODES:
                raise InvalidSource("yaml_complexity_limit")
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def _mapping(loader, node):
    if not isinstance(node, yaml.MappingNode):
        raise InvalidSource("invalid_yaml_mapping")
    pairs = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, (str, int, bool)):
            raise InvalidSource("complex_yaml_key")
        if str(key) == "<<":
            raise InvalidSource("yaml_merge_not_trusted")
        if key in pairs:
            raise InvalidSource("duplicate_yaml_key")
        pairs[key] = loader.construct_object(value_node, deep=True)
    return pairs


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _git(*args, max_output=5_000_000):
    """Read Git objects under a live byte/time cap (not after buffering)."""
    try:
        process = subprocess.Popen(["git", *args], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
    except OSError as exc:
        raise InvalidSource("git_source_unavailable") from exc
    chunks = []
    stdout_bytes = 0
    stderr_bytes = 0
    deadline = time.monotonic() + 20
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InvalidSource("git_source_timeout")
                ready = selector.select(timeout=remaining)
                if not ready:
                    raise InvalidSource("git_source_timeout")
                for key, _ in ready:
                    part = os.read(key.fileobj.fileno(), 65_536)
                    if not part:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        stdout_bytes += len(part)
                        if stdout_bytes > max_output:
                            raise InvalidSource("git_output_too_large")
                        chunks.append(part)
                    else:
                        stderr_bytes += len(part)
                        if stderr_bytes > 8192:
                            raise InvalidSource("git_stderr_too_large")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise InvalidSource("git_source_timeout")
        if process.wait(timeout=remaining):
            raise InvalidSource("git_source_unavailable")
        return b"".join(chunks)
    except subprocess.TimeoutExpired as exc:
        raise InvalidSource("git_source_timeout") from exc
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def _commit(sha):
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise InvalidSource("invalid_immutable_sha")
    if _git("cat-file", "-t", sha, max_output=100).strip() != b"commit":
        raise InvalidSource("expected_commit_not_tree")


def _blob(sha, path):
    try:
        listing = _git("ls-tree", sha, "--", path, max_output=2000).decode("utf-8")
    except UnicodeError as exc:
        raise InvalidSource("invalid_git_tree") from exc
    if not listing.strip():
        return None
    if not listing.startswith("100644 blob ") and not listing.startswith("100755 blob "):
        raise InvalidSource("non_regular_workflow")
    line = listing.strip()
    _, _, identity = line.partition("\t")
    if identity != path:
        raise InvalidSource("ambiguous_git_path")
    entry = line.split("\t", 1)[0].split()
    if len(entry) != 3 or not SHA.fullmatch(entry[2]):
        raise InvalidSource("invalid_git_blob")
    raw_size = _git("cat-file", "-s", entry[2], max_output=100)
    try:
        length = int(raw_size)
    except ValueError as exc:
        raise InvalidSource("invalid_blob_size") from exc
    if length > MAX_BLOB:
        raise InvalidSource("workflow_too_large")
    return _git("cat-file", "blob", entry[2], max_output=MAX_BLOB)


# Only these canonical GitHub-managed hosted runner labels can be excluded.
# Arbitrary labels (including 'vps', 'zcloud', custom larger runners and future
# labels) can route to self-hosted hardware. An unknown must never be treated
# as evidence of GitHub-hosted execution.
_HOSTED_RUNNER = re.compile(
    r"^(?:ubuntu-(?:latest|(?:22|24)\.04(?:-arm)?|slim)|"
    r"windows-(?:latest|(?:2022|2025)(?:-arm)?)|"
    r"macos-(?:latest|(?:13|14|15|26)(?:-large|-xlarge|-intel)?))$"
)


def _candidate_runner(value):
    """Treat unknown labels as VPS-capable; only known hosted labels exclude.

    Never use substring matching: 'ubuntu-latest-my-vps' is not hosted proof.
    """
    pending = [value]
    seen = set()
    while pending:
        current = pending.pop()
        if current is None:
            return True  # no runs-on = unknown, not hosted evidence
        if isinstance(current, str):
            if not _HOSTED_RUNNER.fullmatch(current):
                return True
        elif isinstance(current, list):
            if not current:
                return True  # [] cannot identify a valid hosted runner
            identity = id(current)
            if identity in seen:
                raise InvalidSource("cyclic_runner_selection")
            seen.add(identity)
            if len(seen) > 64 or len(current) > 64:
                raise InvalidSource("oversized_runner_selection")
            pending.extend(current)
        else:
            # group/labels mappings, complex scalar values, or unknown tags
            # cannot establish that the runner is GitHub hosted.
            return True
    return False


def _static_matrix_value(value):
    # GitHub expressions can evaluate to arrays or other shapes after YAML
    # parsing. A literal-looking list containing an expression is not proof
    # of static cardinality.
    if isinstance(value, str):
        return "${{" not in value
    if isinstance(value, bool) or isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _matrix_count(matrix):
    if not isinstance(matrix, dict):
        return None
    count = 1
    axes = 0
    for key, values in matrix.items():
        if key in ("include", "exclude"):
            if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
                return None
            if any(not isinstance(k, str) or not _static_matrix_value(item)
                   for row in values for k, item in row.items()):
                return None
            if len(values) > MAX_MATRIX_JOBS:
                return MAX_MATRIX_JOBS + 1
            continue
        if not isinstance(key, str) or not isinstance(values, list) or not values:
            return None
        if any(not _static_matrix_value(v) for v in values):
            return None
        count *= len(values)
        axes += 1
        if count > MAX_MATRIX_JOBS:
            return count
    extra = len(matrix.get("include", []))
    if axes == 0:
        return extra if extra else None
    return count + extra  # conservative upper bound, including overrides


def findings(source):
    try:
        data = yaml.load(source, Loader=UniqueLoader)
    except (yaml.YAMLError, UnicodeError, ValueError, TypeError) as exc:
        raise InvalidSource("unparseable_yaml") from exc
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        raise InvalidSource("unknown_workflow_schema")
    risks = []
    # Each separately named self-hosted matrix job can execute in parallel
    # with other jobs even when every job has max-parallel: 1. Prevent a
    # collection of individually bounded matrices from bypassing the overall
    # capacity ceiling for newly introduced workflow source.
    matrix_groups = []
    if len(data["jobs"]) > 256:
        raise InvalidSource("too_many_jobs")
    for name, job in data["jobs"].items():
        if not isinstance(name, str) or not isinstance(job, dict):
            raise InvalidSource("invalid_job")
        strategy = job.get("strategy", {})
        if not isinstance(strategy, dict):
            raise InvalidSource("unknown_strategy")
        # A reusable workflow job has no runs-on here, yet its callee may
        # fan out onto permanent VPS runners. Treat callers as unknown runner
        # destinations instead of silently certifying them as hosted-only.
        potential_vps = _candidate_runner(job.get("runs-on")) or "uses" in job
        if "matrix" not in strategy or not potential_vps:
            continue
        matrix_groups.append(name)
        parallel = strategy.get("max-parallel")
        matrix_size = _matrix_count(strategy["matrix"])
        if type(parallel) is not int or parallel != 1:
            risks.append((name, "unsafe_max_parallel:" + str(parallel)[:50]))
        if matrix_size is None:
            risks.append((name, "unknown_matrix_size"))
        elif matrix_size > MAX_MATRIX_JOBS:
            risks.append((name, "matrix_fanout:" + str(matrix_size)))
    if len(matrix_groups) > MAX_MATRIX_JOBS:
        risks.append(("__workflow__", "aggregate_matrix_groups:" + str(len(matrix_groups))))
    return sorted(risks)


def _matrix_job_config(data):
    """Return only run-target/matrix configuration for VPS-capable jobs."""
    jobs = data["jobs"]
    config = {}
    for name, job in jobs.items():
        if not isinstance(job, dict):
            raise InvalidSource("invalid_job")
        strategy = job.get("strategy", {})
        if not isinstance(strategy, dict):
            raise InvalidSource("unknown_strategy")
        if ("matrix" in strategy and
                (_candidate_runner(job.get("runs-on")) or "uses" in job)):
            config[name] = {key: job.get(key) for key in ("runs-on", "uses", "strategy")}
    return config


def inspect(base, head):
    _commit(base)
    _commit(head)
    raw = _git("diff", "--name-only", "-z", base, head, "--", ".github/workflows",
               max_output=100_000)
    paths = raw.split(b"\0")
    if paths[-1] != b"":
        raise InvalidSource("truncated_changed_paths")
    paths = paths[:-1]
    if len(paths) > MAX_CHANGED or len(set(paths)) != len(paths):
        raise InvalidSource("unbounded_or_duplicate_changes")
    new_risks = []
    for raw_path in paths:
        try:
            path = raw_path.decode("utf-8")
        except UnicodeError as exc:
            raise InvalidSource("invalid_workflow_filename") from exc
        if not path.startswith(".github/workflows/") or path.count("/") != 2:
            continue
        if not path.endswith((".yml", ".yaml")):
            # Git tooling and hosting platforms differ on extension casing.
            # Never silently certify uppercase workflow YAML as not a file.
            if path.lower().endswith((".yml", ".yaml")):
                raise InvalidSource("noncanonical_workflow_extension")
            continue
        candidate = _blob(head, path)
        if candidate is None:
            continue  # deletion cannot add matrix jobs
        original = _blob(base, path)
        if original == candidate:
            continue
        try:
            after = findings(candidate.decode("utf-8"))
        except UnicodeError as exc:
            raise InvalidSource("non_utf8_workflow") from exc
        before = []
        if original is not None:
            try:
                before = findings(original.decode("utf-8"))
            except (UnicodeError, InvalidSource):
                # An edited, non-parseable historical file cannot establish
                # a trusted same-job baseline.
                raise InvalidSource("legacy_baseline_unknown")
        prior = set(before)
        for job, reason in after:
            if (job, reason) not in prior:
                new_risks.append({"path": path, "job": job, "reason": reason})
        # Generic "unknown size" or "unsafe parallelism" debt must not
        # become a bypass for replacing its underlying matrix expression
        # with an even riskier one under the same classification.
        common_risky_jobs = {job for job, reason in after if (job, reason) in prior}
        if common_risky_jobs:
            old_jobs = yaml.load(original, Loader=UniqueLoader)["jobs"]
            new_jobs = yaml.load(candidate, Loader=UniqueLoader)["jobs"]
            if "__workflow__" in common_risky_jobs:
                if _matrix_job_config({"jobs": old_jobs}) != _matrix_job_config({"jobs": new_jobs}):
                    new_risks.append({"path": path, "job": "__workflow__",
                                      "reason": "legacy_aggregate_matrix_configuration_changed"})
                common_risky_jobs.remove("__workflow__")
            for job in sorted(common_risky_jobs):
                old_job, new_job = old_jobs[job], new_jobs[job]
                keys = ("runs-on", "uses", "strategy")
                if any(old_job.get(key) != new_job.get(key) for key in keys):
                    new_risks.append({"path": path, "job": job,
                                      "reason": "legacy_matrix_configuration_changed"})
    return {"policy": POLICY, "base_sha": base, "head_sha": head,
            "workflow_paths_checked": len(paths), "new_risks": new_risks,
            "mutation_authorized": False, "merge_authorized": False,
            "production_authorized": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect(args.base, args.head)
    except InvalidSource as exc:
        print(json.dumps({"policy": POLICY, "decision": "DENY",
                          "reason": str(exc), "mutation_authorized": False,
                          "merge_authorized": False, "production_authorized": False}))
        return 2
    result["decision"] = "DENY" if result["new_risks"] else "SOURCE_ONLY_PASS"
    print(json.dumps(result, sort_keys=True))
    return 1 if result["new_risks"] else 0


if __name__ == "__main__":
    sys.exit(main())
