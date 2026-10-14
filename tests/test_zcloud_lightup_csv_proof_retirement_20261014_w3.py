"""Offline-only source contract for retired historical LightUp CSV proof workflows."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
DOCUMENT = ROOT / "docs" / "deployops-lightup-csv-proof-retirement-20261014-w3.md"
PROOF = WORKFLOWS / "zcloud-lightup-csv-proof-retirement-hosted-20261014-w3.yml"

RETIRED = {
    956: "e782012b48eb87a201eeac3edf79b4e1d7f027cb",
    959: "f26cd5ff4e3ca941af62ccf6e539a6d084297898",
    960: "5e86335eac4177bcc261fea28df38b18465daf98",
    962: "2e4898c9383cc622f9f5b0e1a87c5152bb7956cc",
}
PRESERVED = "lightup-pr963-exact-head-proof.yml"
PRESERVED_SHA = "d5734836114b5bbc9271198b3bc36aacdb2a04e7"


class LightUpCsvProofRetirementTests(unittest.TestCase):
    def test_all_four_merged_pr_runner_entrypoints_are_gone(self):
        for pr in RETIRED:
            with self.subTest(pr=pr):
                self.assertFalse((WORKFLOWS / f"lightup-pr{pr}-exact-head-proof.yml").exists())

    def test_removed_pr_sha_is_not_used_by_other_active_workflows(self):
        for item in WORKFLOWS.iterdir():
            if item.suffix.lower() not in (".yml", ".yaml"):
                continue
            text = item.read_text(encoding="utf-8")
            for pr, sha in RETIRED.items():
                with self.subTest(workflow=item.name, pr=pr):
                    self.assertNotIn(sha, text)

    def test_open_lightup_pr963_proof_is_left_untouched(self):
        preserved = WORKFLOWS / PRESERVED
        self.assertTrue(preserved.is_file())
        source = preserved.read_text(encoding="utf-8")
        self.assertIn(PRESERVED_SHA, source)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", source)

    def test_old_cancellation_workflow_remains_out_of_scope(self):
        path = WORKFLOWS / "lightup-export-obsolete-proof-cleanup.yml"
        self.assertTrue(path.is_file())
        self.assertIn("lightup-pr960-exact-head-proof.yml", path.read_text(encoding="utf-8"))

    def test_retirement_provenance_is_recorded_and_source_only(self):
        doc = DOCUMENT.read_text(encoding="utf-8")
        self.assertIn("566f7663be12ea13c1cabdd43d6501dd9cad03a6", doc)
        for pr, sha in RETIRED.items():
            with self.subTest(pr=pr):
                self.assertIn(f"#{pr}", doc)
                self.assertIn(sha, doc)
        self.assertIn("#963", doc)
        self.assertIn("does not cancel existing", doc)

    def test_new_proof_is_github_hosted_only_and_no_privileged_gate(self):
        source = PROOF.read_text(encoding="utf-8")
        self.assertIn("  pull_request:", source)
        self.assertIn("runs-on: ubuntu-latest", source)
        self.assertIn("permissions:\n  contents: read", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", source)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_HEAD"', source)
        for forbidden in (
            "workflow_dispatch:", "workflow_run:", "\n  push:",
            "runs-on: [self-hosted", "actions: write", "contents: write",
            "sudo ", "systemctl ", "gh api --method", "git push",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
