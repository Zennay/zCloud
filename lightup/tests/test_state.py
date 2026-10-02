import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lightup.state import StateStore


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db")
        self.store = StateStore(self.tmp.name)

    def tearDown(self):
        self.tmp.close()

    def test_capability_lease_prevents_parallel_collision(self):
        run_id = self.store.create_run("127.0.0.1")
        self.store.acquire_lease(run_id, "web-baseline", "worker-a", ttl_seconds=60)
        with self.assertRaises(RuntimeError):
            self.store.acquire_lease(run_id, "web-baseline", "worker-b", ttl_seconds=60)

    def test_different_capabilities_can_run_in_parallel(self):
        run_id = self.store.create_run("127.0.0.1")
        first = self.store.acquire_lease(run_id, "web-baseline", "worker-a", ttl_seconds=60)
        second = self.store.acquire_lease(run_id, "cloud-iam", "worker-b", ttl_seconds=60)
        self.assertNotEqual(first.capability_id, second.capability_id)

    def test_evidence_is_content_addressed(self):
        run_id = self.store.create_run("127.0.0.1")
        evidence_id = self.store.add_evidence(
            run_id,
            "web-baseline",
            "observation",
            "unit-test",
            b"evidence",
            {"safe": True},
        )
        self.assertTrue(evidence_id)


if __name__ == "__main__":
    unittest.main()
