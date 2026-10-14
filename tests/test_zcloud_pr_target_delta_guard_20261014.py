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

    def test_named_checkout_step_with_untrusted_head_ref(self):
        src = SAFE + "      - name: Checkout PR\n        uses: actions/checkout@v4\n        with:\n          ref: ${{ github.event.pull_request.head.sha }}\n"
        self.assertIn("untrusted_pr_checkout", gate.risk_set(src))
        self.assertIn("untrusted_pr_checkout", gate.increasing_risks(SAFE, src))

    def test_named_run_step_with_untrusted_pr_input(self):
        src = SAFE + "      - name: Execute head branch\n        shell: bash\n        run: git fetch origin '${{ github.head_ref }}'\n"
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))

    def test_if_first_step_still_detects_checkout(self):
        src = SAFE + "      - if: always()\n        name: Checkout head\n        uses: actions/checkout@v4\n        with:\n          ref: ${{ github.event.pull_request.head.ref }}\n"
        self.assertIn("untrusted_pr_checkout", gate.risk_set(src))

    def test_if_first_step_detects_run_pr_input(self):
        src = SAFE + "      - if: always()\n        name: Execute\n        run: echo '${{ github.event.pull_request.title }}'\n"
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))

    def test_separate_checkout_and_named_head_metadata_do_not_conflate(self):
        src = SAFE + "      - name: Checkout base\n        uses: actions/checkout@v4\n        with:\n          ref: ${{ github.event.pull_request.base.sha }}\n"
        src += "      - name: Metadata\n        env:\n          HEAD: ${{ github.event.pull_request.head.sha }}\n        run: echo safe\n"
        self.assertNotIn("untrusted_pr_checkout", gate.risk_set(src))

    def test_named_checkout_head_label_variants_are_scanned(self):
        src = SAFE + "      - name: Checkout head\n        uses: actions/checkout@v4\n        with:\n          ref: ${{ github.event.pull_request.head.label }}\n"
        self.assertIn("untrusted_pr_checkout", gate.risk_set(src))

    def test_new_job_condition_is_not_appended_to_earlier_run(self):
        src = SAFE + "  second_job:\n    if: github.event.pull_request.head.repo.full_name != github.repository\n    runs-on: ubuntu-latest\n    steps:\n      - name: safe\n        run: echo safe\n"
        self.assertNotIn("pr_source_in_run_job", gate.risk_set(src))
        self.assertEqual(gate.risk_set(src), set())


    def test_minimally_indented_checkout_step_is_not_invisible(self):
        # YAML accepts indentation other than the conventional six spaces.
        src = """name: varied indentation
on:
 pull_request_target:
permissions:
 contents: read
jobs:
 gate:
  runs-on: ubuntu-latest
  steps:
   - "uses": actions/checkout@v4
     with:
      ref: ${{ github.event.pull_request.head.sha }}
"""
        self.assertIn("untrusted_pr_checkout", gate.risk_set(src))
        self.assertIn("untrusted_pr_checkout", gate.increasing_risks(SAFE, src))

    def test_quoted_run_key_with_untrusted_title_is_detected(self):
        src = """name: quoted run
on:
  pull_request_target:
permissions:
  contents: read
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - 'name': risky step
        'run': printf '%s' '${{ github.event.pull_request.title }}'
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))

    def test_minimal_indent_job_boundaries_do_not_launder_run_exposure(self):
        src = """name: separate jobs
on:
 pull_request_target:
permissions:
 contents: read
jobs:
 first:
  runs-on: ubuntu-latest
  steps:
   - run: echo safe
 second:
  if: github.event.pull_request.head.repo.full_name != github.repository
  runs-on: ubuntu-latest
  steps:
   - run: echo trusted
