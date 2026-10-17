#!/usr/bin/env python3
"""Non-authorizing, offline no-new-debt check for workflow run-shell expressions.

This checks committed Git trees, never reads working-tree workflow files, and
never launches jobs. It is a conservative syntactic tripwire, NOT a YAML parser,
runner attestation, production/release approval, or full injection analysis.
"""
from __future__ import annotations

import argparse
import hashlib
import html
from collections import Counter
import json
import re
import subprocess

MAX_WORKFLOWS = 512
MAX_TREE_ENTRIES = 16384
MAX_BLOB_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_EXPRESSIONS = 8192
SHA = re.compile(r"^[0-9a-f]{40}$")
RUN = re.compile(r"^( *)(?:-\s*)?(?:run|['\"]run['\"])\s*:\s*(.*)$")
RUN_ESCAPED_KEY = re.compile(
    r'^( *)(?:-\s*)?"(?:r|\\u0072|\\x72|\\U00000072)(?:u|\\u0075|\\x75|\\U00000075)(?:n|\\u006e|\\x6e|\\U0000006e)"\s*:\s*(.*)$',
    re.I,
)
YAML_QUOTED_ESCAPE = re.compile(r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})")
UNTRUSTED = re.compile(r"\b(?:github\.event\b|github\.head_ref\b|github\.ref_name\b|github\.ref\b|github\.base_ref\b|github\.workflow_ref\b|github\.workflow\b|github\.token\b|github\.action_ref\b|inputs\b|env\b|vars\b|secrets\b)", re.I)
DERIVED_OUTPUT_UNTRUSTED = re.compile(r"\b(?:steps\.(?:[A-Za-z_][A-Za-z0-9_-]*|\*)\.outputs\b|needs\.(?:[A-Za-z_][A-Za-z0-9_-]*|\*)\.outputs\b|matrix\b)", re.I)
ROOT_CONTEXT_JSON = re.compile(r"\btojson\s*\(\s*(?:github|steps|needs|env|vars|secrets)\s*\)", re.I)
# Serializing a whole child job/step includes its outputs even if the
# expression does not explicitly access .outputs.
DERIVED_OBJECT_JSON = re.compile(
    r"\btojson\s*\(\s*(?:steps|needs)\.(?:[A-Za-z_][A-Za-z0-9_-]*|\*)\s*\)", re.I,
)
BRACKET_PROPERTY = re.compile(r"\[\s*(['\"])([A-Za-z_][A-Za-z0-9_-]*)\1\s*\]")
DYNAMIC_PROPERTY = re.compile(r"\b(?:github|inputs|steps|needs|env|vars|secrets)\s*\[", re.I)
FLOW_RUN_KEY = re.compile(
    r'(?<![A-Za-z0-9_])(?:run|["]run["]|[\x27]run[\x27]|"(?:r|\\u0072|\\x72|\\U00000072)(?:u|\\u0075|\\x75|\\U00000075)(?:n|\\u006e|\\x6e|\\U0000006e)")\s*:',
    re.I,
)

EXPLICIT_RUN_KEY = re.compile(
    r'^\s*(?:-\s*)?\?\s*(?:run|["]run["]|[\x27]run[\x27]|"(?:r|\\u0072|\\x72|\\U00000072)(?:u|\\u0075|\\x75|\\U00000075)(?:n|\\u006e|\\x6e|\\U0000006e)")\s*(?:#.*)?$',
    re.I,
)


# A YAML scalar tag or key anchor can precede a mapping key that resolves
# to "run" without starting the line with that spelling. We intentionally
# fingerprint the entire committed workflow rather than parsing tags/anchors.
# This can reject benign additions; it must not silently approve new scripts.
RUN_TAGGED_OR_ANCHORED_KEY = re.compile(
    r'''^\s*(?:-\s*)?(?:\?\s*)?
        (?:(?:!![A-Za-z][\w.-]*|![^\s]+|&[A-Za-z_][\w.-]*)\s+)+
        (?:run|['"]run['"]|"(?:r|\\u0072|\\x72|\\U00000072)(?:u|\\u0075|\\x75|\\U00000075)(?:n|\\u006e|\\x6e|\\U0000006e)")
        \s*(?::|(?:\#.*)?$)''',
    re.I | re.X,
)


