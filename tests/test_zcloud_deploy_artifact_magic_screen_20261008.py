"""Non-authorizing regressions for offline artifact signature triage."""
import importlib.util
from pathlib import Path
import unittest

SRC = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_deploy_artifact_magic_screen_20261008.py"
spec = importlib.util.spec_from_file_location("artifact_magic_screen", SRC)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class ArtifactMagicTests(unittest.TestCase):
    def test_common_signatures_never_authorize(self):
        for raw in (b"PK\x03\x04", bytes.fromhex("1f8b08"), bytes.fromhex("28b52ffd"),
                    bytes.fromhex("fd377a585a00"), b"BZh"):
            with self.subTest(raw=raw):
                result = mod.screen(raw.hex())
                self.assertEqual(result["classification"], "SIGNATURE_ONLY_UNVERIFIED")
                self.assertFalse(any(result[k] for k in ("authorized", "may_extract", "may_deploy", "may_recover")))
    def test_tar_offset_not_proof(self):
        raw = bytearray(262)
        raw[257:262] = b"ustar"
        self.assertEqual(mod.screen(raw.hex())["reason"], "tar_ustar")
        self.assertFalse(mod.screen(raw.hex())["authorized"])
    def test_bad_or_unknown_always_denies(self):
        for value in ("", "abc", "gg", "0" * 1026, None, "../tmp", "00", "504b"):
            with self.subTest(value=value):
                result = mod.screen(value)
                self.assertFalse(result["authorized"])
                self.assertFalse(result["may_extract"])
                self.assertFalse(result["may_deploy"])
                self.assertFalse(result["may_recover"])

if __name__ == "__main__":
    unittest.main()
