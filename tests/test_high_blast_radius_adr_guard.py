import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_high_blast_radius_adr_guard.py"
SPEC = importlib.util.spec_from_file_location("adr_guard", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(guard)


VALID_ADR = """# ADR: Protect high-blast-radius change review

## Context
A change touches a control-plane surface whose failure could affect multiple workers or projects.

## Decision
Require an explicit architecture decision record in the same pull request before integration.

## Blast Radius
The affected runtime or operational path can influence multiple projects, workers, or recovery lanes.

## Rollback
Revert the candidate change and its ADR together, then verify the prior exact revision remains healthy.

## Validation
Run focused contract tests plus the repository regression workflow on the exact candidate revision.
"""


class HighBlastRadiusAdrGuardTests(unittest.TestCase):
    def test_exact_control_plane_surfaces_are_high_blast(self):
        expected = {
            "server.py": "runtime_core",
            "projects.json": "project_registry",
            "project-contracts.json": "project_contract",
            "resource-policy.json": "resource_policy",
            "autonomy-policy.json": "autonomy_policy",
        }
        for path, category in expected.items():
            with self.subTest(path=path):
                risk = guard.classify_path(path)
                self.assertIsNotNone(risk)
                self.assertEqual(risk.category, category)

    def test_mutating_workflow_and_script_names_are_high_blast(self):
        cases = {
            ".github/workflows/zcloud-production-deploy.yml": "mutating_workflow",
            ".github/workflows/recover-workers.yml": "mutating_workflow",
            "scripts/zcloud_scheduler_reconcile.py": "mutating_script",
            "scripts/rollback_release.py": "mutating_script",
        }
        for path, category in cases.items():
            with self.subTest(path=path):
                risk = guard.classify_path(path)
                self.assertIsNotNone(risk)
                self.assertEqual(risk.category, category)

    def test_normal_policy_tests_and_docs_do_not_require_adr(self):
        for path in (
            "scripts/zcloud_high_blast_radius_adr_guard.py",
            "tests/test_high_blast_radius_adr_guard.py",
            ".github/workflows/zcloud-high-blast-radius-adr.yml",
            "docs/adr/README.md",
            "README.md",
        ):
            with self.subTest(path=path):
                self.assertIsNone(guard.classify_path(path))

    def test_non_risky_change_is_allowed_without_adr(self):
        result = guard.evaluate(
            root=ROOT,
            changed_files=[
                "tests/test_example.py",
                "docs/example.md",
            ],
        )
        self.assertEqual(result["decision"], "ADR_NOT_REQUIRED")
        self.assertEqual(result["risk_count"], 0)
        self.assertEqual(result["adr_count"], 0)

    def test_high_blast_change_requires_adr(self):
        result = guard.evaluate(root=ROOT, changed_files=["server.py"])
        self.assertEqual(result["decision"], "ADR_REQUIRED")
        self.assertEqual(result["risk_count"], 1)
        self.assertEqual(result["risk_categories"], ["runtime_core"])

    def test_valid_changed_adr_covers_high_blast_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "docs" / "adr" / "20261006-control-plane-safety.md"
            path.parent.mkdir(parents=True)
            path.write_text(VALID_ADR, encoding="utf-8")
            result = guard.evaluate(
                root=root,
                changed_files=[
                    "server.py",
                    "docs/adr/20261006-control-plane-safety.md",
                ],
            )
        self.assertEqual(result["decision"], "ADR_PRESENT")
        self.assertEqual(result["risk_count"], 1)
        self.assertEqual(result["adr_count"], 1)

    def test_adr_requires_all_substantive_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "docs" / "adr" / "20261006-incomplete-decision.md"
            path.parent.mkdir(parents=True)
            path.write_text(
                VALID_ADR.replace(
                    "## Rollback\nRevert the candidate change and its ADR together, then verify the prior exact revision remains healthy.\n\n",
                    "",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(guard.AdrGuardError, "adr_missing_rollback"):
                guard.evaluate(
                    root=root,
                    changed_files=[
                        "server.py",
                        "docs/adr/20261006-incomplete-decision.md",
                    ],
                )

    def test_adr_rejects_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "docs" / "adr" / "20261006-placeholder-decision.md"
            path.parent.mkdir(parents=True)
            path.write_text(
                VALID_ADR.replace(
                    "Revert the candidate change and its ADR together, then verify the prior exact revision remains healthy.",
                    "TODO placeholder rollback details will be supplied later.",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(guard.AdrGuardError, "adr_placeholder_rollback"):
                guard.evaluate(
                    root=root,
                    changed_files=[
                        "server.py",
                        "docs/adr/20261006-placeholder-decision.md",
                    ],
                )

    def test_invalid_adr_filename_is_rejected(self):
        with self.assertRaisesRegex(guard.AdrGuardError, "invalid_adr_filename"):
            guard.changed_adr_paths(
                ["server.py", "docs/adr/Decision With Spaces.md"]
            )

    def test_symlink_adr_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adr_dir = root / "docs" / "adr"
            adr_dir.mkdir(parents=True)
            real = adr_dir / "real.md"
            real.write_text(VALID_ADR, encoding="utf-8")
            link = adr_dir / "20261006-symlink-decision.md"
            try:
                link.symlink_to(real)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unsupported")
            with self.assertRaisesRegex(guard.AdrGuardError, "adr_symlink_rejected"):
                guard.evaluate(
                    root=root,
                    changed_files=["server.py", "docs/adr/20261006-symlink-decision.md"],
                )

    def test_changed_paths_fail_closed(self):
        with self.assertRaisesRegex(guard.AdrGuardError, "unsafe_changed_path"):
            guard.evaluate(root=ROOT, changed_files=["../server.py"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
