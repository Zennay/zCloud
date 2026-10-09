"""Structured, offline receipt-contract runner for the focused GitHub workflow.

Capture unittest results directly rather than parsing interleaved HTTP logs.
Expected failures remain visible and block new unreviewed xfails.
"""
import json
import sys
import unittest
from pathlib import Path

# Direct script execution places tests/ rather than repository root on sys.path.
# Production-handler HTTP tests import server from the repository root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASELINE_TESTS = 67
MAX_EXPECTED_FAILURES = 13


def main():
    tests = unittest.defaultTestLoader.discover(
        start_dir=str(Path(__file__).resolve().parent),
        pattern="test_runner_command_*_w4.py",
    )
    result = unittest.TextTestRunner(verbosity=2).run(tests)
    failures = sorted(test.id() for test, _ in result.expectedFailures)
    summary = {
        "tests_run": result.testsRun,
        "expected_failures": failures,
        "expected_failure_count": len(failures),
        "unexpected_successes": [test.id() for test in result.unexpectedSuccesses],
        "failures": len(result.failures),
        "errors": len(result.errors),
    }
    Path("receipt-results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if result.testsRun < BASELINE_TESTS:
        print(f"ERROR: only {result.testsRun} tests discovered (minimum {BASELINE_TESTS})", file=sys.stderr)
        return 1
    if len(failures) > MAX_EXPECTED_FAILURES:
        print(f"ERROR: {len(failures)} expected failures exceed budget {MAX_EXPECTED_FAILURES}", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
