"""Fail-closed URL syntax regression vectors; no network, deploy or VPS calls."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deploy_health_url_syntax_offline_w26 import screen_health_url, live_recovery_authorized

HOSTS = frozenset({"dashboard.example.org"})

class DeployHealthUrlOfflineTests(unittest.TestCase):
    def check_deny(self, *urls):
        for url in urls:
            with self.subTest(url=url):
                self.assertFalse(screen_health_url(url, HOSTS))

    def test_canonical_health_url(self):
        self.assertTrue(screen_health_url("https://dashboard.example.org/health", HOSTS))

    def test_explicit_default_tls_port(self):
        self.assertTrue(screen_health_url("https://dashboard.example.org:443/health", HOSTS))

    def test_scheme_and_port(self):
        self.check_deny("http://dashboard.example.org/health",
                        "https://dashboard.example.org:444/health",
                        "//dashboard.example.org/health")

    def test_injected_credentials(self):
        self.check_deny("https://user@dashboard.example.org/health",
                        "https://user:pass@dashboard.example.org/health")

    def test_path_and_suffix(self):
        self.check_deny("https://dashboard.example.org/",
                        "https://dashboard.example.org/health/",
                        "https://dashboard.example.org/health/../health",
                        "https://dashboard.example.org/health%2f")

    def test_query_and_fragment(self):
        self.check_deny("https://dashboard.example.org/health?token=secret",
                        "https://dashboard.example.org/health#fragment")

    def test_host_confusion(self):
        self.check_deny("https://dashboard.example.org.evil.test/health",
                        "https://evil.test/health",
                        "https://dashboard.example.org./health",
                        "https://DASHBOARD.example.org/health")

    def test_whitespace_controls(self):
        self.check_deny(" https://dashboard.example.org/health",
                        "https://dashboard.example.org/health\n",
                        "https://dashboard.example.org/health\t")

    def test_invalid_types(self):
        for item in (None, 42, {}, [], b"https://dashboard.example.org/health"):
            self.assertFalse(screen_health_url(item, HOSTS))

    def test_unconfigured_allowlist(self):
        self.assertFalse(screen_health_url("https://dashboard.example.org/health", frozenset()))
        self.assertFalse(screen_health_url("https://dashboard.example.org/health", {"dashboard.example.org"}))
        self.assertFalse(screen_health_url("https://dashboard.example.org/health", frozenset({"Dashboard.example.org"})))

    def test_local_ip_denied_even_when_allowed(self):
        self.assertFalse(screen_health_url("https://127.0.0.1/health", frozenset({"127.0.0.1"})))

    def test_never_authorizes_live_recovery(self):
        self.assertFalse(live_recovery_authorized(True, url="https://dashboard.example.org/health"))

if __name__ == "__main__":
    unittest.main()
