"""Bounded command-line zSentinel evidence ingestion, including hostile filesystem inputs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "zsentinel_diagnose.py"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def command(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), str(path)], capture_output=True,
        text=True, timeout=12, check=False,
    )


class ZSentinelCliEvidenceSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zsentinel-evidence-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def assert_rejected(self, path: Path) -> None:
        result = command(path)
        self.assertEqual(2, result.returncode, (result.stdout, result.stderr))
        self.assertIn("zSentinel evidence rejected:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual("", result.stdout)

    def test_valid_single_project_report_is_read_only(self):
        path = self.root / "safe.json"
        path.write_text(json.dumps({
            "schema_version": 1, "observed_at": utc(),
            "projects": [{"id": "cloud", "notion": {"posture": "Paused"}}],
        }), encoding="utf-8")
        result = command(path)
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual("intentional_wait", receipt["projects"][0]["state"])
        self.assertFalse(receipt["deploy_authorized"])
        self.assertFalse(receipt["mutations_performed"])
        self.assertEqual([], receipt["incidents"])

    def test_missing_file_fails_without_python_traceback(self):
        self.assert_rejected(self.root / "missing.json")

    def test_directory_refused_as_nonregular_input(self):
        self.assert_rejected(self.root)

    def test_symlink_not_followed_even_when_target_valid(self):
        target = self.root / "target.json"
        target.write_text("{}", encoding="utf-8")
        link = self.root / "danger.json"
        link.symlink_to(target)
        self.assert_rejected(link)

    def test_oversized_file_refused_before_parsing(self):
        path = self.root / "too-big.json"
        path.write_bytes(b"x" * 1_000_001)
        self.assert_rejected(path)

    def test_invalid_utf8_rejected_with_bounded_error(self):
        path = self.root / "bad-utf8.json"
        path.write_bytes(b"\xff\xfe\xfd")
        self.assert_rejected(path)

    def test_invalid_json_exits_two(self):
        path = self.root / "malformed.json"
        path.write_text("{", encoding="utf-8")
        self.assert_rejected(path)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX mkfifo required")
    def test_fifo_rejected_without_blocking(self):
        path = self.root / "pipe.json"
        os.mkfifo(path)
        self.assert_rejected(path)

    def test_oversized_at_boundary_is_still_parse_checked(self):
        path = self.root / "boundary.json"
        path.write_bytes(b" " * 1_000_000)
        self.assert_rejected(path)


if __name__ == "__main__":
    unittest.main()
