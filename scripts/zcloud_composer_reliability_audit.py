#!/usr/bin/env python3
"""Read-only audit for bounded browser composer detection and recovery."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


class ComposerAuditError(RuntimeError):
    pass


def read_source(path: Path) -> str:
    if path.is_symlink():
        raise ComposerAuditError(f"source path must not be a symlink: {path.name}")
    if not path.exists() or not path.is_file():
        raise ComposerAuditError(f"source path must be a regular file: {path.name}")
    return path.read_text(encoding="utf-8")


def const_ms(source: str, name: str) -> int | None:
    match = re.search(
        rf"\bconst\s+{re.escape(name)}\s*=\s*(\d+)(?:\s*\*\s*(\d+))?\s*;",
        source,
    )
    if not match:
        return None
    value = int(match.group(1))
    if match.group(2):
        value *= int(match.group(2))
    return value


def literal_ms(source: str, pattern: str) -> int | None:
    match = re.search(pattern, source)
    return int(match.group(1)) if match else None


def audit(primary_source: str, fallback_source: str) -> dict:
    primary_send_timeout_ms = literal_ms(
        primary_source, r"waitForSendButton\(timeoutMs\s*=\s*(\d+)\)"
    )
    primary_send_poll_ms = literal_ms(
        primary_source, r"waitForSendButton[\s\S]{0,800}?await\s+sleep\((\d+)\)"
    )
    primary_blocked_report_dedupe_ms = literal_ms(
        primary_source, r"lastSendBlockedReport\.at\s*<\s*(\d+)"
    )

    fallback_check_ms = const_ms(fallback_source, "CHECK_MS")
    fallback_startup_idle_ms = const_ms(fallback_source, "STARTUP_IDLE_MS")
    fallback_recovery_ms = const_ms(fallback_source, "COMPOSER_RECOVERY_MS")
    fallback_retry_floor_ms = literal_ms(
        fallback_source, r"now\s*-\s*lastStartupAttemptAt\s*<\s*(\d+)"
    )
    fallback_last_prompt_floor_ms = literal_ms(
        fallback_source, r"now\s*-\s*lastPromptSentAt\s*>=\s*(\d+)"
    )

    primary_selector_coverage = all(
        token in primary_source
        for token in (
            '#prompt-textarea',
            '[contenteditable="true"][role="textbox"]',
            'document.querySelector("textarea")',
        )
    )
    fallback_selector_coverage = all(
        token in fallback_source
        for token in (
            '#prompt-textarea',
            '[contenteditable="true"][role="textbox"]',
            'document.querySelector("textarea")',
        )
    )
    fallback_one_shot_recovery = all(
        token in fallback_source
        for token in (
            "composerMissingSince",
            "COMPOSER_RECOVERY_MS",
            "!recoveryRequested",
            'type: "runner-new-chat"',
            'reason: "composer-missing"',
        )
    )
    primary_local_composer_recovery = all(
        token in primary_source
        for token in (
            "composerMissingSince",
            "COMPOSER_RECOVERY_MS",
            'type: "runner-new-chat"',
        )
    )

    primary_send_wait_bounded = bool(
        primary_send_timeout_ms is not None
        and 250 <= primary_send_timeout_ms <= 5000
        and primary_send_poll_ms is not None
        and 25 <= primary_send_poll_ms <= 250
    )
    fallback_fast_detection = bool(
        fallback_check_ms is not None
        and 500 <= fallback_check_ms <= 5000
        and fallback_startup_idle_ms is not None
        and fallback_startup_idle_ms <= 10000
    )
    fallback_bounded_recovery = bool(
        fallback_recovery_ms is not None
        and 30000 <= fallback_recovery_ms <= 120000
        and fallback_retry_floor_ms is not None
        and fallback_retry_floor_ms >= 1000
        and fallback_last_prompt_floor_ms is not None
        and fallback_last_prompt_floor_ms >= 60000
        and fallback_one_shot_recovery
    )

    gaps: list[str] = []
    if not primary_selector_coverage:
        gaps.append("primary_selector_coverage_missing")
    if not primary_send_wait_bounded:
        gaps.append("primary_send_wait_unbounded")
    if not primary_local_composer_recovery:
        gaps.append("primary_local_composer_recovery_missing")
    if not fallback_selector_coverage:
        gaps.append("fallback_selector_coverage_missing")
    if not fallback_fast_detection:
        gaps.append("fallback_detection_not_fast")
    if not fallback_bounded_recovery:
        gaps.append("fallback_recovery_not_bounded")

    return {
        "schema_version": 1,
        "primary": {
            "selector_coverage": primary_selector_coverage,
            "send_wait_bounded": primary_send_wait_bounded,
            "send_timeout_ms": primary_send_timeout_ms,
            "send_poll_ms": primary_send_poll_ms,
            "blocked_report_dedupe_ms": primary_blocked_report_dedupe_ms,
            "local_composer_recovery": primary_local_composer_recovery,
        },
        "fallback": {
            "selector_coverage": fallback_selector_coverage,
            "fast_detection": fallback_fast_detection,
            "check_ms": fallback_check_ms,
            "startup_idle_ms": fallback_startup_idle_ms,
            "bounded_recovery": fallback_bounded_recovery,
            "composer_recovery_ms": fallback_recovery_ms,
            "startup_retry_floor_ms": fallback_retry_floor_ms,
            "last_prompt_floor_ms": fallback_last_prompt_floor_ms,
            "one_shot_recovery": fallback_one_shot_recovery,
        },
        "coverage_complete": not gaps,
        "gaps": gaps,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, default=Path("public/zcloud-worker.user.js"))
    parser.add_argument("--fallback", type=Path, default=Path("firefox-extension/background.js"))
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = audit(read_source(args.primary), read_source(args.fallback))
    except (OSError, UnicodeError, ComposerAuditError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "ZCLOUD_COMPOSER_RELIABILITY "
            f"coverage_complete={str(result['coverage_complete']).lower()} "
            f"gaps={','.join(result['gaps']) or 'none'}"
        )
    if args.require_complete and not result["coverage_complete"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
