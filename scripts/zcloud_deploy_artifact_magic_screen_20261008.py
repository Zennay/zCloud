#!/usr/bin/env python3
"""Offline artifact header triage. NEVER authorizes extraction or deployment.

This deliberately does not read paths, open archives, fetch URLs, or trust
metadata. The only input is a caller-supplied hexadecimal prefix.
"""
import argparse
import json
import re

MAX_PREFIX_BYTES = 512
SIGNATURES = (
    ("gzip", bytes.fromhex("1f8b08")),
    ("zip_local", b"PK\x03\x04"),
    ("zip_empty", b"PK\x05\x06"),
    ("zip_spanned", b"PK\x07\x08"),
    ("zstd", bytes.fromhex("28b52ffd")),
    ("xz", bytes.fromhex("fd377a585a00")),
    ("bzip2", b"BZh"),
)

def screen(prefix_hex):
    result = {
        "classification": "INVALID",
        "reason": "invalid_hex_prefix",
        "authorized": False,
        "may_extract": False,
        "may_deploy": False,
        "may_recover": False,
    }
    if not isinstance(prefix_hex, str) or not prefix_hex or len(prefix_hex) % 2:
        return result
    if len(prefix_hex) > MAX_PREFIX_BYTES * 2 or not re.fullmatch(r"[0-9a-fA-F]+", prefix_hex):
        return result
    raw = bytes.fromhex(prefix_hex)
    for name, magic in SIGNATURES:
        if raw.startswith(magic):
            result.update(classification="SIGNATURE_ONLY_UNVERIFIED", reason=name)
            return result
    # POSIX tar ustar signature is at byte offset 257; do not confuse it
    # with proof of a valid tar header or archive integrity.
    if len(raw) >= 262 and raw[257:262] == b"ustar":
        result.update(classification="SIGNATURE_ONLY_UNVERIFIED", reason="tar_ustar")
        return result
    result.update(classification="UNKNOWN_UNVERIFIED", reason="no_recognized_header")
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix_hex", help="hexadecimal leading bytes; never a file path or URL")
    args = parser.parse_args()
    print(json.dumps(screen(args.prefix_hex), sort_keys=True))
    return 2  # Valid magic is NEVER a release admission decision.

if __name__ == "__main__":
    raise SystemExit(main())
