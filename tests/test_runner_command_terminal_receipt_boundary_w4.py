"""Regression contract for delayed runner-command receipts.

This test intentionally asserts the desired behavior against the real server
source. It is expected to fail while the endpoint performs unconditional
terminal-state updates. No live SQLite database or runner is touched.
"""
import pathlib
import re
import unittest

SERVER = pathlib.Path(__file__).resolve().parents[1] / "server.py"


def callback_source():
    source = SERVER.read_text(encoding="utf-8")
    start = source.index("if u.path=='/api/runner-command-result':")
    end = source.index("if u.path=='/api/project-state-receipts':", start)
    return source[start:end]


class RunnerReceiptTerminalStateContract(unittest.TestCase):
    def test_only_local_clients_can_report_command_results(self):
        body = callback_source()
        self.assertIn("127.0.0.1", body)
        self.assertIn("::1", body)
        self.assertIn("403", body)

    def test_receipt_status_is_terminal_allowlist(self):
        self.assertRegex(callback_source(), r"status not in \\('completed','failed'\\)")

    def test_terminal_receipt_must_not_overwrite_previous_terminal_result(self):
        body = callback_source()
        # WHERE id alone admits late or duplicate callbacks changing completed
        # to failed (and vice versa), including after stale-command fencing.
        updates = re.findall(
            r"UPDATE runner_commands SET status=\\?[^\\n]*", body
        )
        self.assertTrue(updates, "callback update statement missing")
        self.assertTrue(
            all(re.search(r"\\bstatus\\s*=\\s*['\\\"]pending['\\\"]", sql, re.I)
                for sql in updates),
            "command result callback must CAS pending -> terminal",
        )

    def test_bad_command_ids_are_rejected_before_mutating_sqlite(self):
        body = callback_source()
        # Require an explicit positive-ID guard; int() alone accepts negatives
        # and raises on malformed input, routed through a broad exception.
        self.assertRegex(body, r"command_id\\s*<=\\s*0|command_id\\s*<\\s*1")


if __name__ == "__main__":
    unittest.main()
