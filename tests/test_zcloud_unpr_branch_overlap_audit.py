import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_unpr_branch_overlap_audit as audit


ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 10, 7, 7, 40, tzinfo=dt.timezone.utc)


def snapshot(branches, **overrides):
    data = {
        "captured_at": "2026-10-07T07:39:30Z",
        "inventory_complete": True,
        "base_branch": "main",
        "branches": branches,
    }
    data.update(overrides)
    return data


class UnprBranchOverlapAuditTests(unittest.TestCase):
    def test_detects_overlap_only_for_branch_without_open_pr(self):
        payload = snapshot(
            [
                {
                    "name": "feature/existing-unpr",
                    "has_open_pr": False,
                    "changed_files_complete": True,
                    "changed_files": ["scripts/example.py", "docs/other.md"],
                },
                {
                    "name": "feature/already-pr-owned",
                    "has_open_pr": True,
                    "changed_files_complete": True,
                    "changed_files": ["tests/new_test.py"],
                },
            ]
        )
        result = audit.audit(payload, ["scripts/example.py", "tests/new_test.py"], NOW)
        self.assertEqual("overlap", result["status"])
        self.assertEqual(1, result["unpr_branch_count"])
        self.assertEqual(
            [{"branch": "feature/existing-unpr", "paths": ["scripts/example.py"]}],
            result["overlaps"],
        )
        self.assertFalse(result["mutation_performed"])

    def test_overlap_output_is_deterministic(self):
        payload = snapshot(
            [
                {
                    "name": "ops/z-last",
                    "has_open_pr": False,
                    "changed_files_complete": True,
                    "changed_files": ["scripts/shared.py"],
                },
                {
                    "name": "ops/a-first",
                    "has_open_pr": False,
                    "changed_files_complete": True,
                    "changed_files": ["scripts/shared.py"],
                },
            ]
        )
        result = audit.audit(payload, ["scripts/shared.py"], NOW)
        self.assertEqual(
            ["ops/a-first", "ops/z-last"],
            [row["branch"] for row in result["overlaps"]],
        )

    def test_clear_when_unpr_branches_are_file_disjoint(self):
        payload = snapshot(
            [
                {
                    "name": "ops/other-slice",
                    "has_open_pr": False,
                    "changed_files_complete": True,
                    "changed_files": ["docs/other.md"],
                }
            ]
        )
        result = audit.audit(payload, ["scripts/new.py"], NOW)
        self.assertEqual("clear", result["status"])
        self.assertEqual(0, result["overlap_count"])

    def test_fails_closed_on_incomplete_branch_file_inventory(self):
        payload = snapshot(
            [
                {
                    "name": "feature/truncated",
                    "has_open_pr": False,
                    "changed_files_complete": False,
                    "changed_files": [],
                }
            ]
        )
        with self.assertRaisesRegex(audit.AuditError, "changed_files_incomplete"):
            audit.audit(payload, ["scripts/new.py"], NOW)

    def test_fails_closed_on_stale_or_incomplete_global_inventory(self):
        with self.assertRaisesRegex(audit.AuditError, "inventory_incomplete"):
            audit.audit(snapshot([], inventory_complete=False), ["scripts/new.py"], NOW)
        stale = snapshot([], captured_at="2026-10-07T07:00:00Z")
        with self.assertRaisesRegex(audit.AuditError, "snapshot_stale"):
            audit.audit(stale, ["scripts/new.py"], NOW)

    def test_rejects_unsafe_paths_and_duplicate_branches(self):
        with self.assertRaisesRegex(audit.AuditError, "path_invalid"):
            audit.audit(snapshot([]), ["../escape"], NOW)
        duplicate = snapshot(
            [
                {"name": "ops/x", "has_open_pr": False, "changed_files_complete": True, "changed_files": []},
                {"name": "ops/x", "has_open_pr": False, "changed_files_complete": True, "changed_files": []},
            ]
        )
        with self.assertRaisesRegex(audit.AuditError, "branch_duplicate"):
            audit.audit(duplicate, ["scripts/new.py"], NOW)

    def test_rejects_control_characters_in_paths_and_branches(self):
        with self.assertRaisesRegex(audit.AuditError, "path_invalid"):
            audit.audit(snapshot([]), ["scripts/bad\nname.py"], NOW)
        bad_branch = snapshot(
            [
                {
                    "name": "ops/bad\tname",
                    "has_open_pr": False,
                    "changed_files_complete": True,
                    "changed_files": [],
                }
            ]
        )
        with self.assertRaisesRegex(audit.AuditError, "branch_invalid"):
            audit.audit(bad_branch, ["scripts/new.py"], NOW)

    def test_cli_require_clear_returns_two_for_overlap(self):
        payload = snapshot(
            [
                {
                    "name": "feature/existing-unpr",
                    "has_open_pr": False,
                    "changed_files_complete": True,
                    "changed_files": ["scripts/example.py"],
                }
            ],
            captured_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            code = audit.main(
                [
                    "--snapshot",
                    str(path),
                    "--candidate-path",
                    "scripts/example.py",
                    "--require-clear",
                ]
            )
        self.assertEqual(2, code)

    def test_self_hosted_proof_is_exact_head_read_only_and_guarded(self):
        text = (ROOT / ".github/workflows/zcloud-unpr-branch-overlap-audit.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn(
            "github.actor == 'Zennay' && github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("actions: write", text)


if __name__ == "__main__":
    unittest.main()
