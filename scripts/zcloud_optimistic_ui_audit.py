#!/usr/bin/env python3
"""Fail-closed audit for zCloud dashboard write actions without in-flight feedback."""

from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path
from typing import Any

SCHEMA = "zcloud-optimistic-ui-audit-v1"
MAX_BYTES = 2 * 1024 * 1024
KNOWN_BASELINE_DEBT = frozenset({"saveProjectLayout"})
WRITE_HELPERS = frozenset({"post"})

_FUNCTION_RE = re.compile(
    r"(?m)^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("
)
_AWAIT_POST_RE = re.compile(r"await\s+post\s*\(")
_AWAIT_FETCH_POST_RE = re.compile(
    r"await\s+fetch\s*\([^;]{0,1800}?method\s*:\s*['\"]POST['\"]",
    re.DOTALL,
)
_DISABLED_RE = re.compile(r"\.disabled\s*=\s*true\b")
_PENDING_COPY_RE = re.compile(
    r"(?:textContent\s*=|setLabel\s*\(|innerText\s*=)[^;\n]{0,180}"
    r"(?:…|ing\b|Saving|Starting|Pausing|Pushing|Resuming|Finish|Herstart)",
    re.IGNORECASE,
)


class AuditError(ValueError):
    pass


def _read_source(path: Path) -> str:
    try:
        info = path.lstat()
    except OSError as exc:
        raise AuditError(f"source_unreadable:{path.name}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise AuditError(f"source_not_regular:{path.name}")
    if info.st_size > MAX_BYTES:
        raise AuditError(f"source_too_large:{path.name}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AuditError(f"source_invalid_utf8:{path.name}") from exc


def _function_end(source: str, match: re.Match[str]) -> int:
    """Return the exact closing brace for a named function."""
    brace_at = source.find("{", match.end())
    if brace_at < 0:
        raise AuditError(f"function_body_missing:{match.group(1)}")

    depth = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    index = brace_at
    while index < len(source):
        char = source[index]
        nxt = source[index + 1] if index + 1 < len(source) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue

        if char == "/" and nxt == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and nxt == "*":
            block_comment = True
            index += 2
            continue
        if char in ("'", '"', "`"):
            quote = char
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1

    raise AuditError(f"function_unclosed:{match.group(1)}")


def _function_spans(source: str) -> list[tuple[str, int, int, str]]:
    spans: list[tuple[str, int, int, str]] = []
    for match in _FUNCTION_RE.finditer(source):
        end = _function_end(source, match)
        spans.append((match.group(1), match.start(), end, source[match.start():end]))
    return spans


def _function_blocks(source: str) -> list[tuple[str, str]]:
    return [(name, block) for name, _start, _end, block in _function_spans(source)]


def _write_offsets(text: str) -> list[int]:
    offsets = [match.start() for match in _AWAIT_POST_RE.finditer(text)]
    offsets.extend(match.start() for match in _AWAIT_FETCH_POST_RE.finditer(text))
    return sorted(set(offsets))


def _first_write_offset(block: str) -> int | None:
    offsets = _write_offsets(block)
    return offsets[0] if offsets else None


def _guard_state(block: str) -> tuple[bool, bool]:
    write_at = _first_write_offset(block)
    if write_at is None:
        return False, False
    prefix = block[:write_at]
    return bool(_DISABLED_RE.search(prefix)), bool(_PENDING_COPY_RE.search(prefix))


def audit(source: str) -> dict[str, Any]:
    spans = _function_spans(source)
    mutators: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, _start, _end, block in spans:
        if name in WRITE_HELPERS:
            continue
        if _first_write_offset(block) is None:
            continue
        if name in seen:
            raise AuditError(f"duplicate_function:{name}")
        seen.add(name)
        guarded, visible_pending = _guard_state(block)
        mutators.append(
            {
                "name": name,
                "inflight_guard": guarded,
                "visible_pending": visible_pending,
            }
        )

    if not mutators:
        raise AuditError("no_mutating_dashboard_functions")

    inline_writes: list[bool] = []
    for offset in _write_offsets(source):
        if any(start <= offset < end for _name, start, end, _block in spans):
            continue
        prefix = source[max(0, offset - 600):offset]
        inline_writes.append(bool(_DISABLED_RE.search(prefix)))

    unguarded = sorted(item["name"] for item in mutators if not item["inflight_guard"])
    unexpected = sorted(set(unguarded) - KNOWN_BASELINE_DEBT)
    if any(not guarded for guarded in inline_writes):
        unexpected.append("inline_write_without_guard")
    baseline = sorted(set(unguarded) & KNOWN_BASELINE_DEBT)
    status = "regressed" if unexpected else ("baseline_bounded" if baseline else "complete")
    return {
        "schema": SCHEMA,
        "status": status,
        "mutator_count": len(mutators),
        "guarded_count": sum(1 for item in mutators if item["inflight_guard"]),
        "visible_pending_count": sum(1 for item in mutators if item["visible_pending"]),
        "inline_write_count": len(inline_writes),
        "inline_guarded_count": sum(1 for guarded in inline_writes if guarded),
        "remaining_baseline_debt": baseline,
        "unexpected_debt": sorted(set(unexpected)),
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, default=Path("public/app.js"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-ratchet", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = audit(_read_source(args.app))
    except AuditError as exc:
        result = {
            "schema": SCHEMA,
            "status": "incomplete",
            "reason": str(exc),
            "mutation_performed": False,
        }
        print(json.dumps(result, sort_keys=True))
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"status={result['status']}")
        print(f"mutator_count={result['mutator_count']}")
        print(f"guarded_count={result['guarded_count']}")
        print(f"inline_write_count={result['inline_write_count']}")
        print(f"inline_guarded_count={result['inline_guarded_count']}")
        print("remaining_baseline_debt=" + ",".join(result["remaining_baseline_debt"]))
        print("unexpected_debt=" + ",".join(result["unexpected_debt"]))

    if args.require_ratchet and result["status"] == "regressed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
