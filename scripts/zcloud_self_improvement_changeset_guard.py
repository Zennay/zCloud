#!/usr/bin/env python3
"""Fail-closed provenance guard for zCloud self-improvement change-sets.

The guard has two bounded modes:
- pr proves a candidate for main comes from a distinct branch/change-set;
- push proves an exact commit observed on main is associated with a
  merged pull request targeting main.

It intentionally does not mutate GitHub, the VPS, SQLite, services, or runtime
configuration. Repository rules/branch protection remain the preventive layer;
this guard provides executable detection evidence even when that administration
surface is unavailable to the worker.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_PROTECTED_BASES = {"main", "master"}


class GuardFailure(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _require_sha(value: str, code: str) -> str:
    value = str(value or "").strip().lower()
    if not _SHA_RE.fullmatch(value):
        raise GuardFailure(code)
    return value


def _normalize_ref(value: str, code: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 240:
        raise GuardFailure(code)
    return value


def _bounded_changed_files(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        path = str(raw or "").strip().replace("\\", "/")
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise GuardFailure("unsafe_changed_path")
        if len(path) > 300:
            raise GuardFailure("changed_path_too_long")
        if path not in result:
            result.append(path)
        if len(result) > 500:
            raise GuardFailure("too_many_changed_files")
    if not result:
        raise GuardFailure("empty_change_set")
    return result


def validate_pr(
    *,
    base_sha: str,
    head_sha: str,
    base_ref: str,
    head_ref: str,
    changed_files: Iterable[str],
) -> dict[str, Any]:
    base_sha = _require_sha(base_sha, "invalid_base_sha")
    head_sha = _require_sha(head_sha, "invalid_head_sha")
    base_ref = _normalize_ref(base_ref, "invalid_base_ref")
    head_ref = _normalize_ref(head_ref, "invalid_head_ref")
    files = _bounded_changed_files(changed_files)

    if base_sha == head_sha:
        raise GuardFailure("head_equals_base")
    if head_ref == base_ref or head_ref.lower() in _PROTECTED_BASES:
        raise GuardFailure("head_ref_is_protected_base")

    return {
        "ok": True,
        "mode": "pr",
        "base_ref": base_ref,
        "separate_head_ref": True,
        "change_count": len(files),
        "head_distinct_from_base": True,
    }


def _merged_to_main(pr: Any) -> bool:
    if not isinstance(pr, dict):
        return False
    merged_at = pr.get("merged_at")
    base = pr.get("base")
    base_ref = base.get("ref") if isinstance(base, dict) else None
    return bool(merged_at and base_ref == "main")


def validate_push(*, head_sha: str, ref: str, associated_prs: Any) -> dict[str, Any]:
    _require_sha(head_sha, "invalid_head_sha")
    ref = _normalize_ref(ref, "invalid_push_ref")
    if ref != "refs/heads/main":
        raise GuardFailure("push_ref_not_main")
    if not isinstance(associated_prs, list):
        raise GuardFailure("invalid_associated_pr_payload")

    merged = [pr for pr in associated_prs if _merged_to_main(pr)]
    if not merged:
        raise GuardFailure("main_push_without_merged_pr")

    return {
        "ok": True,
        "mode": "push",
        "ref": ref,
        "associated_pr_count": min(len(associated_prs), 1000),
        "merged_main_pr_count": min(len(merged), 1000),
    }


def git_changed_files(base_sha: str, head_sha: str) -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRD", f"{base_sha}...{head_sha}"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise GuardFailure("git_diff_failed")
    return completed.stdout.splitlines()


def _load_json(path: str) -> Any:
    target = Path(path)
    if target.is_symlink():
        raise GuardFailure("associated_pr_payload_symlink")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise GuardFailure("invalid_associated_pr_payload")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)

    pr = sub.add_parser("pr")
    pr.add_argument("--base-sha", required=True)
    pr.add_argument("--head-sha", required=True)
    pr.add_argument("--base-ref", required=True)
    pr.add_argument("--head-ref", required=True)

    push = sub.add_parser("push")
    push.add_argument("--head-sha", required=True)
    push.add_argument("--ref", required=True)
    push.add_argument("--associated-prs-json", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "pr":
            result = validate_pr(
                base_sha=args.base_sha,
                head_sha=args.head_sha,
                base_ref=args.base_ref,
                head_ref=args.head_ref,
                changed_files=git_changed_files(args.base_sha, args.head_sha),
            )
        else:
            result = validate_push(
                head_sha=args.head_sha,
                ref=args.ref,
                associated_prs=_load_json(args.associated_prs_json),
            )
    except GuardFailure as exc:
        print(json.dumps({"ok": False, "code": exc.code}, sort_keys=True))
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