YAML_ALIAS_VALUE = re.compile(
    r"""^\s*(?:-\s*)?(?:[A-Za-z_][\w.-]*|'[^']+'|"[^"]+")\s*:\s*\*[A-Za-z_][\w.-]*\s*(?:#.*)?$"""
)
YAML_ALIAS_ITEM = re.compile(r"^\s*-\s+\*[A-Za-z_][\w.-]*\s*(?:#.*)?$")


class EvidenceError(ValueError):
    """The evidence is incomplete, invalid or ambiguous: deny."""


def git(*args: str) -> bytes:
    result = subprocess.run(["git", *args], capture_output=True, timeout=15, check=False)
    if result.returncode:
        raise EvidenceError("git_evidence_unavailable")
    return result.stdout


def tree_files(rev: str) -> dict[str, str]:
    if not SHA.fullmatch(rev):
        raise EvidenceError("invalid_commit_sha")
    # The same shell-injection boundary applies to local composite Actions.
    # Enumerate the tree, then inspect only workflow and action metadata blobs.
    entries = git("ls-tree", "-rz", rev)
    files: dict[str, str] = {}
    count = 0
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        count += 1
        if count > MAX_TREE_ENTRIES:
            raise EvidenceError("too_many_tree_entries")
        try:
            meta, raw_path = entry.split(b"\t", 1)
            mode, kind, blob = meta.decode("ascii").split(" ")
            path = raw_path.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise EvidenceError("malformed_git_tree") from exc
        if path.startswith("/") or path.startswith("../") or "/../" in path or "\x00" in path:
            raise EvidenceError("unsafe_workflow_path")
        workflow = path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))
        action_metadata = path.rsplit("/", 1)[-1] in {"action.yml", "action.yaml"}
        if not (workflow or action_metadata):
            continue
        if len(files) >= MAX_WORKFLOWS:
            raise EvidenceError("too_many_workflow_sources")
        if mode != "100644" or kind != "blob" or not SHA.fullmatch(blob):
            raise EvidenceError("non_regular_workflow")
        if path in files:
            raise EvidenceError("duplicate_workflow_path")
        files[path] = blob
    if not files:
        raise EvidenceError("empty_workflow_inventory")
    return files


STEP_ITEM = re.compile(r"^( *)-\s+\S")
STEP_PARENT = re.compile(r"^\s*steps\s*:")


def step_scope(lines: list[str], run_row: int, run_indent: int) -> dict | None:
    """Bind shell risks to one YAML sequence item's body and order.

    Deliberately fail-conservative on step metadata edits; this is a bounded
    indentation heuristic, not a claim to parse GitHub Actions YAML.
    """
    start = -1
    level = -1
    for row in range(run_row, -1, -1):
        match = STEP_ITEM.match(lines[row])
        if match and len(match.group(1)) <= run_indent:
            start, level = row, len(match.group(1))
            break
    if start < 0:
        return None
    stop = len(lines)
    for row in range(start + 1, len(lines)):
        text = lines[row]
        if text.strip() and len(text) - len(text.lstrip(" ")) <= level:
            stop = row
            break
    parent = 0
    for row in range(start - 1, -1, -1):
        text = lines[row]
        if STEP_PARENT.match(text) and len(text) - len(text.lstrip(" ")) < level:
            parent = row
            break
    ordinal = sum(
        1 for row in range(parent + 1, start)
        if (match := STEP_ITEM.match(lines[row])) and len(match.group(1)) == level
    )
    return {"ordinal": ordinal, "body": lines[start:stop]}


ROOT_EXECUTION_CONTROLS = frozenset({"on", "permissions", "defaults", "env", "concurrency"})
JOB_EXECUTION_CONTROLS = frozenset({
    "if", "permissions", "runs-on", "environment", "container", "services",
    "defaults", "strategy", "concurrency", "timeout-minutes", "env",
})
SECTION_HEADER = re.compile(r"""^(?:([A-Za-z_][\w.-]*)|'([^']+)'|"([^"]+)")\s*:""")


