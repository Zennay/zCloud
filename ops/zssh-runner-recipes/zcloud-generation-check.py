#!/usr/bin/env python3
"""Approved zSSH read-only worker-generation admission check.

The zSSH bridge executes this exact checked-in recipe from current main only.
Success means fresh browser-reported generating evidence for every allocated
ChatGPT worker, not verification of physical tab presence or future progress.
The bridge suppresses all stdout/stderr; no prompts or session URLs are logged.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
completed = subprocess.run(
    [sys.executable, "scripts/zcloud_worker_generation_evidence_20261013_w1.py"],
    cwd=ROOT,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    timeout=15,
    check=False,
)
raise SystemExit(completed.returncode)
