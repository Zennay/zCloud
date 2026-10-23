#!/usr/bin/env python3
"""Reviewed zCloud guard regression proof for ChatGPT-initiated VPS jobs.

Checked in on main before use. No shell and no user-provided argv.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
result = subprocess.run(
    [sys.executable, "-m", "unittest", "-q", "tests.test_vps_runner_guard"],
    cwd=ROOT,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    timeout=15,
    check=False,
)
raise SystemExit(result.returncode)