def execution_sections(lines: list[str], start: int, stop: int,
                       indent: int, keys: frozenset[str]) -> list[str]:
    """Collect only non-comment source lines in selected YAML key sections."""
    gathered: list[str] = []
    enabled = False
    for line in lines[start:stop]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        current_indent = len(line) - len(line.lstrip(" "))
        if current_indent <= indent:
            key = SECTION_HEADER.match(stripped) if current_indent == indent else None
            enabled = bool(key and any(group in keys for group in key.groups() if group))
        if enabled and current_indent >= indent:
            gathered.append(stripped)
    return gathered


def execution_control_context(lines: list[str], run_row: int) -> dict:
    """Track high-impact source controls independently of step-shell bytes.

    A legacy shell expression cannot quietly inherit broader workflow triggers
    or more powerful job permissions. Unrelated jobs and display titles do not
    invalidate its no-new-debt baseline. This is a YAML indentation heuristic,
    not evidence that a GitHub Actions job has actually run.
    """
    global_controls = execution_sections(
        lines, 0, len(lines), 0, ROOT_EXECUTION_CONTROLS
    )
    jobs_root = next(
        (i for i in range(run_row - 1, -1, -1)
         if (match := SECTION_HEADER.match(lines[i])) and "jobs" in match.groups()),
        None,
    )
    if jobs_root is None:
        return {"global": global_controls, "job": []}
    candidates = []
    for i in range(jobs_root + 1, run_row):
        line = lines[i]
        indentation = len(line) - len(line.lstrip(" "))
        if indentation > 0 and SECTION_HEADER.match(line.lstrip()):
            candidates.append((i, indentation))
    if not candidates:
        return {"global": global_controls, "job": []}
    # YAML is indentation-driven, not constrained to a 2-space style.
    # Job headers use the shallowest positive key indent below jobs:.
    job_indent = min(level for _, level in candidates)
    job_start = next(i for i, level in reversed(candidates)
                     if level == job_indent)
    job_stop = next(
        (i for i in range(job_start + 1, len(lines))
         if lines[i].strip() and not lines[i].lstrip().startswith("#")
         and len(lines[i]) - len(lines[i].lstrip(" ")) <= job_indent),
        len(lines),
    )
    metadata_levels = [
        len(line) - len(line.lstrip(" "))
        for line in lines[job_start + 1:job_stop]
        if SECTION_HEADER.match(line.lstrip())
        and len(line) - len(line.lstrip(" ")) > job_indent
    ]
    if not metadata_levels:
        return {"global": global_controls, "job": []}
    job_controls = execution_sections(
        lines, job_start + 1, job_stop, min(metadata_levels), JOB_EXECUTION_CONTROLS
    )
    return {"global": global_controls, "job": job_controls}