"""
        self.assertEqual(gate.risk_set(src), set())

    def test_minimal_indent_named_run_still_detects_head_ref(self):
        src = SAFE.replace(
            "      - run: echo 'trusted metadata'",
            "   - name: inspect head\n     run: echo '${{ github.head_ref }}'",
        )
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))


    def test_bracket_expression_pr_title_executed_in_run_is_denied(self):
        src = SAFE + """      - name: Unsafe expression
        run: echo '${{ github['event']['pull_request']['title'] }}'
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))
        self.assertIn("pr_source_in_run_job", gate.increasing_risks(SAFE, src))

    def test_mixed_access_pr_head_checkout_is_denied(self):
        src = SAFE + """      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request['head']['sha'] }}
"""
        self.assertIn("untrusted_pr_checkout", gate.risk_set(src))

    def test_bracket_head_ref_direct_execution_is_denied(self):
        src = SAFE + """      - run: echo '${{ github['head_ref'] }}'
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))

    def test_direct_head_object_expansion_is_denied(self):
        src = SAFE + """      - run: echo '${{ github.event.pull_request.head }}'
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))

    def test_dotted_to_bracket_pr_expression_change_not_laundered(self):
        old = SAFE + """      - run: echo '${{ github.event.pull_request.title }}'
"""
        new = SAFE + """      - run: echo '${{ github.event.pull_request['body'] }}'
"""
        self.assertEqual(gate.risk_counts(old)["pr_source_in_run_job"], 1)
        self.assertEqual(gate.risk_counts(new)["pr_source_in_run_job"], 1)
        self.assertIn("pr_source_in_run_job", gate.increasing_risks(old, new))


    def test_folded_uses_checkout_of_pr_head_is_denied(self):
        src = SAFE + """      - name: Checkout untrusted code
        uses: >-
          actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
"""
        self.assertIn("untrusted_pr_checkout", gate.risk_set(src))

    def test_plain_multiline_checkout_scalar_is_denied(self):
        src = SAFE + """      - uses:
          actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request['head']['ref'] }}
"""
        self.assertIn("untrusted_pr_checkout", gate.risk_set(src))

    def test_folded_trusted_base_checkout_does_not_conflate_other_metadata(self):
        src = SAFE + """      - uses: >-
          actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.base.sha }}
      - name: unrelated metadata
        run: echo safe
"""
        self.assertNotIn("untrusted_pr_checkout", gate.risk_set(src))


    def test_quoted_write_all_permissions_never_miss_authority(self):
        for token in ("'write-all'", '"write-all"'):
            with self.subTest(token=token):
                src = SAFE.replace("permissions:\n  contents: read", f"permissions: {token}")
                self.assertIn("write_all", gate.risk_set(src))
                self.assertIn("write_all", gate.increasing_risks(SAFE, src))

    def test_folded_and_plain_multiline_write_all_are_denied(self):
        for marker in (">-", "|", ""):
            with self.subTest(marker=marker):
                scalar = f"permissions: {marker}\n  write-all"
                src = SAFE.replace("permissions:\n  contents: read", scalar)
                self.assertIn("write_all", gate.risk_set(src))
                self.assertIn("write_all", gate.increasing_risks(SAFE, src))

    def test_quoted_root_permission_key_is_recognized(self):
        src = SAFE.replace("permissions:\n  contents: read", "'permissions':\n  contents: read")
        self.assertNotIn("missing_explicit_root_permissions", gate.risk_set(src))
        privileged = src.replace("contents: read", "contents: write")
        self.assertIn("write_permission", gate.risk_set(privileged))


    def test_aliased_root_event_must_not_look_like_non_target(self):
        # An alias can expand to a privileged trigger absent from source text
        # in the on: value. Refuse even if the explicit trigger is invisible.
        src = SAFE.replace(
            "on:\n  pull_request_target:\n    branches: [main]",
            "on: *privileged_events",
        )
        with self.assertRaisesRegex(gate.Unverified, "aliased_event_trigger"):
            gate.risk_set(src)

    def test_quoted_alias_event_is_not_a_safe_non_target(self):
        src = SAFE.replace(
            "on:\n  pull_request_target:\n    branches: [main]",
            "on: '*privileged_events'",
        )
        with self.assertRaisesRegex(gate.Unverified, "aliased_event_trigger"):
            gate.risk_set(src)

    def test_event_mapping_merge_alias_is_denied_before_early_exit(self):
        src = SAFE.replace(
            "on:\n  pull_request_target:\n    branches: [main]",
            "on:\n  <<: *privileged_events",
        )
        with self.assertRaisesRegex(gate.Unverified, "aliased_event_trigger"):
            gate.risk_set(src)

    def test_event_anchor_is_denied_when_target_unobservable(self):
        src = SAFE.replace(
            "on:\n  pull_request_target:\n    branches: [main]",
            "on: &events\n  push: {}",
        )
        with self.assertRaisesRegex(gate.Unverified, "aliased_event_trigger"):
            gate.risk_set(src)

    def test_other_yaml_anchor_not_used_for_events_keeps_non_target(self):
        src = SAFE.replace("pull_request_target:", "push:")
        src += "x-ignore: &docs\n  note: safe\n"
        self.assertEqual(gate.risk_set(src), set())


    def test_existing_write_all_cannot_hide_second_job_write_all(self):
        original = SAFE.replace("permissions:\n  contents: read", "permissions: write-all")
        candidate = original.replace(
            "    runs-on:", "    permissions: write-all\n    runs-on:"
        )
        self.assertEqual(gate.risk_counts(original)["write_all"], 1)
        self.assertEqual(gate.risk_counts(candidate)["write_all"], 2)
        self.assertIn("write_all", gate.increasing_risks(original, candidate))

    def test_quoted_and_folded_write_all_counts_still_monotonic(self):
        previous = SAFE.replace("permissions:\n  contents: read", "permissions: 'write-all'")
        candidate = previous.replace(
            "    runs-on:", "    permissions: >-\n      write-all\n    runs-on:"
        )
        self.assertEqual(gate.risk_counts(previous)["write_all"], 1)
        self.assertEqual(gate.risk_counts(candidate)["write_all"], 2)
        self.assertIn("write_all", gate.increasing_risks(previous, candidate))

    def test_repeat_write_all_rewriting_comments_not_added_authority(self):
        previous = SAFE.replace("permissions:\n  contents: read", "permissions: write-all")
        self.assertNotIn("write_all", gate.increasing_risks(previous, previous + "# write-all\n"))


    def test_explicit_pull_merge_ref_checkout_is_never_trusted(self):
        source = SAFE + """      - uses: actions/checkout@v4
        with:
          ref: refs/pull/42/merge
"""
        self.assertIn("untrusted_pr_checkout", gate.risk_set(source))
        self.assertIn("untrusted_pr_checkout", gate.increasing_risks(SAFE, source))

    def test_interpolated_pull_head_ref_checkout_is_denied(self):
        source = SAFE + """      - name: Checkout pull request
        uses: actions/checkout@v4
        with:
          ref: refs/pull/${{ github.event.number }}/head
"""
        self.assertIn("untrusted_pr_checkout", gate.risk_set(source))

    def test_literal_pull_ref_in_run_is_denied(self):
        source = SAFE + """      - run: git checkout refs/pull/78/merge
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(source))

    def test_existing_pull_ref_does_not_launder_new_pull_ref_identity(self):
        previous = SAFE + """      - uses: actions/checkout@v4
        with:
          ref: refs/pull/42/merge
