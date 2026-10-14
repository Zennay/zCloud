#!/usr/bin/env python3
"""Monotonic source-only PR-target risk guard for changed Actions workflows.

Reads immutable git objects; never dispatches Actions or authorizes a release.
This deliberately supplements, rather than replaces, trusted-main workflow
admission and human review. Detection is conservative, not a YAML evaluator.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess

SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
PR_SOURCE = re.compile(r"\b(?:github\.event\.pull_request\.head\.(?:sha|ref)|github\.head_ref|github\.event\.pull_request\.head\.repo)\b", re.I)
CHECKOUT = re.compile(r"(?m)^\s*-\s*uses\s*:\s*['\"]?actions/checkout@", re.I)
RUN_STEP = re.compile(r"(?m)^\s*-\s*run\s*:", re.I)
PR_TARGET = re.compile(r"\bpull_request_target\b")


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
    for line in source.splitlines():
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
            if ch == "#" and quote is None:
                break
            useful.append(ch)
        output.append("".join(useful))
    return "\n".join(output) + "\n"


def risk_set(source: str) -> set[str]:
    """Return coarse, monotonic reasons (no source text or secrets exposed)."""
    if not source:
        return set()
    clean = _content_without_comments(source)
    anchors = list(re.finditer(r"(?m)^['\"]?on['\"]?:[ \t]*(.*)$", clean))
    if len(anchors) != 1:
        raise Unverified("ambiguous_event_trigger")
    # Only a root-level event block or supported inline event forms qualify.
    event_region = clean[anchors[0].start():]
    match = re.search(r"(?m)^[A-Za-z_][\w-]*:[ \t]*", clean[anchors[0].end():])
    if match:
        event_region = clean[anchors[0].start():anchors[0].end() + match.start()]
    if not PR_TARGET.search(event_region):
        return set()
    risk = set()
    # PR-target events may inherit a write-scoped GITHUB_TOKEN unless the
    # workflow explicitly narrows root permissions. Deny newly omitted roots,
    # even if every step looks read-only in this syntax-only scan.
    if not re.search(r"(?m)^permissions:[ \t]*", clean):
        risk.add("missing_explicit_root_permissions")
    # Merge aliases or YAML anchors can obscure the effective trigger and
    # permissions. Do not allow a changed PR-target job to use them.
    if re.search(r"(?m)^\s*<<\s*:|\s[&*][A-Za-z][\w-]*", clean):
        risk.add("yaml_alias_or_merge")
    if len(re.findall(r"(?m)^permissions:\s*", clean)) > 1 or len(anchors) > 1:
        risk.add("ambiguous_permissions_or_trigger")
    # Fail closed on granting ANY explicit write scope, regardless of where
    # permissions are nested. Harmless comments are not counted.
    if re.search(r"(?m)^\s*permissions\s*:\s*write-all\s*$", clean):
        risk.add("write_all")
    if re.search(r"(?m)^\s*[A-Za-z][\w-]*\s*:\s*['\"]?write['\"]?\s*(?:$|[},])", clean) or re.search(
        r"\b[A-Za-z][\w-]*\s*:\s*['\"]?write['\"]?\s*(?=[},])", clean
    ):
        risk.add("write_permission")
    if re.search(r"\bsecrets\s*\.", clean, re.I) or re.search(r"\bsecrets\s*\[", clean, re.I):
        risk.add("secret_reference")
    if CHECKOUT.search(clean) and PR_SOURCE.search(clean):
        risk.add("untrusted_pr_checkout")
    if RUN_STEP.search(clean) and PR_SOURCE.search(clean):
        risk.add("pr_source_in_run_job")
    return risk


def evaluate(root: Path, base: str, head: str) -> tuple[int, list[tuple[str, str]]]:
    root = root.resolve(strict=True)
    paths = changed_workflows(root, base, head)
    findings = []
    for path in paths:
        original = source_at(root, base, path, absent_ok=True)
        candidate = source_at(root, head, path)
        # Existing risk may be reduced, but never newly introduced. This
        # preserves already-owned legacy debt without legitimizing it.
        additional = risk_set(candidate) - risk_set(original)
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
