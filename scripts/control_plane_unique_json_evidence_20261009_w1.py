"""Offline reference for rejecting ambiguous JSON control-plane evidence.

This helper does not authorize runtime actions. Runtime integrations need separate
authenticated, fresh, atomically fenced evidence and owner review.
"""
import json
from typing import Any


class AmbiguousEvidence(ValueError):
    """Evidence JSON is invalid or contains a duplicate object key."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AmbiguousEvidence("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise AmbiguousEvidence("non-finite JSON constant")


def parse_unique_evidence(raw: str, *, max_bytes: int = 65536) -> dict[str, Any]:
    """Parse a strictly bounded object without last-key-wins interpretation."""
    if not isinstance(raw, str):
        raise AmbiguousEvidence("JSON text required")
    if type(max_bytes) is not int or not 1 <= max_bytes <= 1048576:
        raise AmbiguousEvidence("invalid evidence size limit")
    try:
        byte_length = len(raw.encode("utf-8"))
    except UnicodeError as exc:
        raise AmbiguousEvidence("invalid Unicode evidence") from exc
    if byte_length > max_bytes:
        raise AmbiguousEvidence("oversized JSON evidence")
    try:
        data = json.loads(raw, object_pairs_hook=_unique_pairs,
                          parse_constant=_reject_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise AmbiguousEvidence("invalid or ambiguous JSON evidence") from exc
    if not isinstance(data, dict):
        raise AmbiguousEvidence("top-level object required")
    return data
