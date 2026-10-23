"""Retired 13-Oct pinned UI publisher must not regain cancellation authority.

The published blob fingerprints no longer match current main. This one-night
workflow also held actions:write and performed cancellations of historical run
IDs before attempting to release pinned static assets. A normal UI pipeline
must be source/approval-bound, not resurrect this obsolete workflow.
"""
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
RETIRED = "zcloud-ui-publish-20261013.yml"
MAX_BYTES = 2 * 1024 * 1024
MARKERS = (
    "zCloud UI-only guarded publish 2026-10-13",
    "UI_PRIORITY_PINNED_ASSETS_VERIFIED=1",
    "UI_PUBLISH_VERIFIED=1 source_sha=",
    "publish-dashboard-ui:",
    "38322783586|zCloud UI-only guarded publish 2026-10-13",
)


def scan_retired_ui_publisher(directory: Path) -> dict[str, list[str]]:
    problems = {}
    for entry in sorted(directory.iterdir()):
        if entry.suffix.lower() not in {".yml", ".yaml"}:
            continue
        if entry.is_symlink() or not entry.is_file():
            problems[entry.name] = ["untrusted_workflow_type"]
            continue
        try:
            before = entry.stat()
            if before.st_size > MAX_BYTES:
                problems[entry.name] = ["oversized_workflow"]
                continue
            data = entry.read_text(encoding="utf-8")
            after = entry.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ):
                problems[entry.name] = ["raced_workflow_source"]
                continue
        except (OSError, UnicodeError):
            problems[entry.name] = ["workflow_unreadable"]
            continue
        hits = [marker for marker in MARKERS if marker in data]
        if hits:
            problems[entry.name] = hits
    return problems


class RetiredStaticUiPublisherTests(unittest.TestCase):
    def test_retired_one_night_ui_publish_not_dispatchable(self):
        self.assertFalse((WORKFLOWS / RETIRED).exists())

    def test_historical_actions_write_canceller_fingerprints_gone(self):
        self.assertEqual({}, scan_retired_ui_publisher(WORKFLOWS))

    def test_yaml_name_change_still_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "innocuous.yaml"
            path.write_text(
                "name: test\non: workflow_dispatch\n"
                "jobs:\n  publish-dashboard-ui:\n    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            self.assertEqual(
                ["publish-dashboard-ui:"], scan_retired_ui_publisher(Path(tmp))[path.name]
            )

    def test_old_cancellation_identity_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old-cancel.yml"
            path.write_text(
                "run: echo 38322783586|zCloud UI-only guarded publish 2026-10-13\n",
                encoding="utf-8",
            )
            self.assertIn(path.name, scan_retired_ui_publisher(Path(tmp)))

    def test_legitimate_new_ui_source_proof_not_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "safe-ui-source.yml"
            path.write_text(
                "name: static UI source proof\non: pull_request\n"
                "permissions:\n  contents: read\n"
                "jobs:\n  validate:\n    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            self.assertEqual({}, scan_retired_ui_publisher(Path(tmp)))

    def test_symlinked_workflow_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            other = root / "external.txt"
            other.write_text("safe", encoding="utf-8")
            (root / "untrusted.yml").symlink_to(other)
            self.assertEqual(
                ["untrusted_workflow_type"],
                scan_retired_ui_publisher(root)["untrusted.yml"],
            )

    def test_oversized_workflow_fail_closed_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.yml"
            with path.open("wb") as stream:
                stream.truncate(MAX_BYTES + 1)
            self.assertEqual(
                ["oversized_workflow"], scan_retired_ui_publisher(Path(tmp))[path.name]
            )


if __name__ == "__main__":
    unittest.main()
