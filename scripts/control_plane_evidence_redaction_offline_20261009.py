"""Offline-only redaction of diagnostic handoff values.

This is a reference classifier. It is NOT wired to logging, cannot establish
provenance, and never authorizes production actions.
"""
from __future__ import annotations

import re
import math
from typing import Any

_SECRET_KEY = re.compile(
    r"(?:token|secret|password|passwd|authorization|api[_-]?key|cookie|private[_-]?key|client[_-]?secret)",
    re.IGNORECASE,
)
_INLINE = re.compile(
    r"(?i)(?:bearer\s+)[a-z0-9._~+/-]+|(?:gh[pousr]_[a-z0-9_]{10,})|"
    r"(?:github_pat_[a-z0-9_]{10,})|(?:sk-[a-z0-9_-]{10,})"
)
_ASSIGNMENT = re.compile(
    r"(?i)\b((?:password|passwd|api[_-]?key|access[_-]?token|"
    r"client[_-]?secret|authorization|cookie)\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s&;,]+)"
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|api_key|apikey|"
    r"client_secret|password|auth_token|id_token)=)[^&#\s]*"
)
_MAX_DEPTH = 12
_MAX_ITEMS = 200
_MAX_CHARS = 4096
_REDACTED = "[REDACTED]"
_TRUNCATED = "[TRUNCATED]"


def sanitize(value: Any, *, _depth: int = 0) -> Any:
    """Return bounded, redacted plain data; unknown types fail closed."""
    if _depth > _MAX_DEPTH:
        return _TRUNCATED
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else _REDACTED
    if isinstance(value, str):
        # Remove terminal controls before redaction so handoff text cannot
        # manipulate log rendering or visually hide a credential.
        value = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", value)
        value = "".join(c if c in "\n\r\t" or (32 <= ord(c) < 127) or ord(c) >= 160 else "?" for c in value)
        return _QUERY_SECRET.sub(lambda m: m.group(1) + _REDACTED, _ASSIGNMENT.sub(lambda m: m.group(1) + _REDACTED, _INLINE.sub(_REDACTED, value[:_MAX_CHARS]))) + (
            _TRUNCATED if len(value) > _MAX_CHARS else ""
        )
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:_MAX_ITEMS]:
            if not isinstance(key, str):
                out["[NON_STRING_KEY]"] = _REDACTED
                continue
            safe_key = sanitize(key, _depth=_depth + 1)
            if _SECRET_KEY.search(key):
                out[safe_key] = _REDACTED
            else:
                out[safe_key] = sanitize(item, _depth=_depth + 1)
        if len(value) > _MAX_ITEMS:
            out["[EXCESS_ITEMS]"] = _TRUNCATED
        return out
    if isinstance(value, (list, tuple)):
        out = [sanitize(item, _depth=_depth + 1) for item in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            out.append(_TRUNCATED)
        return out
    return _REDACTED


def classify_handoff(raw: Any) -> dict[str, Any]:
    """Sanitize evidence without treating presence as action authority."""
    return {
        "evidence": sanitize(raw),
        "authenticated_origin": False,
        "authorizes_restart": False,
        "authorizes_queue_write": False,
        "authorizes_deploy": False,
        "mutation_performed": False,
    }
