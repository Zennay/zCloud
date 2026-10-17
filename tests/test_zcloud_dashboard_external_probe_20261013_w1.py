"""Offline integration and negative matrix for dashboard external probe (#1166).

No network access, runner mutation, SQLite or privileged recovery.
"""
import importlib.util
import io
from email.message import Message
import json
import pathlib
import socket
import os
import subprocess
import tempfile
import textwrap
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
        # A healthy verdict must not rely on an intermediary's old status.
        self.assertIn("no-cache", request.get_header("Cache-control").lower())
        self.assertIn("no-store", request.get_header("Cache-control").lower())
        self.assertEqual(request.get_header("Pragma"), "no-cache")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(request.get_method(), "GET")

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
        self.assertFalse(result.transport_authenticated)

    def test_unexpected_exception_counts_as_transport_failure_then_recovers(self):
        calls = []
        def raising_then_healthy():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("SECRET_PRIVATE_URL_DO_NOT_LOG")
            return "healthy"
        summary = probe.run_probe(raising_then_healthy, lambda _: None, attempts=2)
        self.assertEqual(summary.status, "healthy")
        self.assertEqual(summary.attempts, 2)
        self.assertEqual(summary.failure_counts, {"transport_failure": 1})
        self.assertTrue(summary.recovered_after_retry)
        self.assertFalse(summary.authorized)
        self.assertNotIn("SECRET", json.dumps(probe.asdict(summary)))

    def test_persistent_unexpected_exceptions_remain_unhealthy(self):
        def raising():
            raise ValueError("internal endpoint or bearer credential")
        summary = probe.run_probe(raising, lambda _: None, attempts=3)
        self.assertEqual(summary.status, "unhealthy")
        self.assertEqual(summary.last_failure_category, "transport_failure")
        self.assertEqual(summary.failure_counts, {"transport_failure": 3})
        self.assertFalse(summary.authorized)
        self.assertFalse(summary.mutations_performed)
        self.assertNotIn("bearer", probe.operator_markdown(summary))

    def test_persistent_failure_classified_without_body(self):
        result = probe.run_probe(lambda: "invalid_json", lambda _: None, attempts=3)
        self.assertEqual(result.last_failure_category, "invalid_json")
        self.assertEqual(result.failure_counts, {"invalid_json": 3})
        self.assertFalse(result.recovered_after_retry)
        self.assertEqual(result.attempts, 3)

    def test_http_non_200_is_not_healthy(self):
        self.assertEqual(probe.probe_once(Opener(Response(status=503))), "http_server_error")
        self.assertEqual(probe.probe_once(Opener(error=urllib.error.HTTPError(
            probe.STATUS_URL, 302, "redirect", {}, None))), "http_redirect")

    def test_grouped_http_failures_never_claim_recovery(self):
        examples = ((201, "http_status"), (302, "http_redirect"),
                    (401, "http_client_error"), (404, "http_client_error"),
                    (429, "http_client_error"), (500, "http_server_error"),
                    (503, "http_server_error"), (599, "http_server_error"),
                    (700, "http_status"), (True, "http_status"))
        for status, expected in examples:
            with self.subTest(status=status):
                self.assertEqual(probe.probe_once(Opener(Response(status=status))), expected)
                self.assertEqual(probe.probe_once(Opener(error=urllib.error.HTTPError(
                    probe.STATUS_URL, status, "SECRET_RESPONSE", {}, None))), expected)
        labels = (
            ("http_redirect", "Onverwachte HTTP-doorverwijzing"),
            ("http_client_error", "HTTP-aanvraag geweigerd (4xx)"),
            ("http_server_error", "HTTP-serverfout (5xx)"),
        )
        for category, label in labels:
            with self.subTest(category=category):
                result = probe.run_probe(lambda: category, lambda _: None, attempts=1)
                self.assertEqual(result.status, "unhealthy")
                self.assertFalse(result.authorized)
                markdown = probe.operator_markdown(result)
                self.assertIn(label, markdown)
                self.assertNotIn(probe.STATUS_URL, markdown)

    def test_requires_json_mime_type(self):
        self.assertEqual(probe.probe_once(Opener(Response(content_type="text/html"))), "content_type")
        self.assertEqual(probe.probe_once(Opener(Response(content_type="application/json-ish"))), "content_type")

    def test_strict_json_content_type_rejects_conflicting_charsets(self):
        for value in ("application/json; charset=latin-1",
                      "application/json; charset=iso-8859-1",
                      "application/json; charset=utf-8; profile=secret",
                      "application/json; charset=", "application/json;",
                      "application/json; profile=foo", "application/json\u000a",
                      "application/json; charset=utf-8\u0000",
                      "application/json; charset=utf-8; charset=utf-8",
                      "application/json; " + "x" * 200):
            with self.subTest(value=repr(value)):
                self.assertEqual(probe.probe_once(Opener(Response(content_type=value))),
                                 "content_type")

    def test_strict_json_content_type_accepts_normalized_utf8(self):
        for value in ("application/json", "application/json; charset=utf-8",
                      'APPLICATION/JSON; CHARSET="UTF-8"',
                      "application/json ; charset=UTF-8"):
            with self.subTest(value=value):
                self.assertEqual(probe.probe_once(Opener(Response(content_type=value))),
                                 "healthy")

    def test_rejects_mismatched_and_malformed_content_length(self):
        for length in ("0", "1", "999", "-1", "+3", "1, 2",
                       "1e5", "１２", "1" * 100):
            with self.subTest(length=length):
                response = Response()
                response.headers["Content-Length"] = length
                expected = ("body_too_large" if length.isascii() and
                            length.isdigit() and len(length) <= 12 and
                            int(length) > probe.MAX_BYTES else "invalid_headers")
                self.assertEqual(probe.probe_once(Opener(response)), expected)

    def test_accepts_truthful_content_length_and_identity_encoding(self):
        response = Response()
        response.headers["Content-Length"] = str(len(response.raw))
        response.headers["Content-Encoding"] = "identity"
        self.assertEqual(probe.probe_once(Opener(response)), "healthy")

    def test_rejects_encoded_payload_headers_without_decompression(self):
        for value in ("gzip", "br", "chunked", "identity, gzip"):
            with self.subTest(value=value):
                response = Response()
                response.headers["Content-Encoding"] = value
                self.assertEqual(probe.probe_once(Opener(response)), "invalid_headers")

    def test_rejects_ambiguous_content_encoding_metadata(self):
        for coding in ("", "identity\\n", "identity\\t", "identity\\x00",
                       "identity,identity", "br", "gZip",
                       "identity" + " " * 40, "idèntity"):
            with self.subTest(coding=repr(coding)):
                response = Response()
                response.headers["Content-Encoding"] = coding
                response.read = mock.Mock(side_effect=AssertionError("must not read"))
                self.assertEqual(probe.probe_once(Opener(response)), "invalid_headers")
                response.read.assert_not_called()
        response = Response()
        response.headers["Content-Encoding"] = " IDENTITY "
        self.assertEqual(probe.probe_once(Opener(response)), "healthy")

    def test_rejects_duplicate_critical_http_headers(self):
        for header, value in (("Content-Length", str(len(VALID))),
                              ("Content-Type", "application/json"),
                              ("Content-Encoding", "identity")):
            with self.subTest(header=header):
                headers = Message()
                headers.add_header("Content-Type", "application/json")
                if header == "Content-Type":
                    headers.add_header("Content-Type", "application/json")
                elif header == "Content-Length":
                    headers.add_header("Content-Length", value)
                    headers.add_header("Content-Length", value)
                else:
                    headers.add_header("Content-Encoding", value)
                    headers.add_header("Content-Encoding", value)
                response = Response()
                response.headers = headers
                self.assertEqual(probe.probe_once(Opener(response)), "invalid_headers")

    def test_transfer_encoding_is_never_trusted_for_status_proof(self):
        # Even where urllib silently decodes an HTTP/1.1 chunked response,
        # the external probe must not confuse uncertain framing with health.
        for coding in ("chunked", "gzip", "identity", "chunked, gzip", ""):
            with self.subTest(coding=coding):
                response = Response()
                response.headers["Transfer-Encoding"] = coding
                response.read = mock.Mock(side_effect=AssertionError("must not read"))
                self.assertEqual(probe.probe_once(Opener(response)), "invalid_headers")
                response.read.assert_not_called()

    def test_duplicate_transfer_encoding_and_length_conflict_fail_closed(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                headers = Message()
                headers.add_header("Content-Type", "application/json")
                headers.add_header("Content-Length", str(len(VALID)))
                headers.add_header("Transfer-Encoding", "chunked")
                if duplicate:
                    headers.add_header("Transfer-Encoding", "gzip")
                response = Response()
                response.headers = headers
                response.read = mock.Mock(side_effect=AssertionError("must not read"))
                self.assertEqual(probe.probe_once(Opener(response)), "invalid_headers")
                response.read.assert_not_called()

    def test_http_age_header_nonzero_never_claims_live_reachability(self):
        for age in ("1", "30", "180", "999999999999"):
            with self.subTest(age=age):
                response = Response()
                response.headers["Age"] = age
                response.read = mock.Mock(side_effect=AssertionError("must not read cached body"))
                self.assertEqual(probe.probe_once(Opener(response)), "cache_evidence")
                response.read.assert_not_called()
        response = Response()
        response.headers["Age"] = "0"
        self.assertEqual(probe.probe_once(Opener(response)), "healthy")

    def test_http_age_header_malformed_or_duplicate_fails_closed(self):
        for age in ("", "00x", "+1", "-1", "１", "1, 2",
                    " 1", "1 ", "1.0", "1" * 14):
            with self.subTest(age=repr(age)):
                response = Response()
                response.headers["Age"] = age
                response.read = mock.Mock(side_effect=AssertionError("must not read"))
                self.assertEqual(probe.probe_once(Opener(response)), "invalid_headers")
                response.read.assert_not_called()
        headers = Message()
        headers.add_header("Content-Type", "application/json")
        headers.add_header("Age", "0")
        headers.add_header("Age", "0")
        response = Response()
        response.headers = headers
        response.read = mock.Mock(side_effect=AssertionError("must not read"))
        self.assertEqual(probe.probe_once(Opener(response)), "invalid_headers")
        response.read.assert_not_called()

    def test_cache_evidence_is_unauthenticated_and_never_authorizes_mutation(self):
        summary = probe.run_probe(lambda: "cache_evidence", lambda _: None, attempts=2)
        self.assertEqual(summary.status, "unhealthy")
        self.assertEqual(summary.failure_counts, {"cache_evidence": 2})
        self.assertEqual(summary.last_failure_category, "cache_evidence")
        self.assertFalse(summary.authorized)
        self.assertFalse(summary.mutations_performed)
        self.assertFalse(summary.transport_authenticated)
        text = probe.operator_markdown(summary)
        self.assertIn("HTTP-antwoord heeft cacheleeftijd: 2", text)
        self.assertNotIn(probe.STATUS_URL, text)

    def test_http_cache_status_hit_is_a_non_authorizing_failure(self):
        for value in ("zcloud; hit", "CacheOne; fwd=stale, CDN; HIT",
                      "cdn; hit; ttl=15", "cdn;hit"):
            with self.subTest(value=value):
                response = Response()
                response.headers["Cache-Status"] = value
                response.read = mock.Mock(side_effect=AssertionError("do not read cached body"))
                self.assertEqual(probe.probe_once(Opener(response)), "cache_evidence")
                response.read.assert_not_called()
        for value in ("cdn; fwd=uri-miss", "cdn; fwd=miss",
                      'cdn; detail="hit"', "cdn"):
            with self.subTest(miss=value):
                response = Response()
                response.headers["Cache-Status"] = value
                self.assertEqual(probe.probe_once(Opener(response)), "healthy")

    def test_structured_cache_status_boolean_and_quoted_parameters(self):
        # RFC 9211 Cache-Status hit is a structured boolean: an explicit
        # hit=?1 must not slip through as fresh status. Quoted separators
        # inside detail must never create a phantom 'hit' token.
        cases = (
            ("CDN; hit=?1", "cache_evidence"),
            ("Origin; fwd=miss, CDN; hit=?1; ttl=35", "cache_evidence"),
            ("cdn; hit=?0", "healthy"),
            ("cdn; hit=?0; fwd=uri-miss", "invalid_headers"),
            ('cdn; detail="quoted; hit"; fwd=uri-miss', "healthy"),
            ('cdn; detail="quoted, other; hit=?1"', "healthy"),
            ('cdn; detail="escaped ' + chr(92) + '"; hit"; fwd=miss', "healthy"),
            ("cdn; hit=?1; fwd=miss", "invalid_headers"),
            ("cdn; hit; fwd=stale", "invalid_headers"),
            ("cdn; hit=?1; hit=?1", "invalid_headers"),
            ("cdn; hit=?1; hit=?0", "invalid_headers"),
            ("cdn; hit=yes", "invalid_headers"),
            ("cdn; hit=1", "invalid_headers"),
            ("cdn; hit=?", "invalid_headers"),
            ("cdn; hit=", "invalid_headers"),
            ("cdn; fwd", "invalid_headers"),
            ("cdn; fwd=uri-miss", "healthy"),
            ("cdn; fwd=miss; fwd=stale", "invalid_headers"),
            ('cdn; fwd="uri-miss"', "invalid_headers"),
            ("cdn; fwd=?1", "invalid_headers"),
            ("cdn; fwd=not a token", "invalid_headers"),
            ("cdn; hit=?2", "invalid_headers"),
            ('cdn; detail="unterminated', "invalid_headers"),
            ("cdn; fwd=miss,", "invalid_headers"),
            ("cdn;; hit", "invalid_headers"),
        )
        for value, expected in cases:
            with self.subTest(cache_status=value):
                response = Response()
                response.headers["Cache-Status"] = value
                if expected != "healthy":
                    response.read = mock.Mock(
                        side_effect=AssertionError("cached or ambiguous body must not be read")
                    )
                self.assertEqual(probe.probe_once(Opener(response)), expected)
                if expected != "healthy":
                    response.read.assert_not_called()

    def test_structured_cache_identifiers_and_duplicate_extension_keys(self):
        cases = (
            ('"CDN Company Here"; hit', "cache_evidence"),
            ('"CDN, Gateway"; fwd=uri-miss', "healthy"),
            ("CDN; hit; detail=MEMORY", "cache_evidence"),
            ("CDN; detail=MEMORY; fwd=miss", "healthy"),
            ("CDN; hit; detail=MEMORY; detail=DISK", "invalid_headers"),
            ("CDN; fwd=miss; foo=a; foo=b", "invalid_headers"),
            ("CDN; fwd=miss; foo=bar; FOO=baz", "invalid_headers"),
            ("CDN; hit; 1bad=value", "invalid_headers"),
            ("CDN; hit; bad name=value", "invalid_headers"),
            ("CDN; hit; @bad=value", "invalid_headers"),
            ("CDN Name; hit", "invalid_headers"),
            ("CDN=0; hit", "invalid_headers"),
            (":; hit", "invalid_headers"),
            ("; hit", "invalid_headers"),
            ('"Unfinished; hit', "invalid_headers"),
            ('"Cached "Broken"; hit', "invalid_headers"),
            ('"Backslash ' + chr(92) + 'q"; hit', "invalid_headers"),
        )
        for value, expected in cases:
            with self.subTest(cache_status=value):
                response = Response()
                response.headers["Cache-Status"] = value
                if expected != "healthy":
                    response.read = mock.Mock(
                        side_effect=AssertionError("malformed cache body should not be read")
                    )
                self.assertEqual(probe.probe_once(Opener(response)), expected)
                if expected != "healthy":
                    response.read.assert_not_called()

    def test_structured_cache_revalidation_requires_full_origin_status(self):
        # RFC 9211 fwd-status 304/206 can mean the intermediary reused or
        # assembled a stored representation, not a fresh full origin status.
        cases = (
            ("CDN; fwd=stale; fwd-status=304", "cache_evidence"),
            ("CDN; fwd=partial; fwd-status=206", "cache_evidence"),
            ("CDN; fwd=uri-miss; fwd-status=200", "healthy"),
            ("CDN; fwd=miss; stored; collapsed=?1", "healthy"),
            ("CDN; fwd=stale; stored=?0; ttl=-1", "healthy"),
            ("CDN; hit; ttl=45", "cache_evidence"),
            ("CDN; fwd-status=304", "invalid_headers"),
            ("CDN; stored", "invalid_headers"),
            ("CDN; collapsed=?1", "invalid_headers"),
            ("CDN; fwd=miss; fwd-status=2000", "invalid_headers"),
            ("CDN; fwd=miss; fwd-status=0", "invalid_headers"),
            ("CDN; fwd=miss; fwd-status=true", "invalid_headers"),
            ('CDN; fwd=miss; fwd-status="304"', "invalid_headers"),
            ("CDN; fwd=miss; stored=maybe", "invalid_headers"),
            ("CDN; fwd=miss; collapsed=1", "invalid_headers"),
            ("CDN; fwd=miss; ttl=1.5", "invalid_headers"),
            ("CDN; fwd=miss; ttl=+2", "invalid_headers"),
            ("CDN; fwd=miss; ttl=1" + "0" * 16, "invalid_headers"),
        )
        for value, expected in cases:
            with self.subTest(header=value):
                response = Response()
                response.headers["Cache-Status"] = value
                if expected != "healthy":
                    response.read = mock.Mock(side_effect=AssertionError("body should not be read"))
                self.assertEqual(probe.probe_once(Opener(response)), expected)
                if expected != "healthy":
                    response.read.assert_not_called()

    def test_structured_extension_values_must_be_unambiguous(self):
        cases = (
            ('CDN; fwd=miss; note="safe; despite punctuation"', "healthy"),
            ("CDN; fwd=miss; custom=12.25", "healthy"),
            ("CDN; fwd=miss; custom=-12", "healthy"),
            ("CDN; fwd=miss; custom=?1", "healthy"),
            ("CDN; fwd=miss; custom=MEMORY", "healthy"),
            ("CDN; fwd=miss; custom=bad value", "invalid_headers"),
            ("CDN; fwd=miss; custom=?2", "invalid_headers"),
            ("CDN; fwd=miss; custom=1.2345", "invalid_headers"),
            ('CDN; fwd=miss; custom="ok"junk', "invalid_headers"),
            ('CDN; fwd=miss; custom="broken ' + chr(92) + 'q"', "invalid_headers"),
            ('CDN; fwd=miss; custom=:AQID:', "invalid_headers"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                response = Response()
                response.headers["Cache-Status"] = value
                if expected != "healthy":
                    response.read = mock.Mock(side_effect=AssertionError("must not read"))
                self.assertEqual(probe.probe_once(Opener(response)), expected)
                if expected != "healthy":
                    response.read.assert_not_called()

    def test_http_cache_status_malformed_or_duplicate_denied(self):
        for value in ("", "cdn; " + "x" * 513, "cdn; hit\n",
                      "cdn; hit\x00", "cdn; hït"):
            with self.subTest(value=repr(value)):
                response = Response()
                response.headers["Cache-Status"] = value
                response.read = mock.Mock(side_effect=AssertionError("must not read"))
                self.assertEqual(probe.probe_once(Opener(response)), "invalid_headers")
                response.read.assert_not_called()
        headers = Message()
        headers.add_header("Content-Type", "application/json")
        headers.add_header("Cache-Status", "cdn; fwd=miss")
        headers.add_header("Cache-Status", "cdn; hit")
        response = Response()
        response.headers = headers
        response.read = mock.Mock(side_effect=AssertionError("must not read"))
        self.assertEqual(probe.probe_once(Opener(response)), "invalid_headers")
        response.read.assert_not_called()

    def test_declared_oversize_rejected_without_reading_body(self):
        response = Response()
        response.headers["Content-Length"] = str(probe.MAX_BYTES + 1)
        response.read = mock.Mock(side_effect=AssertionError("must not read"))
        self.assertEqual(probe.probe_once(Opener(response)), "body_too_large")
        response.read.assert_not_called()

    def test_http_header_category_never_claims_healed_service(self):
        summary = probe.run_probe(lambda: "invalid_headers", lambda _: None, attempts=1)
        self.assertEqual(summary.status, "unhealthy")
        self.assertFalse(summary.authorized)
        self.assertIn("Tegenstrijdige HTTP-headers: 1", probe.operator_markdown(summary))
        self.assertNotIn(probe.STATUS_URL, probe.operator_markdown(summary))

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

    def test_projects_must_be_objects_not_merely_a_json_list(self):
        now = datetime.now(timezone.utc)
        for rows in ([None], [True], [0], ["fake"], [[]],
                     [{"id": "cloud"}, 0], [{"id": "cloud"}, None]):
            with self.subTest(rows=rows):
                raw = json.dumps({"time": now.isoformat(), "projects": rows}).encode()
                self.assertEqual(probe._validate_payload(raw, now=now), "invalid_schema")
        for rows in ([], [{}], [{"id": "cloud"}, {"id": "ftmo"}]):
            with self.subTest(valid_rows=rows):
                raw = json.dumps({"time": now.isoformat(), "projects": rows}).encode()
                self.assertEqual(probe._validate_payload(raw, now=now), "healthy")

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

    def test_nested_and_cyclic_urllib_errors_are_privacy_safe(self):
        cyclic = urllib.error.URLError("secret upstream URL")
        cyclic.reason = cyclic
        self.assertEqual(probe._exception_category(cyclic), "transport_failure")
        self.assertEqual(probe.probe_once(Opener(error=cyclic)), "transport_failure")
        nested = TimeoutError("do not log internal URI")
        for _ in range(10):
            nested = urllib.error.URLError(nested)
        self.assertEqual(probe._exception_category(nested), "transport_failure")
        summary = probe.run_probe(lambda: probe._exception_category(cyclic),
                                  lambda _: None, attempts=1)
        serialized = json.dumps(probe.asdict(summary))
        self.assertNotIn("secret", serialized)
        self.assertNotIn("URL", serialized)
        self.assertFalse(summary.authorized)

    def test_unhashable_and_nonstring_probe_categories_fail_closed(self):
        for invalid in ([], {}, None, True, 4.2):
            with self.subTest(invalid=type(invalid).__name__):
                summary = probe.run_probe(lambda: invalid, lambda _: None, attempts=1)
                self.assertEqual(summary.last_failure_category, "transport_failure")
                self.assertEqual(summary.status, "unhealthy")
                self.assertFalse(summary.authorized)
                self.assertFalse(summary.mutations_performed)

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
        self.assertFalse(doc["transport_authenticated"])

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

    def test_unauthenticated_http_is_explicit_in_status_outputs(self):
        for status in ("healthy", "timeout"):
            with self.subTest(status=status):
                result = probe.run_probe(lambda: status, lambda _: None, attempts=1)
                self.assertFalse(result.transport_authenticated)
                self.assertFalse(result.authorized)
                self.assertIn('"transport_authenticated": false',
                              json.dumps(probe.asdict(result), sort_keys=True))
                text = probe.operator_markdown(result)
                self.assertIn("publiek HTTP zonder TLS", text)
                self.assertIn("niet cryptografisch bevestigd", text)

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
                             redirect=False, extra_headers=(), declared_length=None):
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
            self.send_header("Content-Length", str(len(payload)) if declared_length is None
                             else str(declared_length))
            for key, value in extra_headers:
                self.send_header(key, value)
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
        self._run("http_server_error", code=503)

    def test_actual_http_redirect_is_never_followed(self):
        self._run("http_redirect", redirect=True)

    def test_actual_http_wrong_media_type(self):
        self._run("content_type", media_type="text/html")

    def test_actual_http_conflicting_json_charset_is_not_healthy(self):
        self._run("content_type", media_type="application/json; charset=latin-1")
        self._run("content_type", media_type="application/json; charset=utf-8; profile=x")

    def test_actual_http_standard_utf8_json_charset_is_accepted(self):
        self._run("healthy", media_type="application/json; charset=UTF-8")

    def test_actual_http_oversize_stream_is_rejected(self):
        self._run("body_too_large", payload=b"A" * (probe.MAX_BYTES + 1024))

    def test_actual_http_bad_json(self):
        self._run("invalid_json", payload=b'{"time":')

    def test_actual_http_bad_schema(self):
        self._run("invalid_schema", payload=b'{"time":"2026-10-13T22:50:00Z"}')

    def test_actual_http_malformed_project_rows_are_not_healthy(self):
        for rows in ([None], [0], [["unexpected"]], [{"id": "cloud"}, "fake"]):
            with self.subTest(rows=rows):
                raw = json.dumps({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "projects": rows,
                }).encode("utf-8")
                self._run("invalid_schema", payload=raw)

    def test_actual_http_valid_project_objects_are_reachable(self):
        raw = json.dumps({
            "time": datetime.now(timezone.utc).isoformat(),
            "projects": [{"id": "cloud"}, {"id": "ftmo"}],
        }).encode("utf-8")
        self._run("healthy", payload=raw)

    def test_actual_http_duplicate_content_type_is_not_healthy(self):
        self._run("invalid_headers",
                  extra_headers=(("Content-Type", "application/json"),))

    def test_actual_http_duplicate_content_length_is_not_healthy(self):
        self._run("invalid_headers",
                  extra_headers=(("Content-Length", str(len(VALID))),))

    def test_actual_http_ambiguous_content_encoding_is_not_healthy(self):
        for coding in ("gzip", "", "identity, gzip"):
            with self.subTest(coding=coding):
                self._run("invalid_headers",
                          extra_headers=(("Content-Encoding", coding),))

    def test_actual_http_conflicting_transfer_framing_is_not_healthy(self):
        # An actual HTTP/1.1 response with both Content-Length and
        # Transfer-Encoding is ambiguous; urllib can otherwise hide the coding.
        for value in ("chunked", "gzip", "identity"):
            with self.subTest(transfer_encoding=value):
                self._run("invalid_headers",
                          extra_headers=(("Transfer-Encoding", value),))

    def test_actual_http_duplicate_transfer_codings_are_not_healthy(self):
        self._run("invalid_headers",
                  extra_headers=(("Transfer-Encoding", "chunked"),
                                 ("Transfer-Encoding", "gzip")))

    def test_actual_http_cache_age_zero_is_still_only_unauthenticated_reachability(self):
        self._run("healthy", extra_headers=(("Age", "0"),))

    def test_actual_http_nonzero_age_is_cache_evidence_not_health(self):
        for age in ("1", "180"):
            with self.subTest(age=age):
                self._run("cache_evidence", extra_headers=(("Age", age),))

    def test_actual_http_duplicate_age_is_denied(self):
        self._run("invalid_headers", extra_headers=(("Age", "0"), ("Age", "1")))

    def test_actual_http_explicit_cache_hit_is_denied(self):
        self._run("cache_evidence",
                  extra_headers=(("Cache-Status", "CDN; hit; ttl=30"),))
        self._run("healthy",
                  extra_headers=(("Cache-Status", "CDN; fwd=uri-miss"),))

    def test_actual_http_structured_boolean_cache_hit_not_green(self):
        for value, expected in (
            ("CDN; hit=?1", "cache_evidence"),
            ("CDN; hit=?0", "healthy"),
            ('Edge; detail="semi; hit"; fwd=uri-miss', "healthy"),
            ("Origin; fwd=uri-miss, CDN; hit=?1", "cache_evidence"),
            ("CDN; hit=?1; fwd=miss", "invalid_headers"),
            ("CDN; hit=?0; fwd=miss", "invalid_headers"),
            ('CDN; fwd="quoted"', "invalid_headers"),
        ):
            with self.subTest(value=value):
                self._run(expected, extra_headers=(("Cache-Status", value),))

    def test_actual_http_malformed_cache_member_does_not_claim_reachability(self):
        for value, expected in (
            ('"CDN Company Here"; hit', "cache_evidence"),
            ("CDN Name; hit", "invalid_headers"),
            ("CDN; hit; detail=MEMORY; detail=DISK", "invalid_headers"),
            ("CDN; fwd=miss; ext=value", "healthy"),
        ):
            with self.subTest(value=value):
                self._run(expected, extra_headers=(("Cache-Status", value),))

    def test_actual_http_revalidated_cache_status_does_not_claim_fresh_origin(self):
        for value, expected in (
            ("CDN; fwd=stale; fwd-status=304", "cache_evidence"),
            ("CDN; fwd=partial; fwd-status=206", "cache_evidence"),
            ("CDN; fwd=uri-miss; fwd-status=200", "healthy"),
            ("CDN; fwd-status=304", "invalid_headers"),
        ):
            with self.subTest(value=value):
                self._run(expected, extra_headers=(("Cache-Status", value),))

    def test_actual_http_malformed_cache_extension_does_not_appear_fresh(self):
        for value, expected in (
            ("CDN; fwd=miss; ext=bad value", "invalid_headers"),
            ("CDN; fwd=miss; ext=25", "healthy"),
            ('CDN; fwd=miss; ext="quoted; safe"', "healthy"),
        ):
            with self.subTest(value=value):
                self._run(expected, extra_headers=(("Cache-Status", value),))

    def test_actual_http_duplicate_cache_status_is_denied(self):
        self._run("invalid_headers",
                  extra_headers=(("Cache-Status", "CDN; fwd=miss"),
                                 ("Cache-Status", "CDN; hit")))

    def test_actual_http_claims_impossible_large_length(self):
        self._run("body_too_large", declared_length=probe.MAX_BYTES + 1)

    def test_actual_http_length_shorter_than_document(self):
        # An HTTP framing lie may truncate JSON before we can parse it.
        # Either an invalid framing, or unusable JSON, must be denied.
        with temporary_dashboard_http(declared_length=1) as (url, calls):
            with mock.patch.object(probe, "STATUS_URL", url):
                self.assertIn(probe.probe_once(), {"invalid_headers", "invalid_json"})
            self.assertEqual(calls, [("GET", "/api/status")])

    def test_actual_http_expired_snapshot_denied(self):
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=240)).isoformat()
        raw = json.dumps({"time": stamp, "projects": []}).encode("utf-8")
        self._run("stale_timestamp", payload=raw)