"""
        candidate = previous.replace("refs/pull/42/merge", "refs/pull/43/head")
        self.assertEqual(gate.risk_counts(previous)["untrusted_pr_checkout"], 1)
        self.assertEqual(gate.risk_counts(candidate)["untrusted_pr_checkout"], 1)
        self.assertIn("untrusted_pr_checkout", gate.increasing_risks(previous, candidate))

    def test_trusted_main_ref_checkout_remains_unflagged(self):
        source = SAFE + """      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.base.sha }}
"""
        self.assertNotIn("untrusted_pr_checkout", gate.risk_set(source))


    def test_write_all_relocation_from_job_to_workflow_is_escalation(self):
        before = SAFE.replace("    runs-on:", "    permissions: write-all\n    runs-on:")
        after = SAFE.replace("permissions:\n  contents: read", "permissions: write-all")
        self.assertEqual(gate.risk_counts(before)["write_all"], 1)
        self.assertEqual(gate.risk_counts(after)["write_all"], 1)
        self.assertIn("write_all", gate.increasing_risks(before, after))
        self.assertEqual(
            gate.risk_signatures(after)["write_all"]["workflow_root"], 1
        )

    def test_restricting_workflow_write_all_to_one_job_is_not_expansion(self):
        before = SAFE.replace("permissions:\n  contents: read", "permissions: write-all")
        after = SAFE.replace("    runs-on:", "    permissions: write-all\n    runs-on:")
        self.assertNotIn("write_all", gate.increasing_risks(before, after))

    def test_quoted_root_write_all_also_gains_root_risk_identity(self):
        before = SAFE.replace("    runs-on:", "    permissions: 'write-all'\n    runs-on:")
        after = SAFE.replace("permissions:\n  contents: read", '"permissions": "write-all"')
        self.assertIn("write_all", gate.increasing_risks(before, after))


    def test_pr_title_passed_through_step_env_into_shell_is_risky(self):
        original = SAFE
        candidate = SAFE + """      - name: command from PR title
        env:
          COMMAND: ${{ github.event.pull_request.title }}
        run: bash -c "$COMMAND"
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(candidate))
        self.assertIn("pr_source_in_run_job", gate.increasing_risks(original, candidate))

    def test_pr_env_data_not_used_by_run_is_not_shell_exposure(self):
        candidate = SAFE + """      - name: metadata
        env:
          HEAD: ${{ github.event.pull_request.head.sha }}
        run: echo safe
"""
        self.assertNotIn("pr_source_in_run_job", gate.risk_set(candidate))

    def test_pr_env_in_different_step_does_not_conflate_with_execution(self):
        candidate = SAFE + """      - name: metadata only
        env:
          CODE: ${{ github.event.pull_request.body }}
      - name: safe job
        run: echo "$CODE"
"""
        self.assertNotIn("pr_source_in_run_job", gate.risk_set(candidate))

    def test_braced_shell_variable_with_quoted_env_key(self):
        candidate = SAFE + """      - name: command
        'env':
          'SCRIPT': ${{ github.event.pull_request['body'] }}
        run: bash -c "${SCRIPT}"
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(candidate))

    def test_github_expression_env_binding_reused_in_run(self):
        candidate = SAFE + """      - name: head command
        env:
          CMD: ${{ github.event.pull_request.head.ref }}
        run: eval '${{ env.CMD }}'
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(candidate))

    def test_equal_count_env_pr_source_identity_swap_denied(self):
        previous = SAFE + """      - env:
          PAYLOAD: ${{ github.event.pull_request.title }}
        run: bash -c "$PAYLOAD"
"""
        candidate = previous.replace(
            "github.event.pull_request.title", "github.event.pull_request.body"
        )
        self.assertEqual(gate.risk_counts(previous)["pr_source_in_run_job"], 1)
        self.assertEqual(gate.risk_counts(candidate)["pr_source_in_run_job"], 1)
        self.assertIn("pr_source_in_run_job", gate.increasing_risks(previous, candidate))


    def test_dynamic_pr_number_builds_untrusted_checkout_ref(self):
        src = SAFE + """      - uses: actions/checkout@v4
        with:
          ref: ${{ format('refs/{0}/{1}', 'pull', github.event.pull_request.number) }}
"""
        self.assertIn("untrusted_pr_checkout", gate.risk_set(src))
        self.assertIn("untrusted_pr_checkout", gate.increasing_risks(SAFE, src))

    def test_merge_commit_sha_is_untrusted_checkout_ref(self):
        src = SAFE + """      - name: unsafe merge checkout
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.merge_commit_sha }}
"""
        self.assertIn("untrusted_pr_checkout", gate.risk_set(src))

    def test_github_event_number_in_run_is_untrusted_pr_data(self):
        src = SAFE + """      - run: echo '${{ github.event.number }}'
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))

    def test_bracket_pr_number_in_run_is_untrusted_pr_data(self):
        src = SAFE + """      - run: echo '${{ github['event']['pull_request']['number'] }}'
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))

    def test_safe_number_metadata_in_concurrency_does_not_run(self):
        src = SAFE + """concurrency:
  group: reader-${{ github.event.pull_request.number }}
"""
        self.assertNotIn("pr_source_in_run_job", gate.risk_set(src))

    def test_pr_number_to_merge_commit_identity_swap_is_not_laundered(self):
        prior = SAFE + """      - run: echo '${{ github.event.pull_request.number }}'
"""
        candidate = prior.replace("pull_request.number", "pull_request.merge_commit_sha")
        self.assertEqual(gate.risk_counts(prior)["pr_source_in_run_job"], 1)
        self.assertEqual(gate.risk_counts(candidate)["pr_source_in_run_job"], 1)
        self.assertIn("pr_source_in_run_job", gate.increasing_risks(prior, candidate))


    def test_job_env_pr_body_executed_with_bash_c_is_denied(self):
        src = """name: job env risk
on:
  pull_request_target:
permissions:
  contents: read
jobs:
  examine:
    runs-on: ubuntu-latest
    env:
      PAYLOAD: ${{ github.event.pull_request.body }}
    steps:
      - name: run payload
        run: bash -c "$PAYLOAD"
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))
        self.assertIn("pr_source_in_run_job", gate.increasing_risks(SAFE, src))

    def test_job_env_not_executed_remains_metadata_only(self):
        src = """name: safe job
