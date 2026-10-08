"""Offline regression for deploy evidence member-name handling."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_deploy_artifact_member_path_boundary_20261008.py"
spec = importlib.util.spec_from_file_location("deploy_artifact_path_boundary", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class MemberPathBoundaryTests(unittest.TestCase):
    def test_simple_relative_artifacts_are_screened(self):
        for path in ("receipts/sha256.json", "evidence/report.txt", "2026-10-08/receipt.json"):
            with self.subTest(path=path):
                result = mod.evaluate_member(path)
                self.assertTrue(result["accepted_for_offline_name_screen"])
                self.assertFalse(result["deploy_authorized"])
                self.assertFalse(result["recovery_authorized"])
                self.assertFalse(result["mutation_performed"])

    def test_fail_closed_for_unsafe_names(self):
        for name in (None, "", 3, "../secret", "a/../b", "a/./b",
                     "/etc/passwd", "C:/Windows/system32", "a\\..\\secret",
                     "a//b", "a/", "./a", "x\x00y", "x\ny",
                     "\\\\server\\share", "name:stream", "x.",
                     "x ", "x" * 513, "résumé.json", "a b.txt",
                     "a?b", "a*test", "a%2f..%2fsecret", "a\\u202ejson",
                     ".hidden", "a/💾.json"):
            with self.subTest(name=repr(name)):
                result = mod.evaluate_member(name)
                self.assertFalse(result["accepted_for_offline_name_screen"])
                self.assertTrue(result["reasons"])
                self.assertFalse(result["deploy_authorized"])
                self.assertFalse(result["recovery_authorized"])
                self.assertFalse(result["mutation_performed"])

    def test_safe_ascii_segments(self):
        for name in ("a_b-1.txt", "nested/receipt_2026-10-08.json"):
            self.assertTrue(mod.evaluate_member(name)["accepted_for_offline_name_screen"])

    def test_repeated_checks_are_deterministic(self):
        self.assertEqual(mod.evaluate_member("a/../b"), mod.evaluate_member("a/../b"))


if __name__ == "__main__":
    unittest.main()
