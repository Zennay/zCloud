"""Offline-only redaction guard for deploy-ops evidence shared in reviews.

This module does NOT grant release, deploy or recovery authorization. It does
not read live logs or contact GitHub, the runner, or the VPS.
"""
from __future__ import annotations

import re

# Conservative: redact complete lines containing credential-bearing assignments
# or common authorization headers. Never echo the value in diagnostics.
_CREDENTIAL_LINE = re.compile(
    r"(?i)(?:authorization\s*:|(?:^|[\s,;{])(?:"
    r"gh[_-]?token|github[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"api[_-]?key|client[_-]?secret|password|private[_-]?key"
    r")\s*[:=])"
)
_GH_PAT = re.compile(r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,})\b")
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def redact_evidence(raw: str, *, max_chars: int = 12000) -> str:
    """Produce bounded review text; fail closed on invalid input."""
    if not isinstance(raw, str) or max_chars < 1 or max_chars > 12000:
        raise ValueError("invalid evidence input or limit")
    output = []
    for line in raw.splitlines():
        if _CREDENTIAL_LINE.search(line):
            output.append("[REDACTED CREDENTIAL LINE]")
        else:
            clean = _GH_PAT.sub("[REDACTED TOKEN]", line)
            clean = _BEARER.sub("Bearer [REDACTED TOKEN]", clean)
            output.append(_CONTROL.sub("?", clean))
    result = "\n".join(output)
    if len(result) > max_chars:
        return result[:max_chars] + "\n[TRUNCATED]"
    return result
