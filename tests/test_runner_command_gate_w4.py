"""Self-tests for the focused receipt evidence gate (no live runtime writes)."""
import unittest
import sys
from pathlib import Path

# Works both in focused discovery and repository-root unittest discovery.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_receipt_contract_w4 import approved_xfail_ids, has_assertion_heading


class ReceiptGateSelfTest(unittest.TestCase):
    def test_reviewed_allowlist_has_exact_thirteen_distinct_ids(self):
        ids = approved_xfail_ids()
        self.assertEqual(13, len(ids))
        self.assertEqual(11, sum("receipt_http_w4." in item for item in ids))
        self.assertEqual(2, sum("terminal_receipt_boundary_w4." in item for item in ids))

    def test_same_method_in_wrong_class_is_not_approved(self):
        ids = approved_xfail_ids()
        self.assertNotIn(
            "another_module.AnotherClass.test_unknown_id_must_not_report_success", ids
        )

    def test_recognizes_assertion_in_chained_exception(self):
        trace = ("Traceback (most recent call last):\n"
                 "AssertionError: expected terminal state\n"
                 "\nDuring handling of the above exception, another exception occurred:\n"
                 "RuntimeError: shutdown\n")
        self.assertTrue(has_assertion_heading(trace))

    def test_does_not_accept_assertion_text_inside_code_or_message(self):
        trace = ('  File "fixture.py", line 3\n'
                 '    raise RuntimeError("AssertionError: injected")\n'
                 'RuntimeError: AssertionError: injected\n')
        self.assertFalse(has_assertion_heading(trace))

    def test_rejects_non_assertion_failure_and_empty_trace(self):
        self.assertFalse(has_assertion_heading("NameError: missing_name\n"))
        self.assertFalse(has_assertion_heading(""))


if __name__ == "__main__":
    unittest.main()
