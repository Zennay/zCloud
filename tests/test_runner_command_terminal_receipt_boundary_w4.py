"""Offline red regression: late runner command receipts must be fenced.

Run: python3 -m unittest tests/test_runner_command_terminal_receipt_boundary_w4.py
Expected current-main result: two tracked expected failures (known defects).
Unexpected success is a hard unittest failure and requires contract review.
No runtime, SQLite, browser or runner state is touched.
"""
import ast
import pathlib
import re
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "server.py"


def endpoint():
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("if u.path=='/api/runner-command-result':")
    end = text.index("if u.path=='/api/project-state-receipts':", start)
    return text[start:end]


class CommandReceiptContract(unittest.TestCase):
    def test_positive_id_guard_matcher_examples(self):
        pattern = re.compile(r"if\s+command_id\s*(?:<=\s*0|<\s*1)\s*:")
        for valid in ("if command_id <= 0:", "if command_id<1:"):
            self.assertIsNotNone(pattern.search(valid))
        for invalid in ("if command_id > 0:", "if command_id == 0:",
                        "command_id <= 0", "if different_id <= 0:"):
            self.assertIsNone(pattern.search(invalid))

    def test_handler_is_post_only(self):
        source = SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        route = "/api/runner-command-result"
        owners = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in ("do_POST", "do_GET", "do_PUT", "do_DELETE", "do_PATCH"):
                continue
            literals = [
                part.value for part in ast.walk(node)
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            ]
            if route in literals:
                owners.append(node.name)
        self.assertEqual(["do_POST"], owners,
                         "command result route must exist only in POST handler")
        route_checks = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            and any(isinstance(part, ast.Constant) and part.value == route
                    for part in ast.walk(node))
        ]
        self.assertEqual(1, len(route_checks),
                         "exact route comparison must not be duplicated")
        comparison = route_checks[0]
        self.assertIsInstance(comparison.left, ast.Attribute)
        self.assertEqual("path", comparison.left.attr)
        self.assertEqual(1, len(comparison.ops))
        self.assertIsInstance(comparison.ops[0], ast.Eq)

    def test_scheduler_stale_sweep_only_updates_pending_commands(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("def _reconcile_stale_runner_commands_locked(")
        end = source.index("def reconcile_stale_runner_commands(", start)
        sweeper = source[start:end]
        self.assertIn("AND status='pending'", sweeper,
                      "stale sweeper must not replace terminal results")
        self.assertIn("BEGIN IMMEDIATE", source[end:source.index("def autonomy_scheduler_tick(", end)],
                      "stale reconciliation must acquire a write transaction")

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
        # Match a positive-ID rejection predicate, not just any mention of
        # command_id or an unrelated comparison later in the handler.
        self.assertRegex(
            snippet,
            r"if\s+command_id\s*(?:<=\s*0|<\s*1)\s*:",
            "nonpositive command IDs must be rejected before the SQL write",
        )


if __name__ == "__main__":
    unittest.main()
