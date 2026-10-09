"""Offline-only reference for comparing control-plane observation watermarks.

This module has no runtime, database or worker integration and confers no authority.
"""
from dataclasses import dataclass
from datetime import datetime
import re

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MAX_COUNTER = 2**63 - 1


@dataclass(frozen=True)
class Watermark:
    generation: int
    sequence: int
    head_sha: str
    observed_at: str


def _valid(w):
    if not isinstance(w, Watermark):
        return False
    if type(w.generation) is not int or not 0 <= w.generation <= _MAX_COUNTER:
        return False
    if type(w.sequence) is not int or not 0 <= w.sequence <= _MAX_COUNTER:
        return False
    if not isinstance(w.head_sha, str) or not _SHA.fullmatch(w.head_sha):
        return False
    if not isinstance(w.observed_at, str) or not w.observed_at.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(w.observed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0


def compare(previous, candidate):
    """Return a non-authorizing offline classification, denying ambiguous progression."""
    if not _valid(previous) or not _valid(candidate):
        return "invalid"
    if candidate.generation < previous.generation:
        return "stale_generation"
    if candidate.generation == previous.generation:
        if candidate.head_sha != previous.head_sha:
            return "conflicting_head"
        if candidate.sequence < previous.sequence:
            return "stale_sequence"
        if candidate.sequence == previous.sequence:
            return "duplicate" if candidate == previous else "conflicting_receipt"
        if candidate.observed_at < previous.observed_at:
            return "regressed_observation_time"
        return "forward"
    if candidate.sequence != 0:
        return "unanchored_generation"
    return "new_generation_unverified"
