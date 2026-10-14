"""Prevent resurrection of the expired 13-Oct UI Actions-write canceler.

The historical queue helper used hardcoded old UI artifact/blob/run SHAs and
GitHub Actions write permission to cancel pending runs. This is a one-shot
release coordination step, not a reusable deployment control entrypoint.
All checks are source-only; no API/runner/service/SQLite requests are made.
"""
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
EXPIRED = "zcloud-ui-priority-queue-20261013.yml"
FINGERPRINTS = (
    "PRIORITY_UI_KEEP_RUN=",
    "PRIORITY_UI_CANCEL_SUPERSEDED=",
    "PRIORITY_UI_QUEUE_FREED=",
    "free-ui-deploy-slot:",
)
LIMIT_BYTES = 2 * 1024 * 1024


def retired_queue_canceler_signatures(directory: Path) -> dict[str, list[str]]:
    """Inspect actual YAML files without following symlinks or reading secrets."""
    violations = {}
    for file in sorted(directory.iterdir()):
        if file.suffix.lower() not in {".yml", ".yaml"}:
            continue
        if file.is_symlink() or not file.is_file():
            violations[file.name] = ["untrusted_file_type"]
            continue
        try:
            before = file.stat()
            if before.st_size < 0 or before.st_size > LIMIT_BYTES:
                violations[file.name] = ["unbounded_file"]
                continue
            data = file.read_text(encoding="utf-8")
            after = file.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ):
                violations[file.name] = ["source_changed_during_scan"]
                continue
        except (OSError, UnicodeError):
            violations[file.name] = ["source_unavailable"]
            continue
        hits = [fragment for fragment in FINGERPRINTS if fragment in data]
        if hits:
            violations[file.name] = hits
    return violations


class RetiredUiQueueCancelerTests(unittest.TestCase):
    def test_original_actions_write_entrypoint_is_gone(self):
        self.assertFalse((WORKFLOWS / EXPIRED).exists())

    def test_a_renamed_copy_cannot_reappear_in_workflows(self):
        self.assertEqual({}, retired_queue_canceler_signatures(WORKFLOWS))

    def test_renamed_yaml_clone_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "renamed.yaml"
            file.write_text(
                "name: another release\npermissions:\n  actions: write\n"
                "jobs:\n  free-ui-deploy-slot:\n    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            self.assertIn("renamed.yaml", retired_queue_canceler_signatures(Path(tmp)))

    def test_source_clone_marker_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "canceler.yml"
            file.write_text("run: echo PRIORITY_UI_CANCEL_SUPERSEDED=$id\n", encoding="utf-8")
            self.assertEqual(
                ["PRIORITY_UI_CANCEL_SUPERSEDED="],
                retired_queue_canceler_signatures(Path(tmp))[file.name],
            )

    def test_safe_independent_owner_release_not_banned(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "guarded-ui-publish.yml"
            file.write_text(
                "name: Owner approved UI release\non: workflow_dispatch\n"
                "permissions:\n  contents: read\n"
                "jobs:\n  verify:\n    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            self.assertEqual({}, retired_queue_canceler_signatures(Path(tmp)))

    def test_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "outside"
            destination.write_text("none", encoding="utf-8")
            (Path(tmp) / "danger.yml").symlink_to(destination)
            self.assertEqual(
                ["untrusted_file_type"],
                retired_queue_canceler_signatures(Path(tmp))["danger.yml"],
            )

    def test_oversized_workflow_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "oversized.yml"
            with file.open("wb") as handle:
                handle.truncate(LIMIT_BYTES + 1)
            self.assertEqual(
                ["unbounded_file"], retired_queue_canceler_signatures(Path(tmp))[file.name]
            )


if __name__ == "__main__":
    unittest.main()
