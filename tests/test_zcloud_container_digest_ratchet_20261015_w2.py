"""Adversarial tests for the source-only GitHub Actions container digest guard."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_container_digest_ratchet_20261015_w2 as guard

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/zcloud_container_digest_ratchet_20261015_w2.py"
DIGEST = "a" * 64


def run_git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class ContainerDigestGuardTests(unittest.TestCase):
    def scan(self, body: str):
        return guard._scan(".github/workflows/sample.yml", body)

    def test_pinned_job_and_service_images(self):
        source = f"""jobs:
  verify:
    runs-on: ubuntu-latest
    container:
      image: ghcr.io/acme/test@sha256:{DIGEST}
    services:
      redis:
        image: redis:7@sha256:{DIGEST}
"""
        self.assertEqual([], self.scan(source))

    def test_new_tagged_image_is_risk(self):
        self.assertEqual("image", self.scan("      image: redis:latest\n")[0].kind)

    def test_inline_container_tag_is_risk(self):
        self.assertEqual("inline_container", self.scan("    container: ubuntu:24.04\n")[0].kind)

    def test_inline_pinned_container_is_safe(self):
        self.assertEqual([], self.scan(f"    container: alpine@sha256:{DIGEST}\n"))

    def test_docker_uses_tag_vs_digest(self):
        self.assertEqual("docker_uses", self.scan("      - uses: docker://alpine:3\n")[0].kind)
        self.assertEqual([], self.scan(f"      - uses: docker://alpine@sha256:{DIGEST}\n"))

    def test_invalid_short_digest_denied(self):
        self.assertEqual(1, len(self.scan("    image: foo@sha256:1234\n")))

    def test_dynamic_container_image_denied(self):
        self.assertEqual(1, len(self.scan("    image: ${{ inputs.image }}\n")))

    def test_dynamic_digest_not_trusted(self):
        self.assertEqual(1, len(self.scan(f"    image: ${{{{ inputs.image }}}}@sha256:{DIGEST}\n")))

    def test_yaml_alias_not_trusted(self):
        self.assertEqual(1, len(self.scan("    image: *base_image\n")))

    def test_folded_multiline_image_not_trusted(self):
        self.assertEqual(1, len(self.scan("    image: >\n      redis:latest\n")))

    def test_flow_mapping_image_needs_review(self):
        source = f"    container: {{image: redis@sha256:{DIGEST}}}\n"
        self.assertEqual("inline_container", self.scan(source)[0].kind)
        self.assertEqual(1, len(self.scan("    container: {image: redis:latest}\n")))

    def test_quoted_image_property_is_scanned(self):
        self.assertEqual(1, len(self.scan("    'image': redis:latest\n")))
        self.assertEqual(1, len(self.scan('    "container": alpine:latest\n')))

    def test_digest_only_in_yaml_comment_must_not_pin_tag(self):
        self.assertEqual(1, len(self.scan(
            f"    image: redis:latest # old@sha256:{DIGEST}\n"
        )))

    def test_digest_must_cover_entire_image_reference(self):
        self.assertEqual(1, len(self.scan(
            f"    image: redis@sha256:{DIGEST} unexpected\n"
        )))
        self.assertEqual([], self.scan(
            f"    image: 'redis@sha256:{DIGEST}' # tracked source\n"
        ))

    def test_quoted_pinned_image_is_valid(self):
        self.assertEqual([], self.scan(f"      image: 'redis@sha256:{DIGEST}'\n"))

    def test_comment_and_shell_run_body_not_a_container_key(self):
        content = """# image: redis:latest
jobs:
  test:
    steps:
      - name: fake
        run: |
          image: redis:latest
          docker:
            image: redis:latest
      - uses: actions/checkout@1234
