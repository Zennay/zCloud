"""Hermetic negative matrix for monotonic pull_request_target gate."""
import importlib.util
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

TARGET = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_pr_target_delta_guard_20261014.py"
spec = importlib.util.spec_from_file_location("target_delta", TARGET)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

SAFE = """name: read only
on:
  pull_request_target:
    branches: [main]
permissions:
  contents: read
jobs:
  inventory:
    runs-on: ubuntu-latest
    steps:
      - run: echo 'trusted metadata'
"""


class GuardTests(unittest.TestCase):
    def test_safe_hosted_read_only(self):
        self.assertEqual(gate.risk_set(SAFE), set())

    def test_missing_root_permissions(self):
        candidate = SAFE.replace("permissions:\n  contents: read\n", "")
        self.assertIn("missing_explicit_root_permissions", gate.risk_set(candidate))
        self.assertEqual(gate.risk_set(SAFE.replace("permissions:\n  contents: read", "permissions: {}")), set())

    def test_root_write(self):
        self.assertIn("write_permission", gate.risk_set(SAFE.replace("contents: read", "contents: write")))

    def test_job_write(self):
        src = SAFE.replace("    runs-on:", "    permissions:\n      actions: write\n    runs-on:")
        self.assertIn("write_permission", gate.risk_set(src))

    def test_inline_and_quoted_permissions(self):
        for replacement in ("permissions: {contents: write}",
                            "permissions:\n  contents: 'write'",
                            'permissions: {contents: read, actions: "write"}'):
            with self.subTest(replacement=replacement):
                src = SAFE.replace("permissions:\n  contents: read", replacement)
                self.assertIn("write_permission", gate.risk_set(src))

    def test_write_all(self):
        self.assertIn("write_all", gate.risk_set(SAFE.replace("permissions:\n  contents: read", "permissions: write-all")))

    def test_secret_reference(self):
        self.assertIn("secret_reference", gate.risk_set(SAFE + "      - run: echo '${{ secrets.ADMIN }}'\n"))

    def test_untrusted_checkout(self):
        src = SAFE + "      - uses: actions/checkout@abcdef\n        with:\n          ref: ${{ github.event.pull_request.head.sha }}\n"
        self.assertIn("untrusted_pr_checkout", gate.risk_set(src))

    def test_untrusted_head_in_run(self):
        src = SAFE + "      - run: git checkout '${{ github.head_ref }}'\n"
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))

    def test_comments_are_not_risks(self):
        self.assertEqual(gate.risk_set(SAFE + "# permissions: write-all; secrets.ADMIN\n"), set())

    def test_non_pr_target_unaffected(self):
        self.assertEqual(gate.risk_set(SAFE.replace("pull_request_target", "pull_request").replace("contents: read", "contents: write")), set())

    def test_alias_denied(self):
        self.assertIn("yaml_alias_or_merge", gate.risk_set(SAFE.replace("    runs-on:", "    <<: *unsafe\n    runs-on:")))

    def test_malformed_event_refused(self):
        with self.assertRaises(gate.Unverified):
            gate.risk_set(SAFE + "on: [workflow_dispatch]\n")

    def test_monotonic_same_debt_is_allowed(self):
        baseline = gate.risk_set(SAFE.replace("contents: read", "contents: write"))
        candidate = gate.risk_set(SAFE.replace("contents: read", "contents: write") + "# extra docs\n")
        self.assertEqual(candidate - baseline, set())

    def test_monotonic_added_secret_denied(self):
        baseline = gate.risk_set(SAFE.replace("contents: read", "contents: write"))
        candidate = gate.risk_set(SAFE.replace("contents: read", "contents: write") + "      - run: echo '${{ secrets.X }}'\n")
        self.assertEqual(candidate - baseline, {"secret_reference"})

    def test_preexisting_secret_does_not_hide_new_secret(self):
        previous = SAFE + "      - run: echo '${{ secrets.FIRST }}'\n"
        current = previous + "      - run: echo '${{ secrets.SECOND }}'\n"
        self.assertEqual(gate.risk_counts(previous)["secret_reference"], 1)
        self.assertEqual(gate.risk_counts(current)["secret_reference"], 2)

    def test_preexisting_write_does_not_hide_new_write_scope(self):
        previous = SAFE.replace("contents: read", "contents: write")
        current = previous.replace("    runs-on:", "    permissions:\n      actions: write\n    runs-on:")
        self.assertEqual(gate.risk_counts(previous)["write_permission"], 1)
        self.assertEqual(gate.risk_counts(current)["write_permission"], 2)

    def test_equal_total_write_scope_swap_does_not_launder_new_authority(self):
        previous = SAFE.replace("contents: read", "contents: write")
        candidate = SAFE.replace("contents: read", "actions: write")
        self.assertEqual(gate.risk_counts(previous)["write_permission"], 1)
        self.assertEqual(gate.risk_counts(candidate)["write_permission"], 1)
        self.assertIn("write_permission", gate.increasing_risks(previous, candidate))

    def test_equal_total_secret_swap_does_not_launder_new_secret(self):
        previous = SAFE + "      - run: echo '${{ secrets.OLD_KEY }}'\n"
        candidate = SAFE + "      - run: echo '${{ secrets.NEW_KEY }}'\n"
        self.assertEqual(gate.risk_counts(previous)["secret_reference"], 1)
        self.assertEqual(gate.risk_counts(candidate)["secret_reference"], 1)
        self.assertIn("secret_reference", gate.increasing_risks(previous, candidate))
        for signature in gate.risk_signatures(candidate)["secret_reference"]:
            self.assertRegex(signature, r"^[0-9a-f]{64}$")
            self.assertNotIn("NEW_KEY", signature)

    def test_unchanged_legacy_secret_and_write_identity_does_not_block(self):
        previous = SAFE.replace("contents: read", "contents: write")
        previous += "      - run: echo '${{ secrets.EXISTING }}'\n"
        self.assertEqual(gate.increasing_risks(previous, previous + "# harmless edit\n"), set())

    def test_quoted_permission_key_new_authority_denied(self):
        candidate = SAFE.replace("contents: read", "'actions': write")
        self.assertIn("write_permission", gate.risk_set(candidate))
        self.assertIn("write_permission", gate.increasing_risks(SAFE, candidate))

    def test_quoted_permission_key_identity_is_accepted(self):
        baseline = SAFE.replace("contents: read", "actions: write")
        candidate = SAFE.replace("contents: read", "'actions': write")
        self.assertEqual(gate.increasing_risks(baseline, candidate), set())

    def test_repeated_identical_secret_reference_ratcheted(self):
        previous = SAFE + "      - run: echo '${{ secrets.EXISTING }}'\n"
        candidate = previous + "      - run: echo '${{ secrets.EXISTING }}'\n"
        self.assertIn("secret_reference", gate.increasing_risks(previous, candidate))

    def test_checkout_head_expression_swap_denied_at_equal_count(self):
        base = SAFE + "      - uses: actions/checkout@012345\n        with:\n          ref: ${{ github.event.pull_request.head.ref }}\n"
        candidate = base.replace("github.event.pull_request.head.ref", "github.event.pull_request.head.sha")
        self.assertEqual(gate.risk_counts(base)["untrusted_pr_checkout"], 1)
        self.assertEqual(gate.risk_counts(candidate)["untrusted_pr_checkout"], 1)
        self.assertIn("untrusted_pr_checkout", gate.increasing_risks(base, candidate))

    def test_run_head_expression_swap_denied_at_equal_count(self):
        base = SAFE + "      - run: git fetch origin '${{ github.head_ref }}'\n"
        candidate = base.replace("github.head_ref", "github.event.pull_request.head.sha")
        self.assertEqual(gate.risk_counts(base)["pr_source_in_run_job"], 1)
        self.assertEqual(gate.risk_counts(candidate)["pr_source_in_run_job"], 1)
        self.assertIn("pr_source_in_run_job", gate.increasing_risks(base, candidate))

    def test_comment_only_rewrite_preserves_risk_counters(self):
        previous = SAFE.replace("contents: read", "contents: write") + "      - run: echo '${{ secrets.FIRST }}'\n"
        candidate = previous + "# actions: write and secrets.BAD are commented out\n"
        self.assertEqual(gate.risk_counts(previous), gate.risk_counts(candidate))

    def test_trusted_target_workflow_isolated_from_untrusted_source(self):
        source = (TARGET.parents[1] / ".github/workflows/zcloud-pr-target-risk-ratchet-20261014.yml").read_text()
        trusted = (TARGET.parents[1] / ".github/workflows/zcloud-pr-target-trusted-base-20261014.yml").read_text()
        self.assertIn("  pull_request:\n", source)
        self.assertNotIn("  pull_request_target:\n", source)
        self.assertIn("  pull_request_target:\n", trusted)
        self.assertNotIn("  pull_request:\n", trusted)
        self.assertIn("zcloud-pr-target-ratchet-source-", source)
        self.assertIn("zcloud-pr-target-trusted-", trusted)
        self.assertIn("cancel-in-progress: false", trusted)
        self.assertIn("permissions:\n  contents: read", trusted)
        self.assertNotIn("self-hosted", trusted)
        self.assertNotIn("permissions: write-all", trusted)
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", trusted)
        self.assertNotIn("ref: ${{ github.event.pull_request.head.sha }}", trusted)
        self.assertIn("path: trusted", trusted)
        self.assertIn("persist-credentials: false", trusted)
        self.assertIn("submodules: false", trusted)
        self.assertIn('test "$current_main" = "$EXACT_BASE"', trusted)
        self.assertIn('test "$final_main" = "$EXACT_BASE"', trusted)
        self.assertLess(trusted.index('test "$current_main" = "$EXACT_BASE"'),
                        trusted.index('python3 trusted/scripts/zcloud_pr_target_delta_guard_20261014.py'))
        self.assertGreater(trusted.index('test "$final_main" = "$EXACT_BASE"'),
                           trusted.index('python3 trusted/scripts/zcloud_pr_target_delta_guard_20261014.py'))
        self.assertIn('git -C trusted fetch --quiet --no-tags origin "$EXACT_HEAD"', trusted)
        self.assertIn("(cd trusted && python3 -m unittest", trusted)
        self.assertIn("python3 trusted/scripts/zcloud_pr_target_delta_guard_20261014.py", trusted)
        self.assertNotIn("python3 candidate/", trusted)
        self.assertNotIn("cd candidate && python3", trusted)
        self.assertEqual(gate.risk_set(trusted), set())

    def test_separate_steps_do_not_conflate_trusted_checkout_with_head_metadata(self):
        trusted = (TARGET.parents[1] / ".github/workflows/zcloud-pr-target-trusted-base-20261014.yml").read_text()
        self.assertIn("github.event.pull_request.head.repo.full_name", trusted)
        self.assertIn("actions/checkout@", trusted)
        self.assertNotIn("untrusted_pr_checkout", gate.risk_set(trusted))

    def test_git_base_delta_blocks_new_risk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def command(*args):
                return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
            command("init", "-q")
            command("config", "user.name", "zcloud test")
            command("config", "user.email", "test@example.invalid")
            path = root / ".github" / "workflows" / "guard.yml"
            path.parent.mkdir(parents=True)
            path.write_text(SAFE)
            command("add", ".")
            command("commit", "-qm", "base")
            base = command("rev-parse", "HEAD")
            path.write_text(SAFE.replace("contents: read", "contents: write"))
            command("add", ".")
            command("commit", "-qm", "head")
            head = command("rev-parse", "HEAD")
            self.assertEqual(gate.evaluate(root, base, head), (1, [('.github/workflows/guard.yml', 'write_permission')]))
            self.assertEqual(gate.evaluate(root, head, head), (0, []))

    def test_changed_workflow_inventory_bounded(self):
        with patch.object(gate, "git", return_value=b".github/workflows/a.yml\x00.github/workflows/a.yml\x00"):
            with self.assertRaises(gate.Unverified):
                gate.changed_workflows(Path("."), "a"*40, "b"*40)

    def test_git_mode_denies_symlink(self):
        with patch.object(gate, "git", return_value=b"120000 blob deadbeef\t.github/workflows/a.yml\n"):
            with self.assertRaises(gate.Unverified):
                gate.source_at(Path("."), "a"*40, ".github/workflows/a.yml")


if __name__ == "__main__":
    unittest.main()
