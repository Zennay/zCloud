from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zssh-production-dns-proof.yml"


class ZsshProductionDnsProofWorkflowTests(unittest.TestCase):
    def test_hosted_dns_proof_keeps_least_privilege_and_immutable_remote_actions(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("pull_request:", text)
        self.assertIn("branches: [main]", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("socket.getaddrinfo", text)
        self.assertIn("ZSSH_EXPECTED_IPV4", text)
        self.assertIn("Upload non-secret DNS evidence", text)

        remote_uses = re.findall(
            r"uses:\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([^\s#]+)",
            text,
        )
        self.assertTrue(remote_uses)
        for action, ref in remote_uses:
            self.assertRegex(ref, r"^[0-9a-f]{40}$", f"{action} must be immutable")

        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1",
            text,
        )
        self.assertNotIn("actions/upload-artifact@v4", text)
        self.assertNotIn("runs-on: self-hosted", text)
        self.assertNotIn("runs-on: [self-hosted", text)


if __name__ == "__main__":
    unittest.main()
