#!/usr/bin/env python3
"""Select safe, currently-unowned self-hosted workflow trust-debt candidates.

This utility is deliberately conservative. It never edits workflows and never
queries GitHub itself. Callers supply paths already owned by active PRs/lanes;
the selector scans the repository and ranks only read-only workflow candidates
that still show trust/provenance debt.

The goal is to make issue #633 coordination deterministic: prefer the safest
read-only remediation slice and avoid taking over a path already owned by a
parallel worker.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


WORKFLOW_SUFFIXES = {".yml", ".yaml"}

SELF_HOSTED_RE = re.compile(
    r"(?m)^\s*runs-on:\s*(?:self-hosted|\[[^\n\]]*\bself-hosted\b[^\n\]]*\])\s*$"
)
GENERIC_SELF_HOSTED_RE = re.compile(r"(?m)^\s*runs-on:\s*self-hosted\s*$")
FLOATING_CHECKOUT_RE = re.compile(
    r"(?m)^\s*-\s+uses:\s+actions/checkout@v\d+(?:\.\d+)?\s*(?:#.*)?$"
)
CHECKOUT_RE = re.compile(r"(?m)^(?P<indent>\s*)-\s+uses:\s+actions/checkout@[^\s#]+.*$")
PULL_REQUEST_TRIGGER_RE = re.compile(r"(?m)^\s{0,2}pull_request:\s*(?:$|\{)")
OWNER_GUARD_RE = re.compile(r"github\.actor\s*==\s*['\"]Zennay['\"]")
SAME_REPO_GUARD_RE = re.compile(
    r"github\.event\.pull_request\.head\.repo\.full_name\s*==\s*github\.repository"
)
RUNNER_GUARD_RE = re.compile(
    r"(?:scripts/zcloud_vps_runner_guard\.py|"
    r"RUNNER_NAME[^\n]*vps-bb300bba|"
    r"hostname[^\n]*vps-bb300bba)"
)

# Keep this intentionally broad: false negatives here could make the selector
# recommend a live-mutating workflow as a "safe" remediation candidate.
MUTATION_PATTERNS = (
    re.compile(r"\bsystemctl\s+(?:start|stop|restart|reload|enable|disable|daemon-reload)\b"),
    re.compile(r"\bservice\s+\S+\s+(?:start|stop|restart|reload)\b"),
    re.compile(r"\b(?:INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM|REPLACE\s+INTO)\b", re.I),
    re.compile(r"\bgh\s+api\b[^\n]*(?:--method|-X)\s+(?:POST|PUT|PATCH|DELETE)\b", re.I),
    re.compile(r"\bcurl\b[^\n]*(?:-X|--request)\s+(?:POST|PUT|PATCH|DELETE)\b", re.I),
    re.compile(r"\bgit\s+push\b"),
    re.compile(r"\b(?:requests|httpx)\.(?:post|put|patch|delete)\s*\(", re.I),
    re.compile(r"\bmethod\s*=\s*['\"](?:POST|PUT|PATCH|DELETE)['\"]", re.I),
    re.compile(r"\b(?:os\.remove|Path\([^\n]+\)\.unlink|shutil\.rmtree)\s*\("),
)


@dataclass(frozen=True)
class Candidate:
    path: str
    priority: int
    findings: tuple[str, ...]
    reason: str


def _checkout_steps(text: str) -> Iterable[str]:
    """Yield checkout step snippets using YAML indentation as a safe boundary."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = CHECKOUT_RE.match(line)
        if not match:
            continue
        indent = len(match.group("indent"))
        chunk = [line]
        for following in lines[index + 1 :]:
            stripped = following.lstrip()
            if not stripped:
                chunk.append(following)
                continue
            following_indent = len(following) - len(stripped)
            if following_indent <= indent and stripped.startswith("-"):
                break
            chunk.append(following)
        yield "\n".join(chunk)


