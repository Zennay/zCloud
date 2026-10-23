"""Prevent the retired 7-Oct HaxLab lockfile generator from returning.

The workflow ran on zCloud's production VPS solely to install a transient
package-lock-only dependency and print its base64 bytes into Actions logs.
It never committed the resulting lockfile to HaxLab. Retirement is limited
to this historic, workflow-file-triggered entrypoint: current HaxLab builds
and zCloud deployment/runner workflows are not altered.
"""
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = "haxlab-node-lockfile-generator-20261007.yml"
MAX_BYTES = 2 * 1024 * 1024
FINGERPRINTS = (
    "HaxLab Node lockfile generator 2026-10-07",
    "HAXLAB_PACKAGE_LOCK_BASE64=",
    "Generate canonical HaxLab package lock",
    "haxlab-node-lockfile-generator-20261007.yml",
    '"name": "haxlab-replay-engine"',
)


def find_retired_generator(directory: Path) -> dict[str, list[str]]:
    """Scan Actions workflow entrypoints, including renamed legacy copies."""
    hits: dict[str, list[str]] = {}
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in {".yml", ".yaml"}:
            continue
        if path.is_symlink() or not path.is_file():
            hits[path.name] = ["untrusted_workflow"]
            continue
        try:
            before = path.stat()
            if not 0 <= before.st_size <= MAX_BYTES:
                hits[path.name] = ["unbounded_workflow"]
                continue
            text = path.read_text(encoding="utf-8")
            after = path.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ):
                hits[path.name] = ["workflow_changed_during_scan"]
                continue
        except (OSError, UnicodeError):
            hits[path.name] = ["workflow_unavailable"]
            continue
        matched = [fingerprint for fingerprint in FINGERPRINTS if fingerprint in text]
        if matched:
            hits[path.name] = matched
    return hits


class RetiredHaxLabLockfileGeneratorTests(unittest.TestCase):
    def test_old_self_hosted_generator_removed(self):
        self.assertFalse((WORKFLOWS / RETIRED).exists())

    def test_renamed_workflow_cannot_resurrect_generator(self):
        self.assertEqual({}, find_retired_generator(WORKFLOWS))

    def test_output_of_package_lock_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "renamed.yaml").write_text(
                'run: echo "HAXLAB_PACKAGE_LOCK_BASE64="\n', encoding="utf-8"
            )
            self.assertIn("renamed.yaml", find_retired_generator(Path(tmp)))

    def test_old_command_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "another.yml").write_text(
                'name: Generate canonical HaxLab package lock\n', encoding="utf-8"
            )
            self.assertIn("another.yml", find_retired_generator(Path(tmp)))

    def test_old_file_name_as_trigger_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "new-name.yml").write_text(
                'paths: [".github/workflows/haxlab-node-lockfile-generator-20261007.yml"]\n',
                encoding="utf-8",
            )
            self.assertIn("new-name.yml", find_retired_generator(Path(tmp)))

    def test_current_hosted_offline_haxlab_source_test_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "haxlab-validation.yaml").write_text(
                "name: HaxLab offline source test\non: pull_request\n"
                "permissions:\n  contents: read\njobs:\n  check:\n"
                "    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            self.assertEqual({}, find_retired_generator(Path(tmp)))

    def test_symlinked_workflow_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "external.txt").write_text("safe\n", encoding="utf-8")
            (root / "alias.yml").symlink_to(root / "external.txt")
            self.assertEqual(
                ["untrusted_workflow"], find_retired_generator(root)["alias.yml"]
            )

    def test_oversized_workflow_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.yaml"
            with path.open("wb") as f:
                f.truncate(MAX_BYTES + 1)
            self.assertEqual(
                ["unbounded_workflow"], find_retired_generator(Path(tmp))["large.yaml"]
            )

    def test_invalid_utf8_workflow_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bad.yml").write_bytes(b"\xff\xfe")
            self.assertEqual(
                ["workflow_unavailable"], find_retired_generator(Path(tmp))["bad.yml"]
            )


if __name__ == "__main__":
    unittest.main()
