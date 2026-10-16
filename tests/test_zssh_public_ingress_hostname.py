"""Regression coverage for the selected zSSH production ingress hostname."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zssh-public-ingress-bootstrap.yml"


class ZsshPublicIngressHostnameTests(unittest.TestCase):
    def test_ingress_uses_owned_zennay_subdomain_and_expected_vps(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("ZSSH_PRODUCTION_ORIGIN: https://zssh.zennay.nl", text)
        self.assertIn("ZSSH_PRODUCTION_HOST: zssh.zennay.nl", text)
        self.assertIn("ZSSH_EXPECTED_IPV4: 198.244.191.182", text)
        self.assertIn('ZSSH_PUBLIC_GATEWAY_PORT: "8789"', text)
        self.assertNotIn("zssh.cheapgpt.shop", text)

    def test_ingress_remains_explicit_owner_gated_and_sha_bound(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for marker in (
            "workflow_dispatch:",
            "docker inspect zssh-https",
            "ZSSH_DOCKER_CADDY_TOPOLOGY_UNSUPPORTED",
            "expected_zssh_sha:",
            "confirm_sha:",
            "INSTALL_ZSSH_PUBLIC_INGRESS",
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
            'test "$GITHUB_SHA" = "$CONFIRM_SHA"',
            "git ls-remote https://github.com/Zennay/zCloud.git refs/heads/main",
            "systemctl --user is-active --quiet zssh-public.service",
            "install-public-caddy.sh",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_ingress_preserves_transactional_tls_and_pairing_checks(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for marker in (
            "ZSSH_PUBLIC_INGRESS_DNS_GREEN",
            "ZSSH_PUBLIC_GATEWAY_LOCAL_GREEN",
            "ZSSH_PUBLIC_CADDY_PROMOTION_GREEN",
            "ZSSH_PUBLIC_TLS_LOCAL_GREEN",
            "ZSSH_PUBLIC_INGRESS_BOOTSTRAP_GREEN",
            "ZSSH_TARGET_AGENT_CONTROL_GREEN",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
