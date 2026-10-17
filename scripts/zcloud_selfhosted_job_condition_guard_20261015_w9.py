#!/usr/bin/env python3
"""Prevent new job-level status-check bypasses on potentially self-hosted Actions.

A GitHub Actions job with if: always()/failure()/!cancelled() may run
after its prerequisite admission job failed. Offline no-new-debt verification
preserves identical legacy job conditions; it never mutates VPS or artifacts.
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
_HOSTED = frozenset(["macos-14","macos-15","macos-26","macos-latest","ubuntu-22.04","ubuntu-22.04-arm","ubuntu-24.04","ubuntu-24.04-arm","ubuntu-latest","windows-2022","windows-2025","windows-latest"])


def _is_hosted(label: str) -> bool:
    """Only explicitly known hosted runner labels; never infer from a prefix."""
    return label in _HOSTED
_MAX = 250_000


class AdmissionDenied(Exception):
    pass


def git(root: Path, *args: str) -> bytes:
    try:
        p = subprocess.run(
            ["git", *args], cwd=root, check=False, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdmissionDenied("git_unavailable") from exc
    if p.returncode != 0:
        raise AdmissionDenied("git_failed")
    if len(p.stdout) > _MAX:
        raise AdmissionDenied("git_response_too_large")
    return p.stdout


def commit(root: Path, value: str) -> None:
    if not _SHA.fullmatch(value):
        raise AdmissionDenied("invalid_sha")
    if git(root, "rev-parse", "--verify", value + "^{commit}").decode().strip() != value:
        raise AdmissionDenied("wrong_sha")


def read(root: Path, sha: str, path: str) -> str | None:
    item = git(root, "ls-tree", sha, "--", path)
    if not item:
        return None
    header, sep, reported = item.partition(b"\t")
    if sep != b"\t" or reported.rstrip(b"\n").decode("utf-8", "strict") != path:
        raise AdmissionDenied("wrong_tree_path")
    metadata = header.split()
    if len(metadata) != 3 or metadata[:2] not in (
        [b"100644", b"blob"], [b"100755", b"blob"]
    ):
        raise AdmissionDenied("unsafe_file_mode")
    oid = metadata[2].decode("ascii")
    if int(git(root, "cat-file", "-s", oid).strip()) > _MAX:
        raise AdmissionDenied("oversized_workflow")
    try:
        return git(root, "cat-file", "blob", oid).decode("utf-8", "strict")
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


def lines_without_comments(content: str) -> list[str]:
    if "\t" in content:
        raise AdmissionDenied("tabs_in_yaml")
    return [_strip_yaml_comment(line) for line in content.splitlines()]


def jobs_of(content: str) -> list[tuple[str, list[str]]]:
    lines = lines_without_comments(content)
    items: list[tuple[str, list[str]]] = []
    active = False
    name: str | None = None
    body: list[str] = []

    def store() -> None:
        if name is not None:
            items.append((name, list(body)))

    for line in lines:
        if line and not line.startswith(" "):
            if active:
                store()
                name, body = None, []
            if re.match(r"^(?:jobs|['\"]jobs['\"])\s*:", line) and line != "jobs:":
                raise AdmissionDenied("unsupported_jobs_mapping")
            active = line == "jobs:"
            continue
        if not active:
            continue
        m = re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*", line)
        if m:
            store()
            name, body = m.group(1), []
        elif re.match(r"^  [^ ]", line) and line.strip():
            raise AdmissionDenied("unrecognized_job_id")
        elif name is not None:
            body.append(line)
        elif line.strip():
            raise AdmissionDenied("malformed_jobs_mapping")
    if active:
        store()
    return items


def property_section(body: list[str], key: str) -> tuple[str | None, list[str]]:
    regex = re.compile(r"^ {4}" + re.escape(key) + r":\s*(.*?)\s*$")
    hits = [(i, m.group(1)) for i, line in enumerate(body) if (m := regex.match(line))]
    if not hits:
        return None, []
    if len(hits) != 1:
        raise AdmissionDenied("duplicate_job_property")
    begin, value = hits[0]
    end = begin + 1
    while end < len(body):
        line = body[end]
        if line.strip() and len(line) - len(line.lstrip(" ")) <= 4:
            break
        end += 1
    return value, body[begin:end]


def status_override(expr: str) -> bool:
    compact = re.sub(r"\s+", "", expr).lower()
    # A status function suppresses GitHub's implicit success() requirement.
    # Reject all non-leading/compared success() usages: success()==0,
    # success()==null, and custom truthiness checks can evaluate true on a
    # failed prerequisite. Only an explicit positive AND prefix is admitted.
    candidate = compact
    if candidate.startswith("${{") and candidate.endswith("}}"):
        candidate = candidate[3:-2]
    if "success()" in candidate and not (
        candidate == "success()" or candidate.startswith("success()&&")
    ):
        return True
    # GitHub Actions injects success() automatically only if no job status
    # function is present. Detect status functions capable of bypassing a
    # failed prerequisite or explicitly cancelling admission.
    return (
        any(re.search(r"(?<![\w])" + f + r"\(", compact) for f in ("always", "failure", "cancelled"))
        or "!success(" in compact
        or "!cancelled(" in compact
        or "success()==false" in compact.replace("\'", "").replace('"', "")
        or "success()!=true" in compact.replace("\'", "").replace('"', "")
        or "false==success()" in compact.replace("\'", "").replace('"', "")
        or "true!=success()" in compact.replace("\'", "").replace('"', "")
        or "||" in compact
        or compact.startswith(("*", "&", "!"))
    )


def vulnerabilities(content: str) -> Counter[str]:
    risky: Counter[str] = Counter()
    for name, body in jobs_of(content):
        if any(re.match(r"^ {4}<<\s*:", line) for line in body):
            raise AdmissionDenied("opaque_job_merge")
        if any(re.match(r"^ {4}['\"](?:runs-on|if)['\"]\s*:", line) for line in body):
            raise AdmissionDenied("quoted_job_property")
        run, run_source = property_section(body, "runs-on")
        expr, if_source = property_section(body, "if")
        if expr is None:
            continue
        if run_source and len(run_source) == 1 and _is_hosted((run or "").strip("'\"")):
            continue
        # Support YAML folded conditionals by examining each continuation,
        # but refuse opaque tags and aliases even if they hide status funcs.
        full = " ".join([expr, *(line.strip() for line in if_source[1:])])
        if status_override(full) or (expr.startswith(("*", "&"))):
            fingerprint = json.dumps([name, run_source, if_source], separators=(",", ":"))
            risky[fingerprint] += 1
    return risky


def audit(root: Path, base: str, head: str) -> dict[str, object]:
    commit(root, base)
    commit(root, head)
    if git(root, "rev-parse", "HEAD").decode().strip() != head:
        raise AdmissionDenied("head_checkout_mismatch")
    if git(root, "merge-base", base, head).decode().strip() != base:
        raise AdmissionDenied("base_not_ancestor")
    changed = git(
        root, "diff", "--no-renames", "--name-only", "--diff-filter=ACMRT", "-z",
        base, head, "--", ".github/workflows",
    )
    if changed and not changed.endswith(b"\0"):
        raise AdmissionDenied("incomplete_diff")
    files = []
    for item in filter(None, changed.split(b"\0")):
        path = item.decode("utf-8", "strict")
        if ".." in Path(path).parts or not path.startswith(".github/workflows/"):
            raise AdmissionDenied("invalid_path")
        if path.count("/") > 2:
            raise AdmissionDenied("nested_workflow")
        if path.endswith((".yml", ".yaml")):
            files.append(path)
    if len(files) > 250:
        raise AdmissionDenied("too_many_files")
    existing = introduced = 0
    for file in files:
        prior = vulnerabilities(read(root, base, file) or "")
        latest_content = read(root, head, file)
        if latest_content is None:
            raise AdmissionDenied("missing_candidate")
        now = vulnerabilities(latest_content)
        existing += sum(prior.values())
        introduced += sum((now - prior).values())
    return {
        "policy": "zcloud-selfhosted-job-condition-bypass-ratchet-v1",
        "base": base,
        "head": head,
        "changed_workflows": len(files),
        "legacy_risks": existing,
        "new_risks": introduced,
        "mutation_performed": False,
        "ok": introduced == 0,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--repo-root", type=Path, default=Path("."))
    a = p.parse_args(argv)
    try:
        payload = audit(a.repo_root.resolve(), a.base, a.head)
    except (AdmissionDenied, OSError, UnicodeError, ValueError) as exc:
        payload = {
            "policy": "zcloud-selfhosted-job-condition-bypass-ratchet-v1",
            "ok": False,
            "reason": str(exc) if isinstance(exc, AdmissionDenied) else "invalid_source",
            "mutation_performed": False,
        }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
