import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_self_hosted_workflow_trust_inventory import inventory


WORKFLOW = Path(".github/workflows/zcloud-self-hosted-workflow-trust-inventory.yml")


class SelfHostedWorkflowTrustInventoryTests(unittest.TestCase):
    def _write(self, root: Path, name: str, content: str) -> Path:
        path = root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_classifies_generic_and_labeled_self_hosted_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "sample.yml",
                """
name: sample
on:
  pull_request:
jobs:
  generic:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
  hardened:
    if: github.event_name != 'pull_request' || (github.actor == 'Zennay' && github.event.pull_request.head.repo.full_name == github.repository)
    runs-on: [self-hosted, zcloud, vps]
    env:
      EXPECTED_SHA: ${{ github.event.pull_request.head.sha || github.sha }}
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803
        with:
          ref: ${{ env.EXPECTED_SHA }}
          persist-credentials: false
      - run: python3 scripts/zcloud_vps_runner_guard.py --json
""",
            )

            report = inventory(root)
            self.assertEqual(report["counts"]["self_hosted_jobs"], 2)
            self.assertEqual(report["counts"]["generic_self_hosted_jobs"], 1)
            self.assertEqual(report["counts"]["labeled_self_hosted_jobs"], 1)

            generic, hardened = report["jobs"]
            self.assertEqual(generic["job"], "generic")
            self.assertIn("generic_self_hosted_runner", generic["findings"])
            self.assertIn(
                "pr_self_hosted_without_owner_same_repo_guard", generic["findings"]
            )
            self.assertIn("floating_checkout_action", generic["findings"])
            self.assertIn(
                "checkout_credentials_not_explicitly_disabled", generic["findings"]
            )
            self.assertIn("checkout_exact_ref_not_evident", generic["findings"])

            self.assertEqual(hardened["job"], "hardened")
            self.assertEqual(hardened["findings"], [])
            self.assertTrue(hardened["owner_same_repo_guard"])
            self.assertTrue(hardened["runner_guard"])

    def test_multiple_checkouts_fail_closed_if_any_checkout_is_weak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "multi.yml",
                """
name: multi
on:
  workflow_dispatch:
jobs:
  test:
    runs-on: [self-hosted, zcloud, vps]
    steps:
      - uses: actions/checkout@0123456789012345678901234567890123456789
        with:
          ref: ${{ github.sha }}
          persist-credentials: false
      - uses: actions/checkout@v4
      - run: python3 scripts/zcloud_vps_runner_guard.py --json
""",
            )
            report = inventory(root)
            findings = report["jobs"][0]["findings"]
            self.assertIn("floating_checkout_action", findings)
            self.assertIn("checkout_credentials_not_explicitly_disabled", findings)
            self.assertIn("checkout_exact_ref_not_evident", findings)

    def test_inline_pull_request_trigger_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "inline.yml",
                """
name: inline
on: [pull_request, workflow_dispatch]
jobs:
  test:
    runs-on: self-hosted
""",
            )
            report = inventory(root)
            self.assertTrue(report["jobs"][0]["pull_request_trigger"])
            self.assertIn(
                "pr_self_hosted_without_owner_same_repo_guard",
                report["jobs"][0]["findings"],
            )

    def test_zcloud_vps_without_runner_guard_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "vps.yml",
                """
name: vps
on:
  workflow_dispatch:
jobs:
  probe:
    runs-on: [self-hosted, zcloud, vps]
    steps:
      - uses: actions/checkout@0123456789012345678901234567890123456789
        with:
          ref: ${{ github.sha }}
          persist-credentials: false
""",
            )
            report = inventory(root)
            self.assertIn(
                "zcloud_vps_runner_guard_not_evident",
                report["jobs"][0]["findings"],
            )

    def test_hosted_jobs_are_not_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "hosted.yml",
                """
name: hosted
on:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
""",
            )
            report = inventory(root)
            self.assertEqual(report["counts"]["self_hosted_jobs"], 0)
            self.assertEqual(report["jobs"], [])

    def test_output_is_deterministic_and_json_serializable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "z.yml", "jobs:\n  z:\n    runs-on: self-hosted\n")
            self._write(
                root,
                "a.yml",
                "jobs:\n  a:\n    runs-on: [self-hosted, custom]\n",
            )
            report = inventory(root)
            self.assertEqual(
                [(item["workflow"], item["job"]) for item in report["jobs"]],
                [("a.yml", "a"), ("z.yml", "z")],
            )
            json.dumps(report, sort_keys=True)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_rejects_symlink_workflow_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workflows"
            root.mkdir()
            target = base / "outside.yml"
            target.write_text(
                "jobs:\n  test:\n    runs-on: self-hosted\n",
                encoding="utf-8",
            )
            (root / "linked.yml").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                inventory(root)

    def test_inventory_workflow_is_hosted_read_only_and_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', text)
        self.assertNotIn("runs-on: self-hosted", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("systemctl ", text)
        self.assertIn("ZCLOUD_SELF_HOSTED_TRUST_COUNTS=", text)
        self.assertIn("ZCLOUD_SELF_HOSTED_TRUST_FINDINGS=", text)
        self.assertIn("ZCLOUD_SELF_HOSTED_TRUST_INVENTORY_GREEN=1", text)


if __name__ == "__main__":
    unittest.main()
