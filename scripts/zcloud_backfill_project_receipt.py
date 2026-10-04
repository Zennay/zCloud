#!/usr/bin/env python3
"""Backfill one project-state receipt from live zCloud runtime evidence."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import project_runtime as runtime  # noqa: E402


_QUEUE_TO_CI_STATUS = {
    "done": "success",
    "queued": "queued",
    "claimed": "in_progress",
    "running": "in_progress",
    "verifying": "in_progress",
}


def _load_project(runtime_root: Path, project_id: str) -> tuple[Path, dict]:
    projects_path = runtime_root / "projects.json"
    projects = json.loads(projects_path.read_text(encoding="utf-8"))
    if not isinstance(projects, list):
        raise RuntimeError("runtime-owned projects.json must be a list")
    project = next((item for item in projects if item.get("id") == project_id), None)
    if not project:
        raise RuntimeError(f"{project_id} project missing from runtime-owned projects.json")
    if str(project.get("status") or "").lower() == "archived":
        raise RuntimeError(f"{project_id} is archived; refusing receipt backfill")
    return projects_path, project


def _latest_queue_row(connection: sqlite3.Connection, project_id: str) -> dict:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(portfolio_queue)").fetchall()
    }
    if not columns:
        raise RuntimeError("portfolio_queue table missing from live SQLite state")
    order_column = "updated_at" if "updated_at" in columns else "created_at"
    if order_column not in columns:
        raise RuntimeError("portfolio_queue lacks updated_at/created_at ordering evidence")
    row = connection.execute(
        f"SELECT * FROM portfolio_queue WHERE project_id=? "
        f"ORDER BY {order_column} DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"no {project_id} portfolio queue evidence available")
    return dict(row)


def backfill_receipt(
    *,
    runtime_root: Path,
    project_id: str,
    source: str,
    workflow_run_id: str = "",
) -> dict:
    runtime_root = runtime_root.resolve()
    projects_path, project = _load_project(runtime_root, project_id)
    contract = runtime.project_contract(project_id)

    phase = str(project.get("phase") or "").strip()
    next_gate = str(project.get("next_step") or "").strip()
    if not phase or not next_gate:
        raise RuntimeError(f"runtime {project_id} phase/next_step is incomplete")

    db_path = runtime_root / "history.db"
    connection = sqlite3.connect(db_path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    runtime.init_tables(connection)

    try:
        queue_mode = str(contract.get("queue_mode") or "")
        latest: dict = {}
        if queue_mode == "human-gated":
            ci_status = "skipped"
            blocker = str(project.get("blocker") or next_gate).strip()
            action = "Recorded human-gated runtime state from canonical project catalog"
        else:
            latest = _latest_queue_row(connection, project_id)
            queue_status = str(latest.get("status") or "").lower()
            if queue_status not in _QUEUE_TO_CI_STATUS:
                raise RuntimeError(
                    f"latest {project_id} queue state is not admissible evidence: {queue_status}"
                )
            ci_status = _QUEUE_TO_CI_STATUS[queue_status]
            blocker = str(latest.get("blocker") or "").strip()
            action = (
                "Backfilled execution state from runtime-owned project catalog "
                "and SQLite queue evidence"
            )

        evidence = {
            "workflow_run_id": str(workflow_run_id or ""),
            "project_catalog": str(projects_path),
            "queue_database": str(db_path),
            "project_milestone_revision": project.get("milestone_revision"),
            "project_priority": project.get("priority"),
            "queue_mode": queue_mode,
            "lane_profile": contract.get("lane_profile"),
            "queue_id": latest.get("queue_id"),
            "queue_status": latest.get("status"),
            "queue_title": latest.get("title"),
            "queue_evidence": latest.get("evidence"),
            "queue_blocker": latest.get("blocker"),
            "evidence_marker": "PORTFOLIO_STATE_RECEIPT_BACKFILL_GREEN",
        }
        receipt = runtime.record_receipt(
            connection,
            project_id,
            phase=phase,
            action=action,
            ci_status=ci_status,
            blocker=blocker,
            next_gate=next_gate,
            source=source,
            evidence=evidence,
        )
        connection.commit()

        readback = connection.execute(
            "SELECT id,ci_status,source FROM project_state_receipts "
            "WHERE project_id=? ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if not readback:
            raise RuntimeError(f"{project_id} receipt readback missing after write")
        if readback["id"] != receipt["id"]:
            raise RuntimeError(f"{project_id} receipt readback id mismatch")
        if readback["ci_status"] != ci_status:
            raise RuntimeError(f"{project_id} receipt readback CI status mismatch")
        if readback["source"] != source:
            raise RuntimeError(f"{project_id} receipt readback source mismatch")
        return receipt
    finally:
        connection.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, default=Path("/home/ubuntu/zennay-cloud"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--source", default="github-actions:portfolio-state-receipt-backfill")
    parser.add_argument("--workflow-run-id", default="")
    args = parser.parse_args(argv)

    receipt = backfill_receipt(
        runtime_root=args.runtime_root,
        project_id=str(args.project).strip(),
        source=str(args.source).strip(),
        workflow_run_id=str(args.workflow_run_id).strip(),
    )
    print(
        "PORTFOLIO_STATE_RECEIPT_READBACK_GREEN "
        + json.dumps(
            {
                "id": receipt.get("id"),
                "project_id": receipt.get("project_id"),
                "ci_status": receipt.get("ci_status"),
                "source": receipt.get("source"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    print("PORTFOLIO_STATE_RECEIPT_BACKFILL_GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
