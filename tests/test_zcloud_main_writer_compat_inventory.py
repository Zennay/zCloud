from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_main_writer_compat_inventory.py"

spec = importlib.util.spec_from_file_location("writer_inventory", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader is not None
spec.loader.exec_module(module)


class MainWriterCompatibilityInventoryTests(unittest.TestCase):
    def make_repo(self, workflows: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        wf = root / ".github" / "workflows"
        wf.mkdir(parents=True)
        for name, body in workflows.items():
            (wf / name).write_text(body, encoding="utf-8")
        return root

    def test_classifies_direct_main_push(self):
        root = self.make_repo(
            {
                "writer.yml": """
permissions:
  contents: write
jobs:
  publish:
    steps:
      - run: git push origin HEAD:main
""",
                "reader.yml": """
permissions:
  contents: read
jobs:
  check:
    steps:
      - run: echo ok
""",
            }
        )
        result = module.build_inventory(root)
        self.assertEqual(result["direct_main_writer_count"], 1)
        self.assertEqual(
            result["direct_main_writers"][0]["signals"],
            ["contents_write", "git_push_main"],
        )
        self.assertFalse(result["mutation_performed"])

    def test_classifies_main_refs_api_write(self):
        signals = module.classify_text(
            """
permissions:
  contents: write
steps:
  - run: gh api --method PATCH repos/o/r/git/refs/heads/main -f sha=deadbeef
"""
        )
        self.assertIn("main_refs_write_api", signals)

    def test_read_only_main_ref_plus_unrelated_post_is_not_api_write(self):
        signals = module.classify_text(
            """
steps:
  - run: git ls-remote origin refs/heads/main
  - run: |
      python3 - <<'PY'
      request = urllib.request.Request("https://api.github.com/repos/o/r/statuses/abc", method="POST")
      PY
"""
        )
        self.assertNotIn("main_refs_write_api", signals)

    def test_contents_write_without_direct_main_is_separate(self):
        root = self.make_repo(
            {
                "artifact.yml": """
permissions:
  contents: write
jobs:
  receipt:
    steps:
      - run: echo receipt
"""
            }
        )
        result = module.build_inventory(root)
        self.assertEqual(result["direct_main_writer_count"], 0)
        self.assertEqual(result["contents_write_only_count"], 1)

    def test_symlink_workflow_is_skipped(self):
        root = self.make_repo({"safe.yml": "permissions:\n  contents: read\n"})
        wf = root / ".github" / "workflows"
        target = root / "outside.yml"
        target.write_text("permissions:\n  contents: write\n", encoding="utf-8")
        (wf / "linked.yml").symlink_to(target)
        result = module.build_inventory(root)
        self.assertEqual(result["workflow_files_skipped"], 1)
        self.assertEqual(result["direct_main_writer_count"], 0)

    def test_cli_strict_gate_fails_when_writer_exists(self):
        root = self.make_repo(
            {"writer.yml": "permissions:\n  contents: write\nsteps:\n  - run: git push origin HEAD:main\n"}
        )
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--json",
                "--require-zero-direct-main-writers",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "inventory_complete")
        self.assertEqual(payload["direct_main_writer_count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
