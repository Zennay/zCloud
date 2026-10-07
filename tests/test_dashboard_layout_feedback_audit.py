import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_dashboard_layout_feedback_audit.py"
APP = ROOT / "public" / "app.js"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-dashboard-layout-feedback-audit.yml"

spec = importlib.util.spec_from_file_location("layout_feedback_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(audit)


def source(archive_handler: str, restore_handler: str) -> str:
    return (
        '<button data-archive="cloud">Archive</button>'
        '<button data-restore="cloud">Restore</button>'
        "const archive=e.target.closest('[data-archive]');"
        + archive_handler
        + "const restore=e.target.closest('[data-restore]');"
        + restore_handler
        + "const projectCardTarget=e.target.closest('[data-project-id]');"
    )


class DashboardLayoutFeedbackAuditTests(unittest.TestCase):
    def test_complete_controls_require_pending_and_error_feedback(self):
        text = source(
            "archive.disabled=true;archive.textContent='Archiving…';"
            "try{await saveProjectLayout([],[])}catch(error){window.alert(error.message)}",
            "restore.disabled=true;restore.setAttribute('aria-busy','true');"
            "try{await saveProjectLayout([],[])}catch(error){$('notice').textContent=error.message}",
        )
        result = audit.audit_source(text)
        self.assertEqual("complete", result["status"])
        self.assertEqual(2, result["controls_complete"])
        self.assertEqual({}, result["missing_by_control"])

    def test_silent_pending_controls_are_reported_without_source_leakage(self):
        text = source(
            "await saveProjectLayout([],[]);",
            "await saveProjectLayout([],[]);",
        )
        result = audit.audit_source(text)
        self.assertEqual("needs_hardening", result["status"])
        self.assertEqual(2, result["controls_missing_feedback"])
        self.assertEqual(
            ["pending_disable", "pending_copy", "error_feedback"],
            result["missing_by_control"]["archive"],
        )
        self.assertNotIn("saveProjectLayout", json.dumps(result))

    def test_missing_render_or_handler_fails_closed(self):
        result = audit.audit_source("const nothing='here';")
        self.assertEqual("needs_hardening", result["status"])
        self.assertIn("render_marker", result["missing_by_control"]["archive"])
        self.assertIn("handler", result["missing_by_control"]["restore"])

    def test_current_dashboard_exposes_known_layout_feedback_debt(self):
        result = audit.audit_source(APP.read_text(encoding="utf-8"))
        self.assertEqual("needs_hardening", result["status"])
        self.assertEqual(2, result["controls_observed"])
        self.assertIn("archive", result["missing_by_control"])
        self.assertIn("restore", result["missing_by_control"])

    def test_require_complete_is_strict_but_observation_mode_is_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.js"
            path.write_text(source("await saveProjectLayout([],[]);", "await saveProjectLayout([],[]);"), encoding="utf-8")
            with mock.patch("builtins.print"):
                self.assertEqual(0, audit.main(["--source", str(path), "--json"]))
                self.assertEqual(1, audit.main(["--source", str(path), "--json", "--require-complete"]))

    def test_symlink_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "real.js"
            target.write_text("x", encoding="utf-8")
            link = root / "app.js"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                audit.read_source(link)

    def test_workflow_is_read_only_and_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("zcloud_dashboard_layout_feedback_audit.py --json", text)
        self.assertNotIn("--require-complete", text)
        for forbidden in ("curl ", "ssh ", "sudo ", "systemctl ", "sqlite3 "):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