"""
        self.assertEqual([], self.scan(content))

    def test_false_positive_cannot_suppress_new_changed_risk(self):
        path = ".github/workflows/x.yml"
        old = self.scan("  image: alpine:3\n")
        new = self.scan("  image: alpine:4\n")
        self.assertEqual(1, guard.compare(old, new)["new_findings"])
        self.assertEqual(0, guard.compare(old, old)["new_findings"])
        self.assertEqual(0, guard.compare(old, [])["new_findings"])

    def test_duplicate_new_finding_cannot_be_hidden(self):
        old = self.scan("  image: alpine:3\n")
        new = self.scan("  image: alpine:3\n  image: alpine:3\n")
        # Whole-file risk identity means *both* old and duplicated unsafe
        # scalars require review when the surrounding workflow changed.
        self.assertEqual(2, guard.compare(old, new)["new_findings"])

    def test_json_is_bounded_and_non_authorizing(self):
        result = guard.compare([], self.scan("  image: alpine:latest\n") * 30)
        self.assertEqual(30, result["new_findings"])
        self.assertEqual(25, len(result["new_risks"]))
        for key in ("release_authorized", "deployment_authorized", "mutation_performed"):
            self.assertIs(result[key], False)

    def _init(self, root: Path) -> tuple[str, Path]:
        run_git(root, "init", "-q", "-b", "main")
        run_git(root, "config", "user.name", "Source Test")
        run_git(root, "config", "user.email", "source@example.invalid")
        workflow = root / ".github" / "workflows" / "container.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(f"jobs:\n  test:\n    container: alpine@sha256:{DIGEST}\n")
        run_git(root, "add", ".")
        run_git(root, "commit", "-qm", "base")
        return run_git(root, "rev-parse", "HEAD"), workflow

    def test_real_git_tree_and_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, path = self._init(root)
            path.write_text("jobs:\n  test:\n    container: alpine:latest\n")
            run_git(root, "add", ".")
            run_git(root, "commit", "-qm", "tag drift")
            head = run_git(root, "rev-parse", "HEAD")
            before = path.read_bytes()
            self.assertEqual("inline_container", guard._scan(path.as_posix(), path.read_text())[0].kind)
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(1, check.returncode, check.stderr)
            payload = json.loads(check.stdout)
            self.assertEqual(1, payload["new_findings"])
            self.assertFalse(payload["mutation_performed"])
            self.assertEqual(before, path.read_bytes())

    def test_real_git_symlink_metadata_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, _path = self._init(root)
            target = root / ".github" / "actions" / "comp" / "action.yml"
            target.parent.mkdir(parents=True)
            (root / "outside.yml").write_text("name: dangerous\n")
            target.symlink_to(root / "outside.yml")
            run_git(root, "add", ".github")
            run_git(root, "commit", "-qm", "symlink")
            head = run_git(root, "rev-parse", "HEAD")
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(2, check.returncode)
            self.assertFalse(json.loads(check.stdout)["inventory_complete"])

    def test_real_git_deleted_workflow_tree_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, workflow = self._init(root)
            workflow.unlink()
            run_git(root, "add", "-A")
            run_git(root, "commit", "-qm", "deleted all workflows")
            head = run_git(root, "rev-parse", "HEAD")
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(2, check.returncode, check.stdout)
            self.assertFalse(json.loads(check.stdout)["inventory_complete"])

    def test_real_git_symlinked_actions_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, _workflow = self._init(root)
            (root / "other").mkdir()
            (root / "other" / "action.yml").write_text("name: untrusted\n")
            (root / ".github" / "actions").symlink_to(root / "other")
            run_git(root, "add", ".github")
            run_git(root, "commit", "-qm", "replace actions directory with symlink")
            head = run_git(root, "rev-parse", "HEAD")
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(2, check.returncode, check.stdout)
            self.assertFalse(json.loads(check.stdout)["inventory_complete"])

    def test_flow_style_docker_steps_must_not_escape_image_pin_gate(self):
        self.assertEqual(1, len(self.scan("    - {uses: docker://alpine:latest}\n")))
        self.assertEqual(1, len(self.scan("    - {'uses': 'docker://alpine:latest'}\n")))
        self.assertEqual(1, len(self.scan('    - {"uses": "docker://alpine:latest"}\n')))

    def test_escaped_double_quoted_yaml_keys_need_review(self):
        for key in (
            r'"\u0069mage"', r'"\x69mage"', r'"\U00000069mage"',
            r'"\u0063ontainer"', r'"\u0075ses"',
            r'"\u0069\u006d\u0061\u0067\u0065"',
        ):
            with self.subTest(key=key):
                self.assertEqual(1, len(self.scan(f"    {key}: alpine:latest\n")))

    def test_explicit_yaml_mapping_keys_need_review(self):
        self.assertEqual(1, len(self.scan("    ? image\n    : alpine:latest\n")))
        self.assertEqual(1, len(self.scan("    ? 'uses'\n    : docker://alpine:latest\n")))

    def test_encoded_flow_docker_uses_key_cannot_hide_tag(self):
        for key in (r'"\u0075ses"', r'"\x75ses"', r'"\U00000075ses"'):
            with self.subTest(key=key):
                self.assertEqual(1, len(
                    self.scan(f'    - {{{key}: "docker://alpine:latest"}}\n')
                ))

    def test_multiline_uses_block_requires_review(self):
        for style in (">", ">-", "|", "|-"):
            with self.subTest(style=style):
                self.assertEqual(1, len(
                    self.scan(f"      - uses: {style}\n          docker://alpine:latest\n")
                ))

    def test_hosted_no_new_debt_gate_runs_for_all_workflow_and_action_edits(self):
        workflow = (ROOT / ".github/workflows" /
                    "zcloud-container-digest-ratchet-20261015-w2.yml").read_text()
        self.assertNotIn("    paths:", workflow.split("permissions:", 1)[0])
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("Reject untrusted PR provenance before checkout", workflow)
        self.assertNotIn("    if: >-", workflow)
        self.assertLess(
            workflow.index("Reject untrusted PR provenance"),
            workflow.index("Checkout immutable PR revision"),
        )
        self.assertNotIn("runs-on: [self-hosted", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("fetch-depth: 1", workflow)
        self.assertIn('test "$HEAD_REPOSITORY" = "$SOURCE_REPOSITORY"', workflow)
        self.assertIn('git fetch --no-tags --depth=1 origin refs/heads/main', workflow)
        self.assertIn('test "$BASE_SHA" = "$EXPECTED_BASE"', workflow)
        self.assertIn('test "$(git rev-parse HEAD)" = "$HEAD_SHA"', workflow)
        self.assertIn('GITHUB_STEP_SUMMARY', workflow)
        self.assertIn('guard.dutch_operator_summary(result, status)', workflow)
        self.assertIn('CONTAINER_SOURCE_EVIDENCE_DENIED=1', workflow)
        self.assertIn('scanner_status=$?', workflow)
        self.assertIn('SCAN_EXIT="$scanner_status"', workflow)

    def test_action_metadata_selection_in_repo_root_and_nested_paths(self):
        self.assertTrue(guard._selected("action.yml"))
        self.assertTrue(guard._selected("ci/local/action.yaml"))
        self.assertTrue(guard._selected("tools/deploy/action.yml"))
        self.assertFalse(guard._selected("tools/not-an-action.yml"))
        self.assertFalse(guard._selected("src/example.py"))

    def test_real_git_nested_action_docker_tag_drift_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, _workflow = self._init(root)
            action = root / "tools" / "local-probe" / "action.yaml"
            action.parent.mkdir(parents=True)
            action.write_text(
                f"name: local\nruns:\n  using: docker\n  image: example/probe@sha256:{DIGEST}\n",
                encoding="utf-8",
            )
            run_git(root, "add", ".")
            run_git(root, "commit", "-qm", "pinned action")
            pinned = run_git(root, "rev-parse", "HEAD")
            action.write_text("name: local\nruns:\n  using: docker\n  image: example/probe:latest\n", encoding="utf-8")
            run_git(root, "add", ".")
            run_git(root, "commit", "-qm", "mutable action tag")
            head = run_git(root, "rev-parse", "HEAD")
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", pinned, "--head-sha", head],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(1, check.returncode, check.stdout + check.stderr)
            payload = json.loads(check.stdout)
            self.assertEqual(1, payload["new_findings"])
            self.assertEqual("tools/local-probe/action.yaml", payload["new_risks"][0]["path"])
            self.assertFalse(payload["mutation_performed"])

    def test_real_git_new_root_action_unpinned_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, _workflow = self._init(root)
            (root / "action.yml").write_text(
                "name: Root action\nruns:\n  using: docker\n  image: alpine:latest\n", encoding="utf-8"
            )
            run_git(root, "add", ".")
            run_git(root, "commit", "-qm", "root action added")
            head = run_git(root, "rev-parse", "HEAD")
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(1, check.returncode, check.stdout + check.stderr)
            self.assertEqual("action.yml", json.loads(check.stdout)["new_risks"][0]["path"])

    def test_legacy_unpinned_image_cannot_move_between_jobs_unchanged(self):
        previous = """jobs:
  harmless:
    runs-on: ubuntu-latest
    container:
      image: alpine:latest
  privileged:
    runs-on: ubuntu-latest
