"""Fail-closed contract for emergency zCloud bulk worker recovery.

Bulk operations must never undo memory-pressure stabilization or delete a
live browser handoff. Tests intentionally do not contact VPS/API/SQLite.
"""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "push-all-workers-now.yml"


class BulkRecoverySafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.triggers = cls.source.split("\npermissions:", 1)[0]
        cls.script = cls.source.split("          python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        cls.script = "\n".join(
            line[10:] if line.startswith("          ") else line
            for line in cls.script.splitlines()
        )

    def test_only_explicit_manual_dispatch_can_bulk_push(self):
        self.assertIn("  workflow_dispatch:", self.triggers)
        self.assertNotIn("  schedule:", self.triggers)
        self.assertNotIn("  push:", self.triggers)
        self.assertNotIn("  workflow_run:", self.triggers)

    def test_never_forces_seven_or_rewrites_pool_settings(self):
        self.assertNotIn('desired["chatgpt_count"] = 7', self.source)
        self.assertNotIn('"/api/dynamic-workers", desired', self.source)
        self.assertNotIn('call("POST", "/api/dynamic-workers"', self.source)
        self.assertIn('call("GET", "/api/dynamic-workers")', self.source)
        self.assertIn('reconciled = {"ok": True, "dynamic_workers": dynamic}', self.source)

    def test_preserves_in_flight_commands_before_force_push(self):
        self.assertIn('sqlite3.connect(f"file:{DB}?mode=ro", uri=True', self.source)
        self.assertIn("EXISTING_PENDING_CONTROLS=", self.source)
        self.assertIn('if pending_controls:', self.source)
        self.assertIn('raise SystemExit("bulk push deferred:', self.source)
        self.assertLess(
            self.source.index('if pending_controls:'),
            self.source.index('call("POST", "/api/dynamic-workers/force-push"'),
        )
        self.assertNotIn("SUPERSEDED_PENDING=", self.source)
        self.assertNotIn("scheduled/manual push-all superseded", self.source)

    def test_embedded_python_remains_syntactically_valid(self):
        ast.parse(self.script, filename=str(WORKFLOW))


if __name__ == "__main__":
    unittest.main()
