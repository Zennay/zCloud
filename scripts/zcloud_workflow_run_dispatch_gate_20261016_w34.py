#!/usr/bin/env python3
"""No-new-debt gate for workflow_run-triggered recursive dispatch.

Source-only, read-only, and deliberately not a release/runner authority.
Existing exact source debt is inventoried, not certified safe.
"""
from __future__ import annotations

import argparse
import hashlib
from collections import Counter
import os
from pathlib import Path
import re
import subprocess
import sys

REPO = "Zennay/zCloud"
ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
ON = re.compile(r"(?m)^(?:on|'on'|\"on\")[ \t]*:[ \t]*(.*)$")
RUN = re.compile(r"""(?m)^( +)(?:-[ \t]+)?(?:run|'run'|"run"):[ \t]*(.*)$""")
# gh permits multiple global repository/hostname options before the verb.
# The previous single-option recognizer allowed multi-flag dispatch calls to
# evade the read-only no-new-recursion scan.
GH_GLOBAL_ARGS = r"(?:\s+(?:(?:-R|--repo|--hostname)(?:=|\s+)\S+))*"
RISK = (
    re.compile(r"\bgh" + GH_GLOBAL_ARGS + r"\s+workflow\s+run\b", re.I),
    re.compile(r"\bgh" + GH_GLOBAL_ARGS + r"\s+run\s+rerun\b", re.I),
    re.compile(r"\bgh" + GH_GLOBAL_ARGS + r"\s+api\b[^\n]*", re.I),
    re.compile(r"\bcurl\b[^\n]*", re.I),
    re.compile(r"\b(?:create_workflow_dispatch|createWorkflowDispatch|create_repository_dispatch|createRepositoryDispatch|reRunWorkflow|reRunWorkflowFailedJobs|reRunJob|rerunWorkflow)\b"),
    re.compile(r"\bwget\b[^\n]*", re.I),
    re.compile(r"\b(?:Invoke-RestMethod|Invoke-WebRequest|irm|iwr)\b[^\n]*", re.I),
    re.compile(r"\b(?:requests|httpx)\.(?:post|request)\s*\([^\n]*", re.I),
    re.compile(r"\bfetch\s*\([^\n]*", re.I),
    re.compile(r"\baxios\.post\s*\([^\n]*", re.I),
    re.compile(r"\b(?:octokit|github)\.request\s*\([^\n]*", re.I),
)
STEP_USES = re.compile(r"""(?m)^( +)(?:-\s+)?(?:uses|'uses'|"uses"):[ \t]*(.*)$""")
# Explicit workflow-dispatch action families. Other custom actions still need review;
# no attempt is made to execute, fetch or trust remote action code.
DISPATCH_ACTIONS = {
    "peter-evans/repository-dispatch",
    "benc-uk/workflow-dispatch",
    "convictional/trigger-workflow-and-wait",
}

# GitHub REST rerun endpoints can recursively enqueue another attempt without
# calling the workflow/repository dispatch API at all.
REST_RERUN = re.compile(
    r"/actions/(?:runs/[^/\s]+/rerun(?:-failed-jobs)?|jobs/[^/\s]+/rerun)(?:\b|$)",
    re.I,
)

GH_POST = re.compile(r"(?:-X(?:=|\s*)POST|--method(?:=|\s+)POST|(?:-f|-F|--field|--raw-field|--input)(?=[ =]))", re.I)
CURL_POST = re.compile(r"(?:-X(?:=|\s*)POST|--request(?:=|\s+)POST|(?:-d|--data(?:-raw|-binary|-urlencode)?|--json|-F|--form(?:-string)?)(?=[ =@]))", re.I)
WGET_POST = re.compile(r"(?:--method(?:=|\s+)POST\b|--post-(?:data|file)(?:=|\s+))", re.I)
POWERSHELL_POST = re.compile(r"-Method\s+(?:['\"]?POST['\"]?)(?:\s|$)", re.I)

MAX_PATHS = 400
MAX_SOURCE = 200_000
MAX_TOTAL = 16_000_000


class EvidenceError(Exception):
    """Unverifiable immutable source: deny without printing untrusted text."""


