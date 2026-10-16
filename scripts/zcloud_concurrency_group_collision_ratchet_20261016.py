#!/usr/bin/env python3
"""Read-only admission against new cross-workflow cancellation and self-deadlock."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
import yaml

SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.\-/]+\.ya?ml\Z")
MAX_PATHS = 512
MAX_TREE_BYTES = 512 * 1024
MAX_BLOB_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_GROUP_LENGTH = 256
MAX_FINDINGS = 20000
MAX_JOBS_PER_WORKFLOW = 256
MAX_GROUP_ENTRIES = 4096
# Only these stable GitHub contexts have a constrained source-level identity.
# Event payload, inputs, matrix, needs and custom contexts can alias another
# group at runtime even if their selector source text differs. Treat newly
# introduced values as unresolved, not proof of group uniqueness.
STATIC_IDENTITY_SELECTORS = frozenset({
    # github.workflow is substituted using the explicit static workflow name;
    # another workflow with the same name is still compared as a collision.
    # github.run_id is unique per workflow run (including run-scoped proofs).
    "github.workflow", "github.run_id",
})

class AdmissionError(ValueError):
    pass

class StrictLoader(yaml.SafeLoader):
    """SafeLoader with duplicate-key rejection and non-boolean 'on' key."""
StrictLoader.yaml_implicit_resolvers = {
    k: [(tag, p) for tag, p in vs if tag != "tag:yaml.org,2002:bool"]
    for k, vs in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
StrictLoader.add_implicit_resolver("tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE), list("TtFf"))

def _strict_mapping(loader, node):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, (str, int, float, bool)) or key in result:
            raise AdmissionError("duplicate or non-scalar YAML key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result
StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_mapping)

def git_bytes(*args: str, limit: int) -> bytes:
    """Bound stdout/stderr while streaming; bound wall clock and child lifetime."""
    # Ignore all inherited Git knobs, including alternate object stores,
    # namespaces, config injection, SSH helpers and trace sinks.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    p = subprocess.Popen(["git", "-c", "core.pager=cat", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, env=env)
    sel = selectors.DefaultSelector()
    parts = {"out": bytearray(), "err": bytearray()}
    try:
        assert p.stdout and p.stderr
        sel.register(p.stdout, selectors.EVENT_READ, "out")
        sel.register(p.stderr, selectors.EVENT_READ, "err")
        deadline = time.monotonic() + 15
        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AdmissionError("git deadline exceeded")
            for key, _ in sel.select(remaining):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    sel.unregister(key.fileobj)
                    continue
                buf = parts[key.data]
                buf.extend(chunk)
                if len(buf) > (limit if key.data == "out" else 8192):
                    raise AdmissionError("git output limit exceeded")
        if p.wait(timeout=max(0.1, deadline - time.monotonic())) != 0:
            raise AdmissionError("git command failed")
        return bytes(parts["out"])
    except (subprocess.TimeoutExpired, OSError) as e:
        raise AdmissionError("git execution failed") from e
    finally:
        sel.close()
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=3)
        if p.stdout:
            p.stdout.close()
        if p.stderr:
            p.stderr.close()

def _sha(value: str) -> str:
    if not SHA.fullmatch(value):
        raise AdmissionError("invalid immutable commit SHA")
    return value

def _git_ref(*args: str) -> str:
    return _sha(git_bytes(*args, limit=200).decode("ascii").strip())

def assert_commits(base: str, head: str) -> None:
    base, head = _sha(base), _sha(head)
    if _git_ref("rev-parse", "HEAD") != head:
        raise AdmissionError("working checkout is not exact PR head")
    for ref in (base, head):
        if _git_ref("rev-parse", "--verify", ref + "^{commit}") != ref:
            raise AdmissionError("not a commit")
    if _git_ref("merge-base", base, head) != base:
        raise AdmissionError("head is not descendant of immutable base")

def _workflow_entries(commit: str):
    raw = git_bytes("ls-tree", "-r", "-z", commit, "--", ".github/workflows",
                    limit=MAX_TREE_BYTES)
    out = []
    for item in raw.split(b"\x00"):
        if not item:
            continue
        try:
            meta, path = item.split(b"\t", 1)
            mode, kind, sha = meta.decode("ascii").split(" ")
            name = path.decode("ascii")
        except (ValueError, UnicodeError) as e:
            raise AdmissionError("unreadable workflow tree entry") from e
        if not name.endswith((".yml", ".yaml")):
            continue
        if not WORKFLOW.fullmatch(name) or ".." in Path(name).parts:
            raise AdmissionError("unsafe workflow source path")
        if mode not in ("100644", "100755") or kind != "blob" or not SHA.fullmatch(sha):
            raise AdmissionError("unsafe workflow object (including symlink)")
        out.append((name, sha))
        if len(out) > MAX_PATHS:
            raise AdmissionError("workflow file budget exceeded")
    return out

def _normalize_group(group: str, workflow_name: str) -> str:
    """Compare simple evaluated expressions rather than their YAML whitespace.

    github.workflow resolves to the *name* of the workflow, not its path.
    A duplicate name in another file must never silently bypass this gate.
    Complex GitHub expressions are left literal and require separate review.
    """
    def replace(match):
        expr = match.group(1).strip()
        if re.fullmatch(r"github\.workflow", expr, re.IGNORECASE):
            return workflow_name
        if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_0-9-]+)+", expr):
            return "${{" + expr.casefold() + "}}"
        return match.group(0)
    return re.sub(r"\$\{\{([^{}]{0,256})\}\}", replace, group)

def _is_opaque_expression(group: str) -> bool:
    """Unmodelled GitHub expressions cannot be certified from a static token.

    Leave existing expressions for owner review, but never let changed/new
    opaque expressions silently bypass collision detection by text inequality.
    Only a narrow documented allowlist of GitHub runtime identities is
    compared statically; arbitrary dotted selectors remain unresolved.
    """
    expression = re.compile(r"\$\{\{([^{}]{0,256})\}\}")
    residual = expression.sub("", group)
    if "${{" in residual or "}}" in residual:
        return True
    for match in expression.finditer(group):
        expr = match.group(1).strip()
        # A syntactically simple dotted selector is NOT necessarily a
        # statically unique runtime identity. For example, github.event.number
        # and github.event.pull_request.number may resolve to equal values.
        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_0-9-]+)+", expr
        ) or expr.casefold() not in STATIC_IDENTITY_SELECTORS:
            return True
    return False


def _group_entry(path: str, scope: str, value: object, workflow_name: str, *, name_explicit: bool):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AdmissionError("invalid concurrency mapping")
    group = value.get("group")
    cancel = value.get("cancel-in-progress", False)
    queue = value.get("queue", "single")
    if queue not in ("single", "max") or not isinstance(queue, str):
        raise AdmissionError("invalid concurrency queue mode")
    if queue == "max" and cancel is not False:
        raise AdmissionError("queue max conflicts with cancel-in-progress")
    if not isinstance(group, str) or not (1 <= len(group) <= MAX_GROUP_LENGTH):
        raise AdmissionError("non-literal or unbounded concurrency group")
    if re.search(r"[\x00-\x1f\x7f]", group):
        raise AdmissionError("control characters in group")
    if cancel is True:
        mode = "true"
    elif cancel is False:
        mode = "false"
    elif isinstance(cancel, str) and "${{" in cancel and "}}" in cancel:
        mode = "dynamic"
    else:
        raise AdmissionError("invalid cancel-in-progress value")
    # GitHub evaluates workflow-name interpolations and ignores simple
    # expression whitespace; collisions can arise with duplicate names.
    resolved_group = _normalize_group(group, workflow_name)
    token = hashlib.sha256(resolved_group.casefold().encode("utf-8")).hexdigest()
    uses_workflow_name = re.search(r"\$\{\{\s*github\.workflow\s*\}\}", group, re.IGNORECASE)
    unresolved_workflow_name = bool(uses_workflow_name and (
        not name_explicit or "${{" in workflow_name or "}}" in workflow_name
    ))
    # Do not collapse distinct dynamic running-cancellation predicates into
    # the same literal 'dynamic' bucket: changing a predicate can increase
    # the chance of cancelling a different workflow's active run.
    cancel_digest = (hashlib.sha256(cancel.encode("utf-8")).hexdigest()
                     if mode == "dynamic" else "")
    return (token, path, scope, mode, queue, cancel_digest,
            _is_opaque_expression(group) or unresolved_workflow_name)

def _entries_for_file(path: str, contents: bytes):
    try:
        body = yaml.load(contents.decode("utf-8"), Loader=StrictLoader)
    except (UnicodeError, yaml.YAMLError, AdmissionError, RecursionError) as e:
        raise AdmissionError("ambiguous or invalid workflow YAML") from e
    if not isinstance(body, dict):
        raise AdmissionError("workflow must be a YAML mapping")
    name_explicit = "name" in body
    name = body.get("name", path)
    if not isinstance(name, str) or not (1 <= len(name) <= 256):
        raise AdmissionError("non-literal or invalid workflow name")
    entries = []
    root = _group_entry(path, "workflow", body.get("concurrency"), name,
                        name_explicit=name_explicit)
    if root:
        entries.append(root)
    jobs = body.get("jobs", {})
    if not isinstance(jobs, dict):
        raise AdmissionError("workflow jobs must be mapping")
    if len(jobs) > MAX_JOBS_PER_WORKFLOW:
        raise AdmissionError("workflow job count exceeds bounded budget")
    for job_id, definition in jobs.items():
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,120}", job_id):
            raise AdmissionError("unsafe workflow job ID")
        if not isinstance(definition, dict):
            raise AdmissionError("workflow job must be mapping")
        item = _group_entry(path, "job:" + job_id, definition.get("concurrency"),
                            name, name_explicit=name_explicit)
        if item:
            entries.append(item)
    return entries

def findings(commit: str, *, include_opaque: bool = False):
    grouped = defaultdict(list)
    opaque = set()
    total = 0
    total_entries = 0
    for path, sha in _workflow_entries(commit):
        blob = git_bytes("cat-file", "blob", sha, limit=MAX_BLOB_BYTES)
        total += len(blob)
        if total > MAX_TOTAL_BYTES:
            raise AdmissionError("aggregate workflow source budget exceeded")
        entries = _entries_for_file(path, blob)
        total_entries += len(entries)
        if total_entries > MAX_GROUP_ENTRIES:
            raise AdmissionError("repository concurrency entry budget exceeded")
        for token, p, scope, mode, queue, cancel_digest, is_opaque in entries:
            grouped[token].append((p, scope, mode, queue, cancel_digest))
            if is_opaque:
                opaque.add((p, scope, token))
    out = set()
    for token, members in grouped.items():
        # Identical workflow paths cannot create cross-workflow collisions;
        # deduplicate within each path before any quadratic comparison.
        members = list(dict.fromkeys(members))
        for i, first in enumerate(members):
            for second in members[i + 1:]:
                # Even cancel-in-progress:false replaces a previous *pending*
                # run under the default queue:single; queue:max can cancel
                # once its bounded capacity is exhausted. Same-group flows
                # therefore cannot be assumed independent across workflows.
                if first[0] == second[0]:
                    # Within one workflow, sharing a group across the top-level
                    # workflow and a nested job can block the job behind its
                    # parent lock. Distinct sibling jobs may intentionally
                    # serialize and remain outside this narrow self-deadlock
                    # policy.
                    if "workflow" not in (first[1], second[1]):
                        continue
                out.add((token, *sorted((first, second))))
                if len(out) > MAX_FINDINGS:
                    raise AdmissionError("concurrency finding budget exceeded")
    return (out, opaque) if include_opaque else out

def _pair_identity(pair):
    """Stable source identity separate from monotonic risk attributes."""
    group_hash, first, second = pair
    return group_hash, (first[0], first[1]), (second[0], second[1])


def _non_escalating_endpoint(previous, candidate) -> bool:
    """Allow only equal risk or provably safer edits of an existing pair.

    'single' to 'max' preserves GitHub's finite capacity but stops routine
    replacement of pending runs. It cannot remove all overload risk, so the
    pair remains a legacy finding requiring owner review. 'true' or dynamic
    cancel to literal false is also monotonic; other changes fail closed.
    """
    if previous[:2] != candidate[:2]:
        return False
    old_mode, old_queue = previous[2], previous[3]
    new_mode, new_queue = candidate[2], candidate[3]
    if (old_mode == new_mode == "dynamic"
            and previous[4] != candidate[4]):
        return False
    if old_mode != new_mode and new_mode != "false":
        return False
    if old_queue != new_queue and (old_queue, new_queue) != ("single", "max"):
        return False
    return True


def _non_escalating_pair(previous, candidate) -> bool:
    return (
        _pair_identity(previous) == _pair_identity(candidate)
        and _non_escalating_endpoint(previous[1], candidate[1])
        and _non_escalating_endpoint(previous[2], candidate[2])
    )


def _activation_fingerprints(commit: str, members: set) -> dict:
    """Hash event triggers and job 'if' for existing collision participants.

    A legacy writer gaining a new automatic trigger or a weaker job guard
    increases exposure despite unchanged group and cancel mode. Changes
    require the original workflow owner to review, not silent admission.
    """
    tree = dict(_workflow_entries(commit))
    by_path = defaultdict(set)
    for path, scope in members:
        by_path[path].add(scope)
    out = {}
    for path, scopes in sorted(by_path.items()):
        sha = tree.get(path)
        if sha is None:
            continue
        raw = git_bytes("cat-file", "blob", sha, limit=MAX_BLOB_BYTES)
        try:
            body = yaml.load(raw.decode("utf-8"), Loader=StrictLoader)
        except (UnicodeError, yaml.YAMLError, AdmissionError, RecursionError) as e:
            raise AdmissionError("unsafe trigger evidence source") from e
        if not isinstance(body, dict):
            raise AdmissionError("unsafe trigger evidence structure")
        jobs = body.get("jobs", {})
        if not isinstance(jobs, dict):
            raise AdmissionError("invalid trigger evidence jobs")
        for scope in scopes:
            conditional = None
            if scope.startswith("job:"):
                job = jobs.get(scope[4:])
                if not isinstance(job, dict):
                    raise AdmissionError("missing legacy collision job")
                conditional = job.get("if")
            source = {"events": body.get("on"), "job_if": conditional}
            try:
                serialized = json.dumps(source, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=True, default=str)
            except (TypeError, ValueError, RecursionError) as e:
                raise AdmissionError("unreadable trigger evidence") from e
            out[(path, scope)] = hashlib.sha256(serialized.encode("ascii")).hexdigest()
    return out


def check(base: str, head: str):
    assert_commits(base, head)
    old, previous_opaque = findings(base, include_opaque=True)
    new, candidate_opaque = findings(head, include_opaque=True)
    assert_commits(base, head)
    new_opaque = candidate_opaque - previous_opaque
    if new_opaque:
        paths = sorted({item[0] for item in new_opaque})
        raise AdmissionError(
            f"new or rewritten opaque GitHub concurrency expressions "
            f"({len(new_opaque)}): {', '.join(paths[:10])}"
        )
    existing = {_pair_identity(pair): pair for pair in old}
    escalations = []
    for pair in new:
        previous = existing.get(_pair_identity(pair))
        if previous is None or not _non_escalating_pair(previous, pair):
            escalations.append(pair)
    if escalations:
        paths = sorted({m[0] for risk in escalations for m in risk[1:]})
        raise AdmissionError(
            f"new or escalating cross-workflow cancellation collisions "
            f"({len(escalations)}): {', '.join(paths[:10])}"
        )
    legacy_members = {(item[0], item[1]) for pair in old for item in pair[1:]}
    new_members = {(item[0], item[1]) for pair in new for item in pair[1:]}
    retained = legacy_members & new_members
    before = _activation_fingerprints(base, retained)
    after = _activation_fingerprints(head, retained)
    changes = sorted(key for key in retained if before.get(key) != after.get(key))
    assert_commits(base, head)
    if changes:
        paths = sorted({path for path, _ in changes})
        raise AdmissionError(
            f"legacy concurrency member event/condition changed without owner review "
            f"({len(changes)}): {', '.join(paths[:10])}"
        )
    return len(old), len(new)

def inventory(base: str, head: str) -> dict:
    """Human-review queue with immutable evidence, never production authority."""
    check(base, head)
    legacy, baseline_opaque = findings(base, include_opaque=True)
    _, candidate_opaque = findings(head, include_opaque=True)
    if len(legacy) > 128:
        raise AdmissionError("human review inventory budget exceeded")
    assert_commits(base, head)
    records = []
    clusters = defaultdict(lambda: {"paths": set(), "members": set(), "pair_count": 0})
    for group_hash, first, second in sorted(legacy):
        members = (first, second)
        records.append({
            "group_sha256": group_hash,
            "members": [
                {"workflow_path": item[0], "scope": item[1],
                 "cancel_in_progress": item[2], "queue": item[3],
                 "cancel_expression_sha256": item[4]}
                for item in members
            ],
            "reviewer": "UNASSIGNED",
            "owner": "UNASSIGNED",
            "disposition": "UNREVIEWED",
        })
        cluster = clusters[group_hash]
        cluster["pair_count"] += 1
        for item in members:
            cluster["paths"].add(item[0])
            cluster["members"].add((item[0], item[1]))
    summaries = [
        {
            "group_sha256": group_hash,
            "pair_count": cluster["pair_count"],
            "unique_workflow_count": len(cluster["paths"]),
            "unique_scopes": len(cluster["members"]),
            "workflow_paths": sorted(cluster["paths"]),
            "owner": "UNASSIGNED",
            "reviewer": "UNASSIGNED",
            "disposition": "UNREVIEWED",
            "reviewed": False,
            "production_mutation_authorized": False,
        }
        for group_hash, cluster in sorted(clusters.items())
    ]
    return {
        "evidence_type": "immutable_source_only",
        "base_commit_sha": base,
        "head_commit_sha": head,
        "legacy_collision_count": len(records),
        "legacy_opaque_expression_count": len(baseline_opaque),
        "candidate_opaque_expression_count": len(candidate_opaque),
        "static_expression_coverage_complete": not candidate_opaque,
        "no_new_opaque_expressions": True,
        "unresolved_expression_reviews": [
            {"workflow_path": path, "scope": scope, "group_sha256": token,
             "owner": "UNASSIGNED", "reviewer": "UNASSIGNED",
             "disposition": "UNREVIEWED"}
            for path, scope, token in sorted(baseline_opaque)
        ],
        "legacy_group_count": len(summaries),
        "unique_affected_workflows": len({
            path for cluster in clusters.values() for path in cluster["paths"]
        }),
        "group_summaries": summaries,
        "new_collisions_allowed": 0,
        "reviewed": False,
        "merge_authorized": False,
        "production_mutation_authorized": False,
        "review_items": records,
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--inventory-json", action="store_true",
                        help="Print only SHA-bound review evidence, never release authorization")
    args = parser.parse_args()
    try:
        if args.inventory_json:
            print(json.dumps(inventory(args.base, args.head), sort_keys=True,
                             separators=(",", ":"), ensure_ascii=True))
            return 0
        before, after = check(args.base, args.head)
    except AdmissionError as e:
        print(f"ZCLOUD_CONCURRENCY_COLLISION_SOURCE_DENY: {e}", file=sys.stderr)
        return 2
    print(f"ZCLOUD_CONCURRENCY_COLLISION_SOURCE_ONLY_GREEN=1 baseline={before} candidate={after}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