on:
  pull_request_target:
permissions:
  contents: read
jobs:
  examine:
    runs-on: ubuntu-latest
    env:
      TITLE: ${{ github.event.pull_request.title }}
    steps:
      - run: echo "$TITLE"
"""
        self.assertNotIn("pr_source_in_run_job", gate.risk_set(src))

    def test_job_env_does_not_cross_job_boundary(self):
        src = """name: job isolation
on:
  pull_request_target:
permissions:
  contents: read
jobs:
  metadata:
    runs-on: ubuntu-latest
    env:
      SCRIPT: ${{ github.event.pull_request.title }}
    steps:
      - run: echo metadata
  execute:
    runs-on: ubuntu-latest
    steps:
      - run: eval "$SCRIPT"
"""
        self.assertNotIn("pr_source_in_run_job", gate.risk_set(src))

    def test_job_env_pr_number_to_head_swap_detected_at_equal_count(self):
        before = """name: job env identities
on:
  pull_request_target:
permissions:
  contents: read
jobs:
  eval:
    runs-on: ubuntu-latest
    env:
      INPUT: ${{ github.event.pull_request.number }}
    steps:
      - run: eval "$INPUT"
"""
        after = before.replace("pull_request.number", "pull_request.head.sha")
        self.assertEqual(gate.risk_counts(before)["pr_source_in_run_job"], 1)
        self.assertEqual(gate.risk_counts(after)["pr_source_in_run_job"], 1)
        self.assertIn("pr_source_in_run_job", gate.increasing_risks(before, after))

    def test_job_env_safe_immutable_sha_is_not_shell_execution(self):
        src = """name: source-only fetch
