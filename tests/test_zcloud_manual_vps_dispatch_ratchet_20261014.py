"""Offline adversarial checks for manual self-hosted dispatch trust ratchet."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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

    def test_known_hosted_runner_images_remain_allowed(self):
        for label in ("ubuntu-latest", "ubuntu-24.04", "windows-latest", "macos-15"):
            with self.subTest(label=label):
                out = guard.evaluate({}, {WF: workflow(runner=label)})
                self.assertTrue(out["ok"])

    def test_no_dispatch_event_is_not_in_scope(self):
        self.assertTrue(guard.evaluate({}, {WF: workflow(event="push:")})["ok"])

    def test_dispatch_after_push_is_not_ignored(self):
        body = workflow().decode().replace("  workflow_dispatch:", "  push:\n  workflow_dispatch:")
        self.assertDenied(body.encode())

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
