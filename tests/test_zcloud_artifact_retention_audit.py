from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_artifact_retention_audit as audit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_artifact_retention_audit.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-artifact-retention-audit.yml"


class ArtifactRetentionAuditTests(unittest.TestCase):
    def test_classifies_bounded_missing_over_limit_and_dynamic(self):
        text = """name: demo
jobs:
  one:
    steps:
      - uses: actions/upload-artifact@v4
        with:
          name: bounded
          retention-days: 14
      - uses: actions/upload-artifact@v4
        with:
          name: missing
      - uses: actions/upload-artifact@v4
        with:
          name: long
          retention-days: 45
      - uses: actions/upload-artifact@v4
        with:
          name: dynamic
          retention-days: ${{ inputs.days }}
"""
        rows = audit.inspect_workflow(".github/workflows/demo.yml", text, max_days=30)
        self.assertEqual(
            ["bounded", "missing", "over_limit", "dynamic_or_invalid"],
            [row.status for row in rows],
        )
        self.assertEqual([14, None, 45, None], [row.retention_days for row in rows])

    def test_detects_named_upload_step_with_uses_on_following_line(self):
        text = """jobs:
  proof:
    steps:
      - name: Upload evidence
        uses: actions/upload-artifact@v4
        with:
          name: evidence
          retention-days: 7
"""
        rows = audit.inspect_workflow(".github/workflows/named.yml", text, max_days=30)
        self.assertEqual(1, len(rows))
        self.assertEqual("bounded", rows[0].status)
        self.assertEqual(7, rows[0].retention_days)

    def test_rejects_invalid_github_retention_range(self):
        text = """jobs:
  proof:
    steps:
      - uses: actions/upload-artifact@v4
        with:
          retention-days: 0
      - uses: actions/upload-artifact@v4
        with:
          retention-days: 91
"""
        rows = audit.inspect_workflow(".github/workflows/demo.yml", text, max_days=30)
        self.assertEqual(["invalid", "invalid"], [row.status for row in rows])

    def test_summary_is_bounded_and_marks_no_mutation(self):
        rows = [
            audit.ArtifactUpload(".github/workflows/a.yml", 10, 14, "bounded"),
            audit.ArtifactUpload(".github/workflows/b.yml", 20, None, "missing"),
        ]
        payload = audit.summarize(rows, max_days=30)
        self.assertEqual(2, payload["upload_count"])
        self.assertEqual(1, payload["finding_count"])
        self.assertFalse(payload["mutation_performed"])
        self.assertEqual(
            {"path", "line", "retention_days", "status"},
            set(payload["findings"][0]),
        )

    def test_repository_scan_rejects_symlink_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            target = root / "outside.yml"
            target.write_text(
                "steps:\n  - uses: actions/upload-artifact@v4\n",
                encoding="utf-8",
            )
            (workflows / "linked.yml").symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "symlink workflow rejected"):
                audit.scan_repository(root, max_days=30)

    def test_cli_is_read_only_by_default_and_require_bounded_can_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            path = workflows / "demo.yml"
            path.write_text(
                "jobs:\n  proof:\n    steps:\n"
                "      - uses: actions/upload-artifact@v4\n"
                "        with:\n          name: demo\n",
                encoding="utf-8",
            )
            before = path.read_bytes()
            normal = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(root)],
                check=False,
                capture_output=True,
                text=True,
            )
            strict = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(root),
                    "--require-bounded",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, normal.returncode, normal.stderr)
            self.assertEqual(1, strict.returncode, strict.stderr)
            self.assertEqual(before, path.read_bytes())
            payload = json.loads(normal.stdout)
            self.assertFalse(payload["ok"])
            self.assertFalse(payload["mutation_performed"])

    def test_validation_workflow_is_exact_head_read_only_and_guarded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("mutation_performed", text)
        for forbidden in (
            "contents: write",
            "actions: write",
            "sudo ",
            "systemctl restart",
            "git push",
            "--require-bounded",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
