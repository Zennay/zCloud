import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_open_pr_overlap_audit.py"

sys.path.insert(0, str(ROOT / "scripts"))
import zcloud_open_pr_overlap_audit as audit  # noqa: E402


def row(number, branch, files, *, draft=False, updated="2026-10-07T02:00:00Z"):
    return {
        "number": number,
        "headRefName": branch,
        "isDraft": draft,
        "updatedAt": updated,
        "changedFiles": len(files),
        "files": [{"path": path} for path in files],
        # These fields must never leak into the report if a caller supplies them.
        "title": f"sensitive title {number}",
        "body": f"sensitive body {number}",
        "author": {"login": "private-author"},
    }


class OpenPrOverlapAuditTests(unittest.TestCase):
    def test_current_pr_reports_exact_overlap_only(self):
        prs = audit.parse_snapshot(
            [
                row(10, "feature/current", ["scripts/a.py", "tests/a.py"]),
                row(11, "feature/peer", ["tests/a.py", "docs/peer.md"]),
                row(12, "feature/unrelated", ["server.py"]),
            ]
        )

        report = audit.overlap_report(prs, current_pr=10)

        self.assertFalse(report["clear"])
        self.assertEqual(report["current_pr"], 10)
        self.assertEqual(report["conflicting_pr_numbers"], [11])
        self.assertEqual(
            report["conflicts"],
            [{"path": "tests/a.py", "pr_numbers": [11]}],
        )
        encoded = json.dumps(report)
        self.assertNotIn("sensitive title", encoded)
        self.assertNotIn("sensitive body", encoded)
        self.assertNotIn("private-author", encoded)

    def test_draft_prs_still_own_their_changed_paths(self):
        prs = audit.parse_snapshot(
            [
                row(20, "feature/current", ["scripts/shared.py"]),
                row(21, "draft/owner", ["scripts/shared.py"], draft=True),
            ]
        )

        report = audit.overlap_report(prs, current_pr=20)

        self.assertEqual(report["conflicting_pr_numbers"], [21])
        self.assertEqual(report["conflict_count"], 1)

    def test_global_report_groups_all_path_owners(self):
        prs = audit.parse_snapshot(
            [
                row(1, "a", ["server.py"]),
                row(2, "b", ["server.py"]),
                row(3, "c", ["server.py"]),
            ]
        )

        report = audit.overlap_report(prs)

        self.assertEqual(
            report["conflicts"],
            [{"path": "server.py", "pr_numbers": [1, 2, 3]}],
        )

    def test_missing_current_pr_fails_closed(self):
        prs = audit.parse_snapshot([row(1, "a", ["server.py"])])
        with self.assertRaises(audit.AuditError):
            audit.overlap_report(prs, current_pr=99)

    def test_truncated_file_evidence_is_rejected(self):
        item = row(6, "a", ["a.py"])
        item["changedFiles"] = 2
        with self.assertRaises(audit.AuditError):
            audit.parse_snapshot([item])

    def test_duplicate_pr_number_is_rejected(self):
        with self.assertRaises(audit.AuditError):
            audit.parse_snapshot(
                [
                    row(7, "a", ["a.py"]),
                    row(7, "b", ["b.py"]),
                ]
            )

    def test_duplicate_path_inside_pr_is_rejected(self):
        with self.assertRaises(audit.AuditError):
            audit.parse_snapshot([row(8, "a", ["a.py", "a.py"])])

    def test_unsafe_paths_are_rejected(self):
        for bad in ("/etc/passwd", "../server.py", "a/../server.py", "a//b.py", "a\\b.py"):
            with self.subTest(path=bad):
                with self.assertRaises(audit.AuditError):
                    audit.parse_snapshot([row(9, "a", [bad])])

    def test_symlink_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "snapshot.json"
            link = root / "link.json"
            target.write_text("[]", encoding="utf-8")
            link.symlink_to(target)
            with self.assertRaises(audit.AuditError):
                audit.load_snapshot(link)

    def test_require_clear_exit_codes_distinguish_conflict_and_bad_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conflict = root / "conflict.json"
            conflict.write_text(
                json.dumps(
                    [
                        row(30, "current", ["shared.py"]),
                        row(31, "peer", ["shared.py"]),
                    ]
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(conflict),
                    "--current-pr",
                    "30",
                    "--require-clear",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["conflicting_pr_numbers"], [31])

            malformed = root / "malformed.json"
            malformed.write_text("{}", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(malformed)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertIn("error", payload)

    def test_clean_cli_is_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(
                json.dumps(
                    [
                        row(40, "current", ["scripts/current.py"]),
                        row(41, "peer", ["scripts/peer.py"]),
                    ]
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(snapshot),
                    "--current-pr",
                    "40",
                    "--require-clear",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["clear"])
            self.assertEqual(payload["conflict_count"], 0)


if __name__ == "__main__":
    unittest.main()
