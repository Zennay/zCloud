from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_deploy_freshness_report as report


class DeployFreshnessReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "zcloud-tests@example.invalid")
        self.git("config", "user.name", "zCloud Tests")
        (self.repo / "server.py").write_text("print('baseline')\n", encoding="utf-8")
        self.git("add", "server.py")
        self.git("commit", "-m", "baseline")
        self.deployed_sha = self.head()
        self.state = self.root / "state"
        self.write_lkg(self.deployed_sha)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            text=True,
            capture_output=True,
        )
        return completed.stdout.strip()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def commit_file(self, path: str, content: str, message: str) -> str:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-m", message)
        return self.head()

    def write_lkg(self, sha: str, *, evidence: str = "POSTDEPLOY_GREEN tx=test"):
        snapshot_id = "20261006T220000Z-test"
        snapshot = self.state / "snapshots" / snapshot_id
        snapshot.mkdir(parents=True, exist_ok=True)
        (snapshot / "manifest.json").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "snapshot_id": snapshot_id,
                    "evidence": evidence,
                    "git": {"head": sha},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (self.state / "last-known-good.json").write_text(
            json.dumps(
                {
                    "snapshot_id": snapshot_id,
                    "updated_at": "2026-10-06T22:00:00+00:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def build(self, green_sha: str, current_sha: str | None = None) -> dict:
        return report.build_report(
            repo=self.repo,
            state=self.state,
            green_sha=green_sha,
            current_sha=current_sha,
        )

    def test_exact_green_reports_last_deploy_and_no_validation_gap(self):
        result = self.build(self.deployed_sha)

        self.assertEqual("exact_green", result["validation"]["status"])
        self.assertFalse(result["validation"]["needs_validation"])
        self.assertEqual(self.deployed_sha, result["last_green_deploy"]["commit_sha"])
        self.assertTrue(result["last_green_deploy"]["postdeploy_green"])
        self.assertTrue(result["source"]["matches_deployed_sha"])
        self.assertEqual(0, result["source"]["commits_ahead_of_deploy"])

    def test_docs_only_commit_is_runtime_equivalent_to_green_ancestor(self):
        current = self.commit_file(
            "docs/operator-note.md",
            "documentation only\n",
            "docs only",
        )

        result = self.build(self.deployed_sha, current)

        self.assertEqual("runtime_equivalent", result["validation"]["status"])
        self.assertFalse(result["validation"]["needs_validation"])
        self.assertEqual([], result["validation"]["runtime_changed_paths"])
        self.assertEqual(1, result["source"]["commits_ahead_of_deploy"])

    def test_runtime_change_after_green_is_explicitly_unvalidated(self):
        current = self.commit_file(
            "server.py",
            "print('changed runtime')\n",
            "runtime change",
        )

        result = self.build(self.deployed_sha, current)

        self.assertEqual(
            "runtime_changes_unvalidated",
            result["validation"]["status"],
        )
        self.assertTrue(result["validation"]["needs_validation"])
        self.assertEqual(["server.py"], result["validation"]["runtime_changed_paths"])
        self.assertEqual(1, result["validation"]["runtime_changed_count"])

    def test_current_commit_behind_green_evidence_is_not_claimed_valid(self):
        green = self.commit_file(
            "docs/validated-later.md",
            "later validated commit\n",
            "later green",
        )

        result = self.build(green, self.deployed_sha)

        self.assertEqual("current_behind_green", result["validation"]["status"])
        self.assertTrue(result["validation"]["needs_validation"])

    def test_diverged_green_evidence_fails_closed_to_needs_validation(self):
        self.git("checkout", "-b", "green-branch")
        green = self.commit_file("docs/green.md", "green\n", "green branch")
        self.git("checkout", "main")
        current = self.commit_file("docs/current.md", "current\n", "current branch")

        result = self.build(green, current)

        self.assertEqual("diverged_from_green", result["validation"]["status"])
        self.assertTrue(result["validation"]["needs_validation"])

    def test_lkg_without_postdeploy_green_evidence_is_rejected(self):
        self.write_lkg(self.deployed_sha, evidence="HEALTH_GREEN only")

        with self.assertRaisesRegex(
            report.DeployFreshnessError,
            "POSTDEPLOY_GREEN",
        ):
            self.build(self.deployed_sha)

    def test_symlink_lkg_pointer_is_rejected(self):
        real_pointer = self.state / "real-pointer.json"
        real_pointer.write_text(
            (self.state / "last-known-good.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (self.state / "last-known-good.json").unlink()
        (self.state / "last-known-good.json").symlink_to(real_pointer)

        with self.assertRaisesRegex(
            report.DeployFreshnessError,
            "regular non-symlink",
        ):
            self.build(self.deployed_sha)

    def test_unknown_current_commit_is_rejected(self):
        with self.assertRaisesRegex(
            report.DeployFreshnessError,
            "not available as a commit",
        ):
            self.build(self.deployed_sha, "f" * 40)


if __name__ == "__main__":
    unittest.main()
