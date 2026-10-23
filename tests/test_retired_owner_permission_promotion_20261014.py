"""Keep dated 7-Oct privileged owner permission promotion/cancel jobs retired.

These source-only tests prevent an old checkout-and-promote job or a run-cancel
worker from being resurrected after its single-use incident window.
"""
from pathlib import Path
import tempfile
import unittest

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
OLD_NAMES = (
    "owner-promote-worker-permission-20261007.yml",
    "owner-cancel-stale-promotion-20261007.yml",
)
PROTECTED_FINGERPRINTS = (
    "owner-worker-recovery-20261007",
    "zcloud-owner-stale-promotion-cancel/1",
    "owner-promote-worker-permission-20261007.yml",
    "STALE_RUN_ID: \"37562484060\"",
)
MAX_SOURCE_BYTES = 2 * 1024 * 1024


def offenders(root):
    matches = {}
    for path in sorted(root.iterdir()):
        if path.suffix.lower() not in {".yml", ".yaml"}:
            continue
        if path.is_symlink() or not path.is_file():
            matches[path.name] = "unsafe_file_type"
            continue
        try:
            before = path.stat()
            if before.st_size > MAX_SOURCE_BYTES:
                matches[path.name] = "oversized"
                continue
            value = path.read_text(encoding="utf-8")
            after = path.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ):
                matches[path.name] = "unstable_read"
                continue
        except (UnicodeError, OSError):
            matches[path.name] = "unreadable"
            continue
        for signature in PROTECTED_FINGERPRINTS:
            if signature in value:
                matches[path.name] = "legacy_owner_promotion"
                break
    return matches


class RetiredOwnerPromotionTests(unittest.TestCase):
    def test_dated_entrypoints_absent(self):
        for name in OLD_NAMES:
            with self.subTest(workflow=name):
                self.assertFalse((WORKFLOWS / name).exists())

    def test_old_promote_and_cancel_signatures_absent_from_all_workflows(self):
        self.assertEqual({}, offenders(WORKFLOWS))

    def test_renamed_promoter_still_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "renamed.yaml").write_text(
                "on: push\nrun: python3 scripts/zcloud_transactional_promote.py "
                "--actor owner-worker-recovery-20261007\n", encoding="utf-8")
            self.assertEqual(
                "legacy_owner_promotion", offenders(Path(directory))["renamed.yaml"]
            )

    def test_renamed_cancel_still_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "new.yml").write_text(
                "run: echo zcloud-owner-stale-promotion-cancel/1\n",
                encoding="utf-8",
            )
            self.assertEqual(
                "legacy_owner_promotion", offenders(Path(directory))["new.yml"]
            )

    def test_unrelated_bounded_hosted_proof_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "safe.yml").write_text(
                "name: hosted permission verification\n"
                "on: pull_request\njobs:\n  check:\n    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            self.assertEqual({}, offenders(Path(directory)))

    def test_symlinks_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "source.txt"
            target.write_text("benign", encoding="utf-8")
            (Path(directory) / "linked.yml").symlink_to(target)
            self.assertEqual(
                "unsafe_file_type", offenders(Path(directory))["linked.yml"]
            )


if __name__ == "__main__":
    unittest.main()
