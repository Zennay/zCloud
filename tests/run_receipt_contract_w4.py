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
# Reviewed baseline: a new expectedFailure must not replace an old one while
# leaving the count unchanged. Removing names after remediation is permitted.
REVIEWED_XFAIL_NAMES = frozenset([
    "test_unsafe_javascript_integer_id_must_be_rejected",
    "test_unknown_id_must_not_report_success",
    "test_scheduler_expired_terminal_state_cannot_be_revived",
    "test_competing_http_callbacks_have_one_terminal_winner",
    "test_replayed_callback_must_not_overwrite_terminal_result",
    "test_boolean_id_must_be_rejected_without_mutation",
    "test_negative_id_must_return_controlled_400",
    "test_numeric_string_id_must_not_coerce_to_command",
    "test_null_id_must_return_controlled_400",
    "test_malformed_id_must_return_controlled_400",
    "test_zero_id_must_be_rejected_without_mutation",
    "test_pending_compare_and_swap_required",
    "test_positive_id_must_be_validated"
]
)



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
    unexpected_xfails = sorted(
        name for name in failures
        if name.rsplit(".", 1)[-1] not in REVIEWED_XFAIL_NAMES
    )
    if unexpected_xfails:
        print("ERROR: unreviewed expected-failure tests: " +
              ", ".join(unexpected_xfails), file=sys.stderr)
        return 1
    if len(failures) > MAX_EXPECTED_FAILURES:
        print(f"ERROR: {len(failures)} expected failures exceed budget {MAX_EXPECTED_FAILURES}", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
