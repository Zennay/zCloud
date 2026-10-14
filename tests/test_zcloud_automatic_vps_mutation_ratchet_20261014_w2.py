"""Isolated source tests for the GitHub-hosted, deny-first deploy-ops ratchet."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_automatic_vps_mutation_ratchet_20261014_w2 as guard

OLD = """name: Legacy
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: [self-hosted, zcloud, vps]
    steps:
      - run: sudo systemctl restart zennay-cloud.service
"""
SAFE = """name: Safe
on:
  pull_request:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: python3 -m unittest discover
"""
DANGEROUS_MANUAL = """name: Owner manual
on:
  workflow_dispatch:
jobs:
  deploy:
    runs-on: [self-hosted, zcloud, vps]
    steps:
      - run: sudo systemctl restart zennay-cloud.service
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        capture_output=True, text=True,
    ).stdout.strip()


class RatchetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workflows = self.root / ".github/workflows"
        self.workflows.mkdir(parents=True)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "safe@example.invalid")
        git(self.root, "config", "user.name", "CI fixture")

    def save(self, name: str, value: str):
        (self.workflows / name).write_text(value, encoding="utf-8")

    def baseline(self) -> str:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "base")
        return git(self.root, "rev-parse", "HEAD")

    def test_legacy_automatic_mutation_unchanged_is_grandfathered(self):
        self.save("legacy.yml", OLD)
        sha = self.baseline()
        receipt = guard.check(self.root, sha)
        self.assertTrue(receipt["safe_to_review"])
        self.assertEqual(1, receipt["baseline_exposure_count"])
        self.assertEqual(0, receipt["new_exposure_count"])
        self.assertIs(receipt["mutation_authorized"], False)
        self.assertIs(receipt["production_verified"], False)

    def test_new_automatic_mutation_is_denied(self):
        self.save("normal.yml", SAFE)
        sha = self.baseline()
        self.save("new.yml", OLD)
        receipt = guard.check(self.root, sha)
        self.assertEqual([".github/workflows/new.yml"], receipt["new_exposure_paths"])
        self.assertFalse(receipt["safe_to_review"])

    def test_modified_automatic_mutation_requires_review_even_if_existing(self):
        self.save("legacy.yml", OLD)
        sha = self.baseline()
        self.save("legacy.yml", OLD + "\n# source changed\n")
        self.assertEqual(1, guard.check(self.root, sha)["new_exposure_count"])

    def test_new_manual_only_mutation_not_misreported_as_automatic(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        self.save("manual.yml", DANGEROUS_MANUAL)
        self.assertTrue(guard.check(self.root, sha)["safe_to_review"])

    def test_new_hosted_only_push_workflow_is_not_vps_mutation(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        self.save("hosted.yml", OLD.replace("self-hosted, zcloud, vps", "ubuntu-latest"))
        self.assertTrue(guard.check(self.root, sha)["safe_to_review"])

    def test_new_scheduled_self_hosted_restart_denied(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        self.save("scheduled.yml", OLD.replace("push:\n    branches: [main]", "schedule:\n    - cron: '0 * * * *'"))
        self.assertFalse(guard.check(self.root, sha)["safe_to_review"])

    def test_existing_auto_workflow_removed_is_not_new_debt(self):
        self.save("legacy.yml", OLD)
        sha = self.baseline()
        (self.workflows / "legacy.yml").unlink()
        self.assertTrue(guard.check(self.root, sha)["safe_to_review"])

    def test_missing_and_malformed_base_denied(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        with self.assertRaisesRegex(ValueError, "immutable"):
            guard.check(self.root, "main")
        with self.assertRaises(subprocess.SubprocessError):
            guard.check(self.root, "f" * 40)
        self.assertTrue(guard.check(self.root, sha)["safe_to_review"])

    def test_worktree_symlink_denied(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        (self.workflows / "link.yml").symlink_to(self.workflows / "safe.yml")
        with self.assertRaisesRegex(ValueError, "file type"):
            guard.check(self.root, sha)

    def test_baseline_symlink_denied(self):
        self.save("safe.yml", SAFE)
        (self.workflows / "link.yml").symlink_to(self.workflows / "safe.yml")
        sha = self.baseline()
        (self.workflows / "link.yml").unlink()
        with self.assertRaisesRegex(ValueError, "symlink"):
            guard.check(self.root, sha)

    def test_unknown_future_or_oversize_content_denied(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        (self.workflows / "new.yml").write_bytes(b"x" * (guard.MAX_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "oversized"):
            guard.check(self.root, sha)

    def test_literal_workflow_run_and_generic_self_hosted(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        self.save("auto.yml", OLD.replace("push:\n    branches: [main]", "workflow_run:\n    workflows: [CI]")
                  .replace("[self-hosted, zcloud, vps]", "self-hosted"))
        self.assertFalse(guard.check(self.root, sha)["safe_to_review"])


    def test_new_automatic_self_hosted_python_script_denied_without_sudo_keyword(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        self.save("script.yml", OLD.replace("sudo systemctl restart zennay-cloud.service",
                                             "python3 scripts/maybe_writes_history.py"))
        self.assertFalse(guard.check(self.root, sha)["safe_to_review"])

    def test_dynamic_runner_selector_denied_even_without_literal_self_hosted(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        dynamic = SAFE.replace("pull_request:", "push:")
        dynamic = dynamic.replace("runs-on: ubuntu-latest", "runs-on: " + "$" + "{{ matrix.target }}")
        self.save("dynamic.yml", dynamic)
        self.assertEqual([".github/workflows/dynamic.yml"],
                         guard.check(self.root, sha)["new_exposure_paths"])

    def test_workflow_call_is_automatic_entrypoint_for_potential_vps(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        self.save("call.yml", OLD.replace("push:", "workflow_call:"))
        self.assertFalse(guard.check(self.root, sha)["safe_to_review"])

    def test_inline_array_with_manual_and_auto_events_denied(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        self.save("inline.yml", OLD.replace("on:\n  push:\n    branches: [main]",
                                             "on: [workflow_dispatch, pull_request]"))
        self.assertFalse(guard.check(self.root, sha)["safe_to_review"])

    def test_quoted_on_and_inline_reusable_trigger_denied(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        self.save("quote.yml", OLD.replace("on:\n  push:\n    branches: [main]",
                                            "'on': workflow_call"))
        self.assertFalse(guard.check(self.root, sha)["safe_to_review"])

    def test_ubuntu_24_04_stays_hosted(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        self.save("hosted.yml", OLD.replace("[self-hosted, zcloud, vps]", "ubuntu-24.04"))
        self.assertTrue(guard.check(self.root, sha)["safe_to_review"])

    def test_unreadable_non_utf8_new_workflow_denied(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        (self.workflows / "invalid.yml").write_bytes(bytes((0xff, 0xfe)))
        with self.assertRaises(UnicodeDecodeError):
            guard.check(self.root, sha)



    def test_automatically_invoked_reusable_workflow_is_denied(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        reusable = """name: Reusable proxy
on: [push]
jobs:
  downstream:
    uses: ./.github/workflows/zcloud-vps-deploy.yml
"""
        self.save("reusable.yml", reusable)
        self.assertFalse(guard.check(self.root, sha)["safe_to_review"])

    def test_hosted_step_level_uses_is_not_reusable_job(self):
        self.save("safe.yml", SAFE)
        sha = self.baseline()
        hosted = """name: Hosted CI
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""
        self.save("hosted-ci.yml", hosted)
        self.assertTrue(guard.check(self.root, sha)["safe_to_review"])



if __name__ == "__main__":
    unittest.main()
