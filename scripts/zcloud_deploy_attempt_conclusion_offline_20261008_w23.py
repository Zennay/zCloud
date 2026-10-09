"""Offline-only deployment attempt conclusion evidence contract.

This validator does not authorize production action. It rejects ambiguous,
contradictory or non-terminal records and never makes network calls.
"""
from datetime import datetime, timezone

TERMINAL = {"success", "failure", "cancelled", "timed_out", "skipped"}
ALLOWED = {"run_id", "attempt", "head_sha", "job_id", "conclusion", "completed_at"}

def is_terminal_attempt_evidence(record, *, run_id, attempt, head_sha):
    if not isinstance(record, dict) or set(record) != ALLOWED:
        return False
    if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
        return False
    if not isinstance(head_sha, str) or len(head_sha) != 40 or any(c not in "0123456789abcdef" for c in head_sha):
        return False
    if type(record["run_id"]) is not int or record["run_id"] != run_id:
        return False
    if type(record["attempt"]) is not int or record["attempt"] != attempt:
        return False
    if record["head_sha"] != head_sha or type(record["job_id"]) is not int or record["job_id"] <= 0:
        return False
    if type(record["conclusion"]) is not str or record["conclusion"] not in TERMINAL:
        return False
    stamp = record["completed_at"]
    if not isinstance(stamp, str) or not stamp.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0
    except (ValueError, OverflowError):
        return False

def has_unique_terminal_evidence(records, *, run_id, attempt, head_sha):
    """For forensic use only: exactly one terminal record per job, all valid."""
    if not isinstance(records, list) or not records:
        return False
    seen = set()
    for record in records:
        if not is_terminal_attempt_evidence(record, run_id=run_id, attempt=attempt, head_sha=head_sha):
            return False
        if record["job_id"] in seen:
            return False
        seen.add(record["job_id"])
    return True

def live_deploy_authorized(*args, **kwargs):
    """Offline fixtures are never live deployment authorization."""
    return False
