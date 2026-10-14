"""Offline-only guard that keeps obsolete FTMO PR408 proof authority retired."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED_NAME = "ftmo-pr408-provider-boundary-proof.yml"
RETIRED_SHA = "98c033b2b571cea2313f20bee31820286132226f"
NEW_PROOF = WORKFLOWS / "zcloud-ftmo-pr408-retirement-hosted-20261014-w2.yml"
RUNBOOK = ROOT / "docs" / "deployops-ftmo-pr408-proof-retirement-20261014-w2.md"


class RetiredFtmoPR408ProofTests(unittest.TestCase):
    def test_legacy_vps_entrypoint_is_absent(self):
        self.assertFalse((WORKFLOWS / RETIRED_NAME).exists())
        self.assertEqual([], list(WORKFLOWS.glob("ftmo-pr408-*")))

    def test_old_exact_sha_is_not_reachable_from_an_active_workflow(self):
        for path in WORKFLOWS.glob("*.y*ml"):
            with self.subTest(path=path.name):
                self.assertNotIn(RETIRED_SHA, path.read_text(encoding="utf-8"))

    def test_retirement_evidence_preserves_exact_provenance(self):
        doc = RUNBOOK.read_text(encoding="utf-8")
        for marker in (
            RETIRED_NAME, RETIRED_SHA, "Zennay/Ftmo/pull/408",
            "eac3d115741a60c9bb9004636d69b34a3ca81ed2",
            "2b8c7c457ba62f724dae89c0765a72b6ce9b90f1",
            "8b0f91478b9d6024dcf35f207ad2a1c784d3170c",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, doc)

    def test_new_proof_can_only_run_on_github_hosted_pr_jobs(self):
        src = NEW_PROOF.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", src)
        self.assertIn("  pull_request:", src)
        self.assertIn("permissions:\n  contents: read", src)
        self.assertIn("persist-credentials: false", src)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", src)
        self.assertIn("test \"$(git rev-parse HEAD)\" = \"$EXPECTED_HEAD\"", src)
        for dangerous in (
            "workflow_dispatch:", "workflow_run:", "  push:",
            "runs-on: [self-hosted", "sudo ", "systemctl ", "gh api --method",
            "actions: write", "contents: write",
        ):
            with self.subTest(dangerous=dangerous):
                self.assertNotIn(dangerous, src)


if __name__ == "__main__":
    unittest.main()
