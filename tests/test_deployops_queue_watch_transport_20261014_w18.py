"""Read-only hosted VPS queue-watch GitHub transport provenance tests.

No network, VPS, runner, queue write or workflow dispatch.
"""
from datetime import datetime, timezone
import json
import unittest

from scripts import zcloud_runner_queue_watch_20261014 as watcher


class FakeHeaders:
    def __init__(self, mime="application/json"):
        self.mime = mime

    def get_content_type(self):
        return self.mime


class FakeReply:
    def __init__(self, payload, url, mime="application/json"):
        self.payload = payload
        self.url = url
        self.headers = FakeHeaders(mime)

    def geturl(self):
        return self.url

    def read(self, limit):
        assert limit == 1024 * 1024 + 1
        return self.payload[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *ignored):
        return False


class FakeOpener:
    def __init__(self, payload=b'{"total_count":0}', *, url=None, mime="application/json"):
        self.payload = payload
        self.url = url
        self.mime = mime
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        return FakeReply(self.payload, self.url or request.full_url, self.mime)


class HostedQueueTransportTests(unittest.TestCase):
    def test_valid_finite_json_retains_normal_count_data(self):
        example = b'{"total_count":12,"load":1.25,"runs":[]}'
        parsed = watcher.strict_json(example)
        self.assertEqual(parsed["total_count"], 12)
        self.assertEqual(parsed["load"], 1.25)
        self.assertEqual(parsed["runs"], [])

    def test_invalid_nonfinite_and_overflowed_numbers_are_denied(self):
        for payload in (b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}',
                        b'{"x":1e10000}', b'{"x":-1e10000}',
                        b'{"nested":{"x":1e400}}'):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(watcher.Unavailable, "nonfinite"):
                    watcher.strict_json(payload)

    def test_ambiguous_keys_and_bad_json_also_refuse(self):
        for payload in (b'{"id":1,"id":2}', b'{"x":{"y":1,"y":2}}',
                        b'[]', b'null', b'{', b''):
            with self.subTest(payload=payload), self.assertRaises(watcher.Unavailable):
                watcher.strict_json(payload)
        with self.assertRaises(watcher.Unavailable):
            watcher.strict_json(b"x" * (1024 * 1024 + 1))

    def test_exact_canonical_response_url_is_mandatory(self):
        url = watcher.URL
        for final in ("https://evil.invalid/redirect",
                      "https://api.github.com/repos/another/repo/actions/runs",
                      url + "&extra=true"):
            opener = FakeOpener(url=final)
            with self.subTest(final=final), self.assertRaisesRegex(
                    watcher.Unavailable, "unexpected_final_url"):
                watcher._get(url, "sample-token", opener)
            self.assertEqual(opener.calls[-1], (url, 12))

    def test_mime_and_body_refused_before_runner_classification(self):
        for payload, mime, reason in ((b'{"x":1}', "text/html", "not_json"),
                                      (b'{"x":1,"x":2}', "application/json", "duplicate_key"),
                                      (b'{"x":1e10000}', "application/json", "nonfinite")):
            opener = FakeOpener(payload, mime=mime)
            with self.subTest(reason=reason), self.assertRaisesRegex(
                    watcher.Unavailable, reason):
                watcher._get(watcher.URL, "sample-token", opener)

    def test_canonical_json_response_remains_read_only(self):
        opener = FakeOpener(b'{"total_count":1,"workflow_runs":[]}')
        result = watcher._get(watcher.URL, "sample-token", opener)
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(opener.calls, [(watcher.URL, 12)])

    def test_redirect_handler_is_fail_closed(self):
        with self.assertRaisesRegex(watcher.Unavailable, "redirect_denied"):
            watcher.NoRedirect().redirect_request(None, None, 302, "redirect",
                                                  {}, "https://evil.invalid")


if __name__ == "__main__":
    unittest.main()