"""
        candidate = """jobs:
  harmless:
    runs-on: ubuntu-latest
  privileged:
    runs-on: ubuntu-latest
    container:
      image: alpine:latest
"""
        original = self.scan(previous)
        changed = self.scan(candidate)
        self.assertEqual(1, len(original))
        self.assertEqual(1, len(changed))
        self.assertEqual(1, guard.compare(original, changed)["new_findings"])

    def test_legacy_unpinned_image_changed_job_trust_context_fails_closed(self):
        previous = "jobs:\n  build:\n    runs-on: ubuntu-latest\n    container:\n      image: alpine:latest\n"
        candidate = "jobs:\n  build:\n    runs-on: [self-hosted, zcloud, vps]\n    container:\n      image: alpine:latest\n"
        self.assertEqual(1, guard.compare(self.scan(previous), self.scan(candidate))["new_findings"])

    def test_real_git_nested_action_directory_symlink_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, _workflow = self._init(root)
            outside = root / "outside"
            outside.mkdir()
            (outside / "action.yml").write_text(
                "name: injected\nruns:\n  using: docker\n  image: alpine:latest\n",
                encoding="utf-8",
            )
            (root / ".github" / "actions").mkdir()
            (root / ".github" / "actions" / "nested").symlink_to(outside, target_is_directory=True)
            run_git(root, "add", "-A")
            run_git(root, "commit", "-qm", "nested action directory symlink")
            head = run_git(root, "rev-parse", "HEAD")
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(2, check.returncode, check.stdout + check.stderr)
            self.assertFalse(json.loads(check.stdout)["inventory_complete"])

    def test_real_git_submodule_in_local_actions_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, _workflow = self._init(root)
            run_git(root, "update-index", "--add", "--cacheinfo", f"160000,{base},.github/actions/opaque")
            run_git(root, "commit", "-qm", "opaque gitlink under actions")
            head = run_git(root, "rev-parse", "HEAD")
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(2, check.returncode, check.stdout + check.stderr)
            self.assertFalse(json.loads(check.stdout)["inventory_complete"])

    def test_global_repository_scan_is_not_hidden_behind_workflow_path_filter(self):
        workflow = (ROOT / ".github/workflows" /
                    "zcloud-container-digest-ratchet-20261015-w2.yml").read_text()
        self.assertIn("  pull_request:\n    branches: [main]", workflow)
        # Any changed path may be a symlink to an arbitrary local Action; do
        # not suppress the guard simply because the symlink is named alias.
        self.assertNotIn("    paths:", workflow.split("permissions:", 1)[0])

    def test_real_git_arbitrary_action_alias_symlink_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, _workflow = self._init(root)
            (root / "components").mkdir()
            (root / "tools").mkdir()
            (root / "tools" / "action-wrapper").symlink_to(
                root / "components", target_is_directory=True
            )
            run_git(root, "add", ".")
            run_git(root, "commit", "-qm", "opaque action alias outside github")
            head = run_git(root, "rev-parse", "HEAD")
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(2, check.returncode, check.stdout + check.stderr)
            self.assertFalse(json.loads(check.stdout)["inventory_complete"])

    def test_same_file_non_image_change_demands_legacy_owner_review(self):
        old = self.scan("jobs:\n  old:\n    container:\n      image: alpine:latest\n")
        new = self.scan("jobs:\n  old:\n    timeout-minutes: 30\n    container:\n      image: alpine:latest\n")
        self.assertEqual(1, guard.compare(old, new)["new_findings"])

    def test_hosted_source_rechecks_current_pr_head_after_scan(self):
        workflow = (ROOT / ".github/workflows" /
                    "zcloud-container-digest-ratchet-20261015-w2.yml").read_text()
        self.assertIn('test "$GITHUB_BASE_REF" = "main"', workflow)
        self.assertIn('[[ "$GITHUB_HEAD_REF" =~ ^[A-Za-z0-9._/-]{1,200}$ ]]', workflow)
        self.assertIn('origin "refs/heads/$GITHUB_HEAD_REF"', workflow)
        self.assertIn('test "$remote_head" = "$EXPECTED_HEAD"', workflow)
        self.assertIn('test "$final_head" = "$HEAD_SHA"', workflow)
        self.assertLess(workflow.index('test "$remote_head" = "$EXPECTED_HEAD"'),
                        workflow.index("Run isolated real-Git and adversarial"))
        self.assertGreater(workflow.index('test "$final_head" = "$HEAD_SHA"'),
                           workflow.index("NO_NEW_CONTAINER_IMAGE_TAG_DEBT"))

    def test_dutch_operator_summary_is_safe_for_success_and_new_debt(self):
        clean = guard.compare([], [])
        rendered = guard.dutch_operator_summary(clean, 0)
        self.assertIn("Geen nieuwe risico", rendered)
        self.assertIn("| Nieuwe risico's | 0 |", rendered)
        self.assertIn("\n", rendered)
        self.assertNotIn("\\n", rendered)
        tagged = guard.compare([], self.scan("  image: alpine:latest\n"))
        rejected = guard.dutch_operator_summary(tagged, 1)
        self.assertIn("Afgewezen: nieuwe risico", rejected)
        self.assertIn("| Nieuwe risico's | 1 |", rejected)
        self.assertNotIn("alpine:latest", rejected)
        self.assertIn("Geen VPS-herstel, productie-deploy of vrijgave", rejected)

    def test_dutch_operator_summary_reports_incomplete_git_without_exposing_error(self):
        report = {
            "policy": "container-image-digest-no-new-debt-v1",
            "inventory_complete": False,
            "error": "private/internal/location?token=secret",
            "release_authorized": False,
            "deployment_authorized": False,
            "mutation_performed": False,
        }
        summary = guard.dutch_operator_summary(report, 2)
        self.assertIn("bronbewijs incompleet", summary)
        self.assertIn("Afgewezen", summary)
        self.assertNotIn("secret", summary)
        self.assertNotIn("private", summary)

    def test_dutch_operator_summary_rejects_forged_authority_and_invalid_counts(self):
        clean = guard.compare([], [])
        for key in ("release_authorized", "deployment_authorized", "mutation_performed"):
            bad = dict(clean, **{key: True})
            with self.subTest(key=key), self.assertRaises(ValueError):
                guard.dutch_operator_summary(bad, 0)
        for count in (True, -1, 8193, "0", None):
            with self.subTest(count=count), self.assertRaises(ValueError):
                guard.dutch_operator_summary(dict(clean, new_findings=count), 0)
        with self.assertRaises(ValueError):
            guard.dutch_operator_summary(clean, 1)
        with self.assertRaises(ValueError):
            guard.dutch_operator_summary(dict(clean, inventory_complete=False), 0)
        with self.assertRaises(ValueError):
            guard.dutch_operator_summary(dict(clean, policy="unknown"), 0)

    def test_docker_uses_digest_must_cover_entire_scalar(self):
        digest = DIGEST
        variants = (
            f"      - uses: docker://alpine@sha256:{digest} untrusted-trailer\n",
            f"      - uses: 'docker://alpine@sha256:{digest}' trailing-yaml\n",
            f'      - uses: "docker://alpine@sha256:{digest} suffix"\n',
            f"      - uses: docker://alpine@sha256:{digest}'\n",
        )
        for value in variants:
            with self.subTest(value=value):
                self.assertEqual(1, len(self.scan(value)))

    def test_opaque_action_uses_alias_and_expression_are_denied(self):
        opaque = (
            "      - uses: *docker_action\n",
            "      - uses: &container docker://alpine:latest\n",
            "      - uses: ${{ inputs.step }}\n",
            "      - uses: !yaml-tag docker://alpine:latest\n",
            "      - uses: [docker://alpine:latest]\n",
        )
        for value in opaque:
            with self.subTest(value=value):
                self.assertEqual(1, len(self.scan(value)))
        self.assertEqual([], self.scan(
            "      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803\n"
        ))

    def test_pinned_image_must_have_balanced_yaml_quotes(self):
        for scalar in (
            f"'redis@sha256:{DIGEST}",
            f"redis@sha256:{DIGEST}'",
            f'"redis@sha256:{DIGEST}',
            f'redis@sha256:{DIGEST}"',
            f"'redis@sha256:{DIGEST}''",
            f'"redis@sha256:{DIGEST}""',
        ):
            with self.subTest(scalar=scalar):
                self.assertEqual(1, len(self.scan(f"      image: {scalar}\n")))
        self.assertEqual([], self.scan(f"    image: 'redis@sha256:{DIGEST}' # pinned\n"))
        self.assertEqual([], self.scan(f'    image: "redis@sha256:{DIGEST}" # pinned\n'))

    def test_real_git_full_docker_uses_scalar_denies_trailing_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, workflow = self._init(root)
            workflow.write_text(
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                f"      - uses: docker://alpine@sha256:{DIGEST} trailing-invalid-yaml\n",
                encoding="utf-8",
            )
            run_git(root, "add", ".")
            run_git(root, "commit", "-qm", "appended unsafe Docker uses scalar")
            head = run_git(root, "rev-parse", "HEAD")
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(1, check.returncode, check.stdout + check.stderr)
            data = json.loads(check.stdout)
            self.assertTrue(data["inventory_complete"])
            self.assertEqual(1, data["new_findings"])
            self.assertEqual("docker_uses", data["new_risks"][0]["kind"])
            self.assertFalse(data["deployment_authorized"])

    def test_real_git_action_uses_alias_denied_and_unmodified_source_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, workflow = self._init(root)
            workflow.write_text(
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: *docker_image_ref\n",
                encoding="utf-8",
            )
            run_git(root, "add", ".")
            run_git(root, "commit", "-qm", "indirect Docker action")
            head = run_git(root, "rev-parse", "HEAD")
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(1, check.returncode, check.stdout + check.stderr)
            self.assertEqual("ambiguous_uses_reference",
                             json.loads(check.stdout)["new_risks"][0]["kind"])
            same = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", head, "--head-sha", head],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(0, same.returncode, same.stdout + same.stderr)
            self.assertEqual(0, json.loads(same.stdout)["new_findings"])

    def test_operator_report_rejects_inconsistent_risk_inventory_sizes(self):
        clean = guard.compare([], [])
        invalid = [
            dict(clean, baseline_findings=0, candidate_findings=0, new_findings=1,
                 new_risks=[{"path": "x", "line": 1, "kind": "image"}]),
            dict(clean, baseline_findings=0, candidate_findings=1, new_findings=1,
                 new_risks=[]),
            dict(clean, baseline_findings=0, candidate_findings=0, new_findings=0,
                 new_risks=[{"path": "x", "line": 1, "kind": "image"}]),
            dict(clean, new_risks="spoofed-array"),
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                guard.dutch_operator_summary(payload, 1 if payload["new_findings"] else 0)

    def test_operator_report_denial_can_include_only_bounded_count(self):
        risks = self.scan("  image: alpine:latest\n") * 26
        report = guard.compare([], risks)
        self.assertEqual(26, report["new_findings"])
        self.assertEqual(25, len(report["new_risks"]))
        rendered = guard.dutch_operator_summary(report, 1)
        self.assertIn("| Nieuwe risico's | 26 |", rendered)
        self.assertNotIn("alpine", rendered)

    def test_invalid_sha_fails_closed(self):
        check = subprocess.run(
            [sys.executable, str(SCRIPT), "--base-sha", "main", "--head-sha", "main"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(2, check.returncode)
        data = json.loads(check.stdout)
        self.assertFalse(data["release_authorized"])
        self.assertFalse(data["inventory_complete"])


    def test_flow_sequence_docker_uses_is_never_ignored(self):
        # Previously only "- {uses: docker://...}" was found. A whole
        # sequence in a YAML flow mapping can hide the same Docker image.
        samples = (
            "steps: [{uses: docker://alpine:latest}]\n",
            "steps: [{ 'uses': 'docker://alpine:latest' }]\n",
            'steps: [{ "uses": "docker://alpine:latest" }]\n',
            f"steps: [{{uses: docker://alpine@sha256:{DIGEST}}}]\n",
        )
        for source in samples:
            with self.subTest(source=source):
                risks = self.scan(source)
                self.assertEqual(1, len(risks), risks)
                self.assertEqual("ambiguous_flow_uses", risks[0].kind)

    def test_flow_job_container_cannot_hide_tag(self):
        samples = (
            "jobs: {build: {container: alpine:latest}}\n",
            'jobs: {build: {"container": "alpine:latest"}}\n',
            f"jobs: {{build: {{container: alpine@sha256:{DIGEST}}}}}\n",
        )
        for source in samples:
            with self.subTest(source=source):
                risks = self.scan(source)
                self.assertEqual(1, len(risks), risks)
                self.assertEqual("ambiguous_flow_container", risks[0].kind)
        # The flow image key still gets separately classified.
        self.assertEqual("ambiguous_flow_image",
                         self.scan("services: {redis: {image: redis:latest}}\n")[0].kind)

    def test_run_block_flow_text_does_not_claim_execution_image(self):
        shell = ("jobs:\n  build:\n    steps:\n      - run: |\n"
                 "          echo 'steps: [{uses: docker://alpine:latest}]'\n"
                 "          echo 'jobs: {build: {container: alpine:latest}}'\n")
        self.assertEqual([], self.scan(shell))

    def test_real_git_flow_sequence_docker_tag_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, workflow = self._init(root)
            workflow.write_text(
                "jobs:\n  build:\n    runs-on: ubuntu-latest\n"
                "    steps: [{uses: docker://alpine:latest}]\n", encoding="utf-8",
            )
            run_git(root, "add", ".")
            run_git(root, "commit", "-qm", "flow shorthand adds mutable docker action")
            head = run_git(root, "rev-parse", "HEAD")
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(1, check.returncode, check.stdout + check.stderr)
            result = json.loads(check.stdout)
            self.assertEqual(1, result["new_findings"])
            self.assertEqual("ambiguous_flow_uses", result["new_risks"][0]["kind"])
            self.assertFalse(result["deployment_authorized"])
            self.assertFalse(result["mutation_performed"])


    def test_encoded_yaml_flow_uses_key_is_not_trusted(self):
        # Quoted YAML keys decode Unicode escapes before interpreting the
        # step, so the literal text need not contain "uses".
        for escaped in (r'"\u0075ses"', r'"\x75ses"', r'"\U00000075ses"'):
            source = f'steps: [{{{escaped}: "docker://alpine:latest"}}]\n'
            with self.subTest(escaped=escaped):
                risks = self.scan(source)
                self.assertEqual(1, len(risks))
                self.assertEqual("ambiguous_encoded_flow_key", risks[0].kind)

    def test_encoded_yaml_flow_container_key_is_not_trusted(self):
        source = r'jobs: {build: {"\u0063ontainer": alpine:latest}}' + "\n"
        risks = self.scan(source)
        self.assertEqual(1, len(risks))
        self.assertEqual("ambiguous_encoded_flow_key", risks[0].kind)



    def test_real_git_unicode_escaped_flow_key_is_denied_without_value_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, workflow = self._init(root)
            workflow.write_text(
                "jobs:\n  verify:\n    runs-on: ubuntu-latest\n"
                + r'    steps: [{"\u0075ses": "docker://alpine:latest"}]'
                + "\n", encoding="utf-8",
            )
            run_git(root, "add", ".")
            run_git(root, "commit", "-qm", "encoded flow key")
            head = run_git(root, "rev-parse", "HEAD")
            check = subprocess.run(
                [sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(1, check.returncode, check.stdout + check.stderr)
            payload = json.loads(check.stdout)
            self.assertEqual(1, payload["new_findings"])
            self.assertEqual("ambiguous_encoded_flow_key", payload["new_risks"][0]["kind"])
            self.assertNotIn("docker://", check.stdout)
            self.assertFalse(payload["release_authorized"])
            self.assertFalse(payload["deployment_authorized"])



if __name__ == "__main__":
    unittest.main()