def _checkout_findings(text: str) -> list[str]:
    findings: list[str] = []
    steps = list(_checkout_steps(text))
    if not steps:
        return findings

    if FLOATING_CHECKOUT_RE.search(text):
        findings.append("floating_checkout_action")

    if any("persist-credentials: false" not in step for step in steps):
        findings.append("checkout_credentials_not_explicitly_disabled")

    if any(not re.search(r"(?m)^\s+ref:\s*\S+", step) for step in steps):
        findings.append("checkout_exact_ref_not_evident")

    return findings


def _looks_mutating(text: str) -> bool:
    return any(pattern.search(text) for pattern in MUTATION_PATTERNS)


def inspect_workflow(path: Path, repo_root: Path) -> Candidate | None:
    text = path.read_text(encoding="utf-8")
    if not SELF_HOSTED_RE.search(text):
        return None

    findings: list[str] = []
    if GENERIC_SELF_HOSTED_RE.search(text):
        findings.append("generic_self_hosted_runner")

    findings.extend(_checkout_findings(text))

    if not RUNNER_GUARD_RE.search(text):
        findings.append("zcloud_vps_runner_guard_not_evident")

    if (
        PULL_REQUEST_TRIGGER_RE.search(text)
        and not (OWNER_GUARD_RE.search(text) and SAME_REPO_GUARD_RE.search(text))
    ):
        findings.append("pr_self_hosted_without_owner_same_repo_guard")

    if not findings or _looks_mutating(text):
        return None

    if "pr_self_hosted_without_owner_same_repo_guard" in findings:
        priority = 0
        reason = "PR-triggered self-hosted workflow lacks trusted owner/same-repo admission"
    elif "generic_self_hosted_runner" in findings:
        priority = 1
        reason = "generic self-hosted runner can be narrowed before less direct debt"
    elif "floating_checkout_action" in findings:
        priority = 2
        reason = "floating checkout can be pinned without changing workflow semantics"
    else:
        priority = 3
        reason = "remaining read-only runner provenance debt"

    return Candidate(
        path=path.relative_to(repo_root).as_posix(),
        priority=priority,
        findings=tuple(sorted(set(findings))),
        reason=reason,
    )


def select_candidates(repo_root: Path, owned_paths: set[str]) -> list[Candidate]:
    workflows = repo_root / ".github" / "workflows"
    if not workflows.is_dir():
        raise FileNotFoundError(f"workflow directory not found: {workflows}")

    candidates: list[Candidate] = []
    for path in sorted(workflows.iterdir()):
        if path.suffix.lower() not in WORKFLOW_SUFFIXES or not path.is_file():
            continue
        candidate = inspect_workflow(path, repo_root)
        if candidate is None or candidate.path in owned_paths:
            continue
        candidates.append(candidate)

    return sorted(candidates, key=lambda item: (item.priority, item.path))


def _load_owned_paths(json_path: Path | None, repeated: list[str]) -> set[str]:
    owned = {item.strip() for item in repeated if item.strip()}
    if json_path is None:
        return owned

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        values = data
    elif isinstance(data, dict) and isinstance(data.get("paths"), list):
        values = data["paths"]
    else:
        raise ValueError("owned paths JSON must be an array or an object with a 'paths' array")

    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("owned path entries must be non-empty strings")
        owned.add(value.strip())
    return owned


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--owned-path", action="append", default=[])
    parser.add_argument("--owned-paths-json", type=Path)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--require-candidate", action="store_true")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    owned = _load_owned_paths(args.owned_paths_json, args.owned_path)
    candidates = select_candidates(repo_root, owned)
    shown = candidates[: max(0, args.limit)]

    payload = {
        "candidate_count": len(candidates),
        "owned_path_count": len(owned),
        "candidates": [asdict(item) for item in shown],
    }
    print(json.dumps(payload, sort_keys=True, indent=2))

    if args.require_candidate and not candidates:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
