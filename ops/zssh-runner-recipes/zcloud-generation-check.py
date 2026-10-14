#!/usr/bin/env python3
"""Approved zSSH read-only worker-generation admission check.

The zSSH bridge executes this exact checked-in recipe from current main only.
Success means fresh browser-reported new assistant-text progress for every
allocated ChatGPT worker, not a mere heartbeat. Physical tab presence and
future progress are not proven. A later heartbeat may overwrite progress.
The bridge suppresses all stdout/stderr; no prompts or session URLs are logged.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
completed = subprocess.run(
    [sys.executable, "scripts/zcloud_worker_generation_evidence_20261013_w1.py", "--require-progress"],
    cwd=ROOT,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    timeout=15,
    check=False,
)
raise SystemExit(completed.returncode)
