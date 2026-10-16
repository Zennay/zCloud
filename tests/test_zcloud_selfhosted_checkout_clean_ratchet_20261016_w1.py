"""Adversarial tests for persistent runner checkout hygiene (#633)."""
import importlib.util
from pathlib import Path
import unittest

PATH = (Path(__file__).resolve().parents[1] / "scripts" /
        "zcloud_selfhosted_checkout_clean_ratchet_20261016_w1.py")
SPEC = importlib.util.spec_from_file_location("checkout_clean_ratchet", PATH)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def sample(clean="true", runner="[self-hosted, zcloud, vps]",
           extra="", uses="actions/checkout@0123456789abcdef"):
    return """name: Test
on: workflow_dispatch
jobs:
  inspect:
    runs-on: %s
    timeout-minutes: 5
    steps:
      - uses: %s
        with:
          persist-credentials: false
          clean: %s
%s
""" % (runner, uses, clean, extra)


class CheckoutCleanTests(unittest.TestCase):
    def find(self, value):
        return guard.analyze(value, ".github/workflows/demo.yml")

    def test_explicit_true_safe(self):
        self.assertEqual(self.find(sample("true")), [])

    def test_default_clean_safe(self):
        self.assertEqual(self.find(sample().replace("          clean: true\n", "")), [])

    def test_false_unsafe(self):
        self.assertEqual(len(self.find(sample("false"))), 1)

    def test_quoted_false_unsafe(self):
        self.assertEqual(len(self.find(sample("'false'"))), 1)

    def test_dynamic_clean_unsafe(self):
        self.assertEqual(len(self.find(sample("$" + "{{ inputs.clean }}"))), 1)

    def test_unexpected_zero_is_unsafe(self):
        self.assertEqual(len(self.find(sample("0"))), 1)

    def test_missing_runner_is_not_selfhosted(self):
        self.assertEqual(self.find(sample("false", runner="ubuntu-latest")), [])

    def test_multiline_selfhosted_runner(self):
        data = sample("false", runner="\n      - self-hosted\n      - zcloud\n      - vps")
        self.assertEqual(len(self.find(data)), 1)

    def test_noncheckout_step_ignored(self):
        data = sample("false", uses="actions/setup-node@pinned")
        self.assertEqual(self.find(data), [])

    def test_duplicate_clean_denied(self):
        data = sample("true", extra="          clean: true\n")
        self.assertTrue(any("duplicate-clean" in row[-1] for row in self.find(data)))

    def test_merge_alias_denied(self):
        data = sample("true", extra="          <<: *unsafe_checkout\n")
        self.assertTrue(any("merged-with-map" in row[-1] for row in self.find(data)))

    def test_inline_map_denied(self):
        data = sample("true").replace(
            "        with:\n          persist-credentials: false\n          clean: true",
            "        with: {clean: false}")
        self.assertTrue(any("inline-with" in row[-1] for row in self.find(data)))

    def test_unsafe_unrelated_job_not_scored(self):
        data = sample("false", runner="ubuntu-latest") + """  other:
    runs-on: [self-hosted, zcloud, vps]
    steps:
      - run: echo ok
"""
        self.assertEqual(self.find(data), [])

    def test_two_selfhosted_checkouts_are_counted(self):
        data = sample("false") + """      - uses: actions/checkout@pin
        with:
          clean: false
"""
        self.assertEqual(len(self.find(data)), 2)

    def test_bad_encoding_bytes_blocked(self):
        with self.assertRaises(guard.Denied):
            self.find(sample("false") + "\tunsafe: key\n")

    def test_oversized_workflow_denied(self):
        with self.assertRaises(guard.Denied):
            self.find("#" + "X" * (guard.MAX_BYTES + 1))

    def test_no_new_debt_is_monotone(self):
        item = self.find(sample("false"))[0]
        old = guard.collections.Counter([item])
        new = guard.collections.Counter([item])
        self.assertFalse(new - old)
        new.update([item])
        self.assertEqual(sum((new - old).values()), 1)

    def test_non_checkout_dynamic_step_not_authorized(self):
        self.assertEqual(self.find(sample("true", uses="other/tool@v1")), [])


    def test_aliased_selfhosted_steps_denied(self):
        source = sample("true").replace(
            "    steps:\n      - uses:", "    steps: *old_checkout\n      - uses:")
        self.assertTrue(any("nonliteral-steps" in row[-1] for row in self.find(source)))

    def test_dynamic_selfhosted_steps_denied(self):
        source = sample("true").replace(
            "    steps:\n", "    steps: $" + "{{ inputs.steps }}\n")
        self.assertTrue(any("nonliteral-steps" in row[-1] for row in self.find(source)))

    def test_inline_list_selfhosted_steps_denied(self):
        source = sample("true").replace(
            "    steps:\n", "    steps: [{uses: actions/checkout@v4, with: {clean: false}}]\n")
        self.assertTrue(any("nonliteral-steps" in row[-1] for row in self.find(source)))

    def test_duplicate_steps_map_denied(self):
        source = sample("true").replace(
            "    steps:\n", "    steps:\n    steps:\n")
        self.assertTrue(any("duplicate-steps-map" in row[-1] for row in self.find(source)))

    def test_flow_style_checkout_step_denied(self):
        source = sample("true").replace(
            "      - uses: actions/checkout@0123456789abcdef",
            "      - {uses: actions/checkout@v4, with: {clean: false}}")
        self.assertTrue(any("flow-step-unverified" in row[-1] for row in self.find(source)))

    def test_flow_style_hosted_job_does_not_raise_new_selfhosted_debt(self):
        source = sample("true", runner="ubuntu-latest").replace(
            "      - uses: actions/checkout@0123456789abcdef",
            "      - {uses: actions/checkout@v4, with: {clean: false}}")
        self.assertEqual(self.find(source), [])

    def test_empty_steps_syntax_not_automatically_risky(self):
        source = sample("true").replace(
            "    steps:\n      - uses:", "    steps: []\n      - uses:")
        self.assertFalse(any("nonliteral-steps" in row[-1] for row in self.find(source)))



