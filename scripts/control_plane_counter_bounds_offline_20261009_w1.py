"""Offline-only bounded monotonic counter evidence reference.

This module is not imported by production and never authorizes a control action.
Inputs must be native JSON-compatible integer counters (bool is not an int here).
"""
MAX_COUNTER = (1 << 63) - 1

def assess(previous, current, *, allow_equal=True):
    """Return bounded observation; fail closed on malformed or rollback evidence."""
    if type(previous) is not int or type(current) is not int:
        return {"valid": False, "reason": "non_integer"}
    if not (0 <= previous <= MAX_COUNTER and 0 <= current <= MAX_COUNTER):
        return {"valid": False, "reason": "out_of_range"}
    if current < previous:
        return {"valid": False, "reason": "rollback"}
    if current == previous and not allow_equal:
        return {"valid": False, "reason": "no_progress"}
    return {"valid": True, "reason": "equal" if current == previous else "advanced"}