on:
  pull_request_target:
permissions:
  contents: read
jobs:
  guard:
    runs-on: ubuntu-latest
    env:
      HEAD_SHA: ${{ github.event.pull_request.head.sha }}
    steps:
      - run: git fetch origin "$HEAD_SHA"
"""
        self.assertNotIn("pr_source_in_run_job", gate.risk_set(src))


    def test_workflow_env_pr_title_consumed_by_exec_denied(self):
        candidate = """name: globally inherited danger
on:
  pull_request_target:
permissions:
  contents: read
env:
  GLOBAL_COMMAND: ${{ github.event.pull_request.title }}
jobs:
  execute:
    runs-on: ubuntu-latest
    steps:
      - run: eval "$GLOBAL_COMMAND"
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(candidate))
        self.assertIn("pr_source_in_run_job", gate.increasing_risks(SAFE, candidate))

    def test_workflow_env_pr_data_as_nonexec_argument_not_denied(self):
        candidate = """name: global metadata
on:
  pull_request_target:
permissions:
  contents: read
env:
  EXACT_HEAD: ${{ github.event.pull_request.head.sha }}
jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - run: git fetch origin "$EXACT_HEAD"
"""
        self.assertNotIn("pr_source_in_run_job", gate.risk_set(candidate))

    def test_workflow_env_pr_data_not_used_by_any_run_is_safe(self):
        candidate = """name: independent metadata
on:
  pull_request_target:
permissions:
  contents: read
env:
  PR_NUMBER: ${{ github.event.pull_request.number }}
jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - run: eval "$OTHER"
"""
        self.assertNotIn("pr_source_in_run_job", gate.risk_set(candidate))

    def test_workflow_env_identity_swap_denied_at_equal_count(self):
        before = """name: global env
on:
  pull_request_target:
permissions:
  contents: read
env:
  COMMAND: ${{ github.event.pull_request.title }}
jobs:
  execute:
    runs-on: ubuntu-latest
    steps:
      - run: sh -c "$COMMAND"
"""
        after = before.replace("pull_request.title", "pull_request.body")
        self.assertEqual(gate.risk_counts(before)["pr_source_in_run_job"], 1)
        self.assertEqual(gate.risk_counts(after)["pr_source_in_run_job"], 1)
        self.assertIn("pr_source_in_run_job", gate.increasing_risks(before, after))


    def test_bash_login_command_execution_from_pr_env_denied(self):
        src = SAFE + """      - env:
          SCRIPT: ${{ github.event.pull_request.body }}
        run: bash -lc "$SCRIPT"
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))

    def test_shell_combined_flags_exec_from_pr_env_denied(self):
        src = SAFE + """      - env:
          SCRIPT: ${{ github.event.pull_request.title }}
        run: sh -ec "$SCRIPT"
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))

    def test_node_eval_from_pr_env_denied(self):
        src = SAFE + """      - env:
          SOURCE: ${{ github.event.pull_request.body }}
        run: node -e "$SOURCE"
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))

    def test_dot_source_execution_from_pr_env_denied(self):
        src = SAFE + """      - env:
          FILEPATH: ${{ github.event.pull_request.head.ref }}
        run: . "$FILEPATH"
