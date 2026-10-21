"""Offline strict JSON evidence loader: duplicate keys and non-finite numbers deny.

No workflow, runner, deploy, release or recovery authority. This primitive is
intentionally standalone so owners can opt into it after review.
"""
import json
import math
from pathlib import Path


class EvidenceFormatError(ValueError):
    """An evidence document has ambiguous or unsupported JSON."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceFormatError("duplicate JSON member")
        result[key] = value
    return result


def _deny_constant(_value):
    raise EvidenceFormatError("non-finite JSON numeric literal")


def load_strict_evidence(raw: str):
    """Parse one JSON object, denying duplicate keys even in nested objects."""
    try:
        data = json.loads(raw, object_pairs_hook=_unique_pairs,
                          parse_constant=_deny_constant)
    except (json.JSONDecodeError, TypeError, RecursionError) as exc:
        raise EvidenceFormatError("invalid JSON evidence") from exc
    if not isinstance(data, dict):
        raise EvidenceFormatError("root must be a JSON object")
    return data


def read_strict_evidence(path: str, max_bytes: int = 65536):
    """Read a bounded regular file, without following symlinks."""
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise EvidenceFormatError("not a regular evidence file")
    if p.stat().st_size > max_bytes:
        raise EvidenceFormatError("evidence too large")
    try:
        raw = p.read_bytes()
        if len(raw) > max_bytes:
            raise EvidenceFormatError("evidence too large")
        return load_strict_evidence(raw.decode("utf-8"))
    except (OSError, UnicodeError) as exc:
        raise EvidenceFormatError("unreadable evidence") from exc
