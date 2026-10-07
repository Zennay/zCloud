#!/usr/bin/env python3
"""Bounded, read-only inventory of workflows with automatic live-mutation authority.

This is a conservative static audit. A workflow is a candidate when it has at
least one non-manual trigger and contains a known mutation primitive. The audit
never claims that a candidate will mutate on every trigger; job-level guards
may further restrict execution and should be reviewed before remediation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = "zcloud-live-mutation-trigger-audit-v1"
DEFAULT_MAX_FILES = 500
DEFAULT_MAX_BYTES = 1_000_000

MANUAL_TRIGGER = "workflow_dispatch"
KNOWN_TRIGGERS = {
    "branch_protection_rule",
    "check_run",
    "check_suite",
    "create",
    "delete",
    "deployment",
    "deployment_status",
    "discussion",
    "discussion_comment",
    "fork",
    "gollum",
    "issue_comment",
    "issues",
    "label",
    "merge_group",
    "milestone",
    "page_build",
    "project",
    "project_card",
    "project_column",
    "public",
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "pull_request_target",
    "push",
    "registry_package",
    "release",
    "repository_dispatch",
    "schedule",
    "status",
    "watch",
    "workflow_call",
    "workflow_dispatch",
    "workflow_run",
}

MUTATION_PATTERNS = (
    (
        "zcloud_queue_write",
        re.compile(
            r"/api/portfolio-queue.{0,120}(?:POST|PUT|PATCH|DELETE)"
            r"|(?:POST|PUT|PATCH|DELETE).{0,120}/api/portfolio-queue",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "zcloud_runner_control",
        re.compile(r"/api/runner-control", re.IGNORECASE),
    ),
    (
        "systemd_mutation",
        re.compile(
            r"\bsystemctl\s+(?:--[^\s]+\s+|-[^\s]+\s+)*"
            r"(?:restart|start|stop|enable|disable|reenable|daemon-reload|reset-failed)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "git_push",
        re.compile(r"\bgit\s+push\b", re.IGNORECASE),
    ),
    (
        "github_api_write",
        re.compile(
            r"\bgh\s+api\b.{0,160}(?:--method|-X)\s+"
            r"(?:POST|PUT|PATCH|DELETE)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "http_write",
        re.compile(
            r"\bcurl\b.{0,160}(?:-X|--request)\s*"
            r"(?:POST|PUT|PATCH|DELETE)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "container_mutation",
        re.compile(
            r"\b(?:docker|sudo\s+docker)\s+(?:compose\s+up|build|run|restart|stop|rm)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_config_write",
        re.compile(
            r"(?:\binstall\b|\bcp\b|\btee\b).{0,180}/etc/(?:systemd|caddy|nginx|docker)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)


class AuditError(ValueError):
    """Raised when the inventory cannot be proven complete."""


@dataclass(frozen=True)
class WorkflowRecord:
    path: str
    triggers: tuple[str, ...]
    automatic_triggers: tuple[str, ...]
    mutation_reasons: tuple[str, ...]
    classification: str


def _normalise_key(raw: str) -> str:
    key = raw.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in {"'", '"'}:
        key = key[1:-1]
    return key.strip()


def _parse_inline_triggers(value: str) -> set[str]:
    value = value.strip()
    if not value:
        return set()
    if value.startswith("[") and value.endswith("]"):
        items = value[1:-1].split(",")
        return {
            _normalise_key(item)
            for item in items
            if _normalise_key(item) in KNOWN_TRIGGERS
        }
    key = _normalise_key(value)
    return {key} if key in KNOWN_TRIGGERS else set()


def parse_triggers(text: str) -> tuple[str, ...]:
    """Extract top-level GitHub Actions event keys without a YAML dependency."""
    lines = text.splitlines()
    triggers: set[str] = set()
    in_on = False

    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        top = re.match(r'^(["\']?on["\']?)\s*:\s*(.*?)\s*$', line)
        if top:
            in_on = True
            triggers.update(_parse_inline_triggers(top.group(2)))
            continue

        if in_on and re.match(r"^[^\s#][^:]*:", line):
            break

        if in_on:
            nested = re.match(r"^ {2}([A-Za-z_][A-Za-z0-9_]*)\s*:", line)
            if nested and nested.group(1) in KNOWN_TRIGGERS:
                triggers.add(nested.group(1))

    return tuple(sorted(triggers))


def mutation_reasons(text: str) -> tuple[str, ...]:
    return tuple(
        reason for reason, pattern in MUTATION_PATTERNS if pattern.search(text)
    )


def classify(triggers: Iterable[str], reasons: Iterable[str]) -> str:
    trigger_set = set(triggers)
    reason_set = set(reasons)
    if not reason_set:
        return "non_mutating_or_unclassified"
    automatic = trigger_set - {MANUAL_TRIGGER, "workflow_call"}
    if automatic:
        return "automatic_live_mutation_candidate"
    if MANUAL_TRIGGER in trigger_set or "workflow_call" in trigger_set:
        return "manual_or_called_mutation_candidate"
    return "mutation_without_recognised_trigger"


def audit_directory(
    root: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    root = root.resolve()
    if max_files < 1 or max_bytes < 1:
        raise AuditError("bounds_must_be_positive")
    if not root.is_dir():
        raise AuditError("workflow_root_missing")

    candidates = sorted(
        [*root.glob("*.yml"), *root.glob("*.yaml")],
        key=lambda item: item.name,
    )
    if len(candidates) > max_files:
        raise AuditError("workflow_file_limit_exceeded")

    records: list[WorkflowRecord] = []
    for path in candidates:
        if path.is_symlink():
            raise AuditError(f"symlink_workflow:{path.name}")
        try:
            stat = path.stat()
        except OSError as exc:
            raise AuditError(f"workflow_stat_failed:{path.name}") from exc
        if stat.st_size > max_bytes:
            raise AuditError(f"workflow_file_too_large:{path.name}")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise AuditError(f"workflow_read_failed:{path.name}") from exc

        triggers = parse_triggers(text)
        reasons = mutation_reasons(text)
        automatic = tuple(
            trigger
            for trigger in triggers
            if trigger not in {MANUAL_TRIGGER, "workflow_call"}
        )
        records.append(
            WorkflowRecord(
                path=path.name,
                triggers=triggers,
                automatic_triggers=automatic,
                mutation_reasons=reasons,
                classification=classify(triggers, reasons),
            )
        )

    automatic_candidates = [
        record
        for record in records
        if record.classification == "automatic_live_mutation_candidate"
    ]
    manual_candidates = [
        record
        for record in records
        if record.classification == "manual_or_called_mutation_candidate"
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_root": ".github/workflows",
        "inventory_complete": True,
        "mutation_performed": False,
        "workflow_count": len(records),
        "automatic_live_mutation_candidate_count": len(automatic_candidates),
        "manual_or_called_mutation_candidate_count": len(manual_candidates),
        "records": [asdict(record) for record in records],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory automatic GitHub Actions live-mutation candidates."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(".github/workflows"),
        help="Workflow directory (default: .github/workflows).",
    )
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument(
        "--require-clear",
        action="store_true",
        help="Exit 2 when automatic live-mutation candidates are present.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = audit_directory(
            args.root,
            max_files=args.max_files,
            max_bytes=args.max_bytes,
        )
    except AuditError as exc:
        error = {
            "schema_version": SCHEMA_VERSION,
            "inventory_complete": False,
            "mutation_performed": False,
            "error": str(exc),
        }
        print(json.dumps(error, sort_keys=True))
        return 1

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            "ZCLOUD_LIVE_MUTATION_TRIGGER_AUDIT "
            f"workflows={report['workflow_count']} "
            "automatic_candidates="
            f"{report['automatic_live_mutation_candidate_count']} "
            "manual_candidates="
            f"{report['manual_or_called_mutation_candidate_count']} "
            "mutation_performed=false"
        )
        for record in report["records"]:
            if record["classification"] == "automatic_live_mutation_candidate":
                print(
                    "candidate="
                    f"{record['path']} "
                    f"triggers={','.join(record['automatic_triggers'])} "
                    f"reasons={','.join(record['mutation_reasons'])}"
                )

    if args.require_clear and report["automatic_live_mutation_candidate_count"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
