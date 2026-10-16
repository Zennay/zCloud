"""Adversarial source-only admission tests for #633 runner identity regression."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

file = Path(__file__).resolve().parents[1] / "scripts/zcloud_new_runner_identity_guard_20261016.py"
spec = importlib.util.spec_from_file_location("guard", file)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
PIN = "d23441a48e516b6c34aea4fa41551a30e30af803"


def wf(runner="[self-hosted, zcloud, vps]", guard=True):
    cmd = "python3 scripts/zcloud_vps_runner_guard.py --json" if guard else "echo untrusted"
    return f'''name: guarded
on:
  workflow_dispatch:
jobs:
  audit:
    runs-on: {runner}
    steps:
      - uses: actions/checkout@{PIN}
        with:
          persist-credentials: false
      - name: Identity first
        run: |
          set -euo pipefail
          {cmd}
      - run: echo after
'''


class IdentityPolicyTests(unittest.TestCase):
    def test_guarded_self_hosted_allowed(self):
        self.assertEqual(g.unsafe_jobs(wf()), {})

    def test_new_unguarded_job_denied(self):
        self.assertEqual(g.unsafe_jobs(wf(guard=False)), {"audit": "missing_early_runner_guard"})

    def test_hosted_allowed(self):
        self.assertEqual(g.unsafe_jobs(wf("ubuntu-latest", False)), {})

    def test_ftmo_dedicated_runner_outside_scope(self):
        self.assertEqual(g.unsafe_jobs(wf("[self-hosted, ftmo-research]", False)), {})

    def test_dynamic_runner_denied(self):
        self.assertIn("audit", g.unsafe_jobs(wf("$" + "{{ inputs.runner }}", False)))

    def test_untrusted_action_before_identity_denied(self):
        text = wf().replace("      - name: Identity first",
                            "      - uses: malicious/action@main\n      - name: Identity first")
        self.assertIn("audit", g.unsafe_jobs(text))

    def test_fake_quoted_guard_denied(self):
        text = wf().replace("python3 scripts/zcloud_vps_runner_guard.py --json",
                            "echo 'python3 scripts/zcloud_vps_runner_guard.py --json'")
        self.assertIn("audit", g.unsafe_jobs(text))

    def test_guard_in_second_script_line_denied(self):
        text = wf().replace("          python3 scripts/",
                            "          echo bad\n          python3 scripts/")
        self.assertIn("audit", g.unsafe_jobs(text))

    def test_skipped_guard_step_denied(self):
        text = wf().replace("        run: |", "        if: false\n        run: |")
        self.assertIn("audit", g.unsafe_jobs(text))

    def test_guard_continue_on_error_denied(self):
        text = wf().replace("        run: |", "        continue-on-error: true\n        run: |")
        self.assertIn("audit", g.unsafe_jobs(text))

    def test_step_conditional_guard_denied(self):
        text = wf().replace("        run: |", "        if: github.event_name == 'workflow_dispatch'\n        run: |")
        self.assertIn("audit", g.unsafe_jobs(text))

    def test_failed_open_guard_denied(self):
        self.assertIn("audit", g.unsafe_jobs(wf().replace("--json", "--json || true")))

    def test_custom_policy_override_denied(self):
        self.assertIn("audit", g.unsafe_jobs(wf().replace("--json", "--policy /tmp/forged.json")))

    def test_unpinned_checkout_denied(self):
        self.assertIn("audit", g.unsafe_jobs(wf().replace(PIN, "main")))

    def test_no_checkout_denied(self):
        self.assertIn("audit", g.unsafe_jobs(wf().replace(
            f"      - uses: actions/checkout@{PIN}\n", "")))

    def test_duplicate_runner_denied(self):
        text = wf().replace("    runs-on:", "    runs-on: ubuntu-latest\n    runs-on:")
        self.assertEqual(g.unsafe_jobs(text), {"audit": "ambiguous_runner"})

    def test_rename_unsafe_job_is_new_debt(self):
        a = g.unsafe_jobs(wf(guard=False))
        b = g.unsafe_jobs(wf(guard=False).replace("  audit:", "  replacement:"))
        self.assertTrue(set(b) - set(a))

    def test_prior_unguarded_job_grandfathered(self):
        old = g.unsafe_jobs(wf(guard=False))
        new = g.unsafe_jobs(wf(guard=False).replace("name: guarded", "name: renamed"))
        self.assertEqual(set(new) - set(old), set())

    def test_ambiguous_jobs_root_fails_closed(self):
        with self.assertRaises(ValueError):
            g.unsafe_jobs(wf() + "\njobs:\n  evil:\n    runs-on: self-hosted\n")

    def test_reusable_job_no_runner_ok(self):
        self.assertEqual(g.unsafe_jobs("jobs:\n  reuse:\n    uses: org/repo/.github/workflows/x.yml@" + PIN), {})

    def test_missing_runner_denied(self):
        self.assertEqual(g.unsafe_jobs(wf().replace(
            "    runs-on: [self-hosted, zcloud, vps]\n", "")), {"audit": "unknown_runner"})

    def test_source_path_rejection(self):
        with self.assertRaises(ValueError):
            g.source("a" * 40, ".github/workflows/../secret.yml")

    def test_bad_sha_rejection(self):
        with self.assertRaises(ValueError):
            g.inventory("main", "a" * 40)

    def test_fail_closed_on_incomplete_git_inventory(self):
        with mock.patch.object(g, "inventory", side_effect=ValueError("truncated")):
            with self.assertRaises(ValueError):
                g.evaluate("a" * 40, "b" * 40)

    def test_changed_only_regression(self):
        with mock.patch.object(g, "inventory", return_value=[".github/workflows/new.yml"]), \
             mock.patch.object(g, "source", side_effect=[None, wf(guard=False)]):
            result = g.evaluate("a" * 40, "b" * 40)
        self.assertEqual(len(result["new_findings"]), 1)
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["mutation_performed"])


if __name__ == "__main__":
    unittest.main()
