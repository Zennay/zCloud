#!/usr/bin/env python3
"""Read-only no-new-debt guard for self-hosted GitHub Actions matrix fail-fast.

Matrix strategy.fail-fast defaults to true. A failed matrix child can therefore
cancel another self-hosted deployment/proof job. This guard admits only new
potentially self-hosted matrix jobs with an explicit literal false policy.
Legacy risk remains grandfathered only for the same job, runner and strategy.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys

_SHA = re.compile(r"[a-f0-9]{40}\Z")
_MAX = 250_000
_MAX_FILES = 250
_HOSTED = frozenset(["macos-14","macos-15","macos-26","macos-latest","ubuntu-22.04","ubuntu-22.04-arm","ubuntu-24.04","ubuntu-24.04-arm","ubuntu-latest","windows-2022","windows-2025","windows-latest"])


def _is_hosted(label: str) -> bool:
    """Only explicitly known hosted runner labels; never infer from a prefix."""
    return label in _HOSTED


class AdmissionDenied(Exception):
    """Ambiguous or untrusted source input."""


def _git(root: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", *arguments], cwd=root, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdmissionDenied("git_unavailable") from exc
    if result.returncode:
        raise AdmissionDenied("git_command_failed")
    if len(result.stdout) > _MAX:
        raise AdmissionDenied("git_output_limit")
    return result.stdout


def _revision(root: Path, rev: str) -> None:
    if not _SHA.fullmatch(rev):
        raise AdmissionDenied("invalid_revision")
    actual = _git(root, "rev-parse", "--verify", rev + "^{commit}").decode().strip()
    if actual != rev:
        raise AdmissionDenied("revision_mismatch")


def _source(root: Path, sha: str, path: str) -> str | None:
    listing = _git(root, "ls-tree", sha, "--", path)
    if not listing:
        return None
    entry, sep, name = listing.partition(b"\t")
    if sep != b"\t" or name.rstrip(b"\n").decode("utf-8", "strict") != path:
        raise AdmissionDenied("unexpected_tree_entry")
    parts = entry.split()
    if len(parts) != 3 or parts[:2] not in (
        [b"100644", b"blob"], [b"100755", b"blob"],
    ):
        raise AdmissionDenied("unsafe_workflow_file_type")
    # Size checked BEFORE reading the blob; never materialize huge source.
    raw_size = _git(root, "cat-file", "-s", parts[2].decode("ascii"))
    try:
        size = int(raw_size.strip())
    except ValueError as exc:
        raise AdmissionDenied("invalid_blob_size") from exc
    if size > _MAX:
        raise AdmissionDenied("oversized_workflow")
    try:
        return _git(root, "cat-file", "blob", parts[2].decode("ascii")).decode("utf-8", "strict")
    except UnicodeError as exc:
        raise AdmissionDenied("invalid_utf8") from exc


def _strip_yaml_comment(line: str) -> str:
    """Respect YAML comment boundaries and quoted '#' runner labels.

    In YAML an unquoted '#' without preceding whitespace is scalar content,
    not a comment. Quotes can also contain '#'; blindly splitting at '#'
    can turn a private runner label into a trusted hosted runner label.
    """
    quote = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote == "'":
            if c == "'":
                if i + 1 < len(line) and line[i + 1] == "'":
                    i += 2
                    continue
                quote = None
        elif quote == '"':
            if c == "\\":
                i += 2
                continue
            if c == '"':
                quote = None
        elif c in ("'", '"'):
            quote = c
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def _code_lines(text: str) -> list[str]:
    if "\t" in text:
        raise AdmissionDenied("workflow_tabs")
    # Source scanner deliberately fails closed on complex YAML: inline mappings,
    # aliases and dynamic strategies are not interpreted as false.
    return [_strip_yaml_comment(line) for line in text.splitlines()]


def _job_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    jobs: list[tuple[str, list[str]]] = []
    in_jobs = False
    name = None
    body: list[str] = []

    def flush() -> None:
        if name is not None:
            jobs.append((name, body.copy()))

    for line in lines:
        if line and not line.startswith(" "):
            if in_jobs:
                flush()
                name, body = None, []
            if re.match(r"^(?:jobs|[\"\x27]jobs[\"\x27])\s*:", line) and line != "jobs:":
                raise AdmissionDenied("unsupported_jobs_mapping")
            in_jobs = line == "jobs:"
            continue
        if not in_jobs:
            continue
        match = re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*", line)
        if match:
            flush()
            name, body = match.group(1), []
        elif re.match(r"^  [^ ]", line) and line.strip():
            raise AdmissionDenied("unsupported_job_identifier")
        elif name is not None:
            body.append(line)
        elif line.strip():
            raise AdmissionDenied("unsupported_job_syntax")
    if in_jobs:
        flush()
    return jobs


def _property(body: list[str], name: str, indent: int) -> tuple[str | None, list[str]]:
    regex = re.compile(r"^" + (" " * indent) + re.escape(name) + r":\s*(.*?)\s*$")
    matches = [(i, m.group(1)) for i, line in enumerate(body) if (m := regex.match(line))]
    if not matches:
        return None, []
    if len(matches) != 1:
        raise AdmissionDenied("duplicate_job_key")
    at, value = matches[0]
    end = at + 1
    while end < len(body):
        s = body[end]
        if s.strip() and len(s) - len(s.lstrip(" ")) <= indent:
            break
        end += 1
    return value, body[at:end]


def _potentially_self_hosted(runs_on: str | None, section: list[str]) -> bool:
    if runs_on is None:
        # A matrix reusable-workflow job may indirectly use a VPS runner.
        return True
    if section and len(section) == 1 and _is_hosted(runs_on.strip("'\"")):
        return False
    # Anything dynamic, grouped, labeled, multiline or unknown is conservatively
    # treated as potentially self-hosted.
    return True


def risks(text: str) -> Counter[str]:
    lines = _code_lines(text)
    risky: Counter[str] = Counter()
    for job, body in _job_sections(lines):
        if any(re.match(r"^ {4,6}[\"\x27](?:strategy|matrix|fail-fast|runs-on)[\"\x27]\s*:", line) for line in body):
            raise AdmissionDenied("ambiguous_quoted_job_key")
        runner, runner_section = _property(body, "runs-on", 4)
        strategy, strategy_section = _property(body, "strategy", 4)
        if strategy is None:
            continue
        if not _potentially_self_hosted(runner, runner_section):
            continue
        if not strategy_section:
            continue
        matrix, _ = _property(body, "matrix", 6)
        # Inline/dynamic/alias strategies can conceal a matrix; deny rather
        # than infer a safe default from a partial parser.
        dynamic_strategy = bool(strategy.strip()) or any(
            re.match(r"^ {6}(?:<<|[&*])", line) for line in strategy_section
        )
        if matrix is None and not dynamic_strategy:
            continue
        fast, _ = _property(body, "fail-fast", 6)
        # Duplicate and inline keys are unsafe. The *only* safe value for
        # a supported block-style matrix is literal 'false'.
        if fast == "false" and not dynamic_strategy and matrix is not None:
            continue
        fingerprint = json.dumps(
            [job, runner_section, strategy_section], separators=(",", ":"),
            ensure_ascii=True,
        )
        risky[fingerprint] += 1
    return risky


def audit(root: Path, base: str, head: str) -> dict[str, object]:
    _revision(root, base)
    _revision(root, head)
    if _git(root, "rev-parse", "HEAD").decode().strip() != head:
        raise AdmissionDenied("checkout_head_mismatch")
    if _git(root, "merge-base", base, head).decode().strip() != base:
        raise AdmissionDenied("base_not_ancestor")
    changed = _git(
        root, "diff", "--no-renames", "--name-only", "-z", "--diff-filter=ACMRT",
        base, head, "--", ".github/workflows",
    )
    if changed and not changed.endswith(b"\0"):
        raise AdmissionDenied("truncated_diff")
    paths: list[str] = []
    for raw in filter(None, changed.split(b"\0")):
        try:
            path = raw.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise AdmissionDenied("invalid_diff_path") from exc
        if ".." in Path(path).parts or not path.startswith(".github/workflows/"):
            raise AdmissionDenied("unsafe_path")
        if path.count("/") == 2 and path.endswith((".yml", ".yaml")):
            paths.append(path)
        elif path.count("/") > 2:
            raise AdmissionDenied("nested_workflow_path")
    if len(paths) > _MAX_FILES:
        raise AdmissionDenied("too_many_workflows")
    previous_risks = introduced = 0
    for path in paths:
        before = risks(_source(root, base, path) or "")
        current_text = _source(root, head, path)
        if current_text is None:
            raise AdmissionDenied("changed_workflow_missing")
        current = risks(current_text)
        previous_risks += sum(before.values())
        introduced += sum((current - before).values())
    return {
        "policy": "zcloud-selfhosted-matrix-failfast-no-new-debt-v1",
        "base": base,
        "head": head,
        "changed_workflows": len(paths),
        "legacy_risks": previous_risks,
        "introduced_risks": introduced,
        "mutation_performed": False,
        "ok": introduced == 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        payload = audit(args.repo_root.resolve(), args.base, args.head)
    except (AdmissionDenied, OSError, UnicodeError, ValueError) as exc:
        payload = {
            "policy": "zcloud-selfhosted-matrix-failfast-no-new-debt-v1",
            "ok": False,
            "reason": str(exc) if isinstance(exc, AdmissionDenied) else "invalid_source",
            "mutation_performed": False,
        }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
