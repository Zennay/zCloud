#!/usr/bin/env python3
"""Conservatively tune only the dedicated zCloud Firefox profile at browser startup.

No tab unload, session-store change, Fission/isolated process change, or browser
restart is performed. A managed user.js block is reversible without disturbing
unrelated profile preferences. Firefox reads user.js on its *next* launch.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile

BEGIN = "// BEGIN zCloud Firefox memory caps (managed)"
END = "// END zCloud Firefox memory caps (managed)"
# Mozilla's default decoded image surface cap can reach 2 GiB. A modest
# 128 MiB cap is appropriate for a text-centric worker profile, not for the
# user's regular desktop browser. The HTTP memory cache defaults to automatic.
PREFS = (
    ("image.mem.surfacecache.max_size_kb", "131072"),
    ("browser.cache.memory.capacity", "16384"),
    ("network.prefetch-next", "false"),
    ("network.predictor.enabled", "false"),
)
MAX_USER_JS_BYTES = 1024 * 1024


def _unmanaged_content(content: str) -> str:
    begin_count, end_count = content.count(BEGIN), content.count(END)
    if begin_count == end_count == 0:
        return content
    if begin_count != 1 or end_count != 1:
        raise ValueError("invalid or duplicate managed preference markers")
    start = content.index(BEGIN)
    finish = content.index(END) + len(END)
    if finish <= start:
        raise ValueError("managed preference markers out of order")
    if content[finish:finish + 1] == "\n":
        finish += 1
    return content[:start] + content[finish:]


def generate(existing: str, *, remove: bool = False) -> str:
    """Preserve all unmanaged settings; never override existing custom prefs."""
    clean = _unmanaged_content(existing)
    if remove:
        return clean
    lines = []
    for key, value in PREFS:
        if re.search(r"(?m)^\s*user_pref\s*\(\s*['\"]" + re.escape(key) + r"['\"]\s*,", clean):
            continue  # respect an explicitly configured profile preference
        lines.append(f'user_pref("{key}", {value});')
    if not lines:
        return clean
    prefix = clean if not clean or clean.endswith("\n") else clean + "\n"
    return prefix + BEGIN + "\n" + "\n".join(lines) + "\n" + END + "\n"


def profile_in_use(profile: Path) -> bool:
    """Fail closed for a live Firefox profile, even if run manually."""
    needle = str(profile.resolve()).encode("utf-8")
    for item in Path("/proc").iterdir():
        if not item.name.isdigit() or int(item.name) == os.getpid():
            continue
        try:
            cmdline = (item / "cmdline").read_bytes().replace(b"\x00", b" ")
        except (OSError, PermissionError):
            continue
        if b"firefox" in cmdline.lower() and needle in cmdline:
            return True
    return False


def apply(profile: Path, *, remove: bool = False, check: bool = False) -> str:
    if not profile.is_dir() or profile.is_symlink():
        raise ValueError("profile must be an existing, non-symlink directory")
    path = profile / "user.js"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("unsafe user.js destination")
    old_bytes = path.read_bytes() if path.exists() else b""
    if len(old_bytes) > MAX_USER_JS_BYTES:
        raise ValueError("user.js exceeds safe size limit")
    try:
        old = old_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("user.js must be valid UTF-8") from exc
    new = generate(old, remove=remove)
    if new == old:
        return "unchanged"
    if check:
        return "would-change"
    if profile_in_use(profile):
        raise ValueError("Firefox profile is in use; will not edit running browser")
    # Write atomically in the same profile to avoid a partially written user.js.
    fd, temp = tempfile.mkstemp(prefix=".zcloud-memory-", dir=str(profile))
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(new.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return "removed" if remove else "installed-next-start"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--remove", action="store_true", help="Remove ONLY the managed prefs block")
    parser.add_argument("--check", action="store_true", help="Check without writing")
    args = parser.parse_args(argv)
    try:
        print(apply(args.profile, remove=args.remove, check=args.check))
        return 0
    except (OSError, ValueError) as exc:
        print(f"firefox-memory-caps: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