def git(root: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_", "GITHUB_", "GH_"))}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
    try:
        cmd = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args],
                             cwd=root, env=env, capture_output=True, timeout=12,
                             check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("git_unavailable") from exc
    if cmd.returncode:
        raise EvidenceError("unverifiable_git_object")
    return cmd.stdout


def inventory(root: Path, sha: str) -> dict[str, str]:
    if not SHA.fullmatch(sha):
        raise EvidenceError("invalid_sha")
    raw = git(root, "ls-tree", "-r", "-z", sha, "--", ".github/workflows")
    if len(raw) > MAX_TOTAL:
        raise EvidenceError("oversized_inventory")
    sources: dict[str, str] = {}
    total = 0
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            info, path = record.split(b"\t", 1)
            mode, kind, oid = info.decode("ascii").split()
            name = path.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise EvidenceError("invalid_tree_entry") from exc
        if not WORKFLOW.fullmatch(name):
            continue
        if name in sources or len(sources) >= MAX_PATHS:
            raise EvidenceError("ambiguous_workflow_inventory")
        if mode not in ("100644", "100755") or kind != "blob" or not SHA.fullmatch(oid):
            raise EvidenceError("unsafe_workflow_object")
        # Query object metadata BEFORE reading the blob. An attacker can add
        # a multi-GB YAML blob; checking len() after capture_output=True
        # already incurred the memory allocation and could OOM hosted CI.
        size_bytes = git(root, "cat-file", "-s", oid)
        if not re.fullmatch(rb"[0-9]{1,12}\n?", size_bytes):
            raise EvidenceError("unverifiable_blob_size")
        declared_size = int(size_bytes)
        if declared_size > MAX_SOURCE:
            raise EvidenceError("oversized_workflow")
        raw_source = git(root, "cat-file", "blob", oid)
        if len(raw_source) != declared_size:
            raise EvidenceError("inconsistent_blob_size")
        total += declared_size
        if total > MAX_TOTAL:
            raise EvidenceError("oversized_inventory")
        try:
            sources[name] = raw_source.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise EvidenceError("invalid_workflow_encoding") from exc
    return sources


def workflow_run_event(source: str) -> bool:
    headers = list(ON.finditer(source))
    # Missing or duplicated trigger mapping is not reliable evidence.
    if len(headers) != 1:
        raise EvidenceError("ambiguous_event_mapping")
    start = headers[0]
    same_line = start.group(1).split("#", 1)[0].strip()
    if same_line:
        # YAML aliases/merge mappings can change the effective trigger without
        # changing any literal workflow_run token. Do not infer a safe trigger
        # from a flow collection whose elements are partly unknown.
        if re.search(r"(?<![A-Za-z0-9_])(?:[*&][A-Za-z_][A-Za-z0-9_-]*|<<\s*:)", same_line):
            raise EvidenceError("aliased_event_mapping")
        if same_line.startswith("[") and same_line.endswith("]"):
            tokens = [entry.strip() for entry in same_line[1:-1].split(",")]
            names = []
            for token in tokens:
                if not re.fullmatch(r"""(?:[A-Za-z_][A-Za-z0-9_]*|'[A-Za-z_][A-Za-z0-9_]*'|"[A-Za-z_][A-Za-z0-9_]*")""", token):
                    raise EvidenceError("unsupported_event_mapping")
                names.append(token.strip("'\""))
            if len(names) != len(set(names)):
                raise EvidenceError("duplicate_event")
            return "workflow_run" in names
        if same_line.startswith("{") and same_line.endswith("}"):
            # Duplicate inline YAML keys can be silently overwritten by
            # different parsers. Their trigger semantics are not trustworthy.
            possible_keys = re.findall(
                r"""(?:^|[, {])(?:'([A-Za-z_][A-Za-z0-9_]*)'|"([A-Za-z_][A-Za-z0-9_]*)"|([A-Za-z_][A-Za-z0-9_]*))\s*:""",
                same_line,
            )
            keys = [next(value for value in parts if value) for parts in possible_keys]
            if len(keys) != len(set(keys)):
                raise EvidenceError("duplicate_inline_event_mapping")
            return "workflow_run" in keys
        if same_line in ("workflow_run", "'workflow_run'", '"workflow_run"'):
            return True
        if re.fullmatch(r"['\"]?[A-Za-z_]+['\"]?", same_line):
            return False
        raise EvidenceError("unsupported_event_mapping")
    rest = source[start.end():]
    end = re.search(r"(?m)^[A-Za-z_][A-Za-z0-9_-]*\s*:", rest)
    block = rest[:end.start()] if end else rest
    if re.search(r"(?m)^ +(?:<<\s*:|[^\n]*[&*][A-Za-z0-9_-]+)", block):
        raise EvidenceError("aliased_event_mapping")
    # Valid YAML event mappings may be indented by 1, 2 or 4+ spaces.
    # Detect first-level mapping keys instead of assuming exactly two spaces;
    # a nested workflow_run inside another event is NOT a top-level trigger.
    rows = re.findall(
        r"(?m)^( +)((?:[A-Za-z_][A-Za-z0-9_]*|'[A-Za-z_][A-Za-z0-9_]*'|\"[A-Za-z_][A-Za-z0-9_]*\"))\s*:",
        block,
    )
    if not rows:
        raise EvidenceError("missing_event_mapping")
    minimum = min(len(spaces) for spaces, _ in rows)
    keys = [key.strip("'\"") for spaces, key in rows if len(spaces) == minimum]
    if len(keys) != len(set(keys)):
        raise EvidenceError("duplicate_event")
    return "workflow_run" in keys


