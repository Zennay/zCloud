#!/usr/bin/env python3
"""Offline artifact byte-count metadata screen; NEVER grants deployment authority.

This intentionally does not download, unpack, attest, or authorize an artifact.
Metadata supplied by an untrusted party is not evidence of actual byte size.
"""
import json
import sys

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024

def screen_sizes(archive_bytes, expanded_bytes):
    """Return a denial-only classification for two purported size fields."""
    for value in (archive_bytes, expanded_bytes):
        if type(value) is not int or value <= 0:
            return {"classification": "INVALID_SIZE_METADATA", "authorized": False}
    if archive_bytes > MAX_ARCHIVE_BYTES or expanded_bytes > MAX_EXPANDED_BYTES:
        return {"classification": "SIZE_LIMIT_EXCEEDED", "authorized": False}
    if expanded_bytes < archive_bytes:
        return {"classification": "INCONSISTENT_SIZE_METADATA", "authorized": False}
    return {"classification": "METADATA_ONLY_UNVERIFIED", "authorized": False}

def main():
    if len(sys.argv) != 3:
        print(json.dumps({"classification": "INVALID_ARGUMENTS", "authorized": False}))
        return 2
    # Accept canonical positive decimal integers only (no signs, whitespace,
    # exponents, leading zeros, Unicode digits or JSON booleans).
    args = sys.argv[1:]
    if any(not s.isascii() or not s.isdecimal() or s.startswith("0") for s in args):
        print(json.dumps({"classification": "INVALID_SIZE_METADATA", "authorized": False}))
        return 2
    result = screen_sizes(int(args[0]), int(args[1]))
    print(json.dumps(result, sort_keys=True))
    return 2  # Unconditionally non-authorizing, even when metadata looks valid.

if __name__ == "__main__":
    sys.exit(main())
