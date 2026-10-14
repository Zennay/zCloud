import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_deploy_evidence_strict_json_20261008 import (
    EvidenceFormatError, load_strict_evidence, read_strict_evidence,
)


class StrictEvidenceTests(unittest.TestCase):
    def test_valid_nested_document(self):
        self.assertEqual(load_strict_evidence('{"gate":{"ok":false},"sha":"abc"}'),
                         {"gate": {"ok": False}, "sha": "abc"})

    def test_duplicate_root_key_denied(self):
        with self.assertRaises(EvidenceFormatError):
            load_strict_evidence('{"released":false,"released":true}')

    def test_duplicate_nested_key_denied(self):
        with self.assertRaises(EvidenceFormatError):
            load_strict_evidence('{"gate":{"released":false,"released":true}}')

    def test_nonfinite_numbers_denied(self):
        for raw in ('{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(EvidenceFormatError):
                load_strict_evidence(raw)

    def test_non_object_root_denied(self):
        with self.assertRaises(EvidenceFormatError):
            load_strict_evidence('[{"released":true}]')

    def test_trailing_document_denied(self):
        with self.assertRaises(EvidenceFormatError):
            load_strict_evidence('{"released":true} {"released":false}')

    def test_symlink_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / "real.json").write_text('{"ok":true}')
            (p / "alias.json").symlink_to(p / "real.json")
            with self.assertRaises(EvidenceFormatError):
                read_strict_evidence(str(p / "alias.json"))

    def test_oversized_file_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.json"
            path.write_text('{"padding":"' + "x" * 128 + '"}')
            with self.assertRaises(EvidenceFormatError):
                read_strict_evidence(str(path), max_bytes=64)


if __name__ == "__main__":
    unittest.main()
