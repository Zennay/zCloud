#!/usr/bin/env python3
"""Read-only no-new-debt admission for GitHub-hosted job timeout policy.

Scope is *only* literal, recognized GitHub-managed runner labels. Custom,
self-hosted, dynamic and reusable jobs are owned by separate policies.
No workflow is changed; no token/API/write authority is requested.
"""
import argparse
import hashlib
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


def hosted_runner(value):
    """Only explicit GitHub-managed labels count as hosted proof."""
    if isinstance(value, str):
        return HOSTED.fullmatch(value) is not None
    if isinstance(value, list) and 0 < len(value) <= 8:
        return all(isinstance(x, str) and HOSTED.fullmatch(x) for x in value)
    return False


def violations(source):
    try:
        document = yaml.load(source, Loader=StrictLoader)
    except (yaml.YAMLError, UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise InvalidSource("unparseable_workflow_yaml") from exc
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
        raise InvalidSource("invalid_workflow_jobs")
    bad = set()
    for job_id, job in document["jobs"].items():
        if not isinstance(job_id, str) or not isinstance(job, dict):
            raise InvalidSource("invalid_job_definition")
        if "uses" in job:  # reusable jobs are intentionally out of scope
            continue
        runner = job.get("runs-on")
        if not hosted_runner(runner):
            continue
        if "timeout-minutes" not in job:
            reason = "missing_timeout"
            observed = "<missing>"
        else:
            timeout = job["timeout-minutes"]
            if isinstance(timeout, bool) or not isinstance(timeout, int):
                reason = "dynamic_or_invalid_timeout"
            elif timeout < 1 or timeout > 30:
                reason = "timeout_outside_1_to_30"
            else:
                continue
            try:
                observed = repr(timeout)
            except RecursionError as exc:
                raise InvalidSource("recursive_timeout_value") from exc
        runner_name = runner if isinstance(runner, str) else ",".join(runner)
        # Changes within the same broad risk category are *not* automatically
        # grandfathered: changing 60 -> 360 minutes would increase runner cost
        # while still looking like the same "timeout_outside" finding.
        # Store only a digest, never interpolate arbitrary workflow input into
        # status/log output. A legacy finding passes only when unchanged.
        fingerprint = hashlib.sha256(observed.encode("utf-8")).hexdigest()[:20]
        bad.add((job_id, runner_name, fingerprint, reason))
    return bad


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
        print("ZCLOUD_HOSTED_TIMEOUT=DENY invalid_source=" + str(exc))
        return 2
    print(
        "ZCLOUD_HOSTED_TIMEOUT="
        + ("DENY" if denied else "PASS")
        + f" changed_workflows={examined} existing_debt={baseline}"
        + f" current_debt={current} new_debt={len(denied)}"
        + " mutation_performed=false"
    )
    for path, job, runner, reason in denied[:25]:
        print(f"NEW_HOSTED_TIMEOUT_DEBT path={path} job={job} runner={runner} reason={reason}")
    return 1 if denied else 0


if __name__ == "__main__":
    sys.exit(main())
