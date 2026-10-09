"""Offline red regression: late runner command receipts must be fenced.

Run: python3 -m unittest tests/test_runner_command_terminal_receipt_boundary_w4.py
Expected current-main result: two tracked expected failures (known defects).
Unexpected success is a hard unittest failure and requires contract review.
No runtime, SQLite, browser or runner state is touched.
"""
import pathlib
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "server.py"


def endpoint():
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("if u.path=='/api/runner-command-result':")
    end = text.index("if u.path=='/api/project-state-receipts':", start)
    return text[start:end]


class CommandReceiptContract(unittest.TestCase):
    def test_local_only(self):
        snippet = endpoint()
        self.assertIn("127.0.0.1", snippet)
        self.assertIn("::1", snippet)
        self.assertIn("403", snippet)

    def test_status_allowlist(self):
        self.assertIn("status not in ('completed','failed')", endpoint())

    def test_live_endpoint_truncates_result_to_300_characters(self):
        self.assertIn("str(payload.get('result') or '')[:300]", endpoint())

    def test_invalid_status_precedes_database_mutation(self):
        snippet = endpoint()
        self.assertLess(snippet.index("status not in ('completed','failed')"),
                        snippet.index('UPDATE runner_commands SET status='))

    def test_live_endpoint_uses_bound_parameters(self):
        snippet = endpoint()
        self.assertIn("WHERE id=?", snippet)
        self.assertIn("(status,now(),", snippet)

    def test_command_id_parsed_before_mutation(self):
        snippet = endpoint()
        self.assertLess(snippet.index("command_id=int("),
                        snippet.index("UPDATE runner_commands SET status="))

    def test_existing_local_auth_guard_precedes_payload_id_parsing(self):
        snippet = endpoint()
        self.assertLess(snippet.index("127.0.0.1"),
                        snippet.index("command_id=int("))

    @unittest.expectedFailure  # Existing server.py gap; unexpected success requires review.
    def test_pending_compare_and_swap_required(self):
        snippet = endpoint()
        sql = next(line for line in snippet.splitlines()
                   if "UPDATE runner_commands SET status=" in line)
        self.assertIn("status='pending'", sql,
                      "late receipt can overwrite completed/failed result")

    @unittest.expectedFailure  # Existing server.py gap; unexpected success requires review.
    def test_positive_id_must_be_validated(self):
        snippet = endpoint()
        self.assertTrue("command_id<=0" in snippet or "command_id < 1" in snippet
                        or "command_id <=" in snippet,
                        "negative or zero command IDs must be rejected")


if __name__ == "__main__":
    unittest.main()