def executable_steps(source: str) -> list[str]:
    steps: list[str] = []
    for match in RUN.finditer(source):
        indent = len(match.group(1))
        lines = [match.group(2)]
        after = source[match.end():]
        for line in after.splitlines():
            if not line.strip():
                lines.append("")
                continue
            depth = len(line) - len(line.lstrip(" "))
            if depth <= indent:
                break
            if not line.lstrip().startswith("#"):
                lines.append(line.strip())
        steps.append("\n".join(lines))
    return steps


def action_dispatch_sites(source: str) -> Counter[tuple[int, str]]:
    """Fingerprint dispatch action steps independent of legal YAML indentation."""
    sites: Counter[tuple[int, str]] = Counter()
    lines = source.splitlines()
    for index, line in enumerate(lines):
        match = STEP_USES.match(line)
        if match is None:
            continue
        reference = match.group(2).split("#", 1)[0].strip().strip("'\"")
        if reference.partition("@")[0].lower() not in DISPATCH_ACTIONS:
            continue
        uses_depth = len(match.group(1))
        # Find the nearest enclosing step list item at an equal or shallower
        # indentation. Do not reach into a preceding job or unrelated step.
        start = index
        while start >= 0:
            candidate = re.match(r"^( +)- ", lines[start])
            if candidate and len(candidate.group(1)) <= uses_depth:
                break
            text = lines[start]
            if text.strip() and not text.lstrip().startswith("#"):
                depth = len(text) - len(text.lstrip(" "))
                if depth < uses_depth and not candidate:
                    raise EvidenceError("unbounded_dispatch_step")
            start -= 1
        if start < 0:
            raise EvidenceError("missing_dispatch_step")
        step_depth = len(candidate.group(1))
        end = start + 1
        while end < len(lines):
            text = lines[end]
            if text.strip() and not text.lstrip().startswith("#"):
                depth = len(text) - len(text.lstrip(" "))
                if depth < step_depth or (
                    depth == step_depth and re.match(r"^ +- ", text)
                ):
                    break
            end += 1
        site = "\n".join(text.strip() for text in lines[start:end]
                         if text.strip() and not text.lstrip().startswith("#"))
        digest = hashlib.sha256(site.encode("utf-8")).hexdigest()
        sites[(len(RISK), digest)] += 1
    return sites


def join_wrapped_call_lines(source: str) -> str:
    """Join lines only inside balanced call parentheses, not unrelated steps.

    This is bounded static analysis, not a general YAML or source interpreter.
    """
    result = []
    depth = 0
    quoted = None
    escape = False
    for ch in source:
        if quoted is not None:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quoted:
                quoted = None
        elif ch in ("'", '"', "\x60"):
            quoted = ch
        elif ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        if ch == "\n" and depth:
            result.append(" ")
        else:
            result.append(ch)
    return "".join(result)


