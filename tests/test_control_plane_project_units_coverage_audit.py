import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_project_units_coverage_audit.py"

spec = importlib.util.spec_from_file_location("units_audit", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


class ProjectUnitsCoverageAuditTests(unittest.TestCase):
    def test_current_main_exposes_zguard_units_gap(self):
        report = mod.audit(ROOT / "projects.json", ROOT / "enhancements.py")
        self.assertEqual(report["format"], "zcloud-project-units-coverage-v1")
        self.assertIn("zguard", report["missing_active_projects"])
        self.assertFalse(report["coverage_complete"])

    def test_duplicate_project_ids_fail_closed(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            projects = root / "projects.json"
            projects.write_text(
                '[{"id":"x","status":"active"},{"id":"x","status":"active"}]',
                encoding="utf-8",
            )
            enhancements = root / "enhancements.py"
            enhancements.write_text('PROJECT_UNITS = {"x": []}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate project ids"):
                mod.audit(projects, enhancements)

    def test_symlink_inputs_are_rejected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "projects-real.json"
            source.write_text('[{"id":"x","status":"active"}]', encoding="utf-8")
            link = root / "projects.json"
            link.symlink_to(source)
            enhancements = root / "enhancements.py"
            enhancements.write_text('PROJECT_UNITS = {"x": []}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "symlink"):
                mod.audit(link, enhancements)


if __name__ == "__main__":
    unittest.main()
