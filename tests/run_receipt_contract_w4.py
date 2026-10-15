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



def approved_xfail_ids():
    """Reviewed exact module/class/method identities, not bare method names."""
    terminal = {"test_pending_compare_and_swap_required",
                "test_positive_id_must_be_validated"}
    return frozenset(
        ("test_runner_command_terminal_receipt_boundary_w4.CommandReceiptContract."
         if name in terminal
         else "test_runner_command_receipt_http_w4.RunnerCommandReceiptHttpW4.")
        + name
        for name in REVIEWED_XFAIL_NAMES
    )


def has_assertion_heading(trace):
    """Match traceback exception headings, not inline quoted source text."""
    return any(line.startswith("AssertionError:") for line in trace.splitlines())


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
        # Keep exact assertion traces for the serialized implementation owner.
        # Python's runner reports these as expected, so they otherwise vanish
        # from the human-readable terminal failure summary.
        "expected_failure_details": {
            test.id(): traceback
            for test, traceback in result.expectedFailures
        },
        "expected_failure_count": len(failures),
        "unexpected_successes": [test.id() for test in result.unexpectedSuccesses],
        "failures": len(result.failures),
        "failure_details": {test.id(): traceback for test, traceback in result.failures},
        "errors": len(result.errors),
        "error_details": {test.id(): traceback for test, traceback in result.errors},
        "skipped": [{"test": test.id(), "reason": reason}
                    for test, reason in result.skipped],
    }
    # Always publish diagnostic evidence, including when a validation gate
    # below fails; otherwise Actions' always() upload cannot preserve it.
    Path("receipt-results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if set(summary["expected_failure_details"]) != set(failures):
        print("ERROR: expected-failure trace identities do not match", file=sys.stderr)
        return 1
    if any(not trace.strip() for trace in summary["expected_failure_details"].values()):
        print("ERROR: empty expected-failure traceback", file=sys.stderr)
        return 1
    # expectedFailure accepts *any* exception, including a broken test
    # fixture. Chained exceptions can append frames after an assertion;
    # accept only unindented exception headings, not source-code text.
    non_assertion_xfails = sorted(
        name for name, trace in summary["expected_failure_details"].items()
        if not has_assertion_heading(trace)
    )
    if non_assertion_xfails:
        print("ERROR: expected-failure probes failed without an assertion: " +
              ", ".join(non_assertion_xfails), file=sys.stderr)
        return 1
    # unittest considers skipped tests successful. In this focused contract
    # suite, skipping even one test could conceal a production regression.
    if result.skipped:
        print("ERROR: receipt contract tests were skipped: " +
              ", ".join(test.id() for test, _ in result.skipped),
              file=sys.stderr)
        return 1
    if result.testsRun < BASELINE_TESTS:
        print(f"ERROR: only {result.testsRun} tests discovered (minimum {BASELINE_TESTS})", file=sys.stderr)
        return 1
    # The approved names belong to precisely two known test classes.
    # A second class reusing a method name must not inherit xfail approval.
    approved_ids = approved_xfail_ids()
    unexpected_xfails = sorted(set(failures) - approved_ids)
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