def dispatch_sites(source: str) -> Counter[tuple[int, str]]:
    """Count every distinct executable site, including repeated commands."""
    if not workflow_run_event(source):
        return Counter()
    sites: Counter[tuple[int, str]] = Counter()
    sites.update(action_dispatch_sites(source))
    for body in executable_steps(source):
        normalized = re.sub(r"\\\s*\n", " ", body)
        # YAML folded block scalars (>, >-, >+) join adjacent physical lines
        # into one shell command even without explicit backslashes. Treat
        # those lines as a single command for dispatch API origin detection.
        if body.lstrip().startswith(">"):
            normalized = " ".join(part.strip() for part in normalized.splitlines()
                                  if part.strip())
        wrapped = join_wrapped_call_lines(normalized)
        for detector, pattern in enumerate(RISK):
            # HTTP client methods can accept arguments across several lines.
            inspected = wrapped if detector in (7, 8, 9, 10) else normalized
            for match in pattern.finditer(inspected):
                if detector in (2, 3, 5, 6, 7, 8, 9, 10):
                    fragment = match.group(0)
                    if "/dispatches" not in fragment and not REST_RERUN.search(fragment):
                        continue
                    methods = {2: GH_POST, 3: CURL_POST, 5: WGET_POST, 6: POWERSHELL_POST}
                    if detector == 7:
                        if not re.search(r"\.(?:post\s*\(|request\s*\(\s*['\"]POST['\"])", fragment, re.I):
                            continue
                    elif detector == 8:
                        if not re.search(r"['\"]?method['\"]?\s*:\s*['\"]POST['\"]", fragment, re.I):
                            continue
                    elif detector == 9:
                        pass  # axios.post always sends a POST
                    elif detector == 10:
                        if not re.search(r"\.request\s*\(\s*['\"]POST(?:\s|['\"])", fragment, re.I):
                            continue
                    elif not methods[detector].search(fragment):
                        continue
                start = inspected.rfind("\n", 0, match.start()) + 1
                end = inspected.find("\n", match.end())
                if end < 0:
                    end = len(inspected)
                line = inspected[start:end].strip()
                digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
                sites[(detector, digest)] += 1
    return sites


def classify(source: str) -> bool:
    return bool(dispatch_sites(source))


def evaluate(root: Path, base: str, head: str) -> dict:
    if not root.is_dir() or root.is_symlink() or not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise EvidenceError("invalid_source_identity")
    git(root, "merge-base", "--is-ancestor", base, head)
    old = inventory(root, base)
    new = inventory(root, head)
    previous: Counter[tuple[str, int, str]] = Counter()
    current: Counter[tuple[str, int, str]] = Counter()
    for path, source in old.items():
        previous.update({(path, detector, digest): count
                         for (detector, digest), count in dispatch_sites(source).items()})
    for path, source in new.items():
        current.update({(path, detector, digest): count
                        for (detector, digest), count in dispatch_sites(source).items()})
    new_findings = sorted(path for (path, _detector, _digest) in (current - previous).elements())
    return {
        "base": base, "head": head,
        "scanned_base": len(old), "scanned_head": len(new),
        "previous_findings": sum(previous.values()),
        "current_findings": sum(current.values()),
        "new_findings": new_findings,
        "production_mutation_authorized": False,
        "merge_authorized": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.root, args.base, args.head)
    except EvidenceError as exc:
        print("WORKFLOW_RUN_DISPATCH_GATE=DENY reason=" + str(exc))
        print("PRODUCTION_MUTATION_AUTHORIZED=false MERGE_AUTHORIZED=false")
        return 2
    except (OSError, UnicodeError, ValueError):
        print("WORKFLOW_RUN_DISPATCH_GATE=DENY reason=unverifiable_source")
        print("PRODUCTION_MUTATION_AUTHORIZED=false MERGE_AUTHORIZED=false")
        return 2
    print("WORKFLOW_RUN_DISPATCH_GATE=" + ("DENY" if result["new_findings"] else "PASS"))
    print("SCANNED_BASE=" + str(result["scanned_base"]) +
          " SCANNED_HEAD=" + str(result["scanned_head"]) +
          " LEGACY=" + str(result["previous_findings"]))
    for path in result["new_findings"][:30]:
        print("NEW_RECURSIVE_DISPATCH=" + path)
    print("PRODUCTION_MUTATION_AUTHORIZED=false MERGE_AUTHORIZED=false")
    return 1 if result["new_findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
