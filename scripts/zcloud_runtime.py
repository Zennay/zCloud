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


def _validated_owner(value: str) -> str:
    """Reject ambiguous/truncated CLI lease identities before opening SQLite.

    The runtime currently persists owner_id with a 200-character maximum.
    Do not silently truncate or normalize: callers must be able to renew and
    release with exactly the identity supplied at acquisition.
    """
    if not value or not value.strip() or len(value) > 200 or "\x00" in value:
        raise ValueError("--owner must be nonempty, NUL-free and <= 200 characters")
    return value


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
    if args.command in {"acquire", "release"}:
        _validated_owner(args.owner)
    conn = connect(args.db)
    try:
        if args.command == "receipt":
            evidence = json.loads(args.evidence_json)
            if not isinstance(evidence, dict):
                raise ValueError("--evidence-json must be an object")
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
            metadata = json.loads(args.metadata_json)
            if not isinstance(metadata, dict):
                raise ValueError("--metadata-json must be an object")
            result = runtime.acquire_resource(
                conn,
                args.project,
                args.owner,
                lease_seconds=args.lease_seconds,
                metadata=metadata,
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
