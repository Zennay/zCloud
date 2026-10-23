"""Regression contract for the retired 9-Oct owner-wide new-chat dispatcher.

This was a dated one-time incident action, not a reusable worker-control API.
Reintroducing it as a new GitHub Actions entrypoint could push new_chat to
*every* allocated worker without current lease, progress or idle evidence.
These tests only read repository files; they never contact the runner or VPS.
"""
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = "owner-one-shot-worker-restart-20261009.yml"
FINGERPRINTS = (
    "zcloud-owner-oneshot-restart/1",
    "OWNER_ONESHOT_WORKERS_RESTARTED_OR_SAFELY_DEFERRED=1",
    "zcloud-owner-worker-restart-20261009",
)
MAX_SOURCE_BYTES = 2 * 1024 * 1024


def retired_dispatchers(workflows):
    """Return paths with exact legacy signatures; refuse unknown file types."""
    hits = {}
    for path in sorted(workflows.iterdir()):
        if path.suffix.lower() not in {".yml", ".yaml"}:
            continue
        if path.is_symlink() or not path.is_file():
            hits[path.name] = ["untrusted_workflow_type"]
            continue
        try:
            before = path.stat()
            if before.st_size < 0 or before.st_size > MAX_SOURCE_BYTES:
                hits[path.name] = ["oversize"]
                continue
            source = path.read_text(encoding="utf-8")
            after = path.stat()
            if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (
                after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size
            ):
                hits[path.name] = ["changed_during_scan"]
                continue
        except (UnicodeError, OSError):
            hits[path.name] = ["unreadable"]
            continue
        matched = [marker for marker in FINGERPRINTS if marker in source]
        if matched:
            hits[path.name] = matched
    return hits


class RetiredOwnerOneshotRestartTests(unittest.TestCase):
    def test_original_one_shot_workflow_has_been_deleted(self):
        self.assertFalse((WORKFLOWS / RETIRED).exists())

    def test_renamed_clone_cannot_restore_dated_restart(self):
        self.assertEqual({}, retired_dispatchers(WORKFLOWS))

    def test_cloned_dispatcher_detected(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "different-restart.yml").write_text(
                "on: workflow_dispatch\n# zcloud-owner-oneshot-restart/1\n",
                encoding="utf-8",
            )
            self.assertIn("different-restart.yml", retired_dispatchers(Path(root)))

    def test_yaml_extension_variants_are_detected(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "recovery.yaml").write_text(
                "echo OWNER_ONESHOT_WORKERS_RESTARTED_OR_SAFELY_DEFERRED=1",
                encoding="utf-8",
            )
            self.assertIn("recovery.yaml", retired_dispatchers(Path(root)))

    def test_symlink_workflow_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "outside.txt"
            file.write_text("normal")
            (Path(root) / "linked.yml").symlink_to(file)
            self.assertEqual(
                ["untrusted_workflow_type"],
                retired_dispatchers(Path(root))["linked.yml"],
            )

    def test_oversize_workflow_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "oversize.yml"
            with file.open("wb") as out:
                out.truncate(MAX_SOURCE_BYTES + 1)
            self.assertEqual(["oversize"], retired_dispatchers(Path(root))[file.name])

    def test_other_wellformed_workflows_unaffected(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "safe.yml").write_text(
                "name: Ordinary read-only hosted probe\non: workflow_dispatch\n"
                "jobs:\n  safe:\n    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            self.assertEqual({}, retired_dispatchers(Path(root)))


if __name__ == "__main__":
    unittest.main()
