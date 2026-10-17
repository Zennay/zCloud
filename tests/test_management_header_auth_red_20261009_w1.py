"""Red contract for unauthenticated remote management-request header spoofing.

Reads only the action_request_allowed function AST from server.py, no server
import, HTTP listener, production database, VPS or management token. The two
negative tests intentionally FAIL on main until the serialized server.py owner
can install and accept an authenticated non-loopback write boundary.

Run: python3 -m unittest -v tests.test_management_header_auth_red_20261009_w1
"""
import ast
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def isolated_management_guard(allowlist_path):
    """Load the real guard implementation without executing the server module."""
    source = (ROOT / "server.py").read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(ROOT / "server.py"))
    matches = [
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "action_request_allowed"
    ]
    if len(matches) != 1:
        raise AssertionError("expected one canonical action_request_allowed")
    isolated = ast.Module(body=matches, type_ignores=[])
    namespace = {"ACTION_ALLOW_FILE": allowlist_path}
    exec(compile(ast.fix_missing_locations(isolated), str(ROOT / "server.py"), "exec"), namespace)
    return namespace["action_request_allowed"]


class FakeHandler:
    def __init__(self, address, headers=None):
        self.client_address = (address, 65432)
        self.headers = headers or {}


class ManagementHeaderAuthRedContract(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="zcloud-origin-auth-fixture-")
        self.addCleanup(temp.cleanup)
        self.allowlist = Path(temp.name) / "allowed-ips"
        self.allowed = isolated_management_guard(self.allowlist)

    def test_untrusted_remote_without_claims_is_denied(self):
        self.assertFalse(self.allowed(FakeHandler("203.0.113.40")))

    def test_untrusted_remote_with_cross_origin_claim_is_denied(self):
        self.assertFalse(self.allowed(FakeHandler("203.0.113.40", {
            "Host": "dashboard.example",
            "Origin": "https://other.example",
            "Sec-Fetch-Site": "cross-site",
        })))

    def test_untrusted_remote_cannot_become_loopback_via_proxy_headers(self):
        self.assertFalse(self.allowed(FakeHandler("203.0.113.40", {
            "X-Forwarded-For": "127.0.0.1",
            "Host": "dashboard.example",
        })))

    def test_actual_loopback_remains_allowed(self):
        self.assertTrue(self.allowed(FakeHandler("127.0.0.1")))
        self.assertTrue(self.allowed(FakeHandler("::1")))

    def test_explicit_trusted_ip_allowlist_remains_allowed(self):
        self.allowlist.write_text("192.0.2.40\n", encoding="utf-8")
        self.assertTrue(self.allowed(FakeHandler("192.0.2.40")))

    def test_remote_client_cannot_self_assert_trusted_origin(self):
        """RED until server.py requires non-forgeable evidence, not just headers."""
        self.assertFalse(self.allowed(FakeHandler("203.0.113.40", {
            "Host": "dashboard.example",
            "Origin": "https://dashboard.example",
            "Sec-Fetch-Site": "same-origin",
        })))

    def test_remote_client_cannot_self_assert_trusted_referer(self):
        """RED: HTTP Referer and Fetch Metadata are not remote authentication."""
        self.assertFalse(self.allowed(FakeHandler("203.0.113.40", {
            "Host": "dashboard.example",
            "Referer": "https://dashboard.example/controls",
            "Sec-Fetch-Site": "same-site",
        })))


if __name__ == "__main__":
    unittest.main()
