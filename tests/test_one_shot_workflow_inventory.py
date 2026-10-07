import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_one_shot_workflow_inventory import (
    InventoryError,
    MAX_WORKFLOW_BYTES,
    inventory,
)


class OneShotWorkflowInventoryTests(unittest.TestCase):
    def make_root(self, temp: str) -> Path:
        root = Path(temp) / ".github" / "workflows"
        root.mkdir(parents=True)
        return root

    def write(self, root: Path, name: str, text: str) -> Path:
        path = root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_conservative_filename_markers_and_risk_hints(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            self.write(
                root,
                "ftmo-pr521-runner-recovery.yml",
                """name: recovery
on:
  workflow_dispatch:
permissions:
  contents: write
jobs:
  recover:
    runs-on: [self-hosted, zcloud, vps]
    steps:
      - run: sudo -n systemctl restart example.service
""",
            )
            self.write(
                root,
                "zcloud-regression-smoke.yml",
                """name: evergreen
on:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
""",
            )

            report = inventory(root)
            self.assertEqual(report["workflows_scanned"], 2)
            self.assertEqual(report["legacy_candidates"], 1)
            candidate = report["candidates"][0]
            self.assertEqual(
                candidate["workflow"],
                ".github/workflows/ftmo-pr521-runner-recovery.yml",
            )
            self.assertEqual(candidate["markers"], ["pr_scoped_filename"])
            self.assertEqual(candidate["triggers"], ["workflow_dispatch"])
            self.assertTrue(candidate["self_hosted"])
            self.assertEqual(
                candidate["risk_flags"],
                ["contents_write", "service_mutation"],
            )

    def test_dated_and_task_scoped_markers_are_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            self.write(
                root,
                "ftmo-runner-recovery-20261002.yml",
                "on:\n  push:\njobs:\n  x:\n    runs-on: ubuntu-latest\n",
            )
            self.write(
                root,
                "ftmo-task37-proof.yml",
                "on:\n  workflow_dispatch:\njobs:\n  x:\n    runs-on: ubuntu-latest\n",
            )

            report = inventory(root)
            by_name = {row["workflow"]: row for row in report["candidates"]}
            self.assertEqual(
                by_name[
                    ".github/workflows/ftmo-runner-recovery-20261002.yml"
                ]["markers"],
                ["dated_filename"],
            )
            self.assertEqual(
                by_name[".github/workflows/ftmo-task37-proof.yml"]["markers"],
                ["task_scoped_filename"],
            )
            self.assertEqual(
                report["marker_counts"],
                {"dated_filename": 1, "task_scoped_filename": 1},
            )

    def test_candidate_output_is_sorted_and_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            for name in (
                "z-pr9-proof.yml",
                "a-pr2-proof.yml",
                "m-pr4-proof.yml",
            ):
                self.write(
                    root,
                    name,
                    "on:\n  workflow_dispatch:\njobs:\n  x:\n    runs-on: ubuntu-latest\n",
                )

            report = inventory(root, max_candidates=2)
            self.assertEqual(report["legacy_candidates"], 3)
            self.assertTrue(report["candidates_truncated"])
            self.assertEqual(
                [row["workflow"] for row in report["candidates"]],
                [
                    ".github/workflows/a-pr2-proof.yml",
                    ".github/workflows/m-pr4-proof.yml",
                ],
            )

    def test_symlink_workflow_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            target = Path(temp) / "target.yml"
            target.write_text("on:\n  push:\n", encoding="utf-8")
            link = root / "ftmo-pr1-proof.yml"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaises(InventoryError):
                inventory(root)

    def test_oversized_candidate_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            path = root / "ftmo-pr1-proof.yml"
            path.write_text("x" * (MAX_WORKFLOW_BYTES + 1), encoding="utf-8")
            with self.assertRaises(InventoryError):
                inventory(root)

    def test_invalid_candidate_limit_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            with self.assertRaises(InventoryError):
                inventory(root, max_candidates=0)


if __name__ == "__main__":
    unittest.main()
