import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_diagnostic_evidence_policy as policy


class DiagnosticEvidencePolicyTests(unittest.TestCase):
    def test_push_screenshot_capture_is_ungated(self):
        text = """name: capture
on:
  push:
    branches: [main]
jobs:
  proof:
    runs-on: ubuntu-latest
    steps:
      - name: Capture desktop
        run: scrot "$RUNNER_TEMP/screen.png"
"""
        findings = policy.scan_workflow(".github/workflows/capture.yml", text)
        self.assertEqual(1, len(findings))
        self.assertEqual("ungated", findings[0].gate)
        self.assertEqual(("screenshot_capture",), findings[0].kinds)

    def test_failure_gated_journal_is_allowed(self):
        text = """name: diagnose
on:
  pull_request:
jobs:
  proof:
    runs-on: ubuntu-latest
    steps:
      - name: Failure evidence
        if: failure()
        run: journalctl -u zennay-cloud.service -n 80 --no-pager
"""
        finding = policy.scan_workflow(".github/workflows/diag.yml", text)[0]
        self.assertEqual("error_gated", finding.gate)
        self.assertEqual(("raw_journal_dump",), finding.kinds)

    def test_manual_only_diagnostic_is_allowed(self):
        text = """name: diagnose
on:
  workflow_dispatch:
jobs:
  proof:
    runs-on: ubuntu-latest
    steps:
      - name: Manual process evidence
        run: ps -eo pid,ppid,args
"""
        finding = policy.scan_workflow(".github/workflows/manual.yml", text)[0]
        self.assertEqual("manual_only", finding.gate)

    def test_tool_availability_probe_is_not_screenshot_capture(self):
        text = """name: tools
on:
  push:
jobs:
  proof:
    runs-on: ubuntu-latest
    steps:
      - name: Tool inventory
        run: |
          for tool in xdotool import scrot; do
            command -v "$tool" || true
          done
"""
        self.assertEqual([], policy.scan_workflow(".github/workflows/tools.yml", text))

    def test_new_ungated_delta_does_not_reflag_legacy_debt(self):
        legacy = policy.Finding(
            ".github/workflows/legacy.yml",
            "Legacy journal",
            ("raw_journal_dump",),
            "ungated",
        )
        allowed = policy.Finding(
            ".github/workflows/new.yml",
            "Failure screenshot",
            ("screenshot_capture",),
            "error_gated",
        )
        new = policy.Finding(
            ".github/workflows/new.yml",
            "Always screenshot",
            ("screenshot_capture",),
            "ungated",
        )
        delta = policy.compare_findings([legacy], [legacy, allowed, new])
        self.assertEqual([new], delta)

    def test_worktree_scan_rejects_symlink_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            target = root / "outside.yml"
            target.write_text("name: outside\n", encoding="utf-8")
            (workflows / "linked.yml").symlink_to(target)
            with self.assertRaises(RuntimeError):
                policy.scan_tree(root)

    def test_bounded_output_never_contains_command_body(self):
        finding = policy.Finding(
            ".github/workflows/private.yml",
            "Failure evidence",
            ("raw_journal_dump",),
            "ungated",
        )
        output = finding.bounded()
        self.assertEqual(
            {"path", "step", "kinds", "gate", "signature"},
            set(output),
        )
        self.assertNotIn("command", output)
        self.assertEqual(64, len(output["signature"]))
        self.assertNotIn("Failure evidence", output["signature"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
