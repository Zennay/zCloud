"""Offline regression tests for the hosted probe; never call a real dashboard."""
import importlib.util
import io
import pathlib
import socket
import unittest
import urllib.error

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "dashboard_hosted_probe.py"
spec = importlib.util.spec_from_file_location("dashboard_hosted_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeResponse:
    def __init__(self, body=b'{"time":"2026-10-08T03:00:00Z","projects":[]}', status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def getcode(self):
        return self.status

    def read(self, count):
        return self.body[:count]


class DashboardProbeTests(unittest.TestCase):
    URL = "https://localhost.invalid/api/status"

    def check(self, response=None, error=None):
        def opener(*_, **__):
            if error:
                raise error
            return response or FakeResponse()
        return probe.check_once(self.URL, opener=opener)

    def test_healthy(self):
        self.assertEqual(self.check(), "ok")

    def test_dns(self):
        self.assertEqual(self.check(error=urllib.error.URLError(socket.gaierror(-2, "secret"))), "dns_error")

    def test_connection_refused(self):
        self.assertEqual(self.check(error=urllib.error.URLError(ConnectionRefusedError())), "connection_error")

    def test_timeout(self):
        self.assertEqual(self.check(error=urllib.error.URLError(socket.timeout())), "timeout")

    def test_http_error(self):
        self.assertEqual(self.check(FakeResponse(status=503)), "http_error")

    def test_invalid_json(self):
        self.assertEqual(self.check(FakeResponse(body=b"{")), "invalid_json")

    def test_missing_schema(self):
        self.assertEqual(self.check(FakeResponse(body=b'{"time":"x"}')), "schema_error")

    def test_invalid_projects_type(self):
        self.assertEqual(self.check(FakeResponse(body=b'{"time":"x","projects":{}}')), "schema_error")

    def test_oversized_response(self):
        self.assertEqual(self.check(FakeResponse(body=b"x" * 65537)), "response_too_large")

    def test_recovers_after_one_failure(self):
        seq = iter(["timeout", "ok"])
        result = probe.probe(self.URL, attempts=3, delay=0, checker=lambda _: next(seq))
        self.assertEqual(result, {"status": "healthy", "attempts": 2, "transient_failures": 1})

    def test_persistent_failure_is_not_masked(self):
        result = probe.probe(self.URL, attempts=3, delay=0, checker=lambda _: "http_error")
        self.assertEqual(result["status"], "unhealthy")
        self.assertEqual(result["failures"], ["http_error"] * 3)

    def test_rejects_unbounded_attempts(self):
        with self.assertRaises(ValueError):
            probe.probe(self.URL, attempts=7)


if __name__ == "__main__":
    unittest.main()
