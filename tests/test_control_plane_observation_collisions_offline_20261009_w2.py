"""Adversarial offline collision tests; no production imports."""
import unittest
from scripts.control_plane_observation_collisions_offline_20261009_w2 import classify_observation_batch as classify


class CollisionTests(unittest.TestCase):
    def test_distinct_is_not_authority(self):
        self.assertEqual(classify([{"worker_id":"worker_1","state":"running"}]), {
            "valid": True, "reason": "distinct_untrusted_observations", "authorizes_action": False})

    def test_duplicate_identical_and_conflicting(self):
        for second in ("running", "stopped", "unknown"):
            with self.subTest(second=second):
                result = classify([{"worker_id":"worker_1","state":"running"},
                                   {"worker_id":"worker_1","state":second}])
                self.assertEqual(result["reason"], "duplicate_worker_id")
                self.assertFalse(result["authorizes_action"])

    def test_case_and_unicode_are_not_coerced(self):
        self.assertTrue(classify([{"worker_id":"worker_a","state":"running"},
                                  {"worker_id":"worker_A","state":"running"}])["valid"] is False)
        self.assertEqual(classify([{"worker_id":"worker_a","state":"running"},
                                   {"worker_id":"worker-a","state":"running"}])["valid"], True)
        for bad in ("ｗorker", "worker\u200b", "Worker", "", "a"*65):
            self.assertFalse(classify([{"worker_id":bad,"state":"running"}])["valid"])

    def test_shape_and_budget(self):
        for invalid in (None, {}, (), [], [{}], [{"worker_id":"a"}],
                        [{"worker_id":"a","state":"running","token":"secret"}],
                        [{"worker_id":"a","state":"active"}],
                        [{"worker_id":True,"state":"running"}],
                        [{"worker_id":"a","state":True}],
                        [{"worker_id":"a","state":"running"}]*257):
            with self.subTest(invalid=str(invalid)[:40]):
                result = classify(invalid)
                self.assertFalse(result["valid"])
                self.assertFalse(result["authorizes_action"])

    def test_subclasses_rejected(self):
        class FakeList(list): pass
        class FakeDict(dict): pass
        self.assertFalse(classify(FakeList([{"worker_id":"a","state":"running"}]))["valid"])
        self.assertFalse(classify([FakeDict(worker_id="a",state="running")])["valid"])


if __name__ == "__main__":
    unittest.main()
