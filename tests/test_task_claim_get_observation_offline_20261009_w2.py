"""Offline, fixture-only GET observation contract tests; no live management API."""
import unittest
from tools.task_claim_get_observation_offline_20261009_w2 import classify_claim_get


class TaskClaimGetObservationTests(unittest.TestCase):
    def test_snapshot_never_authorizes_even_when_empty(self):
        actual = classify_claim_get(http_status=200, body='{"claims":[]}', project_id="cloud")
        self.assertEqual(actual.state, "observed")
        self.assertEqual(actual.reason, "snapshot_only")
        self.assertEqual(actual.claims, ())

    def test_active_claim_observation_is_not_lease_proof(self):
        actual = classify_claim_get(
            http_status=200,
            body='{"claims":[{"project_id":"cloud","claim_key":"x","owner_id":"worker-a"}]}',
            project_id="cloud",
        )
        self.assertEqual(actual.state, "observed")
        self.assertEqual(actual.claims[0]["owner_id"], "worker-a")

    def test_transient_and_malformed_fail_closed(self):
        cases = [(503, '{"claims":[]}', "http_untrusted"),
                 (None, None, "http_untrusted"),
                 (200, "invalid", "invalid_json"),
                 (200, None, "missing_body"),
                 (200, '{"claims":{}}', "invalid_shape"),
                 (200, '[]', "invalid_shape")]
        for status, body, reason in cases:
            with self.subTest(status=status, body=body):
                actual = classify_claim_get(http_status=status, body=body, project_id="cloud")
                self.assertEqual((actual.state, actual.reason), ("unknown", reason))

    def test_wrong_project_or_malformed_claim_fails_closed(self):
        for body in (
            '{"claims":[{"project_id":"other","claim_key":"x","owner_id":"a"}]}',
            '{"claims":[{"project_id":"cloud","claim_key":"x"}]}',
            '{"claims":[42]}',
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    classify_claim_get(http_status=200, body=body, project_id="cloud").state,
                    "unknown",
                )

    def test_missing_project_never_grants_broad_observation(self):
        self.assertEqual(
            classify_claim_get(http_status=200, body='{"claims":[]}', project_id="").reason,
            "missing_project",
        )


if __name__ == "__main__":
    unittest.main()
