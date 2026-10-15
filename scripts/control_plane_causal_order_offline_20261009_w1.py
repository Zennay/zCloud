"""Offline-only reference for ordered control-plane event receipts.

This module MUST NOT be wired to production authorization, queues, or workers.
"""
import re

_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_EVENTS = frozenset({"requested", "admitted", "started", "completed", "failed", "cancelled"})
_TRANSITIONS = {
    None: frozenset({"requested"}),
    "requested": frozenset({"admitted", "cancelled"}),
    "admitted": frozenset({"started", "cancelled"}),
    "started": frozenset({"completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

def validate_sequence(receipts):
    """Return (accepted, reason); evidence is untrusted and non-authorizing."""
    if type(receipts) is not list or not 1 <= len(receipts) <= 1000:
        return False, "invalid_receipts"
    previous = None
    expected_sequence = 0
    run_id = None
    for receipt in receipts:
        if type(receipt) is not dict or set(receipt) != {"run_id", "sequence", "event"}:
            return False, "invalid_receipt_shape"
        identifier = receipt["run_id"]
        if type(identifier) is not str or not _ID.fullmatch(identifier):
            return False, "invalid_run_id"
        if run_id is None:
            run_id = identifier
        elif identifier != run_id:
            return False, "mixed_run_ids"
        seq = receipt["sequence"]
        if type(seq) is not int or seq != expected_sequence:
            return False, "noncontiguous_sequence"
        event = receipt["event"]
        if type(event) is not str or event not in _EVENTS:
            return False, "unknown_event"
        if event not in _TRANSITIONS[previous]:
            return False, "invalid_transition"
        previous = event
        expected_sequence += 1
    if previous not in {"completed", "failed", "cancelled"}:
        return False, "unterminated_sequence"
    return True, "valid_offline_only"
