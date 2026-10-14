import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_workflow_dependency_inventory import inventory


WORKFLOW = Path(".github/workflows/zcloud-workflow-dependency-inventory.yml")


class WorkflowDependencyInventoryTests(unittest.TestCase):
    def _write(self, root: Path, name: str, content: str) -> Path:
        path = root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_classifies_pinned_floating_local_and_docker_uses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "sample.yml",
                """
jobs:
  test:
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803
      - uses: actions/setup-python@v5
      - uses: ./local-action
      - uses: docker://alpine:3.20
""",
            )

            report = inventory(root)

            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(
                report["counts"],
                {
                    "workflows": 1,
                    "uses_total": 4,
                    "external_total": 2,
                    "pinned_sha": 1,
                    "floating": 1,
                    "local": 1,
                    "docker": 1,
                    "invalid_external": 0,
                },
            )
            self.assertEqual(
                report["floating_dependencies"],
                [
                    {
                        "workflow": "sample.yml",
                        "line": 6,
                        "action": "actions/setup-python",
                        "kind": "floating",
                        "ref": "v5",
                    }
                ],
            )

    def test_detects_missing_external_ref_fail_closed_in_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "invalid.yaml",
                """
jobs:
  test:
    steps:
      - uses: owner/action
""",
            )

            report = inventory(root)

            self.assertEqual(report["counts"]["invalid_external"], 1)
            self.assertEqual(report["counts"]["external_total"], 1)
            self.assertEqual(
                report["floating_dependencies"][0]["kind"], "invalid_external"
            )

    def test_output_order_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "z.yml", "- uses: z/action@v1\n")
            self._write(root, "a.yaml", "- uses: a/action@main\n")

            report = inventory(root)

            self.assertEqual(
                [item["workflow"] for item in report["floating_dependencies"]],
                ["a.yaml", "z.yml"],
            )
            json.dumps(report, sort_keys=True)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_rejects_symlink_workflow_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workflows"
            root.mkdir()
            target = base / "outside.yml"
            target.write_text("- uses: actions/checkout@v4\n", encoding="utf-8")
            (root / "linked.yml").symlink_to(target)

            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                inventory(root)

    def test_inventory_workflow_is_read_only_and_immutable(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)
        self.assertIn(
            "python3 -m unittest tests.test_workflow_dependency_inventory", text
        )
        self.assertIn(
            "python3 scripts/zcloud_workflow_dependency_inventory.py --json", text
        )
        self.assertNotIn("runs-on: self-hosted", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("systemctl", text)


if __name__ == "__main__":
    unittest.main()
