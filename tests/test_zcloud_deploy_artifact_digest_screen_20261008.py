"""Negative/positive syntax fixtures for the non-authorizing digest screen."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from zcloud_deploy_artifact_digest_screen_20261008 import screen_digest


class DigestScreenTests(unittest.TestCase):
    def test_lowercase_sha256_is_only_syntax_valid(self):
        receipt = screen_digest("sha256:" + "a" * 64)
        self.assertEqual(receipt["status"], "SYNTAX_ONLY_UNVERIFIED")
        self.assertTrue(receipt["syntax_valid"])
        self.assertFalse(receipt["content_verified"])

    def test_bad_inputs_rejected(self):
        good = "sha256:" + "a" * 64
        samples = [
            None, 0, {}, [], b"sha256:" + b"a" * 64,
            "", "a" * 64, "SHA256:" + "a" * 64,
            "sha256:" + "A" * 64, "sha256:" + "a" * 63,
            "sha256:" + "a" * 65, " sha256:" + "a" * 64,
            good + "\n", good + " ", good + ":extra",
            "sha512:" + "a" * 64, "sha256:" + "g" * 64,
            "\u0000" + good, good + "\u0000",
        ]
        for value in samples:
            with self.subTest(value=repr(value)):
                receipt = screen_digest(value)
                self.assertEqual(receipt["status"], "REJECTED")
                self.assertFalse(receipt["syntax_valid"])

    def test_no_input_grants_authority(self):
        for value in ("sha256:" + "0" * 64, "bad", None):
            with self.subTest(value=value):
                receipt = screen_digest(value)
                for field in ("deploy_authorized", "recovery_authorized",
                              "mutation_performed", "content_verified"):
                    self.assertIs(receipt[field], False)


if __name__ == "__main__":
    unittest.main()
