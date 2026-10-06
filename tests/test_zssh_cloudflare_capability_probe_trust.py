from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zssh-cloudflare-capability-probe.yml"


class ZsshCloudflareCapabilityProbeTrustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pull_requests_use_hosted_validation_only(self) -> None:
        text = self.text
        self.assertIn("pull_request:", text)
        self.assertIn("github.event_name == 'pull_request'", text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", text)
        self.assertIn(
            "python3 -m unittest tests/test_zssh_cloudflare_capability_probe_trust.py",
            text,
        )
        self.assertIn("github.event_name != 'pull_request'", text)

    def test_live_probe_is_main_only_exact_revision_and_permanent_vps_bound(self) -> None:
        text = self.text
        live = text[text.index("\n  probe:\n") :]
        self.assertIn("github.repository == 'Zennay/zCloud'", live)
        self.assertIn("github.ref == 'refs/heads/main'", live)
        self.assertIn(
            "(github.event_name != 'workflow_dispatch' || github.actor == 'Zennay')",
            live,
        )
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", live)
        self.assertNotIn("runs-on: self-hosted", live)
        self.assertIn("ZCLOUD_EXPECTED_SHA: ${{ github.sha }}", live)
        self.assertRegex(
            live,
            r"uses: actions/checkout@[0-9a-f]{40} # v7\.0\.1",
        )
        self.assertIn("ref: ${{ env.ZCLOUD_EXPECTED_SHA }}", live)
        self.assertIn("persist-credentials: false", live)
        self.assertIn("clean: true", live)
        self.assertIn("fetch-depth: 1", live)
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "$ZCLOUD_EXPECTED_SHA"',
            live,
        )
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", live)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', live)
        self.assertIn('test "$(id -un)" = "ubuntu"', live)
        self.assertIn('test "$(id -u)" -ne 0', live)

        exact_revision = live.index('test "$(git rev-parse HEAD)" = "$ZCLOUD_EXPECTED_SHA"')
        runner_guard = live.index("scripts/zcloud_vps_runner_guard.py --json")
        capability_probe = live.index("python3 scripts/zssh_cloudflare_capability_probe.py")
        self.assertLess(exact_revision, runner_guard)
        self.assertLess(runner_guard, capability_probe)

    def test_pull_request_has_safe_permanent_vps_contract_proof(self) -> None:
        text = self.text
        proof = text[text.index("\n  prove:\n") : text.index("\n  probe:\n")]
        self.assertIn("name: Prove trust contract on permanent VPS", proof)
        self.assertIn("needs: validate", proof)
        self.assertIn("github.event_name == 'pull_request'", proof)
        self.assertIn("github.actor == 'Zennay'", proof)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", proof)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", proof)
        self.assertIn("ZCLOUD_EXPECTED_SHA: ${{ github.event.pull_request.head.sha }}", proof)
        self.assertIn("ref: ${{ env.ZCLOUD_EXPECTED_SHA }}", proof)
        self.assertIn('test "$(git rev-parse HEAD)" = "$ZCLOUD_EXPECTED_SHA"', proof)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json >/dev/null", proof)
        self.assertIn("tests/test_zssh_cloudflare_capability_probe.py", proof)
        self.assertIn("tests/test_zssh_cloudflare_capability_probe_trust.py", proof)
        self.assertNotIn("python3 scripts/zssh_cloudflare_capability_probe.py", proof)

    def test_pr_validation_cannot_cancel_live_probe(self) -> None:
        text = self.text
        self.assertIn("zssh-cloudflare-capability-probe-${{", text)
        self.assertIn("format('pr-{0}', github.event.pull_request.number)", text)
        self.assertIn("|| 'live'", text)
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", text)

    def test_probe_preserves_read_only_cloudflare_semantics(self) -> None:
        text = self.text
        self.assertIn("Probe persistent Cloudflare authorization without mutation", text)
        for forbidden in (
            "api.cloudflare.com/client/v4",
            "cloudflare.com/client/v4",
            "curl -X POST",
            "curl -X PUT",
            "curl -X PATCH",
            "curl -X DELETE",
            "systemctl restart",
            "systemctl start",
            "systemctl stop",
            "sudo tee",
        ):
            self.assertNotIn(forbidden, text)

    def test_remote_actions_are_immutable_and_evidence_is_bounded(self) -> None:
        text = self.text
        action_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith("uses:")
        ]
        self.assertGreaterEqual(len(action_lines), 3)
        for line in action_lines:
            self.assertRegex(line, r"^uses: [^@]+@[0-9a-f]{40}(?: # .+)?$")
        self.assertIn("Upload non-secret capability evidence", text)
        self.assertIn("if-no-files-found: error", text)
        self.assertNotIn("set -x", text)


if __name__ == "__main__":
    unittest.main()
