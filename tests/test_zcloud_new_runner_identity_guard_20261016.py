"""Adversarial source-only admission tests for #633 runner identity regression."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import subprocess
import tempfile

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

    def test_custom_hosted_prefix_is_not_trusted(self):
        for runner in ("ubuntu-vps", "windows-zcloud", "macos-self-hosted",
                       "ubuntu-worker-custom", "ubuntu-24.04-vps",
                       "windows-2025-private", "macos-15-custom"):
            with self.subTest(runner=runner):
                self.assertEqual(g._runner(runner), "needs_guard")
                self.assertIn("audit", g.unsafe_jobs(wf(runner, False)))

    def test_allowlisted_github_hosted_labels(self):
        for runner in ("ubuntu-latest", "ubuntu-24.04", "ubuntu-24.04-arm",
                       "ubuntu-22.04", "windows-latest", "windows-2025",
                       "macos-latest", "macos-15", "macos-15-intel"):
            with self.subTest(runner=runner):
                self.assertEqual(g._runner(runner), "hosted")
                self.assertEqual(g.unsafe_jobs(wf(runner, False)), {})

    def test_hosted_runner_shadow_mapping_fails_closed(self):
        for hidden in (
            '    "runs-on": [self-hosted, vps]',
            "    'runs-on': [self-hosted, zcloud]",
            '    <<: *injected_runner',
            '    ? runs-on: [self-hosted, vps]',
            "    !key runs-on: [self-hosted, vps]",
        ):
            with self.subTest(hidden=hidden):
                doc = wf("ubuntu-latest", False).replace(
                    "    steps:", hidden + "\n    steps:"
                )
                with self.assertRaises(ValueError):
                    g.unsafe_jobs(doc)

    def test_hosted_job_with_legitimate_other_metadata(self):
        doc = wf("ubuntu-24.04", False).replace(
            "    steps:",
            "    container: ubuntu:24.04\n    services: {redis: {image: redis:7}}\n    steps:",
        )
        self.assertEqual(g.unsafe_jobs(doc), {})

    def test_ftmo_dedicated_runner_outside_scope(self):
        self.assertEqual(g.unsafe_jobs(wf("[self-hosted, ftmo-research]", False)), {})

    def test_generic_self_hosted_list_does_not_bypass_identity(self):
        for value in ("[self-hosted]", "[self-hosted, unknown]", "[self-hosted, linux]",
                      "[self-hosted, ftmo-research, extra]", "[self-hosted, label]", "SELF-HOSTED"):
            with self.subTest(runner=value):
                self.assertIn("audit", g.unsafe_jobs(wf(value, False)))

    def test_dedicated_ftmo_label_order_is_scoped(self):
        self.assertEqual(g.unsafe_jobs(wf("[ftmo-research, self-hosted]", False)), {})

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

    def test_inherited_runtime_env_cannot_shadow_guard(self):
        for key in ("BASH_ENV", "PYTHONPATH", "PYTHONHOME", "PATH"):
            with self.subTest(key=key):
                root = wf().replace("jobs:", "env:\n  " + key + ": /tmp/unsafe\njobs:")
                job = wf().replace("    steps:", "    env:\n      " + key + ": /tmp/unsafe\n    steps:")
                step = wf().replace("        run: |",
                                    "        env:\n          " + key + ": /tmp/unsafe\n        run: |")
                for val in (root, job, step):
                    self.assertIn("audit", g.unsafe_jobs(val))

    def test_inline_shadowed_runtime_environment_is_denied(self):
        for val in (wf().replace("jobs:", "env: {BASH_ENV: /tmp/code}\njobs:"),
                    wf().replace("    steps:", "    env: {PYTHONPATH: /tmp/payload}\n    steps:")):
            self.assertIn("audit", g.unsafe_jobs(val))

    def test_unrelated_environment_is_permitted(self):
        val = wf().replace("    steps:", "    env:\n      BUILD_MODE: offline\n    steps:")
        self.assertEqual(g.unsafe_jobs(val), {})

    def test_quoted_workflow_environment_cannot_bypass_guard(self):
        for key in ('"env"', "'env'", "!!str env", "? env"):
            with self.subTest(root_key=key):
                doc = wf().replace("jobs:", key + ":\n  BASH_ENV: /tmp/startup\njobs:")
                self.assertIn("audit", g.unsafe_jobs(doc))

    def test_root_environment_merge_or_alias_is_not_trusted(self):
        for setting in ("env: *injected", "env: {BASH_ENV: /tmp/injected}",
                        "env:\n  <<: *injected", "env:\n  PATH: /tmp/malicious",
                        "env:\n  'PYTHONPATH': /tmp/modules"):
            with self.subTest(setting=setting):
                doc = wf().replace("jobs:", setting + "\njobs:")
                self.assertIn("audit", g.unsafe_jobs(doc))

    def test_workflow_environment_with_unrelated_flat_metadata_allowed(self):
        doc = wf().replace("jobs:", "env:\n  BUILD_MODE: offline\n  LABEL: fixture\njobs:")
        self.assertEqual(g.unsafe_jobs(doc), {})

    def test_duplicate_workflow_env_mapping_is_not_trusted(self):
        doc = wf().replace("jobs:", "env:\n  SAFE: one\nenv:\n  SAFE: two\njobs:")
        self.assertIn("audit", g.unsafe_jobs(doc))

    def test_pre_guard_node_git_and_loader_environment_denied(self):
        hook_names = (
            "NODE_OPTIONS", "NODE_PATH", "LD_PRELOAD", "LD_LIBRARY_PATH",
            "GIT_SSH_COMMAND", "GIT_SSH", "GIT_ASKPASS", "GIT_CONFIG_COUNT",
            "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
            "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
        )
        for key in hook_names:
            with self.subTest(key=key):
                for scope in ("workflow", "job", "step"):
                    value = wf()
                    if scope == "workflow":
                        value = value.replace("jobs:", "env:\n  " + key + ": /tmp/injected\njobs:")
                    elif scope == "job":
                        value = value.replace("    steps:", "    env:\n      " + key + ": /tmp/injected\n    steps:")
                    else:
                        value = value.replace("        run: |",
                                              "        env:\n          " + key + ": /tmp/injected\n        run: |")
                    self.assertIn("audit", g.unsafe_jobs(value), (scope, key))

    def test_unrelated_flat_workflow_and_job_env_remain_allowed(self):
        value = wf().replace("jobs:", "env:\n  STAGE: test\njobs:").replace(
            "    steps:", "    env:\n      BUILD_LABEL: offline\n    steps:"
        )
        self.assertEqual(g.unsafe_jobs(value), {})

    def test_folded_guard_script_is_not_trustworthy(self):
        for scalar in (">", ">-"):
            with self.subTest(scalar=scalar):
                self.assertIn("audit", g.unsafe_jobs(wf().replace("run: |", "run: " + scalar)))

    def test_custom_step_shell_cannot_hide_runner_guard(self):
        for shell in ("python", "pwsh", "sh", "bash -c 'true' {0}"):
            with self.subTest(shell=shell):
                value = wf().replace("        run: |", "        shell: " + shell + "\n        run: |")
                self.assertIn("audit", g.unsafe_jobs(value))

    def test_post_guard_failure_status_override_denied(self):
        for condition in ("always()", "failure()", "!cancelled()", "${{ success() || always() }}"):
            with self.subTest(condition=condition):
                val = wf().replace("      - run: echo after",
                                   "      - if: " + condition + "\n        run: systemctl restart zcloud")
                self.assertIn("audit", g.unsafe_jobs(val))

    def test_post_guard_normal_success_step_allowed(self):
        val = wf().replace("      - run: echo after",
                           "      - if: success()\n        run: echo safe")
        self.assertEqual(g.unsafe_jobs(val), {})

    def test_quoted_identity_step_key_cannot_shadow_run_or_shell(self):
        for extra in ('"run": echo untrusted', "'run': echo untrusted",
                      '"shell": python', "'if': true", "!!str run: echo unsafe",
                      "run: |"):
            with self.subTest(field=extra):
                doc = wf().replace("        run: |", "        run: |\n        " + extra)
                self.assertIn("audit", g.unsafe_jobs(doc))

    def test_identity_step_env_alias_is_not_allowed(self):
        for setting in ("        env: *malicious", "        env: {BASH_ENV: /tmp/code}",
                        "        'env': {PATH: /tmp}"):
            with self.subTest(setting=setting):
                doc = wf().replace("        run: |", setting + "\n        run: |")
                self.assertIn("audit", g.unsafe_jobs(doc))

    def test_job_env_alias_cannot_inject_shell_hook(self):
        for setting in ("    env: *inherited", "    env: {SAFE: yes}"):
            with self.subTest(setting=setting):
                doc = wf().replace("    steps:", setting + "\n    steps:")
                self.assertIn("audit", g.unsafe_jobs(doc))

    def test_identity_step_with_normal_literal_shell_is_still_valid(self):
        doc = wf().replace("        run: |", "        shell: bash\n        run: |")
        self.assertEqual(g.unsafe_jobs(doc), {})

    def test_explicit_bash_shell_preserves_safe_guard(self):
        value = wf().replace("        run: |", "        shell: bash\n        run: |")
        self.assertEqual(g.unsafe_jobs(value), {})

    def test_job_shell_defaults_cannot_override_guard(self):
        value = wf().replace("    steps:", "    defaults:\n      run:\n        shell: python\n    steps:")
        self.assertIn("audit", g.unsafe_jobs(value))

    def test_workflow_shell_defaults_cannot_override_guard(self):
        value = wf().replace("jobs:", "defaults:\n  run:\n    shell: python\njobs:")
        self.assertIn("audit", g.unsafe_jobs(value))

    def test_inline_workflow_default_shell_is_denied(self):
        for defaults in ('defaults: {run: {shell: python}}', '"defaults": {run: {shell: pwsh}}'):
            with self.subTest(defaults=defaults):
                value = wf().replace("jobs:", defaults + "\njobs:")
                self.assertIn("audit", g.unsafe_jobs(value))

    def test_must_have_literal_steps_mapping(self):
        value = wf().replace("    steps:", "    renamed_steps:")
        with self.assertRaises(ValueError):
            g.unsafe_jobs(value)

    def test_skipped_checkout_is_not_trusted(self):
        value = wf().replace("        with:", "        if: false\n        with:")
        self.assertIn("audit", g.unsafe_jobs(value))

    def test_continue_on_error_checkout_is_not_trusted(self):
        value = wf().replace("        with:", "        continue-on-error: true\n        with:")
        self.assertIn("audit", g.unsafe_jobs(value))

    def test_identity_guard_must_execute_from_checked_out_repo_root(self):
        for directory in ("/tmp/attacker", ".", "${{ runner.temp }}", "../other"):
            with self.subTest(directory=directory):
                val = wf().replace("        run: |",
                                   "        working-directory: " + directory + "\n        run: |")
                self.assertIn("audit", g.unsafe_jobs(val))

    def test_pre_guard_service_container_denied(self):
        for service in ("    services:\n      postgres:\n        image: postgres:16",
                        "    services: {redis: {image: redis:7}}"):
            with self.subTest(service=service):
                val = wf().replace("    steps:", service + "\n    steps:")
                self.assertIn("audit", g.unsafe_jobs(val))

    def test_quoted_pre_guard_services_and_job_defaults_denied(self):
        for key in ('"services"', "'services'", '"container"', "'defaults'"):
            with self.subTest(key=key):
                text = wf().replace("    steps:", "    " + key + ": {db: {image: postgres:16}}\n    steps:")
                with self.assertRaises(ValueError):
                    g.unsafe_jobs(text)

    def test_job_yaml_merge_cannot_synthesize_services_before_identity(self):
        for suffix in ("    <<: *preset", "    ? services: {db: {image: postgres:16}}"):
            with self.subTest(suffix=suffix):
                text = wf().replace("    steps:", suffix + "\n    steps:")
                with self.assertRaises(ValueError):
                    g.unsafe_jobs(text)

    def test_tagged_unknown_or_quoted_job_keys_cannot_hide_services(self):
        for k in ("!!str services", "!service services", '"services"', "'services'",
                  "surprise-setting"):
            with self.subTest(key=k):
                val = wf().replace("    steps:", "    " + k + ": {db: {image: postgres:16}}\n    steps:")
                with self.assertRaises(ValueError):
                    g.unsafe_jobs(val)

    def test_supported_literal_job_metadata_still_allows_guard(self):
        val = wf().replace("    steps:", "    timeout-minutes: 12\n    permissions:\n"
                           "      contents: read\n    steps:")
        self.assertEqual(g.unsafe_jobs(val), {})

    def test_checkout_remote_or_alternate_source_denied(self):
        for setting in ("repository: attacker/fork", "ref: evil-branch",
                        "path: subfolder", "ssh-key: ${{ secrets.KEY }}",
                        "token: ${{ secrets.ELEVATED_TOKEN }}",
                        "sparse-checkout: scripts", "filter: blob:none",
                        "submodules: true", "submodules: recursive", "lfs: true",
                        "clean: false", "persist-credentials: true"):
            with self.subTest(setting=setting):
                val = wf().replace("          persist-credentials: false",
                                   "          persist-credentials: false\n          " + setting)
                self.assertIn("audit", g.unsafe_jobs(val))

    def test_checkout_credentials_must_be_explicitly_disabled(self):
        for val in (wf().replace("        with:\n          persist-credentials: false\n", ""),
                    wf().replace("persist-credentials: false", "persist-credentials: true"),
                    wf().replace("persist-credentials: false", "persist-credentials: ${{ inputs.flag }}")):
            self.assertIn("audit", g.unsafe_jobs(val))

    def test_checkout_rejects_duplicated_or_inline_inputs(self):
        examples = (
            wf().replace("          persist-credentials: false",
                         "          persist-credentials: false\n          persist-credentials: false"),
            wf().replace("        with:\n          persist-credentials: false",
                         "        with: {persist-credentials: false}"),
            wf().replace("        with:", "        with:\n          persist-credentials: false\n        with:"),
        )
        for val in examples:
            self.assertIn("audit", g.unsafe_jobs(val))

    def test_bounded_static_checkout_options_allowed(self):
        val = wf().replace("          persist-credentials: false",
                           "          persist-credentials: false\n          clean: true\n          submodules: false\n"
                           "          lfs: false\n          fetch-depth: 1")
        self.assertEqual(g.unsafe_jobs(val), {})

    def test_checkout_pre_guard_environment_is_not_trusted(self):
        val = wf().replace("        with:", "        env:\n          GIT_CONFIG_GLOBAL: /tmp/malicious\n        with:")
        self.assertIn("audit", g.unsafe_jobs(val))

    def test_unknown_pinned_checkout_commit_denied_before_identity(self):
        for sha in ("a" * 40, "0" * 40, "c" * 40):
            with self.subTest(sha=sha):
                doc = wf().replace(PIN, sha)
                self.assertEqual(g.unsafe_jobs(doc),
                                 {"audit": "missing_early_runner_guard"})

    def test_approved_checkout_revision_is_exact_not_similar(self):
        for value in ("actions/checkout@" + PIN + "-preview",
                      "actions/checkout@" + PIN.upper(),
                      "attacker/checkout@" + PIN):
            with self.subTest(action=value):
                doc = wf().replace("actions/checkout@" + PIN, value)
                self.assertIn("audit", g.unsafe_jobs(doc))

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

    def test_inline_job_mapping_fails_closed(self):
        with self.assertRaises(ValueError):
            g.unsafe_jobs(wf() + "  injected: {runs-on: [self-hosted, zcloud], steps: []}\n")

    def test_alias_job_fails_closed(self):
        with self.assertRaises(ValueError):
            g.unsafe_jobs(wf() + "  injected: *unguarded\n")

    def test_quoted_root_shadow_fails_closed(self):
        with self.assertRaises(ValueError):
            g.unsafe_jobs(wf() + '"jobs": {unsafe: {runs-on: self-hosted}}\n')

    def test_root_yaml_merge_fails_closed(self):
        with self.assertRaises(ValueError):
            g.unsafe_jobs(wf() + "<<: *override\n")

    def test_ambiguous_jobs_root_fails_closed(self):
        with self.assertRaises(ValueError):
            g.unsafe_jobs(wf() + "\njobs:\n  evil:\n    runs-on: self-hosted\n")

    def test_reusable_job_runner_is_unverified_not_implicitly_hosted(self):
        doc = "jobs:\n  reuse:\n    uses: org/repo/.github/workflows/x.yml@" + PIN
        self.assertEqual(g.unsafe_jobs(doc), {"reuse": "delegated_runner_unverified"})

    def test_new_delegated_job_is_fresh_runner_debt(self):
        path = ".github/workflows/new.yml"
        delegated = "jobs:\n  reuse:\n    uses: org/repo/.github/workflows/x.yml@" + PIN
        with mock.patch.object(g, "inventory", return_value=[path]), \
             mock.patch.object(g, "source", side_effect=[None, delegated]):
            result = g.evaluate("a" * 40, "b" * 40)
        self.assertEqual(result["new_findings"], [
            {"path": path, "job": "reuse", "reason": "delegated_runner_unverified"}
        ])

    def test_unmodified_existing_delegated_job_is_not_new_debt(self):
        base = ("name: before\njobs:\n  reuse:\n"
                "    uses: org/repo/.github/workflows/x.yml@" + PIN)
        with mock.patch.object(g, "inventory", return_value=[".github/workflows/legacy.yml"]), \
             mock.patch.object(g, "source", side_effect=[base, base]):
            result = g.evaluate("a" * 40, "b" * 40)
        self.assertEqual(result["new_findings"], [])

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

    def test_unchanged_legacy_job_can_coexist_with_safe_work(self):
        base = wf(guard=False).replace("      - run: echo after", "")
        head = base + (
            "  hosted:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: echo safe\n"
        )
        with mock.patch.object(g, "inventory", return_value=[".github/workflows/legacy.yml"]), \
             mock.patch.object(g, "source", side_effect=[base, head]):
            report = g.evaluate("a" * 40, "b" * 40)
        self.assertEqual(report["new_findings"], [])

    def test_mutating_same_legacy_unsafe_job_is_fresh_debt(self):
        base = wf(guard=False)
        head = base.replace("echo untrusted", "echo untrusted && echo again")
        with mock.patch.object(g, "inventory", return_value=[".github/workflows/legacy.yml"]), \
             mock.patch.object(g, "source", side_effect=[base, head]):
            report = g.evaluate("a" * 40, "b" * 40)
        self.assertEqual(report["new_findings"][0]["reason"], "modified_legacy_unsafe_job")

    def test_legacy_job_changed_runner_but_same_risk_category_denied(self):
        base = wf("[self-hosted, vps]", False)
        head = wf("[self-hosted, zcloud]", False)
        with mock.patch.object(g, "inventory", return_value=[".github/workflows/legacy.yml"]), \
             mock.patch.object(g, "source", side_effect=[base, head]):
            report = g.evaluate("a" * 40, "b" * 40)
        self.assertEqual(report["new_findings"][0]["reason"], "modified_legacy_unsafe_job")

    def test_comment_only_risky_job_change_not_new_debt(self):
        base = wf(guard=False)
        head = base.replace("      - run: echo after", "      # review note\n      - run: echo after")
        with mock.patch.object(g, "inventory", return_value=[".github/workflows/legacy.yml"]), \
             mock.patch.object(g, "source", side_effect=[base, head]):
            report = g.evaluate("a" * 40, "b" * 40)
        self.assertEqual(report["new_findings"], [])

    def test_legacy_manual_vps_job_cannot_be_silently_auto_triggered(self):
        old = wf(guard=False)
        new = old.replace("  workflow_dispatch:", "  push:")
        with mock.patch.object(g, "inventory", return_value=[".github/workflows/legacy.yml"]), \
             mock.patch.object(g, "source", side_effect=[old, new]):
            report = g.evaluate("a" * 40, "b" * 40)
        self.assertEqual(report["new_findings"][0]["reason"],
                         "modified_legacy_unsafe_workflow_envelope")

    def test_legacy_vps_job_cannot_inherit_new_workflow_privileges(self):
        old = wf(guard=False)
        new = old.replace("jobs:", "permissions: write-all\njobs:")
        with mock.patch.object(g, "inventory", return_value=[".github/workflows/legacy.yml"]), \
             mock.patch.object(g, "source", side_effect=[old, new]):
            report = g.evaluate("a" * 40, "b" * 40)
        self.assertEqual(report["new_findings"][0]["reason"],
                         "modified_legacy_unsafe_workflow_envelope")

    def test_legacy_root_comment_change_is_not_privilege_debt(self):
        old = wf(guard=False)
        new = old.replace("jobs:", "# reviewed\njobs:")
        with mock.patch.object(g, "inventory", return_value=[".github/workflows/legacy.yml"]), \
             mock.patch.object(g, "source", side_effect=[old, new]):
            report = g.evaluate("a" * 40, "b" * 40)
        self.assertEqual(report["new_findings"], [])

    def test_changed_only_regression(self):
        with mock.patch.object(g, "inventory", return_value=[".github/workflows/new.yml"]), \
             mock.patch.object(g, "source", side_effect=[None, wf(guard=False)]):
            result = g.evaluate("a" * 40, "b" * 40)
        self.assertEqual(len(result["new_findings"]), 1)
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["mutation_performed"])



class RealGitEvidenceTests(unittest.TestCase):
    def fixture(self, original=None):
        """Build a separate local Git repository without network or system state."""
        from contextlib import contextmanager
        @contextmanager
        def repo():
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run(["git", "init", "-q", tmp], check=True)
                for key, value in (("user.name", "Offline Test"), ("user.email", "test@example.invalid")):
                    subprocess.run(["git", "-C", tmp, "config", key, value], check=True)
                folder = Path(tmp) / ".github" / "workflows"
                folder.mkdir(parents=True)
                file = folder / "vps.yml"
                file.write_text(original or wf("ubuntu-latest", False))
                self.commit(tmp)
                base = self.rev(tmp)
                yield tmp, file, base
        return repo()

    @staticmethod
    def commit(tmp):
        subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
        subprocess.run(["git", "-C", tmp, "commit", "-qm", "offline fixture"], check=True)

    @staticmethod
    def rev(tmp):
        return subprocess.check_output(["git", "-C", tmp, "rev-parse", "HEAD"], text=True).strip()

    def active_git(self, tmp):
        actual = subprocess.check_output
        return mock.patch.object(g, "git", side_effect=lambda *args: actual(
            ["git", *args], cwd=tmp, timeout=20, stderr=subprocess.DEVNULL))

    def active_merge(self, tmp):
        actual = subprocess.call
        return mock.patch.object(g.subprocess, "call", side_effect=lambda argv, **kwargs: actual(
            argv, cwd=tmp, **kwargs))

    def test_real_git_added_unguarded_runner_denied(self):
        with self.fixture() as (tmp, file, base):
            file.write_text(wf(guard=False))
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                report = g.evaluate(base, self.rev(tmp))
            self.assertEqual(len(report["new_findings"]), 1)
            self.assertFalse(report["release_authorized"])

    def test_real_git_new_guarded_runner_allowed(self):
        with self.fixture() as (tmp, file, base):
            file.write_text(wf())
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                report = g.evaluate(base, self.rev(tmp))
            self.assertEqual(report["new_findings"], [])
            self.assertFalse(report["mutation_performed"])

    def test_real_git_changed_legacy_vps_trigger_denied(self):
        with self.fixture(wf(guard=False)) as (tmp, file, base):
            file.write_text(wf(guard=False).replace("  workflow_dispatch:", "  push:"))
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                report = g.evaluate(base, self.rev(tmp))
            self.assertEqual(report["new_findings"][0]["reason"],
                             "modified_legacy_unsafe_workflow_envelope")

    def test_real_git_safe_hosted_job_does_not_reopen_legacy_debt(self):
        with self.fixture(wf(guard=False)) as (tmp, file, base):
            file.write_text(wf(guard=False) + (
                "  added_hosted:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - run: echo source-only\n"
            ))
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                report = g.evaluate(base, self.rev(tmp))
            self.assertEqual(report["new_findings"], [])
            self.assertGreater(report["remaining_debt_in_changed_files"], 0)

    def test_real_git_delegated_job_fails_closed(self):
        with self.fixture() as (tmp, file, base):
            file.write_text(wf("ubuntu-latest", False) + (
                "  delegated:\n"
                "    uses: org/unknown/.github/workflows/vps.yml@" + PIN + "\n"
            ))
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                report = g.evaluate(base, self.rev(tmp))
            self.assertIn("delegated_runner_unverified",
                          [f["reason"] for f in report["new_findings"]])

    def test_real_git_unsafe_root_environment_is_new_runner_debt(self):
        with self.fixture() as (tmp, file, base):
            file.write_text(wf().replace(
                "jobs:", '"env":\n  NODE_OPTIONS: --require=/tmp/agent.js\njobs:'))
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                report = g.evaluate(base, self.rev(tmp))
            self.assertEqual(report["new_findings"][0]["reason"],
                             "missing_early_runner_guard")
            self.assertFalse(report["mutation_performed"])

    def test_real_git_unknown_checkout_sha_is_new_runner_debt(self):
        with self.fixture() as (tmp, file, base):
            file.write_text(wf().replace(PIN, "a" * 40))
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                report = g.evaluate(base, self.rev(tmp))
            self.assertEqual(report["new_findings"][0]["reason"],
                             "missing_early_runner_guard")
            self.assertFalse(report["release_authorized"])

    def test_real_git_quoted_guard_run_shadow_is_new_runner_debt(self):
        with self.fixture() as (tmp, file, base):
            file.write_text(wf().replace(
                "        run: |", '        run: |\n        "run": echo injected'))
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                report = g.evaluate(base, self.rev(tmp))
            self.assertEqual(report["new_findings"][0]["reason"],
                             "missing_early_runner_guard")

    def test_real_git_symlink_workflow_rejected(self):
        with self.fixture() as (tmp, file, base):
            file.unlink()
            file.symlink_to("/tmp/unsafe-source.yml")
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                with self.assertRaises(ValueError):
                    g.evaluate(base, self.rev(tmp))

    def test_real_git_oversize_workflow_refused_before_materializing_blob(self):
        # The prior implementation only checked length after reading the
        # complete attacker-controlled blob into memory.
        with self.fixture() as (tmp, file, base):
            file.write_text("name: padded\\n" + "#" * 256_010, encoding="utf-8")
            self.commit(tmp)
            large_blob = subprocess.check_output(
                ["git", "-C", tmp, "hash-object", str(file)], text=True
            ).strip()
            with self.active_git(tmp), self.active_merge(tmp):
                original_git = g.git
                observed = []

                def recording_git(*args):
                    observed.append(args)
                    return original_git(*args)

                with mock.patch.object(g, "git", side_effect=recording_git):
                    with self.assertRaisesRegex(ValueError, "oversized_or_binary_workflow"):
                        g.evaluate(base, self.rev(tmp))
            self.assertIn(("cat-file", "-s", large_blob), observed)
            self.assertNotIn(("cat-file", "blob", large_blob), observed)

    def test_real_git_exact_size_limit_is_readable(self):
        with self.fixture() as (tmp, file, base):
            prefix = "name: bounded\\njobs:\\n  hosted:\\n    runs-on: ubuntu-latest\\n"
            file.write_bytes(prefix.encode("utf-8") + b"#" * (256_000 - len(prefix)))
            self.commit(tmp)
            with self.active_git(tmp), self.active_merge(tmp):
                # At the exact bound the blob may be read. No self-hosted
                # runner is introduced, so the admission has zero findings.
                report = g.evaluate(base, self.rev(tmp))
            self.assertEqual(report["new_findings"], [])

    def test_real_git_blob_not_commit_rejected(self):
        with self.fixture() as (tmp, file, base):
            blob = subprocess.check_output(["git", "-C", tmp, "hash-object", str(file)], text=True).strip()
            with self.active_git(tmp), self.active_merge(tmp):
                with self.assertRaises(ValueError):
                    g.inventory(blob, base)


if __name__ == "__main__":
    unittest.main()
