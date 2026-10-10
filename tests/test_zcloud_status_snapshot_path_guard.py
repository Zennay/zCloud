"""No-VPS regression for status snapshot parent-link escapes."""
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_status_snapshot_path_guard import (
    UnsafeStatusPath,
    validate_status_snapshot_path,
)


class StatusSnapshotPathGuardTests(unittest.TestCase):
    def test_regular_snapshot_within_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "trusted"
            root.mkdir()
            snapshot = root / "status.json"
            snapshot.write_text("{}", encoding="utf-8")
            self.assertEqual(validate_status_snapshot_path(snapshot, root), snapshot)

    def test_parent_symlink_rejected_even_if_leaf_regular(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            trusted = base / "trusted"
            trusted.mkdir()
            outside = base / "outside"
            outside.mkdir()
            (outside / "status.json").write_text("{}", encoding="utf-8")
            (trusted / "alias").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(UnsafeStatusPath):
                validate_status_snapshot_path(trusted / "alias" / "status.json", trusted)

    def test_escape_and_leaf_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "trusted"
            root.mkdir()
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            (root / "link.json").symlink_to(target)
            for candidate in (root / "link.json", root / ".." / "other.json"):
                with self.subTest(candidate=candidate), self.assertRaises(UnsafeStatusPath):
                    validate_status_snapshot_path(candidate, root)

    def test_trusted_root_ancestor_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            physical = base / "physical"
            physical.mkdir()
            root = physical / "trusted"
            root.mkdir()
            snapshot = root / "status.json"
            snapshot.write_text("{}", encoding="utf-8")
            (base / "alias").symlink_to(physical, target_is_directory=True)
            with self.assertRaises(UnsafeStatusPath):
                validate_status_snapshot_path(base / "alias" / "trusted" / "status.json",
                                              base / "alias" / "trusted")

    def test_directory_and_outside_regular_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "trusted"
            root.mkdir()
            outside = Path(tmp) / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            for candidate in (root, outside):
                with self.subTest(candidate=candidate), self.assertRaises(UnsafeStatusPath):
                    validate_status_snapshot_path(candidate, root)

    def test_missing_and_relative_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for candidate in ("status.json", root / "missing.json"):
                with self.subTest(candidate=candidate), self.assertRaises(UnsafeStatusPath):
                    validate_status_snapshot_path(candidate, root)


if __name__ == "__main__":
    unittest.main()
