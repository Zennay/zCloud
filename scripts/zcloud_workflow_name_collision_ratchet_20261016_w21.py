#!/usr/bin/env python3
"""Read-only Git object ratchet: deny newly duplicated GitHub Actions workflow names."""
import argparse
import itertools
import fnmatch
import json
import re
import subprocess
import os
import selectors
import time
from pathlib import PurePosixPath
from collections import Counter
import unicodedata

SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
MAX_FILES = 512
MAX_BLOB = 131072
MAX_TOTAL = 8 * 1024 * 1024


class InvalidInventory(ValueError):
    pass


def _git(args, cwd, max_bytes):
    """Execute a read-only Git command with a hard streaming byte/time budget."""
    if max_bytes < 1 or max_bytes > MAX_TOTAL:
        raise InvalidInventory("invalid_git_output_budget")
    proc = None
    try:
        proc = subprocess.Popen(["git", *args], cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15.0
        chunks, total = [], 0
        with selectors.DefaultSelector() as watch:
            watch.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InvalidInventory("git_read_timeout")
                if not watch.select(remaining):
                    raise InvalidInventory("git_read_timeout")
                chunk = os.read(proc.stdout.fileno(), min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise InvalidInventory("git_output_too_large")
                chunks.append(chunk)
        proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        if proc.returncode != 0:
            raise InvalidInventory("git_read_failed")
        return b"".join(chunks)
    except (OSError, subprocess.SubprocessError) as exc:
        raise InvalidInventory("git_read_failed") from exc
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def _scalar(value):
    value = value.strip()
    if not value or value.startswith(("|", ">", "&", "*", "!", "{", "[")):
        raise InvalidInventory("unsupported_workflow_name")
    if value[0] == '"':
        try:
            name, pos = json.JSONDecoder().raw_decode(value)
        except (ValueError, TypeError) as exc:
            raise InvalidInventory("unsupported_workflow_name") from exc
        trailing = value[pos:].strip()
        if not isinstance(name, str) or (trailing and not trailing.startswith("#")):
            raise InvalidInventory("unsupported_workflow_name")
    elif value[0] == "'":
        match = re.fullmatch(r"'((?:[^']|'')*)'(?:\s*#.*)?", value)
        if not match:
            raise InvalidInventory("unsupported_workflow_name")
        name = match.group(1).replace("''", "'")
    else:
        name = value.split(" #", 1)[0].rstrip()
        if "#" in name or ": " in name or name.startswith(("-", "?")):
            raise InvalidInventory("unsupported_workflow_name")
    if (not name or len(name) > 200 or
            any(ord(c) < 32 or ord(c) == 127 or
                unicodedata.category(c) in {"Cf", "Cs"} for c in name) or
            chr(36) + "{{" in name):
        raise InvalidInventory("unsupported_workflow_name")
    return name



def _ambiguous_escaped_root_key(line, indent):
    """Reject escaped YAML mapping keys that could decode to on or name."""
    stripped = line[indent:].lstrip()
    if not stripped.startswith('"'):
        return False
    key_part = stripped.split(":", 1)[0]
    return "\\" in key_part


def workflow_name(path, source):
    try:
        source = source.decode("utf-8", "strict").removeprefix("\ufeff")
    except UnicodeDecodeError as exc:
        raise InvalidInventory("non_utf8_workflow") from exc
    # YAML root mappings may be uniformly indented; do not mistake nested step
    # names for the workflow display name or silently use the filename fallback.
    significant = [line for line in source.splitlines()
                   if line.strip() and not line.lstrip().startswith("#")
                   and line.strip() not in {"---", "..."}]
    if not significant:
        raise InvalidInventory("empty_workflow_source")
    indent = min(len(line) - len(line.lstrip(" ")) for line in significant)
    if indent > 8 or significant[0].lstrip().startswith(("{", "[")):
        raise InvalidInventory("unsupported_workflow_root")
    if any(_ambiguous_escaped_root_key(line, indent) for line in significant
           if len(line) - len(line.lstrip(" ")) == indent):
        raise InvalidInventory("ambiguous_escaped_root_key")
    if any(line[indent:].lstrip().startswith(("<<:", "? ", "?[", "?{"))
           for line in significant
           if len(line) - len(line.lstrip(" ")) == indent):
        raise InvalidInventory("ambiguous_workflow_name_root")
    pattern = r"""(?m)^""" + " " * indent + r"""(?:name|'name'|"name")[ \t]*:(.*)$"""
    matches = re.findall(pattern, source)
    if len(matches) > 1:
        raise InvalidInventory("duplicate_name_field")
    return _scalar(matches[0]) if matches else str(PurePosixPath(path))



def _yaml_flow_items(value):
    """Parse simple literal YAML sequence; refuse dynamic or ambiguous items."""
    if not value.startswith("["):
        raise InvalidInventory("unsupported_workflow_run_reference")
    # Find the first unquoted closing bracket. A bracket inside a quoted item
    # or a trailing YAML comment is not the end of the sequence.
    quote, escaped, closing = "", False, None
    index = 1
    while index < len(value):
        char = value[index]
        if quote:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                if quote == "'" and index + 1 < len(value) and value[index + 1] == "'":
                    index += 1
                else:
                    quote = ""
        elif char in {"'", '"'}:
            quote = char
        elif char == "]":
            closing = index
            break
        elif char in "[{":
            raise InvalidInventory("unsupported_workflow_run_reference")
        index += 1
    if closing is None or (value[closing + 1:].strip() and
                           not value[closing + 1:].strip().startswith("#")):
        raise InvalidInventory("unsupported_workflow_run_reference")
    text = value[1:closing].strip()
    if not text:
        raise InvalidInventory("empty_workflow_run_references")
    pieces, buf, quote = [], [], ""
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and ch == "\\":
                escaped = True
            elif ch == quote:
                if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                    i += 1
                    buf.append("'")
                else:
                    quote = ""
        elif ch in {"'", '"'}:
            quote = ch
            buf.append(ch)
        elif ch == ",":
            pieces.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if quote:
        raise InvalidInventory("unsupported_workflow_run_reference")
    pieces.append("".join(buf).strip())
    if len(pieces) > 64 or any(not piece for piece in pieces):
        raise InvalidInventory("unsupported_workflow_run_reference")
    # Flow delimiters inside an unquoted YAML sequence scalar must never be
    # treated as ordinary literal characters (e.g. [Build]] garbage).
    if any(piece[0] not in {"'", '"'} and
           any(char in piece for char in "[]{}") for piece in pieces):
        raise InvalidInventory("unsupported_workflow_run_reference")
    return tuple(_scalar(piece) for piece in pieces)


# The allowlist is deliberately restricted to event names with no need for a
# nested event configuration. Unknown inline event syntax is evidence debt,
# not proof that workflow_run is absent.
SIMPLE_INLINE_EVENTS = frozenset({
    "branch_protection_rule", "check_run", "check_suite", "create", "delete",
    "deployment", "deployment_status", "discussion", "discussion_comment",
    "fork", "gollum", "issue_comment", "issues", "label", "merge_group",
    "milestone", "page_build", "public", "pull_request",
    "pull_request_review", "pull_request_review_comment",
    "pull_request_target", "push", "registry_package", "release",
    "repository_dispatch", "schedule", "status", "watch",
    "workflow_call", "workflow_dispatch",
})


def _literal_inline_non_run_events(value):
    """Accept only an explicit, unaliased list/scalar of non-workflow_run events."""
    try:
        if value.startswith("["):
            items = _yaml_flow_items(value)
        else:
            items = (_scalar(value),)
    except InvalidInventory:
        return False
    return bool(items) and all(item in SIMPLE_INLINE_EVENTS for item in items)


def workflow_run_targets(source):
    """Read literal workflow_run references, fail closed only on NEW unsupported syntax.

    None indicates an unverified trigger; baseline-only debt remains visible.
    This bounded lexer accepts ordinary block YAML and simple inline lists.
    """
    text = source.decode("utf-8", "strict").removeprefix("\ufeff")
    significant = [line for line in text.splitlines()
                   if line.strip() and not line.lstrip().startswith("#")
                   and line.strip() not in {"---", "..."}]
    if not significant:
        return None
    root = min(len(line) - len(line.lstrip(" ")) for line in significant)
    top = re.compile(r"""^(?:on|'on'|"on")[ \t]*:(.*)$""")
    child = re.compile(r"""^(?:workflow_run|'workflow_run'|"workflow_run")[ \t]*:(.*)$""")
    target = re.compile(r"""^(?:workflows|'workflows'|"workflows")[ \t]*:(.*)$""")
    lines = text.splitlines()
    results = []
    event_blocks = []
    for index, line in enumerate(lines):
        if len(line) - len(line.lstrip(" ")) != root:
            continue
        root_key = line[root:].lstrip()
        if _ambiguous_escaped_root_key(line, root):
            return None
        # YAML mapping merges / explicit complex keys can inject an on field.
        # We cannot establish that workflow_run is absent without expansion.
        if root_key.startswith(("<<:", "? ", "?[", "?{")):
            return None
        match = top.match(line[root:])
        if match:
            event_blocks.append((index, match.group(1).strip()))
    if len(event_blocks) > 1:
        return None
    if not event_blocks:
        return ()
    start, remainder = event_blocks[0]
    if remainder and not remainder.startswith("#"):
        return () if _literal_inline_non_run_events(remainder) else None
    event_end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if (line.strip() and not line.lstrip().startswith("#")
                and len(line) - len(line.lstrip(" ")) <= root):
            event_end = index
            break
    seen_workflow_run = False
    for index in range(start + 1, event_end):
        line = lines[index]
        level = len(line) - len(line.lstrip(" "))
        stripped = line[level:]
        if not stripped or stripped.startswith("#"):
            continue
        # A YAML merge/complex key under on can synthesize a workflow_run
        # event unseen by this literal-only parser.
        if level > root and (stripped.startswith(("<<:", "? ", "?[", "?{")) or
                             _ambiguous_escaped_root_key(line, level)):
            return None
        match = child.match(stripped)
        if not match or level <= root:
            continue
        if seen_workflow_run:
            # YAML mappings with repeated trigger keys have ambiguous semantics.
            return None
        seen_workflow_run = True
        if match.group(1).strip() not in {"", "#"}:
            return None
        run_end = event_end
        for k in range(index + 1, event_end):
            nxt = lines[k]
            if (nxt.strip() and not nxt.lstrip().startswith("#")
                    and len(nxt) - len(nxt.lstrip(" ")) <= level):
                run_end = k
                break
        found = False
        for j in range(index + 1, run_end):
            row = lines[j]
            depth = len(row) - len(row.lstrip(" "))
            if depth > level and _ambiguous_escaped_root_key(row, depth):
                return None
            m = target.match(row[depth:])
            if not m or depth <= level:
                continue
            if found:
                return None
            found = True
            value = m.group(1).strip()
            try:
                if value.startswith("["):
                    results.extend(_yaml_flow_items(value))
                elif value and not value.startswith("#"):
                    results.append(_scalar(value))
                else:
                    values = []
                    for k in range(j + 1, run_end):
                        nxt = lines[k]
                        d = len(nxt) - len(nxt.lstrip(" "))
                        if nxt.strip() and d <= depth:
                            break
                        stripped = nxt.strip()
                        if not stripped or stripped.startswith("#"):
                            continue
                        if not stripped.startswith("- "):
                            return None
                        values.append(_scalar(stripped[2:].strip()))
                    if not values or len(values) > 64:
                        return None
                    results.extend(values)
            except InvalidInventory:
                return None
        if not found:
            return None
    return tuple(sorted(set(results)))



def literal_workflow_filter(value):
    """Return only a *literal* workflow_run target; never approximate glob matching.

    GitHub accepts extglob/wildcard syntax in workflow_run.workflows. This
    conservative source gate must not mistake a pattern for one workflow.
    Escaping a reserved symbol is allowed; unknown backslash escapes are not.
    """
    reserved = set("*?+!@[]{}()")
    result = []
    escaped = False
    for char in value:
        if escaped:
            if char not in reserved and char != "\\":
                return None
            result.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char in reserved:
            return None
        else:
            result.append(char)
    if escaped:
        return None
    return "".join(result)


def reference_risks(names, references):
    counts = Counter(names.values())
    findings = set()
    for path, targets in references.items():
        if targets is None:
            findings.add((path, "unknown_source", "<unparsed>"))
            continue
        for value in targets:
            literal = literal_workflow_filter(value)
            if literal is None:
                findings.add((path, "pattern_needs_review", value))
            elif counts[literal] == 0:
                findings.add((path, "missing_target", literal))
            elif counts[literal] > 1:
                findings.add((path, "ambiguous_target", literal))
    return findings



def _could_match_historical_pattern(name, pattern):
    """Conservatively screen additions against an unresolved legacy pattern.

    Python's simple glob semantics can prove an ordinary * or ? pattern is
    disjoint. GitHub's extended glob grammar is broader: when its syntax is
    present do not pretend fnmatch is an authoritative negative proof.
    """
    if any(char in pattern for char in "!+@(){}[]\\"):
        return True
    return fnmatch.fnmatchcase(name, pattern)


def newly_activated_legacy_targets(base, candidate, base_refs, candidate_refs):
    """Find candidate destinations that activate an unchanged historic trigger.

    An unchanged reference previously missing its destination, or an old
    wildcard, can suddenly acquire a new target when a different workflow is
    added/renamed. Comparing only finding-set deltas would miss that change.
    """
    additions = [(path, name) for path, name in candidate.items()
                 if base.get(path) != name]
    if not additions:
        return set()
    previous_counts = Counter(base.values())
    findings = set()
    for consumer, target_names in candidate_refs.items():
        old_targets = base_refs.get(consumer)
        if old_targets is None or target_names is None:
            continue
        for target in target_names:
            if target not in old_targets:
                continue
            exact = literal_workflow_filter(target)
            for path, display in additions:
                if exact is None:
                    if _could_match_historical_pattern(display, target):
                        findings.add((consumer, "legacy_pattern_new_target", path))
                elif previous_counts[exact] == 0 and display == exact:
                    findings.add((consumer, "missing_target_activated", path))
    return findings


def cyclic_reference_edges(names, references):
    """Find only resolvable workflow_run edges inside cyclic components.

    Report path identities, not display names; legacy cycles remain baseline
    debt but new cyclic edges fail closed. Ambiguous/missing names are separately
    handled by reference_risks and never trusted as graph vertices.
    """
    counts = Counter(names.values())
    unique_names = {display: path for path, display in names.items()
                    if counts[display] == 1}
    graph = {path: set() for path in names}
    for source, targets in references.items():
        if source not in graph or targets is None:
            continue
        for target_name in targets:
            literal = literal_workflow_filter(target_name)
            dest = unique_names.get(literal) if literal is not None else None
            if dest is not None:
                graph[source].add(dest)

    # Tarjan SCC: O(vertices+edges); <= MAX_FILES vertices by Git admission.
    index = 0
    indices, lowlink, stack, on_stack, components = {}, {}, [], set(), []

    def visit(node):
        nonlocal index
        indices[node] = lowlink[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for next_node in sorted(graph[node]):
            if next_node not in indices:
                visit(next_node)
                lowlink[node] = min(lowlink[node], lowlink[next_node])
            elif next_node in on_stack:
                lowlink[node] = min(lowlink[node], indices[next_node])
        if lowlink[node] == indices[node]:
            component = set()
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.add(member)
                if member == node:
                    break
            components.append(component)

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    result = set()
    for component in components:
        if len(component) > 1:
            for source in component:
                result.update((source, dest) for dest in graph[source]
                              if dest in component)
        else:
            node = next(iter(component))
            if node in graph[node]:
                result.add((node, node))
    return result


def pairs(items):
    by_name = {}
    for path, name in items.items():
        # The GitHub UI can render canonically equivalent Unicode names alike.
        # Compare display identities in NFC without rewriting GitHub's raw names.
        canonical_name = unicodedata.normalize("NFC", name)
        by_name.setdefault(canonical_name, []).append(path)
    return {(name, a, b) for name, paths in by_name.items()
            for a, b in itertools.combinations(sorted(paths), 2)}


def judge(base, candidate, base_refs=None, candidate_refs=None,
          *, base_blobs=None, candidate_blobs=None):
    baseline_reference_risks = reference_risks(base, base_refs or {})
    candidate_reference_risks = reference_risks(candidate, candidate_refs or {})
    baseline_cycle_edges = cyclic_reference_edges(base, base_refs or {})
    candidate_cycle_edges = cyclic_reference_edges(candidate, candidate_refs or {})
    new = sorted(pairs(candidate) - pairs(base))
    reference_new = sorted(candidate_reference_risks - baseline_reference_risks)
    if base_blobs is not None and candidate_blobs is not None:
        # A previously unparseable workflow is grandfathered only while its
        # exact Git blob stays unchanged. Otherwise unverified new trigger
        # semantics could hide behind the same generic unknown_source marker.
        for path, targets in (candidate_refs or {}).items():
            if targets is None and (base_refs or {}).get(path) is None:
                if (path in base_blobs and path in candidate_blobs and
                        base_blobs[path] != candidate_blobs[path]):
                    reference_new.append(
                        (path, "unknown_source_changed", "<git-blob-changed>"))
        reference_new.sort()
    reference_new.extend(sorted(newly_activated_legacy_targets(
        base, candidate, base_refs or {}, candidate_refs or {})))
    reference_new = sorted(set(reference_new))
    cycle_new = sorted(candidate_cycle_edges - baseline_cycle_edges)
    return {
        "policy": "workflow-name-no-new-collision-v1",
        "status": "deny" if new or reference_new or cycle_new else "pass",
        "added_collisions": [{"name": n, "paths": [a, b]} for n, a, b in new[:20]],
        "added_collision_count": len(new),
        "added_reference_risk_count": len(reference_new),
        "added_cycle_edge_count": len(cycle_new),
        "added_cycle_edges": [dict(source=source, target=target)
                              for source, target in cycle_new[:20]],
        "added_reference_risks": [dict(workflow=path, reason=reason, target=name)
                                  for path, reason, name in reference_new[:20]],
        "baseline_collision_count": len(pairs(base)),
        "candidate_collision_count": len(pairs(candidate)),
        "baseline_reference_risk_count": len(baseline_reference_risks),
        "candidate_reference_risk_count": len(candidate_reference_risks),
        "baseline_reference_risks": [
            dict(workflow=path, reason=reason, target=name)
            for path, reason, name in sorted(baseline_reference_risks)[:10]],
        "baseline_cycle_edge_count": len(baseline_cycle_edges),
        "candidate_cycle_edge_count": len(candidate_cycle_edges),
        "merge_authorized": False,
        "production_mutation_authorized": False,
    }


def read_inventory(sha, *, cwd, include_blob_ids=False):
    if not SHA.fullmatch(sha):
        raise InvalidInventory("invalid_sha")
    resolved = _git(["rev-parse", "--verify", sha + "^{commit}"], cwd, 48)
    if resolved.strip().decode("ascii", "strict") != sha:
        raise InvalidInventory("revision_not_commit")
    raw = _git(["ls-tree", "-rz", "--full-tree", sha, "--",
                ".github/workflows"], cwd, MAX_FILES * 200)
    names, references, blob_ids, total = {}, {}, {}, 0
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        if len(names) >= MAX_FILES:
            raise InvalidInventory("too_many_workflows")
        try:
            meta, raw_path = entry.split(b"\t", 1)
            mode, kind, blobsha = meta.decode("ascii").split(" ")
            path = raw_path.decode("ascii")
        except (UnicodeError, ValueError) as exc:
            raise InvalidInventory("invalid_git_tree") from exc
        if (mode not in {"100644", "100755"} or kind != "blob"
                or not SHA.fullmatch(blobsha)):
            raise InvalidInventory("nonregular_workflow")
        if not WORKFLOW.fullmatch(path) or path in names:
            raise InvalidInventory("unexpected_workflow_path")
        blob_size = _git(["cat-file", "-s", blobsha], cwd, 24)
        if not blob_size.strip().isdigit() or int(blob_size) > MAX_BLOB:
            raise InvalidInventory("workflow_blob_too_large")
        blob = _git(["cat-file", "blob", blobsha], cwd, MAX_BLOB)
        total += len(blob)
        if total > MAX_TOTAL:
            raise InvalidInventory("workflow_sources_too_large")
        names[path] = workflow_name(path, blob)
        references[path] = workflow_run_targets(blob)
        blob_ids[path] = blobsha
    if not names:
        raise InvalidInventory("empty_workflow_inventory")
    return (names, references, blob_ids) if include_blob_ids else (names, references)


def read_names(sha, *, cwd):
    return read_inventory(sha, cwd=cwd)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", default=".")
    opts = parser.parse_args()
    try:
        base, base_refs, base_blobs = read_inventory(
            opts.base, cwd=opts.root, include_blob_ids=True)
        head, head_refs, head_blobs = read_inventory(
            opts.head, cwd=opts.root, include_blob_ids=True)
        result = judge(base, head, base_refs, head_refs,
                       base_blobs=base_blobs, candidate_blobs=head_blobs)
        result["workflow_count_base"] = len(base)
        result["workflow_count_head"] = len(head)
        result["source_commit_base"] = opts.base
        result["source_commit_head"] = opts.head
    except InvalidInventory as exc:
        result = {
            "policy": "workflow-name-no-new-collision-v1", "status": "deny",
            "error_code": str(exc), "merge_authorized": False,
            "production_mutation_authorized": False,
        }
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
