"""Offline negative-case coverage for Content-Type evidence metadata."""
import unittest

from scripts.zcloud_deploy_artifact_content_type_screen_20261008 import screen_content_type


class ContentTypeBoundaryTests(unittest.TestCase):
    def test_allowed_is_still_non_authorizing(self):
        for value in ("application/json", "application/json; charset=utf-8"):
            with self.subTest(value=value):
                result = screen_content_type(value)
                self.assertEqual(result["observation"], "SYNTAX_ONLY_UNVERIFIED")
                self.assertFalse(result["deploy_authorized"])
                self.assertFalse(result["recovery_authorized"])
                self.assertFalse(result["mutation_authorized"])

    def test_ambiguous_or_untrusted_types_are_rejected(self):
        cases = (
            None, 0, b"application/json", "", "APPLICATION/JSON",
            "application/json; charset=latin-1", "application/json; charset=utf-8; x=1",
            "application/json, text/html", "text/html", "application/problem+json",
            "application/json\r\nX-Injected: yes", " application/json",
            "application/json ", "application/json; charset=UTF-8",
            "application/json; charset=utf-8" + " " * 150,
        )
        for value in cases:
            with self.subTest(value=value):
                result = screen_content_type(value)
                self.assertEqual(result["observation"], "REJECTED")
                self.assertFalse(any(result[k] for k in (
                    "deploy_authorized", "recovery_authorized", "mutation_authorized"
                )))


if __name__ == "__main__":
    unittest.main()
