"""Keep the expired 7-Oct owner continuity watcher non-dispatchable.

It automatically consumed a self-hosted runner after a source-only push, polled
GET routes with possible SQLite write-side effects (#1341), logged raw worker
identities/control commands, and hardcoded exactly seven workers.
These are historical incident fingerprints, not current availability proof.
"""
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
RETIRED = "owner-worker-continuity-watch-20261007.yml"
MAX_BYTES = 2 * 1024 * 1024
MARKERS = (
    "name: Owner worker continuity watch 2026-10-07",
    "OWNER_WORKER_CONTINUITY_GREEN=1",
    "continuity failure: only",
    "owner-worker-continuity-watch-20261007",
    "CONTINUITY_BUSY_DEFERRED=",
)


def find_retired_watch(directory: Path) -> dict[str, list[str]]:
    found = {}
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in {".yml", ".yaml"}:
            continue
        if path.is_symlink() or not path.is_file():
            found[path.name] = ["untrusted_workflow"]
            continue
        try:
            before = path.stat()
            if not 0 <= before.st_size <= MAX_BYTES:
                found[path.name] = ["unbounded_workflow"]
                continue
            content = path.read_text(encoding="utf-8")
            after = path.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ):
                found[path.name] = ["workflow_changed_during_scan"]
                continue
        except (OSError, UnicodeError):
            found[path.name] = ["workflow_unavailable"]
            continue
        hits = [item for item in MARKERS if item in content]
        if hits:
            found[path.name] = hits
    return found


class RetiredOwnerContinuityWatchTests(unittest.TestCase):
    def test_outdated_seven_worker_entrypoint_removed(self):
        self.assertFalse((WORKFLOWS / RETIRED).exists())

    def test_renamed_legacy_watch_cannot_reappear(self):
        self.assertEqual({}, find_retired_watch(WORKFLOWS))

    def test_old_busy_deferred_watch_marker_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = Path(tmp) / "innocent.yaml"
            name.write_text('run: echo "CONTINUITY_BUSY_DEFERRED="\\n', encoding="utf-8")
            self.assertIn(name.name, find_retired_watch(Path(tmp)))

    def test_old_success_marker_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = Path(tmp) / "renamed.yml"
            name.write_text("run: echo OWNER_WORKER_CONTINUITY_GREEN=1\n",
                            encoding="utf-8")
            self.assertEqual(
                ["OWNER_WORKER_CONTINUITY_GREEN=1"],
                find_retired_watch(Path(tmp))[name.name],
            )

    def test_modern_hosted_non_mutating_continuity_proof_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = Path(tmp) / "worker-source-proof.yml"
            name.write_text(
                "name: Current worker source proof\non: pull_request\n"
                "permissions:\n  contents: read\njobs:\n  tests:\n"
                "    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            self.assertEqual({}, find_retired_watch(Path(tmp)))

    def test_symlinked_workflow_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside.txt"
            outside.write_text("safe", encoding="utf-8")
            (root / "alias.yml").symlink_to(outside)
            self.assertEqual(
                ["untrusted_workflow"],
                find_retired_watch(root)["alias.yml"],
            )

    def test_oversized_workflow_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "giant.yaml"
            with target.open("wb") as fp:
                fp.truncate(MAX_BYTES + 1)
            self.assertEqual(
                ["unbounded_workflow"],
                find_retired_watch(Path(tmp))[target.name],
            )


if __name__ == "__main__":
    unittest.main()
