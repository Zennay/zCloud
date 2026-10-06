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
) -> str:
    evidence = evidence or {}
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
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(summary)
    print("ZCLOUD_DEPLOY_OPERATOR_SUMMARY_WRITTEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
