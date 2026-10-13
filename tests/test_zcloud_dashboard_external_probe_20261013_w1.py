"""Offline integration and negative matrix for dashboard external probe (#1166).

No network access, runner mutation, SQLite or privileged recovery.
"""
import importlib.util
import io
import json
import pathlib
import socket
import ssl
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

MODULE = pathlib.Path(__file__).resolve().parents[1] / "scripts/zcloud_dashboard_external_probe_20261013_w1.py"
spec = importlib.util.spec_from_file_location("zcloud_dashboard_external_probe_20261013_w1", MODULE)
probe = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = probe
spec.loader.exec_module(probe)

VALID = b'{"time":"2026-10-13T22:41:00Z","projects":[]}'


class Response:
    def __init__(self, raw=VALID, status=200, content_type="application/json; charset=utf-8"):
        self.raw = raw
        self.status = status
        self.headers = {"Content-Type": content_type}
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False
    def read(self, limit):
        return self.raw[:limit]


class Opener:
    def __init__(self, response=None, error=None):
        self.response = response if response is not None else Response()
        self.error = error
        self.calls = []
    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        return self.response


class DashboardProbeTests(unittest.TestCase):
    def test_healthy_external_api(self):
        opener = Opener()
        self.assertEqual(probe.probe_once(opener), "healthy")
        request, timeout = opener.calls[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, probe.STATUS_URL)
        self.assertEqual(timeout, probe.TIMEOUT_SECONDS)

    def test_retry_recovers_without_authorizing_restart(self):
        statuses = iter(["connection", "timeout", "healthy"])
        sleeps = []
        result = probe.run_probe(lambda: next(statuses), sleeps.append)
        self.assertEqual(result.status, "healthy")
        self.assertTrue(result.recovered_after_retry)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.failure_counts, {"connection": 1, "timeout": 1})
        self.assertEqual(sleeps, [5, 5])
        self.assertFalse(result.authorized)
        self.assertFalse(result.mutations_performed)

    def test_persistent_failure_classified_without_body(self):
        result = probe.run_probe(lambda: "invalid_json", lambda _: None, attempts=3)
        self.assertEqual(result.last_failure_category, "invalid_json")
        self.assertEqual(result.failure_counts, {"invalid_json": 3})
        self.assertFalse(result.recovered_after_retry)
        self.assertEqual(result.attempts, 3)

    def test_http_non_200_is_not_healthy(self):
        self.assertEqual(probe.probe_once(Opener(Response(status=503))), "http_status")
        self.assertEqual(probe.probe_once(Opener(error=urllib.error.HTTPError(
            probe.STATUS_URL, 302, "redirect", {}, None))), "http_status")

    def test_requires_json_mime_type(self):
        self.assertEqual(probe.probe_once(Opener(Response(content_type="text/html"))), "content_type")
        self.assertEqual(probe.probe_once(Opener(Response(content_type="application/json-ish"))), "content_type")

    def test_invalid_json_and_encoding(self):
        for raw in (b"{", b"\xff", b'{"time":NaN,"projects":[]}',
                    b'{"time":"ok","time":"duplicated","projects":[]}'):
            with self.subTest(raw=raw):
                self.assertEqual(probe.probe_once(Opener(Response(raw=raw))), "invalid_json")

    def test_reject_float_exponent_overflow_in_ignored_fields(self):
        raw = b'{"time":"2026-10-13T22:41:00Z","projects":[1e999]}'
        self.assertEqual(probe.probe_once(Opener(Response(raw=raw))), "invalid_json")

    def test_nested_surrogate_and_depth_rejected(self):
        raw = b'{"time":"2026-10-13T22:41:00Z","projects":["\\ud800"]}'
        self.assertEqual(probe.probe_once(Opener(Response(raw=raw))), "invalid_json")
        overdepth = b'{"time":"2026-10-13T22:41:00Z","projects":' + b"[" * 36 + b"0" + b"]" * 36 + b"}"
        self.assertEqual(probe.probe_once(Opener(Response(raw=overdepth))), "invalid_json")

    def test_schema_invalid_or_timezone_missing(self):
        for raw in (
            b"[]",
            b'{"time":"","projects":[]}',
            b'{"time":"2026-10-13T12:00:00","projects":[]}',
            b'{"time":"2026-10-13T12:00:00Z","projects":{}}',
            b'{"time":"2026-10-13T12:00:00Z"}',
            b'{"time":"bad","projects":[]}',
        ):
            with self.subTest(raw=raw):
                self.assertEqual(probe.probe_once(Opener(Response(raw=raw))), "invalid_schema")

    def test_bounded_body(self):
        body = b"x" * (probe.MAX_BYTES + 100)
        self.assertEqual(probe.probe_once(Opener(Response(raw=body))), "body_too_large")

    def test_network_failure_taxonomy(self):
        examples = [
            (TimeoutError(), "timeout"),
            (socket.timeout(), "timeout"),
            (socket.gaierror(-2, "no name"), "dns"),
            (ConnectionRefusedError(), "connection"),
            (ConnectionResetError(), "connection"),
            (ssl.SSLError("certificate error"), "tls"),
            (urllib.error.URLError(socket.gaierror(-2, "host secret")), "dns"),
            (urllib.error.URLError(TimeoutError("sensitive url")), "timeout"),
        ]
        for error, category in examples:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(probe.probe_once(Opener(error=error)), category)

    def test_nonstandard_telemetry_never_includes_raw_values(self):
        marker = "SECRET_TOKEN_OR_PRIVATE_URL"
        result = probe.run_probe(lambda: marker, lambda _: None, attempts=1)
        output = json.dumps(probe.asdict(result))
        self.assertNotIn(marker, output)
        self.assertIn("transport_failure", output)
        self.assertFalse(result.authorized)

    def test_denies_invalid_attempt_budgets(self):
        for invalid in (True, 0, 7, -1, 1.5, "3"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    probe.run_probe(attempts=invalid)

    def test_cli_invalid_config_exits_without_network(self):
        capture = io.StringIO()
        with redirect_stdout(capture):
            code = probe.main(["--attempts", "9"])
        self.assertEqual(code, 2)
        doc = json.loads(capture.getvalue())
        self.assertFalse(doc["authorized"])
        self.assertFalse(doc["mutations_performed"])

    def test_default_opener_cannot_use_inherited_proxy(self):
        with mock.patch.object(probe.urllib.request, "build_opener") as builder:
            builder.return_value = Opener()
            self.assertEqual(probe.probe_once(), "healthy")
            self.assertIsInstance(builder.call_args.args[0], probe.urllib.request.ProxyHandler)
            self.assertEqual(builder.call_args.args[0].proxies, {})
            self.assertIsInstance(builder.call_args.args[1], probe.NoRedirect)

    def test_manual_observation_workflow_is_owner_main_hosted_only(self):
        workflow = (MODULE.parents[1] / ".github/workflows/"
                    "zcloud-dashboard-external-triage-manual-20261013-w1.yml")
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("  workflow_dispatch:", text)
        self.assertIn("github.repository == 'Zennay/zCloud'", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("permissions:", text)
        self.assertIn("  contents: read", text)
        self.assertNotIn("actions: write", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("scripts/zcloud_dashboard_external_probe_20261013_w1.py", text)
        self.assertNotIn("self-hosted", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("workflow_run:", text)
        self.assertNotIn("pull_request:", text)
        self.assertNotIn("push:", text)

    def test_source_has_no_mutation_methods(self):
        source = MODULE.read_text(encoding="utf-8")
        self.assertNotIn("method=\"POST\"", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("systemctl", source)


if __name__ == "__main__":
    unittest.main()
