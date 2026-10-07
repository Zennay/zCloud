#!/usr/bin/env python3
"""Render a compact, sanitized operator summary for a zCloud production deploy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _clean(value: Any, *, limit: int = 240) -> str:
    text = str(value if value is not None else "").replace("\r", " ").replace("\n", " ").strip()
    text = text.replace("|", "\\|")
    return text[:limit] or "n/a"


def _load_evidence(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_idle_fields(payload: dict[str, Any] | None) -> tuple[str, str]:
    payload = payload or {}
    if not payload:
        return "n/a", "n/a"
    if payload.get("safe_idle") is True:
        return "entered", "none"
    if payload.get("restored_after_timeout") is True:
        status = "timeout-restored"
    elif payload.get("restored_after_error") is True:
        status = "error-restored"
    elif payload.get("restored") is True:
        status = "restored"
    else:
        status = "not-entered"

    blockers = payload.get("blocking_workers")
    if not isinstance(blockers, list) or not blockers:
        return status, "none"
    rendered = []
    for item in blockers:
        if not isinstance(item, dict):
            continue
        worker_id = str(item.get("worker_id") or "unknown")
        generating = str(bool(item.get("generating"))).lower()
        sending = str(bool(item.get("sending"))).lower()
        drain_status = str(item.get("drain_command_status") or "unknown")
        rendered.append(
            f"{worker_id} generating={generating} sending={sending} drain={drain_status}"
        )
    return status, "; ".join(rendered) or "none"


def build_summary(
    *,
    deploy_sha: str,
    deploy_decision: str,
    prewrite_decision: str,
    job_status: str,
    regression_run_id: str,
    regression_mode: str,
    run_url: str,
    artifact_name: str,
    evidence: dict[str, Any] | None = None,
    safe_idle: dict[str, Any] | None = None,
) -> str:
    evidence = evidence or {}
    safe_idle_status, safe_idle_blockers = _safe_idle_fields(safe_idle)
    deploy_requested = deploy_decision.lower() == "true"
    prewrite_confirmed = prewrite_decision.lower() == "true"

    if not deploy_requested:
        outcome = "Skipped before VPS mutation"
    elif not prewrite_confirmed:
        outcome = "Skipped because main moved before VPS writes"
    elif job_status.lower() == "success":
        outcome = "Production deploy green"
    else:
        outcome = "Production deploy failed"

    rows = [
        ("Outcome", outcome),
        ("Deploy SHA", deploy_sha),
        ("Job status", job_status),
        ("Deploy decision", deploy_decision),
        ("Prewrite decision", prewrite_decision),
        ("Regression run", regression_run_id),
        ("Regression mode", regression_mode),
        ("LKG snapshot", evidence.get("lkg_snapshot_id")),
        ("Runner", evidence.get("runner_name")),
        ("Machine", evidence.get("machine")),
        ("Health", evidence.get("health_summary")),
        ("Safe-idle", safe_idle_status),
        ("Safe-idle blockers", safe_idle_blockers),
        ("Evidence artifact", artifact_name),
        ("Workflow run", run_url),
    ]

    lines = [
        "## zCloud production deploy",
        "",
        "| Field | Value |",
        "| --- | --- |",
    ]
    lines.extend(f"| {_clean(key)} | {_clean(value)} |" for key, value in rows)
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--evidence")
    parser.add_argument("--safe-idle-state")
    parser.add_argument("--deploy-sha", default="")
    parser.add_argument("--deploy-decision", default="")
    parser.add_argument("--prewrite-decision", default="")
    parser.add_argument("--job-status", default="")
    parser.add_argument("--regression-run-id", default="")
    parser.add_argument("--regression-mode", default="")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--artifact-name", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    evidence_path = Path(args.evidence) if args.evidence else None
    safe_idle_path = Path(args.safe_idle_state) if args.safe_idle_state else None
    summary = build_summary(
        deploy_sha=args.deploy_sha,
        deploy_decision=args.deploy_decision,
        prewrite_decision=args.prewrite_decision,
        job_status=args.job_status,
        regression_run_id=args.regression_run_id,
        regression_mode=args.regression_mode,
        run_url=args.run_url,
        artifact_name=args.artifact_name,
        evidence=_load_evidence(evidence_path),
        safe_idle=_load_evidence(safe_idle_path),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(summary)
    print("ZCLOUD_DEPLOY_OPERATOR_SUMMARY_WRITTEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
