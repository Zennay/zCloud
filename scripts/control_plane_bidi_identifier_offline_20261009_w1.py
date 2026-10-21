"""Offline-only conservative control-plane identifier check.

This function cannot authenticate claims or authorize any live action.
It deliberately rejects all non-ASCII and Unicode formatting/control characters.
"""
import re

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)

def is_safe_display_identifier(value):
    """Validate a bounded identifier for unambiguous display and comparison."""
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None
