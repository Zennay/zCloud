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

    def test_only_actual_steps_count_not_shell_documentation(self):
        source = """jobs:
  check:
    steps:
      - name: Show a sample (not a real upload)
        run: |
          cat <<'EXAMPLE'
          steps:
            - uses: actions/upload-artifact@v4
              with:
                retention-days: 3
          EXAMPLE
      - uses: actions/upload-artifact@v4
        with:
          name: real
          retention-days: 14
"""
        rows = audit.inspect_workflow("sample.yml", source, max_days=30)
        self.assertEqual(1, len(rows))
        self.assertEqual("bounded", rows[0].status)
        self.assertEqual(14, rows[0].retention_days)

    def test_retention_input_must_be_in_upload_steps_with_mapping(self):
        source = """jobs:
  check:
    steps:
      - uses: actions/upload-artifact@v4
        env:
          retention-days: 7
        with:
          name: evidence
      - uses: actions/upload-artifact@v4
        with:
          name: second
          unrelated:
            retention-days: 1
      - uses: "actions/upload-artifact@v4"
        with:
          retention-days: '20'
"""
        rows = audit.inspect_workflow("sample.yml", source, max_days=30)
        self.assertEqual(["missing", "missing", "bounded"], [r.status for r in rows])
        self.assertEqual([None, None, 20], [r.retention_days for r in rows])

    def test_comments_nested_or_non_step_uses_do_not_count(self):
        source = """# - uses: actions/upload-artifact@v4
jobs:
  check:
    steps:
      - name: demo
        run: >-
          - uses: actions/upload-artifact@v4
      - uses: actions/checkout@v4
        with:
          extra: uses: actions/upload-artifact@v4
      - uses: actions/upload-artifact@v4 # a real step
        with:
          retention-days: ${{ inputs.retention_days }}
"""
        rows = audit.inspect_workflow("sample.yml", source, max_days=30)
        self.assertEqual(1, len(rows))
        self.assertEqual("dynamic_or_invalid", rows[0].status)

    def test_rejects_symlink_workflow_dir_and_oversized_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            (root / ".github").mkdir()
            (root / ".github" / "workflows").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "symlink workflow directory"):
                audit.scan_repository(root, max_days=30)
            (root / ".github" / "workflows").unlink()
            work = root / ".github" / "workflows"
            work.mkdir()
            (work / "huge.yml").write_bytes(b"x" * (1024 * 1024 + 1))
            with self.assertRaisesRegex(RuntimeError, "workflow too large"):
                audit.scan_repository(root, max_days=30)

    def test_duplicate_step_uses_cannot_hide_an_upload(self):
        text = """jobs:
  verify:
    steps:
      - uses: actions/upload-artifact@v4
        uses: actions/checkout@v4
        with:
          retention-days: 14
      - uses: actions/checkout@v4
        uses: actions/upload-artifact@v4
        with:
          retention-days: 7
"""
        rows = audit.inspect_workflow("ambiguous.yml", text, max_days=30)
        self.assertEqual(2, len(rows))
        self.assertEqual(["dynamic_or_invalid"] * 2, [row.status for row in rows])
        self.assertEqual([None] * 2, [row.retention_days for row in rows])

    def test_duplicate_with_or_retention_cannot_become_bounded(self):
        text = """jobs:
  verify:
    steps:
      - uses: actions/upload-artifact@v4
        with:
          retention-days: 90
        with:
          retention-days: 7
      - uses: actions/upload-artifact@v4
        with:
          retention-days: 45
          retention-days: 7
      - uses: actions/upload-artifact@v4
        with:
          retention-days: 7
          name: safe
"""
        rows = audit.inspect_workflow("ambiguous.yml", text, max_days=30)
        self.assertEqual(
            ["dynamic_or_invalid", "dynamic_or_invalid", "bounded"],
            [row.status for row in rows],
        )
        self.assertEqual([None, None, 7], [row.retention_days for row in rows])

    def test_retention_rejects_quoted_hash_unicode_and_leading_zero(self):
        text = """jobs:
  verify:
    steps:
      - uses: actions/upload-artifact@v4
        with:
          retention-days: "14#not-a-comment"
      - uses: actions/upload-artifact@v4
        with:
          retention-days: '07'
      - uses: actions/upload-artifact@v4
        with:
          retention-days: '١٤'
      - uses: actions/upload-artifact@v4
        with:
          retention-days: '14' # legitimate comment
      - uses: actions/upload-artifact@v4
        with:
          retention-days: 45 # beyond policy
"""
        rows = audit.inspect_workflow("ambiguous.yml", text, max_days=30)
        self.assertEqual(
            [
                "dynamic_or_invalid",
                "dynamic_or_invalid",
                "dynamic_or_invalid",
                "bounded",
                "over_limit",
            ],
            [row.status for row in rows],
        )
        self.assertEqual([None, None, None, 14, 45], [r.retention_days for r in rows])

    def test_yaml_explicit_indentation_scalar_cannot_forge_upload(self):
        text = """jobs:
  audit:
    steps:
      - name: Inline YAML example
        run: |2-
          steps:
            - uses: actions/upload-artifact@v4
              with:
                retention-days: 2
      - name: Folded YAML example
        run: >+2
          steps:
            - uses: actions/upload-artifact@v4
              with:
                retention-days: 3
      - uses: actions/upload-artifact@v4
        with:
          retention-days: 14
"""
        rows = audit.inspect_workflow("explicit-scalar.yml", text, max_days=30)
        self.assertEqual(1, len(rows))
        self.assertEqual(["bounded"], [r.status for r in rows])
        self.assertEqual([14], [r.retention_days for r in rows])

    def test_repository_scan_denies_symlink_github_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            (outside / "workflows").mkdir(parents=True)
            (outside / "workflows" / "proof.yml").write_text(
                "jobs:\n  proof:\n    steps:\n"
                "      - uses: actions/upload-artifact@v4\n",
                encoding="utf-8",
            )
            (root / ".github").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "symlink workflow directory"):
                audit.scan_repository(root, max_days=30)

    def test_unresolved_yaml_uses_identity_fails_closed(self):
        for unusual in (
            "*shared_artifact",
            "&save actions/upload-artifact@v4",
            "!!str actions/upload-artifact@v4",
            "|-",
        ):
            with self.subTest(unusual=unusual):
                workflow = (
                    "jobs:\n  proof:\n    steps:\n"
                    "      - uses: " + unusual + "\n"
                    "        with:\n          retention-days: 14\n"
                )
                with self.assertRaisesRegex(RuntimeError, "unresolved action identity"):
                    audit.inspect_workflow("alias.yml", workflow, max_days=30)

    def test_unresolved_action_cli_refuses_success_without_content_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow_dir = root / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            path = workflow_dir / "redacted.yml"
            path.write_text(
                "jobs:\n  proof:\n    steps:\n      - uses: *private_alias\n",
                encoding="utf-8",
            )
            original = path.read_bytes()
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(root)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, result.returncode)
            payload = json.loads(result.stdout)
            self.assertIs(payload["ok"], False)
            self.assertEqual("RuntimeError", payload["error"])
            self.assertNotIn("private_alias", result.stdout + result.stderr)
            self.assertEqual(original, path.read_bytes())

    def test_yaml_flow_step_collections_refuse_partial_scan(self):
        cases = (
            "    steps: [{uses: actions/upload-artifact@v4, with: {retention-days: 7}}]",
            "    steps: *shared_steps",
            "    steps: &named_steps",
        )
        for candidate in cases:
            with self.subTest(candidate=candidate):
                text = "jobs:\n  proof:\n" + candidate + "\n"
                with self.assertRaisesRegex(
                    RuntimeError, "unresolved workflow steps collection"
                ):
                    audit.inspect_workflow("flow.yml", text, max_days=30)

    def test_yaml_flow_step_item_cannot_be_skipped(self):
        text = """jobs:
  proof:
    steps:
      - {uses: actions/upload-artifact@v4, with: {retention-days: 7}}
"""
        with self.assertRaisesRegex(RuntimeError, "unresolved flow-style workflow step"):
            audit.inspect_workflow("flow-item.yml", text, max_days=30)

    def test_indentless_step_sequence_fails_closed(self):
        text = """jobs:
  proof:
    steps:
    - uses: actions/upload-artifact@v4
      with:
        retention-days: 14
"""
        with self.assertRaisesRegex(RuntimeError, "unresolved indentless steps collection"):
            audit.inspect_workflow("indentless.yml", text, max_days=30)

    def test_quoted_mapping_keys_cannot_hide_uploads(self):
        cases = (
            '    "steps":',
            "      - 'uses': actions/upload-artifact@v4",
            '        "with":',
            "          'retention-days': 12",
        )
        for candidate in cases:
            with self.subTest(candidate=candidate):
                text = (
                    "jobs:\n  proof:\n    steps:\n"
                    "      - uses: actions/upload-artifact@v4\n"
                    "        with:\n          retention-days: 14\n"
                    + candidate + "\n"
                )
                with self.assertRaisesRegex(
                    RuntimeError, "unresolved workflow mapping key"
                ):
                    audit.inspect_workflow("quoted-key.yml", text, max_days=30)

    def test_empty_inline_steps_remains_harmless(self):
        text = """jobs:
  no_steps:
    steps: [] # intentionally empty
  actual:
    steps:
      - uses: actions/upload-artifact@v4
        with:
          retention-days: 14
"""
        rows = audit.inspect_workflow("empty-inline.yml", text, max_days=30)
        self.assertEqual(1, len(rows))
        self.assertEqual("bounded", rows[0].status)

    def test_flow_step_cli_rejects_without_echoing_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / ".github" / "workflows"
            folder.mkdir(parents=True)
            path = folder / "ambiguous.yml"
            path.write_text(
                "jobs:\n  proof:\n"
                "    steps: *PRIVATE_SCAN_TOKEN\n", encoding="utf-8"
            )
            original = path.read_bytes()
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(root)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertEqual(
                {"ok": False, "error": "RuntimeError"},
                json.loads(result.stdout),
            )
            self.assertNotIn("PRIVATE_SCAN_TOKEN", result.stdout + result.stderr)
            self.assertEqual(original, path.read_bytes())

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
