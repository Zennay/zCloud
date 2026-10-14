#!/usr/bin/env python3
"""Audit whether zCloud keeps technical dashboard depth behind one-step disclosure."""

from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path
from typing import Any

SCHEMA = "zcloud-progressive-disclosure-audit-v1"
MAX_BYTES = 2 * 1024 * 1024


class AuditError(ValueError):
    pass


def _read_source(path: Path) -> str:
    try:
        info = path.lstat()
    except OSError as exc:
        raise AuditError(f"source_unreadable:{path.name}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise AuditError(f"source_not_regular:{path.name}")
    if info.st_size > MAX_BYTES:
        raise AuditError(f"source_too_large:{path.name}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AuditError(f"source_invalid_utf8:{path.name}") from exc


def _function_slice(source: str, start: str, end: str) -> str:
    start_at = source.find(start)
    if start_at < 0:
        raise AuditError(f"function_missing:{start}")
    end_at = source.find(end, start_at + len(start))
    if end_at < 0:
        raise AuditError(f"function_boundary_missing:{start}")
    return source[start_at:end_at]


def _check(name: str, ok: bool, reason: str) -> dict[str, Any]:
    return {"id": name, "ok": bool(ok), "reason": "ok" if ok else reason}


def audit(app_source: str, enhancements_source: str) -> dict[str, Any]:
    worker = _function_slice(app_source, "function workerDetailPanel(p){", "async function controlWorker(")
    evidence = _function_slice(enhancements_source, "function evidencePanel(p){", "  function qPanel(p){")
    readiness = _function_slice(enhancements_source, "function readinessPanel(p){", "  function resourcePanel(){")
    resource = _function_slice(enhancements_source, "function resourcePanel(){", "  function incidentPanel(){")
    incident = _function_slice(enhancements_source, "function incidentPanel(){", "  function milestonePanel(p){")

    checks = [
        _check(
            "worker_technical_details_collapsed",
            '<details class="worker-details"><summary>Meer details</summary>' in worker
            and all(token in worker for token in ("Worker-ID", "Conversation-ID", "Heartbeat", "Branch / PR", "Lease")),
            "worker technical identity/provenance is not kept behind one disclosure",
        ),
        _check(
            "evidence_sources_collapsed",
            '<details class="section-details evidence-details"><summary>Sources and technical details</summary>' in evidence,
            "evidence provenance is not kept behind one disclosure",
        ),
        _check(
            "ftmo_test_details_collapsed",
            '<details class="section-details readiness-details"><summary>Technical test details</summary>' in readiness,
            "FTMO technical test metrics are not kept behind one disclosure",
        ),
        _check(
            "incident_technical_details_collapsed",
            '<details class="section-details incident-details"><summary>Technical details</summary>' in incident,
            "incident technical detail is not kept behind one disclosure",
        ),
        _check(
            "resource_technical_details_collapsed",
            "<details" in resource
            and "resource-tech-panel" in resource
            and ("Technical details" in resource or "Meer details" in resource),
            "CPU/RAM/weight detail is rendered directly instead of behind one disclosure",
        ),
    ]
    missing = [item["id"] for item in checks if not item["ok"]]
    return {
        "schema": SCHEMA,
        "status": "complete" if not missing else "needs_hardening",
        "checks": checks,
        "missing": missing,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, default=Path("public/app.js"))
    parser.add_argument("--enhancements", type=Path, default=Path("public/enhancements.js"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = audit(_read_source(args.app), _read_source(args.enhancements))
    except AuditError as exc:
        result = {
            "schema": SCHEMA,
            "status": "incomplete",
            "reason": str(exc),
            "mutation_performed": False,
        }
        print(json.dumps(result, sort_keys=True))
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"status={result['status']}")
        for item in result["checks"]:
            print(f"{item['id']}={'ok' if item['ok'] else item['reason']}")
    if args.require_complete and result["status"] != "complete":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
