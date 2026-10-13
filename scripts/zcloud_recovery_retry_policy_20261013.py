#!/usr/bin/env python3
"""Offline fail-closed production-status recovery reference, not release authority.

No GitHub API, dispatch, VPS, browser, queue or production SQLite access.
Owners must provide independently trusted snapshots and a durable shared ledger.
"""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
from pathlib import Path

SCHEMA = "zcloud-recovery-retry-evidence-v1"
REPO = "Zennay/zCloud"
SHA = re.compile(r"^[0-9a-f]{40}$")
ACTIVE = {"queued", "in_progress", "pending", "waiting", "requested"}
TERMINAL = {"success", "failure", "cancelled", "timed_out", "neutral", "skipped", "action_required", "stale", "startup_failure"}
WORKFLOWS = {"deploy": "zcloud-vps-deploy.yml", "regression": "zcloud-regression-smoke.yml"}


class EvidenceError(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise EvidenceError(reason)


def good_sha(value):
    return isinstance(value, str) and bool(SHA.fullmatch(value))


def runs(snapshot, kind, sha):
    page = snapshot.get(kind)
    require(isinstance(page, dict), kind + "_pages_missing")
    require(page.get("complete") is True and page.get("has_next_page") is False, kind + "_pages_incomplete")
    require(page.get("sha") == sha and page.get("workflow") == WORKFLOWS[kind], kind + "_provenance")
    rows = page.get("runs")
    require(isinstance(rows, list) and len(rows) <= 10000, kind + "_invalid_runs")
    seen = set()
    matching = []
    for row in rows:
        require(isinstance(row, dict), kind + "_invalid_row")
        ident = (row.get("id"), row.get("run_attempt"))
        require(all(type(n) is int and n > 0 for n in ident) and ident not in seen, kind + "_ambiguous_attempt")
        seen.add(ident)
        require(good_sha(row.get("head_sha")), kind + "_invalid_sha")
        require(row.get("event") in {"push", "workflow_run", "workflow_dispatch"}, kind + "_invalid_event")
        status, conclusion = row.get("status"), row.get("conclusion")
        require(status in ACTIVE | {"completed"}, kind + "_invalid_status")
        require((status == "completed" and conclusion in TERMINAL) or
                (status in ACTIVE and conclusion is None), kind + "_contradictory_outcome")
        if row["head_sha"] == sha:
            require(row.get("head_branch") == "main", kind + "_wrong_branch")
            if kind == "deploy":
                require(row["event"] == "workflow_run", "untrusted_deploy_origin")
            matching.append(row)
    return matching


def classify(evidence):
    """Decision is observational; even READY never grants workflow dispatch."""
    try:
        require(isinstance(evidence, dict), "invalid_snapshot")
        require(evidence.get("schema") == SCHEMA and evidence.get("repository") == REPO, "untrusted_source")
        sha = evidence.get("main_sha")
        require(good_sha(sha), "invalid_main_sha")
        status = evidence.get("production_status")
        require(isinstance(status, dict) and status.get("complete") is True and status.get("sha") == sha, "missing_or_stale_status")
        values = status.get("statuses")
        require(isinstance(values, list), "invalid_status_array")
        require(all(isinstance(x, dict) and isinstance(x.get("context"), str) and isinstance(x.get("state"), str) for x in values), "invalid_status_entry")
        deploy, regression = runs(evidence, "deploy", sha), runs(evidence, "regression", sha)
        if any(x["context"] == "zcloud/vps-production" for x in values):
            decision, reason = "NOOP", "production_status_present"
        elif any(x["status"] in ACTIVE for x in deploy):
            decision, reason = "WAIT", "deploy_active"
        elif deploy:
            decision, reason = "DENY", "terminal_deploy_missing_status"
        elif any(x["status"] in ACTIVE for x in regression):
            decision, reason = "WAIT", "regression_active"
        elif not any(x["status"] == "completed" and x["conclusion"] == "success" and
                     x["event"] in {"push", "workflow_dispatch"} for x in regression):
            decision, reason = "WAIT", "no_trusted_regression"
        else:
            decision, reason = "READY_FOR_EXCLUSIVE_RESERVATION", "candidate_only"
        return {"decision": decision, "reason": reason, "sha": sha, "authorized": False}
    except EvidenceError as exc:
        return {"decision": "DENY", "reason": str(exc), "sha": None, "authorized": False}
    except (TypeError, ValueError):
        # Lists/dicts supplied for scalar fields must never crash the verifier.
        return {"decision": "DENY", "reason": "malformed_evidence_types", "sha": None, "authorized": False}


def reserve_reference(db_path, decision, *, intent="automatic"):
    """Demonstrate atomic SHA-keyed at-most-once reservation with BEGIN IMMEDIATE.

    This is for disposable/offline SQLite acceptance only. Hosted Actions
    ephemeral SQLite is NOT a shared durable ledger and cannot be deployed
    as the production idempotency mechanism. Ambiguous HTTP outcomes consume
    the reservation; manual re-arm is intentionally unsupported.
    """
    if not isinstance(decision, dict) or decision.get("decision") != "READY_FOR_EXCLUSIVE_RESERVATION":
        return False
    if decision.get("authorized") is not False or not good_sha(decision.get("sha")) or intent != "automatic":
        return False
    with sqlite3.connect(str(db_path), timeout=10, isolation_level=None) as conn:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS recovery_retry_reference ("
                         "repository TEXT NOT NULL, sha TEXT NOT NULL, workflow TEXT NOT NULL, "
                         "intent TEXT NOT NULL, state TEXT NOT NULL, "
                         "PRIMARY KEY(repository, sha, workflow, intent))")
            cursor = conn.execute("INSERT OR IGNORE INTO recovery_retry_reference "
                                  "(repository, sha, workflow, intent, state) VALUES (?, ?, ?, ?, ?)",
                                  (REPO, decision["sha"], WORKFLOWS["regression"], intent, "reserved"))
            inserted = cursor.rowcount == 1
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return inserted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    try:
        raw = args.snapshot.read_bytes()
        if len(raw) > 1048576:
            raise ValueError("oversize")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result
        snapshot = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        result = classify(snapshot)
    except (OSError, ValueError, UnicodeError, RecursionError):
        result = {"decision": "DENY", "reason": "ambiguous_or_unreadable_json", "sha": None, "authorized": False}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["decision"] in {"NOOP", "WAIT"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
