import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_progressive_disclosure_audit as audit


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_progressive_disclosure_audit.py"
APP = ROOT / "public" / "app.js"
ENHANCEMENTS = ROOT / "public" / "enhancements.js"


class ProgressiveDisclosureAuditTests(unittest.TestCase):
    def test_current_dashboard_baseline_identifies_only_resource_detail_gap(self):
        result = audit.audit(
            APP.read_text(encoding="utf-8"),
            ENHANCEMENTS.read_text(encoding="utf-8"),
        )
        self.assertEqual("needs_hardening", result["status"])
        self.assertEqual(["resource_technical_details_collapsed"], result["missing"])
        passed = {item["id"] for item in result["checks"] if item["ok"]}
        self.assertEqual(
            {
                "worker_technical_details_collapsed",
                "evidence_sources_collapsed",
                "ftmo_test_details_collapsed",
                "incident_technical_details_collapsed",
            },
            passed,
        )
        self.assertFalse(result["mutation_performed"])

    def test_complete_fixture_is_green(self):
        app = (
            "function workerDetailPanel(p){"
            '<details class="worker-details"><summary>Meer details</summary>'
            "Worker-ID Conversation-ID Heartbeat Branch / PR Lease</details>"
            "}async function controlWorker("
        )
        enhancements = (
            "function evidencePanel(p){"
            '<details class="section-details evidence-details"><summary>Sources and technical details</summary></details>'
            "}  function qPanel(p){"
            "}function readinessPanel(p){"
            '<details class="section-details readiness-details"><summary>Technical test details</summary></details>'
            "}  function resourcePanel(){"
            '<details class="resource-tech-panel"><summary>Technical details</summary></details>'
            "}  function incidentPanel(){"
            '<details class="section-details incident-details"><summary>Technical details</summary></details>'
            "}  function milestonePanel(p){"
        )
        result = audit.audit(app, enhancements)
        self.assertEqual("complete", result["status"])
        self.assertEqual([], result["missing"])

    def test_require_complete_rejects_current_known_gap(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--app",
                str(APP),
                "--enhancements",
                str(ENHANCEMENTS),
                "--json",
                "--require-complete",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, proc.returncode)
        payload = json.loads(proc.stdout)
        self.assertEqual("needs_hardening", payload["status"])
        self.assertEqual(["resource_technical_details_collapsed"], payload["missing"])

    def test_missing_function_fails_closed(self):
        with self.assertRaises(audit.AuditError):
            audit.audit("function nope(){}", ENHANCEMENTS.read_text(encoding="utf-8"))

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
