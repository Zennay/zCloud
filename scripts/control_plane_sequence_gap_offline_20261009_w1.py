"""Offline-only reference for detecting gaps in per-worker event sequences.

This is NOT an authenticated runtime admission control. It never writes state.
"""
MAX_EVENTS = 1000
MAX_ID = 128

def inspect_sequence(events, *, expected_worker, expected_generation, last_sequence):
    """Return a non-authorizing diagnostic; reject ambiguous or discontinuous evidence."""
    deny = lambda reason: {"continuous": False, "reason": reason, "authorizes_action": False}
    def valid_id(value):
        return (isinstance(value, str) and 1 <= len(value) <= MAX_ID
                and all(c.isascii() and (c.isalnum() or c in "._:-") for c in value))
    if not valid_id(expected_worker) or not valid_id(expected_generation):
        return deny("invalid_identity")
    if type(last_sequence) is not int or not 0 <= last_sequence <= 2**63 - 1:
        return deny("invalid_cursor")
    if type(events) not in (list, tuple) or not 1 <= len(events) <= MAX_EVENTS:
        return deny("invalid_batch")
    cursor = last_sequence
    for entry in events:
        if type(entry) is not dict or len(entry) != 3 or set(entry) != {"worker", "generation", "sequence"}:
            return deny("invalid_event")
        if entry["worker"] != expected_worker or entry["generation"] != expected_generation:
            return deny("identity_mismatch")
        sequence = entry["sequence"]
        if type(sequence) is not int or not 0 <= sequence <= 2**63 - 1:
            return deny("invalid_sequence")
        if sequence != cursor + 1:
            return deny("sequence_gap_or_replay")
        cursor = sequence
    return {"continuous": True, "reason": "continuous_offline_only",
            "last_sequence": cursor, "authorizes_action": False}
