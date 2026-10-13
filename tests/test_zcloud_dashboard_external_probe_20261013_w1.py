"""Offline integration and negative matrix for dashboard external probe (#1166).

No network access, runner mutation, SQLite or privileged recovery.
"""
import importlib.util
import io
import json
import pathlib
import socket
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ssl
import unittest
from datetime import datetime, timedelta, timezone
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

MODULE = pathlib.Path(__file__).resolve().parents[1] / "scripts/zcloud_dashboard_external_probe_20261013_w1.py"
spec = importlib.util.spec_from_file_location("zcloud_dashboard_external_probe_20261013_w1", MODULE)
probe = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = probe
spec.loader.exec_module(probe)

VALID = json.dumps({"time": datetime.now(timezone.utc).isoformat(), "projects": []}).encode("utf-8")


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

    def test_server_timestamp_freshness_and_future_denial(self):
        now = datetime(2026, 10, 13, 22, 45, tzinfo=timezone.utc)
        cases = [
            (timedelta(seconds=-181), "stale_timestamp"),
            (timedelta(seconds=-180), "healthy"),
            (timedelta(seconds=30), "healthy"),
            (timedelta(seconds=31), "future_timestamp"),
        ]
        for delta, expected in cases:
            with self.subTest(seconds=delta.total_seconds()):
                stamp = (now + delta).isoformat()
                raw = json.dumps({"time": stamp, "projects": []}).encode("utf-8")
                self.assertEqual(probe._validate_payload(raw, now=now), expected)
        raw = json.dumps({"time": now.isoformat(), "projects": []}).encode("utf-8")
        with self.assertRaises(ValueError):
            probe._validate_payload(raw, now=datetime(2026, 10, 13))

    def test_stale_response_not_reported_healthy(self):
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        raw = json.dumps({"time": stamp, "projects": []}).encode("utf-8")
        self.assertEqual(probe.probe_once(Opener(Response(raw=raw))), "stale_timestamp")

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

    def test_operator_markdown_is_readable_private_and_non_authorizing(self):
        summary = probe.ProbeSummary("unhealthy", "timeout", 3, False,
                                     {"timeout": 2, "dns": 1})
        output = probe.operator_markdown(summary)
        self.assertIn("API niet aantoonbaar bereikbaar", output)
        self.assertIn("DNS / naamoplossing: 1", output)
        self.assertIn("Time-out: 2", output)
        self.assertIn(chr(10), output)
        self.assertIn("nooit automatisch", output)
        self.assertNotIn(probe.STATUS_URL, output)

    def test_operator_markdown_recovered_does_not_claim_prod_health(self):
        summary = probe.ProbeSummary("healthy", None, 2, True, {"connection": 1})
        output = probe.operator_markdown(summary)
        self.assertIn("API bereikbaar", output)
        self.assertIn("Hersteld na hercontrole:** ja", output)
        self.assertIn("bewijst niet", output)
        with mock.patch.object(probe, "run_probe", return_value=summary):
            capture = io.StringIO()
            with redirect_stdout(capture):
                self.assertEqual(probe.main(["--format", "markdown"]), 0)
        self.assertIn("## zCloud", capture.getvalue())

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

@contextmanager
def temporary_dashboard_http(*, code=200, payload=None, media_type="application/json",
                             redirect=False):
    """Actual ephemeral loopback HTTP transport; never the production VPS."""
    calls = []
    if payload is None:
        payload = json.dumps({
            "time": datetime.now(timezone.utc).isoformat(),
            "projects": [],
        }).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(("GET", self.path))
            if redirect:
                self.send_response(302)
                self.send_header("Location", "/redirect-target-not-allowed")
                self.end_headers()
                return
            self.send_response(code)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            calls.append(("POST", self.path))
            self.send_error(405)

        def log_message(self, *args):
            # Do not print raw URLs, requests, or bodies in public CI.
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        address = "http://127.0.0.1:%d/api/status" % httpd.server_port
        yield address, calls
    finally:
        httpd.shutdown()
        thread.join(timeout=2)
        httpd.server_close()


class DashboardProbeRealHttpTests(unittest.TestCase):
    def _run(self, expected, **options):
        with temporary_dashboard_http(**options) as (url, calls):
            with mock.patch.object(probe, "STATUS_URL", url):
                # Inherited proxies must NEVER intercept even the local test server.
                with mock.patch.dict(os.environ, {
                    "http_proxy": "http://127.0.0.1:1",
                    "HTTP_PROXY": "http://127.0.0.1:1",
                    "ALL_PROXY": "http://127.0.0.1:1",
                    "no_proxy": "",
                    "NO_PROXY": "",
                }):
                    observed = probe.probe_once()
            self.assertEqual(observed, expected)
            self.assertEqual(calls, [("GET", "/api/status")])

    def test_actual_http_success_without_proxy_or_post(self):
        self._run("healthy")

    def test_actual_http_503_denied(self):
        self._run("http_status", code=503)

    def test_actual_http_redirect_is_never_followed(self):
        self._run("http_status", redirect=True)

    def test_actual_http_wrong_media_type(self):
        self._run("content_type", media_type="text/html")

    def test_actual_http_oversize_stream_is_rejected(self):
        self._run("body_too_large", payload=b"A" * (probe.MAX_BYTES + 1024))

    def test_actual_http_bad_json(self):
        self._run("invalid_json", payload=b'{"time":')

    def test_actual_http_bad_schema(self):
        self._run("invalid_schema", payload=b'{"time":"2026-10-13T22:50:00Z"}')

    def test_actual_http_expired_snapshot_denied(self):
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=240)).isoformat()
        raw = json.dumps({"time": stamp, "projects": []}).encode("utf-8")
        self._run("stale_timestamp", payload=raw)


if __name__ == "__main__":
    unittest.main()
