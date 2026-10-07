from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DualProviderWorkerRecoveryTests(unittest.TestCase):
    def test_node_recovery_contract(self):
        proc = subprocess.run(
            ["node", str(ROOT / "tests" / "test_dual_provider_worker_recovery.js")],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("Dual-provider worker recovery checks passed.", proc.stdout)


if __name__ == "__main__":
    unittest.main()
