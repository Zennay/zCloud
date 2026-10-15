#!/usr/bin/env python3
"""Offline fail-closed Git-tree ratchet for directly piped remote shell in Actions.

This is a narrow source-risk tripwire, NOT a YAML parser, runtime security
boundary or authorization to deploy. Unmodified workflow debt is grandfathered.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
SCALAR = re.compile(r"^(?P<indent>\s*)(?:-\s+)?(?P<key>run|shell)\s*:\s*(?P<value>.*)$")
SHELL_EXEC = r"(?:(?:sudo\s+(?:-[A-Za-z]+\s+)*|/usr/bin/env\s+))?(?:/usr/bin/|/bin/)?(?:bash|sh|zsh)\b"
REMOTE = re.compile(r"\b(?:curl|wget)\b[^\n|;]*\|\s*" + SHELL_EXEC, re.I)
SUBSTITUTION = re.compile(SHELL_EXEC + r"\s+<\(\s*(?:curl|wget)\b", re.I)
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 512, 1024 * 1024, 32 * 1024 * 1024


class SourceError(ValueError):
    pass


def git(repo: Path, *argv: str) -> bytes:
    p = subprocess.run(["git", "-C", str(repo), *argv],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       check=False, timeout=25)
    if p.returncode:
        raise SourceError("Git evidence unavailable or invalid")
    return p.stdout


def verify_commit(repo: Path, sha: str) -> None:
    if not SHA.fullmatch(sha):
        raise SourceError("Expected immutable 40-character lower-case commit SHA")
    got = git(repo, "rev-parse", "--verify", sha + "^{commit}").decode().strip()
    if got != sha:
        raise SourceError("Resolved Git object differs from requested commit")


def committed_workflows(repo: Path, sha: str) -> dict[str, bytes]:
    verify_commit(repo, sha)
    entries = git(repo, "ls-tree", "-r", "-z", "--full-tree", sha, ".github/workflows").split(b"\0")
    found: dict[str, bytes] = {}
    total = 0
    for entry in entries:
        if not entry:
            continue
        try:
            meta, raw_path = entry.split(b"\t", 1)
            mode, typ, object_id = meta.decode("ascii").split(" ")
            path = raw_path.decode("utf-8", "strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceError("Malformed Git tree entry") from exc
        if mode not in ("100644", "100755") or typ != "blob" or not SHA.fullmatch(object_id):
            raise SourceError("Unsafe workflow tree entry")
        if not path.startswith(".github/workflows/"):
            raise SourceError("Unexpected Git tree path")
        if not path.endswith((".yml", ".yaml")):
            continue
        if path in found or len(found) >= MAX_FILES:
            raise SourceError("Duplicate or excessive workflow inventory")
        size = git(repo, "cat-file", "-s", object_id).decode().strip()
        if not size.isdecimal() or int(size) > MAX_FILE_BYTES:
            raise SourceError("Oversized or invalid workflow blob")
        total += int(size)
        if total > MAX_TOTAL_BYTES:
            raise SourceError("Excessive workflow bytes")
        data = git(repo, "cat-file", "blob", object_id)
        if len(data) != int(size) or b"\0" in data:
            raise SourceError("Inconsistent or binary workflow blob")
        data.decode("utf-8", "strict")
        found[path] = data
    if not found:
        raise SourceError("No committed GitHub Actions workflow evidence")
    return found


def extract_run_scripts(yaml: str) -> list[str]:
    """Scan literal run scalars; no full YAML semantic claim."""
    lines = yaml.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = SCALAR.match(line)
        i += 1
        if not m or m.group("key") != "run" or line.lstrip().startswith("#"):
            continue
        raw = m.group("value").strip()
        if raw.startswith("#"):
            continue
        if raw.startswith(("|", ">")):
            indent = len(m.group("indent"))
            body = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                if nxt.strip() and not nxt.lstrip().startswith("#"):
                    body.append(nxt.strip())
                i += 1
            blocks.append("\n".join(body))
        elif raw:
            blocks.append(raw.split(" #", 1)[0])
    return blocks


def findings(data: bytes) -> int:
    text_value = data.decode("utf-8", "strict")
    matched = 0
    for script in extract_run_scripts(text_value):
        command = re.sub(r"\\\s*\n\s*", " ", script)
        matched += len(REMOTE.findall(command)) + len(SUBSTITUTION.findall(command))
    return matched


@dataclass(frozen=True)
class Result:
    policy: str
    baseline_findings: int
    candidate_findings: int
    new_risky_workflows: int
    source_complete: bool
    release_authorized: bool = False
    deployment_authorized: bool = False
    mutation_performed: bool = False


def audit(repo: Path, base: str, head: str) -> Result:
    before = committed_workflows(repo, base)
    after = committed_workflows(repo, head)
    git(repo, "merge-base", "--is-ancestor", base, head)
    head_bad = {p for p, v in after.items() if findings(v)}
    # Whole-workflow identity: relocating pre-existing code to elevated context
    # cannot reuse the old debt exception.
    delta = {p for p in head_bad if before.get(p) != after[p]}
    return Result("zcloud-remote-shell-no-new-debt-v1",
                  sum(findings(v) for v in before.values()),
                  sum(findings(v) for v in after.values()), len(delta), True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=".")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    a = p.parse_args(argv)
    try:
        v = audit(Path(a.repo).resolve(), a.base, a.head)
    except (SourceError, OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        print(json.dumps({"policy": "zcloud-remote-shell-no-new-debt-v1",
                          "source_complete": False, "new_risky_workflows": None,
                          "release_authorized": False,
                          "deployment_authorized": False,
                          "mutation_performed": False}, sort_keys=True))
        return 2
    print(json.dumps(vars(v), sort_keys=True))
    return 1 if v.new_risky_workflows else 0


if __name__ == "__main__":
    raise SystemExit(main())