class DashboardManualMainLeaseTests(unittest.TestCase):
    """Execute real manual shell with a fake GitHub CLI, never touching VPS."""

    def _workflow_shell(self):
        path = MODULE.parents[1] / ".github/workflows/zcloud-dashboard-external-triage-manual-20261013-w1.yml"
        body = path.read_text(encoding="utf-8").split("        run: |\n", 1)[1]
        return textwrap.dedent(body)

    def _simulate(self, refs, expected_rc, *, output=None, probe_rc=0,
                  published=None, expected_reads=None):
        shell = self._workflow_shell()
        # Fake only the external probe; execute real bounded Git and real
        # publication shell logic in a disposable environment with no VPS.
        expected = ('env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 TZ=UTC timeout -k 2s 115s python3 '
                    'scripts/zcloud_dashboard_external_probe_20261013_w1.py '
                    '--attempts 6 --format markdown > "$summary_file" || probe_rc=$?')
        self.assertIn(expected, shell)
        shell = shell.replace(
            expected, 'printf "%s" "$FAKE_PROBE_OUTPUT" > "$summary_file"; '
                      'probe_rc="$FAKE_PROBE_RC"')
        # Execute the REAL standalone sanitizer even in fake-GitHub-shell tests.
        # Only the network probe and gh CLI are substituted.
        guard_cmd = "python3 scripts/zcloud_dashboard_summary_guard_20261014_w1.py"
        self.assertIn(guard_cmd, shell)
        shell = shell.replace(
            guard_cmd,
            "python3 " + str(MODULE.parents[0] / "zcloud_dashboard_summary_guard_20261014_w1.py"),
        )
        if output is None:
            output = probe.operator_markdown(
                probe.ProbeSummary("healthy", None, 1, False, {})
            ) + "\n"
        if published is None:
            published = expected_rc == 0
        current = "a" * 40
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            shim = root / "gh"
            shim.write_text(
                "#!/usr/bin/env python3\n"
                "import os, pathlib, sys\n"
                "expected=['api','--method','GET','repos/Zennay/zCloud/git/ref/heads/main','--jq','.object.sha']\n"
                "if sys.argv[1:] != expected: sys.exit(87)\n"
                "count=pathlib.Path(os.environ['FAKE_MAIN_COUNT'])\n"
                "n=int(count.read_text()) if count.exists() else 0\n"
                "count.write_text(str(n+1))\n"
                "refs=os.environ['FAKE_MAIN_REFS'].split(',')\n"
                "if n>=len(refs) or refs[n]=='ERROR': sys.exit(88)\n"
                "print(refs[n])\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)
            summary = root / "summary.md"
            env = dict(os.environ)
            env.update({
                "PATH": str(root) + os.pathsep + env.get("PATH", ""),
                "GITHUB_SHA": current,
                "GITHUB_STEP_SUMMARY": str(summary),
                "FAKE_MAIN_REFS": ",".join(refs),
                "FAKE_MAIN_COUNT": str(root / "calls"),
                "GH_TOKEN": "opaque-test-token-not-real",
                "FAKE_PROBE_OUTPUT": output,
                "FAKE_PROBE_RC": str(probe_rc),
            })
            proc = subprocess.run(["bash", "-c", shell], env=env, cwd=root,
                                  text=True, capture_output=True, timeout=10,
                                  check=False)
            self.assertEqual(proc.returncode, expected_rc, proc.stdout + proc.stderr)
            observed = summary.read_text() if summary.exists() else ""
            # No raw response, secrets, URL or implied recovery authority.
            self.assertNotIn("https://github.com/", observed)
            self.assertEqual(observed != "", published)
            # Preserve exact read-count proof across initial, post-probe and
            # post-sanitizer main checks without consulting production.
            if expected_reads is None:
                expected_reads = (1 if refs[0] != current else
                                  2 if (len(refs) < 2 or refs[1] != current or
                                        not published) else 3)
            self.assertEqual((root / "calls").read_text(), str(expected_reads))
            return observed

    def test_exact_head_checkout_and_bounded_main_transport_contract(self):
        hosted_path = MODULE.parents[1] / ".github/workflows/zcloud-dashboard-external-probe-hosted-20261013-w1.yml"
        hosted = hosted_path.read_text(encoding="utf-8")
        self.assertIn("head.repo.full_name == 'Zennay/zCloud'", hosted)
        self.assertIn("github.event.pull_request.head.sha || github.sha", hosted)
        self.assertIn("persist-credentials: false", hosted)
        pin = "uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
        self.assertIn(pin, hosted)
        self.assertNotIn("uses: actions/checkout@v4", hosted)
        manual = self._workflow_shell()
        self.assertIn("GH_HOST=github.com GH_PROMPT_DISABLED=1 timeout -k 2s 12s gh api --method GET repos/Zennay/zCloud/git/ref/heads/main --jq '.object.sha'", manual)
        workflow = (MODULE.parents[1] / ".github/workflows/zcloud-dashboard-external-triage-manual-20261013-w1.yml").read_text()
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn(pin, workflow)
        self.assertNotIn("uses: actions/checkout@v4", workflow)
        self.assertNotIn("git ls-remote", manual)
        self.assertNotIn("gh api --method POST", manual)
        self.assertIn('env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 TZ=UTC timeout -k 2s 115s python3 scripts/', manual)
        self.assertEqual(manual.count("          verify_main_head"), 0)
        self.assertEqual(manual.count("verify_main_head\n"), 3)
        self.assertLess(manual.index("verify_main_head\n"),
                        manual.index("summary_file="))
        self.assertLess(manual.rindex("verify_main_head\n"),
                        manual.index('cat "$verified_file"; printf'))

    def test_probe_subprocess_uses_minimal_allowlisted_environment(self):
        # The same env -i policy as the manual workflow strips arbitrary
        # inherited secrets, not just known GitHub token environment names.
        # This is entirely local and never invokes the public dashboard.
        names = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN",
                 "GITHUB_ENTERPRISE_TOKEN", "ZCLOUD_EXTERNAL_SECRET",
                 "UNRELATED_SERVICE_KEY", "HTTP_PROXY", "HOME")
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            for key in names:
                env[key] = "SECRET_FAKE_CREDENTIAL"
            proc = subprocess.run(
                ["env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin",
                 "LANG=C.UTF-8", "TZ=UTC", sys.executable, "-c",
                 "import json, os; print(json.dumps({"
                 "'sensitive': sorted(k for k in " + repr(names)
                 + " if k in os.environ), "
                 "'required': sorted(k for k in ('PATH','LANG','TZ') "
                 "if k in os.environ)}))"],
                env=env, cwd=tmp, capture_output=True, text=True,
                timeout=5, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(result["sensitive"], [])
            self.assertEqual(result["required"], ["LANG", "PATH", "TZ"])
            self.assertIn("PATH=/usr/local/bin:/usr/bin:/bin", proc.args)
            self.assertNotIn("SECRET_FAKE_CREDENTIAL", proc.stdout + proc.stderr)

    def test_workflow_bash_parses_without_network(self):
        result = subprocess.run(["bash", "-n"], input=self._workflow_shell(),
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_current_main_same_before_after_can_publish(self):
        observed = self._simulate(["a" * 40] * 3, 0)
        self.assertIn("**Status:** API bereikbaar", observed)
        self.assertIn("**Getoetste main-commit:** `" + "a" * 40 + "`", observed)

    def test_empty_success_output_never_green(self):
        self._simulate(["a" * 40, "a" * 40], 3, output="", published=False)

    def test_invalid_success_summary_never_green(self):
        self._simulate(["a" * 40, "a" * 40], 3,
                       output="unexpected public URL: SECRET_TOKEN\n", published=False)

    def test_duplicate_and_contradictory_statuses_never_green(self):
        valid = ("## zCloud · externe dashboardcontrole\n"
                 "**Status:** API bereikbaar\n"
                 "**Herstel of herstart wordt nooit automatisch uitgevoerd.**\n")
        for extra in ("**Status:** API bereikbaar\n",
                      "**Status:** API niet aantoonbaar bereikbaar\n"):
            with self.subTest(extra=extra):
                self._simulate(["a" * 40, "a" * 40], 3,
                               output=valid + extra, published=False)

    def test_missing_safety_disclaimer_never_green(self):
        self._simulate(["a" * 40, "a" * 40], 3,
                       output="## zCloud · externe dashboardcontrole\n"
                              "**Status:** API bereikbaar\n", published=False)

    def test_failure_exit_cannot_publish_healthy_summary(self):
        self._simulate(["a" * 40, "a" * 40], 3, probe_rc=1, published=False)

    def test_failure_summary_can_publish_but_job_remains_failed(self):
        body = probe.operator_markdown(
            probe.ProbeSummary("unhealthy", "timeout", 1, False, {"timeout": 1})
        ) + "\n"
        observed = self._simulate(["a" * 40] * 3, 1,
                                  output=body, probe_rc=1, published=True)
        self.assertIn("niet aantoonbaar", observed)

    def test_canonical_summary_guard_drops_injected_markdown(self):
        valid = probe.operator_markdown(
            probe.ProbeSummary("healthy", None, 1, False, {})
        ) + "\n"
        for injected in ("\\nSECRET_PRIVATE_LINK=http://internal.example",
                         "\\n**Status:** API bereikbaar",
                         "\\n## Unexpected heading",
                         "\\n- Unexpected additional diagnostics: secret"):
            with self.subTest(injection=injected):
                observed = valid + injected + "\n"
                self._simulate(["a" * 40, "a" * 40], 3,
                               output=observed, published=False)

    def test_canonical_summary_guard_drops_noncanonical_failure_receipt(self):
        fake_failure = ("## zCloud · externe dashboardcontrole\n"
                        "**Status:** API niet aantoonbaar bereikbaar\n"
                        "**Herstel of herstart wordt nooit automatisch uitgevoerd.**\n")
        self._simulate(["a" * 40, "a" * 40], 3,
                       output=fake_failure, probe_rc=1, published=False)

    def test_global_timeout_replaces_partial_sensitive_output(self):
        observed = self._simulate(["a" * 40] * 3, 124,
                                  output="SECRET_PARTIAL_RESPONSE_OR_URL",
                                  probe_rc=124, published=True)
        self.assertIn("totale tijdslimiet overschreden", observed)
        self.assertNotIn("SECRET_PARTIAL", observed)
        self.assertIn("API niet aantoonbaar bereikbaar", observed)

    def test_global_timeout_after_main_change_is_suppressed(self):
        self._simulate(["a" * 40, "b" * 40], 2,
                       output="SECRET_PARTIAL_RESPONSE_OR_URL",
                       probe_rc=124, published=False)

    def test_oversized_summary_is_rejected(self):
        body = ("## zCloud · externe dashboardcontrole\n"
                "**Status:** API bereikbaar\n" + "x" * 2200)
        self._simulate(["a" * 40, "a" * 40], 3, output=body, published=False)

    def test_main_moved_during_final_publication_preflight_denies_report(self):
        # First two immutable-main checks pass, but an independently moved
        # branch on the final read must suppress even sanitized green output.
        self._simulate(["a" * 40, "a" * 40, "b" * 40], 2,
                       published=False, expected_reads=3)

    def test_main_unverifiable_after_sanitizer_denies_report(self):
        self._simulate(["a" * 40, "a" * 40, "ERROR"], 2,
                       published=False, expected_reads=3)

    def test_failure_report_denied_if_main_changes_after_sanitizer(self):
        body = probe.operator_markdown(
            probe.ProbeSummary("unhealthy", "timeout", 1, False, {"timeout": 1})
        ) + "\n"
        self._simulate(["a" * 40, "a" * 40, "b" * 40], 2,
                       output=body, probe_rc=1,
                       published=False, expected_reads=3)

    def test_main_moved_during_probe_denies_summary(self):
        self._simulate(["a" * 40, "b" * 40], 2)

    def test_stale_initial_main_denies_probe_and_summary(self):
        self._simulate(["b" * 40], 2)

    def test_missing_main_ref_denies_probe_and_summary(self):
        self._simulate(["ERROR"], 2)


if __name__ == "__main__":
    unittest.main()
