"""Deny Python import shadowing in automatic source-only PR admission.

Explicit deny-only tests; no GitHub, runner or VPS calls.
"""
import contextlib
import io
import json
import unittest

from scripts import zcloud_autonomous_pr_integrator as engine
from tests.test_zcloud_autonomous_pr_integrator import A, B, ENV, checks, files, pr


class PythonSourceImportBoundary(unittest.TestCase):
    DANGEROUS = (
        "scripts/__init__.py",
        "tests/__init__.py",
        "scripts/sitecustomize.py",
        "tests/sitecustomize.py",
        "scripts/usercustomize.py",
        "tests/usercustomize.py",
        "scripts/json.py",
        "scripts/json.pyc",
        "scripts/json/__init__.py",
        "scripts/argparse.py",
        "scripts/re.py",
        "scripts/urllib/__init__.py",
        "scripts/urllib/request.py",
        "scripts/email/__init__.py",
        "scripts/xml/__init__.py",
        "scripts/typing.py",
        "scripts/logging.py",
        "tests/some_module.pyc",
        "tests/__pycache__/danger.pyc",
    )

    def test_startup_and_import_shadow_paths_cannot_be_auto_integrated(self):
        for path in self.DANGEROUS:
            with self.subTest(path=path):
                self.assertTrue(engine.unsafe_import_shadow(path))
                self.assertFalse(engine.safe_path(path))
                candidate = pr()
                candidate["changed_files"] = 1
                self.assertEqual(
                    engine.decide(candidate, [{"filename": path, "status": "added"}],
                                  checks(), [], B),
                    "unsafe_python_import_root")

    def test_import_aliases_cannot_hide_in_mixed_safe_PR(self):
        for path in self.DANGEROUS:
            candidate = pr()
            candidate["changed_files"] = 2
            changed = files() + [{"filename": path, "status": "modified"}]
            with self.subTest(path=path):
                self.assertEqual(
                    engine.decide(candidate, changed, checks(), [], B),
                    "unsafe_python_import_root")

    def test_legitimate_python_helpers_still_pass(self):
        for path in (
            "scripts/my_separate_health_check.py",
            "tests/test_independent_quality.py",
            "docs/installation.md",
            "scripts/geometry/helpers.py",
            "scripts/urllib_helper.py",
        ):
            with self.subTest(path=path):
                self.assertFalse(engine.unsafe_import_shadow(path))
                self.assertTrue(engine.safe_path(path))
                self.assertEqual(engine.decide(
                    pr(), [{"filename": path, "status": "added"}], checks(), [], B),
                    "eligible")

    def test_live_second_pass_refuses_added_import_hook_before_put(self):
        reads = 0
        writes = []
        def transport(token, method, path, data=None):
            nonlocal reads
            if method == "PUT":
                writes.append((path, data))
                self.fail("must never write an import-hook auto-merge")
            if path == "/branches/main":
                return {"commit": {"sha": B}}
            if path.startswith("/pulls?"):
                return [pr()]
            if path == "/pulls/21":
                return pr()
            if path == "/pulls/21/files?per_page=100":
                reads += 1
                if reads == 1:
                    return files()
                return [{"filename": "tests/__init__.py", "status": "modified"}]
            if path == f"/commits/{A}/check-runs?per_page=100":
                sample = checks()
                return {"total_count": len(sample), "check_runs": sample}
            if path == f"/commits/{A}/statuses?per_page=100":
                return []
            self.fail("unexpected endpoint " + path)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(engine.main(["--apply"], ENV, transport), 0)
        self.assertEqual(reads, 2)
        self.assertEqual(writes, [])
        self.assertEqual(json.loads(out.getvalue())["receipts"][0]["decision"],
                         "recheck_unsafe_python_import_root")

    def test_import_roots_require_manual_review(self):
        self.assertTrue(engine.STDLIB_IMPORT_ROOTS)
        self.assertIn("json", engine.STDLIB_IMPORT_ROOTS)
        self.assertIn("urllib", engine.STDLIB_IMPORT_ROOTS)
        self.assertIn("argparse", engine.STDLIB_IMPORT_ROOTS)
        self.assertIn("tests/__init__.py", engine.PYTHON_PACKAGE_ENTRYPOINTS)


if __name__ == "__main__":
    unittest.main()
