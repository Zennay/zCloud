import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "node is required for the dashboard panel test")
class WorkerDebugPanelTests(unittest.TestCase):
    def test_dashboard_panel_renders_escapes_and_is_wired(self):
        result = subprocess.run(["node", str(ROOT / "tests" / "test_worker_debug_panel.js")],
                                capture_output=True, text=True, cwd=ROOT, timeout=60)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("worker debug panel OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
