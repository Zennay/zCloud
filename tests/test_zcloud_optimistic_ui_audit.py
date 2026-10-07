import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_optimistic_ui_audit as audit


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_optimistic_ui_audit.py"
APP = ROOT / "public" / "app.js"


class OptimisticUiAuditTests(unittest.TestCase):
    def test_current_dashboard_has_only_bounded_layout_gap(self):
        result = audit.audit(APP.read_text(encoding="utf-8"))
        self.assertEqual("baseline_bounded", result["status"])
        self.assertEqual(["saveProjectLayout"], result["remaining_baseline_debt"])
        self.assertEqual([], result["unexpected_debt"])
        self.assertGreaterEqual(result["mutator_count"], 9)
        self.assertEqual(result["mutator_count"] - 1, result["guarded_count"])
        self.assertFalse(result["mutation_performed"])

    def test_new_unguarded_write_is_a_regression(self):
        source = APP.read_text(encoding="utf-8") + """
async function unsafeFutureWrite(value){
  await post('/api/future-write',{value});
}
"""
        result = audit.audit(source)
        self.assertEqual("regressed", result["status"])
        self.assertEqual(["unsafeFutureWrite"], result["unexpected_debt"])

    def test_new_guarded_write_is_allowed(self):
        source = APP.read_text(encoding="utf-8") + """
async function guardedFutureWrite(button,value){
  button.disabled=true;
  button.textContent='Saving…';
  await post('/api/future-write',{value});
}
"""
        result = audit.audit(source)
        self.assertNotEqual("regressed", result["status"])
        self.assertEqual([], result["unexpected_debt"])
        self.assertGreaterEqual(result["visible_pending_count"], 1)

    def test_require_ratchet_accepts_current_bounded_baseline(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--app", str(APP), "--json", "--require-ratchet"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual("baseline_bounded", payload["status"])
        self.assertEqual(["saveProjectLayout"], payload["remaining_baseline_debt"])

    def test_require_ratchet_rejects_new_debt(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "app.js"
            candidate.write_text(
                APP.read_text(encoding="utf-8")
                + """
async function unsafeFutureWrite(value){
  await post('/api/future-write',{value});
}
""",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--app",
                    str(candidate),
                    "--json",
                    "--require-ratchet",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(2, proc.returncode)
        payload = json.loads(proc.stdout)
        self.assertEqual("regressed", payload["status"])

    def test_symlink_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "app.js"
            link = root / "link.js"
            target.write_text("safe", encoding="utf-8")
            link.symlink_to(target)
            with self.assertRaises(audit.AuditError):
                audit._read_source(link)


if __name__ == "__main__":
    unittest.main()
