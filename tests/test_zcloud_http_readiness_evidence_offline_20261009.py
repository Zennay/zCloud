import unittest
from scripts.zcloud_http_readiness_evidence_offline_20261009 import classify_http_readiness


class ReadinessEvidenceTests(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(classify_http_readiness(200, service_active=True, sqlite_ok=True).state, "ready")

    def test_503_with_healthy_dependencies_is_not_service_death(self):
        result = classify_http_readiness(503, service_active=True, sqlite_ok=True)
        self.assertEqual(result.state, "transient")
        self.assertFalse(result.authorizes_restart)

    def test_gateway_errors_are_transient(self):
        for status in (502, 503, 504):
            with self.subTest(status=status):
                self.assertEqual(classify_http_readiness(status, service_active=True, sqlite_ok=True).state, "transient")

    def test_inactive_service(self):
        self.assertEqual(classify_http_readiness(503, service_active=False, sqlite_ok=True).state, "unavailable")

    def test_database_failed(self):
        self.assertEqual(classify_http_readiness(200, service_active=True, sqlite_ok=False).state, "degraded")

    def test_missing_evidence_fails_closed(self):
        for value in (None, 1, "true"):
            with self.subTest(value=value):
                self.assertEqual(classify_http_readiness(200, service_active=value, sqlite_ok=True).state, "incomplete")

    def test_invalid_status_fails_closed(self):
        for value in (None, True, "503", 99, 600):
            with self.subTest(value=value):
                self.assertEqual(classify_http_readiness(value, service_active=True, sqlite_ok=True).state, "incomplete")

    def test_http_success_does_not_override_failed_dependencies(self):
        for status in (200, 201, 204):
            with self.subTest(status=status):
                self.assertEqual(classify_http_readiness(status, service_active=True, sqlite_ok=False).state, "degraded")
                self.assertEqual(classify_http_readiness(status, service_active=False, sqlite_ok=True).state, "unavailable")

    def test_error_codes_other_than_gateway_are_degraded(self):
        for status in (301, 401, 403, 404, 408, 429, 500):
            with self.subTest(status=status):
                self.assertEqual(classify_http_readiness(status, service_active=True, sqlite_ok=True).state, "degraded")

    def test_non_boolean_sqlite_evidence_is_incomplete(self):
        for sqlite in (None, 0, 1, "ok", [], {}):
            with self.subTest(sqlite=sqlite):
                self.assertEqual(classify_http_readiness(200, service_active=True, sqlite_ok=sqlite).state, "incomplete")

    def test_no_observation_authorizes_restart(self):
        for http in (200, 400, 502, 503, 504):
            for active in (True, False, None):
                for db in (True, False, None):
                    self.assertFalse(classify_http_readiness(http, service_active=active, sqlite_ok=db).authorizes_restart)


if __name__ == "__main__":
    unittest.main()