class ImmutableGitTreeTests(unittest.TestCase):
    """Exercise the complete scanner with actual local Git objects and remote main."""

    def setUp(self):
        import subprocess
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-checkout-clean-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / "work"
        self.remote = self.root / "origin.git"
        subprocess.run(["git", "init", "--bare", str(self.remote)],
                       check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(self.work)],
                       check=True, capture_output=True)
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Checkout Fixture")
        self.git("config", "commit.gpgsign", "false")
        self.git("remote", "add", "origin", str(self.remote))
        self.workflow("baseline.yml", sample())
        self.commit("baseline")
        self.base = self.rev()
        self.git("push", "origin", "main")

    def git(self, *args):
        import subprocess
        proc = subprocess.run(["git", *args], cwd=self.work,
                              capture_output=True, text=True, check=True,
                              timeout=15)
        return proc.stdout.strip()

    def rev(self):
        return self.git("rev-parse", "HEAD")

    def workflow(self, name, data):
        target = self.work / ".github" / "workflows" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding="utf-8")

    def commit(self, label):
        self.git("add", "-A")
        self.git("commit", "-m", label)

    def scan(self):
        import os
        old = Path.cwd()
        try:
            os.chdir(self.work)
            return guard.run(self.base, self.rev())
        finally:
            os.chdir(old)

    def test_real_git_detects_new_dirty_checkout(self):
        self.workflow("danger.yml", sample("false"))
        self.commit("introduce unsafe clean")
        result = self.scan()
        self.assertEqual(result["baseline_findings"], 0)
        self.assertEqual(result["new_finding_count"], 1)
        self.assertEqual(result["candidate_findings"], 1)
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["mutation_performed"])

    def test_real_git_safe_checkout_is_accepted(self):
        self.workflow("safe.yml", sample("true"))
        self.commit("add safe checkout")
        result = self.scan()
        self.assertEqual(result["new_finding_count"], 0)

    def test_real_git_grandfathers_unchanged_baseline_debt(self):
        self.workflow("baseline.yml", sample("false"))
        self.commit("legacy debt committed")
        self.base = self.rev()
        self.git("push", "origin", "main")
        self.workflow("safe.yml", sample("true"))
        self.commit("add unrelated safe checkout")
        result = self.scan()
        self.assertEqual(result["baseline_findings"], 1)
        self.assertEqual(result["candidate_findings"], 1)
        self.assertEqual(result["new_finding_count"], 0)

    def test_real_git_copy_of_existing_debt_is_new(self):
        self.workflow("baseline.yml", sample("false"))
        self.commit("legacy debt")
        self.base = self.rev()
        self.git("push", "origin", "main")
        self.workflow("newcopy.yml", sample("false"))
        self.commit("copy debt to new path")
        result = self.scan()
        self.assertEqual(result["baseline_findings"], 1)
        self.assertEqual(result["candidate_findings"], 2)
        self.assertEqual(result["new_finding_count"], 1)

    def test_real_git_rejects_moving_main(self):
        self.workflow("changed.yml", sample("true"))
        self.commit("advance head")
        self.git("push", "origin", "main")
        with self.assertRaisesRegex(guard.Denied, "moving_or_foreign_main"):
            self.scan()

    def test_real_git_rejects_symlinked_workflow(self):
        target = self.work / ".github" / "workflows" / "danger.yml"
        target.symlink_to("baseline.yml")
        self.commit("unsafe symlink")
        with self.assertRaisesRegex(guard.Denied, "nonregular_workflow_manifest"):
            self.scan()

    def test_real_git_rejects_head_not_descended_from_main(self):
        self.git("checkout", "-b", "unrelated")
        self.git("commit", "--allow-empty", "-m", "unrelated")
        self.git("checkout", "--orphan", "detached")
        self.workflow("different.yml", sample("false"))
        self.commit("unrelated root")
        with self.assertRaises(guard.Denied):
            self.scan()



if __name__ == "__main__":
    unittest.main()
