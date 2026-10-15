"""Real-Git adversarial regression suite for token-permission drift."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.zcloud_token_permission_ratchet_20261015 import (
    IncompleteSource, check, permission_debt,
)


class PermissionRatchetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="token-ratchet-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Source Test")
        self.git("config", "user.email", "source@example.invalid")
        self.put(".github/workflows/original.yml",
                 "name: original\non: push\npermissions:\n  contents: read\n"
                 "jobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.base = self.commit("baseline")

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.root, stderr=subprocess.DEVNULL
        ).decode().strip()

    def put(self, name, contents):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")

    def commit(self, label):
        self.git("add", "-A")
        self.git("commit", "-qm", label)
        return self.git("rev-parse", "HEAD")

    def check_file(self, text, name=".github/workflows/original.yml"):
        self.put(name, text)
        return check(self.root, self.base, self.commit("candidate"))

    def test_read_only_source_passes(self):
        result = self.check_file(
            "name: changed\non: push\npermissions:\n  contents: read\n"
            "jobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    permissions:\n      contents: read\n"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_risks"], 0)

    def test_top_level_write_all_denied(self):
        value = self.check_file("on: push\npermissions: write-all\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(value["ok"])
        self.assertEqual(value["new_risks"], 2)
        self.assertEqual(value["removed_safe_scopes"], 1)

    def test_job_level_write_denied(self):
        value = self.check_file("on: push\njobs:\n  build:\n    permissions:\n"
                                "      id-token: write\n    runs-on: ubuntu-latest\n")
        self.assertFalse(value["ok"])

    def test_new_workflow_write_denied(self):
        value = self.check_file("on: push\npermissions:\n  statuses: write\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
                                ".github/workflows/new.yml")
        self.assertFalse(value["ok"])

    def test_untrusted_expression_denied(self):
        value = self.check_file("on: push\npermissions: ${{ inputs.perms }}\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(value["ok"])

    def test_flow_mapping_denied(self):
        value = self.check_file("on: push\npermissions: {contents: write}\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(value["ok"])

    def test_quoted_keys_denied(self):
        value = self.check_file("on: push\n'permissions':\n  'id-token': write\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(value["ok"])

    def test_read_comment_not_write(self):
        value = self.check_file("on: push\npermissions:\n  contents: read # write\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertTrue(value["ok"])

    def test_grandfather_exact_existing_debt(self):
        self.put(".github/workflows/original.yml",
                 "on: push\npermissions: write-all\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        old = self.commit("legacy")
        self.put("README.md", "non workflow change")
        new = self.commit("docs")
        outcome = check(self.root, old, new)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["baseline_risks"], 1)

    def test_move_legacy_risk_to_different_job_denied(self):
        self.put(".github/workflows/original.yml",
                 "on: push\njobs:\n  old:\n    permissions:\n      actions: write\n    runs-on: ubuntu-latest\n")
        old = self.commit("legacy")
        self.put(".github/workflows/original.yml",
                 "on: push\njobs:\n  new:\n    permissions:\n      actions: write\n    runs-on: ubuntu-latest\n")
        self.assertFalse(check(self.root, old, self.commit("move"))["ok"])

    def test_duplicated_legacy_risk_denied(self):
        self.put(".github/workflows/original.yml", "on: push\npermissions: write-all\njobs: {}\n")
        old = self.commit("legacy")
        self.put(".github/workflows/original.yml", "on: push\npermissions: write-all\npermissions: write-all\njobs: {}\n")
        self.assertFalse(check(self.root, old, self.commit("duplicate"))["ok"])

    def test_opaque_mapping_denied(self):
        value = self.check_file("on: push\npermissions:\n  contents: read\n  checks: ${{ input.checks }}\njobs: {}\n")
        self.assertFalse(value["ok"])

    def test_reject_symlink(self):
        target = self.root / "README.md"
        target.write_text("on: push\npermissions: write-all\n")
        workflow = self.root / ".github/workflows/original.yml"
        workflow.unlink()
        workflow.symlink_to(target)
        new = self.commit("symlink")
        with self.assertRaises(IncompleteSource):
            check(self.root, self.base, new)

    def test_reject_backwards_ancestry_and_invalid_sha(self):
        self.put("README.md", "change")
        new = self.commit("change")
        with self.assertRaises(IncompleteSource):
            check(self.root, new, self.base)
        with self.assertRaises(IncompleteSource):
            check(self.root, "invalid", new)

    def test_release_authority_never_granted(self):
        value = self.check_file("on: push\npermissions: write-all\njobs: {}\n")
        for flag in ("release_authorized", "deployment_authorized", "mutation_performed"):
            self.assertIs(value[flag], False)

    def test_secret_like_workflow_comment_not_emitted(self):
        value = permission_debt(
            "permissions: write-all\n# SECRET_TOKEN=private\n",
            ".github/workflows/example.yml"
        )
        self.assertNotIn("SECRET_TOKEN", repr(value))


    def test_whitespace_before_permissions_colon_denied(self):
        value = self.check_file("on: push\npermissions : write-all\njobs: {}\n")
        self.assertFalse(value["ok"])

    def test_yaml_explicit_mapping_key_denied(self):
        value = self.check_file("on: push\n? permissions\n: write-all\njobs: {}\n")
        self.assertFalse(value["ok"])

    def test_unicode_escaped_permission_key_denied(self):
        value = self.check_file('on: push\n"permiss\\u0069ons": write-all\njobs: {}\n')
        self.assertFalse(value["ok"])

    def test_yaml_merge_alias_denied(self):
        value = self.check_file("on: push\npermissions: &admin write-all\n<<: *admin\njobs: {}\n")
        self.assertFalse(value["ok"])

    def test_malformed_quoted_read_is_not_safe(self):
        value = self.check_file("on: push\npermissions:\n  contents: 'read\njobs: {}\n")
        self.assertFalse(value["ok"])

    def test_job_level_write_on_compact_jobs_flow_fails_closed(self):
        value = self.check_file("on: push\njobs: { test: { permissions: write-all } }\n")
        self.assertFalse(value["ok"])


    def test_opaque_legacy_mapping_changed_content_denied(self):
        self.put(".github/workflows/original.yml",
                 "on: push\npermissions: {contents: read}\njobs: {}\n")
        old = self.commit("legacy-opaque")
        self.put(".github/workflows/original.yml",
                 "on: push\npermissions: {contents: write}\njobs: {}\n")
        result = check(self.root, old, self.commit("opaque-write"))
        self.assertFalse(result["ok"])

    def test_compact_legacy_flow_jobs_changed_denied(self):
        self.put(".github/workflows/original.yml", "on: push\njobs: { test: { permissions: read-all } }\n")
        old = self.commit("flow-old")
        self.put(".github/workflows/original.yml", "on: push\njobs: { test: { permissions: write-all } }\n")
        self.assertFalse(check(self.root, old, self.commit("flow-new"))["ok"])

    def test_opaque_legacy_expression_swap_denied(self):
        self.put(".github/workflows/original.yml", "on: push\npermissions: ${{ inputs.a }}\njobs: {}\n")
        old = self.commit("expression-old")
        self.put(".github/workflows/original.yml", "on: push\npermissions: ${{ inputs.b }}\njobs: {}\n")
        self.assertFalse(check(self.root, old, self.commit("expression-new"))["ok"])


    def test_removing_explicit_read_only_repo_policy_denied(self):
        value = self.check_file("on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(value["ok"])

    def test_removing_safe_job_permissions_denied(self):
        self.put(".github/workflows/original.yml",
                 "on: push\njobs:\n  test:\n    permissions:\n      contents: read\n    runs-on: ubuntu-latest\n")
        old = self.commit("safe-job")
        self.put(".github/workflows/original.yml",
                 "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertFalse(check(self.root, old, self.commit("removed-scope"))["ok"])

    def test_allow_safer_read_to_none_with_same_scope(self):
        value = self.check_file(
            "on: push\npermissions:\n  contents: none\n"
            "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
        )
        self.assertTrue(value["ok"])


if __name__ == "__main__":
    unittest.main()
