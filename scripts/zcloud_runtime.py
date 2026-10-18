#!/usr/bin/env python3
"""CLI for zCloud evidence receipts and portfolio resource admission."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import project_runtime as runtime  # noqa: E402


def connect(path: Path):
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    runtime.init_tables(conn)
    return conn


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "history.db")
    sub = parser.add_subparsers(dest="command", required=True)

    receipt = sub.add_parser("receipt")
    receipt.add_argument("--project", required=True)
    receipt.add_argument("--phase", default="")
    receipt.add_argument("--action", default="")
    receipt.add_argument("--commit", default="")
    receipt.add_argument("--ci-status", default="")
    receipt.add_argument("--blocker", default="")
    receipt.add_argument("--next-gate", default="")
    receipt.add_argument("--source", default="cli")
    receipt.add_argument("--evidence-json", default="{}")

    acquire = sub.add_parser("acquire")
    acquire.add_argument("--project", required=True)
    acquire.add_argument("--owner", required=True)
    acquire.add_argument("--lease-seconds", type=int, default=1800)
    acquire.add_argument("--metadata-json", default="{}")

    release = sub.add_parser("release")
    release.add_argument("--project", required=True)
    release.add_argument("--owner", required=True)

    sub.add_parser("status")

    args = parser.parse_args(argv)
    # A rejected receipt must not even initialize its SQLite database. This
    # applies to CLI producers in addition to the core record_receipt guard.
    evidence = None
    if args.command == "receipt":
        runtime.project_contract(args.project)
        runtime.validated_receipt_text(
            phase=args.phase, action=args.action, commit_sha=args.commit,
            blocker=args.blocker, next_gate=args.next_gate, source=args.source,
        )
        normalized_status = str(args.ci_status or "").lower()
        if normalized_status not in {
            "", "queued", "in_progress", "success", "failure", "cancelled", "skipped"
        }:
            raise ValueError("unsupported ci_status")
        evidence = json.loads(args.evidence_json)
        if not isinstance(evidence, dict):
            raise ValueError("--evidence-json must be an object")
        runtime._serialize_receipt_evidence(evidence)
    if args.command in {"acquire", "release"}:
        if not runtime._valid_resource_owner(args.owner):
            raise ValueError("--owner must be nonempty, valid UTF-8, NUL-free and <= 200 characters")
    if args.command == "acquire":
        runtime.project_contract(args.project)
        args.validated_metadata = json.loads(
            args.metadata_json,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid resource metadata")),
        )
        if runtime._resource_metadata_json(args.validated_metadata) is None:
            raise ValueError("--metadata-json must be a finite object of <= 4000 UTF-8 bytes")
    conn = connect(args.db)
    try:
        if args.command == "receipt":
            result = runtime.record_receipt(
                conn,
                args.project,
                phase=args.phase,
                action=args.action,
                commit_sha=args.commit,
                ci_status=args.ci_status,
                blocker=args.blocker,
                next_gate=args.next_gate,
                source=args.source,
                evidence=evidence,
            )
            conn.commit()
            print(json.dumps({"ok": True, "receipt": result}, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "acquire":
            result = runtime.acquire_resource(
                conn,
                args.project,
                args.owner,
                lease_seconds=args.lease_seconds,
                metadata=args.validated_metadata,
            )
            conn.commit()
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result.get("acquired") else 75
        if args.command == "release":
            result = runtime.release_resource(conn, args.project, args.owner)
            conn.commit()
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result.get("released") else 1
        result = runtime.resource_status(conn)
        conn.commit()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
