from __future__ import annotations

import re

_PATTERNS: tuple[tuple[re.Pattern[str], object], ...] = (
    (
        re.compile(r"(?i)\b(authorization:\s*bearer\s+)[A-Za-z0-9._~+\-/]+=*"),
        r"\1[REDACTED]",
    ),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_ACCESS_KEY]"),
    (
        re.compile(
            r'''(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*(["']?)[^\s,"']+\2'''
        ),
        lambda m: f"{m.group(1)}=[REDACTED]",
    ),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
