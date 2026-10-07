import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_resource_priority_feedback_audit.py"
SOURCE = ROOT / "public" / "enhancements.js"

sys.path.insert(0, str(ROOT / "scripts"))
import zcloud_resource_priority_feedback_audit as audit  # noqa: E402


COMPLETE = """
<select data-resource-priority="cloud" data-previous-value="normal"></select>
<script>
document.addEventListener('change',async function(e){
  var el=e.target.closest('[data-resource-priority]');
  var previous=el.dataset.previousValue||'normal';
  el.disabled=true;
  el.setAttribute('aria-busy','true');
  $('notice').hidden=false;
  $('notice').textContent='Saving priority…';
  try{
    var response=await fetch('/api/resource-priority',{method:'POST'});
    var data=await response.json();
    if(!response.ok)throw new Error('Save failed');
    var saved=data.resource.priority;
    el.dataset.previousValue=saved;
    $('notice').hidden=false;
    $('notice').textContent='Priority updated.';
    if(data.resource.applied===false){
      $('notice').textContent='Priority saved. The live VPS weight could not be applied yet';
    }
    await refresh(true);
  }catch(err){
    el.value=previous;
    $('notice').hidden=false;
    $('notice').textContent='Project priority could not be saved: '+err;
  }finally{
    el.disabled=false;
    el.removeAttribute('aria-busy');
  }
  },true);
</script>
"""


class ResourcePriorityFeedbackAuditTests(unittest.TestCase):
    def test_complete_contract_is_accepted(self):
        report = audit.audit_source(COMPLETE)
        self.assertEqual("complete", report["state"])
        self.assertEqual([], report["missing"])
        self.assertTrue(all(report["checks"][key] for key in (
            "control_present",
            "pending_disable",
            "pending_feedback",
            "backend_confirmation",
            "success_feedback",
            "degraded_feedback",
            "failure_feedback",
            "reenabled",
        )))

    def test_missing_pending_and_success_feedback_are_explicit(self):
        source = COMPLETE.replace(
            "$('notice').textContent='Saving priority…';", ""
        ).replace(
            "$('notice').textContent='Priority updated.';", ""
        ).replace("el.setAttribute('aria-busy','true');", "")
        report = audit.audit_source(source)
        self.assertEqual("needs_hardening", report["state"])
        self.assertEqual(
            ["pending_feedback", "success_feedback"],
            report["missing"],
        )
        self.assertTrue(report["checks"]["failure_feedback"])
        self.assertTrue(report["checks"]["degraded_feedback"])

    def test_backend_confirmation_and_reenable_order_are_required(self):
        source = COMPLETE.replace("el.disabled=true;", "")
        report = audit.audit_source(source)
        self.assertIn("pending_disable", report["missing"])

        source = COMPLETE.replace("el.disabled=false;", "")
        report = audit.audit_source(source)
        self.assertIn("reenabled", report["missing"])

    def test_unrelated_feedback_copy_cannot_satisfy_resource_handler(self):
        source = COMPLETE.replace(
            "$('notice').textContent='Saving priority…';", ""
        ).replace(
            "$('notice').textContent='Priority updated.';", ""
        ).replace("el.setAttribute('aria-busy','true');", "")
        source += "\n<div>Saving… Priority updated.</div>\n"
        report = audit.audit_source(source)
        self.assertEqual(
            ["pending_feedback", "success_feedback"],
            report["missing"],
        )

    def test_current_repo_baseline_cannot_regress_beyond_known_debt(self):
        report = audit.audit_source(SOURCE.read_text(encoding="utf-8"))
        self.assertIn(report["state"], ("complete", "needs_hardening"))
        self.assertTrue(
            set(report["missing"]).issubset({"pending_feedback", "success_feedback"}),
            report["missing"],
        )
        for key in (
            "control_present",
            "pending_disable",
            "backend_confirmation",
            "confirmed_refresh",
            "degraded_feedback",
            "failure_feedback",
            "reenabled",
        ):
            self.assertTrue(report["checks"][key], key)

    def test_feedback_must_be_causally_placed_in_the_handler(self):
        source = COMPLETE.replace(
            "  el.setAttribute('aria-busy','true');\n", ""
        ).replace(
            "  $('notice').textContent='Saving priority…';\n", ""
        ).replace(
            "    $('notice').textContent='Priority updated.';\n", ""
        )
        source = source.replace(
            "  el.disabled=true;\n",
            "  $('notice').textContent='Saving priority…';\n  el.disabled=true;\n",
        )
        source = source.replace(
            "    el.dataset.previousValue=saved;\n",
            "    $('notice').textContent='Priority updated.';\n    el.dataset.previousValue=saved;\n",
        )
        report = audit.audit_source(source)
        self.assertIn("pending_feedback", report["missing"])
        self.assertIn("success_feedback", report["missing"])

    def test_cli_observation_stays_green_but_require_complete_fails_closed(self):
        observed = subprocess.run(
            [sys.executable, str(SCRIPT), "--source", str(SOURCE), "--json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, observed.returncode, observed.stderr)
        payload = json.loads(observed.stdout)
        self.assertEqual("needs_hardening", payload["state"])

        required = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source",
                str(SOURCE),
                "--json",
                "--require-complete",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, required.returncode)

    def test_symlink_and_oversized_sources_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.js"
            target.write_text(COMPLETE, encoding="utf-8")
            link = root / "link.js"
            link.symlink_to(target)
            with self.assertRaisesRegex(audit.AuditError, "source_symlink"):
                audit.read_bounded_regular_file(link)

            large = root / "large.js"
            large.write_bytes(b"x" * (audit.MAX_SOURCE_BYTES + 1))
            with self.assertRaisesRegex(audit.AuditError, "source_too_large"):
                audit.read_bounded_regular_file(large)

    def test_report_never_contains_source_text(self):
        secret = "SUPER_SECRET_RESOURCE_PRIORITY_TOKEN"
        report = audit.audit_source(COMPLETE + secret)
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn(secret, serialized)


if __name__ == "__main__":
    unittest.main()
