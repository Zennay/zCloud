"""Standalone negative tests; no runtime imports or mutations."""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bidi_identifier_reference",
    ROOT / "scripts" / "control_plane_bidi_identifier_offline_20261009_w1.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

class IdentifierTests(unittest.TestCase):
    def test_ascii_identity(self):
        for value in ("cloud", "worker-1", "cloud:worker_2", "A.0"):
            with self.subTest(value=value):
                self.assertTrue(module.is_safe_display_identifier(value))

    def test_invisible_and_directional(self):
        for char in ("\u200b", "\u200c", "\u200d", "\u202a", "\u202e",
                     "\u2066", "\u2069", "\ufeff", "\u00ad"):
            with self.subTest(codepoint=ord(char)):
                self.assertFalse(module.is_safe_display_identifier("worker"+char+"1"))

    def test_confusables_and_normalization(self):
        for value in ("wοrker", "ｗorker", "café", "e\u0301", "İ", "K"):
            with self.subTest(value=value):
                self.assertFalse(module.is_safe_display_identifier(value))

    def test_bounds_and_types(self):
        for value in ("", "-start", "."*2, "x"*129, "has space",
                      "line\nbreak", "\x00", None, 1, True, b"worker"):
            with self.subTest(value=repr(value)):
                self.assertFalse(module.is_safe_display_identifier(value))
        self.assertTrue(module.is_safe_display_identifier("x"*128))

    def test_unicode_formatting_families(self):
        # Exhaustively reject Unicode format codepoints regardless of display.
        import unicodedata
        for codepoint in range(0x110000):
            char = chr(codepoint)
            if unicodedata.category(char) == "Cf":
                with self.subTest(codepoint=codepoint):
                    self.assertFalse(module.is_safe_display_identifier("a" + char + "b"))

    def test_coercion_is_never_attempted(self):
        class Explosive:
            def __str__(self):
                raise AssertionError("untrusted object stringification")

            def __repr__(self):
                raise AssertionError("untrusted object representation")

        self.assertFalse(module.is_safe_display_identifier(Explosive()))
        self.assertFalse(module.is_safe_display_identifier(["worker"]))
        self.assertFalse(module.is_safe_display_identifier({"id": "worker"}))

    def test_ascii_punctuation_restrictions(self):
        for value in ("a/b", "a\\\\b", "a@b", "a#b", "a,b", "a=b",
                      "a;b", "a?b", "a%20b", "a\\tb", "a\\rb"):
            with self.subTest(value=value):
                self.assertFalse(module.is_safe_display_identifier(value))

if __name__ == "__main__":
    unittest.main()
