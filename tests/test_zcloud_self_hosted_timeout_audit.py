from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_self_hosted_timeout_audit as audit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_self_hosted_timeout_audit.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-self-hosted-timeout-audit.yml"


class SelfHostedTimeoutAuditTests(unittest.TestCase):
    def test_classifies_self_hosted_job_timeouts(self):
        text = """name: demo
jobs:
  bounded:
    runs-on: [self-hosted, zcloud, vps]
    timeout-minutes: 10
    steps:
      - run: true
  missing:
    runs-on: self-hosted
    steps:
      - run: true
  long:
    runs-on: [self-hosted, zcloud, vps]
    timeout-minutes: 90
    steps:
      - run: true
  dynamic:
    runs-on: self-hosted
    timeout-minutes: ${{ inputs.timeout }}
    steps:
      - run: true
  hosted:
    runs-on: ubuntu-latest
    steps:
      - run: true
"""
        rows = audit.inspect_workflow(".github/workflows/demo.yml", text, max_minutes=30)
        self.assertEqual(
            ["bounded", "missing", "over_limit", "dynamic_or_invalid"],
            [row.status for row in rows],
        )
        self.assertEqual(
            ["bounded", "missing", "long", "dynamic"],
            [row.job for row in rows],
        )
        self.assertEqual([10, None, 90, None], [row.timeout_minutes for row in rows])

    def test_detects_multiline_self_hosted_runs_on(self):
        text = """jobs:
  prove:
    runs-on:
      - self-hosted
      - zcloud
      - vps
    timeout-minutes: 5
    steps:
      - run: true
"""
        rows = audit.inspect_workflow(".github/workflows/multi.yml", text, max_minutes=30)
        self.assertEqual(1, len(rows))
        self.assertEqual("bounded", rows[0].status)
        self.assertEqual(5, rows[0].timeout_minutes)

    def test_ignores_reusable_and_hosted_jobs(self):
        text = """jobs:
  reusable:
    uses: ./.github/workflows/reusable.yml
  hosted:
    runs-on: ubuntu-latest
    timeout-minutes: 500
    steps:
      - run: true
"""
        self.assertEqual(
            [],
            audit.inspect_workflow(".github/workflows/hosted.yml", text, max_minutes=30),
        )

    def test_summary_is_bounded_and_read_only(self):
        rows = [
            audit.TimeoutFinding(".github/workflows/a.yml", "one", 10, "bounded"),
            audit.TimeoutFinding(".github/workflows/b.yml", "two", None, "missing"),
        ]
        payload = audit.summarize(rows, max_minutes=30)
        self.assertEqual(2, payload["self_hosted_job_count"])
        self.assertEqual(1, payload["finding_count"])
        self.assertFalse(payload["mutation_performed"])
        self.assertEqual(
            {"path", "job", "timeout_minutes", "status"},
            set(payload["findings"][0]),
        )

    def test_repository_scan_rejects_symlink_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            target = root / "outside.yml"
            target.write_text(
                "jobs:\n  proof:\n    runs-on: self-hosted\n",
                encoding="utf-8",
            )
            (workflows / "linked.yml").symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "symlink workflow rejected"):
                audit.scan_repository(root, max_minutes=30)

    def test_cli_audits_without_mutation_and_strict_mode_can_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            path = workflows / "demo.yml"
            path.write_text(
                "jobs:\n  proof:\n    runs-on: self-hosted\n    steps:\n      - run: true\n",
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
            self.assertEqual(1, payload["self_hosted_job_count"])
            self.assertEqual(1, payload["finding_count"])
            self.assertFalse(payload["mutation_performed"])

    def test_workflow_is_exact_head_read_only_and_bounded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("timeout-minutes: 5", text)
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
        self.assertIn("retention-days: 14", text)
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