"""
        self.assertIn("pr_source_in_run_job", gate.risk_set(src))

    def test_safe_job_sha_fetch_without_shell_eval_still_allowed(self):
        src = SAFE + """      - env:
          EXACT_HEAD: ${{ github.event.pull_request.head.sha }}
        run: git fetch --no-tags origin "$EXACT_HEAD"
"""
        self.assertNotIn("pr_source_in_run_job", gate.risk_set(src))


    def test_job_env_pr_head_ref_passed_to_checkout_denied(self):
        candidate = """name: indirect PR checkout
on:
  pull_request_target:
permissions:
  contents: read
jobs:
  checkout:
    runs-on: ubuntu-latest
    env:
      PR_REF: ${{ github.event.pull_request.head.ref }}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ env.PR_REF }}
"""
        self.assertIn("untrusted_pr_checkout", gate.risk_set(candidate))
        self.assertIn("untrusted_pr_checkout", gate.increasing_risks(SAFE, candidate))

    def test_workflow_env_pr_merge_sha_forwarded_to_checkout_denied(self):
        candidate = """name: inherited checkout
on:
  pull_request_target:
permissions:
  contents: read
env:
  PR_MERGE: ${{ github.event.pull_request.merge_commit_sha }}
jobs:
  checkout:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ env.PR_MERGE }}
"""
        self.assertIn("untrusted_pr_checkout", gate.risk_set(candidate))

    def test_job_env_pr_checkout_does_not_flow_into_another_job(self):
        candidate = """name: no shared job env
on:
  pull_request_target:
permissions:
  contents: read
jobs:
  metadata:
    runs-on: ubuntu-latest
    env:
      REV: ${{ github.event.pull_request.head.sha }}
    steps:
      - run: echo harmless
  checkout:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ env.REV }}
"""
        self.assertNotIn("untrusted_pr_checkout", gate.risk_set(candidate))

    def test_unreferenced_job_env_does_not_contaminate_checkout(self):
        candidate = """name: base only
on:
  pull_request_target:
permissions:
  contents: read
jobs:
  checkout:
    runs-on: ubuntu-latest
    env:
      HEAD: ${{ github.event.pull_request.head.sha }}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.base.sha }}
