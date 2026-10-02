import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lightup.orchestrator import ExecutionDisabled, NetworkExecutor


class ExecutorTests(unittest.TestCase):
    def test_m0_network_execution_is_hard_disabled(self):
        with self.assertRaises(ExecutionDisabled):
            NetworkExecutor().execute("127.0.0.1")


if __name__ == "__main__":
    unittest.main()
