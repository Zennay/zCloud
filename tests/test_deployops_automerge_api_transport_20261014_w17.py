"""Hermetic security tests for GitHub write-token API transport.

Only fake in-memory responses. No GitHub API access, VPS access or mutations.
"""
from email.message import Message
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scripts import zcloud_autonomous_pr_integrator as engine


class FakeResponse:
    def __init__(self, payload, url=None, content_type="application/json"):
        self.payload = payload
        self.url = url
        self.headers = Message()
        self.headers.add_header("Content-Type", content_type)

    def geturl(self):
        return self.url

    def read(self, n):
        return self.payload[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeOpener:
    def __init__(self, body=b'{"commit":{"sha":"abc"}}', *, other_url=None,
                 content_type="application/json", exc=None):
        self.body = body
        self.other_url = other_url
        self.content_type = content_type
        self.exc = exc
        self.calls = []
        self.handlers = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.exc:
            raise self.exc
        return FakeResponse(self.body, self.other_url or request.full_url,
                            self.content_type)


class SecureGitHubApiTransportTests(unittest.TestCase):
    def test_strict_json_allows_valid_representative_github_shapes(self):
        for data in ({}, {"name": "test", "num": 9, "meta": {"ok": True}},
                     [{"number": 7}, {"number": 9}]):
            with self.subTest(data=data):
                encoded = json.dumps(data).encode("utf-8")
                self.assertEqual(engine.strict_github_json(encoded), data)

    def test_duplicate_nested_and_top_level_keys_fail_closed(self):
        for raw in (b'{"pr":1,"pr":2}', b'{"outer":{"sha":"one","sha":"two"}}',
                    b'[{"name":"a","name":"b"}]'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(engine.Refused, "duplicate_key"):
                    engine.strict_github_json(raw)

    def test_invalid_or_ambiguous_json_shapes_fail_closed(self):
        bad = (b'null', b'true', b'12', b'"string"', b'NaN', b'{"score":Infinity}',
               b'{"score":-Infinity}', b'{"score":1e10000}',
               b'{"score":-1e10000}', b'{', b'\\xff\\xfe', b'')
        for raw in bad:
            with self.subTest(raw=raw):
                with self.assertRaises(engine.Refused):
                    engine.strict_github_json(raw)
        with self.assertRaisesRegex(engine.Refused, "response_oversize"):
            engine.strict_github_json(b"x" * (1024 * 1024 + 1))

    def test_valid_finite_fraction_is_preserved(self):
        self.assertEqual(engine.strict_github_json(b'{"fraction":1.25}'),
                         {"fraction": 1.25})

    def test_redirect_request_is_denied_without_following(self):
        h = engine.NoGitHubRedirect()
        with self.assertRaisesRegex(engine.Refused, "github_api_redirect_denied"):
            h.redirect_request(None, None, 307, "redirect", {},
                               "https://attacker.invalid/token")
        with self.assertRaisesRegex(engine.Refused, "github_api_redirect_denied"):
            h.redirect_request(None, None, 302, "redirect", {},
                               "https://api.github.com/other")

    def test_real_api_request_shape_uses_bounded_no_redirect_opener(self):
        fake = FakeOpener()
        seen = []
        def factory(*handlers):
            seen.extend(handlers)
            return fake
        with patch.object(engine, "build_opener", side_effect=factory):
            response = engine.api("write-capable-secret", "GET", "/branches/main")
            self.assertEqual(response, {"commit": {"sha": "abc"}})
            self.assertEqual(len(fake.calls), 1)
            request, timeout = fake.calls[0]
            self.assertEqual(request.full_url,
                             "https://api.github.com/repos/Zennay/zCloud/branches/main")
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.get_header("Authorization"),
                             "Bearer write-capable-secret")
            self.assertEqual(timeout, 15)
            self.assertTrue(any(isinstance(h, engine.NoGitHubRedirect) for h in seen))

    def test_sha_bound_put_keeps_exact_repository_and_json_body(self):
        fake = FakeOpener(body=b'{"sha":"' + b"a" * 40 + b'","merged":true}')
        with patch.object(engine, "build_opener", return_value=fake):
            resp = engine.api("secret", "PUT", "/pulls/123/merge",
                              {"sha": "b" * 40, "merge_method": "squash"})
        self.assertTrue(resp["merged"])
        request, _ = fake.calls[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(request.full_url,
                         "https://api.github.com/repos/Zennay/zCloud/pulls/123/merge")
        self.assertEqual(json.loads(request.data)["sha"], "b" * 40)

    def test_wrong_final_url_and_wrong_content_type_are_not_trusted(self):
        cases = (
            {"other_url": "https://evil.invalid/redirect"},
            {"other_url": "https://api.github.com/repos/Zennay/zCloud/other"},
            {"content_type": "text/html"},
            {"content_type": "application/problem+json"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with patch.object(engine, "build_opener",
                                  return_value=FakeOpener(**changes)):
                    with self.assertRaisesRegex(engine.Refused, "untrusted_response"):
                        engine.api("secret-token", "GET", "/branches/main")

    def test_duplicate_json_and_oversized_http_body_never_authorize(self):
        for body in (b'{"sha":"good","sha":"bad"}',
                     b'{"ok":NaN}', b'<' + b"x" * (1024 * 1024 + 1)):
            with self.subTest(size=len(body)):
                with patch.object(engine, "build_opener",
                                  return_value=FakeOpener(body=body)):
                    with self.assertRaises(engine.Refused):
                        engine.api("secret-token", "GET", "/branches/main")

    def test_http_and_transport_errors_are_bounded_without_token_echo(self):
        url = "https://api.github.com/repos/Zennay/zCloud/branches/main"
        for err in (HTTPError(url, 302, "redirect", None, None),
                    URLError("network down"), TimeoutError("timeout"),
                    OSError("io-error")):
            with self.subTest(error=type(err).__name__):
                with patch.object(engine, "build_opener",
                                  return_value=FakeOpener(exc=err)):
                    with self.assertRaisesRegex(engine.Refused,
                                                "github_api_request_failed") as caught:
                        engine.api("secret-token", "GET", "/branches/main")
                    self.assertNotIn("secret-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
