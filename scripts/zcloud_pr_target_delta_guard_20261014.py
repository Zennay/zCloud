#!/usr/bin/env python3
"""Monotonic source-only PR-target risk guard for changed Actions workflows.

Reads immutable git objects; never dispatches Actions or authorizes a release.
This deliberately supplements, rather than replaces, trusted-main workflow
admission and human review. Detection is conservative, not a YAML evaluator.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from pathlib import Path
import re
import subprocess

SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
# GitHub expressions accept both dotted and bracket-key access. Match the
# untrusted PR fields without evaluating expressions or printing source data.
PR_SOURCE = re.compile(
    r"""(?ix)
    \bgithub
    (?:
      (?:\s*\.\s*event|\s*\[\s*['"]event['"]\s*\])
      (?:
        (?:\s*\.\s*pull_request|\s*\[\s*['"]pull_request['"]\s*\])
        (?:\s*\.\s*(?:head|title|body|number|merge_commit_sha)\b
          |\s*\[\s*['"](?:head|title|body|number|merge_commit_sha)['"]\s*\]
          |(?=\s*(?:\}\}|\)|[,|]|$)))
        (?:\s*\.\s*[A-Za-z_]\w*|\s*\[\s*['"][A-Za-z_]\w*['"]\s*\])*
        |
        (?:\s*\.\s*number\b|\s*\[\s*['"]number['"]\s*\])
      )
      |
      (?:\s*\.\s*head_ref\b|\s*\[\s*['"]head_ref['"]\s*\])
    )
    """
)
# GitHub accepts plain, quoted, folded and literal YAML scalar values for
# "uses"; a line break must not hide an untrusted checkout target.
CHECKOUT = re.compile(
    r"""(?mix)
    ^[ \t]*(?:-[ \t]*)?['"]?uses['"]?[ \t]*:
    (?:[ \t]*['"]?actions/checkout@
      | [ \t]*(?:[>|][+-]?)?[ \t]*\n[ \t]+['"]?actions/checkout@)
    """
)
RUN_STEP = re.compile(r"(?m)^[ \t]*(?:-[ \t]*)?['\"]?run['\"]?[ \t]*:", re.I)
PR_TARGET = re.compile(r"\bpull_request_target\b")
# A PR head/merge can be checked out with a literal refs/pull/... ref
# without referring to an untrusted github.head expression at all.
PULL_REF = re.compile(r"""(?i)\brefs/pull/[^\s'"<>]{1,160}""")


class Unverified(ValueError):
    pass


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, timeout=20, stderr=subprocess.DEVNULL)


def checked_sha(value: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise Unverified("invalid_sha")
    return value


def changed_workflows(root: Path, base: str, head: str) -> list[str]:
    checked_sha(base)
    checked_sha(head)
    # Deleting or renaming away a workflow can remove an existing security
    # enforcement job. Fail closed rather than silently treating it as no diff.
    # --no-renames exposes the removed side of a rename for explicit review.
    removed = git(root, "diff", "--name-only", "-z", "--no-renames",
                  "--diff-filter=D", f"{base}...{head}", "--", ".github/workflows/")
    removed_paths = [item.decode("utf-8", "strict")
                     for item in removed.split(b"\x00") if item]
    if len(removed_paths) > 250 or len(removed_paths) != len(set(removed_paths)):
        raise Unverified("incomplete_or_untrusted_workflow_inventory")
    if any(WORKFLOW.fullmatch(path) for path in removed_paths):
        raise Unverified("workflow_removal_requires_review")
    raw = git(root, "diff", "--name-only", "-z", "--diff-filter=ACMRT", f"{base}...{head}", "--", ".github/workflows/")
    paths = [part.decode("utf-8", "strict") for part in raw.split(b"\x00") if part]
    if len(paths) > 250 or len(paths) != len(set(paths)) or any(not WORKFLOW.fullmatch(p) for p in paths):
        raise Unverified("incomplete_or_untrusted_workflow_inventory")
    return paths


def source_at(root: Path, sha: str, path: str, *, absent_ok: bool = False) -> str:
    checked_sha(sha)
    if not WORKFLOW.fullmatch(path):
        raise Unverified("invalid_source_path")
    # Git object mode disallows symlink targets and gitlinks, even if the
    # current filesystem checkout materializes differently.
    try:
        mode = git(root, "ls-tree", sha, "--", path).decode("ascii", "strict")
    except (subprocess.CalledProcessError, UnicodeError) as exc:
        raise Unverified("untrusted_git_tree") from exc
    if not mode:
        if absent_ok:
            return ""
        raise Unverified("missing_candidate_source")
    if not mode.startswith("100644 blob ") and not mode.startswith("100755 blob "):
        raise Unverified("unsafe_git_mode")
    try:
        raw = git(root, "show", f"{sha}:{path}")
        if len(raw) > 100_000 or b"\x00" in raw:
            raise Unverified("invalid_source_size")
        return raw.decode("utf-8", "strict")
    except (subprocess.CalledProcessError, UnicodeError) as exc:
        raise Unverified("unreadable_source") from exc


def _content_without_comments(source: str) -> str:
    # Quotes containing # are retained; comments are discarded. This is NOT a
    # YAML parser: ambiguous aliases / templates are refused separately.
    output = []
    block_parent_indent = None
    for line in source.splitlines():
        # A YAML block scalar preserves its content literally. Lines beginning
        # with '#' inside run: | or run: > are not YAML comments and may still
        # contain GitHub expressions, even when the shell would ignore them.
        current_indent = len(line) - len(line.lstrip(" "))
        if block_parent_indent is not None:
            if not line.strip() or current_indent > block_parent_indent:
                output.append(line)
                continue
            block_parent_indent = None
        quote = None
        escaped = False
        useful = []
        for ch in line:
            if quote and escaped:
                useful.append(ch)
                escaped = False
                continue
            if quote and ch == "\\":
                useful.append(ch)
                escaped = True
                continue
            if ch in "\"'":
                quote = None if ch == quote else ch if quote is None else quote
            # YAML only begins a plain-scalar comment at a separation
            # boundary. A contiguous x#${{ expression }} is literal data.
            if ch == "#" and quote is None and (not useful or useful[-1].isspace()):
                break
            useful.append(ch)
        cleaned_line = "".join(useful)
        output.append(cleaned_line)
        scalar = re.match(
            r"""^([ ]*)(?:-[ ]+)?['"]?[A-Za-z_][\w-]*['"]?[ \t]*:"""
            r"""[ \t]*[>|][+-]?[ \t]*$""",
            cleaned_line,
        )
        if scalar:
            item = re.match(r"^[ ]*-[ ]+", cleaned_line)
            block_parent_indent = item.end() if item else len(scalar.group(1))
    return "\n".join(output) + "\n"


def executable_steps(source: str) -> list[str]:
    """Isolate step-level checkout/run contexts, never conflate different jobs.

    This intentionally does not execute YAML or GitHub expressions. Steps are
    grouped by sibling list-item keys (such as "- name", "- if" or "- uses").
    """
    lines = source.splitlines()
    starts = []
    for index, line in enumerate(lines):
        match = re.match(r"^([ ]+)-\s+(?:['\"]?[A-Za-z_][\w-]*['\"]?):", line)
        if match:
            starts.append((index, len(match.group(1))))
    blocks = []
    for start, indent in starts:
        end = len(lines)
        for row_number in range(start + 1, len(lines)):
            row = lines[row_number]
            trimmed = row.lstrip(" ")
            # A new job, steps block, or sibling step ends this step.
            # This also prevents a PR-head expression in a later job
            # from being mistaken for a run argument.
            if trimmed and not trimmed.startswith("#") and len(row) - len(trimmed) <= indent:
                end = row_number
                break
        blocks.append("\n".join(lines[start:end]))
    return blocks


def _run_command_source(step: str, command: re.Match[str]) -> str:
    """Extract a run scalar, excluding later sibling YAML keys in the step.

    GitHub Actions permits arbitrary ordering of run/env/working-directory. A
    later env: mapping is metadata and must not masquerade as run code. Treat
    a '- run:' key as two spaces deeper than the enclosing list-item indent.
    """
    lines = step[command.start():].splitlines()
    if not lines:
        return ""
    first = lines[0]
    indent = len(first) - len(first.lstrip(" "))
    dash = re.match(r"^[ ]*-[ ]+", first)
    if dash:
        indent = len(dash.group(0))
    output = [first]
    for line in lines[1:]:
        trimmed = line.lstrip(" ")
        current_indent = len(line) - len(trimmed)
        if (
            trimmed
            and current_indent <= indent
            and re.match(r"""^['"]?[A-Za-z_][\w-]*['"]?[ \t]*:""", trimmed)
        ):
            break
        output.append(line)
    return "\n".join(output)


def _consumes_env(name: str, command_source: str) -> bool:
    """Recognize a variable expanded by a shell or a GitHub env context."""
    if re.search(
        r"\$(?:\{!?" + re.escape(name)
        + r"(?=[}:?+/%#^,@\[])|" + re.escape(name) + r"\b)",
        command_source,
    ):
        return True
    return bool(re.search(
        r"""\benv(?:\s*\.\s*""" + re.escape(name)
        + r"""|\s*\[\s*['"]""" + re.escape(name) + r"""['"]\s*\])(?=\W|$)""",
        command_source,
    ))


def _env_expressions_executed_by_run(
    step: str, command_source: str, *, require_shell_sink: bool = True
) -> Counter[str]:
    """Trace simple step-local env bindings into a run command without YAML eval.

    A PR title in an env declaration is dangerous if the shell subsequently
    expands that variable. Metadata never read by the command is not counted.
    The scanner deliberately does not claim full shell data-flow coverage.
    """
    references: Counter[str] = Counter()
    # PR values passed as immutable data are routine in the trusted source
    # scanner (e.g. EXACT_HEAD for git fetch). Require an actual dynamic shell
    # execution sink before classifying step-local environment forwarding.
    if require_shell_sink and not re.search(
        r"(?i)\b(?:eval|exec|source)\b"
        r"|\b(?:bash|sh|zsh)\s+-[a-z]*c[a-z]*\b"
        r"|\bpython[0-9.]*\s+-c\b|\bnode\s+-(?:e|p)\b"
        r"|(?:^|[\s;&|])\.[ \t]+(?=[\"'\$])",
        command_source,
    ):
        return references
    in_env = False
    env_indent = 0
    for line in step.splitlines():
        # Flow-style YAML env mappings are valid alongside block-style maps.
        # Parse each named field independently; a PR title in a literal inline
        # mapping must not disappear merely because it lacks nested lines.
        flow = re.match(
            r"""^[ ]*(?:-\s+)?['"]?env['"]?[ \t]*:[ \t]*\{(?P<entries>.*)\}[ \t]*$""",
            line,
        )
        if flow:
            fields = flow.group("entries")
            entries = re.finditer(
                r"""(?:^|,)[ \t]*['"]?(?P<name>[A-Za-z_][A-Za-z0-9_]*)['"]?"""
                r"""[ \t]*:[ \t]*(?P<value>.*?)"""
                r"""(?=[ \t]*,[ \t]*['"]?[A-Za-z_][A-Za-z0-9_]*['"]?[ \t]*:|$)""",
                fields,
            )
            for binding in entries:
                if _consumes_env(binding.group("name"), command_source):
                    references.update(
                        match.group(0).lower()
                        for match in PR_SOURCE.finditer(binding.group("value"))
                    )
            continue
        header = re.match(r"""^([ ]*)(?:-\s+)?['"]?env['"]?[ \t]*:[ \t]*$""", line)
        if header:
            in_env = True
            env_indent = len(header.group(1))
            continue
        if not in_env or not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= env_indent:
            in_env = False
            continue
        binding = re.match(
            r"""^[ ]+['"]?(?P<name>[A-Za-z_][A-Za-z0-9_]*)['"]?[ \t]*:[ \t]*(?P<value>[^\r\n]*)$""",
            line,
        )
        if not binding:
            continue
        name, value = binding.group("name"), binding.group("value")
        pr_expressions = list(PR_SOURCE.finditer(value))
        if not pr_expressions:
            continue
        if _consumes_env(name, command_source):
            references.update(match.group(0).lower() for match in pr_expressions)
    return references


def _job_env_run_risks(clean: str, *, checkout: bool = False) -> Counter[str]:
    """Find job-scoped PR env values consumed by dynamic run steps in that job.

    A source in job A never contaminates a run in job B. This is intentionally
    a syntactic boundary, not general-purpose YAML or shell interpretation.
    """
    jobs = re.search(r"(?m)^jobs:[ \t]*$", clean)
    if not jobs:
        return Counter()
    lines = clean[jobs.end():].splitlines()
    stop = next((i for i, line in enumerate(lines)
                 if line.strip() and not line.startswith((" ", "\t"))),
                len(lines))
    lines = lines[:stop]
    job_headers = []
    for index, line in enumerate(lines):
        match = re.match(r"""^([ ]+)(?:['"]?[\w-]+['"]?)[ \t]*:[ \t]*$""", line)
        if match:
            job_headers.append((index, len(match.group(1))))
    if not job_headers:
        return Counter()
    job_indent = min(indent for _, indent in job_headers)
    headers = [index for index, indent in job_headers if indent == job_indent]
    found: Counter[str] = Counter()
    for offset, start in enumerate(headers):
        end = headers[offset + 1] if offset + 1 < len(headers) else len(lines)
        job_lines = lines[start:end]
        metadata = []
        for index, line in enumerate(job_lines):
            match = re.match(
                r"""^([ ]+)(?:['"]?(?:env|steps|runs-on|if|permissions|strategy|timeout-minutes)['"]?)[ \t]*:""",
                line,
            )
            if match and len(match.group(1)) > job_indent:
                metadata.append((index, len(match.group(1))))
        if not metadata:
            continue
        child_indent = min(indent for _, indent in metadata)
        job_env_blocks = []
        for index, indent in metadata:
            if indent != child_indent or not re.match(
                r"""^[ ]+['"]?env['"]?[ \t]*:""", job_lines[index]
            ):
                continue
            end_env = next(
                (at for at in range(index + 1, len(job_lines))
                 if job_lines[at].strip()
                 and len(job_lines[at]) - len(job_lines[at].lstrip(" ")) <= indent),
                len(job_lines),
            )
            job_env_blocks.extend(job_lines[index:end_env])
        if not job_env_blocks:
            continue
        source = "\n".join(job_lines)
        for step in executable_steps(source):
            command = CHECKOUT.search(step) if checkout else RUN_STEP.search(step)
            if command:
                found.update(_env_expressions_executed_by_run(
                    "\n".join(job_env_blocks), step if checkout else _run_command_source(step, command),
                    require_shell_sink=not checkout,
                ))
    return found


def _workflow_env_run_risks(clean: str, *, checkout: bool = False) -> Counter[str]:
    """Detect untrusted workflow-level env forwarded to executable shell code."""
    header = re.search(
        r"""(?m)^['"]?env['"]?[ \t]*:[ \t]*(?:\{[^\r\n]*\})?[ \t]*$""",
        clean,
    )
    if not header:
        return Counter()
    # Only the top-level env mapping is inherited by every job. A later
    # root key ends this mapping, independently of its position in the file.
    rest = clean[header.start():]
    boundary = re.search(r"""(?m)^[A-Za-z_][\w-]*[ \t]*:""", rest[header.end()-header.start():])
    env_section = rest[:header.end()-header.start()+boundary.start()] if boundary else rest
    found: Counter[str] = Counter()
    for step in executable_steps(clean):
        command = CHECKOUT.search(step) if checkout else RUN_STEP.search(step)
        if command is not None:
            found.update(_env_expressions_executed_by_run(
                env_section, step if checkout else _run_command_source(step, command),
                require_shell_sink=not checkout,
            ))
    return found


def step_reference_risks(clean: str) -> dict[str, Counter[str]]:
    """Count untrusted expressions only inside the step that carries risk.

    Harmless metadata in another step cannot offset a newly introduced
    PR-head checkout or execution, even if expression totals are unchanged.
    """
    found: dict[str, Counter[str]] = {
        "untrusted_pr_checkout": Counter(),
        "pr_source_in_run_job": Counter(),
    }
    for step in executable_steps(clean):
        if CHECKOUT.search(step):
            found["untrusted_pr_checkout"].update(
                match.group(0).lower() for match in PR_SOURCE.finditer(step)
            )
            found["untrusted_pr_checkout"].update(
                "pull_ref:" + match.group(0).lower()
                for match in PULL_REF.finditer(step)
            )
        command = RUN_STEP.search(step)
        if command is not None:
            command_source = _run_command_source(step, command)
            found["pr_source_in_run_job"].update(
                match.group(0).lower()
                for match in PR_SOURCE.finditer(command_source)
            )
            found["pr_source_in_run_job"].update(
                "pull_ref:" + match.group(0).lower()
                for match in PULL_REF.finditer(command_source)
            )
            found["pr_source_in_run_job"].update(
                _env_expressions_executed_by_run(step, command_source)
            )
    found["pr_source_in_run_job"].update(_job_env_run_risks(clean))
    found["pr_source_in_run_job"].update(_workflow_env_run_risks(clean))
    found["untrusted_pr_checkout"].update(_job_env_run_risks(clean, checkout=True))
    found["untrusted_pr_checkout"].update(_workflow_env_run_risks(clean, checkout=True))
    return {reason: count for reason, count in found.items() if count}


def risk_set(source: str) -> set[str]:
    """Return coarse, monotonic reasons (no source text or secrets exposed)."""
    if not source:
        return set()
    clean = _content_without_comments(source)
    anchors = list(re.finditer(r"(?m)^['\"]?on['\"]?:[ \t]*(.*)$", clean))
    if len(anchors) != 1:
        raise Unverified("ambiguous_event_trigger")
    # An aliased trigger may expand to pull_request_target even if the visible
    # event block never spells it out. Refuse before the early non-target exit.
    # This is intentionally narrower than banning unrelated YAML anchors.
    trigger_line = anchors[0].group(1).strip()
    if re.match(r"^['\"]?\*[A-Za-z_][\w-]*", trigger_line) or re.search(
        r"(?m)^['\"]?on['\"]?:[ \t]*[&*][A-Za-z_][\w-]*", clean
    ):
        raise Unverified("aliased_event_trigger")
    # Only a root-level event block or supported inline event forms qualify.
    event_region = clean[anchors[0].start():]
    match = re.search(r"(?m)^[A-Za-z_][\w-]*:[ \t]*", clean[anchors[0].end():])
    if match:
        event_region = clean[anchors[0].start():anchors[0].end() + match.start()]
    # A trigger mapping merge can also import an unseen target event. This
    # must not be misclassified as an ordinary non-target workflow.
    if re.search(r"(?m)^\s*<<\s*:\s*\*[A-Za-z_][\w-]*", event_region):
        raise Unverified("aliased_event_trigger")
    # A list can smuggle the same aliased event after an ordinary trigger:
    # "on: [push, *privileged]" or "on:\n  - *privileged".
    # Never regard an opaque event entry as a verified non-target trigger.
    if re.search(r"(?<![A-Za-z0-9_])\*[A-Za-z_][\w-]*", event_region):
        raise Unverified("aliased_event_trigger")
    if not PR_TARGET.search(event_region):
        return set()
    risk = set()
    # PR-target events may inherit a write-scoped GITHUB_TOKEN unless the
    # workflow explicitly narrows root permissions. Deny newly omitted roots,
    # even if every step looks read-only in this syntax-only scan.
    if not re.search(r"""(?m)^['"]?permissions['"]?[ \t]*:""", clean):
        risk.add("missing_explicit_root_permissions")
    # Merge aliases or YAML anchors can obscure the effective trigger and
    # permissions. Do not allow a changed PR-target job to use them.
    if re.search(r"(?m)^\s*<<\s*:|\s[&*][A-Za-z][\w-]*", clean):
        risk.add("yaml_alias_or_merge")
    if len(re.findall(r"""(?m)^['"]?permissions['"]?:\s*""", clean)) > 1 or len(anchors) > 1:
        risk.add("ambiguous_permissions_or_trigger")
    # Fail closed on granting ANY explicit write scope, regardless of where
    # permissions are nested. Harmless comments are not counted.
    if WRITE_ALL.search(clean):
        risk.add("write_all")
    if WRITE_SCOPE.search(clean):
        risk.add("write_permission")
    if re.search(r"\bsecrets\s*\.", clean, re.I) or re.search(r"\bsecrets\s*\[", clean, re.I):
        risk.add("secret_reference")
    risk.update(step_reference_risks(clean))
    return risk


# Permissions are YAML scalars: quoting, folding or line breaks must not
# conceal the write-all token from the monotonic authority check.
WRITE_ALL = re.compile(
    r"""(?mix)
    ^(?P<indent>[ \t]*)['"]?permissions['"]?[ \t]*:[ \t]*
    (?:['"]?write-all['"]?
       |(?:[>|][+-]?)?[ \t]*\n[ \t]+['"]?write-all['"]?)
    [ \t]*$
    """
)


# Existing legacy debt is counted, not merely bucketed. Otherwise a PR that
# adds a SECOND secret use to a workflow with one pre-existing secret reference
# would silently pass a boolean/category-only monotonic check.
WRITE_SCOPE = re.compile(
    r"""(?mx)
    (?:^[ \t]*|(?<=[{,])[ \t]*)
    (?P<scope>["']?[A-Za-z][\w-]*["']?)[ \t]*:[ \t]*
    (?:["']?write["']?
      |(?:[>|][+-]?)?[ \t]*\n[ \t]+["']?write["']?)
    [ \t]*(?=$|[,}])
    """
)
SECRET_REFERENCE = re.compile(r"\bsecrets[ \t]*(?:\.|\[)", re.I)
SECRET_IDENTITY = re.compile(
    r"(?i)\bsecrets[ \t]*(?:\.[A-Za-z_][A-Za-z0-9_]*|\[[^\]\r\n]{1,160}\])"
)


def risk_signatures(source: str) -> dict[str, Counter[str]]:
    """Track identities in addition to totals, without emitting secret names."""
    risks = risk_set(source)
    if not risks:
        return {}
    clean = _content_without_comments(source)
    signatures: dict[str, Counter[str]] = {}
    if "write_all" in risks:
        # Moving an existing job-only grant to the workflow root broadens
        # GITHUB_TOKEN authority even if the total write-all count is equal.
        root_grants = sum(not match.group("indent") for match in WRITE_ALL.finditer(clean))
        if root_grants:
            signatures["write_all"] = Counter({"workflow_root": root_grants})
    if "write_permission" in risks:
        signatures["write_permission"] = Counter(
            match.group("scope").strip("\"'").lower()
            for match in WRITE_SCOPE.finditer(clean)
        )
        # A write scope used only in job A is not equivalent to granting it
        # to *every* job at workflow root. Keep scope identities and track
        # root grants separately, so equal total counts cannot mask a move.
        permissions = re.search(
            r"""(?m)^['"]?permissions['"]?[ \t]*:""", clean
        )
        if permissions:
            remainder = clean[permissions.end():]
            boundary = re.search(
                r"""(?m)^['"]?[A-Za-z_][\w-]*['"]?[ \t]*:""", remainder
            )
            root_block = clean[permissions.start():permissions.end()] + (
                remainder[:boundary.start()] if boundary else remainder
            )
            signatures["write_permission"].update(
                "workflow_root:" + match.group("scope").strip("\"'").lower()
                for match in WRITE_SCOPE.finditer(root_block)
            )
    if "secret_reference" in risks:
        signatures["secret_reference"] = Counter(
            hashlib.sha256(match.group(0).upper().encode("utf-8")).hexdigest()
            for match in SECRET_IDENTITY.finditer(clean)
        )
    for reason, counter in step_reference_risks(clean).items():
        if reason in risks:
            signatures[reason] = counter
    return signatures


def increasing_risks(before: str, after: str) -> set[str]:
    """Never net new exposure against removed exposure of a different kind."""
    old_counts, new_counts = risk_counts(before), risk_counts(after)
    added = {key for key, count in new_counts.items()
             if count > old_counts.get(key, 0)}
    old_signatures, new_signatures = risk_signatures(before), risk_signatures(after)
    for reason, candidate in new_signatures.items():
        previous = old_signatures.get(reason, Counter())
        if any(count > previous.get(identity, 0)
               for identity, count in candidate.items()):
            added.add(reason)
    return added


def risk_counts(source: str) -> dict[str, int]:
    risks = risk_set(source)
    if not risks:
        return {}
    clean = _content_without_comments(source)
    counts = {reason: 1 for reason in risks}
    if "write_all" in risks:
        # A boolean risk bucket hides added job-level write-all grants.
        counts["write_all"] = max(1, len(WRITE_ALL.findall(clean)))
    if "write_permission" in risks:
        counts["write_permission"] = max(1, len(WRITE_SCOPE.findall(clean)))
    if "secret_reference" in risks:
        counts["secret_reference"] = max(1, len(SECRET_REFERENCE.findall(clean)))
    for reason, counter in step_reference_risks(clean).items():
        if reason in risks:
            counts[reason] = sum(counter.values())
    return counts


def evaluate(root: Path, base: str, head: str) -> tuple[int, list[tuple[str, str]]]:
    root = root.resolve(strict=True)
    paths = changed_workflows(root, base, head)
    findings = []
    for path in paths:
        original = source_at(root, base, path, absent_ok=True)
        candidate = source_at(root, head, path)
        # Existing risk may be reduced, but never newly introduced. This
        # preserves already-owned legacy debt without legitimizing it.
        additional = increasing_risks(original, candidate)
        for reason in sorted(additional):
            findings.append((path, reason))
    return len(paths), findings


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = p.parse_args(argv)
    try:
        scanned, findings = evaluate(args.root, args.base, args.head)
    except (Unverified, OSError, subprocess.SubprocessError, UnicodeError) as exc:
        print("PR_TARGET_RISK=DENY reason=unverifiable_source")
        print("PRODUCTION_MUTATION_AUTHORIZED=false")
        return 2
    print(f"PR_TARGET_RISK={'DENY' if findings else 'PASS'} workflows_scanned={scanned} new_risks={len(findings)}")
    for path, reason in findings[:20]:
        print(f"DENY {path}:{reason}")
    print("PRODUCTION_MUTATION_AUTHORIZED=false")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
