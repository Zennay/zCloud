#!/usr/bin/env python3
"""Source-bound operator receipts for the three manual zSentinel release jobs.

This is an observation formatter, NOT release authorization and NOT proof
of an externally observed live runtime. It never calls GitHub, touches
worker state or reads secrets. Only constant allowlisted outcome text is used.
"""

from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timezone
from pathlib import Path

OUTCOMES = {
    ("addon", "disk-installed"): (
        "INSTALLED_ON_DISK",
        "Approved add-on bytes atomically installed; running browser has NOT been verified or reloaded.",
        "Evaluate the separate browser reload outcome before claiming activation.",
    ),
    ("addon", "reload-deferred-no-debugger"): (
        "RELOAD_DEFERRED",
        "Add-on installed on disk, but there is no verified running debugger session.",
        "Wait for a normal approved activation window. Never restart or replace a chat session here.",
    ),
    ("addon", "reload-deferred-busy"): (
        "RELOAD_DEFERRED",
        "Add-on installed on disk; worker idle evidence is busy, unavailable or unverified.",
        "Resolve source availability or wait for idle workers before owner-approved activation.",
    ),
    ("addon", "reload-deferred-error"): (
        "RELOAD_DEFERRED",
        "Add-on installed on disk; the in-place reload command did not succeed.",
        "Inspect the failed reload without treating the install as an active browser upgrade.",
    ),
    ("addon", "reload-command-succeeded"): (
        "RELOAD_COMMAND_SUCCEEDED",
        "Add-on reload command succeeded; independent live browser acceptance is still NOT verified.",
        "Verify the real browser/worker behaviour separately after authorized release.",
    ),
    ("watchdog", "disk-installed"): (
        "INSTALLED_ON_DISK",
        "Standalone watchdog bytes installed and compared; browser and worker processes unchanged.",
        "Obtain independent runtime acceptance before marking recovery complete.",
    ),
    ("emergency-addon", "reload-command-succeeded"): (
        "RELOAD_COMMAND_SUCCEEDED",
        "Existing add-on reloaded; browser PID was preserved, but independent live acceptance is NOT verified.",
        "Confirm end-to-end worker/browser behaviour separately.",
    ),
}

SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[1-9][0-9]{0,19}$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=("addon", "watchdog", "emergency-addon"), required=True)
    parser.add_argument("--outcome", required=True)
    args = parser.parse_args()
    entry = OUTCOMES.get((args.component, args.outcome))
    if entry is None:
        parser.error("unknown component/outcome combination")

    # Readonly provenance; no raw environment, command lines or file paths
    # are emitted in the operator-visible receipt.
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("GITHUB_SHA", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if (repo != "Zennay/zCloud" or not SHA.fullmatch(sha)
            or not RUN_ID.fullmatch(run_id) or run_attempt != "1"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        parser.error("trusted workflow receipt provenance unavailable")

    status, description, followup = entry
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "### zSentinel · manual release observation",
        "",
        f"- **Component:** `{args.component}`",
        f"- **Outcome:** `{status}`",
        f"- **Exact workflow source:** `{sha}`",
        f"- **GitHub run:** https://github.com/Zennay/zCloud/actions/runs/{run_id}",
        f"- **Observed at:** {timestamp}",
        f"- **Result:** {description}",
        f"- **Next action:** {followup}",
        "- **Delivery:** GitHub job observation only; **not** a protected-main integration or independent live-verification receipt.",
        "",
    ]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        parser.error("GitHub Actions job summary destination unavailable")
    with Path(summary).open("a", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")
    print(f"ZSENTINEL_RELEASE_OBSERVATION={args.component}:{status}")
    if "DEFERRED" in status:
        print("::warning::Add-on installed on disk, but browser activation was deferred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
