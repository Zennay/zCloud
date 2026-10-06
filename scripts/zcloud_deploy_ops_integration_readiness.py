#!/usr/bin/env python3
"""Report whether open deploy-ops PRs are safe to enter the serialized integration window."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable


SHA40 = re.compile(r"^[0-9a-f]{40}$")
DEPLOY_OPS_MARKER = re.compile(r"\bdeploy[- ]ops\b", re.IGNORECASE)
DEPLOY_BRANCH = re.compile(r"^(?:worker/)?deploy-ops[-/]|^fix/deploy-|^worker/cloud-deploy-")
REGRESSION_WORKFLOW = "zCloud regression smoke"


@dataclass(frozen=True)
class Readiness:
    number: int
    title: str
    head: str
    head_sha: str
    behind_by: int | None
    ahead_by: int | None
    regression: str
    decision: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "head": self.head,
            "head_sha": self.head_sha,
            "behind_by": self.behind_by,
            "ahead_by": self.ahead_by,
            "regression": self.regression,
            "decision": self.decision,
            "reasons": list(self.reasons),
        }


def is_deploy_ops_pr(pr: dict[str, Any]) -> bool:
    title = str(pr.get("title") or "")
    body = str(pr.get("body") or "")
    head = str((pr.get("head") or {}).get("ref") or "")
    # Body matches are deliberately limited to the opening scope declaration.
    # Later coordination sections often mention deploy-ops only to say that a
    # control-plane PR does not overlap it; those references must not opt in.
    declared_scope = body[:500]
    return bool(
        DEPLOY_OPS_MARKER.search(title)
        or DEPLOY_BRANCH.search(head)
        or DEPLOY_OPS_MARKER.search(declared_scope)
    )


def latest_exact_head_regression(
    workflow_runs: Iterable[dict[str, Any]], head_sha: str
) -> tuple[str, dict[str, Any] | None]:
    candidates = [
        run
        for run in workflow_runs
        if str(run.get("name") or "") == REGRESSION_WORKFLOW
        and str(run.get("head_sha") or "") == head_sha
    ]
    if not candidates:
        return "missing", None
    candidates.sort(
        key=lambda run: (
            str(run.get("created_at") or ""),
            int(run.get("id") or 0),
        ),
        reverse=True,
    )
    run = candidates[0]
    status = str(run.get("status") or "unknown")
    conclusion = str(run.get("conclusion") or "")
    if status != "completed":
        return "in_progress", run
    if conclusion == "success":
        return "success", run
    return f"failed:{conclusion or 'unknown'}", run


def classify(
    pr: dict[str, Any],
    compare: dict[str, Any] | None,
    workflow_runs: Iterable[dict[str, Any]],
) -> Readiness:
    number = int(pr.get("number") or 0)
    title = str(pr.get("title") or "")
    head = str((pr.get("head") or {}).get("ref") or "")
    head_sha = str((pr.get("head") or {}).get("sha") or "")

    reasons: list[str] = []
    if not SHA40.fullmatch(head_sha):
        reasons.append("invalid_head_sha")

    behind_by: int | None = None
    ahead_by: int | None = None
    if compare is None:
        reasons.append("compare_evidence_unavailable")
    else:
        try:
            behind_by = int(compare.get("behind_by"))
            ahead_by = int(compare.get("ahead_by"))
        except (TypeError, ValueError):
            reasons.append("compare_evidence_invalid")

    regression, _ = latest_exact_head_regression(workflow_runs, head_sha)

    if reasons:
        decision = "evidence_unavailable"
    elif behind_by is not None and behind_by > 0:
        decision = "restack_required"
        reasons.append(f"behind_main:{behind_by}")
        if regression == "success":
            reasons.append("green_regression_is_pre_restack")
    elif regression == "missing":
        decision = "revalidation_required"
        reasons.append("exact_head_regression_missing")
    elif regression == "in_progress":
        decision = "revalidation_in_progress"
        reasons.append("exact_head_regression_not_terminal")
    elif regression.startswith("failed:"):
        decision = "revalidation_failed"
        reasons.append(regression.replace("failed:", "exact_head_regression_", 1))
    else:
        decision = "ready"

    return Readiness(
        number=number,
        title=title,
        head=head,
        head_sha=head_sha,
        behind_by=behind_by,
        ahead_by=ahead_by,
        regression=regression,
        decision=decision,
        reasons=tuple(reasons),
    )


class GithubReader:
    def __init__(self, repo: str, token: str):
        if "/" not in repo or repo.startswith("/") or repo.endswith("/"):
            raise ValueError("repo must be owner/name")
        if not token:
            raise ValueError("GitHub token is required")
        self.repo = repo
        self.token = token

    def get(self, path: str) -> Any:
        if not path.startswith("/"):
            raise ValueError("GitHub API path must start with /")
        url = "https://api.github.com" + path
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "zcloud-deploy-ops-readiness",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"GitHub API {exc.code} for {path}: {body}") from exc

    def open_pulls(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, 11):
            chunk = self.get(
                f"/repos/{self.repo}/pulls?state=open&per_page=100&page={page}"
            )
            if not isinstance(chunk, list):
                raise RuntimeError("pull request response is not a list")
            result.extend(chunk)
            if len(chunk) < 100:
                break
        else:
            raise RuntimeError("open PR pagination exceeded bounded 1000-item limit")
        return result

    def compare(self, base: str, head_sha: str) -> dict[str, Any]:
        encoded_base = urllib.parse.quote(base, safe="")
        encoded_head = urllib.parse.quote(head_sha, safe="")
        result = self.get(
            f"/repos/{self.repo}/compare/{encoded_base}...{encoded_head}"
        )
        if not isinstance(result, dict):
            raise RuntimeError("compare response is not an object")
        return result

    def workflow_runs(self, head_sha: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"head_sha": head_sha, "per_page": 100})
        result = self.get(f"/repos/{self.repo}/actions/runs?{query}")
        runs = result.get("workflow_runs") if isinstance(result, dict) else None
        if not isinstance(runs, list):
            raise RuntimeError("workflow-runs response is invalid")
        return runs


def inventory(reader: GithubReader, base: str) -> list[Readiness]:
    rows: list[Readiness] = []
    for pr in reader.open_pulls():
        if not is_deploy_ops_pr(pr):
            continue
        head_sha = str((pr.get("head") or {}).get("sha") or "")
        if not SHA40.fullmatch(head_sha):
            rows.append(classify(pr, None, []))
            continue
        compare = reader.compare(base, head_sha)
        runs = reader.workflow_runs(head_sha)
        rows.append(classify(pr, compare, runs))
    return sorted(rows, key=lambda row: row.number)


def render_markdown(rows: list[Readiness], repo: str, base: str) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.decision] = counts.get(row.decision, 0) + 1

    lines = [
        "# zCloud deploy-ops integration readiness",
        "",
        f"Repository: `{repo}` · base: `{base}` · deploy-ops PRs: **{len(rows)}**",
        "",
        "> Technical read-only readiness only. **ready never authorizes merge or deploy**; serialized ownership and current handoff gates still apply.",
        "",
    ]
    if counts:
        lines.append(
            " · ".join(f"`{key}` **{counts[key]}**" for key in sorted(counts))
        )
        lines.append("")
    lines.extend(
        [
            "| PR | head | behind | regression | decision |",
            "|---:|---|---:|---|---|",
        ]
    )
    for row in rows:
        short_sha = row.head_sha[:12] if row.head_sha else "?"
        behind = "?" if row.behind_by is None else str(row.behind_by)
        lines.append(
            f"| #{row.number} | `{short_sha}` | {behind} | "
            f"`{row.regression}` | **{row.decision}** |"
        )
    if not rows:
        lines.append("| — | — | — | — | no deploy-ops PRs |")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--base", default="main")
    parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="environment variable containing a read-only GitHub token",
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    token = os.environ.get(args.token_env, "")
    try:
        rows = inventory(GithubReader(args.repo, token), args.base)
    except (RuntimeError, ValueError) as exc:
        print(f"ZCLOUD_DEPLOY_OPS_READINESS_ERROR: {exc}", file=sys.stderr)
        return 2

    if args.format == "markdown":
        print(render_markdown(rows, args.repo, args.base), end="")
    else:
        print(
            json.dumps(
                {
                    "repository": args.repo,
                    "base": args.base,
                    "count": len(rows),
                    "results": [row.as_dict() for row in rows],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
