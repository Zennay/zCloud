from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_main_writer_compatibility_audit as audit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_main_writer_compatibility_audit.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-main-writer-compatibility-audit.yml"


class MainWriterCompatibilityAuditTests(unittest.TestCase):
    def test_direct_main_push_with_contents_write_is_incompatible(self):
        text = """permissions:
  contents: write
jobs:
  proof:
    steps:
      - run: git push origin HEAD:main
"""
        row = audit.inspect_workflow(".github/workflows/demo.yml", text)
        self.assertIsNotNone(row)
        self.assertTrue(row.contents_write)
        self.assertTrue(row.direct_main_push)
        self.assertEqual("incompatible", row.status)

    def test_refs_api_write_is_incompatible(self):
        text = """permissions:
  contents: write
jobs:
  proof:
    steps:
      - run: gh api -X PATCH repos/Zennay/zCloud/git/refs/heads/main -f sha=deadbeef
"""
        row = audit.inspect_workflow(".github/workflows/demo.yml", text)
        self.assertIsNotNone(row)
        self.assertTrue(row.refs_api_write)
        self.assertEqual("incompatible", row.status)

    def test_contents_write_without_detected_direct_main_write_requires_review(self):
        text = """permissions:
  contents: write
jobs:
  proof:
    steps:
      - run: echo artifact
"""
        row = audit.inspect_workflow(".github/workflows/demo.yml", text)
        self.assertIsNotNone(row)
        self.assertFalse(row.direct_main_push)
        self.assertFalse(row.refs_api_write)
        self.assertEqual("review_required", row.status)

    def test_read_only_workflow_is_not_reported(self):
        text = """permissions:
  contents: read
jobs:
  proof:
    steps:
      - run: git ls-remote origin refs/heads/main
"""
        self.assertIsNone(audit.inspect_workflow(".github/workflows/demo.yml", text))

    def test_summary_is_bounded_and_non_mutating(self):
        rows = [
            audit.Finding(".github/workflows/a.yml", True, True, False, "incompatible"),
            audit.Finding(".github/workflows/b.yml", True, False, False, "review_required"),
        ]
        payload = audit.summarize(rows)
        self.assertEqual(2, payload["workflow_count_with_write_signal"])
        self.assertEqual(1, payload["incompatible_count"])
        self.assertEqual(1, payload["review_required_count"])
        self.assertFalse(payload["branch_protection_ready"])
        self.assertFalse(payload["mutation_performed"])
        self.assertEqual(
            {"path", "contents_write", "direct_main_push", "refs_api_write", "status"},
            set(payload["findings"][0]),
        )

    def test_repository_scan_rejects_symlink_and_oversized_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            target = root / "outside.yml"
            target.write_text("permissions:\n  contents: write\n", encoding="utf-8")
            (workflows / "linked.yml").symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "symlink workflow rejected"):
                audit.scan_repository(root)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "huge.yml").write_text(
                "x" * (audit.MAX_WORKFLOW_BYTES + 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "oversized workflow rejected"):
                audit.scan_repository(root)

    def test_cli_is_read_only_and_strict_mode_fails_on_debt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            path = workflows / "demo.yml"
            path.write_text(
                "permissions:\n  contents: write\njobs:\n  x:\n    steps:\n"
                "      - run: git push origin HEAD:main\n",
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
                    "--require-compatible",
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
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", text)
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
            "systemctl ",
            "git push",
            "--require-compatible",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