def run_shell_scalars(source: str) -> list[tuple[str, str]]:
    """Extract bounded run scalars with best-effort YAML ancestor identities.

    Binding a risky shell snippet only to its bytes lets an unchanged
    interpolation move from a read-only job into a privileged job without
    creating new debt. Structural mapping keys supply a conservative,
    indentation-based context; this is still not a full YAML parser.
    """
    structural = re.compile(r"""^( *)(?:-\s*)?(?:([A-Za-z0-9_.-]+)|'([^']+)'|"([^"]+)")\s*:\s*(?:#.*)?$""")
    lines = source.splitlines()
    scalars: list[tuple[str, str]] = []
    ancestors: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        while ancestors and ancestors[-1][0] >= indent:
            ancestors.pop()
        match = RUN.match(line) or RUN_ESCAPED_KEY.match(line)
        if not match:
            key = structural.match(line)
            if key:
                ancestors.append((indent, key.group(2) or key.group(3) or key.group(4)))
            i += 1
            continue
        indent = len(match.group(1))
        payload = [match.group(2)]
        context = json.dumps({"parents": [key for _, key in ancestors],
                              "step": step_scope(lines, i, indent),
                              "execution": execution_control_context(lines, i)},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        context_digest = hashlib.sha256(context.encode("utf-8")).hexdigest()
        i += 1
        while i < len(lines):
            current = lines[i]
            if current.strip() and len(current) - len(current.lstrip(" ")) <= indent:
                break
            payload.append(current)
            i += 1
        scalars.append(("\n".join(payload), context_digest))
    return scalars


def expressions_outside_quoted_closers(scalar: str) -> tuple[list[str], bool]:
    """Extract GitHub expressions without treating '}}' in strings as a closer.

    Actions single-quoted literals escape apostrophes as two single quotes.
    This is deliberately a bounded lexical check, not a full evaluator.
    A nested opener or unmatched expression is conservatively flagged.
    """
    expressions: list[str] = []
    malformed = False
    offset = 0
    while (start := scalar.find("${{", offset)) >= 0:
        i = start + 3
        inside_literal = False
        while i < len(scalar):
            if scalar[i] == "'":
                if inside_literal and scalar[i:i + 2] == "''":
                    i += 2
                    continue
                inside_literal = not inside_literal
                i += 1
                continue
            if not inside_literal and scalar.startswith("${{", i):
                malformed = True
            if not inside_literal and scalar.startswith("}}", i):
                expressions.append(scalar[start + 3:i])
                offset = i + 2
                break
            i += 1
        else:
            malformed = True
            break
    return expressions, malformed


def fingerprint_risks(source: str) -> Counter[str]:
    out: Counter[str] = Counter()
    # A run alias can inherit code from an anchor elsewhere in its workflow.
    # Since this is deliberately not a YAML parser, any edit to that workflow
    # must be treated as new ambiguity while such a run alias is present.
    workflow_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    # Flow-style YAML can put the run key after an inline brace, bypassing a
    # newline-oriented scalar extractor. Treat new flow-run forms as ambiguous
    # rather than silently claiming that no dangerous interpolation exists.
    # Digest the entire line so rewriting an existing flow-run also counts.
    for line in source.splitlines():
        if RUN_TAGGED_OR_ANCHORED_KEY.match(line):
            out["ambiguous_prefixed_run_key|workflow_sha256:" + workflow_digest] += 1
        # YAML's '? run' mapping splits the key from the value.
        # Conservatively bind the ambiguity to the full workflow blob.
        if EXPLICIT_RUN_KEY.match(line):
            out["ambiguous_explicit_run_key|workflow_sha256:" + workflow_digest] += 1
        # GitHub Actions supports YAML aliases, including whole-job clones.
        # A changed alias can instantiate an existing unsafe run without any
        # change to that original scalar. Treat the whole source as ambiguous.
        alias_run = RUN.match(line) or RUN_ESCAPED_KEY.match(line)
        already_covered_run_alias = bool(alias_run and alias_run.group(2).lstrip().startswith("*"))
        if (YAML_ALIAS_VALUE.match(line) or YAML_ALIAS_ITEM.match(line)) and not already_covered_run_alias:
            out["ambiguous_yaml_alias|workflow_sha256:" + workflow_digest] += 1
        match = FLOW_RUN_KEY.search(line)
        # Braces *after* run: can simply be the normal ${{ expression }}
        # delimiters. Only a brace *before* the key indicates flow mapping.
        if match and "{" in line[:match.start()]:
            out["ambiguous_flow_run|workflow_sha256:" + workflow_digest] += 1
    for scalar, context_digest in run_shell_scalars(source):
        # A previously seen expression is not safe to move from a harmless
        # command to a more dangerous one. Bind findings to their actual run
        # scalar contents, not solely to the expression spelling.
        scalar_digest = hashlib.sha256((scalar + "\x00" + context_digest).encode("utf-8")).hexdigest()
        value = scalar.lstrip()
        if value.startswith("*") or value.startswith("!") or value.startswith("&"):
            out["ambiguous_yaml_run_scalar|workflow_sha256:" + workflow_digest] += 1
        # Double-quoted YAML strings decode hex and Unicode escapes before
        # GitHub expression interpolation. A raw-source-only scanner may miss
        # the decoded dollar, braces, or even the interpolated property name.
        # Reject new escaped quoted run values instead of pretending to parse
        # all YAML syntax. Single-quoted and block values are unaffected.
        if value.startswith('"') and YAML_QUOTED_ESCAPE.search(value):
            out["ambiguous_yaml_escaped_quoted_run|run_sha256:" + scalar_digest] += 1
        expressions, malformed = expressions_outside_quoted_closers(scalar)
        # Quote characters within expression string literals cannot close the
        # expression. A missing real terminator or nested opener is ambiguous.
        if malformed:
            out["incomplete_expression|run_sha256:" + scalar_digest] += 1
        for expr in expressions:
            normalized = " ".join(expr.split()).lower()
            if len(normalized) > 4096:
                raise EvidenceError("expression_too_large")
            # GitHub expressions allow github['event']['issue'] as well as
            # github.event.issue. The env context is similarly unsafe for direct\n            # shell interpolation: a prior step or workflow may fill it with\n            # attacker-controlled text; env: injection + quoted shell use is safer.
            # Workflow display titles may contain shell syntax; github.token
            # is a secret-bearing context and must not appear in run text.
            # Configured vars and secrets can also contain shell metacharacters;
            # inject through env: rather than embedding directly in run text.
            normalized = BRACKET_PROPERTY.sub(lambda m: "." + m.group(2), normalized)
            normalized = re.sub(r"\s*\.\s*", ".", normalized)
            if UNTRUSTED.search(normalized) or DERIVED_OUTPUT_UNTRUSTED.search(normalized) or ROOT_CONTEXT_JSON.search(normalized) or DERIVED_OBJECT_JSON.search(normalized):
                out[normalized + "|run_sha256:" + scalar_digest] += 1
            elif DYNAMIC_PROPERTY.search(normalized):
                # Unknown computed key might resolve to event or inputs.
                out["ambiguous_dynamic_property|run_sha256:" + scalar_digest] += 1
    return out


def snapshot(rev: str) -> dict[str, Counter[str]]:
    files = tree_files(rev)
    total_bytes = 0
    found = 0
    result = {}
    for path, blob in sorted(files.items()):
        try:
            size = int(git("cat-file", "-s", blob).strip())
        except ValueError as exc:
            raise EvidenceError("invalid_git_blob_size") from exc
        if size < 0 or size > MAX_BLOB_BYTES:
            raise EvidenceError("workflow_blob_too_large")
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise EvidenceError("workflow_tree_too_large")
        data = git("cat-file", "blob", blob)
        if len(data) != size:
            raise EvidenceError("unstable_blob_size")
        try:
            source = data.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise EvidenceError("invalid_workflow_utf8") from exc
        if "\t" in source:
            raise EvidenceError("ambiguous_workflow_tabs")
        risks = fingerprint_risks(source)
        found += sum(risks.values())
        if found > MAX_EXPRESSIONS:
            raise EvidenceError("too_many_risk_expressions")
        result[path] = risks
    return result


def risk_kind_counts(snapshot_risks: dict[str, Counter[str]]) -> list[dict[str, object]]:
    """Return fixed, non-sensitive reason categories, never source expressions.

    Risk keys deliberately contain normalized shell expressions or SHA hashes.
    They must never be reflected in advisory output, including for malformed
    or legacy sources.
    """
    kinds: Counter[str] = Counter()
    for risks in snapshot_risks.values():
        for fingerprint, count in risks.items():
            if fingerprint.startswith("incomplete_expression|"):
                kind = "incomplete_expression"
            elif fingerprint.startswith("ambiguous_dynamic_property|"):
                kind = "dynamic_property_ambiguity"
            elif fingerprint.startswith("ambiguous_"):
                kind = "yaml_structure_ambiguity"
            else:
                kind = "direct_interpolation"
            kinds[kind] += count
    return [{"kind": kind, "count": count} for kind, count in sorted(kinds.items())]


def audit(base: str, head: str) -> dict:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise EvidenceError("invalid_commit_sha")
    if subprocess.run(["git", "merge-base", "--is-ancestor", base, head], capture_output=True, timeout=15).returncode:
        raise EvidenceError("base_not_head_ancestor")
    before = snapshot(base)
    after = snapshot(head)
    newly: dict[str, Counter[str]] = {}
    for path, risks in after.items():
        diff = risks - before.get(path, Counter())
        if diff:
            newly[path] = diff
    return {
        "schema": "zcloud-shell-expression-ratchet-v1",
        "base_sha": base,
        "head_sha": head,
        "baseline_risks": sum(sum(x.values()) for x in before.values()),
        "candidate_risks": sum(sum(x.values()) for x in after.values()),
        "baseline_risk_kinds": risk_kind_counts(before),
        "candidate_risk_kinds": risk_kind_counts(after),
        # Only Git paths and bounded counts, never potentially malicious shell text.
        "baseline_risky_workflows": [
            {"path": path, "risk_count": sum(risks.values())}
            for path, risks in sorted(before.items()) if risks
        ],
        "candidate_risky_workflows": [
            {"path": path, "risk_count": sum(risks.values())}
            for path, risks in sorted(after.items()) if risks
        ],
        "new_risks": sum(sum(risks.values()) for risks in newly.values()),
        "new_risk_kinds": risk_kind_counts(newly),
        "new_risky_workflows": [
            {"path": path, "risk_count": sum(risks.values()),
             "risk_kinds": risk_kind_counts({path: risks})}
            for path, risks in sorted(newly.items())
        ],
        "affected_workflows": sorted(newly),
        "inventory_complete": True,
        "release_authorized": False,
        "deployment_authorized": False,
        "mutation_performed": False,
    }


def markdown_summary(report: dict) -> str:
    """Safe, bounded human-friendly GitHub job summary; never a release receipt."""
    if report.get("schema") != "zcloud-shell-expression-ratchet-v1":
        raise EvidenceError("invalid_summary_schema")

    def safe_cell(value: object) -> str:
        # Workflow paths are committed source, not trusted Markdown or HTML.
        value = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))[:240]
        return html.escape(value, quote=True).replace("|", "&#124;")

    lines = [
        "### zCloud — alleen-lezen controle van workflow-shellcode",
        "",
        "| Controle | Aantal |",
        "| --- | ---: |",
        f"| Historische syntactische bevindingen | {int(report['baseline_risks'])} |",
        f"| Bevindingen in kandidaat | {int(report['candidate_risks'])} |",
        f"| Nieuwe risicovolle shellinvoegingen | {int(report['new_risks'])} |",
        "",
    ]
    newly = int(report["new_risks"])
    if newly == 0:
        lines += ["**Geen nieuwe risicovolle shellinvoegingen.**", ""]
    else:
        lines += ["**Nieuwe broncode-risico's: wijziging afgewezen.**", "",
                  "| Workflowbestand | Nieuwe risico's | Soort |",
                  "| --- | ---: | --- |"]
        rows = report["new_risky_workflows"]
        for item in rows[:15]:
            kinds = ", ".join(
                f"{safe_cell(k['kind'])}: {int(k['count'])}"
                for k in item.get("risk_kinds", [])
            )
            lines.append(f"| {safe_cell(item['path'])} | {int(item['risk_count'])} | {kinds} |")
        if len(rows) > 15:
            lines.append(f"| {len(rows) - 15} overige bestanden niet getoond | — | — |")
        lines.append("")
    lines += [
        "**Broncontrole, geen productievrijgave.** Syntactische bevindingen "
        "zijn geen bewezen exploits. Runner-, merge-, herstel- en "
        "VPS-autoriteit blijven uitgeschakeld.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Immutable complete 40-hex Git SHA")
    parser.add_argument("--head", required=True, help="Immutable complete 40-hex Git SHA")
    args = parser.parse_args()
    try:
        report = audit(args.base, args.head)
        print(json.dumps(report, sort_keys=True))
        return 1 if report["new_risks"] else 0
    except (EvidenceError, subprocess.TimeoutExpired, OSError) as exc:
        print(json.dumps({"schema": "zcloud-shell-expression-ratchet-v1", "inventory_complete": False,
                          "error": str(exc) if isinstance(exc, EvidenceError) else "git_evidence_unavailable",
                          "release_authorized": False, "deployment_authorized": False,
                          "mutation_performed": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
