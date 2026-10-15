#!/usr/bin/env python3
"""Fail-closed read-only cache-bust gate for changes to zCloud dashboard assets.

Only compares immutable local Git objects. Source evidence is not live VPS evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit

ASSETS = ("public/app.js", "public/enhancements.css")
INDEX = "public/index.html"
SHA = re.compile(r"[0-9a-f]{40}")
PIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
MAX_FILE = 2 * 1024 * 1024


def git(*args: str) -> bytes:
    # All invocations are read-only and bounded, with untrusted refs restricted to SHA.
    try:
        result = subprocess.run(
            ["git", "--no-pager", *args], check=True, capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("immutable_git_read_failed") from exc
    if len(result.stdout) > MAX_FILE + 1024:
        raise ValueError("git_output_too_large")
    return result.stdout


def blob(commit: str, path: str) -> bytes:
    if not SHA.fullmatch(commit) or path not in (*ASSETS, INDEX):
        raise ValueError("invalid_commit_or_asset")
    lines = git("ls-tree", "-z", commit, "--", path).split(b"\x00")
    if len(lines) != 2 or not lines[0]:
        raise ValueError("missing_or_ambiguous_git_path")
    try:
        metadata, name = lines[0].split(b"\t", 1)
        mode, kind, oid = metadata.split()
    except ValueError as exc:
        raise ValueError("invalid_git_tree") from exc
    if mode != b"100644" or kind != b"blob" or name.decode("utf-8") != path:
        raise ValueError("nonregular_git_asset")
    try:
        size = int(git("cat-file", "-s", oid.decode("ascii")).strip())
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid_blob_size") from exc
    if size > MAX_FILE:
        raise ValueError("oversize_asset")
    data = git("cat-file", "blob", oid.decode("ascii"))
    if len(data) != size or b"\x00" in data:
        raise ValueError("invalid_asset_content")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("non_utf8_asset") from exc
    return data


class Refs(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: dict[str, list[str]] = {name: [] for name in ASSETS}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_names = [key.lower() for key, _ in attrs]
        if len(attr_names) != len(set(attr_names)):
            raise ValueError("duplicate_html_attribute")
        values = dict(attrs)
        if tag == "script":
            ref = values.get("src")
        elif tag == "link" and "stylesheet" in (values.get("rel") or "").lower().split():
            ref = values.get("href")
        else:
            return
        if not ref:
            return
        parsed = urlsplit(ref)
        path = "public" + parsed.path if parsed.path.startswith("/") else ""
        if path not in ASSETS:
            return
        if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.query:
            raise ValueError("invalid_asset_ref")
        try:
            query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise ValueError("invalid_asset_query") from exc
        if len(query) != 1 or query[0][0] != "v" or not PIN.fullmatch(query[0][1]):
            raise ValueError("invalid_cache_pin")
        self.refs[path].append(query[0][1])


def pins(html: bytes) -> dict[str, str]:
    parser = Refs()
    parser.feed(html.decode("utf-8"))
    parser.close()
    if any(len(parser.refs[name]) != 1 for name in ASSETS):
        raise ValueError("missing_or_duplicate_asset_ref")
    return {name: parser.refs[name][0] for name in ASSETS}


def evaluate(base: str, head: str) -> dict[str, object]:
    report: dict[str, object] = {
        "policy": "zcloud-dashboard-cache-bust-monotonic-v1",
        "ok": False, "reason": "denied", "changed": [],
        "mutation_performed": False, "release_authorized": False,
        "live_bytes_observed": False,
    }
    try:
        if not SHA.fullmatch(base) or not SHA.fullmatch(head):
            raise ValueError("invalid_sha")
        old = {name: blob(base, name) for name in (*ASSETS, INDEX)}
        new = {name: blob(head, name) for name in (*ASSETS, INDEX)}
        before = pins(old[INDEX])
        after = pins(new[INDEX])
        changed = [name for name in ASSETS if old[name] != new[name]]
        report["changed"] = changed
        unbumped = [name for name in changed if before[name] == after[name]]
        report["ok"] = not unbumped
        report["reason"] = "changed_asset_pin_unchanged" if unbumped else "cache_bust_delta_ok"
        report["unbumped"] = unbumped
    except ValueError as exc:
        report["reason"] = str(exc)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args(argv)
    result = evaluate(args.base_sha, args.head_sha)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
