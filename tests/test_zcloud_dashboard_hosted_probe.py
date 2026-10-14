"""Offline behavioral tests for read-only dashboard classifier."""
import io
import json
import socket
import unittest
import urllib.error

from scripts.zcloud_dashboard_hosted_probe import classify, probe


class Response:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self):
        return self.status

    def read(self, maximum):
        return self.body[:maximum]


def opener(body, status=200):
    return lambda request, timeout: Response(body, status)


class HostedProbeTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(classify("https://ignored", opener=opener(b'{"time":"now","projects":[]}')), "ok")

    def test_invalid_json(self):
        self.assertEqual(classify("https://ignored", opener=opener(b'{')), "invalid_json")

    def test_invalid_schema(self):
        self.assertEqual(classify("https://ignored", opener=opener(b'{"time":"now","projects":{}}')), "invalid_schema")

    def test_status(self):
        self.assertEqual(classify("https://ignored", opener=opener(b'', 503)), "http_status")

    def test_http_error(self):
        def fail(*args, **kwargs):
            raise urllib.error.HTTPError("redacted", 500, "error", {}, None)
        self.assertEqual(classify("https://ignored", opener=fail), "http_status")

    def test_connection_refused(self):
        def fail(*args, **kwargs):
            raise urllib.error.URLError(ConnectionRefusedError())
        self.assertEqual(classify("https://ignored", opener=fail), "connect_or_tls")

    def test_timeout(self):
        def fail(*args, **kwargs):
            raise urllib.error.URLError(socket.timeout())
        self.assertEqual(classify("https://ignored", opener=fail), "timeout")

    def test_dns(self):
        def fail(*args, **kwargs):
            raise urllib.error.URLError(socket.gaierror())
        self.assertEqual(classify("https://ignored", opener=fail), "dns")

    def test_oversized(self):
        self.assertEqual(classify("https://ignored", opener=opener(b'x' * 1048577)), "oversized_response")

    def test_eventual_success(self):
        results = iter(["connect_or_tls", "timeout", "ok"])
        result = probe("ignored", attempts=3, delay=0, sleeper=lambda _: None, checker=lambda _: next(results))
        self.assertEqual(result["result"], "healthy")
        self.assertTrue(result["recovered_after_retry"])
        self.assertEqual(result["attempts"], 3)

    def test_persistent_failure(self):
        result = probe("ignored", attempts=2, delay=0, sleeper=lambda _: None, checker=lambda _: "timeout")
        self.assertEqual(result["result"], "persistent_failure")
        self.assertEqual(result["categories"], ["timeout", "timeout"])

    def test_bounds(self):
        with self.assertRaises(ValueError):
            probe("ignored", attempts=7, checker=lambda _: "ok")


if __name__ == "__main__":
    unittest.main()
