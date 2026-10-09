"""Fixture-only negative contracts; never reads runtime state."""
import unittest
from scripts.control_plane_evidence_quorum_offline_20261009_w4 import assess


class QuorumEvidenceTests(unittest.TestCase):
    def assert_non_authorizing(self, observations, classification):
        result = assess(observations)
        self.assertEqual(result["classification"], classification)
        self.assertIs(result["authorizes_action"], False)

    def test_agreement_running_is_not_proof(self):
        self.assert_non_authorizing([{"source": "worker1", "status": "running"},
                                     {"source": "worker2", "status": "running"}],
                                    "unverified_running")

    def test_agreement_stopped_is_not_proof(self):
        self.assert_non_authorizing([{"source": "worker1", "status": "stopped"}],
                                    "unverified_stopped")

    def test_contradiction_independent_of_order(self):
        a = [{"source": "alpha", "status": "running"},
             {"source": "beta", "status": "stopped"}]
        for sample in (a, list(reversed(a))):
            self.assert_non_authorizing(sample, "contradictory")

    def test_unknown_never_implies_health(self):
        self.assert_non_authorizing([{"source": "alpha", "status": "running"},
                                     {"source": "beta", "status": "unknown"}],
                                    "incomplete")

    def test_contradiction_takes_precedence_over_unknown(self):
        self.assert_non_authorizing([{"source": "a", "status": "running"},
                                     {"source": "b", "status": "stopped"},
                                     {"source": "c", "status": "unknown"}],
                                    "contradictory")

    def test_duplicate_source_denied(self):
        self.assert_non_authorizing([{"source": "a", "status": "running"},
                                     {"source": "a", "status": "stopped"}], "invalid")

    def test_malformed_evidence_denied(self):
        bad = ([], {}, None, "running", [None], [1], [{"source": "a"}],
               [{"source": "a", "status": "RUNNING"}],
               [{"source": "a", "status": True}],
               [{"source": 2, "status": "running"}],
               [{"source": "a", "status": "running", "extra": 1}],
               [{"source": "a b", "status": "running"}],
               [{"source": "а", "status": "running"}],
               [{"source": "a", "status": "running"}] * 33)
        for sample in bad:
            with self.subTest(sample=str(sample)[:100]):
                self.assert_non_authorizing(sample, "invalid")

    def test_mutable_subclass_not_accepted(self):
        class DictSubclass(dict):
            pass
        class ListSubclass(list):
            pass
        self.assert_non_authorizing(ListSubclass([{"source": "a", "status": "running"}]), "invalid")
        self.assert_non_authorizing([DictSubclass(source="a", status="running")], "invalid")


    def test_source_length_boundaries(self):
        self.assert_non_authorizing([{"source": "x" * 64, "status": "running"}],
                                    "unverified_running")
        for source in ("", "x" * 65, "x\\n", "x/y", "x\\u200b"):
            with self.subTest(source=repr(source)):
                self.assert_non_authorizing([{"source": source, "status": "running"}],
                                            "invalid")

    def test_full_capacity_does_not_grant_authority(self):
        observations = [
            {"source": f"worker-{i}", "status": "running"} for i in range(32)
        ]
        self.assert_non_authorizing(observations, "unverified_running")

    def test_input_is_not_mutated(self):
        observations = [{"source": "worker-1", "status": "running"}]
        original = [entry.copy() for entry in observations]
        self.assert_non_authorizing(observations, "unverified_running")
        self.assertEqual(observations, original)


if __name__ == "__main__":
    unittest.main()
