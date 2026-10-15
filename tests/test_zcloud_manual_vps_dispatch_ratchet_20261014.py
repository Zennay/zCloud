"""Offline adversarial checks for manual self-hosted dispatch trust ratchet."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import zcloud_manual_vps_dispatch_ratchet_20261014 as guard

SRC = Path(__file__).resolve().parents[1] / "scripts/zcloud_manual_vps_dispatch_ratchet_20261014.py"
CI_WORKFLOW = SRC.parents[1] / ".github/workflows/zcloud-manual-vps-dispatch-ratchet-20261014.yml"
WF = ".github/workflows/sample.yml"
SAFE_IF = (
    "github.event_name == 'workflow_dispatch' && "
    "github.repository == 'Zennay/zCloud' && "
    "github.ref == 'refs/heads/main' && "
    "github.actor == 'Zennay' && "
    "github.triggering_actor == 'Zennay' && "
    "github.run_attempt == 1"
)

def workflow(*, if_statement: str = "", event: str = "workflow_dispatch:",
             runner: str = "[self-hosted, zcloud, vps]", job: str = "prove") -> bytes:
    condition = f"    if: {if_statement}\n" if if_statement else ""
    return (f"name: proof\non:\n  {event}\npermissions:\n  contents: read\n"
            f"jobs:\n  {job}:\n{condition}    runs-on: {runner}\n"
            f"    steps:\n      - run: echo evidence-only\n").encode()

def commit(repo: Path, text: bytes | None) -> str:
    wf = repo / WF
    if text is None:
        wf.unlink(missing_ok=True)
    else:
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_bytes(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Proof",
                    "-c", "user.email=proof@example.invalid",
                    "commit", "--allow-empty", "-m", "fixture"], check=True, capture_output=True)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

class ManualDispatchPolicy(unittest.TestCase):
    def assertDenied(self, body: bytes, expected: int = 1) -> None:
        out = guard.evaluate({}, {WF: body})
        self.assertFalse(out["ok"])
        self.assertEqual(expected, out["new_debt_count"])
        self.assertFalse(out["mutation_performed"])
        self.assertFalse(out["production_mutation_authorized"])

    def test_essential_ratchet_artifacts_are_regular_and_protected(self):
        source = CI_WORKFLOW.read_text(encoding="utf-8")
        paths = (
            ".github/workflows/zcloud-manual-vps-dispatch-ratchet-20261014.yml",
            "scripts/zcloud_manual_vps_dispatch_ratchet_20261014.py",
            "tests/test_zcloud_manual_vps_dispatch_ratchet_20261014.py",
        )
        for protected in paths:
            self.assertIn(f'"{protected}"', source)
        self.assertIn("git ls-tree", source)
        self.assertIn("grep -Eq '^100644 blob [0-9a-f]{40}[[:space:]]'", source)
        self.assertLess(source.index("for protected in"), source.index("policy_path="))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            target = root / paths[0]
            target.parent.mkdir(parents=True)
            target.write_text("on: workflow_dispatch\n", encoding="utf-8")
            sha = commit(root, workflow(if_statement=SAFE_IF))
            tree = subprocess.check_output([
                "git", "-C", str(root), "ls-tree", sha, "--", paths[0],
            ], text=True)
            self.assertTrue(tree.startswith("100644 blob "), tree)
            target.unlink()
            target.symlink_to("relative-target.yml")
            after = commit(root, workflow(if_statement=SAFE_IF))
            tree_symlink = subprocess.check_output([
                "git", "-C", str(root), "ls-tree", after, "--", paths[0],
            ], text=True)
            self.assertTrue(tree_symlink.startswith("120000 blob "), tree_symlink)

    def test_hosted_policy_uses_immutable_baseline_after_bootstrap(self):
        workflow_source = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('git cat-file -e "${BASE_SHA}:${policy_path}"', workflow_source)
        self.assertIn('git show "${BASE_SHA}:${policy_path}"', workflow_source)
        self.assertIn("POLICY_PROVENANCE=trusted_base_sha", workflow_source)
        self.assertIn("POLICY_PROVENANCE=untrusted_bootstrap_review_required", workflow_source)
        self.assertIn("this passing job is not an independent security admission", workflow_source)
        self.assertIn("Independent review is mandatory before merge", workflow_source)
        self.assertIn("GITHUB_STEP_SUMMARY", workflow_source)
        trusted_start = workflow_source.index('git show "${BASE_SHA}:${policy_path}"')
        trusted_execute = workflow_source.index('python3 "${RUNNER_TEMP}/zcloud-trusted-manual-policy.py"')
        untrusted_execute = workflow_source.index('python3 "$policy_path"')
        self.assertLess(trusted_start, trusted_execute)
        self.assertLess(trusted_execute, untrusted_execute)
        unit_test = workflow_source.index("python3 -m unittest discover")
        self.assertLess(trusted_execute, unit_test)
        self.assertLess(untrusted_execute, unit_test)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            file = root / "scripts/zcloud_manual_vps_dispatch_ratchet_20261014.py"
            file.parent.mkdir(parents=True)
            file.write_text("print('TRUSTED')\n", encoding="utf-8")
            base = commit(root, workflow(if_statement=SAFE_IF))
            file.write_text("print('PR_REPLACEMENT')\n", encoding="utf-8")
            head = commit(root, workflow(if_statement=SAFE_IF))
            trusted = subprocess.check_output([
                "git", "-C", str(root), "show",
                base + ":scripts/zcloud_manual_vps_dispatch_ratchet_20261014.py",
            ], text=True)
            current = subprocess.check_output([
                "git", "-C", str(root), "show",
                head + ":scripts/zcloud_manual_vps_dispatch_ratchet_20261014.py",
            ], text=True)
            self.assertEqual("print('TRUSTED')\n", trusted)
            self.assertNotEqual(trusted, current)

    def test_hosted_proof_rejects_forks_before_checkout_and_main_drift(self):
        workflow_source = CI_WORKFLOW.read_text(encoding="utf-8")
        denial = workflow_source.index("Deny foreign PR source before any checkout")
        checkout = workflow_source.index("Checkout exact PR source without credentials")
        self.assertLess(denial, checkout)
        self.assertIn('test "$HEAD_REPOSITORY" = "$EXPECTED_REPOSITORY"', workflow_source)
        self.assertIn("persist-credentials: false", workflow_source)
        self.assertIn("runs-on: ubuntu-latest", workflow_source)
        self.assertNotIn("runs-on: self-hosted", workflow_source)
        preflight = workflow_source.index('git cat-file -e "${HEAD_SHA}^{commit}"')
        self.assertGreater(workflow_source.index("timeout 20 git ls-remote"), preflight)
        self.assertEqual(2, workflow_source.count("timeout 20 git ls-remote"))
        self.assertEqual(2, workflow_source.count(
            "git ls-remote --exit-code https://github.com/Zennay/zCloud.git refs/heads/main",
        ))
        self.assertEqual(2, workflow_source.count('cd "$RUNNER_TEMP"'))
        self.assertEqual(2, workflow_source.count("GIT_CONFIG_GLOBAL=/dev/null"))
        self.assertNotIn("ls-remote --exit-code origin", workflow_source)
        self.assertEqual(2, workflow_source.count('test "$current_main" = "$BASE_SHA"'))
        self.assertEqual(1, workflow_source.count("python3 -m unittest discover"))

    def test_new_untrusted_manual_vps_job_is_denied(self):
        self.assertDenied(workflow())

    def test_valid_identity_guard_is_permitted(self):
        self.assertTrue(guard.evaluate({}, {WF: workflow(if_statement=SAFE_IF)})["ok"])

    def test_missing_each_admission_dimension_denied(self):
        pieces = SAFE_IF.split(" && ")
        for part in pieces:
            with self.subTest(removed=part):
                self.assertDenied(workflow(if_statement=" && ".join(p for p in pieces if p != part)))

    def test_true_or_admission_cannot_bypass_identity(self):
        self.assertDenied(workflow(if_statement="true || (" + SAFE_IF + ")"))

    def test_trust_literals_inside_functions_cannot_fake_authorization(self):
        fake = "contains('" + SAFE_IF + "', 'Zennay')"
        self.assertDenied(workflow(if_statement=fake))

    def test_braced_positive_github_expression_is_accepted(self):
        guarded = "$" + "{{ " + SAFE_IF + " }}"
        self.assertTrue(guard.evaluate({}, {WF: workflow(if_statement=guarded)})["ok"])

    def test_alias_runner_cannot_hide_manual_vps_selection(self):
        self.assertDenied(workflow(runner="*shared_runner"))

    def test_separate_step_condition_does_not_count_as_job_if(self):
        body = workflow().decode().replace("      - run:", f"      - if: {SAFE_IF}\n        run:")
        self.assertDenied(body.encode())

    def test_hosted_job_does_not_incur_vps_debt(self):
        out = guard.evaluate({}, {WF: workflow(runner="ubuntu-latest")})
        self.assertTrue(out["ok"])
        self.assertEqual(0, out["candidate_debt_count"])

    def test_static_custom_runner_labels_are_vps_candidates(self):
        for label in ("vps", "ftmo-research", "custom-linux"):
            with self.subTest(label=label):
                self.assertDenied(workflow(runner=label))

    def test_private_runner_cannot_disguise_itself_as_an_arbitrary_os_version(self):
        for label in ("ubuntu-24.04-personal", "ubuntu-27.1", "windows-2099",
                      "macos-15-acme", "windows-2022-custom"):
            with self.subTest(label=label):
                self.assertDenied(workflow(runner=label))

    def test_official_hosted_label_with_yaml_comment_remains_allowed(self):
        self.assertTrue(guard.evaluate(
            {}, {WF: workflow(runner="ubuntu-24.04 # hosted runner")},
        )["ok"])

    def test_known_hosted_runner_images_remain_allowed(self):
        for label in ("ubuntu-latest", "ubuntu-24.04", "windows-latest", "macos-15"):
            with self.subTest(label=label):
                out = guard.evaluate({}, {WF: workflow(runner=label)})
                self.assertTrue(out["ok"])

    def test_no_dispatch_event_is_not_in_scope(self):
        self.assertTrue(guard.evaluate({}, {WF: workflow(event="push:")})["ok"])

    def test_on_mapping_with_inline_comment_cannot_hide_manual_vps(self):
        for inline in ("on: # a note about the event", "on:\t# configured events"):
            with self.subTest(inline=inline):
                body = workflow().replace(b"on:", inline.encode(), 1)
                self.assertDenied(body)
                guarded = workflow(if_statement=SAFE_IF).replace(b"on:", inline.encode(), 1)
                self.assertTrue(guard.evaluate({}, {WF: guarded})["ok"])

    def test_dispatch_after_push_is_not_ignored(self):
        body = workflow().decode().replace("  workflow_dispatch:", "  push:\n  workflow_dispatch:")
        self.assertDenied(body.encode())

    def test_repair_of_legacy_quoted_jobs_root_does_not_require_parsable_old_header(self):
        safe = workflow(if_statement=SAFE_IF)
        previous = workflow().replace(b"jobs:", b"'jobs':")
        previous2 = workflow().replace(b"jobs:", b'"jobs":')
        for old in (previous, previous2):
            with self.subTest(old=old[:80]):
                new = guard.evaluate({WF: old}, {WF: safe})
                self.assertTrue(new["ok"])
                self.assertEqual(1, new["ambiguous_touched_baseline_count"])
                with self.assertRaises(guard.RefError):
                    guard.evaluate({WF: old}, {WF: old.replace(b"self-hosted", b"vps")})

    def test_repairing_ambiguous_old_yaml_can_be_admitted_safely(self):
        old = workflow().replace(b"  workflow_dispatch:", b"  workflow_dispatch: &unsafe")
        repaired = workflow(if_statement=SAFE_IF)
        good = guard.evaluate({WF: old}, {WF: repaired})
        self.assertTrue(good["ok"])
        self.assertEqual("changed_workflows_only", good["inventory_scope"])
        self.assertEqual(1, good["ambiguous_touched_baseline_count"])
        broken = guard.evaluate({WF: old}, {WF: workflow()})
        self.assertFalse(broken["ok"])
        self.assertEqual(1, broken["new_debt_count"])
        still_ambiguous = workflow().replace(
            b"  workflow_dispatch:", b"  workflow_dispatch: &newname",
        )
        with self.assertRaises(guard.RefError):
            guard.evaluate({WF: old}, {WF: still_ambiguous})

    def test_unmodified_legacy_ambiguous_yaml_does_not_block_safe_changes(self):
        historical = workflow().replace(b"  workflow_dispatch:", b"  workflow_dispatch: &anchor")
        head = {WF: historical, ".github/workflows/new.yml": workflow(if_statement=SAFE_IF)}
        base = {WF: historical}
        self.assertTrue(guard.evaluate(base, head)["ok"])
        with self.assertRaises(guard.RefError):
            guard.evaluate(base, {WF: historical.replace(b"&anchor", b"&changed")})

    def test_glob_pattern_in_event_path_filter_is_not_yaml_alias(self):
        body = workflow(if_statement=SAFE_IF).decode().replace(
            "  workflow_dispatch:",
            "  pull_request:\n    paths: ['.github/workflows/**', '*.yml']\n  workflow_dispatch:",
        )
        self.assertTrue(guard.evaluate({}, {WF: body.encode()})["ok"])

    def test_multiline_flow_events_fail_closed_even_when_dispatch_is_later(self):
        for events in (
            "on: [push,\n  workflow_dispatch]",
            "on: [\n push,\n workflow_dispatch\n]",
            "on: [push, # path note\n workflow_dispatch]",
        ):
            with self.subTest(events=events):
                source = workflow().replace(b"on:\n  workflow_dispatch:", events.encode())
                with self.assertRaisesRegex(guard.RefError, "ambiguous_event_syntax"):
                    guard.risky_manual_jobs(WF, source)

    def test_valid_single_line_flow_dispatch_is_still_checked(self):
        source = workflow().replace(
            b"on:\n  workflow_dispatch:",
            b"on: [push, 'workflow_dispatch']",
        )
        self.assertDenied(source)
        trusted = workflow(if_statement=SAFE_IF).replace(
            b"on:\n  workflow_dispatch:",
            b"on: [push, 'workflow_dispatch']",
        )
        self.assertTrue(guard.evaluate({}, {WF: trusted})["ok"])

    def test_flow_push_only_with_trailing_comment_does_not_incur_debt(self):
        for declaration in ("on: [push] # workflow_dispatch is not configured",
                            "on: push # workflow_dispatch was removed"):
            with self.subTest(declaration=declaration):
                source = workflow().replace(
                    b"on:\n  workflow_dispatch:", declaration.encode(),
                )
                self.assertTrue(guard.evaluate({}, {WF: source})["ok"])

    def test_inline_comment_does_not_hide_actual_dispatch_value(self):
        for declaration in ("on: workflow_dispatch # allowed manually",
                            "on: [push, workflow_dispatch] # both events"):
            with self.subTest(declaration=declaration):
                source = workflow().replace(
                    b"on:\n  workflow_dispatch:", declaration.encode(),
                )
                self.assertDenied(source)

    def test_flow_dispatch_event_detected(self):
        body = workflow().decode().replace("on:\n  workflow_dispatch:", "on: [workflow_dispatch]")
        self.assertDenied(body.encode())

    def test_dynamic_runner_fails_closed(self):
        text = "$" + "{{ fromJSON(vars.RUNNER) }}"
        self.assertDenied(workflow(runner=text))

    def test_reusable_job_without_job_level_trust_fails_closed(self):
        body = ("on:\n  workflow_dispatch:\njobs:\n  reuse:\n"
                "    uses: Zennay/zCloud/.github/workflows/proof.yml@main\n").encode()
        self.assertDenied(body)

    def test_legacy_unsafe_debt_grandfathered_and_new_job_denied(self):
        old = workflow()
        self.assertTrue(guard.evaluate({WF: old}, {WF: old})["ok"])
        new = workflow(job="another")
        self.assertDenied(new)

    def test_legacy_unguarded_job_cannot_keep_waiver_after_global_authority_change(self):
        old = workflow()
        variants = (
            old.replace(b"  contents: read", b"  contents: write"),
            old.replace(b"  workflow_dispatch:", b"  workflow_dispatch:\n  push:"),
            old.replace(b"jobs:", b"env:\n  UNSAFE_CONTROL: enabled\njobs:"),
            old.replace(b"jobs:", b"concurrency:\n  cancel-in-progress: true\njobs:"),
        )
        for new in variants:
            with self.subTest(changed=new[:95]):
                self.assertDeniedDiff(old, new)

    def test_global_display_anchor_mutation_invalidates_unsafe_job_waiver(self):
        for display in (b"name:", b"run-name:"):
            source = workflow().replace(b"name: proof", display + b" &shared ubuntu-latest")
            source = source.replace(
                b"runs-on: [self-hosted, zcloud, vps]", b"runs-on: *shared",
            )
            changed = source.replace(b"&shared ubuntu-latest", b"&shared self-hosted")
            with self.subTest(display=display):
                result = guard.evaluate({WF: source}, {WF: changed})
                self.assertFalse(result["ok"])
                self.assertEqual(1, result["new_debt_count"])

    def test_legacy_unguarded_job_allows_only_display_name_and_comment_edits(self):
        old = workflow()
        for updated in (
            old.replace(b"name: proof", b"name: display-only revision"),
            old.replace(b"name: proof", b"name: proof\n# a pure comment"),
        ):
            with self.subTest(updated=updated[:70]):
                out = guard.evaluate({WF: old}, {WF: updated})
                self.assertTrue(out["ok"])
                self.assertEqual(0, out["new_debt_count"])

    def assertDeniedDiff(self, old: bytes, new: bytes) -> None:
        result = guard.evaluate({WF: old}, {WF: new})
        self.assertFalse(result["ok"])
        self.assertEqual(1, result["new_debt_count"])
        self.assertFalse(result["production_mutation_authorized"])

    def test_modified_legacy_unguarded_job_is_new_debt(self):
        old = workflow()
        new = old.replace(b"echo evidence-only", b"sudo systemctl restart dangerous")
        self.assertDenied(new)
        result = guard.evaluate({WF: old}, {WF: new})
        self.assertEqual(1, result["new_debt_count"])

    def test_unchanged_unguarded_job_with_unrelated_workflow_edit_is_not_new_debt(self):
        old = workflow()
        new = old.replace(b"name: proof", b"name: renamed proof")
        out = guard.evaluate({WF: old}, {WF: new})
        self.assertTrue(out["ok"])
        self.assertEqual(0, out["new_debt_count"])

    def test_fixing_legacy_debt_is_permitted(self):
        old = workflow()
        new = workflow(if_statement=SAFE_IF)
        out = guard.evaluate({WF: old}, {WF: new})
        self.assertTrue(out["ok"])
        self.assertEqual(0, out["candidate_debt_count"])

    def test_multiple_new_risky_jobs_counted(self):
        body = workflow().decode().replace("    steps:", "  other:\n    runs-on: [self-hosted, zcloud, vps]\n    steps:", 1)
        self.assertDenied(body.encode(), expected=2)

    def test_multiple_event_workflow_cannot_run_manual_vps_job_automatically(self):
        stale = SAFE_IF.replace("github.event_name == 'workflow_dispatch' && ", "")
        both_events = workflow(if_statement=stale).replace(
            b"  workflow_dispatch:",
            b"  push:\n    branches: [main]\n  workflow_dispatch:",
        )
        self.assertDenied(both_events)
        admitted = workflow(if_statement=SAFE_IF).replace(
            b"  workflow_dispatch:",
            b"  push:\n    branches: [main]\n  workflow_dispatch:",
        )
        self.assertTrue(guard.evaluate({}, {WF: admitted})["ok"])

    def test_yaml_multiline_event_scalars_fail_closed_instead_of_skipping(self):
        for scalar in (">-", "|", "|-", "!custom"):
            with self.subTest(scalar=scalar):
                body = workflow().replace(
                    b"on:\n  workflow_dispatch:",
                    ("on: " + scalar + "\n  workflow_dispatch").encode(),
                )
                with self.assertRaises(guard.RefError):
                    guard.risky_manual_jobs(WF, body)

    def test_valid_yaml_block_sequence_and_quoted_events_fail_closed(self):
        for event in (
            '  - workflow_dispatch',
            '    - "workflow_dispatch"',
            "   - 'workflow_dispatch'",
            '  "workflow_dispatch":',
            "    'workflow_dispatch':",
        ):
            with self.subTest(event=event):
                body = workflow().replace(b"  workflow_dispatch:", event.encode())
                self.assertDenied(body)

    def test_valid_nonstandard_event_indentation_cannot_hide_manual_dispatch(self):
        for indent in (" ", "   ", "    ", "      "):
            with self.subTest(indent=len(indent)):
                body = workflow().replace(b"  workflow_dispatch:", (indent + "workflow_dispatch:").encode())
                self.assertDenied(body)
        guarded = workflow(if_statement=SAFE_IF).replace(
            b"  workflow_dispatch:", b"    workflow_dispatch:",
        )
        self.assertTrue(guard.evaluate({}, {WF: guarded})["ok"])

    def test_unindented_comments_cannot_hide_manual_event(self):
        src = workflow().replace(b"  workflow_dispatch:", b"  push:\n# owner comment\n  workflow_dispatch:")
        self.assertDenied(src)

    def test_unindented_comments_cannot_hide_later_risky_job(self):
        safe = workflow(if_statement=SAFE_IF, runner="ubuntu-latest")
        body = safe + b"# ordinary YAML comment inside jobs\n"
        body += b"  dangerous:\n    runs-on: [self-hosted, zcloud, vps]\n    steps:\n      - run: echo exposed\n"
        self.assertDenied(body)

    def test_comments_between_two_safe_jobs_stay_allowed(self):
        safe = workflow(if_statement=SAFE_IF, runner="ubuntu-latest")
        body = safe + b"# comment between jobs\n"
        body += b"  another:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo safe\n"
        self.assertTrue(guard.evaluate({}, {WF: body})["ok"])

    def test_quoted_root_jobs_with_quoted_runner_cannot_bypass_policy(self):
        # YAML accepts these spellings, but a strict source analyzer must not
        # turn an unparsed job into a harmless empty job list.
        for replacement in (b'"jobs":', b"'jobs':", b"jobs :",
                            b"jobs: {danger: {'runs-on': self-hosted}}"):
            with self.subTest(root=replacement):
                source = workflow().replace(b"jobs:", replacement)
                with self.assertRaises(guard.RefError):
                    guard.risky_manual_jobs(WF, source)

    def test_tab_indented_runner_authority_is_not_silently_ignored(self):
        source = workflow(if_statement=SAFE_IF, runner="ubuntu-latest")
        source += b"\truns-on: [self-hosted, zcloud, vps]\n"
        with self.assertRaisesRegex(guard.RefError, "unsupported_job_authority_syntax"):
            guard.risky_manual_jobs(WF, source)

    def test_job_authority_after_steps_is_still_scanned(self):
        safe = workflow(if_statement=SAFE_IF, runner="ubuntu-latest")
        for extra in (b"    runs-on : [self-hosted, zcloud, vps]\n",
                      b'    "runs-on": self-hosted\n',
                      b"    if : true\n"):
            with self.subTest(extra=extra):
                changed = safe + extra
                with self.assertRaisesRegex(guard.RefError, "unsupported_job_authority_syntax"):
                    guard.risky_manual_jobs(WF, changed)

    def test_missing_or_ambiguous_runner_never_counts_as_hosted(self):
        source = workflow(if_statement=SAFE_IF).replace(
            b"    runs-on: [self-hosted, zcloud, vps]\n",
            b"    timeout-minutes: 30\n",
        )
        with self.assertRaisesRegex(guard.RefError, "missing_or_ambiguous_job_runner"):
            guard.risky_manual_jobs(WF, source)

    def test_quoted_and_alternate_job_authority_fields_fail_closed(self):
        safe = workflow(if_statement=SAFE_IF)
        for key in (b'runs-on :', b'"runs-on":', b"'runs-on':",
                    b"uses :", b"'uses':", b'if :', b'"if":'):
            with self.subTest(key=key):
                needle = b"    runs-on:" if b"runs-on" in key else (b"    if:" if b"if" in key else b"    runs-on:")
                value = key + (b" [self-hosted, zcloud, vps]" if b"runs-on" in key else
                               b" " + SAFE_IF.encode() if b"if" in key else
                               b" org/repo/.github/workflows/reuse.yml@main")
                changed = safe.replace(needle, b"    " + value)
                with self.assertRaises(guard.RefError):
                    guard.risky_manual_jobs(WF, changed)

    def test_alternate_runner_indentation_is_not_silently_skipped(self):
        for count in (3, 5, 6):
            with self.subTest(indent=count):
                unsafe = workflow().replace(b"    runs-on:", b" " * count + b"runs-on:")
                with self.assertRaises(guard.RefError):
                    guard.risky_manual_jobs(WF, unsafe)

    def test_duplicate_job_authority_keys_denied(self):
        safe = workflow(if_statement=SAFE_IF)
        for key, addition in (
            ("runs-on", b"    runs-on: ubuntu-latest\n"),
            ("if", b"    if: true\n"),
            ("permissions", b"    permissions: write-all\n"),
        ):
            with self.subTest(key=key):
                base = safe
                if key == "permissions":
                    base = safe.replace(b"    runs-on:", b"    permissions: contents: read\n    runs-on:")
                changed = base.replace(b"    steps:\n", addition + b"    steps:\n")
                with self.assertRaises(guard.RefError):
                    guard.risky_manual_jobs(WF, changed)
        reused = b"on:\n  workflow_dispatch:\njobs:\n  reuse:\n    uses: org/repo/.github/workflows/proof.yml@main\n"
        with self.assertRaises(guard.RefError):
            guard.risky_manual_jobs(WF, reused + b"    uses: org/repo/.github/workflows/other.yml@main\n")

    def test_ambiguous_job_identifiers_and_duplicates_denied(self):
        samples = (
            ("quoted", workflow().replace(b"  prove:", b'  "prove":')),
            ("spaced_colon", workflow().replace(b"  prove:", b"  prove :")),
            ("inline_mapping", workflow().replace(
                b"  prove:\n", b"  prove: {runs-on: self-hosted, steps: []}\n",
            )),
            ("duplicate", workflow() + b"  prove:\n    runs-on: ubuntu-latest\n"),
        )
        for name, source in samples:
            with self.subTest(name=name):
                with self.assertRaises(guard.RefError):
                    guard.risky_manual_jobs(WF, source)

    def test_manual_workflow_standard_job_identifier_accepted(self):
        guarded = workflow(if_statement=SAFE_IF)
        self.assertEqual(set(), guard.risky_manual_jobs(WF, guarded))

    def test_duplicate_authority_top_level_keys_denied(self):
        for altered in (
            workflow(if_statement=SAFE_IF) + b"on:\n  workflow_dispatch:\n",
            workflow(if_statement=SAFE_IF) + b"jobs:\n  malicious:\n    runs-on: self-hosted\n",
        ):
            with self.subTest(source=altered[-35:]):
                with self.assertRaises(guard.RefError):
                    guard.risky_manual_jobs(WF, altered)

    def test_unsupported_event_alias_denied(self):
        body = workflow().replace(b"  workflow_dispatch:", b"  workflow_dispatch: &hidden")
        with self.assertRaises(guard.RefError):
            guard.risky_manual_jobs(WF, body)

    def test_readonly_immutable_ref_git_cli_red_green(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            base = commit(root, workflow(if_statement=SAFE_IF))
            head = commit(root, workflow())
            proc = subprocess.run([sys.executable, str(SRC), "--repo-root", str(root),
                                   "--base-sha", base, "--head-sha", head],
                                  text=True, capture_output=True, timeout=20, check=False)
            self.assertEqual(1, proc.returncode, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(1, result["new_debt_count"])
            self.assertFalse(result["production_mutation_authorized"])
            self.assertEqual({WF: workflow(if_statement=SAFE_IF)}, guard.manifest(root, base))
            denied = subprocess.run([sys.executable, str(SRC), "--repo-root", str(root),
                                     "--base-sha", "main", "--head-sha", head],
                                    text=True, capture_output=True, timeout=20, check=False)
            self.assertEqual("ref_must_be_immutable_sha", json.loads(denied.stdout)["reason"])

    def test_git_stream_reader_preserves_wall_clock_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            sha = commit(root, workflow(if_statement=SAFE_IF))
            with mock.patch.object(guard.time, "monotonic", side_effect=(0.0, 21.0)):
                with self.assertRaisesRegex(guard.RefError, "git_read_timeout"):
                    guard.git(root, "rev-parse", sha)

    def test_git_command_stdout_is_bounded_and_exit_status_verified(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            sha = commit(root, workflow(if_statement=SAFE_IF))
            self.assertEqual(sha.encode(), guard.git(root, "rev-parse", "HEAD").strip())
            with mock.patch.object(guard, "MAX_GIT_STDOUT_BYTES", 8):
                with self.assertRaisesRegex(guard.RefError, "git_output_size_limit"):
                    guard.git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(guard.RefError, "unreadable_git_object"):
                guard.git(root, "does-not-exist")

    def test_git_blob_size_is_checked_before_untrusted_blob_is_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            sha = commit(root, workflow(if_statement=SAFE_IF))
            with mock.patch.object(guard, "MAX_BYTES", 8):
                with mock.patch.object(guard, "git", wraps=guard.git) as spy:
                    with self.assertRaisesRegex(guard.RefError, "oversized_workflow"):
                        guard.manifest(root, sha)
                    calls = [c.args[1:] for c in spy.call_args_list]
                    self.assertTrue(any(args[:2] == ("cat-file", "-s") for args in calls))
                    self.assertFalse(any(args[:2] == ("cat-file", "blob") for args in calls))

    def test_git_manifest_rejects_oversized_workflow_inventory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            sha = commit(root, workflow(if_statement=SAFE_IF))
            self.assertEqual(1, len(guard.manifest(root, sha)))
            with mock.patch.object(guard, "MAX_WORKFLOWS", 0):
                with self.assertRaisesRegex(guard.RefError, "workflow_count_limit"):
                    guard.manifest(root, sha)
            with mock.patch.object(guard, "MAX_TOTAL_BYTES", 1):
                with self.assertRaisesRegex(guard.RefError, "aggregate_workflow_bytes_limit"):
                    guard.manifest(root, sha)

    def test_symlinked_workflow_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            wf = root / WF
            wf.parent.mkdir(parents=True)
            wf.symlink_to("/etc/passwd")
            commit_sha = commit_symlink(root)
            with self.assertRaises(guard.RefError):
                guard.manifest(root, commit_sha)

def commit_symlink(repo: Path) -> str:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Proof",
                    "-c", "user.email=proof@example.invalid",
                    "commit", "-qm", "symlink"], check=True, capture_output=True)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

if __name__ == "__main__":
    unittest.main()
