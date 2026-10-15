"""Real-Git adversarial regressions for the read-only artifact-retention debt ratchet."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/zcloud_artifact_retention_ratchet_20261015_w7.py"
WF = ".github/workflows/sample.yml"


def upload(retention: str | None, *, name: str = "evidence") -> str:
    extra = f"          retention-days: {retention}\n" if retention is not None else ""
    return (
        f"name: sample\non: push\njobs:\n  check:\n    runs-on: ubuntu-latest\n"
        f"    steps:\n      - name: {name}\n"
        "        uses: actions/upload-artifact@v4\n"
        "        with:\n          name: evidence\n"
        "          path: evidence.txt\n"
        + extra
    )


class ArtifactRetentionRatchetTests(unittest.TestCase):
    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, check=True, text=True,
            capture_output=True, timeout=12,
        )
        return result.stdout.strip()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")

    def write(self, content: str, path: str = WF) -> None:
        dest = self.repo / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    def commit(self) -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", "source")
        return self.git("rev-parse", "HEAD")

    def check(self, before: str | None, after: str | None, *,
              touch_other: bool = False, target: str = WF) -> tuple[int, dict]:
        if before is not None:
            self.write(before, target)
        else:
            self.write("name: no uploads\non: push\njobs: {}\n", "README.yml")
        base = self.commit()
        if after is None:
            (self.repo / target).unlink()
        else:
            self.write(after, target)
        if touch_other:
            self.write("changed\n", "notes.txt")
        head = self.commit()
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.repo),
             "--base", base, "--head", head], check=False,
            capture_output=True, text=True, timeout=15,
        )
        return completed.returncode, json.loads(completed.stdout)

    def test_new_bounded_upload_allowed(self):
        code, data = self.check(None, upload("14"))
        self.assertEqual((code, data["new_unsafe_steps"], data["mutation_performed"]), (0, 0, False))

    def test_missing_new_upload_denied(self):
        code, data = self.check(None, upload(None))
        self.assertEqual((code, data["new_unsafe_steps"]), (1, 1))

    def test_dynamic_retention_denied(self):
        code, data = self.check(None, upload("${{ inputs.days }}"))
        self.assertEqual((code, data["new_unsafe_steps"]), (1, 1))

    def test_zero_retention_denied(self):
        code, data = self.check(None, upload("0"))
        self.assertEqual((code, data["new_unsafe_steps"]), (1, 1))

    def test_31_day_retention_denied(self):
        code, data = self.check(None, upload("31"))
        self.assertEqual((code, data["new_unsafe_steps"]), (1, 1))

    def test_existing_unmodified_debt_is_grandfathered(self):
        before = upload(None)
        after = before + "\n# unrelated trailing comment\n"
        code, data = self.check(before, after)
        # Changing the old upload step itself is intentionally denied.
        self.assertEqual(code, 1)

    def test_untouched_legacy_step_with_new_valid_step(self):
        before = upload(None)
        after = before + (
            "      - name: second\n        uses: actions/upload-artifact@v4\n"
            "        with:\n          retention-days: 7\n"
        )
        code, data = self.check(before, after)
        self.assertEqual((code, data["new_unsafe_steps"], data["legacy_unsafe_steps_in_changed_files"]), (0, 0, 1))

    def test_corrected_legacy_step_allowed(self):
        code, data = self.check(upload(None), upload("7"))
        self.assertEqual((code, data["new_unsafe_steps"], data["unsafe_steps_after"]), (0, 0, 0))

    def test_unmodified_upload_when_unrelated_file_changes(self):
        code, data = self.check(upload(None), upload(None), touch_other=True)
        self.assertEqual((code, data["changed_workflows"]), (0, 0))

    def test_deleted_workflow_allowed(self):
        code, data = self.check(upload(None), None)
        self.assertEqual((code, data["changed_workflows"]), (0, 0))

    def test_ambiguous_comment_mentions_denied(self):
        code, data = self.check(None, upload("7") + "# actions/upload-artifact@v4\n")
        self.assertEqual(code, 1)
        self.assertEqual(data.get("reason"), "ambiguous_upload_source")

    def test_duplicate_retention_keys_are_denied(self):
        code, data = self.check(None, upload("7") + "          retention-days: 90\n")
        self.assertEqual((code, data["new_unsafe_steps"]), (1, 1))

    def test_retention_outside_with_block_is_denied(self):
        source = upload(None) + "        retention-days: 14\n"
        code, data = self.check(None, source)
        self.assertEqual((code, data["new_unsafe_steps"]), (1, 1))

    def test_false_base_commit_is_denied(self):
        self.write(upload("7"))
        self.commit()
        head = self.git("rev-parse", "HEAD")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--base", "bad", "--head", head,
             "--repo-root", str(self.repo)],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["reason"], "invalid_commit_identity")


if __name__ == "__main__":
    unittest.main()