"""
        self.assertNotIn("untrusted_pr_checkout", gate.risk_set(candidate))

    def test_job_env_checkout_ref_source_swap_cannot_launder_risk(self):
        before = """name: indirect
on:
  pull_request_target:
permissions:
  contents: read
jobs:
  checkout:
    runs-on: ubuntu-latest
    env:
      REF: ${{ github.event.pull_request.head.sha }}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ env.REF }}
"""
        after = before.replace("pull_request.head.sha", "pull_request.merge_commit_sha")
        self.assertEqual(gate.risk_counts(before)["untrusted_pr_checkout"], 1)
        self.assertEqual(gate.risk_counts(after)["untrusted_pr_checkout"], 1)
        self.assertIn("untrusted_pr_checkout", gate.increasing_risks(before, after))

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

    def test_benign_head_metadata_cannot_offset_added_unsafe_run(self):
        dangerous = "      - run: git checkout '${{ github.head_ref }}'\n"
        harmless = "      - name: metadata\n        env:\n          HEAD: ${{ github.head_ref }}\n        run: echo safe\n"
        previous = SAFE + dangerous + harmless
        candidate = SAFE + dangerous + dangerous
        self.assertEqual(gate.risk_counts(previous)["pr_source_in_run_job"], 1)
        self.assertEqual(gate.risk_counts(candidate)["pr_source_in_run_job"], 2)
        self.assertIn("pr_source_in_run_job", gate.increasing_risks(previous, candidate))

    def test_benign_head_metadata_cannot_offset_added_unsafe_checkout(self):
        dangerous = "      - name: checkout\n        uses: actions/checkout@v4\n        with:\n          ref: ${{ github.event.pull_request.head.sha }}\n"
        harmless = "      - name: metadata\n        env:\n          HEAD: ${{ github.event.pull_request.head.sha }}\n        run: echo safe\n"
        previous = SAFE + dangerous + harmless
        candidate = SAFE + dangerous + dangerous
        self.assertEqual(gate.risk_counts(previous)["untrusted_pr_checkout"], 1)
        self.assertEqual(gate.risk_counts(candidate)["untrusted_pr_checkout"], 2)
        self.assertIn("untrusted_pr_checkout", gate.increasing_risks(previous, candidate))

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
        self.assertIn("fetch-depth: 0", trusted)
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

    def test_external_pr_heads_explicitly_denied_not_silently_skipped(self):
        trusted = (TARGET.parents[1] / ".github/workflows/zcloud-pr-target-trusted-base-20261014.yml").read_text()
        self.assertIn("  deny-foreign-head:\n", trusted)
        denial = trusted.split("  deny-foreign-head:\n", 1)[1]
        self.assertIn("github.event.pull_request.head.repo.full_name != github.repository", denial)
        self.assertIn("PR_TARGET_RISK=DENY reason=foreign_head_repository", denial)
        self.assertIn("PRODUCTION_MUTATION_AUTHORIZED=false", denial)
        self.assertIn("exit 1", denial)
        self.assertIn("runs-on: ubuntu-latest", denial)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", trusted)
        self.assertEqual(gate.risk_set(trusted), set())

    def test_trusted_base_denies_its_own_modified_trust_roots(self):
        trusted = (TARGET.parents[1] / ".github/workflows/zcloud-pr-target-trusted-base-20261014.yml").read_text()
        self.assertIn("trust_root_changes=\"$(git -C trusted diff --name-only", trusted)
        self.assertIn('if [ -n "$trust_root_changes" ]; then', trusted)
        self.assertIn("PR_TARGET_TRUST_ROOT_REVIEW_REQUIRED", trusted)
        for protected in (
            "scripts/zcloud_pr_target_delta_guard_20261014.py",
            "tests/test_zcloud_pr_target_delta_guard_20261014.py",
            ".github/workflows/zcloud-pr-target-risk-ratchet-20261014.yml",
            ".github/workflows/zcloud-pr-target-trusted-base-20261014.yml",
        ):
            self.assertIn(protected, trusted)
        self.assertLess(trusted.index("PR_TARGET_TRUST_ROOT_REVIEW_REQUIRED"),
                        trusted.index("(cd trusted && python3 -m unittest"))

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
